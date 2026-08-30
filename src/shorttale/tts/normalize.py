"""Make text speakable.

Speech engines read literally. "tailmailer.com" becomes "tailmailerdotcom" or
worse if you hand it over raw, and that single mispronunciation makes the whole
video feel synthetic — so the product URL in particular gets careful treatment.
"""

from __future__ import annotations

import re

_SUBREDDIT = re.compile(r"\br/([A-Za-z0-9_]+)")
_USERNAME = re.compile(r"\bu/([A-Za-z0-9_\-]+)")
_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_DOMAIN = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9\-]{1,})\.(com|net|org|io|co|app|dev|ai)\b")
_MONEY = re.compile(r"\$\s?(\d[\d,]*)(?:\.(\d{2}))?")
_PERCENT = re.compile(r"(\d[\d,.]*)\s?%")
_MULTISPACE = re.compile(r"\s{2,}")

_ABBREV = {
    "e.g.": "for example",
    "i.e.": "that is",
    "etc.": "and so on",
    "vs.": "versus",
    "&": "and",
    "w/": "with",
    "b/c": "because",
    "24/7": "twenty four seven",
    "FYI": "F Y I",
    "DM": "D M",
    "OP": "the original poster",
    "TLDR": "in short",
    "TL;DR": "in short",
    "ISP": "I S P",
    "URL": "U R L",
}


def _spell_domain(m: re.Match) -> str:
    name, tld = m.group(1), m.group(2)
    # Split camelCase / compound names so the engine phrases them naturally.
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return f"{spaced} dot {tld}"


def normalize(text: str) -> str:
    if not text:
        return ""
    t = text

    t = _EMAIL.sub("an email address", t)
    t = _SUBREDDIT.sub(lambda m: f"the {m.group(1)} subreddit", t)
    t = _USERNAME.sub("someone", t)

    # Domains before generic URLs, so "tailmailer.com" survives as words.
    t = _DOMAIN.sub(_spell_domain, t)
    t = _URL.sub("the link in the description", t)

    t = _MONEY.sub(
        lambda m: f"{m.group(1).replace(',', '')} dollars"
        + (f" {m.group(2)} cents" if m.group(2) and m.group(2) != "00" else ""),
        t,
    )
    t = _PERCENT.sub(lambda m: f"{m.group(1)} percent", t)

    for k, v in _ABBREV.items():
        t = re.sub(rf"(?<!\w){re.escape(k)}(?!\w)", v, t)

    # Ellipses and dashes become breaths, not mumbles.
    t = t.replace("…", ", ").replace("—", ", ").replace("–", ", ")
    t = re.sub(r"\.{2,}", ", ", t)
    t = re.sub(r"[\"“”]", "", t)

    t = _MULTISPACE.sub(" ", t).strip()
    if t and t[-1] not in ".!?,":
        t += "."
    return t
