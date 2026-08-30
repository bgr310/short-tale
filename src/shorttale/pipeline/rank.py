"""Pick the one candidate worth making a video about.

Two passes: a free heuristic pre-sort so we don't waste GPU time scoring
obvious junk, then a batched LLM judgement on the survivors.
"""

from __future__ import annotations

import logging
import math

from ..campaign import Campaign
from ..llm.client import LLMError, OllamaClient
from ..llm.prompts import RANK_SYSTEM, rank_user_prompt
from ..sources.base import Candidate

log = logging.getLogger(__name__)

#: How many candidates get the (expensive) LLM treatment.
LLM_SHORTLIST = 24
BATCH = 8


def heuristic_score(c: Candidate, campaign: Campaign) -> float:
    """Cheap 0-100 pre-score. Engagement, freshness, keyword density, shape."""
    blob = c.text_blob().lower()

    kw = campaign.topic.keywords
    hits = sum(1 for k in kw if k.lower() in blob) if kw else 0
    kw_score = min(hits / max(len(kw) * 0.25, 1), 1.0) * 40

    # Log-scaled so a 40k-point post doesn't drown out a great 300-point one.
    eng_score = min(math.log10(max(c.score, 1)) / 4.0, 1.0) * 25
    comment_score = min(math.log10(max(c.num_comments, 1)) / 3.0, 1.0) * 10

    # Prefer recent, but don't hard-exclude a great older post.
    age = c.age_days
    fresh_score = 15.0 if age <= 30 else 10.0 if age <= 180 else 5.0

    # First-person stories beat listicles.
    story_score = 10.0 if any(t in blob for t in (" i ", "my ", "i'm ", "i've ")) else 0.0

    # A post with no body and no comments has nothing to narrate.
    if len(c.body) < 80 and not c.top_comments:
        return 0.0

    return kw_score + eng_score + comment_score + fresh_score + story_score


def _batch_block(batch: list[Candidate]) -> str:
    out = []
    for c in batch:
        out.append(f"--- id: {c.external_id}\n{c.summary_for_llm(max_body=700)}")
    return "\n\n".join(out)


def rank(
    candidates: list[Candidate],
    campaign: Campaign,
    llm: OllamaClient | None = None,
) -> list[Candidate]:
    """Return candidates sorted best-first, with .relevance and .rank_reason set."""
    if not candidates:
        return []

    for c in candidates:
        c.relevance = heuristic_score(c, campaign)
    candidates.sort(key=lambda c: c.relevance or 0, reverse=True)

    shortlist = [c for c in candidates if (c.relevance or 0) > 0][:LLM_SHORTLIST]
    if not shortlist:
        log.warning("no candidate cleared the heuristic pre-filter")
        return []

    if llm is None:
        llm = OllamaClient()
    if not llm.health():
        log.warning("Ollama unreachable — falling back to heuristic ranking only")
        return candidates

    by_id = {c.external_id: c for c in shortlist}
    scored = 0

    for i in range(0, len(shortlist), BATCH):
        batch = shortlist[i : i + BATCH]
        try:
            data = llm.chat_json(
                RANK_SYSTEM,
                rank_user_prompt(campaign, _batch_block(batch)),
                model=llm.fast_model,
                temperature=0.2,
                max_tokens=900,
            )
        except (LLMError, Exception) as exc:  # noqa: BLE001
            log.warning("ranking batch %d failed, keeping heuristic scores: %s", i // BATCH, exc)
            continue

        for row in data.get("scores", []) or []:
            cand = by_id.get(str(row.get("id", "")).strip())
            if not cand:
                continue
            try:
                llm_score = float(row.get("score", 0))
            except (TypeError, ValueError):
                continue
            # Blend: the model's judgement leads, the heuristic keeps it honest
            # about engagement and recency.
            cand.relevance = round(0.7 * llm_score + 0.3 * (cand.relevance or 0), 1)
            cand.rank_reason = str(row.get("reason", ""))[:200]
            cand.extra["angle"] = str(row.get("angle", ""))[:300]
            scored += 1

    log.info("ranked %d candidates (%d scored by the model)", len(candidates), scored)
    candidates.sort(key=lambda c: c.relevance or 0, reverse=True)
    return candidates


def select_best(
    candidates: list[Candidate], campaign: Campaign
) -> Candidate | None:
    """The single candidate to build, or None if nothing cleared the bar."""
    for c in candidates:
        if (c.relevance or 0) >= campaign.topic.min_relevance:
            return c
    if candidates:
        log.info(
            "best candidate scored %.0f, below min_relevance %d — skipping this run",
            candidates[0].relevance or 0,
            campaign.topic.min_relevance,
        )
    return None
