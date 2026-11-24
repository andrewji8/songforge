"""Core DSP primitives built on numpy/scipy."""
from __future__ import annotations

import numpy as np
from scipy import signal as sps


def db_to_gain(db: float) -> float:
    return 10 ** (db / 20.0)


def apply_envelope(
    x: np.ndarray, attack_s: float, release_s: float, sample_rate: int
) -> np.ndarray:
    """Zero-latency linear envelope to avoid clicks on onset/offset."""
    n = x.shape[0]
    up = max(1, int(attack_s * sample_rate))
    down = max(1, int(release_s * sample_rate))
    up = min(up, n)
    down = min(down, n)
    env = np.ones(n, dtype=np.float32)
    if up > 1:
        env[:up] = np.linspace(0.0, 1.0, up, dtype=np.float32)
    if down > 1:
        env[-down:] *= np.linspace(1.0, 0.0, down, dtype=np.float32)
    return x * env


def one_pole_lowpass(x: np.ndarray, cutoff_hz: float, sample_rate: int) -> np.ndarray:
    """First-order low-pass (RC, one-pole), vectorized via scipy.lfilter (C).

    y[n] = a*y[n-1] + alpha*x[n],  a = 1 - alpha,
    alpha = dt / (RC + dt), RC = 1 / (2*pi*f_c).
    """
    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    dt = 1.0 / sample_rate
    alpha = dt / (rc + dt)
    a_coef = 1.0 - alpha
    b = np.array([alpha])
    a = np.array([1.0, -a_coef])
    y = sps.lfilter(b, a, x.astype(np.float64))
    return y.astype(np.float32)


def biquad_bandpass(
    x: np.ndarray, freqs: list[float], q: float, sample_rate: int
) -> np.ndarray:
    """Cascade of cascaded second-order Butterworth band-passes."""
    out = x.astype(np.float64)
    for f in freqs:
        b, a = sps.butter(2, f, btype="bandpass", fs=sample_rate, analog=False)
        out = sps.filtfilt(b, a, out)
    return out.astype(np.float32)


def highpass_biquad(x: np.ndarray, cutoff_hz: float, sample_rate: int) -> np.ndarray:
    b, a = sps.butter(2, cutoff_hz, btype="highpass", fs=sample_rate, analog=False)
    return sps.filtfilt(b, a, x.astype(np.float64)).astype(np.float32)


def soft_knee_compressor(
    x: np.ndarray,
    threshold_db: float,
    ratio: float,
    attack_s: float,
    release_s: float,
    makeup_db: float = 0.0,
    sample_rate: int = 44100,
) -> np.ndarray:
    """Envelope-following compressor (mono), fully vectorized.

    The smoothed envelope is approximated by the standard dual one-pole
    trick: a fast forward filter (attack) and a slow backward filter
    (release); env = max of both.
    """
    x = np.asarray(x, dtype=np.float64)
    env = np.abs(x)

    a_atk = np.exp(-1.0 / max(1.0, attack_s * sample_rate))
    a_rel = np.exp(-1.0 / max(1.0, release_s * sample_rate))

    # fast attack: one-pole forward
    b_atk = 1.0 - a_atk
    fwd = sps.lfilter(np.array([b_atk]), np.array([1.0, -a_atk]), env)
    # slow release: one-pole backward (run forward on reversed signal)
    b_rel = 1.0 - a_rel
    rev = sps.lfilter(
        np.array([b_rel]), np.array([1.0, -a_rel]), env[::-1]
    )[::-1]
    env_f = np.maximum(fwd, rev)

    level_db = 20.0 * np.log10(np.maximum(env_f, 1e-9))
    over = np.maximum(level_db - threshold_db, 0.0)
    reduction = over * (1.0 - 1.0 / ratio)
    gain = db_to_gain(-reduction + makeup_db)
    return (x * gain).astype(np.float32)


def limiter(
    x: np.ndarray, ceiling_db: float, sample_rate: int, look_ms: float = 3.0
) -> np.ndarray:
    """Offline hard ceiling with a short look-ahead window.

    Sample i is scaled so the peak over the next `look_ms` (the offline
    look-ahead) does not exceed `ceiling_db`. Vectorized with a strided
    sliding-window max over a zero-padded tail, so the *entire* signal
    (including the final samples) is limited with no per-sample loop.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    ceiling = db_to_gain(ceiling_db)
    x64 = x.astype(np.float64)
    n = x64.shape[0]
    la = max(2, int(look_ms * 1e-3 * sample_rate))
    peak = np.abs(x64)
    if n <= la:
        win_max = np.maximum(peak, 1e-12)
        gain = np.where(win_max > ceiling, ceiling / win_max, 1.0)
        return (x64 * gain).astype(np.float32)
    # zero-pad the tail so every position has a full look-ahead window
    peak_pad = np.concatenate([peak, np.zeros(la)])
    windows = sliding_window_view(peak_pad, la)     # shape (n + 1, la)
    win_max = windows.max(axis=1)                   # max over next `la` samples
    win_max = np.maximum(win_max, 1e-12)
    gain = np.where(win_max > ceiling, ceiling / win_max, 1.0)
    gain = gain[:n]
    return (x64 * gain).astype(np.float32)
