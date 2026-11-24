"""Shared fixtures: isolated Settings (tmp storage + in-memory-ish sqlite),
a DB, an EventBus, and an embedded-worker-disabled app."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# make `backend` importable when running from repo root
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def tmp_storage(tmp_path):
    return tmp_path / "storage"


@pytest.fixture()
def settings(tmp_storage, tmp_path):
    from app.config import Settings, reset_settings_cache

    reset_settings_cache()
    s = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_storage,
        enable_worker=False,
        max_stage_attempts=2,
        stage_retry_base_delay_s=0.0,
        stage_retry_max_delay_s=0.0,
        provider="simulated",
        # no LLM in tests: deterministic fallbacks
        llm_base_url=None,
        llm_api_key=None,
        api_key=None,
    )
    yield s
    reset_settings_cache()


@pytest.fixture()
def db(settings):
    from app.persistence.db import DB, create_engine_and_session

    engine, factory = create_engine_and_session(settings)
    yield DB(factory)
    engine.dispose()


@pytest.fixture()
def bus():
    from app.orchestrator import EventBus

    return EventBus()


@pytest.fixture()
def orchestrator(settings, db, bus):
    from app.orchestrator import Orchestrator
    from app.providers.music_provider import build_provider

    return Orchestrator(settings, db, build_provider(settings), bus)


@pytest.fixture()
def app(settings, db, bus):
    from app.main import create_app

    return create_app(settings, run_embedded_worker=False)


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
