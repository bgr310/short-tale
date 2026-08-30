"""HTTP wrapper around the browser, running inside the publisher container.

Kept as a separate service for one reason: it is the only component that holds
a logged-in session, so it is the only one that needs the profile volume. The
GPU worker never touches a credential of any kind.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .youtube import NotSignedIn, PublishError, check_session, upload

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("publisher")

app = FastAPI(title="short-tale publisher", docs_url=None, redoc_url=None)

# One browser profile means one operation at a time. Everything queues here.
_lock = asyncio.Lock()


class UploadRequest(BaseModel):
    video_path: str
    title: str
    description: str = ""
    tags: list[str] = []
    visibility: str = "private"
    made_for_kids: bool = False
    platform: str = "youtube"


@app.get("/health")
async def health():
    return {"ok": True, "busy": _lock.locked(), "publish_enabled": _enabled()}


@app.get("/session")
async def session():
    if _lock.locked():
        raise HTTPException(409, "the browser is busy with an upload")
    async with _lock:
        try:
            return await check_session()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"session check failed: {exc}") from exc


@app.post("/upload")
async def do_upload(req: UploadRequest):
    if not _enabled():
        raise HTTPException(403, "publishing is disabled (PUBLISH_ENABLED=false)")
    if req.platform != "youtube":
        raise HTTPException(
            400,
            f"{req.platform} uploads are not automated. The rendered file is ready "
            "in out/ — see docs/PUBLISHING.md for why TikTok is manual.",
        )

    path = Path(req.video_path)
    if not path.exists():
        raise HTTPException(404, f"video not found in this container: {path}")
    if req.visibility not in ("public", "unlisted", "private"):
        raise HTTPException(400, f"bad visibility {req.visibility!r}")

    async with _lock:
        try:
            return await upload(
                path,
                title=req.title,
                description=req.description,
                tags=req.tags,
                visibility=req.visibility,
                made_for_kids=req.made_for_kids,
            )
        except NotSignedIn as exc:
            raise HTTPException(401, str(exc)) from exc
        except PublishError as exc:
            raise HTTPException(502, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            log.exception("upload blew up")
            raise HTTPException(500, f"unexpected upload failure: {exc}") from exc


def _enabled() -> bool:
    return os.environ.get("PUBLISH_ENABLED", "true").lower() not in ("0", "false", "no")
