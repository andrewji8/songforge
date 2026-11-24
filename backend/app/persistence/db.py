"""SQLAlchemy persistence: jobs, stage attempts, cost ledger.

MVP default is SQLite (file under .data). Production swaps in Postgres via
SF_DATABASE_URL. All timestamps are UTC.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker,
)
from sqlalchemy.pool import StaticPool

from app.config import Settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Job(Base):
    """One user request → one generated song."""
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    input_type: Mapped[str] = mapped_column(String(16))  # text | voice | loop
    status: Mapped[str] = mapped_column(String(16), index=True, default="queued")
    # queued | running | preview_ready | succeeded | failed | cancelled

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    custom_lyrics: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    style_hints: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)  # JSON list
    language: Mapped[str] = mapped_column(String(8), default="zh")
    parsed_intent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    lyrics: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    key: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    bpm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_secs: Mapped[int] = mapped_column(Integer, default=190)

    # output asset paths (relative to storage dir)
    preview_wav_path: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    master_wav_path: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    master_mp3_path: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    stems_dir: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    lyrics_path: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    preview_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    attempts: Mapped[list["StageAttempt"]] = relationship(
        "StageAttempt", back_populates="job", cascade="all, delete-orphan"
    )


class StageAttempt(Base):
    """One execution of a pipeline stage (for retry/cost auditability)."""
    __tablename__ = "stage_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | failed | budget_blocked
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    job: Mapped["Job"] = relationship("Job", back_populates="attempts")


class CostLedger(Base):
    """Append-only cost events; supports daily cap + audit."""
    __tablename__ = "cost_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    stage: Mapped[str] = mapped_column(String(32))
    amount_usd: Mapped[float] = mapped_column(Float)
    note: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


def create_engine_and_session(settings: Settings):
    url = settings.effective_database_url
    kwargs: dict = {}
    if url.startswith("sqlite"):
        # single-writer SQLite: StaticPool keeps one connection; MVP-friendly
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    engine = None
    from sqlalchemy import create_engine

    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


class DB:
    """Thin wrapper around sessionmaker for dependency injection in FastAPI."""

    def __init__(self, factory: sessionmaker):
        self._factory = factory

    def session(self) -> Session:
        return self._factory()

    @contextmanager
    def scoped(self):
        s = self._factory()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()
