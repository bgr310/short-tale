"""Source and product cards, drawn with Pillow.

We draw the card rather than screenshotting the live page. That means no
headless browser in the worker image, nothing to be blocked by, and a card
that restyles to the campaign's palette instead of breaking whenever Reddit
ships new CSS.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ..campaign import Campaign
from ..sources.base import Candidate
from .fonts import load

log = logging.getLogger(__name__)

CARD_W = 940
PAD = 44
RADIUS = 36


def hex_to_rgb(h: str, alpha: int | None = None) -> tuple:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    return rgb + (alpha,) if alpha is not None else rgb


def _relative_age(days: float) -> str:
    if days < 1:
        return f"{max(int(days * 24), 1)}h ago"
    if days < 30:
        return f"{int(days)}d ago"
    if days < 365:
        return f"{max(int(days / 30), 1)}mo ago"
    return f"{max(int(days / 365), 1)}y ago"


def _fit_lines(draw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    """Greedy wrap on real glyph widths, with an ellipsis only if truncated.

    Truncation is decided by whether any word was actually left over, not by
    comparing string lengths — whitespace differences (YAML folded scalars
    leave a trailing space) made the length test add an ellipsis to text that
    had fitted perfectly well.
    """
    words = text.split()
    lines: list[str] = []
    cur = ""
    i = 0
    while i < len(words):
        trial = f"{cur} {words[i]}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
            i += 1
        else:
            if not cur:            # a single word wider than the line
                cur = words[i]
                i += 1
            lines.append(cur)
            cur = ""
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
        cur = ""

    truncated = i < len(words) or bool(cur)
    if truncated and lines:
        last = lines[-1].rstrip(" .,;:")
        while last and draw.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def render_source_card(
    candidate: Candidate, campaign: Campaign, out: Path, dark: bool = True
) -> Path:
    """A Reddit-style post card sized for a 1080-wide vertical frame."""
    accent = hex_to_rgb(
        campaign.style.background.colors[-1]
        if campaign.style.background.colors
        else "#3D7DFF"
    )
    bg = (26, 27, 30) if dark else (255, 255, 255)
    fg = (236, 238, 240) if dark else (26, 27, 30)
    muted = (150, 155, 163) if dark else (110, 115, 122)
    chip = (38, 40, 45) if dark else (240, 242, 245)

    f_meta = load("DejaVuSans", 27, "regular")
    f_title = load("DejaVuSans-Bold", 43, "bold")
    f_body = load("DejaVuSans", 31, "regular")
    f_stat = load("DejaVuSans-Bold", 27, "bold")

    # Measure on a scratch canvas so we can size the card to its content.
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    inner_w = CARD_W - PAD * 2
    title_lines = _fit_lines(scratch, candidate.title, f_title, inner_w, 4)
    body_lines = (
        _fit_lines(scratch, candidate.body, f_body, inner_w, 5) if candidate.body else []
    )

    header_h = 64
    title_h = len(title_lines) * 56
    body_h = len(body_lines) * 42 + (18 if body_lines else 0)
    stats_h = 62
    card_h = PAD + header_h + 16 + title_h + body_h + 20 + stats_h + PAD

    # Draw at 2x and downsample — cheap, and the text edges come out clean.
    S = 2
    img = Image.new("RGBA", (CARD_W * S, card_h * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle(
        [0, 0, CARD_W * S - 1, card_h * S - 1], radius=RADIUS * S, fill=bg + (255,)
    )
    # A thin accent bar along the top edge ties the card to the palette.
    d.rounded_rectangle(
        [0, 0, CARD_W * S - 1, (RADIUS + 6) * S], radius=RADIUS * S, fill=accent + (255,)
    )
    d.rectangle([0, 12 * S, CARD_W * S - 1, (RADIUS + 6) * S], fill=bg + (255,))

    f_meta_s = load("DejaVuSans", 27 * S, "regular")
    f_title_s = load("DejaVuSans-Bold", 43 * S, "bold")
    f_body_s = load("DejaVuSans", 31 * S, "regular")
    f_stat_s = load("DejaVuSans-Bold", 27 * S, "bold")

    x, y = PAD * S, (PAD + 6) * S

    # Avatar: accent circle with the community initial.
    av = 52 * S
    d.ellipse([x, y, x + av, y + av], fill=accent + (255,))
    initial = (candidate.origin.replace("r/", "") or "?")[0].upper()
    f_av = load("DejaVuSans-Bold", 30 * S, "bold")
    tw = d.textlength(initial, font=f_av)
    d.text((x + av / 2 - tw / 2, y + av / 2 - 20 * S), initial, font=f_av, fill=(255, 255, 255))

    author = "u/anonymous" if campaign.style.anonymize_authors else f"u/{candidate.author}"
    meta = f"{candidate.origin}  ·  {author}  ·  {_relative_age(candidate.age_days)}"
    d.text((x + av + 18 * S, y + 12 * S), meta, font=f_meta_s, fill=muted)

    y += header_h * S + 10 * S
    for line in title_lines:
        d.text((x, y), line, font=f_title_s, fill=fg)
        y += 56 * S

    if body_lines:
        y += 12 * S
        for line in body_lines:
            d.text((x, y), line, font=f_body_s, fill=muted)
            y += 42 * S

    # Stats row
    y += 18 * S
    chip_h = 46 * S

    def pill(px: int, icon: str, label: str) -> int:
        """Icons are drawn, not typed.

        The obvious version uses ▲ and 💬, but DejaVu has no colour-emoji
        glyph, so the speech bubble came out as a tofu box. Primitives are
        font-independent and always render.
        """
        icon_w = 26 * S
        text_w = int(d.textlength(label, font=f_stat_s))
        w = icon_w + text_w + 46 * S
        d.rounded_rectangle([px, y, px + w, y + chip_h], radius=chip_h // 2, fill=chip + (255,))

        ix, iy = px + 18 * S, y + chip_h // 2
        if icon == "up":
            r = 9 * S
            d.polygon(
                [(ix + r, iy - r), (ix, iy + r * 0.55), (ix + r * 2, iy + r * 0.55)],
                fill=muted,
            )
        else:  # speech bubble
            bw, bh = 20 * S, 15 * S
            d.rounded_rectangle(
                [ix, iy - bh // 2 - 2 * S, ix + bw, iy + bh // 2 - 2 * S],
                radius=4 * S, fill=muted,
            )
            d.polygon(
                [
                    (ix + 5 * S, iy + bh // 2 - 3 * S),
                    (ix + 5 * S, iy + bh // 2 + 4 * S),
                    (ix + 12 * S, iy + bh // 2 - 3 * S),
                ],
                fill=muted,
            )
        d.text((px + 18 * S + icon_w + 8 * S, y + 9 * S), label, font=f_stat_s, fill=muted)
        return px + w + 14 * S

    nx = pill(x, "up", _compact(candidate.score))
    pill(nx, "comment", _compact(candidate.num_comments))

    img = img.resize((CARD_W, card_h), Image.LANCZOS)

    # Soft drop shadow so the card reads against any background.
    shadow = Image.new("RGBA", (CARD_W + 80, card_h + 80), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([40, 46, CARD_W + 40, card_h + 44], radius=RADIUS, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    shadow.alpha_composite(img, (40, 40))

    out.parent.mkdir(parents=True, exist_ok=True)
    shadow.save(out, "PNG")
    log.info("source card -> %s (%dx%d)", out.name, shadow.width, shadow.height)
    return out


def _compact(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def render_product_card(campaign: Campaign, out: Path) -> Path:
    """The end card: product name, URL, one line of what it does."""
    colors = campaign.style.background.colors or ["#0b1020", "#1b2a5e", "#3d7dff"]
    accent = hex_to_rgb(colors[-1])
    W, H = 940, 520
    S = 2

    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W * S - 1, H * S - 1], radius=44 * S, fill=(18, 19, 24, 250))
    d.rounded_rectangle(
        [0, 0, W * S - 1, H * S - 1], radius=44 * S, outline=accent + (255,), width=5 * S
    )

    p = campaign.product
    f_name = load("DejaVuSans-Bold", 76 * S, "bold")
    f_url = load("DejaVuSans-Bold", 44 * S, "bold")
    f_line = load("DejaVuSans", 30 * S, "regular")

    y = 80 * S
    tw = d.textlength(p.name, font=f_name)
    d.text(((W * S - tw) / 2, y), p.name, font=f_name, fill=(255, 255, 255))

    y += 110 * S
    tw = d.textlength(p.url, font=f_url)
    d.text(((W * S - tw) / 2, y), p.url, font=f_url, fill=accent)

    y += 90 * S
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for line in _fit_lines(scratch, p.one_liner, load("DejaVuSans", 30, "regular"), W - 140, 3):
        tw = d.textlength(line, font=f_line)
        d.text(((W * S - tw) / 2, y), line, font=f_line, fill=(178, 184, 192))
        y += 44 * S

    img = img.resize((W, H), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    log.info("product card -> %s", out.name)
    return out
