"""Final assembly: background + cards + karaoke captions + voice -> one MP4.

Everything after the background is a single ffmpeg pass. libass burns the
captions, overlay places the cards with an eased rise and an alpha fade, and
the encoder is chosen at runtime — NVENC on the 3080 Ti, libx264 if the
container can't reach the encoder.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..campaign import Campaign
from ..config import get_settings
from .ffmpeg import RenderError, encoder_args, ffmpeg_bin, probe_duration, run

log = logging.getLogger(__name__)

W, H = 1080, 1920
CARD_WIDTH = 980
CARD_CENTER_Y = 0.30      # fraction of frame height
RISE_PX = 44
FADE = 0.35


def _card_filter(idx: int, start: float, end: float, label: str, width: int) -> tuple[str, str]:
    """Scale + fade a card input, and the overlay that places it."""
    fade_out_at = max(end - FADE, start + 0.05)
    prep = (
        f"[{idx}:v]scale={width}:-1,format=rgba,"
        f"fade=t=in:st={start:.2f}:d={FADE}:alpha=1,"
        f"fade=t=out:st={fade_out_at:.2f}:d={FADE}:alpha=1[{label}]"
    )
    # Eases up into place over the first 0.4s it is visible.
    y_expr = (
        f"(H*{CARD_CENTER_Y})-(h/2)+{RISE_PX}*max(0\\,1-(t-{start:.2f})/0.40)"
    )
    overlay = (
        f"overlay=x=(W-w)/2:y='{y_expr}':"
        f"enable='between(t,{start:.2f},{end:.2f})':format=auto"
    )
    return prep, overlay


def compose(
    *,
    background: Path,
    voice: Path,
    ass_file: Path,
    source_card: Path | None,
    product_card: Path | None,
    segments: list[dict],
    campaign: Campaign,
    out: Path,
    work_dir: Path,
) -> Path:
    settings = get_settings()
    fps = settings.video_fps

    voice_len = probe_duration(voice)
    if voice_len <= 0:
        raise RenderError(f"voice track has no duration: {voice}")
    end_card = campaign.style.end_card_seconds
    total = voice_len + end_card

    inputs: list[str] = [
        "-i", str(background),
    ]
    preps: list[str] = []
    overlays: list[str] = []
    idx = 1

    # --- source card ------------------------------------------------------
    card_segments = [s for s in segments if s.get("visual") == "source_card"]
    if source_card and campaign.style.show_source_card and card_segments:
        start = max(card_segments[0]["start"] - 0.15, 0.0)
        end = min(card_segments[-1]["end"] + 0.35, voice_len)
        inputs += ["-loop", "1", "-t", f"{total:.2f}", "-i", str(source_card)]
        prep, ov = _card_filter(idx, start, end, "sc", CARD_WIDTH)
        preps.append(prep)
        overlays.append(("sc", ov))
        idx += 1

    # --- product / end card ------------------------------------------------
    if product_card:
        # Appears with the CTA line and holds through the end card.
        cta_segments = [s for s in segments if s.get("visual") == "product_card"]
        start = cta_segments[0]["start"] if cta_segments else max(voice_len - 3.0, 0.0)
        inputs += ["-loop", "1", "-t", f"{total:.2f}", "-i", str(product_card)]
        prep, ov = _card_filter(idx, start, total, "pc", CARD_WIDTH)
        preps.append(prep)
        overlays.append(("pc", ov))
        idx += 1

    # --- audio -------------------------------------------------------------
    voice_idx = idx
    inputs += ["-i", str(voice)]
    idx += 1

    music = settings.assets_dir / "brand" / "music.mp3"
    music_idx = None
    if music.exists() and campaign.style.music_volume > 0:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        music_idx = idx
        idx += 1

    # --- build the graph ---------------------------------------------------
    chain: list[str] = list(preps)

    cur = "0:v"
    for i, (label, ov) in enumerate(overlays):
        nxt = f"v{i}"
        chain.append(f"[{cur}][{label}]{ov}[{nxt}]")
        cur = nxt

    fontsdir = settings.assets_dir / "fonts"
    sub = f"subtitles={ass_file.name}"
    if fontsdir.is_dir():
        sub += f":fontsdir={fontsdir}"
    chain.append(f"[{cur}]{sub},format=yuv420p[vout]")

    # Voice padded to the full length so the end card isn't cut short.
    chain.append(
        f"[{voice_idx}:a]aresample=48000,apad,atrim=0:{total:.2f},"
        f"asetpts=N/SR/TB,volume=1.0[va]"
    )
    if music_idx is not None:
        vol = campaign.style.music_volume
        chain.append(
            f"[{music_idx}:a]aresample=48000,atrim=0:{total:.2f},asetpts=N/SR/TB,"
            f"volume={vol:.3f},afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5[ma]"
        )
        chain.append("[va][ma]amix=inputs=2:duration=first:dropout_transition=0[aout]")
        amap = "[aout]"
    else:
        amap = "[va]"

    cmd = [
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", ";".join(chain),
        "-map", "[vout]", "-map", amap,
        "-t", f"{total:.2f}",
        "-r", str(fps),
        *encoder_args(settings.video_encoder, crf_cpu=21),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ]

    out.parent.mkdir(parents=True, exist_ok=True)
    log.info(
        "composing %.1fs (%d overlay(s), %s audio)",
        total, len(overlays), "voice+music" if music_idx else "voice",
    )
    # cwd = work_dir so the subtitles filter takes a bare filename and we
    # avoid ffmpeg's filter-path escaping rules entirely.
    import subprocess

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, cwd=str(work_dir))
    except subprocess.TimeoutExpired as exc:
        raise RenderError("compose timed out after 60 minutes") from exc
    if r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-25:])
        raise RenderError(f"compose failed (exit {r.returncode}):\n{tail}")

    actual = probe_duration(out)
    log.info("rendered %s (%.1fs, %.1f MB)", out.name, actual, out.stat().st_size / 1e6)
    return out


def grab_thumbnail(video: Path, out: Path, at: float = 1.5) -> Path:
    """A poster frame for the review UI."""
    out.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{at:.2f}", "-i", str(video),
            "-frames:v", "1", "-vf", "scale=360:-1", str(out),
        ],
        what="thumbnail", timeout=120,
    )
    return out
