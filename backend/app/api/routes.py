"""REST + WebSocket routes.

Routes
------
- POST /v1/jobs                text-input job
- POST /v1/jobs/voice         multipart (form meta + file) -> voice job
- POST /v1/jobs/loop          multipart (form meta + file) -> loop job
- GET  /v1/jobs               list jobs (MVP: all)
- GET  /v1/jobs/{id}         detail + assets + lyrics
- POST /v1/jobs/{id}/cancel
- GET  /v1/jobs/{id}/files/{filename}
- GET  /ws/jobs/{id}         progress stream
- GET  /healthz , /metrics    ops

All routes are bound by `mount_routes(app, ctx)` where `ctx` carries the
shared DB / settings / EventBus, so tests can mount into a throwaway
FastAPI instance without global state.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import (
    FastAPI, Header, HTTPException, Query, UploadFile, WebSocket,
    WebSocketDisconnect, Form,
)
from fastapi.responses import FileResponse, PlainTextResponse

from app.api.schemas import (
    AssetDownload,
    CancelJob,
    CreateJobRequest,
    HealthResponse,
    InputType,
    JobCreated,
    JobDetail,
    JobListResponse,
    JobStatus,
    LyricsSection,
    ProgressEvent,
    VoiceJobCreate,
)
from app.observability.logging import request_id_var
from app.persistence.db import DB, Job, new_uuid, utcnow

log = logging.getLogger(__name__)


@dataclass
class RouteCtx:
    """Shared dependencies passed to `mount_routes`."""
    db: DB
    settings: "object"          # app.config.Settings
    bus: "object"              # app.orchestrator.EventBus


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_list(lst: list[str]) -> str:
    return json.dumps(lst or [])


def _mime(name: str) -> str:
    ext = Path(name).suffix.lstrip(".")
    return {"mp3": "audio/mpeg", "wav": "audio/wav",
            "txt": "text/plain; charset=utf-8"}.get(ext, "application/octet-stream")


def _sanitize_filename(name: str) -> str:
    p = Path(name).name
    return p if p and p not in (".", "..") else name


def _parse_lyrics_text(text: Optional[str]) -> list[LyricsSection]:
    if not text:
        return []
    out: list[LyricsSection] = []
    cur: Optional[LyricsSection] = None
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            cur = LyricsSection(section=s[1:-1].lower(), lines=[])
            out.append(cur)
        elif cur is not None:
            cur.lines.append(s)
    return out


def _job_to_detail(job: Job, settings, db: DB) -> JobDetail:
    storage = settings.effective_storage_dir
    assets: list[AssetDownload] = []

    def add(label: str, filename: str, rel: Optional[str]) -> None:
        if not rel:
            return
        full = storage / rel
        size = full.stat().st_size if full.exists() else 0
        assets.append(AssetDownload(
            label=label, filename=filename,
            url=f"/v1/jobs/{job.id}/files/{filename}", size_bytes=int(size),
        ))

    add("Hook preview", "preview.wav", job.preview_wav_path)
    add("Master WAV", "master.wav", job.master_wav_path)
    add("Master MP3", "master.mp3", job.master_mp3_path)
    if job.stems_dir:
        for name in ("drums", "bass", "chords", "melody", "vox"):
            add(f"Stem: {name}", f"{name}.wav", f"{job.stems_dir}/{name}.wav")
    add("Lyrics", "lyrics.txt", job.lyrics_path)

    return JobDetail(
        job_id=job.id,
        status=JobStatus(job.status),
        input_type=InputType(job.input_type),
        duration_secs=job.duration_secs,
        key=job.key,
        bpm=job.bpm,
        cost_usd=round(job.cost_usd or 0.0, 4),
        error=job.error,
        preview_at=job.preview_at.isoformat() if job.preview_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        assets=assets,
        lyrics_sections=_parse_lyrics_text(job.lyrics),
    )


def _require_key(settings, x_api_key: Optional[str]) -> None:
    if getattr(settings, "api_key", None) and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def mount_routes(app: FastAPI, ctx: RouteCtx) -> None:
    settings = ctx.settings
    db = ctx.db
    bus = ctx.bus

    @app.post("/v1/jobs", response_model=JobCreated)
    async def create_text_job(body: CreateJobRequest,
                              x_api_key: Optional[str] = Header(default=None,
                                                             alias="X-API-Key")):
        _require_key(settings, x_api_key)
        job_id = new_uuid()
        with db.scoped() as s:
            s.add(Job(
                id=job_id,
                input_type=body.input_type.value,
                status="queued",
                description=body.description,
                style_hints=_json_list(body.style_hints),
                language=body.language,
                custom_lyrics=body.lyrics,
                duration_secs=body.duration_secs,
            ))
            s.commit()
        return JobCreated(
            job_id=job_id, status=JobStatus.QUEUED,
            created_at=_utc_iso(),
            preview_eta_seconds=int(settings.preview_slo_seconds),
        )

    async def _create_audio_job(kind: str, file: UploadFile,
                                meta: VoiceJobCreate,
                                x_api_key: Optional[str]) -> JobCreated:
        _require_key(settings, x_api_key)
        contents = await file.read()
        if len(contents) > settings.max_upload_bytes:
            raise HTTPException(413, "file too large")
        if len(contents) < 1024:
            raise HTTPException(400, "file too small / empty")
        job_id = new_uuid()
        upload_dir = settings.effective_storage_dir / "uploads" / job_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(file.filename).suffix if file.filename and "." in file.filename else ".bin"
        saved = upload_dir / f"{kind}{ext}"
        saved.write_bytes(contents)
        with db.scoped() as s:
            s.add(Job(
                id=job_id,
                input_type="voice" if kind == "voice" else "loop",
                status="queued",
                description=meta.description or None,
                style_hints=_json_list(meta.style_hints),
                language=meta.language,
                duration_secs=meta.duration_secs,
            ))
            s.commit()
        return JobCreated(
            job_id=job_id, status=JobStatus.QUEUED,
            created_at=_utc_iso(),
            preview_eta_seconds=int(settings.preview_slo_seconds),
        )

    @app.post("/v1/jobs/voice", response_model=JobCreated)
    async def create_voice_job(
        file: UploadFile,
        description: str = Form(default=""),
        style_hints: str = Form(default="[]"),
        duration_secs: int = Form(default=190, ge=60, le=300),
        language: str = Form(default="zh"),
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ):
        meta = VoiceJobCreate(
            description=description,
            style_hints=json.loads(style_hints or "[]"),
            duration_secs=duration_secs,
            language=language,
        )
        return await _create_audio_job("voice", file, meta, x_api_key)

    @app.post("/v1/jobs/loop", response_model=JobCreated)
    async def create_loop_job(
        file: UploadFile,
        description: str = Form(default=""),
        style_hints: str = Form(default="[]"),
        duration_secs: int = Form(default=190, ge=60, le=300),
        language: str = Form(default="zh"),
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ):
        meta = VoiceJobCreate(
            description=description,
            style_hints=json.loads(style_hints or "[]"),
            duration_secs=duration_secs,
            language=language,
        )
        return await _create_audio_job("loop", file, meta, x_api_key)

    @app.get("/v1/jobs", response_model=JobListResponse)
    async def list_jobs(limit: int = Query(20, ge=1, le=100),
                        offset: int = Query(0, ge=0),
                        x_api_key: Optional[str] = Header(default=None,
                                                         alias="X-API-Key")):
        _require_key(settings, x_api_key)
        with db.scoped() as s:
            rows = (s.query(Job).order_by(Job.created_at.desc())
                    .offset(offset).limit(limit).all())
            total = s.query(Job).count()
            items = [_job_to_detail(r, settings, db) for r in rows]
        return JobListResponse(items=items, total=total,
                               limit=limit, offset=offset)

    @app.get("/v1/jobs/{job_id}", response_model=JobDetail)
    async def get_job(job_id: str,
                      x_api_key: Optional[str] = Header(default=None,
                                                       alias="X-API-Key")):
        _require_key(settings, x_api_key)
        with db.scoped() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            return _job_to_detail(job, settings, db)

    @app.post("/v1/jobs/{job_id}/cancel", response_model=CancelJob)
    async def cancel_job(job_id: str,
                         x_api_key: Optional[str] = Header(default=None,
                                                          alias="X-API-Key")):
        _require_key(settings, x_api_key)
        with db.scoped() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            if job.status in ("succeeded", "failed", "cancelled"):
                return CancelJob(ok=False, status=JobStatus(job.status))
            job.status = "cancelled"
            job.updated_at = utcnow()
            s.commit()
            return CancelJob(ok=True, status=JobStatus.CANCELLED)

    @app.get("/v1/jobs/{job_id}/files/{filename}")
    async def download_file(job_id: str, filename: str,
                            x_api_key: Optional[str] = Header(default=None,
                                                           alias="X-API-Key")):
        _require_key(settings, x_api_key)
        safe = _sanitize_filename(filename)
        allowed = {"preview.wav", "master.wav", "master.mp3", "lyrics.txt",
                   "drums.wav", "bass.wav", "chords.wav", "melody.wav",
                   "vox.wav"}
        if safe not in allowed:
            raise HTTPException(400, "file not allowed")
        stem = {"drums", "bass", "chords", "melody", "vox"}.intersection(allowed)
        if safe in stem:
            rel = f"jobs/{job_id}/stems/{safe}"
        else:
            rel = f"jobs/{job_id}/{safe}"
        full = settings.effective_storage_dir / rel
        if not full.exists():
            raise HTTPException(404, "file not ready")
        return FileResponse(full, filename=safe, media_type=_mime(safe))

    @app.websocket("/ws/jobs/{job_id}")
    async def ws_progress(websocket: WebSocket, job_id: str):
        # Browsers cannot set custom headers on a WebSocket handshake, so
        # the key may also be supplied as ?api_key=. REST routes stay
        # header-only; this is the only place we accept the query form.
        if settings.api_key:
            provided = (websocket.headers.get("x-api-key")
                        or websocket.query_params.get("api_key"))
            if provided != settings.api_key:
                await websocket.close(code=4401)
                return
        await websocket.accept()
        loop = asyncio.get_running_loop()

        async def _send_event(ev: ProgressEvent) -> None:
            try:
                await websocket.send_text(json.dumps(ev.model_dump(), default=str))
            except Exception:
                pass

        def bridge(ev: ProgressEvent) -> None:
            # called from worker threads: hop back onto the socket's loop
            if ev.job_id != job_id:
                return
            asyncio.run_coroutine_threadsafe(_send_event(ev), loop)

        bus.subscribe(bridge)

        try:
            # replay current status on (re)connect
            with db.scoped() as s:
                j = s.get(Job, job_id)
                if j is None:
                    await websocket.send_text(json.dumps({
                        "job_id": job_id, "type": "failed",
                        "error": "job not found"}))
                    await websocket.close()
                    return
                await websocket.send_text(json.dumps({
                    "job_id": job_id, "type": "replay", "detail": j.status,
                    "assets": [],
                }))
            while True:
                try:
                    await websocket.receive_text()  # keep-alive ping
                except WebSocketDisconnect:
                    break
        finally:
            bus.unsubscribe(bridge)

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz():
        import app as _app
        return HealthResponse(
            status="ok",
            version=getattr(_app, "__version__", "0.0.0"),
            worker=bool(settings.enable_worker),
            provider=settings.provider,
            db=settings.effective_database_url.split("://", 1)[0],
            storage=str(settings.effective_storage_dir),
        )

    @app.get("/metrics")
    async def metrics_endpoint():
        from app.observability.metrics import render_metrics
        return PlainTextResponse(render_metrics().decode("utf-8"),
                                 media_type="text/plain; version=0.0.4")


__all__ = ["mount_routes", "RouteCtx"]
