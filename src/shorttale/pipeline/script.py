"""Turn a chosen candidate into a validated, speakable script.

The output of this stage is the thing that will be read aloud and published
under the user's name, so it gets checked twice: a mechanical pass for length
and banned phrasing, and a model pass for claims that drifted outside the
allowed list.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..campaign import Campaign
from ..llm.client import LLMError, OllamaClient
from ..llm.prompts import (
    SCRIPT_SYSTEM,
    VERIFY_SYSTEM,
    script_user_prompt,
    verify_user_prompt,
)
from ..sources.base import Candidate

log = logging.getLogger(__name__)

#: Words per second for the speech engine at rate 1.0. Measured on Kokoro.
WORDS_PER_SECOND = 2.7

#: Phrasing that reads as an ad or as AI slop. Cheap to catch, worth catching.
BANNED_PHRASES = [
    "game changer", "game-changer", "insane", "mind-blowing", "mind blowing",
    "you won't believe", "you wont believe", "this one weird trick",
    "revolutionary", "life-changing", "life changing", "literally the best",
    "in today's digital age", "in today's world", "let's dive in", "dive into",
    "buckle up", "here's the kicker", "the best part?", "trust me",
]

_MARKUP = re.compile(r"[*_`#\[\]]|<[^>]+>")
_EMOJI = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF" "]+",
    flags=re.UNICODE,
)
_STAGE_DIR = re.compile(r"\((?:pause|beat|sfx|music)[^)]*\)|\[[^\]]*\]", re.I)


class Beat(BaseModel):
    text: str
    visual: Literal["source_card", "product_card", "none"] = "none"

    @field_validator("text")
    @classmethod
    def _clean(cls, v: str) -> str:
        return sanitize_for_speech(v)


class VideoScript(BaseModel):
    hook: str
    beats: list[Beat] = Field(default_factory=list)
    cta: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("hook", "cta")
    @classmethod
    def _clean(cls, v: str) -> str:
        return sanitize_for_speech(v)

    # --- derived -----------------------------------------------------------

    @property
    def lines(self) -> list[tuple[str, str]]:
        """(text, visual) in spoken order — the unit the renderer works in."""
        out: list[tuple[str, str]] = [(self.hook, "source_card")]
        out += [(b.text, b.visual) for b in self.beats if b.text.strip()]
        if self.cta.strip():
            out.append((self.cta, "product_card"))
        return [(t, v) for t, v in out if t.strip()]

    @property
    def narration(self) -> str:
        return " ".join(t for t, _ in self.lines)

    @property
    def word_count(self) -> int:
        return len(self.narration.split())

    def estimated_seconds(self, rate: float = 1.0) -> float:
        return self.word_count / (WORDS_PER_SECOND * max(rate, 0.1))


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------


def sanitize_for_speech(text: str) -> str:
    """Strip everything a narrator would not say out loud."""
    if not text:
        return ""
    t = _STAGE_DIR.sub(" ", text)
    t = _EMOJI.sub("", t)
    t = _MARKUP.sub("", t)
    t = t.replace("&amp;", "and").replace("&", " and ")
    t = re.sub(r"#\w+", "", t)              # hashtags
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r'^["\']|["\']$', "", t)     # wrapping quotes
    return t.strip()


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def word_budget(campaign: Campaign) -> dict:
    d = campaign.style.duration
    rate = campaign.style.speaking_rate
    wps = WORDS_PER_SECOND * rate
    # Reserve the end card, which is silent-ish.
    usable = lambda s: max(s - campaign.style.end_card_seconds * 0.5, 4.0)  # noqa: E731
    return {
        "min": int(usable(d.min_seconds) * wps),
        "target": int(usable(d.target_seconds) * wps),
        "max": int(usable(d.max_seconds) * wps),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def mechanical_check(script: VideoScript, campaign: Campaign) -> list[str]:
    """Fast, deterministic problems. Returns a list of complaints."""
    problems: list[str] = []
    budget = word_budget(campaign)
    wc = script.word_count

    if wc < budget["min"]:
        problems.append(f"too short: {wc} words, need at least {budget['min']}")
    if wc > budget["max"]:
        problems.append(f"too long: {wc} words, ceiling is {budget['max']}")

    low = script.narration.lower()
    for phrase in BANNED_PHRASES:
        if phrase in low:
            problems.append(f"remove the phrase {phrase!r} — it reads as an ad")

    for claim in campaign.product.claims_forbidden:
        # Match on a distinctive chunk of the claim rather than the whole
        # sentence, so paraphrases with different wrapping still trip it.
        core = " ".join(claim.lower().split()[:4])
        if core and len(core) > 8 and core in low:
            problems.append(f"forbidden claim detected: {claim!r}")

    url = campaign.product.url.lower().replace("https://", "").replace("www.", "")
    mentions = low.count(url.split("/")[0])
    if mentions == 0:
        problems.append(f"the script never says {campaign.product.url}")
    elif mentions > 2:
        problems.append(f"{campaign.product.url} is said {mentions} times — once is enough")

    if len(script.hook.split()) > 16:
        problems.append("hook is longer than 16 words")
    if not script.title:
        problems.append("missing title")
    if len(script.title) > campaign.publish.title_max_chars:
        problems.append(f"title exceeds {campaign.publish.title_max_chars} characters")

    return problems


def verify_claims(
    script: VideoScript, campaign: Campaign, llm: OllamaClient
) -> tuple[bool, list[dict]]:
    """Second opinion from the model on whether any claim drifted out of bounds."""
    if not campaign.product.claims_forbidden and not campaign.product.claims_allowed:
        return True, []
    try:
        data = llm.chat_json(
            VERIFY_SYSTEM,
            verify_user_prompt(campaign, script.narration),
            temperature=0.0,
            max_tokens=500,
        )
    except Exception as exc:  # noqa: BLE001
        # A failed check must not silently pass. Flag it for human review.
        log.warning("claim verification failed to run: %s", exc)
        return False, [{"quote": "", "why": f"verification did not run: {exc}"}]

    ok = bool(data.get("ok", False))
    violations = data.get("violations") or []
    return ok, violations


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate_script(
    candidate: Candidate,
    campaign: Campaign,
    llm: OllamaClient | None = None,
    max_attempts: int = 3,
) -> tuple[VideoScript, list[str]]:
    """Write and validate a script. Returns (script, remaining_warnings).

    Warnings are non-fatal notes surfaced in the review UI — the human decides.
    """
    llm = llm or OllamaClient()
    budget = word_budget(campaign)
    angle = str(candidate.extra.get("angle", "")) or candidate.rank_reason

    base_user = script_user_prompt(
        campaign, candidate.summary_for_llm(), angle, budget
    )

    last_problems: list[str] = []
    script: VideoScript | None = None

    for attempt in range(1, max_attempts + 1):
        user = base_user
        if last_problems:
            user += (
                "\n\nYOUR PREVIOUS DRAFT WAS REJECTED. Fix all of these and "
                "return the full JSON again:\n"
                + "\n".join(f"- {p}" for p in last_problems)
            )
        try:
            data = llm.chat_json(
                SCRIPT_SYSTEM,
                user,
                temperature=0.8 if attempt == 1 else 0.55,
                max_tokens=1400,
            )
            script = VideoScript.model_validate(data)
        except (LLMError, Exception) as exc:  # noqa: BLE001
            log.warning("script attempt %d failed to parse: %s", attempt, exc)
            last_problems = ["your previous output was not valid JSON in the required shape"]
            continue

        last_problems = mechanical_check(script, campaign)
        if not last_problems:
            break
        log.info("script attempt %d rejected: %s", attempt, "; ".join(last_problems))

    if script is None:
        raise LLMError("the model never produced a usable script")

    warnings = list(last_problems)

    ok, violations = verify_claims(script, campaign, llm)
    if not ok:
        for v in violations:
            warnings.append(
                f"claim check: {v.get('why', 'unsupported claim')} — {v.get('quote', '')}".strip()
            )
        log.warning("claim verification flagged %d issue(s)", len(violations))

    est = script.estimated_seconds(campaign.style.speaking_rate)
    log.info(
        "script: %d words, ~%.1fs estimated, %d beats, %d warning(s)",
        script.word_count, est, len(script.beats), len(warnings),
    )
    return script, warnings
