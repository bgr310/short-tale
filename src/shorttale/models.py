"""Persistent state: jobs, and the dedupe ledger of things we've already used."""

from __future__ import annotations

import enum
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"


#: Ordered pipeline stages, used for progress display in the review UI.
STAGES = [
    "harvest",
    "rank",
    "script",
    "voice",
    "captions",
    "visuals",
    "render",
    "publish",
]


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=32), default=JobStatus.QUEUED, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), default="harvest")
    progress: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # Chosen source material
    source_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Generated artefacts
    work_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Publish metadata (editable in the review UI before approval)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[Any] = mapped_column(JSON, default=list)

    script: Mapped[Any] = mapped_column(JSON, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    publish_result: Mapped[Any] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_jobs_campaign_status", "campaign", "status"),)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "campaign": self.campaign,
            "status": self.status.value if isinstance(self.status, JobStatus) else self.status,
            "stage": self.stage,
            "progress": self.progress,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "relevance": self.relevance,
            "video_path": self.video_path,
            "duration": self.duration,
            "title": self.title,
            "description": self.description,
            "tags": self.tags or [],
            "script": self.script,
            # review_notes carries the claim-check and length warnings. It is
            # the whole reason the review gate exists, so it must reach the UI.
            "review_notes": self.review_notes,
            "source_kind": self.source_kind,
            "thumbnail_path": self.thumbnail_path,
            "error": self.error,
            "publish_result": self.publish_result,
        }


class SeenItem(Base):
    """Dedupe ledger.

    Stops the pipeline making a second video about a post it already used,
    and stops it re-scoring the same candidate on every run.
    """

    __tablename__ = "seen_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign: Mapped[str] = mapped_column(String(64), index=True)
    source_kind: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    used: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("campaign", "source_kind", "external_id", name="uq_seen"),
    )


class PublishLog(Base):
    """One row per successful upload — used to enforce max_per_day."""

    __tablename__ = "publish_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    remote_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
