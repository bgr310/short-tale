"""Common candidate shape and the harvest fan-out."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..campaign import Campaign

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    """One piece of found source material that might become a video."""

    kind: str                      # "reddit" | "rss"
    external_id: str               # stable id within that source
    url: str
    title: str
    body: str = ""
    author: str = ""
    origin: str = ""               # subreddit name, or feed title
    score: int = 0
    num_comments: int = 0
    created_utc: float = 0.0
    top_comments: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    # Filled in by the ranking stage
    relevance: float | None = None
    rank_reason: str = ""

    @property
    def age_days(self) -> float:
        if not self.created_utc:
            return 0.0
        return (datetime.now(timezone.utc).timestamp() - self.created_utc) / 86400.0

    def text_blob(self) -> str:
        parts = [self.title, self.body, *self.top_comments]
        return "\n".join(p for p in parts if p)

    def summary_for_llm(self, max_body: int = 1200, max_comment: int = 320) -> str:
        lines = [
            f"SOURCE: {self.kind} ({self.origin})",
            f"TITLE: {self.title}",
        ]
        if self.body:
            lines.append(f"BODY: {self.body[:max_body]}")
        for i, c in enumerate(self.top_comments[:3], 1):
            lines.append(f"COMMENT {i}: {c[:max_comment]}")
        lines.append(f"ENGAGEMENT: {self.score} points, {self.num_comments} comments")
        return "\n".join(lines)


def contains_any(text: str, needles: list[str]) -> bool:
    if not needles:
        return False
    low = text.lower()
    return any(n.lower() in low for n in needles)


_WS = re.compile(r"\s+")


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    # Strip zero-width and control characters that break ffmpeg's drawtext
    # and look like garbage on a caption.
    s = "".join(ch for ch in s if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    s = s.replace("​", "").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return _WS.sub(" ", s).strip()


def harvest_all(campaign: Campaign, seen_ids: set[str] | None = None) -> list[Candidate]:
    """Run every enabled source for a campaign and return deduped candidates.

    A failure in one source is logged and skipped — a dead RSS feed must not
    stop the Reddit harvest.
    """
    seen_ids = seen_ids or set()
    out: list[Candidate] = []

    if campaign.sources.reddit.enabled:
        try:
            from .reddit import harvest_reddit

            out.extend(harvest_reddit(campaign))
        except Exception as exc:  # noqa: BLE001
            log.error("reddit harvest failed: %s", exc)

    if campaign.sources.rss.enabled:
        try:
            from .rss import harvest_rss

            out.extend(harvest_rss(campaign))
        except Exception as exc:  # noqa: BLE001
            log.error("rss harvest failed: %s", exc)

    deduped: list[Candidate] = []
    local_seen: set[str] = set()
    for c in out:
        key = f"{c.kind}:{c.external_id}"
        if key in seen_ids or key in local_seen:
            continue
        local_seen.add(key)
        deduped.append(c)

    log.info(
        "harvest: %d raw, %d after dedupe (%d previously seen)",
        len(out), len(deduped), len(out) - len(deduped),
    )
    return deduped
