"""Reddit harvesting via PRAW.

Uses a read-only "script" app. The free tier allows roughly 100 queries per
minute per client id, which is far more than this pipeline needs — but we
still pace ourselves and honour the rate-limit headers PRAW surfaces, because
getting throttled mid-harvest is the most common way this stage fails.
"""

from __future__ import annotations

import logging
import time

from ..campaign import Campaign
from ..config import get_settings
from .base import Candidate, clean_text, contains_any

log = logging.getLogger(__name__)

# Be a good citizen and keep well under the limit even on big campaigns.
_PAUSE_BETWEEN_QUERIES = 1.2


def _client():
    import praw

    s = get_settings()
    if not s.has_reddit_credentials:
        raise RuntimeError(
            "Reddit credentials are missing. Create a 'script' app at "
            "https://www.reddit.com/prefs/apps and put REDDIT_CLIENT_ID / "
            "REDDIT_CLIENT_SECRET in your .env file."
        )
    reddit = praw.Reddit(
        client_id=s.reddit_client_id,
        client_secret=s.reddit_client_secret,
        user_agent=s.reddit_user_agent,
        # Read-only: this app never posts, votes, or comments as you.
        check_for_async=False,
    )
    reddit.read_only = True
    return reddit


def _submission_to_candidate(sub, cfg, topic) -> Candidate | None:
    if getattr(sub, "over_18", False) and not cfg.allow_nsfw:
        return None
    if getattr(sub, "stickied", False):
        return None
    if sub.score < cfg.min_score or sub.num_comments < cfg.min_comments:
        return None

    title = clean_text(getattr(sub, "title", ""))
    body = clean_text(getattr(sub, "selftext", ""))
    if body in ("[removed]", "[deleted]"):
        body = ""

    blob = f"{title}\n{body}"
    if contains_any(blob, topic.exclude_keywords):
        return None
    # Require at least one topic keyword somewhere — a cheap pre-filter so we
    # don't spend LLM time scoring obviously irrelevant posts.
    if topic.keywords and not contains_any(blob, topic.keywords):
        return None

    author = "unknown"
    try:
        if sub.author is not None:
            author = str(sub.author)
    except Exception:  # noqa: BLE001
        pass

    top_comments: list[str] = []
    if cfg.include_top_comments:
        try:
            sub.comment_sort = "top"
            sub.comments.replace_more(limit=0)
            for c in sub.comments[: cfg.include_top_comments]:
                txt = clean_text(getattr(c, "body", ""))
                if txt and txt not in ("[removed]", "[deleted]") and len(txt) > 20:
                    top_comments.append(txt)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not fetch comments for %s: %s", sub.id, exc)

    return Candidate(
        kind="reddit",
        external_id=str(sub.id),
        url=f"https://www.reddit.com{sub.permalink}",
        title=title,
        body=body,
        author=author,
        origin=f"r/{sub.subreddit.display_name}",
        score=int(sub.score),
        num_comments=int(sub.num_comments),
        created_utc=float(getattr(sub, "created_utc", 0.0)),
        top_comments=top_comments,
        extra={"upvote_ratio": getattr(sub, "upvote_ratio", None)},
    )


def harvest_reddit(campaign: Campaign) -> list[Candidate]:
    cfg = campaign.sources.reddit
    topic = campaign.topic
    reddit = _client()

    scope = "+".join(cfg.subreddits) if cfg.subreddits else "all"
    subreddit = reddit.subreddit(scope)

    found: dict[str, Candidate] = {}
    queries = cfg.queries or topic.keywords[:6] or [topic.description[:120]]

    for q in queries:
        if len(found) >= cfg.max_candidates:
            break
        try:
            results = subreddit.search(
                q,
                sort=cfg.sort,
                time_filter=cfg.time_filter,
                limit=cfg.max_per_query,
            )
            n_before = len(found)
            for sub in results:
                if len(found) >= cfg.max_candidates:
                    break
                try:
                    cand = _submission_to_candidate(sub, cfg, topic)
                except Exception as exc:  # noqa: BLE001
                    log.debug("skipping a submission: %s", exc)
                    continue
                if cand:
                    found[cand.external_id] = cand
            log.info("reddit search %-40r -> +%d", q[:40], len(found) - n_before)
        except Exception as exc:  # noqa: BLE001
            # 429/5xx/network: log and keep going with the other queries.
            log.warning("reddit search failed for %r: %s", q, exc)
        time.sleep(_PAUSE_BETWEEN_QUERIES)

    out = sorted(found.values(), key=lambda c: c.score, reverse=True)
    log.info("reddit harvest: %d candidates from %d queries", len(out), len(queries))
    return out
