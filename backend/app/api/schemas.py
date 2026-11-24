"""Pydantic API schemas for /v1 routes and WebSocket progress events."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class InputType(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    LOOP = "loop"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PREVIEW_READY = "preview_ready"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CreateJobRequest(BaseModel):
    """POST /v1/jobs — text entry (voice/loop use the multipart endpoint)."""
    description: str = Field(
        min_length=4,
        max_length=500,
        description="一句话描述，例如：'深夜雨声里想念旧朋友的 LoFi 小调'",
    )
    input_type: InputType = InputType.TEXT
    duration_secs: int = Field(default=190, ge=60, le=300)
    style_hints: list[str] = Field(
        default_factory=list,
        description="可选：genre/mood 标签，例如 ['lofi', 'melancholy']",
    )
    language: str = "zh"
    lyrics: Optional[str] = Field(
        default=None,
        description="可选：提供自定义歌词（否则由 LLM/模板生成）",
    )


class VoiceJobCreate(BaseModel):
    """Metadata part of the voice multipart form."""
    description: str = Field(default="", max_length=500)
    style_hints: list[str] = Field(default_factory=list)
    duration_secs: int = Field(default=190, ge=60, le=300)
    language: str = "zh"


class JobCreated(BaseModel):
    job_id: str
    status: JobStatus
    created_at: str
    preview_eta_seconds: int = 25


class AssetDownload(BaseModel):
    label: str
    filename: str
    url: str
    size_bytes: int


class LyricsSection(BaseModel):
    section: str  # intro | verse | chorus | bridge | outro
    lines: list[str]


class JobDetail(BaseModel):
    job_id: str
    status: JobStatus
    input_type: InputType
    duration_secs: int
    key: Optional[str] = None
    bpm: Optional[int] = None
    cost_usd: float = 0.0
    error: Optional[str] = None
    preview_at: Optional[str] = None
    finished_at: Optional[str] = None
    assets: list[AssetDownload] = Field(default_factory=list)
    lyrics_sections: list[LyricsSection] = Field(default_factory=list)


class JobListResponse(BaseModel):
    items: list[JobDetail]
    total: int
    limit: int
    offset: int


class ProgressEvent(BaseModel):
    """WebSocket progress frame: type in
    {'queued','stage_started','stage_done','preview_ready',
     'succeeded','failed','cancelled'}
    """
    job_id: str
    type: str
    stage: Optional[str] = None
    detail: Optional[str] = None
    progress: Optional[float] = Field(default=None, ge=0, le=1)
    preview_url: Optional[str] = None
    assets: list[AssetDownload] = Field(default_factory=list)
    error: Optional[str] = None


class CancelJob(BaseModel):
    ok: bool
    status: JobStatus


class HealthResponse(BaseModel):
    status: str
    version: str
    worker: bool
    provider: str
    db: str
    storage: str
