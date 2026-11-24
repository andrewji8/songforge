"""Beat / key / structure analysis for uploaded loops.

Runs on the CPU with scipy FFT. For the loop-input entry, the upstream
analysis feeds the same `Intent` model as text entry, so the full
synthesis/mastering pipeline stays unified.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal as sps

from app.dsp.wav_io import Audio


@dataclass
class LoopAnalysis:
    bpm: int
    key_hint: str
    energy: float
    beat_seconds: float
    confidence: float


def _onset_envelope(audio: Audio, sr: int = 44100) -> np.ndarray:
    x = audio.mono().data.astype(np.float64)
    if x.shape[0] == 0:
        return np.zeros(1)
    spec = np.abs(np.fft.rfft(x))
    mag = spec / max(spec.max(), 1e-9)
    # temporal resolution
    hop = max(1, int(sr * 0.01))
    n_win = max(1, len(x) // hop)
    onset = np.zeros(n_win)
    for i in range(n_win):
        seg = x[i * hop : (i + 1) * hop]
        if seg.shape[0] == 0:
            continue
        f = np.abs(np.fft.rfft(seg))
        onset[i] = f.sum()
    # flux
    onset = np.maximum(np.diff(onset, prepend=0.0), 0.0)
    return onset


def estimate_bpm(audio: Audio, sr: int = 44100) -> tuple[float, float]:
    """Return (bpm, confidence). Onset-envelope autocorrelation.

    onset frame rate = sr / hop (hop = 0.01 * sr -> ~100 frames/s).
    lag (frames) = 60 * fps / bpm, i.e. slower tempo -> longer lag.
    """
    onset = _onset_envelope(audio)
    if onset.shape[0] < 16:
        return 90.0, 0.0
    on = onset - onset.mean()
    ac = np.correlate(on, on, mode="full")[len(on) - 1 :]
    ac = np.maximum(ac, 0.0)
    fps = sr / max(1, int(sr * 0.01))  # ~100 Hz
    lo = max(1, int(60 * fps / 220.0))  # fastest allowed tempo (220 BPM)
    hi = min(len(ac) - 1, int(60 * fps / 40.0))  # slowest (40 BPM)
    if lo >= hi:
        return 90.0, 0.0
    best = int(lo + np.argmax(ac[lo:hi]))
    best_ac = float(ac[best])
    total = float(ac[lo:hi].sum()) + 1e-9
    conf = min(1.0, best_ac / total)
    bpm = 60.0 * fps / best
    # fold into 60..200 with octaves
    while bpm < 60.0:
        bpm *= 2.0
    while bpm > 200.0:
        bpm /= 2.0
    return float(bpm), conf


def estimate_key(audio: Audio, sr: int = 44100) -> str:
    """Chord/tonic estimation via centroid-weighted pitch class energy."""
    x = audio.mono().data.astype(np.float64)
    if x.shape[0] == 0:
        return "Am"
    n_fft = min(len(x), 1 << 16)
    f_spec = np.fft.rfft(x, n=n_fft)
    mag = np.abs(f_spec)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    # pitch-class energy in 80..1000 Hz
    mask = (freqs >= 80.0) & (freqs <= 1000.0)
    pc_energy = np.zeros(12)
    idx = np.where(mask)[0]
    pc = np.mod(np.round(freqs[idx]), 12).astype(int)
    pc_energy[pc] += mag[idx]
    pc_energy = pc_energy / (pc_energy.sum() + 1e-9)
    # correlate with common major/minor templates
    A_MAJOR = [0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1]
    A_MINOR = [0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 1]
    names = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    best_score, best_key = -1.0, "Am"
    for shift in range(12):
        m = np.roll(A_MAJOR, shift)
        mi = np.roll(A_MINOR, shift)
        sm = float(np.dot(pc_energy, m))
        smin = float(np.dot(pc_energy, mi))
        if sm > best_score:
            best_score, best_key = sm, names[shift]
        if smin > best_score:
            best_score, best_key = smin, names[shift] + "m"
    return best_key


def analyze_loop(audio: Audio) -> LoopAnalysis:
    bpm, conf = estimate_bpm(audio)
    bpm = int(min(200, max(60, round(bpm))))
    energy = min(1.0, float(np.percentile(np.abs(audio.mono().data), 95)) * 2.5)
    return LoopAnalysis(
        bpm=bpm,
        key_hint=estimate_key(audio),
        energy=energy,
        beat_seconds=60.0 / bpm,
        confidence=max(0.0, min(1.0, conf)),
    )
