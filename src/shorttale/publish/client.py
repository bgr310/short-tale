"""App-side client for the publisher service."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


class PublisherUnavailable(RuntimeError):
    pass


def _base() -> str:
    return get_settings().publisher_url.rstrip("/")


def session_status() -> dict:
    try:
        with httpx.Client(timeout=120) as c:
            r = c.get(f"{_base()}/session")
            if r.status_code == 409:
                return {"signed_in": None, "busy": True}
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        raise PublisherUnavailable(f"publisher service unreachable: {exc}") from exc


def health() -> dict:
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{_base()}/health")
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        raise PublisherUnavailable(f"publisher service unreachable: {exc}") from exc


def upload(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    visibility: str,
    made_for_kids: bool = False,
    platform: str = "youtube",
) -> dict:
    payload = {
        "video_path": str(video_path),
        "title": title,
        "description": description,
        "tags": tags,
        "visibility": visibility,
        "made_for_kids": made_for_kids,
        "platform": platform,
    }
    # Uploads are slow: encode, transfer, and YouTube's own processing.
    with httpx.Client(timeout=2400) as c:
        r = c.post(f"{_base()}/upload", json=payload)
        if r.status_code >= 400:
            detail = r.json().get("detail", r.text) if r.text else r.reason_phrase
            raise RuntimeError(f"publish failed ({r.status_code}): {detail}")
        return r.json()
