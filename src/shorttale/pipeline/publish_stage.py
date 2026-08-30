"""The publish stage: take an approved job and put it on the platform.

Guarded by a daily cap and by the campaign's own publish mode, so a runaway
scheduler cannot flood a channel.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..campaign import Campaign
from ..db import session_scope
from ..models import Job, JobStatus, PublishLog
from ..publish import client as publisher

log = logging.getLogger(__name__)


def published_today(campaign_name: str, platform: str) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with session_scope() as s:
        return (
            s.query(PublishLog)
            .filter(
                PublishLog.campaign == campaign_name,
                PublishLog.platform == platform,
                PublishLog.published_at >= since,
            )
            .count()
        )


def publish_job(job_id: int, campaign: Campaign) -> dict:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            return {"ok": False, "error": "no such job"}
        if job.status != JobStatus.APPROVED:
            return {"ok": False, "error": f"job is {job.status.value}, not approved"}
        video_path = job.video_path
        title = job.title or "Untitled"
        description = job.description or ""
        tags = list(job.tags or [])

    if not video_path or not Path(video_path).exists():
        _fail(job_id, "the rendered video is missing from disk")
        return {"ok": False, "error": "video missing"}

    platform = campaign.publish.platforms[0] if campaign.publish.platforms else "youtube"

    cap = campaign.publish.max_per_day
    if cap and published_today(campaign.name, platform) >= cap:
        log.warning(
            "job %s held: %s already hit its cap of %d upload(s) in 24h",
            job_id, campaign.name, cap,
        )
        return {"ok": False, "error": "daily cap reached", "retry": True}

    visibility = campaign.publish.visibility
    if campaign.publish.mode == "auto_private":
        visibility = "private"

    _set(job_id, status=JobStatus.PUBLISHING, stage="publish")
    log.info("job %s uploading to %s as %s", job_id, platform, visibility)

    try:
        result = publisher.upload(
            Path(video_path),
            title=title,
            description=description,
            tags=tags,
            visibility=visibility,
            made_for_kids=campaign.publish.made_for_kids,
            platform=platform,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("job %s upload failed: %s", job_id, exc)
        _fail(job_id, str(exc))
        return {"ok": False, "error": str(exc)}

    with session_scope() as s:
        job = s.get(Job, job_id)
        if job:
            job.status = JobStatus.PUBLISHED
            job.stage = "done"
            job.progress = 1.0
            job.publish_result = result
        s.add(
            PublishLog(
                campaign=campaign.name,
                platform=platform,
                job_id=job_id,
                remote_url=result.get("url"),
            )
        )
    log.info("job %s published: %s", job_id, result.get("url"))
    return {"ok": True, **result}


def _set(job_id: int, **fields) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            return
        for k, v in fields.items():
            setattr(job, k, v)


def _fail(job_id: int, error: str) -> None:
    # Back to review rather than FAILED: the video is fine, the upload wasn't,
    # and the natural next step is for a human to retry it.
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            return
        job.status = JobStatus.AWAITING_REVIEW
        job.error = error[:4000]
        job.review_notes = ((job.review_notes or "") + f"\nupload failed: {error}")[:4000]
