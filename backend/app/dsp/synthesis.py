"""Deterministic seeded song renderer.

Acts as the built-in `SimulatedProvider` back-end: given parsed intent
(key, bpm, section map, voice timbre features) it synthesizes drum /
bass / chord / melody / vocal-stem arrays at 44.1 kHz. Everything is
seed-derived (numpy Generator) so results are reproducible per job and
the whole pipeline is testable with zero external models.

Section map (MVP structure):
    [intro][verse][chorus][verse][chorus][bridge][chorus][outro]
The preview (hook) is the first chorus window, rendered *first* so the
25-second SLO only depends on chorus synthesis + light mastering.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

import numpy as np

from app.dsp.wav_io import Audio

SR = 44100

SECTIONS_FULL = [
    ("intro", 0.25),
    ("verse", 1.0),
    ("chorus", 1.0),
    ("verse", 1.0),
    ("chorus", 1.0),
    ("bridge", 0.75),
    ("chorus", 1.0),
    ("outro", 0.25),
]


@dataclass
class VoiceTimbre:
    """Reduced timbre model of the user's voice clip."""

    f0_mean_hz: float = 140.0
    brightness: float = 0.5  # 0..1, spectral tilt
    formants: tuple = field(default_factory=lambda: (600.0, 900.0, 2400.0))


@dataclass
class Intent:
    key: str = "Am"
    bpm: int = 90
    energy: float = 0.5  # 0..1 -> drum density / chord velocity
    scale: tuple = field(
        default_factory=lambda: (57.0, 60.0, 64.0, 65.0, 67.0)  # A minor pent
    )
    root_midi: float = 45.0
    time_signature: int = 4
    voice: Optional[VoiceTimbre] = None


def midi_to_hz(m: float) -> float:
    return 440.0 * (2.0 ** ((m - 69.0) / 12.0))


def parse_key_key_to_intent(key: str, bpm: int, energy: float,
                            voice: Optional[VoiceTimbre] = None) -> Intent:
    """Very small key table: root + minor/major pentatonic stack."""
    keys = {
        "Am": (57.0, [57, 60, 64, 65, 67]),
        "A": (57.0, [57, 60, 64, 65, 69]),
        "C": (60.0, [60, 64, 67, 69, 72]),
        "Cm": (60.0, [60, 63, 67, 68, 70]),
        "Dm": (62.0, [62, 65, 69, 70, 72]),
        "Em": (64.0, [64, 67, 71, 72, 74]),
        "G": (67.0, [67, 71, 74, 76, 79]),
        "F": (65.0, [65, 69, 72, 74, 77]),
    }
    root, scale = keys.get(key, keys["Am"])
    return Intent(
        key=key,
        bpm=bpm,
        energy=energy,
        scale=tuple(float(s) for s in scale),
        root_midi=float(root),
        voice=voice,
    )


def section_map_for(duration_s: float) -> list[tuple[str, float]]:
    """Scale the canonical section map to exactly `duration_s`."""
    weights = [w for _, w in SECTIONS_FULL]
    total = sum(weights)
    scale = duration_s / total
    out: list[tuple[str, float]] = []
    t = 0.0
    for (name, w), sw in zip(SECTIONS_FULL, weights):
        dur = w * scale
        out.append((name, round(dur, 3)))
        t += dur
    return out


# ---------------------------------------------------------------------------
# oscillators / sources
# ---------------------------------------------------------------------------

def sine(midi: float, seconds: float, sr: int = SR) -> np.ndarray:
    f = midi_to_hz(midi)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    return np.sin(2 * np.pi * f * t).astype(np.float32) * 0.5


def saw(midi: float, seconds: float, sr: int = SR) -> np.ndarray:
    return saw_n(midi, int(seconds * sr), sr)


def saw_n(midi: float, n_samples: int, sr: int = SR) -> np.ndarray:
    """Sawtooth of *exactly* n_samples samples (no seconds round-trip)."""
    f = midi_to_hz(midi)
    n = max(1, int(n_samples))
    t = np.arange(n) / sr
    x = (2.0 * (t * f - np.floor(0.5 + t * f))).astype(np.float32)
    from app.dsp.core import one_pole_lowpass

    return one_pole_lowpass(x, min(f * 4, 4000.0), sr) * 0.35


def noise(seconds: float, sr: int = SR) -> np.ndarray:
    return (np.random.default_rng(0).standard_normal(int(seconds * sr)) * 0.2).astype(
        np.float32
    )


def _adsr(n: int, sr: int, a=0.02, d=0.15, s=0.7, r=0.25) -> np.float32:
    """ADSR envelope of exactly n samples. Regions are clamped so the
    total never exceeds n (short section tails are common)."""
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    na = min(int(a * sr), n)
    nd = min(int(d * sr), n - na)
    nr = min(int(r * sr), n - na - nd)
    env = np.ones(n, dtype=np.float32)
    if na > 0:
        env[:na] = np.linspace(0.0, 1.0, na)
    if nd > 0:
        env[na : na + nd] = np.linspace(1.0, s, nd)
    sustain_end = n - nr
    if na + nd < sustain_end:
        env[na + nd : sustain_end] = s
    if nr > 0:
        env[sustain_end:] = np.linspace(s, 0.0, nr)
    return env


# ---------------------------------------------------------------------------
# instrument stems per section
# ---------------------------------------------------------------------------

def _kick(seconds: float, bpm: int, energy: float) -> np.ndarray:
    beat = 60.0 / bpm
    out = np.zeros(int(seconds * SR), dtype=np.float32)
    step = int(beat * SR)
    kick_len = int(0.22 * SR)
    freq0, freq1 = 120.0, 45.0
    for i in range(0, len(out), step):
        if energy < 0.3 and (i // step) % 2:
            continue
        seg = np.arange(kick_len) / SR
        f = freq0 + (freq1 - freq0) * (seg / 0.22)
        env = np.exp(-seg * 14.0)
        tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * env
        b = min(len(out) - i, kick_len)
        out[i : i + b] += tone[:b].astype(np.float32)
    return out


def _snare(seconds: float, bpm: int, energy: float) -> np.ndarray:
    beat = 60.0 / bpm
    out = np.zeros(int(seconds * SR), dtype=np.float32)
    step = int(beat * SR)
    sn_len = int(0.18 * SR)
    tone = np.sin(2 * np.pi * 190 * np.arange(sn_len) / SR) * np.exp(
        -np.arange(sn_len) / SR * 12
    )
    n_part = noise(0.18, SR) * np.exp(-np.arange(sn_len) / SR * 10)
    tone = (tone + n_part * 0.6)[:sn_len].astype(np.float32)
    for i in range(step, len(out) - sn_len, step * 2):
        out[i : i + sn_len] += tone * (0.5 + energy * 0.5)
    return out


def _hat(seconds: float, bpm: int, energy: float) -> np.ndarray:
    beat = 60.0 / bpm
    out = np.zeros(int(seconds * SR), dtype=np.float32)
    step = int(beat * SR)
    hat_len = int(0.05 * SR)
    tone = (np.random.default_rng(1).standard_normal(hat_len) * np.exp(
        -np.arange(hat_len) / SR * 40
    )).astype(np.float32)
    from app.dsp.core import highpass_biquad

    tone = highpass_biquad(tone, 6000.0, SR) * 0.3
    mult = 4 if energy > 0.6 else 2
    sub = step // mult
    for i in range(0, len(out) - hat_len, sub):
        gain = 0.9 if (i // step) % 2 == 0 else 0.55
        out[i : i + hat_len] += tone * gain
    return out


def _bassline(seconds: float, intent: Intent, section: str) -> np.ndarray:
    beat = 60.0 / intent.bpm
    n = int(seconds * SR)
    root = intent.root_midi - 12
    out = np.zeros(n, dtype=np.float32)
    scale = list(intent.scale)
    pat = {
        "intro": [0, 2, 0, 4],
        "verse": [0, 0, 2, 4],
        "chorus": [0, 4, 2, 5] if len(scale) > 4 else [0, 4, 2, 0],
        "bridge": [0, 5, 4, 2],
        "outro": [0, 0, 2, 0],
    }.get(section, [0, 0, 2, 0])
    i = 0
    while i < n:
        deg = scale[pat[(i // int(beat * SR)) % len(pat)] % len(scale)]
        f = midi_to_hz(root + deg - 12)
        ln = int(beat * 0.95 * SR)
        t = np.arange(min(ln, n - i)) / SR
        x = (np.sin(2 * np.pi * f * t) * 0.6 + np.sin(4 * np.pi * f * t) * 0.15).astype(
            np.float32
        )
        x *= _adsr(min(ln, n - i), SR, a=0.01, d=0.08, s=0.8, r=0.1)
        out[i : i + min(ln, n - i)] += x
        i += int(beat * SR)
    return out


def _chords(seconds: float, intent: Intent, section: str) -> np.ndarray:
    bar = 4 * 60.0 / intent.bpm
    n = int(seconds * SR)
    out = np.zeros(n, dtype=np.float32)
    prog = {
        "intro": [0, 1, 0, 1],
        "verse": [0, 1, 2, 1],
        "chorus": [0, 2, 1, 0],
        "bridge": [3, 2, 1, 0],
        "outro": [0, 1],
    }.get(section, [0, 1, 2, 1])
    scale = list(intent.scale)
    i = 0
    k = 0
    while i < n:
        deg = scale[prog[k % len(prog)] % len(scale)]
        ln = int(bar * SR)
        t = np.arange(min(ln, n - i)) / SR
        x = (
            np.sin(2 * np.pi * midi_to_hz(deg + 12) * t) * 0.18
            + np.sin(2 * np.pi * midi_to_hz(deg + 19) * t) * 0.12
            + np.sin(2 * np.pi * midi_to_hz(deg + 24) * t) * 0.10
        ).astype(np.float32)
        x *= _adsr(min(ln, n - i), SR, a=0.15, d=0.2, s=0.7, r=0.4)
        out[i : i + min(ln, n - i)] += x
        i += int(bar * SR)
        k += 1
    return out


def _melody(seconds: float, intent: Intent, section: str, rng: np.random.Generator) -> np.ndarray:
    beat = 60.0 / intent.bpm
    n = int(seconds * SR)
    out = np.zeros(n, dtype=np.float32)
    scale = list(intent.scale)
    rest = 0.35 if section in ("verse", "chorus") else 0.6
    i = 0
    while i < n:
        ln = int(beat * 0.9 * SR)
        if rng.random() > rest:
            note_val = int(rng.integers(0, len(scale)))
            deg = scale[note_val % len(scale)] + (12 if section == "chorus" else 0)
            seg_len = int(min(ln, n - i))
            t = np.arange(seg_len) / SR
            x = (
                saw_n(deg, seg_len, SR) * 0.8
                + np.sin(2 * np.pi * midi_to_hz(deg + 12) * t) * 0.05
            ).astype(np.float32)
            x *= _adsr(seg_len, SR, a=0.03, d=0.1, s=0.8, r=0.2)
            out[i : i + seg_len] += x
        i += int(beat * SR)
    return out


def _vocal_stub(
    seconds: float, intent: Intent, section: str, rng: np.random.Generator
) -> np.ndarray:
    """'Vocal' stem: formant-filtered melody tone. With VoiceTimbre the
    f0 centre and spectral tilt follow the user's voice clip."""
    beat = 60.0 / intent.bpm
    n = int(seconds * SR)
    out = np.zeros(n, dtype=np.float32)
    scale = list(intent.scale)
    vt = intent.voice or VoiceTimbre()
    f0 = max(60.0, min(vt.f0_mean_hz, 400.0))
    i = 0
    while i < n:
        ln = int(beat * SR)
        if rng.random() > 0.3:
            deg = scale[int(rng.integers(0, len(scale)))]
            freqs = [f0, f0 * 1.4, f0 * 2.6]
            t = np.arange(min(ln, n - i)) / SR
            x = np.zeros_like(t)
            for k, fq in enumerate(freqs):
                x += np.sin(2 * np.pi * fq * t) * (0.5 / (k + 1))
            x = x.astype(np.float32)
            if vt.brightness < 0.5:
                from app.dsp.core import one_pole_lowpass

                x = one_pole_lowpass(x, 1800.0, SR)
            x *= _adsr(min(ln, n - i), SR, a=0.06, d=0.15, s=0.75, r=0.3)
            out[i : i + min(ln, n - i)] += x * 0.9
        i += int(beat * SR)
    return out


# ---------------------------------------------------------------------------
# public render API
# ---------------------------------------------------------------------------

def render_section_stems(
    section: str,
    seconds: float,
    intent: Intent,
    seed: int,
    rng: np.random.Generator | None = None,
) -> Mapping[str, np.ndarray]:
    rng = rng or np.random.default_rng(seed)
    energy = intent.energy
    drums = _kick(seconds, intent.bpm, energy) + _snare(
        seconds, intent.bpm, energy
    ) + _hat(seconds, intent.bpm, energy)
    return {
        "drums": drums.astype(np.float32),
        "bass": _bassline(seconds, intent, section),
        "chords": _chords(seconds, intent, section),
        "melody": _melody(seconds, intent, section, rng),
        "vox": _vocal_stub(seconds, intent, section, rng),
    }


def render_song(
    intent: Intent, duration_s: float, seed: int
) -> tuple[Mapping[str, np.ndarray], list[tuple[str, float]]]:
    """Render all stems of the full song; returns (stems, section_map)."""
    section_map = section_map_for(duration_s)
    stems: dict[str, list[np.ndarray]] = {
        k: [] for k in ("drums", "bass", "chords", "melody", "vox")
    }
    for idx, (sec, dur) in enumerate(section_map):
        rng = np.random.default_rng(seed + idx * 7919)
        out = render_section_stems(sec, dur, intent, seed + idx)
        for k, v in out.items():
            stems[k].append(v)
    merged = {k: np.concatenate(v) for k, v in stems.items()}
    # trim/pad to exact length
    target = int(duration_s * SR)
    merged = {
        k: (np.pad(v, (0, max(0, target - v.shape[0])))[:target] if v.shape[0] else
            np.zeros(target, dtype=np.float32))
        for k, v in merged.items()
    }
    return merged, section_map


def render_hook(intent: Intent, seconds: float, seed: int) -> Mapping[str, np.ndarray]:
    """Render just the chorus (hook) for the fast preview path."""
    return render_section_stems("chorus", seconds, intent, seed + 9999)
