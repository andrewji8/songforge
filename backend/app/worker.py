"""Worker: pulls queued jobs and runs them through the orchestrator.

Two run modes:
- Embedded: `EmbeddedWorker` started from the API process when
  `SF_ENABLE_WORKER=true` (default for MVP single container).
- Standalone: `run_worker_forever(settings)` for a dedicated worker
  container that scales horizontally.

The embedded worker claims jobs by status transition
(queued -> running) using a short DB transaction so two containers
don't double-process the same job. Cost caps are enforced inside the
orchestrator *before* spend is recorded, so a blocked job never burns
budget on the billed stage.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from app.config import Settings
from app.observability import metrics
from app.persistence.db import DB, Job, utcnow
from app.providers.music_provider import MusicProvider, build_provider
from app.orchestrator import EventBus, Orchestrator

log = logging.getLogger(__name__)

UPLOAD_KIND = {"voice": "voice", "loop": "loop"}


class Worker:
    def __init__(
        self,
        settings: Settings,
        db: DB,
        provider: MusicProvider,
        bus: EventBus,
        concurrency: int = 2,
    ):
        self.settings = settings
        self.db = db
        self.bus = bus
        self.orchestrator = Orchestrator(settings, db, provider, bus)
        self.executor = ThreadPoolExecutor(
            max_workers=max(1, concurrency), thread_name_prefix="sf-worker"
        )

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait)

    # ------------------------------------------------------------------
    def claim_next(self) -> Optional[str]:
        """Atomically claim one queued job.

        The status flip is a *conditional* UPDATE (`WHERE id = ? AND
        status = 'queued'`). If another worker won the race the rowcount is
        0 and we move on to the next candidate, so a job is never
        double-processed across containers.
        """
        for _ in range(8):
            with self.db.scoped() as s:
                row = (
                    s.query(Job)
                    .filter(Job.status == "queued")
                    .order_by(Job.created_at.asc())
                    .first()
                )
                if row is None:
                    return None
                job_id = row.id
                claimed = (
                    s.query(Job)
                    .filter(Job.id == job_id, Job.status == "queued")
                    .update(
                        {"status": "running", "updated_at": utcnow()},
                        synchronize_session=False,
                    )
                )
                if not claimed:
                    # lost the race to another worker; try the next row
                    s.commit()
                    continue
                s.commit()
                return job_id
        return None

    def process(self, job_id: str, uploaded_paths: Optional[dict] = None) -> Job:
        log.info("processing job %s", job_id, extra={"job": job_id})
        try:
            return self.orchestrator.run(job_id, uploaded_paths or {})
        except Exception as e:
            # orchestrator already marked the job; surface for callers
            log.exception("job %s failed: %s", job_id, e)
            raise

    def run_pending_once(self) -> int:
        """Poll loop body: run as many pending jobs as idle slots allow.

        Returns the number of jobs processed this pass. Used by both
        the embedded loop and standalone runner.
        """
        processed = 0
        while True:
            job_id = self.claim_next()
            if job_id is None:
                break
            if self.executor._work_queue.qsize() >= self.executor._max_workers:  # noqa: SLF001
                break
            self.executor.submit(self._run_job, job_id)
            processed += 1
        return processed

    def _run_job(self, job_id: str) -> None:
        # uploads are stored by the API as uploads/{job_id}/{kind}{ext};
        # resolve whichever extension was used (voice.webm, loop.mp3, ...)
        storage_root = self.settings.effective_storage_dir
        uploads: dict[str, str] = {}
        with self.db.scoped() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            kind = UPLOAD_KIND.get(job.input_type)
        if kind:
            candidates = sorted(
                (storage_root / f"uploads/{job_id}").glob(f"{kind}.*")
            )
            if candidates:
                uploads[kind] = str(candidates[0])
        self.process(job_id, uploads)

    # ------------------------------------------------------------------
    def start(self) -> None:
        log.info("worker starting (poll=%.1fs)", self.settings.worker_poll_interval_s)
        while True:
            try:
                self.run_pending_once()
            except Exception:  # noqa: BLE001
                log.exception("worker poll failed")
            time.sleep(self.settings.worker_poll_interval_s)


class EmbeddedWorker:
    """Runs the poll loop on a daemon thread inside the API process."""

    def __init__(self, settings: Settings, db: DB, provider: MusicProvider, bus: EventBus):
        self._settings = settings
        self._worker = Worker(
            settings, db, provider, bus, settings.worker_concurrency
        )
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._worker.start, name="sf-embedded-worker", daemon=True
        )
        self._thread.start()
        log.info("embedded worker thread started")

    def stop(self) -> None:
        if self._thread is not None:
            self._worker.shutdown(wait=False)
            self._thread = None

    @property
    def worker(self) -> Worker:
        return self._worker


def build_worker(
    settings: Settings, db: DB, bus: EventBus,
    embedded: Optional[bool] = None,
) -> tuple[Worker, Optional[EmbeddedWorker]]:
    """Build the shared Worker + optional embedded runner.

    `embedded` overrides `settings.enable_worker` when not None.
    """
    provider = build_provider(settings)
    worker = Worker(settings, db, provider, bus, settings.worker_concurrency)
    use_embedded = settings.enable_worker if embedded is None else embedded
    embedded_runner = EmbeddedWorker(settings, db, provider, bus) if use_embedded else None
    if embedded_runner is not None:
        embedded_runner.start()
    metrics.JOBS_IN_FLIGHT  # touch to register at import
    return worker, embedded_runner


def run_worker_forever(settings: Settings, db: DB, bus: EventBus) -> None:
    """Standalone worker entry point (dedicated container)."""
    provider = build_provider(settings)
    worker = Worker(settings, db, provider, bus, settings.worker_concurrency)
    log.info("standalone worker starting")
    worker.start()
