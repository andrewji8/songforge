"""Frontend contract regression: the SPA sends metadata as multipart FORM
fields (browser FormData). Verify the voice/loop endpoints accept that
shape and persist description/duration/style_hints/language correctly.

(Standalone script `verify_frontend_shape.py` was promoted into this test.)
"""
from __future__ import annotations

import shutil

import numpy as np
import pytest

from app.persistence.db import Job


def test_frontend_formdata_shape_persists_metadata(client, app):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required to decode the uploaded clip")

    from app.dsp.wav_io import Audio

    sr = 44100
    t = np.arange(int(4.0 * sr)) / sr
    sig = (np.sin(2 * np.pi * 180.0 * t) * 0.3).astype(np.float32)

    r = client.post(
        "/v1/jobs/voice",
        data={
            "description": "gentle lofi hum",
            "style_hints": '["lofi"]',
            "duration_secs": "70",
            "language": "zh",
        },
        files={"file": ("clip.wav", _wav_bytes(sig, sr), "audio/wav")},
    )
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    with app.state.db.scoped() as s:
        j = s.get(Job, jid)
        assert j.description == "gentle lofi hum"
        assert j.duration_secs == 70
        assert '"lofi"' in j.style_hints
        assert j.language == "zh"
        assert j.input_type == "voice"
        assert j.status == "queued"


def _wav_bytes(sig, sr):
    """Minimal PCM16 WAV bytes so the test doesn't need a real file on disk."""
    import io
    import wave

    pcm = (np.clip(sig, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()
