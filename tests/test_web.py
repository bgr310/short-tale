"""Review UI and API — especially the approval gate."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from shorttale.db import session_scope  # noqa: E402
from shorttale.models import Job, JobStatus  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    # SQLite needs a real filesystem; point the app at a temp dir.
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    from shorttale.config import get_settings

    get_settings.cache_clear()
    import shorttale.db as db

    db._engine = None
    db._Session = None

    from shorttale.web.app import app

    return TestClient(app)


@pytest.fixture
def job(client):
    with session_scope() as s:
        j = Job(
            campaign="demo", status=JobStatus.AWAITING_REVIEW, stage="done",
            progress=1.0, title="A title", description="d", tags=["privacy"],
            source_url="https://example.invalid/p", source_title="A post",
            relevance=88.0, duration=34.0,
            review_notes="claim check: unsupported claim — 'blocks all spam'",
            script={"hook": "h", "beats": [{"text": "b", "visual": "source_card"}], "cta": "c"},
        )
        s.add(j)
        s.flush()
        return j.id


def test_pages_render(client, job):
    for path in ("/healthz", "/", f"/job/{job}", "/api/jobs", "/api/stats"):
        assert client.get(path).status_code == 200, path


def test_review_warnings_reach_the_page(client, job):
    """Claim-check warnings are the point of the gate — they must be visible."""
    html = client.get(f"/job/{job}").text
    assert "claim check" in html
    assert "blocks all spam" in html
    assert "Approve" in html


def test_to_dict_carries_review_notes(client, job):
    assert "claim check" in (client.get(f"/api/jobs/{job}").json()["review_notes"] or "")


def test_cannot_approve_without_a_rendered_video(client, job):
    r = client.post(f"/api/jobs/{job}/approve")
    assert r.status_code == 409
    assert "missing" in r.json()["error"]


def test_cannot_approve_twice(client, job, tmp_path):
    video = tmp_path / "out" / "v.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"not really a video")
    with session_scope() as s:
        s.get(Job, job).video_path = str(video)

    assert client.post(f"/api/jobs/{job}/approve").status_code == 200
    assert client.post(f"/api/jobs/{job}/approve").status_code == 409


def test_tags_are_normalised_on_edit(client, job):
    r = client.patch(f"/api/jobs/{job}", json={"tags": ["a", "#b", "  ", "c"]})
    assert r.json()["tags"] == ["a", "b", "c"]


def test_unknown_campaign_is_rejected(client):
    assert client.post("/api/jobs", json={"campaign": "nope"}).status_code == 400


def test_media_path_traversal_is_blocked(client, job):
    """video_path is stored in the DB; it must still be confined to the roots."""
    with session_scope() as s:
        s.get(Job, job).video_path = "/etc/passwd"
    assert client.get(f"/media/video/{job}").status_code == 403


def test_healthz_never_leaks_a_secret(client, monkeypatch):
    import json

    body = json.dumps(client.get("/healthz").json())
    assert "supersecret" not in body
    assert "reddit_client_secret" in body  # present, but redacted
