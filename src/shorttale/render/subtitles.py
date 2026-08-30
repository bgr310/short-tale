"""Karaoke captions as an ASS subtitle file.

ASS is burned in by libass in the same ffmpeg pass as everything else, which
is both faster and sharper than compositing text frame by frame — and it gives
us the per-word highlight that short-form captions live on.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..campaign import Campaign
from .fonts import ass_font_name

log = logging.getLogger(__name__)

W, H = 1080, 1920


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    """ASS wants &HAABBGGRR — alpha inverted (00 opaque), channels reversed."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
    )


def build_ass(
    words: list[dict], campaign: Campaign, out: Path, total_duration: float
) -> Path:
    """Write an ASS file where each word lights up as it is spoken."""
    cap = campaign.style.captions
    font = ass_font_name(cap.font)
    primary = _ass_color(cap.primary_color)
    highlight = _ass_color(cap.highlight_color)
    outline = _ass_color(cap.outline_color)

    pos_y = int(H * cap.position)
    per_line = max(cap.max_words_per_line, 1)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{cap.font_size},{primary},{primary},{outline},&H96000000,-1,0,0,0,100,100,1,0,1,{cap.outline_width},3,5,80,80,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []

    if cap.style == "none" or not cap.enabled or not words:
        out.write_text(header, encoding="utf-8")
        return out

    groups = [words[i : i + per_line] for i in range(0, len(words), per_line)]

    for group in groups:
        g_start = group[0]["start"]
        g_end = group[-1]["end"]
        texts = [
            (w["word"].upper() if cap.uppercase else w["word"]) for w in group
        ]

        if cap.style == "block":
            body = _escape(" ".join(texts))
            lines.append(
                f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Main,,0,0,0,,"
                f"{{\\pos({W // 2},{pos_y})\\fad(60,60)}}{body}"
            )
            continue

        # karaoke: one event per word, whole group visible, active word lit
        for i, w in enumerate(group):
            start = w["start"]
            end = w["end"] if i < len(group) - 1 else g_end
            if end <= start:
                end = start + 0.10
            parts = []
            for j, t in enumerate(texts):
                esc = _escape(t)
                if j == i:
                    # Active word: highlight colour and a small pop in scale.
                    parts.append(f"{{\\c{highlight}\\fscx108\\fscy108}}{esc}{{\\r}}")
                else:
                    parts.append(f"{{\\c{primary}}}{esc}{{\\r}}")
            body = " ".join(parts)
            fade = "\\fad(50,0)" if i == 0 else ""
            lines.append(
                f"Dialogue: 0,{_ts(start)},{_ts(end)},Main,,0,0,0,,"
                f"{{\\pos({W // 2},{pos_y}){fade}}}{body}"
            )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    log.info("captions: %d words in %d groups -> %s", len(words), len(groups), out.name)
    return out
