"""Mixdown + mastering chain.

All arrays use the layout (samples, channels) to match `Audio`.

Design (production-grade loudness):
- `master()` is a *safe in-process first pass*: mixdown -> per-channel glue
  compression -> 25 Hz HPF -> peak-normalize to a safe intermediate level
  (-6 dBFS) -> true-peak limit. It never chases an exact LUFS in-process
  (that would require the numerically-fragile BS.1770 shelf), and it can
  run with no external tooling.
- `finalize_master()` is the *authoritative* pass: it measures the
  rendered master WAV with `ffmpeg -af ebur128` (the same filter streaming
  services gate on), applies the exact makeup gain to hit the target LUFS,
  re-limits to the true-peak ceiling, and re-saves. It iterates a bounded
  number of times so the delivered file lands at the streaming target by
  construction. No-op (in-process estimate returned) when ffmpeg is
  absent, e.g. on non-ffmpeg CI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping, Optional

import numpy as np

from app.dsp.core import db_to_gain, highpass_biquad, limiter, soft_knee_compressor
from app.dsp.wav_io import Audio

log = logging.getLogger(__name__)


@dataclass
class MixConfig:
    target_lufs: float = -14.0
    true_peak_db: float = -1.0
    sample_rate: int = 44100
    # intermediate peak-normalization target for the in-process first pass
    first_pass_peak_db: float = -6.0
    balance: Mapping[str, float] = field(default_factory=lambda: {
        "drums": 1.0, "bass": 1.0, "chords": 0.95, "melody": 1.05, "vox": 1.1,
    })
    glue: dict = field(default_factory=lambda: dict(threshold_db=-24.0, ratio=3.0))


@dataclass
class MasterResult:
    audio: Audio
    peak_db: float
    applied_gain_db: float
    target_lufs: float
    finalized: bool = False          # True when ffmpeg-authoritative pass ran
    final_lufs: Optional[float] = None
    final_true_peak_db: Optional[float] = None
    stem_paths: dict = field(default_factory=dict)

    @property
    def loudness_lufs(self) -> float:
        return self.final_lufs if self.final_lufs is not None else float(
            "nan" if np.isnan(self.peak_db) else self.peak_db
        )


def mix_stems(
    stems: Mapping[str, np.ndarray],
    sample_rate: int,
    balance: Optional[Mapping[str, float]] = None,
) -> np.ndarray:
    """Sum per-source float arrays into a stereo (samples, 2) mix."""
    balance = balance or MixConfig().balance
    target = min(int(v.shape[0]) for v in stems.values())
    out = np.zeros((target, 2), dtype=np.float64)
    for name, sig in stems.items():
        g = float(balance.get(name, 1.0))
        sig = np.asarray(sig, dtype=np.float64)
        if sig.ndim == 2:
            sig = sig.mean(axis=1)
        sig = sig[:target] * g
        out[:, 0] += sig
        out[:, 1] += sig
    return out


def _peak_db(audio: Audio) -> float:
    m = float(np.max(np.abs(audio.mono().data))) if audio.num_frames else 0.0
    if m < 1e-9:
        return -200.0
    return 20.0 * np.log10(m)


def master(
    mix: np.ndarray,
    sample_rate: int,
    target_lufs: float = -14.0,
    true_peak_db: float = -1.0,
    glue: Optional[Mapping] = None,
    cfg: Optional[MixConfig] = None,
) -> MasterResult:
    """Safe in-process first-pass master (see module docstring)."""
    cfg = cfg or MixConfig()
    glue = glue or cfg.glue
    mix = np.asarray(mix, dtype=np.float64)
    if mix.ndim == 1:
        mix = np.stack([mix, mix], axis=1)
    ch = mix.shape[1]

    # per-channel light glue compression + subsonic HPF
    for c in range(ch):
        mix[:, c] = soft_knee_compressor(
            mix[:, c],
            threshold_db=glue.get("threshold_db", -24.0),
            ratio=glue.get("ratio", 3.0),
            attack_s=0.010,
            release_s=0.15,
            makeup_db=0.0,
            sample_rate=sample_rate,
        )
        mix[:, c] = highpass_biquad(mix[:, c], 25.0, sample_rate)

    # peak-normalize to a safe intermediate level (avoids extreme gain)
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 1e-9:
        target_peak = db_to_gain(cfg.first_pass_peak_db)
        gain_db = 20.0 * np.log10(target_peak / peak)
        mix = mix * db_to_gain(gain_db)
    else:
        gain_db = 0.0

    # true-peak limit to the streaming ceiling
    limited = np.zeros_like(mix)
    for c in range(ch):
        limited[:, c] = limiter(mix[:, c], true_peak_db, sample_rate)
    audio = Audio(limited.astype(np.float32), sample_rate)
    peak_db = _peak_db(audio)
    return MasterResult(
        audio=audio,
        peak_db=peak_db,
        applied_gain_db=round(float(gain_db), 2),
        target_lufs=target_lufs,
    )


def finalize_master(
    master_wav_path,
    sample_rate: int,
    target_lufs: float,
    true_peak_db: float,
    max_nudges: int = 3,
) -> tuple[float, float, bool]:
    """ffmpeg-authoritative LUFS finalization.

    Measures the WAV with `ebur128`, applies the exact makeup gain to reach
    `target_lufs`, re-limits to `true_peak_db`, and re-saves in place.
    Iterates up to `max_nudges` times (converges in 1-2). Returns
    (final_lufs, final_true_peak_db, ffmpeg_used). When ffmpeg is absent,
    returns the in-process estimate and `ffmpeg_used=False`.
    """
    from app.dsp import loudness as L

    audio = Audio.from_wav(master_wav_path, sr=sample_rate)
    if not L.ffmpeg_available():
        lufs, tp = L.momentary_lufs(audio), L.true_peak_db(audio)
        return lufs, tp, False

    for _ in range(max_nudges):
        measured, _tp = L.measure_lufs_ffmpeg(master_wav_path, sample_rate)
        gain_db = round(target_lufs - measured, 2)
        if abs(gain_db) < 0.3:
            break  # within 0.3 LU of target
        audio, _ = L.nudge_to_target(audio, target_lufs, true_peak_db, measured)
        audio.save_wav(master_wav_path)

    final_lufs, final_tp = L.measure_lufs_ffmpeg(master_wav_path, sample_rate)
    return final_lufs, final_tp, True


def master_from_wav(
    wav: Audio,
    target_lufs: float,
    true_peak_db: float,
) -> MasterResult:
    """Master a full clip (used on loop-input jobs)."""
    mono = wav.mono().data.astype(np.float64)
    stereo = np.stack([mono, mono], axis=1)
    return master(stereo, wav.sample_rate, target_lufs, true_peak_db)


__all__ = [
    "MixConfig",
    "MasterResult",
    "mix_stems",
    "master",
    "finalize_master",
    "master_from_wav",
]
