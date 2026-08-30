"""Font resolution.

Drop any .ttf into assets/fonts/ and reference it by stem in the campaign's
style.captions.font. Falls back to the DejaVu faces that ship in the image,
which are metric-stable and cover the punctuation Reddit posts throw at us.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from ..config import get_settings

log = logging.getLogger(__name__)

_SYSTEM_DIRS = [
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts"),
]

_FALLBACKS = {
    "bold": ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
    "regular": ["DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
}


@lru_cache(maxsize=32)
def find_font(name: str = "DejaVuSans-Bold", weight: str = "bold") -> str:
    user_dir = get_settings().assets_dir / "fonts"
    stem = name.replace(".ttf", "")

    if user_dir.is_dir():
        for p in user_dir.glob("*.ttf"):
            if p.stem.lower() == stem.lower():
                return str(p)

    for d in _SYSTEM_DIRS:
        if not d.is_dir():
            continue
        exact = d / f"{stem}.ttf"
        if exact.exists():
            return str(exact)

    for d in _SYSTEM_DIRS:
        for fb in _FALLBACKS.get(weight, _FALLBACKS["regular"]):
            for p in d.rglob(fb):
                return str(p)

    for d in _SYSTEM_DIRS:
        for p in d.rglob("*.ttf"):
            return str(p)

    raise FileNotFoundError("no usable TrueType font found — is fonts-dejavu-core installed?")


def load(name: str, size: int, weight: str = "bold"):
    from PIL import ImageFont

    return ImageFont.truetype(find_font(name, weight), size)


@lru_cache(maxsize=1)
def ass_font_name(font: str = "DejaVuSans-Bold") -> str:
    """The family name libass needs, which is not the filename."""
    try:
        from PIL import ImageFont

        f = ImageFont.truetype(find_font(font, "bold"), 24)
        family, style = f.getname()
        return family if style.lower() in ("regular", "book") else f"{family}"
    except Exception as exc:  # noqa: BLE001
        log.debug("could not read font family (%s) — defaulting to DejaVu Sans", exc)
        return "DejaVu Sans"
