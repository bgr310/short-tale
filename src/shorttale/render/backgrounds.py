"""Backgrounds: generated first, your clip library as the fallback.

The generated presets are pure ffmpeg filter graphs — nothing to download, no
licensing question, and they can never fail for want of an asset. The library
path uses clips you drop into assets/broll/ and is preferred when you have
footage that suits the campaign.

Each background is rendered to its own intermediate file rather than being
folded into the main filter graph. That costs one extra pass and buys a stage
you can watch, cache, and debug on its own.
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path

from ..campaign import Campaign
from ..config import get_settings
from .ffmpeg import (
    RenderError,
    encoder_args,
    ffmpeg_bin,
    filter_supports,
    has_filter,
    probe_duration,
    run,
)

log = logging.getLogger(__name__)

W, H = 1080, 1920

# Generated backgrounds are built at a fraction of the output size and then
# scaled up. A soft gradient carries no detail worth preserving, so this is
# visually identical — but a gblur or geq at 1080x1920 costs ~16x more per
# frame than at 270x480, which is the difference between a render that
# finishes on an older CPU and one that doesn't.
GEN_W, GEN_H = 270, 480

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def _hexes(campaign: Campaign) -> list[str]:
    cols = campaign.style.background.colors or ["#0b1020", "#1b2a5e", "#3d7dff"]
    return [c.lstrip("#") for c in cols]


# ---------------------------------------------------------------------------
# Generated presets
# ---------------------------------------------------------------------------


def _gradients(colors: list[str], duration: float, fps: int, speed: float,
               kind: str | None = None) -> str:
    """A gradients source using only options this ffmpeg build understands."""
    parts = [f"gradients=s={GEN_W}x{GEN_H}"]
    for i, col in enumerate(colors[:8]):
        parts.append(f"c{i}=0x{col}")
    parts.append(f"nb_colors={min(len(colors), 8)}")
    parts.append(f"x0=30:y0=50:x1={GEN_W - 30}:y1={GEN_H - 50}")
    # `type` only exists on ffmpeg 5+; on 4.4 the filter renders linear anyway.
    if kind and filter_supports("gradients", "type"):
        parts.append(f"type={kind}")
    parts.append(f"speed={max(min(speed, 1.0), 1e-05)}")
    parts.append(f"d={duration:.2f}:r={fps}")
    return ":".join(parts)


def _preset_filter(preset: str, campaign: Campaign, duration: float, fps: int) -> tuple[str, str]:
    """Return (lavfi_source, post_filter) for a named preset."""
    c = _hexes(campaign)
    c0, c1, c2 = (c + c + c)[:3]
    motion = {"still": 0.004, "slow": 0.016, "medium": 0.045}[campaign.style.background.motion]

    if preset == "aurora_drift" and has_filter("gradients"):
        src = _gradients([c0, c1, c2], duration, fps, motion, "linear")
        post = (
            "gblur=sigma=10,"
            f"scale=1080:1920:flags=bicubic,"
            "noise=alls=7:allf=t+u,"
            "vignette=PI/4,eq=saturation=1.15:contrast=1.05"
        )
        return src, post

    if preset == "radial_pulse" and has_filter("gradients"):
        # The radial look needs `type`, which older ffmpeg lacks. Rather than
        # silently shipping a linear gradient under a radial name, hand off to
        # the geq preset, which is genuinely radial on every build.
        if not filter_supports("gradients", "type"):
            log.info("radial_pulse needs ffmpeg 5+ — using plasma_drift instead")
            return _preset_filter("plasma_drift", campaign, duration, fps)
        src = _gradients([c0, c2], duration, fps, motion * 1.5, "radial")
        post = f"gblur=sigma=8,scale=1080:1920:flags=bicubic,noise=alls=6:allf=t,vignette=PI/5"
        return src, post

    if preset == "plasma_drift":
        # geq plasma: no dependency on the gradients filter at all.
        src = f"color=c=black:s={GEN_W}x{GEN_H}:d={duration:.2f}:r={fps}"
        r, g, b = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
        post = (
            f"geq="
            f"r='{r/255:.2f}*(128+96*sin(X/45+T*0.5)*cos(Y/55-T*0.35))':"
            f"g='{g/255:.2f}*(128+96*sin(X/52-T*0.4)*cos(Y/42+T*0.3))':"
            f"b='{b/255:.2f}*(128+96*sin((X+Y)/60+T*0.45))',"
            f"gblur=sigma=6,scale=1080:1920:flags=bicubic,vignette=PI/4"
        )
        return src, post

    if preset == "soft_grain":
        src = f"color=c=0x{c0}:s={GEN_W}x{GEN_H}:d={duration:.2f}:r={fps}"
        post = (
            f"drawbox=x=0:y=0:w={GEN_W}:h={GEN_H}:color=0x{c1}@0.35:t=fill,"
            f"gblur=sigma=3,scale=1080:1920:flags=bicubic,noise=alls=12:allf=t+u,vignette=PI/4"
        )
        return src, post

    # Unknown preset name: a calm two-tone that always works.
    log.warning("unknown background preset %r — using soft_grain", preset)
    return _preset_filter("soft_grain", campaign, duration, fps)


def generate_background(
    campaign: Campaign, duration: float, fps: int, out: Path
) -> Path:
    preset = campaign.style.background.preset
    src, post = _preset_filter(preset, campaign, duration, fps)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", src,
        "-t", f"{duration:.2f}",
        "-vf", f"{post},format=yuv420p,scale={W}:{H}",
        "-r", str(fps),
        *encoder_args(get_settings().video_encoder, crf_cpu=23),
        "-an", str(out),
    ]
    run(cmd, what=f"background[{preset}]", timeout=1800)
    log.info("generated %s background: %.1fs -> %s", preset, duration, out.name)
    return out


# ---------------------------------------------------------------------------
# Clip library
# ---------------------------------------------------------------------------


def _library_clips(tags: list[str]) -> list[Path]:
    """Eligible clips from assets/broll, filtered by tag.

    Tags come from an optional manifest.yml ({filename: [tags]}); with no
    manifest, the filename's own words are treated as its tags — so
    `calm-abstract-loop.mp4` matches the tags `calm` and `abstract`.
    """
    base = get_settings().assets_dir / "broll"
    if not base.is_dir():
        return []

    manifest: dict[str, list[str]] = {}
    mpath = base / "manifest.yml"
    if mpath.exists():
        try:
            import yaml

            manifest = yaml.safe_load(mpath.read_text()) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("could not read b-roll manifest: %s", exc)

    clips = [p for p in sorted(base.rglob("*")) if p.suffix.lower() in VIDEO_EXT]
    if not tags:
        return clips

    wanted = {t.lower() for t in tags}
    matched = []
    for p in clips:
        own = {t.lower() for t in manifest.get(p.name, [])}
        if not own:
            own = {w.lower() for w in re.split(r"[^A-Za-z0-9]+", p.stem) if w}
        if own & wanted:
            matched.append(p)
    return matched or clips


def library_background(
    campaign: Campaign, duration: float, fps: int, out: Path
) -> Path:
    clips = _library_clips(campaign.style.background.library_tags)
    if not clips:
        raise RenderError("no clips in assets/broll/")

    clip = random.choice(clips)
    clip_len = probe_duration(clip)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Start somewhere other than frame zero when the clip is long enough, so
    # repeated use of the same file doesn't look repeated.
    seek = 0.0
    if clip_len > duration + 4:
        seek = random.uniform(0, clip_len - duration - 2)

    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},setsar=1,format=yuv420p"
    )
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
        "-stream_loop", "-1",
        "-ss", f"{seek:.2f}", "-i", str(clip),
        "-t", f"{duration:.2f}",
        "-vf", vf, "-r", str(fps),
        *encoder_args(get_settings().video_encoder, crf_cpu=23),
        "-an", str(out),
    ]
    run(cmd, what="background[library]", timeout=1800)
    log.info("library background: %s (from %.0fs) -> %s", clip.name, seek, out.name)
    return out


# ---------------------------------------------------------------------------


def make_background(campaign: Campaign, duration: float, fps: int, out: Path) -> Path:
    """Build the background per the campaign's mode, never failing outright."""
    mode = campaign.style.background.mode
    order = {
        "generated": [generate_background],
        "library": [library_background],
        "generated_first": [generate_background, library_background],
        "library_first": [library_background, generate_background],
    }[mode]

    errors = []
    for fn in order:
        try:
            return fn(campaign, duration, fps, out)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fn.__name__}: {exc}")
            log.warning("%s failed — %s", fn.__name__, str(exc).splitlines()[0][:200])

    # Last resort: a flat colour. A dull video beats a failed job.
    log.error("every background strategy failed; using a flat colour")
    c0 = _hexes(campaign)[0]
    run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=0x{c0}:s={W}x{H}:d={duration:.2f}:r={fps}",
            "-t", f"{duration:.2f}",
            *encoder_args(get_settings().video_encoder, crf_cpu=26),
            "-an", str(out),
        ],
        what="background[flat]",
    )
    return out
