"""RSS / podcast harvesting.

Text feeds are cheap. Podcast audio is not: transcribing an hour-long episode
with Whisper costs real GPU minutes, so `transcribe_audio` is opt-in and
budgeted by `max_transcribe_minutes`.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path

from ..campaign import Campaign
from ..config import get_settings
from .base import Candidate, clean_text, contains_any

log = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return clean_text(_TAG.sub(" ", s or ""))


def _entry_id(entry, feed_url: str) -> str:
    raw = getattr(entry, "id", None) or getattr(entry, "link", None) or getattr(entry, "title", "")
    return hashlib.sha1(f"{feed_url}:{raw}".encode()).hexdigest()[:20]


def _audio_url(entry) -> str | None:
    for enc in getattr(entry, "enclosures", []) or []:
        t = (enc.get("type") or "").lower()
        if t.startswith("audio/"):
            return enc.get("href")
    for link in getattr(entry, "links", []) or []:
        if (link.get("type") or "").lower().startswith("audio/"):
            return link.get("href")
    return None


def _transcribe(url: str, max_minutes: int) -> str:
    """Download a clipped chunk of an episode and transcribe it locally."""
    import subprocess
    import tempfile

    from ..tts.captions import transcribe_file

    s = get_settings()
    tmp = Path(tempfile.mkdtemp(prefix="podcast_", dir=str(s.work_dir)))
    wav = tmp / "clip.wav"
    # ffmpeg pulls the stream directly and stops at the budget — we never
    # store the full episode.
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", url, "-t", str(max_minutes * 60),
        "-ac", "1", "-ar", "16000", str(wav),
    ]
    subprocess.run(cmd, check=True, timeout=900)
    segments = transcribe_file(wav, word_timestamps=False)
    return " ".join(seg["text"] for seg in segments)


def harvest_rss(campaign: Campaign) -> list[Candidate]:
    import feedparser

    cfg = campaign.sources.rss
    topic = campaign.topic
    out: list[Candidate] = []

    for feed_url in cfg.feeds:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("feed %s failed: %s", feed_url, exc)
            continue

        feed_title = clean_text(getattr(parsed.feed, "title", "")) or feed_url
        entries = (parsed.entries or [])[: cfg.max_items_per_feed]
        log.info("feed %-50s -> %d entries", feed_title[:50], len(entries))

        for entry in entries:
            title = clean_text(getattr(entry, "title", ""))
            summary = _strip_html(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            )
            body = summary
            audio = _audio_url(entry)

            blob = f"{title}\n{summary}"
            keyword_hit = contains_any(blob, topic.keywords)

            # Only spend Whisper time on episodes whose metadata already
            # smells relevant, or when the summary is uselessly short.
            if cfg.transcribe_audio and audio and (keyword_hit or len(summary) < 200):
                try:
                    t0 = time.time()
                    body = _transcribe(audio, cfg.max_transcribe_minutes)
                    log.info("transcribed %.0fs of audio in %.0fs", 
                             cfg.max_transcribe_minutes * 60, time.time() - t0)
                except Exception as exc:  # noqa: BLE001
                    log.warning("transcription failed for %s: %s", title[:60], exc)

            blob = f"{title}\n{body}"
            if contains_any(blob, topic.exclude_keywords):
                continue
            if topic.keywords and not contains_any(blob, topic.keywords):
                continue

            out.append(
                Candidate(
                    kind="rss",
                    external_id=_entry_id(entry, feed_url),
                    url=getattr(entry, "link", feed_url),
                    title=title,
                    body=body[:4000],
                    author=clean_text(getattr(entry, "author", "")) or feed_title,
                    origin=feed_title,
                    created_utc=time.mktime(entry.published_parsed)
                    if getattr(entry, "published_parsed", None)
                    else 0.0,
                    extra={"audio_url": audio},
                )
            )

    log.info("rss harvest: %d candidates", len(out))
    return out
