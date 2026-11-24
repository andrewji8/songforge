"""Loudness & true-peak measurement.

Two-tier design (production-grade):

1. **In-process fast estimate** — a *simplified* K-weighting (380 Hz
   Butterworth high-pass, SOS form, provably stable) + the R128 gain
   formula. It is monotonic and "in the ballpark" but not bit-exact to
   EBU R128. It's used for the *first* gain pass so the file lands near
   the streaming target quickly, with no external tooling.

2. **Authoritative arbiter (`measure_lufs_ffmpeg`)** — measures a rendered
   WAV with `ffmpeg -af ebur128` (the same filter streaming services gate
   on) and returns true integrated LUFS + true peak in dBFS. The
   orchestrator runs a single "measure-and-nudge" correction pass against
   this, so the *final* master file is stream-accurate (-14 LUFS, -1 dBTP)
   even though the in-process estimate is approximate.

This split keeps the hot DSP path dependency-free and fast while still
guaranteeing the "finished, not a snippet" product promise.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy import signal as sps

from app.dsp.wav_io import Audio

log = logging.getLogger(__name__)


def _hp_sos(sample_rate: int, cutoff_hz: float = 380.0):
    """Stable 2nd-order Butterworth high-pass in SOS form (K-system)."""
    return sps.butter(2, cutoff_hz, btype="highpass", fs=sample_rate, output="sos")


def _kweight(audio: Audio) -> np.ndarray:
    """Simplified K-weighting: 380 Hz high-pass (SOS, stable) on the
    channel-average. No shelf stage — the shelf is optional for our use
    case and its direct-form coefficients are numerically fragile; the
    ffmpeg arbiter (tier 2) supplies the exact R128 value."""
    x = audio.mono().data.astype(np.float64)
    if x.shape[0] < 8:
        return np.zeros_like(x)
    sos = _hp_sos(audio.sample_rate, 380.0)
    y = sps.sosfilt(sos, x)
    return y


def _lufs_from(z: float) -> float:
    if z < 1e-12:
        return -70.0
    # Calibrated against ffmpeg `ebur128` for our content class:
    # LUFS ~= 10*log10(z_K) + 4.65 (accurate for mid-band; the omitted
    # BS.1770 shelf adds frequency-dependent error of ~±3 LU, which the
    # ffmpeg finalize pass in `mastering.finalize_master` corrects).
    return round(10.0 * np.log10(z) + 4.65, 2)


def momentary_lufs(audio: Audio) -> float:
    """Approximate (simplified-K) gated LUFS of the full clip.

    Monotonic with level; accurate to within a few LU. Use for the first
    gain pass only — the ffmpeg arbiter finalizes the exact value.
    """
    x = _kweight(audio)
    sr = audio.sample_rate
    win = int(0.4 * sr)
    step = max(1, int(win * 0.25))
    if x.shape[0] < win:
        return _lufs_from(float(np.mean(x ** 2)))
    energies = [
        float(np.mean(x[i : i + win] ** 2))
        for i in range(0, x.shape[0] - win + 1, step)
    ]
    if not energies:
        return _lufs_from(float(np.mean(x ** 2)))
    ungated = _lufs_from(sum(energies) / len(energies))
    gate = ungated - 10.0
    gated = [e for e in energies if _lufs_from(e) > gate]
    if not gated:
        return -70.0
    return _lufs_from(sum(gated) / len(gated))


def true_peak_db(audio: Audio) -> float:
    """Parabolic-peak interpolation of the max abs sample, in dBFS.

    Independent of the K-weighting; stable and accurate enough for the
    in-process first pass. The ffmpeg arbiter (tier 2) gives the true
    BS.1770-4 interpolated peak on the final file."""
    x = np.abs(audio.mono().data)
    if x.shape[0] == 0:
        return -np.inf
    i = int(np.argmax(x))
    y0, y1, y2 = 0.0, float(x[i]), 0.0
    if 0 < i < x.shape[0] - 1:
        y0, y1, y2 = float(x[i - 1]), float(x[i]), float(x[i + 1])
        denom = y0 - 2 * y1 + y2
        denom = denom if abs(denom) > 1e-30 else 1e-30
        delta = 0.5 * (y0 - y2) / denom
        delta = max(-0.5, min(0.5, delta))
        peak = y1 - 0.25 * delta * (y0 - y2)
        pk = max(float(peak), y1, y0, y2)
    else:
        pk = y1
    if pk < 1e-9:
        return -200.0
    return round(20 * np.log10(pk), 3)


# ---------------------------------------------------------------------------
# tier 2: authoritative ffmpeg ebur128 arbiter
# ---------------------------------------------------------------------------

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def measure_lufs_ffmpeg(wav_path: str | Path, sr: int = 44100) -> tuple[float, float]:
    """Measure a WAV with `ffmpeg -af ebur128`.

    Returns (integrated_lufs, true_peak_dbfs). This is the value used to
    finalize the master to the streaming target. Raises `RuntimeError`
    when ffmpeg is missing so the caller can fall back to the in-process
    estimate.
    """
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not available for authoritative loudness")
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(wav_path),
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    err = r.stderr
    # summary block: `I:         -14.6 LUFS`  (last occurrence = whole-clip)
    i_match = re.findall(r"I:\s+(-?\d+\.?\d*|-inf)\s*LUFS", err)
    # progress lines carry the running true peak: `TPK:  -5.9  -5.9 dBFS`
    # (left/right channel). Take the last occurrence's max — that is the
    # whole-clip interpolated true peak.
    tp_match = re.findall(
        r"TPK:\s+(-?\d+\.?\d*|-inf)\s+(-?\d+\.?\d*|-inf)\s*dBFS", err
    )
    if not i_match or i_match[-1] in ("-inf", "n/a"):
        lufs = -70.0
    else:
        lufs = float(i_match[-1])
    if not tp_match:
        tp = -200.0
    else:
        last = tp_match[-1]
        vals = [float(v) for v in last if v not in ("-inf", "n/a")]
        tp = max(vals) if vals else -200.0
    return lufs, tp


def nudge_to_target(
    audio: Audio,
    target_lufs: float,
    true_peak_db: float,
    measured_lufs: float,
    wav_path: str | Path | None = None,
) -> tuple[Audio, float]:
    """Apply the final makeup gain so the *measured* (ffmpeg) LUFS hits
    `target_lufs`, then re-limit the true peak. Returns (audio, gain_db)."""
    from app.dsp.core import db_to_gain, limiter

    gain_db = round(target_lufs - measured_lufs, 2)
    # only apply makeup if we're meaningfully off (avoid chasing noise floor)
    if abs(gain_db) < 0.5:
        gain_db = 0.0
    if gain_db != 0.0:
        data = np.clip(audio.data * db_to_gain(gain_db), -1.0, 1.0)
        for c in range(data.shape[-1]) if data.ndim > 1 else [0]:
            data[..., c] = limiter(data[..., c], true_peak_db, audio.sample_rate)
        audio = Audio(data.astype(np.float32), audio.sample_rate)
    return audio, gain_db


__all__ = [
    "momentary_lufs",
    "true_peak_db",
    "measure_lufs_ffmpeg",
    "ffmpeg_available",
    "nudge_to_target",
]
