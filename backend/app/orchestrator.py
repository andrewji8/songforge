"""Pipeline orchestrator.

Runs the deterministic stage graph for a job:

    intent -> lyrics -> hook_preview -> full_compose -> mix_master
         -> export_mp3 -> done

Design goals
------------
- Fast feedback: the `preview` stage renders only the chorus (hook),
  applies light mastering, and emits a `preview_ready` event as soon as
  possible (25 s SLO target).
- Retryable: every stage is wrapped with a bounded retry loop +
  exponential backoff. Retriable failures re-run the stage;
  non-retriable ones fail the job.
- Cost-controlled: a `CostLedger` records per-stage spend. Before a
  billed stage the job/daily budget is checked; on cap-exceeded the job
  is marked failed with a clear error and no further spend.
- Observable: structured logs, Prometheus metrics, and progress events
  on an in-process `EventBus` (replaced by a real broker in prod).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from sqlalchemy import func as sa_func

from app.api.schemas import AssetDownload, ProgressEvent
from app.config import Settings
from app.dsp import encoders
from app.dsp.mastering import MixConfig, finalize_master, master, mix_stems
from app.dsp.synthesis import Intent, parse_key_key_to_intent
from app.dsp.wav_io import Audio
from app.observability import metrics
from app.observability.logging import job_id_var
from app.persistence.db import DB, Job, StageAttempt, utcnow
from app.providers.music_provider import ComposeResult, MusicProvider, ProviderError
from app.services.ingest import AudioIngest, IngestError
from app.services.songforge import SongForgeService, seed_from_job
from app.persistence.db import CostLedger

log = logging.getLogger(__name__)


class StageBlockedError(RuntimeError):
    """Raised when a stage's billed amount would exceed a cost cap."""

    def __init__(self, stage: str, spent: float, cap: float):
        self.stage, self.spent, self.cap = stage, spent, cap
        super().__init__(
            f"cost cap exceeded at stage {stage!r}: ${spent:.4f} > ${cap:.2f}"
        )


class EventBus:
    """In-process pub/sub for progress events.

    The API layer bridges these to WebSockets; a broker-backed
    implementation swaps in here without touching the orchestrator.
    """

    def __init__(self) -> None:
        self._subs: list[Callable[[ProgressEvent], None]] = []

    def subscribe(self, cb: Callable[[ProgressEvent], None]) -> None:
        self._subs.append(cb)

    def unsubscribe(self, cb: Callable[[ProgressEvent], None]) -> None:
        try:
            self._subs.remove(cb)
        except ValueError:
            pass

    def publish(self, event: ProgressEvent) -> None:
        for cb in self._subs:
            try:
                cb(event)
            except Exception:
                log.exception("event bus subscriber failed", extra={"job": event.job_id})


@dataclass
class JobContext:
    job: Job
    settings: Settings
    db: DB
    provider: MusicProvider
    service: SongForgeService
    ingest: AudioIngest
    bus: EventBus
    out_dir: str = ""
    preview_rel: str = ""
    intent: Optional[Intent] = None
    intent_meta: dict = field(default_factory=dict)
    intent_seed: int = 0
    lyrics: Optional[dict] = None
    last_compose: Optional[ComposeResult] = None
    voice_timbre: Optional[object] = None
    loop_analysis: Optional[object] = None

    @property
    def job_id(self) -> str:
        return self.job.id

    @property
    def storage_root(self) -> Path:
        return self.settings.effective_storage_dir


class Orchestrator:
    """Execute a single job through the stage graph."""

    def __init__(
        self,
        settings: Settings,
        db: DB,
        provider: MusicProvider,
        bus: EventBus,
    ) -> None:
        self.settings = settings
        self.db = db
        self.provider = provider
        self.bus = bus
        self.service = SongForgeService(settings)
        self.ingest = AudioIngest(settings)

    # ------------------------------------------------------------------
    def run(self, job_id: str, uploaded_paths: dict[str, str]) -> Job:
        """Run the whole pipeline for a queued job. `uploaded_paths` maps
        input_type -> absolute file path (voice/loop only)."""
        token = job_id_var.set(job_id)
        metrics.JOBS_IN_FLIGHT.inc()
        t0 = time.monotonic()
        ctx: Optional[JobContext] = None
        try:
            ctx = self._load_context(job_id, uploaded_paths)
            self._run_stage(ctx, "intent")
            self._run_stage(ctx, "lyrics")

            # fast-feedback hook preview (free, not billed)
            ctx.preview_rel = self._run_stage(ctx, "preview")
            preview_latency = time.monotonic() - t0
            slo_met = "yes" if preview_latency <= self.settings.preview_slo_seconds else "no"
            metrics.PREVIEW_LATENCY.labels(slo_met).observe(preview_latency)
            self._set_status(ctx, "preview_ready")
            self._emit(ctx.job, "preview_ready", "preview",
                       detail=f"hook ready in {preview_latency:.1f}s",
                       preview_url=self._asset_url(ctx, ctx.preview_rel))

            self._run_stage(ctx, "compose", billed=self.settings.pricing_full_usd)
            self._run_stage(ctx, "mix_master", billed=self.settings.pricing_mix_master_usd)
            export_paths = self._run_stage(ctx, "export")

            assets = self._build_assets(ctx, export_paths)
            self._finalize(ctx, export_paths, assets)
            return ctx.job
        except StageBlockedError as e:
            self._fail(ctx, job_id, f"blocked: {e}", "failed")
            raise
        except (ProviderError, IngestError) as e:
            self._fail(ctx, job_id, str(e), "failed")
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("job failed: %s", job_id)
            self._fail(ctx, job_id, f"internal: {e}", "failed")
            raise
        finally:
            metrics.JOBS_IN_FLIGHT.dec()
            job_id_var.reset(token)

    # ------------------------------------------------------------------
    # context
    # ------------------------------------------------------------------
    def _load_context(self, job_id: str, uploaded_paths: dict[str, str]) -> JobContext:
        with self.db.scoped() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise ValueError(f"unknown job {job_id}")
            job.status = "running"
            job.updated_at = utcnow()
            s.flush()
        ctx = JobContext(
            job=job, settings=self.settings, db=self.db, provider=self.provider,
            service=self.service, ingest=self.ingest, bus=self.bus,
            out_dir=f"jobs/{job_id}",
        )
        ctx.intent_seed = seed_from_job(job_id)

        # ingest audio inputs before the intent stage
        if job.input_type == "voice" and uploaded_paths.get("voice"):
            _, vt = self.ingest.ingest_voice(
                uploaded_paths["voice"], ctx.storage_root / ctx.out_dir
            )
            ctx.voice_timbre = vt
        elif job.input_type == "loop" and uploaded_paths.get("loop"):
            _audio, analysis = self.ingest.ingest_loop(
                uploaded_paths["loop"], ctx.storage_root / ctx.out_dir
            )
            ctx.loop_analysis = analysis
            ctx.intent = parse_key_key_to_intent(
                analysis.key_hint, analysis.bpm, analysis.energy, voice=None
            )
            ctx.intent_meta = {
                "genre": "loop-derived", "mood": "aligned", "source": "loop_analysis",
                "bpm": analysis.bpm, "key": analysis.key_hint,
            }
        return ctx

    # ------------------------------------------------------------------
    # stage runner (retry + cost gate)
    # ------------------------------------------------------------------
    def _run_stage(self, ctx: JobContext, stage: str, billed: float = 0.0) -> object:
        max_attempts = self.settings.max_stage_attempts
        last_err: Optional[Exception] = None
        # Cost gate: verify the budget *before* any billed work runs, so a
        # job that would exceed a cap never spends money it cannot account
        # for. The spend is recorded only after the stage succeeds.
        if billed > 0:
            self._check_budget(ctx, stage, billed)
        for attempt in range(1, max_attempts + 1):
            with self.db.scoped() as s:
                s.add(StageAttempt(job_id=ctx.job.id, stage=stage, attempt=attempt))
                s.flush()
            start = time.monotonic()
            try:
                result = self._execute_stage(ctx, stage)
                with self.db.scoped() as s:
                    att = self._last_attempt(s, ctx.job.id, stage)
                    att.status = "ok"
                    att.finished_at = utcnow()
                    if billed > 0:
                        self._record_cost(s, ctx, stage, billed)
                    s.commit()
                metrics.STAGE_DURATION.labels(stage).observe(time.monotonic() - start)
                if billed > 0:
                    metrics.COST_BILLED.labels(stage).inc(billed)
                self._emit(ctx.job, "stage_done", stage)
                return result
            except StageBlockedError:
                self._mark_attempt_failed(ctx, stage, "budget_blocked")
                raise
            except (ProviderError, IngestError) as e:
                retriable = getattr(e, "retriable", True)
                last_err = e
                self._mark_attempt_failed(ctx, stage, f"failed: {e}")
                metrics.STAGE_DURATION.labels(stage).observe(time.monotonic() - start)
                if not retriable or attempt >= max_attempts:
                    raise
                delay = min(
                    self.settings.stage_retry_max_delay_s,
                    self.settings.stage_retry_base_delay_s * (2 ** (attempt - 1)),
                )
                log.warning("stage %s attempt %d retriable: %s; retry in %.1fs",
                            stage, attempt, e, delay, extra={"stage": stage})
                time.sleep(delay)
            except Exception as e:  # noqa: BLE001
                last_err = e
                self._mark_attempt_failed(ctx, stage, f"failed: {e}")
                if attempt >= max_attempts:
                    raise
                time.sleep(self.settings.stage_retry_base_delay_s)
        if last_err is not None:
            raise last_err
        raise RuntimeError(f"stage {stage} exited without a result")

    def _execute_stage(self, ctx: JobContext, stage: str) -> object:
        return {
            "intent": self._stage_intent,
            "lyrics": self._stage_lyrics,
            "preview": self._stage_preview,
            "compose": self._stage_compose,
            "mix_master": self._stage_mix_master,
            "export": self._stage_export,
        }[stage](ctx)

    # ------------------------------------------------------------------
    # stages
    # ------------------------------------------------------------------
    def _stage_intent(self, ctx: JobContext) -> Intent:
        if ctx.intent is not None:
            return ctx.intent
        intent, meta = self.service.build_intent(
            ctx.job.description or "",
            self._style_hints_list(ctx.job),
            ctx.job.id,
            voice=ctx.voice_timbre,
        )
        ctx.intent, ctx.intent_meta = intent, meta
        with self.db.scoped() as s:
            j = s.get(Job, ctx.job.id)
            j.parsed_intent = json.dumps(meta)
            j.key, j.bpm = intent.key, intent.bpm
            s.commit()
        return intent

    def _stage_lyrics(self, ctx: JobContext) -> dict:
        res = self.service.generate_lyrics(
            ctx.job.description or "",
            ctx.job.language or "zh",
            ctx.intent_meta.get("genre", "pop"),
            ctx.job.custom_lyrics,
        )
        ctx.lyrics = res
        with self.db.scoped() as s:
            j = s.get(Job, ctx.job.id)
            j.lyrics = _lyrics_to_text(res)
            s.commit()
        return res

    def _stage_preview(self, ctx: JobContext) -> str:
        out = ctx.storage_root / ctx.out_dir
        out.mkdir(parents=True, exist_ok=True)
        stems = self.provider.compose_hook(
            ctx.intent, self.settings.preview_seconds, ctx.intent_seed
        )
        mix = mix_stems(stems, self.settings.sample_rate)
        cfg = MixConfig(target_lufs=self.settings.target_lufs,
                        true_peak_db=self.settings.true_peak_db,
                        sample_rate=self.settings.sample_rate)
        mres = master(mix, self.settings.sample_rate,
                      target_lufs=cfg.target_lufs, true_peak_db=cfg.true_peak_db,
                      glue=cfg.glue)
        rel = f"jobs/{ctx.job.id}/preview.wav"
        mres.audio.save_wav(ctx.storage_root / rel)
        return rel

    def _stage_compose(self, ctx: JobContext) -> ComposeResult:
        res = self.provider.compose_full(
            ctx.intent, ctx.job.duration_secs, ctx.intent_seed
        )
        ctx.last_compose = res
        return res

    def _stage_mix_master(self, ctx: JobContext) -> str:
        res = ctx.last_compose
        out = ctx.storage_root / ctx.out_dir
        (out / "stems").mkdir(parents=True, exist_ok=True)
        cfg = MixConfig(target_lufs=self.settings.target_lufs,
                        true_peak_db=self.settings.true_peak_db,
                        sample_rate=self.settings.sample_rate)
        mix = mix_stems(res.stems, self.settings.sample_rate, cfg.balance)
        mres = master(mix, self.settings.sample_rate,
                      target_lufs=cfg.target_lufs, true_peak_db=cfg.true_peak_db,
                      glue=cfg.glue, cfg=cfg)
        master_rel = f"jobs/{ctx.job.id}/master.wav"
        mres.audio.save_wav(ctx.storage_root / master_rel)

        # authoritative LUFS finalize (ffmpeg ebur128 when available)
        fin_lufs, fin_tp, used_ffmpeg = finalize_master(
            ctx.storage_root / master_rel, self.settings.sample_rate,
            cfg.target_lufs, cfg.true_peak_db,
        )
        log.info(
            "master finalized: lufs=%.1f tp=%.2f (ffmpeg=%s)",
            fin_lufs, fin_tp, used_ffmpeg, extra={"job": ctx.job.id},
        )

        # per-stem WAV exports (DAW import)
        for name, sig in res.stems.items():
            sig = np.asarray(sig, dtype=np.float32)
            peak = float(np.abs(sig).max()) if sig.size else 0.0
            a = Audio(sig / max(peak, 1e-6) if peak > 1.0 else sig,
                      self.settings.sample_rate)
            a.save_wav(ctx.storage_root / out / "stems" / f"{name}.wav")
        return master_rel

    def _stage_export(self, ctx: JobContext) -> dict:
        out = ctx.storage_root / ctx.out_dir
        out.mkdir(parents=True, exist_ok=True)
        master_rel = f"jobs/{ctx.job.id}/master.wav"
        mp3_rel = f"jobs/{ctx.job.id}/master.mp3"
        encoders.wav_to_mp3(ctx.storage_root / master_rel,
                            ctx.storage_root / mp3_rel, kbps=320)
        lyrics_rel = f"jobs/{ctx.job.id}/lyrics.txt"
        (ctx.storage_root / lyrics_rel).write_text(
            _lyrics_to_text(ctx.lyrics), encoding="utf-8"
        )
        return {"mp3": mp3_rel, "lyrics": lyrics_rel}

    # ------------------------------------------------------------------
    # cost caps
    # ------------------------------------------------------------------
    def _record_cost(self, session, ctx: JobContext, stage: str, amount: float) -> None:
        session.add(CostLedger(job_id=ctx.job.id, stage=stage,
                               amount_usd=amount, note="stage-billed"))
        j = session.get(Job, ctx.job.id)
        j.cost_usd = round((j.cost_usd or 0.0) + amount, 4)

    def _check_budget(self, ctx: JobContext, stage: str, billed: float) -> None:
        """Raise `StageBlockedError` when `billed` would exceed a cap.

        Runs *before* the stage executes (fail fast, no unaccounted spend).
        """
        with self.db.scoped() as s:
            j = s.get(Job, ctx.job.id)
            spent = float((j.cost_usd if j is not None else 0.0) or 0.0)
            cap = self.settings.job_budget_usd
            if spent + billed > cap:
                raise StageBlockedError(stage, spent + billed, cap)

            # rolling 24h daily cap across all jobs
            since = utcnow() - timedelta(hours=24)
            total = float(
                s.query(sa_func.coalesce(sa_func.sum(CostLedger.amount_usd), 0.0))
                .filter(CostLedger.ts >= since)
                .scalar() or 0.0
            )
            daily_cap = self.settings.daily_cost_cap_usd
            if total + billed > daily_cap:
                raise StageBlockedError(f"daily:{stage}", total + billed, daily_cap)

    # ------------------------------------------------------------------
    # status / assets
    # ------------------------------------------------------------------
    def _set_status(self, ctx: JobContext, status: str) -> None:
        with self.db.scoped() as s:
            j = s.get(Job, ctx.job.id)
            j.status = status
            j.updated_at = utcnow()
            if status == "preview_ready":
                j.preview_at = utcnow()
            s.commit()

    def _finalize(self, ctx: JobContext, export_paths: dict,
                  assets: list[AssetDownload]) -> None:
        with self.db.scoped() as s:
            j = s.get(Job, ctx.job.id)
            j.status = "succeeded"
            j.preview_wav_path = ctx.preview_rel
            j.master_wav_path = f"jobs/{ctx.job.id}/master.wav"
            j.master_mp3_path = export_paths.get("mp3")
            j.stems_dir = f"jobs/{ctx.job.id}/stems"
            j.lyrics_path = export_paths.get("lyrics")
            j.finished_at = utcnow()
            s.commit()
        self._emit(ctx.job, "succeeded", None, assets=assets)
        metrics.JOBS_TOTAL.labels("succeeded", ctx.job.input_type).inc()

    def _fail(self, ctx: Optional[JobContext], job_id: str,
              error: str, status: str) -> None:
        log.error("job %s failed: %s", job_id, error, extra={"job": job_id})
        input_type = "unknown"
        with self.db.scoped() as s:
            j = s.get(Job, job_id)
            if j is not None:
                j.status = status
                j.error = error[:1000]
                j.finished_at = utcnow()
                input_type = j.input_type
                s.commit()
        metrics.JOBS_TOTAL.labels(status, input_type).inc()
        self.bus.publish(ProgressEvent(
            job_id=job_id, type=status, assets=[], error=error))

    def _build_assets(self, ctx: JobContext, export_paths: dict) -> list[AssetDownload]:
        def add(label: str, filename: str, rel: str) -> AssetDownload:
            full = ctx.storage_root / rel
            size = full.stat().st_size if full.exists() else 0
            return AssetDownload(
                label=label, filename=filename,
                url=f"/v1/jobs/{ctx.job.id}/files/{Path(rel).name}",
                size_bytes=int(size),
            )

        assets: list[AssetDownload] = []
        if ctx.preview_rel:
            assets.append(add("Hook preview", "preview.wav", ctx.preview_rel))
        assets.append(add("Master WAV", "master.wav", f"jobs/{ctx.job.id}/master.wav"))
        assets.append(add("Master MP3", "master.mp3", f"jobs/{ctx.job.id}/master.mp3"))
        for name in ("drums", "bass", "chords", "melody", "vox"):
            assets.append(add(f"Stem: {name}", f"{name}.wav",
                             f"jobs/{ctx.job.id}/stems/{name}.wav"))
        assets.append(add("Lyrics", "lyrics.txt", f"jobs/{ctx.job.id}/lyrics.txt"))
        return assets

    def _asset_url(self, ctx: JobContext, rel: str) -> str:
        return f"/v1/jobs/{ctx.job.id}/files/{Path(rel).name}"

    def _emit(self, job: Job, type_: str, stage: Optional[str] = None,
              detail: Optional[str] = None, preview_url: Optional[str] = None,
              assets: Optional[list[AssetDownload]] = None,
              error: Optional[str] = None,
              progress: Optional[float] = None) -> None:
        self.bus.publish(ProgressEvent(
            job_id=job.id, type=type_, stage=stage, detail=detail,
            preview_url=preview_url, assets=assets or [], error=error,
            progress=progress,
        ))

    # ------------------------------------------------------------------
    # misc
    # ------------------------------------------------------------------
    def _last_attempt(self, session, job_id: str, stage: str):
        # returns the most recent StageAttempt row for the stage
        rows = (session.query(StageAttempt)
                .filter(StageAttempt.job_id == job_id,
                        StageAttempt.stage == stage)
                .order_by(StageAttempt.id.desc()).all())
        return rows[0] if rows else None

    def _mark_attempt_failed(self, ctx: JobContext, stage: str, error: str) -> None:
        with self.db.scoped() as s:
            att = self._last_attempt(s, ctx.job.id, stage)
            if att is not None:
                att.status = "failed"
                att.error = error[:500]
                att.finished_at = utcnow()
                s.commit()

    @staticmethod
    def _style_hints_list(job: Job) -> list[str]:
        if not job.style_hints:
            return []
        try:
            return json.loads(job.style_hints) or []
        except json.JSONDecodeError:
            return [h for h in job.style_hints.split(",") if h]


def _lyrics_to_text(lyrics: Optional[dict]) -> str:
    if not lyrics:
        return ""
    out: list[str] = []
    for sec in lyrics.get("sections", []):
        name = str(sec.get("section", "verse")).upper()
        lines = sec.get("lines", [])
        out.append(f"[{name}]\n" + ("\n".join(lines) if lines else ""))
    return "\n\n".join(out)


__all__ = ["Orchestrator", "EventBus", "JobContext", "StageBlockedError"]
