"""Regression tests for the integration fixes.

Covers the critical paths that the original suite missed:
- /metrics returns *valid* Prometheus text (not JSON-quoted)
- WebSocket progress frames are actually delivered to the client
- X-API-Key is enforced as a *header* on /v1 routes
- voice/loop uploads are found by the worker and run end to end
"""
from __future__ import annotations

import shutil

import numpy as np
import pytest

from app.persistence.db import Job


def test_metrics_endpoint_is_valid_prometheus_text(client):
    from prometheus_client.parser import text_string_to_metric_families

    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    # JSONResponse would wrap the body in quotes + escape newlines; the
    # parser would then yield nothing.
    families = list(text_string_to_metric_families(r.text))
    assert any(f.name.startswith("sf_") for f in families)


def test_ws_progress_delivers_events(client, app):
    from app.api.schemas import ProgressEvent

    r = client.post("/v1/jobs", json={"description": "websocket progress test"})
    jid = r.json()["job_id"]

    with client.websocket_connect(f"/ws/jobs/{jid}") as ws:
        replay = ws.receive_json()
        assert replay["type"] == "replay"

        # publishing from this (non-loop) thread must hop onto the socket
        # loop and actually reach the client
        app.state.bus.publish(
            ProgressEvent(job_id=jid, type="stage_done", stage="intent")
        )
        ev = ws.receive_json()
        assert ev["type"] == "stage_done"
        assert ev["stage"] == "intent"

        # events for other jobs are filtered out
        app.state.bus.publish(
            ProgressEvent(job_id="some-other-job", type="stage_done", stage="compose")
        )
        app.state.bus.publish(
            ProgressEvent(job_id=jid, type="preview_ready", stage="preview",
                          preview_url=f"/v1/jobs/{jid}/files/preview.wav")
        )
        ev2 = ws.receive_json()
        assert ev2["type"] == "preview_ready"


def test_api_key_enforced_as_header(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'key.db'}",
        storage_dir=tmp_path / "storage",
        enable_worker=False,
        api_key="s3cret",
        llm_base_url=None,
        llm_api_key=None,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.get("/v1/jobs").status_code == 401
        assert c.get("/v1/jobs", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/v1/jobs", headers={"X-API-Key": "s3cret"}).status_code == 200
        # the key must not be accepted as a query parameter on REST routes
        assert c.get("/v1/jobs", params={"X-API-Key": "s3cret"}).status_code == 401


def test_voice_upload_flows_through_worker(client, app, tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required to decode the uploaded clip")

    from app.dsp.wav_io import Audio

    sr = 44100
    t = np.arange(int(4.0 * sr)) / sr
    sig = (np.sin(2 * np.pi * 180.0 * t) * 0.3).astype(np.float32)
    src = tmp_path / "voice.wav"
    Audio(np.column_stack([sig, sig]), sr).save_wav(src)

    r = client.post(
        "/v1/jobs/voice",
        data={"description": "gentle lofi hum", "duration_secs": "70",
              "language": "zh"},
        files={"file": ("voice.wav", src.read_bytes(), "audio/wav")},
    )
    assert r.status_code == 200
    jid = r.json()["job_id"]

    # exactly what the poll loop does: resolve uploads/{id}/voice.* then run
    app.state.worker._run_job(jid)

    with app.state.db.scoped() as s:
        job = s.get(Job, jid)
        assert job.status == "succeeded"
        assert job.key
        assert job.master_wav_path
