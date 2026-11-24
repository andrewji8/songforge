"""API layer tests via FastAPI TestClient."""
from __future__ import annotations

import json

import pytest


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["provider"] in ("simulated", "http")


def test_metrics_endpoint(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "sf_" in r.text or "{# " in r.text  # prom text format


def test_create_text_job_queued(client, db):
    r = client.post("/v1/jobs", json={
        "description": "lofi 雨夜想念旧朋友",
        "input_type": "text",
        "duration_secs": 90,
        "language": "zh",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["job_id"]
    with db.scoped() as s:
        from app.persistence.db import Job
        j = s.get(Job, body["job_id"])
        assert j.status == "queued"
        assert json.loads(j.style_hints or "[]") == []


def test_list_and_get_job(client):
    r = client.post("/v1/jobs", json={
        "description": "upbeat summer pop", "style_hints": ["pop"],
    })
    jid = r.json()["job_id"]
    lr = client.get("/v1/jobs")
    assert lr.status_code == 200
    assert any(item["job_id"] == jid for item in lr.json()["items"])
    gr = client.get(f"/v1/jobs/{jid}")
    assert gr.status_code == 200
    assert gr.json()["input_type"] == "text"


def test_get_missing_job_404(client):
    assert client.get("/v1/jobs/does-not-exist").status_code == 404


def test_cancel_queued_job(client):
    r = client.post("/v1/jobs", json={"description": "cinematic trailer theme"})
    jid = r.json()["job_id"]
    cr = client.post(f"/v1/jobs/{jid}/cancel")
    assert cr.status_code == 200
    assert cr.json()["status"] == "cancelled"
    # cancelling a cancelled job is a no-op
    cr2 = client.post(f"/v1/jobs/{jid}/cancel")
    assert cr2.json()["ok"] is False


def test_invalid_description_rejected(client):
    r = client.post("/v1/jobs", json={"description": "ab"})  # too short
    assert r.status_code == 422


def test_voice_upload_too_small_rejected(client):
    r = client.post(
        "/v1/jobs/voice",
        files={"file": ("clip.wav", b"x", "audio/wav")},
    )
    assert r.status_code == 400


def test_file_download_not_ready_404(client):
    r = client.post("/v1/jobs", json={"description": "sad piano ballad rain"})
    jid = r.json()["job_id"]
    # job is queued (not run), so master files don't exist yet
    assert client.get(f"/v1/jobs/{jid}/files/master.mp3").status_code == 404


def test_file_download_rejects_unlisted(client):
    r = client.post("/v1/jobs", json={"description": "calm study ambient"})
    jid = r.json()["job_id"]
    assert client.get(f"/v1/jobs/{jid}/files/..%2F..%2Fetc").status_code in (400, 404)
