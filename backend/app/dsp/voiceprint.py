"""Timbre (voice) feature extraction from a short voice clip.

Feeds the `VoiceTimbre` model used by the synthesis engine to shape the
vocal stub toward the user's voice. Kept lightweight (autocorrelation
F0 + spectral tilt) so it runs CPU-only in the preview path.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from app.dsp.wav_io import Audio


@dataclass
class VoiceFeatures:
    f0_mean_hz: float
    brightness: float
    speech_rate_syl_per_s: float


def estimate_f0(x: np.ndarray, sr: int) -> np.ndarray:
    """Per-frame F0 via zero-crossing autocorrelation (50–400 Hz)."""
    frame = int(0.03 * sr)
    hop = int(0.015 * sr)
    f0s: list[float] = []
    min_lag = max(2, int(sr / 400.0))
    max_lag = int(sr / 50.0)
    for i in range(0, len(x) - frame, hop):
        seg = x[i : i + frame].astype(np.float64)
        if np.abs(seg).max() < 0.005:
            continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, mode="full")[len(seg) - 1 :]
        lo = min(max_lag, len(ac) - 1)
        if lo < min_lag:
            continue
        peak = min(max_lag, lo)
        if peak < min_lag:
            continue
        lag = min_lag + int(np.argmax(ac[min_lag:min(peak, len(ac))]))
        if lag < min_lag:
            continue
        f0s.append(sr / lag)
    return np.array(f0s)


def extract_voice_features(audio: Audio) -> VoiceFeatures:
    mono = audio.mono()
    x = mono.data.astype(np.float64)
    sr = mono.sample_rate
    if x.shape[0] < int(0.3 * sr):
        return VoiceFeatures(f0_mean_hz=140.0, brightness=0.5, speech_rate_syl_per_s=3.0)
    # voiced-segment detection
    rms_win = int(0.05 * sr)
    if x.shape[0] >= rms_win:
        env = np.sqrt(_frame_rms(x, rms_win, int(0.01 * sr)))
        voiced = env > max(env.max() * 0.25, 1e-4)
        f0 = estimate_f0(x, sr)
        f0_mean = float(np.mean(f0)) if f0.size else 140.0
        f0_mean = min(350.0, max(70.0, f0_mean))
    else:
        f0_mean = 140.0
        voiced = np.zeros(x.shape[0], dtype=bool)
    # brightness: 0..1 tilt between 500Hz and 4kHz energy
    n_fft = min(len(x), 1 << 18)
    spec = np.abs(np.fft.rfft(x[:n_fft]))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    e_lo = spec[(freqs >= 500) & (freqs < 1500)].sum()
    e_hi = spec[(freqs >= 1500) & (freqs <= 4000)].sum()
    brightness = float(e_hi / (e_hi + e_lo + 1e-9))
    # crude syllable rate from F0 onsets
    n_voiced_frames = max(1, int(voiced.sum()))
    dur = x.shape[0] / sr
    rate = min(8.0, n_voiced_frames / max(0.5, dur))
    return VoiceFeatures(
        f0_mean_hz=f0_mean, brightness=min(1.0, brightness), speech_rate_syl_per_s=rate
    )


def _frame_rms(x: np.ndarray, frame: int, hop: int) -> np.ndarray:
    out = []
    for i in range(0, len(x) - frame + 1, hop):
        out.append(np.mean(x[i : i + frame] ** 2))
    return np.array(out)
