"""Review UI and JSON API.

The whole point of this service is the approval gate: the pipeline renders,
a human watches, and only then does anything reach a public account. Nothing
here uploads on its own.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..campaign import load_campaigns
from ..config import get_settings
from ..db import session_scope
from ..models import Job, JobStatus, PublishLog

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

app = FastAPI(title="short-tale", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(HERE / "templates"))
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


# ---------------------------------------------------------------------------
# Auth: only enforced when APP_TOKEN is set, and only on mutating routes.
# ---------------------------------------------------------------------------


def require_token(request: Request) -> None:
    token = get_settings().app_token
    if not token:
        return
    supplied = (
        request.headers.get("x-app-token")
        or request.query_params.get("token")
        or request.cookies.get("shorttale_token")
    )
    if supplied != token:
        raise HTTPException(status_code=401, detail="invalid or missing token")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class JobPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None


class NewJob(BaseModel):
    campaign: str
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    settings = get_settings()
    campaigns = load_campaigns(settings.config_dir)
    with session_scope() as s:
        jobs = s.query(Job).order_by(Job.id.desc()).limit(60).all()
        rows = [j.to_dict() for j in jobs]
    pending = [r for r in rows if r["status"] == JobStatus.AWAITING_REVIEW.value]
    # Modern Starlette signature: request first. The legacy
    # TemplateResponse(name, context) form is removed in current versions,
    # where it silently reads the context dict as the template name.
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "campaigns": campaigns,
            "jobs": rows,
            "pending": pending,
            "token_required": bool(settings.app_token),
        },
    )


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: int):
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, "no such job")
        data = job.to_dict()
    return templates.TemplateResponse(
        request,
        "review.html",
        {"job": data, "token_required": bool(get_settings().app_token)},
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz():
    from ..llm.client import OllamaClient

    settings = get_settings()
    return {
        "ok": True,
        "ollama": OllamaClient().health(),
        "campaigns": sorted(load_campaigns(settings.config_dir).keys()),
        "config": settings.redacted(),
    }


@app.get("/api/jobs")
def list_jobs(status: str | None = None, limit: int = 60):
    with session_scope() as s:
        q = s.query(Job).order_by(Job.id.desc())
        if status:
            q = q.filter(Job.status == JobStatus(status))
        return [j.to_dict() for j in q.limit(min(limit, 200)).all()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, "no such job")
        return job.to_dict()


@app.post("/api/jobs", dependencies=[Depends(require_token)])
def create_job(payload: NewJob):
    settings = get_settings()
    campaigns = load_campaigns(settings.config_dir)
    if payload.campaign not in campaigns:
        raise HTTPException(400, f"unknown campaign {payload.campaign!r}")
    with session_scope() as s:
        job = Job(campaign=payload.campaign, status=JobStatus.QUEUED, stage="harvest")
        s.add(job)
        s.flush()
        jid = job.id
    log.info("queued job %s for campaign %s", jid, payload.campaign)
    return {"id": jid, "status": "queued"}


@app.patch("/api/jobs/{job_id}", dependencies=[Depends(require_token)])
def patch_job(job_id: int, patch: JobPatch):
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, "no such job")
        if job.status not in (JobStatus.AWAITING_REVIEW, JobStatus.FAILED):
            raise HTTPException(409, f"cannot edit a job that is {job.status.value}")
        if patch.title is not None:
            job.title = patch.title[:100]
        if patch.description is not None:
            job.description = patch.description[:4900]
        if patch.tags is not None:
            job.tags = [t.strip().lstrip("#") for t in patch.tags if t.strip()][:15]
        return job.to_dict()


@app.post("/api/jobs/{job_id}/approve", dependencies=[Depends(require_token)])
def approve(job_id: int):
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, "no such job")
        if job.status != JobStatus.AWAITING_REVIEW:
            raise HTTPException(409, f"job is {job.status.value}, not awaiting review")
        if not job.video_path or not Path(job.video_path).exists():
            raise HTTPException(409, "the rendered video is missing from disk")
        job.status = JobStatus.APPROVED
        log.info("job %s approved for publishing", job_id)
        return job.to_dict()


@app.post("/api/jobs/{job_id}/reject", dependencies=[Depends(require_token)])
def reject(job_id: int, reason: str = ""):
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, "no such job")
        job.status = JobStatus.REJECTED
        job.review_notes = (job.review_notes or "") + f"\nrejected: {reason}"
        return job.to_dict()


@app.post("/api/jobs/{job_id}/retry", dependencies=[Depends(require_token)])
def retry(job_id: int):
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, "no such job")
        if job.status not in (JobStatus.FAILED, JobStatus.REJECTED):
            raise HTTPException(409, "only failed or rejected jobs can be retried")
        new = Job(campaign=job.campaign, status=JobStatus.QUEUED, stage="harvest")
        s.add(new)
        s.flush()
        return {"id": new.id, "status": "queued"}


@app.get("/api/stats")
def stats():
    with session_scope() as s:
        counts = {}
        for st in JobStatus:
            counts[st.value] = s.query(Job).filter(Job.status == st).count()
        published = s.query(PublishLog).count()
    return {"jobs": counts, "published_total": published}


# ---------------------------------------------------------------------------
# Media (range-aware so the browser can scrub the preview)
# ---------------------------------------------------------------------------

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def _safe_media_path(job_id: int, kind: str) -> Path:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, "no such job")
        raw = job.video_path if kind == "video" else job.thumbnail_path
    if not raw:
        raise HTTPException(404, f"no {kind} for this job")
    p = Path(raw).resolve()
    settings = get_settings()
    allowed = [settings.out_dir.resolve(), settings.data_dir.resolve()]
    if not any(str(p).startswith(str(a)) for a in allowed):
        raise HTTPException(403, "path outside the media roots")
    if not p.exists():
        raise HTTPException(404, "file is missing from disk")
    return p


@app.get("/media/thumb/{job_id}")
def media_thumb(job_id: int):
    return FileResponse(_safe_media_path(job_id, "thumb"), media_type="image/jpeg")


@app.get("/media/video/{job_id}")
def media_video(job_id: int, request: Request):
    path = _safe_media_path(job_id, "video")
    size = path.stat().st_size
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    rng = request.headers.get("range")

    if not rng:
        return FileResponse(path, media_type=mime)

    m = _RANGE.match(rng)
    if not m:
        raise HTTPException(416, "malformed range")
    start = int(m.group(1)) if m.group(1) else 0
    end = int(m.group(2)) if m.group(2) else min(start + 4 * 1024 * 1024, size - 1)
    end = min(end, size - 1)
    if start > end:
        raise HTTPException(416, "range out of bounds")

    with path.open("rb") as fh:
        fh.seek(start)
        chunk = fh.read(end - start + 1)
    return Response(
        content=chunk,
        status_code=206,
        media_type=mime,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(chunk)),
        },
    )


@app.exception_handler(HTTPException)
def http_error(_request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
