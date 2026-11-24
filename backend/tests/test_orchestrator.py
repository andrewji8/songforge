"""End-to-end pipeline tests (orchestrator + cost caps + retry)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.persistence.db import Job, StageAttempt, utcnow


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _seed_text_job(db, description="深夜雨声里想念旧朋友", input_type="text",
                   duration=90, status="queued", cost_cap=None):
    from app.persistence.db import new_uuid

    jid = new_uuid()
    with db.scoped() as s:
        s.add(Job(
            id=jid, input_type=input_type, status=status,
            description=description, duration_secs=duration,
        ))
        s.commit()
    return jid


# ---------------------------------------------------------------------------
# full pipeline
# ---------------------------------------------------------------------------

def test_full_pipeline_text_job_succeeds(settings, db, bus, orchestrator):
    from app.orchestrator import StageBlockedError

    jid = _seed_text_job(db, description="lofi 雨夜想念朋友", duration=80)
    job = orchestrator.run(jid, {})

    # status + output files
    with db.scoped() as s:
        j = s.get(Job, jid)
        assert j.status == "succeeded"
        assert j.master_wav_path and j.master_mp3_path and j.lyrics_path
        assert j.cost_usd > 0
    storage = settings.effective_storage_dir
    assert (storage / j.master_mp3_path).exists() or not _has_ffmpeg()
    assert (storage / f"jobs/{jid}/master.wav").exists()
    assert (storage / f"jobs/{jid}/lyrics.txt").exists()
    # stems written
    stems = storage / f"jobs/{jid}/stems"
    assert (stems / "drums.wav").exists()
    # cost ledger entries recorded
    from app.persistence.db import CostLedger
    with db.scoped() as s:
        events = s.query(CostLedger).filter(CostLedger.job_id == jid).all()
        assert sum(e.amount_usd for e in events) > 0


def test_preview_event_emitted_then_succeeded(bus, orchestrator, settings, db):
    types: list[str] = []

    def capture(ev):
        types.append(ev.type)

    bus.subscribe(capture)
    jid = _seed_text_job(db, duration=70)
    orchestrator.run(jid, {})
    assert "preview_ready" in types
    assert types[-1] == "succeeded"


def test_retry_retries_retriable_failure_then_succeeds(orchestrator, settings, db):
    """Force a single retriable ProviderError on the first compose attempt."""
    from app.orchestrator import Orchestrator, ProviderError

    class FlakyProvider(orchestrator.provider.__class__):
        def __init__(self, inner):
            self._inner = inner
            self._calls = 0

        def compose_full(self, intent, duration_s, seed):
            self._calls += 1
            if self._calls == 1:
                raise ProviderError("transient 503", retriable=True)
            return self._inner.compose_full(intent, duration_s, seed)

        def compose_hook(self, intent, seconds, seed):
            return self._inner.compose_hook(intent, seconds, seed)

    jid = _seed_text_job(db, duration=65)
    flaky = FlakyProvider(orchestrator.provider)
    patched = Orchestrator(settings, db, flaky, orchestrator.bus)
    job = patched.run(jid, {})
    assert flaky._calls >= 2  # retried
    with db.scoped() as s:
        j = s.get(Job, jid)
        assert j.status == "succeeded"


def test_non_retriable_failure_fails_job(orchestrator, settings, db):
    from app.orchestrator import Orchestrator, ProviderError

    class BadProvider(orchestrator.provider.__class__):
        def __init__(self, inner):
            self._inner = inner

        def compose_full(self, intent, duration_s, seed):
            raise ProviderError("model returned no audio", retriable=False)

        def compose_hook(self, intent, seconds, seed):
            return self._inner.compose_hook(intent, seconds, seed)

    jid = _seed_text_job(db, duration=65)
    patched = Orchestrator(settings, db, BadProvider(orchestrator.provider),
                           orchestrator.bus)
    with pytest.raises(Exception):
        patched.run(jid, {})
    with db.scoped() as s:
        j = s.get(Job, jid)
        assert j.status == "failed"
        assert j.error


def test_job_budget_cap_blocks_compose(orchestrator, settings, db):
    from app.orchestrator import Orchestrator, StageBlockedError

    # make full-compose bill above the per-job cap so the gate trips
    settings.pricing_full_usd = 10.0
    settings.job_budget_usd = 2.0
    jid = _seed_text_job(db, duration=65)
    patched = Orchestrator(settings, db, orchestrator.provider, orchestrator.bus)
    with pytest.raises(StageBlockedError):
        patched.run(jid, {})
    with db.scoped() as s:
        j = s.get(Job, jid)
        assert j.status == "failed"
        assert "cost cap" in (j.error or "")


def test_stage_attempts_recorded(orchestrator, settings, db):
    jid = _seed_text_job(db, duration=65)
    orchestrator.run(jid, {})
    with db.scoped() as s:
        rows = s.query(StageAttempt).filter(StageAttempt.job_id == jid).all()
        stages = {r.stage for r in rows}
        assert "intent" in stages and "compose" in stages and "export" in stages


# ---------------------------------------------------------------------------
# loop input (needs ffmpeg)
# ---------------------------------------------------------------------------

def test_loop_input_end_to_end_when_ffmpeg(settings, db, orchestrator):
    if not _has_ffmpeg():
        pytest.skip("ffmpeg not available on CI")
    from app.dsp.wav_io import Audio
    import numpy as np

    # craft a short 120 BPM-ish loop
    sr = 44100
    t = np.arange(sr * 4) / sr
    sig = (np.sin(2 * np.pi * 220 * t) * 0.3
           + np.random.default_rng(0).standard_normal(sr * 4).astype(np.float32) * 0.05)
    loop_wav = Audio(np.column_stack([sig, sig]).astype(np.float32), sr)
    loop_path = settings.effective_storage_dir / "loop_in.wav"
    loop_path.parent.mkdir(parents=True, exist_ok=True)
    loop_wav.save_wav(loop_path)

    jid = _seed_text_job(db, input_type="loop", duration=70)
    uploads = {"loop": str(loop_path)}
    job = orchestrator.run(jid, uploads)
    with db.scoped() as s:
        j = s.get(Job, job.id)
        assert j.status == "succeeded"


# ---------------------------------------------------------------------------
# utilities
# ---------------------------------------------------------------------------

def _has_ffmpeg() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


__all__ = []
