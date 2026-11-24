"""Unit tests: DSP primitives + synthesis + mastering + loudness.

These cover the correctness of the audio engine independently of the
orchestrator / API.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.dsp.core import db_to_gain, limiter, one_pole_lowpass, soft_knee_compressor
from app.dsp.loudness import momentary_lufs, true_peak_db
from app.dsp.mastering import MixConfig, master, mix_stems
from app.dsp.synthesis import (
    Intent,
    parse_key_key_to_intent,
    render_hook,
    render_song,
    section_map_for,
    VoiceTimbre,
)
from app.dsp.wav_io import Audio


# ---------------------------------------------------------------------------
# wav io
# ---------------------------------------------------------------------------

def test_audio_silence_shape_and_duration():
    a = Audio.silence(1.0, sr=44100, channels=2)
    assert a.num_frames == 44100
    assert a.channels == 2
    assert a.duration_s == pytest.approx(1.0, abs=1e-6)


def test_audio_from_to_wav_roundtrip(tmp_path):
    a = Audio.silence(0.5, sr=22050, channels=1)
    p = tmp_path / "x.wav"
    a.save_wav(p)
    b = Audio.from_wav(p)
    assert b.num_frames == a.num_frames
    assert b.sample_rate == 22050


def test_audio_resample_changes_rate():
    a = Audio(np.zeros(4410, dtype=np.float32), 44100)
    b = a.resample(22050)
    assert b.sample_rate == 22050
    assert b.num_frames == 2205


# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------

def test_db_to_gain_identity():
    assert db_to_gain(0.0) == 1.0
    assert db_to_gain(6.02) == pytest.approx(2.0, abs=1e-2)


def test_limiter_reduces_peaks():
    sr = 44100
    x = np.ones(sr, dtype=np.float32) * 0.95
    y = limiter(x, ceiling_db=-6.0, sample_rate=sr)
    assert np.abs(y).max() <= 0.55 + 1e-3  # ~-6 dB ceiling
    assert len(y) == len(x)


def test_one_pole_lowpass_reduces_high_freq():
    sr = 44100
    t = np.arange(sr) / sr
    x = (np.sin(2 * np.pi * 200 * t) * 0.5
         + np.sin(2 * np.pi * 8000 * t)).astype(np.float32)
    y = one_pole_lowpass(x, 1000.0, sr)
    # high freq component should be attenuated: energy in >5k band smaller
    spec_x = np.abs(np.fft.rfft(x))
    spec_y = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    hi = freqs > 5000
    assert spec_y[hi].sum() < spec_x[hi].sum()


def test_soft_knee_compressor_applies_gain():
    sr = 44100
    x = (np.sin(2 * np.pi * 100 * np.arange(sr) / sr) * 0.9).astype(np.float32)
    y = soft_knee_compressor(x, threshold_db=-18, ratio=4.0,
                             attack_s=0.01, release_s=0.15,
                             makeup_db=0.0, sample_rate=sr)
    assert len(y) == len(x)
    # compression reduces peak vs input (no makeup gain)
    assert np.abs(y).max() <= np.abs(x).max() + 1e-3


# ---------------------------------------------------------------------------
# loudness
# ---------------------------------------------------------------------------

def _tone_seconds(seconds: float, freq: float = 440.0, sr: int = 44100) -> Audio:
    t = np.arange(int(seconds * sr)) / sr
    x = (np.sin(2 * np.pi * freq * t) * 0.3).astype(np.float32)
    return Audio(x, sr)


def test_momentary_lufs_of_known_tone_is_near_target():
    a = _tone_seconds(3.0)
    lufs = momentary_lufs(a)
    # ffmpeg-verified: a 0.3-amp 440 Hz sine is ~-9 LUFS; the simplified
    # in-process K-weighting (HP-only) tracks it within a few LU.
    assert -14.0 < lufs < -4.0, f"got {lufs}"


def test_true_peak_db_within_ceiling_after_limiter():
    sr = 44100
    x = np.random.default_rng(0).standard_normal(sr).astype(np.float32) * 0.8
    limited = limiter(x, ceiling_db=-1.0, sample_rate=sr)
    out = Audio(limited, sr)
    tp = true_peak_db(out)
    assert tp <= -1.0 + 0.3


def test_lufs_of_quiet_signal_is_lower():
    loud = _tone_seconds(2.0, freq=440.0)
    quiet = Audio(loud.mono().data * 0.25, loud.sample_rate)
    assert momentary_lufs(quiet) < momentary_lufs(loud)


# ---------------------------------------------------------------------------
# mastering
# ---------------------------------------------------------------------------

def _stems_from_hook(intent: Intent, seconds: float, seed: int = 1) -> dict:
    return render_hook(intent, seconds, seed)


def test_mix_stems_output_shape():
    intent = parse_key_key_to_intent("Am", 90, 0.5)
    stems = _stems_from_hook(intent, 3.0, seed=1)
    mix = mix_stems(stems, 44100)
    assert mix.ndim == 2 and mix.shape[1] == 2  # (samples, 2)


def test_master_hits_loudness_target():
    intent = parse_key_key_to_intent("Am", 90, 0.5)
    stems = _stems_from_hook(intent, 4.0, seed=3)
    mix = mix_stems(stems, 44100)
    res = master(mix, 44100, target_lufs=-14.0, true_peak_db=-1.0)
    # in-process first pass lands safely below full scale
    assert res.audio.sample_rate == 44100
    assert res.audio.data.ndim == 2
    # no clipping after the true-peak limit
    assert np.abs(res.audio.data).max() <= 1.0 + 1e-3


def test_master_no_clipping_after_gain():
    intent = parse_key_key_to_intent("C", 120, 0.8)
    stems = _stems_from_hook(intent, 4.0, seed=5)
    mix = mix_stems(stems, 44100)
    res = master(mix, 44100, target_lufs=-14.0, true_peak_db=-1.0)
    assert np.abs(res.audio.data).max() <= 1.0 + 1e-3


def test_finalize_master_with_ffmpeg_reaches_target():
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    from app.dsp.mastering import finalize_master

    intent = parse_key_key_to_intent("Am", 90, 0.5)
    stems = _stems_from_hook(intent, 6.0, seed=7)
    mix = mix_stems(stems, 44100)
    res = master(mix, 44100, target_lufs=-14.0, true_peak_db=-1.0)
    import tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "master.wav")
    res.audio.save_wav(p)
    lufs, tp, used = finalize_master(p, 44100, -14.0, -1.0)
    assert used is True
    assert abs(lufs - (-14.0)) < 1.5, f"final lufs {lufs}"
    assert tp <= -1.0 + 0.5, f"final tp {tp}"


# ---------------------------------------------------------------------------
# synthesis
# ---------------------------------------------------------------------------

def test_section_map_sums_to_duration():
    sm = section_map_for(190.0)
    assert sum(w for _, w in sm) == pytest.approx(190.0, abs=1.5)
    assert sm[0][0] == "intro"
    assert sm[-1][0] == "outro"


def test_render_song_all_stems_present_and_aligned():
    intent = parse_key_key_to_intent("Am", 90, 0.5)
    stems, smap = render_song(intent, 30.0, seed=42)
    for k in ("drums", "bass", "chords", "melody", "vox"):
        assert k in stems, f"missing {k}"
        assert stems[k].ndim == 1
    # all stems share the same sample count
    lens = {len(v) for v in stems.values()}
    assert len(lens) == 1
    assert next(iter(lens)) == int(30.0 * 44100)


def test_render_song_deterministic_per_seed():
    intent = parse_key_key_to_intent("Dm", 100, 0.6)
    a, _ = render_song(intent, 10.0, seed=7)
    b, _ = render_song(intent, 10.0, seed=7)
    for k in a:
        assert np.allclose(a[k], b[k], atol=1e-5), f"stem {k} not deterministic"


def test_render_song_different_seeds_differ():
    intent = parse_key_key_to_intent("Am", 90, 0.5)
    a, _ = render_song(intent, 10.0, seed=1)
    b, _ = render_song(intent, 10.0, seed=2)
    assert not np.allclose(a["melody"], b["melody"], atol=1e-3)


def test_voice_timbre_changes_vocal_stub():
    base = parse_key_key_to_intent("Am", 90, 0.5)
    v1 = parse_key_key_to_intent("Am", 90, 0.5, voice=VoiceTimbre(f0_mean_hz=120))
    v2 = parse_key_key_to_intent("Am", 90, 0.5, voice=VoiceTimbre(f0_mean_hz=260))
    a, _ = render_song(base, 6.0, seed=5)
    b, _ = render_song(v1, 6.0, seed=5)
    c, _ = render_song(v2, 6.0, seed=5)
    # different f0 should shift the vocal stem's spectral content
    assert not np.allclose(b["vox"], c["vox"], atol=1e-4)
