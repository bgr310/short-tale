"""Campaign definitions — the "what topic" and "what kind of content" knobs.

A campaign is a YAML file in config/campaigns/. It is committed to the repo,
so it must never contain credentials. Everything here is content policy:
what to hunt for, what the product is, how the video should look and sound,
and how it should be published.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    """Base for every campaign model.

    `extra="forbid"` matters for more than typo-catching: without it pydantic
    silently discards unknown keys, so a credential pasted into a campaign
    file would vanish from the parsed model and slip straight past the secret
    scan below while still sitting in the committed YAML.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class RedditSource(Strict):
    enabled: bool = True
    subreddits: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    # "all" | "year" | "month" | "week" | "day"
    time_filter: str = "month"
    sort: str = "relevance"
    min_score: int = 10
    min_comments: int = 3
    max_per_query: int = 25
    max_candidates: int = 80
    # Skip posts whose author deleted them, and NSFW subs, by default.
    allow_nsfw: bool = False
    include_top_comments: int = 3


class RssSource(Strict):
    enabled: bool = False
    feeds: list[str] = Field(default_factory=list)
    max_items_per_feed: int = 20
    # Transcribing podcast audio is expensive; off unless you ask for it.
    transcribe_audio: bool = False
    max_transcribe_minutes: int = 20


class Sources(Strict):
    reddit: RedditSource = Field(default_factory=RedditSource)
    rss: RssSource = Field(default_factory=RssSource)


# ---------------------------------------------------------------------------
# Topic + product
# ---------------------------------------------------------------------------


class Topic(Strict):
    """What pain are we looking for in the wild."""

    description: str
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    # A candidate must score at least this (0-100) to be worth a video.
    min_relevance: int = 60


class Product(Strict):
    """What we are plugging, and the guardrails on how."""

    name: str
    url: str
    one_liner: str
    value_props: list[str] = Field(default_factory=list)
    # Hard content policy handed to the model on every generation and
    # re-checked afterwards. This is what stops it inventing features.
    claims_allowed: list[str] = Field(default_factory=list)
    claims_forbidden: list[str] = Field(default_factory=list)
    cta: str = ""
    # How hard to sell. soft = mention once at the end.
    plug_intensity: Literal["soft", "medium", "direct"] = "soft"


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------


class Duration(Strict):
    min_seconds: float = 22.0
    target_seconds: float = 38.0
    max_seconds: float = 58.0  # YouTube Shorts hard limit is 60s


class Background(Strict):
    # generated       — procedural, always available, zero downloads
    # library         — clips you dropped in assets/broll/
    # generated_first — try procedural, fall back to library
    # library_first   — try library, fall back to procedural (never fails)
    mode: Literal["generated", "library", "generated_first", "library_first"] = "generated_first"
    # Which procedural look. See render/backgrounds.py for the catalogue.
    preset: str = "aurora_drift"
    # Palette used by the generated presets, and by card accents.
    colors: list[str] = Field(default_factory=lambda: ["#0f1020", "#2b1055", "#7b2ff7"])
    # Only clips tagged with one of these are eligible from the library.
    library_tags: list[str] = Field(default_factory=list)
    motion: Literal["still", "slow", "medium"] = "slow"


class Captions(Strict):
    enabled: bool = True
    style: Literal["karaoke", "block", "none"] = "karaoke"
    font: str = "DejaVuSans-Bold"
    font_size: int = 84
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFD400"
    outline_color: str = "#000000"
    outline_width: int = 6
    # Vertical position as a fraction of frame height.
    position: float = 0.62
    max_words_per_line: int = 3
    uppercase: bool = False


class Style(Strict):
    # The visual template. reddit_card is the implemented one; the others are
    # registered in render/templates.py.
    format: Literal["reddit_card", "kinetic_text", "quote_card"] = "reddit_card"
    tone: str = "conversational, a little wry, never smug"
    duration: Duration = Field(default_factory=Duration)
    voice: str = "af_heart"
    speaking_rate: float = 1.05
    background: Background = Field(default_factory=Background)
    captions: Captions = Field(default_factory=Captions)
    # Show the source post as a card on screen while it is narrated.
    show_source_card: bool = True
    # Replace real usernames with a neutral handle on the card. On by
    # default: these are real people who did not sign up to appear in an ad.
    anonymize_authors: bool = True
    # Seconds of end card with the product CTA.
    end_card_seconds: float = 3.0
    music_volume: float = 0.10


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


class Publish(Strict):
    # review       — render, then wait for you to click Approve  (default)
    # auto_private — upload automatically as Private
    # auto         — upload automatically as configured visibility
    mode: Literal["review", "auto_private", "auto"] = "review"
    platforms: list[Literal["youtube", "tiktok"]] = Field(default_factory=lambda: ["youtube"])
    visibility: Literal["public", "unlisted", "private"] = "public"
    title_max_chars: int = 90
    hashtags: list[str] = Field(default_factory=list)
    # Appended verbatim to every description. Put your disclosure here.
    description_footer: str = ""
    made_for_kids: bool = False
    # Cron expression for automatic generation. Empty = manual only.
    schedule: str = ""
    # Never publish more than this many per day, per platform.
    max_per_day: int = 2


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------


class Campaign(Strict):
    name: str
    enabled: bool = True
    description: str = ""
    topic: Topic
    product: Product
    sources: Sources = Field(default_factory=Sources)
    style: Style = Field(default_factory=Style)
    publish: Publish = Field(default_factory=Publish)

    @model_validator(mode="before")
    @classmethod
    def _reject_secrets(cls, data):
        """Runs before field parsing, so it sees keys pydantic would drop."""
        if isinstance(data, dict) and _looks_like_secret(data):
            raise ValueError(
                "campaign definition appears to contain a credential. Secrets "
                "belong in .env, never in config/campaigns/ — those files are "
                "committed to the repository."
            )
        return data

    @model_validator(mode="after")
    def _sanity(self) -> "Campaign":
        if self.style.duration.max_seconds > 59.5 and "youtube" in self.publish.platforms:
            raise ValueError(
                "style.duration.max_seconds must stay under 60s for YouTube Shorts"
            )
        if not self.sources.reddit.enabled and not self.sources.rss.enabled:
            raise ValueError("at least one source must be enabled")
        return self


_SECRET_HINTS = ("client_secret", "password", "api_key", "apikey", "token", "bearer")


def _looks_like_secret(obj, _depth: int = 0) -> bool:
    """Cheap guard against someone pasting a key into a committed YAML file."""
    if _depth > 8:
        return False
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(h in str(k).lower() for h in _SECRET_HINTS) and isinstance(v, str) and v.strip():
                return True
            if _looks_like_secret(v, _depth + 1):
                return True
    elif isinstance(obj, list):
        return any(_looks_like_secret(i, _depth + 1) for i in obj)
    return False


def load_campaign(path: Path) -> Campaign:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Campaign.model_validate(data)


def load_campaigns(config_dir: Path) -> dict[str, Campaign]:
    """Load every campaign YAML, skipping (loudly) any that fail validation."""
    import logging

    log = logging.getLogger(__name__)
    out: dict[str, Campaign] = {}
    campaign_dir = config_dir / "campaigns"
    if not campaign_dir.is_dir():
        return out
    for p in sorted(campaign_dir.glob("*.y*ml")):
        try:
            c = load_campaign(p)
            out[c.name] = c
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the app
            log.error("campaign %s failed to load: %s", p.name, exc)
    return out
