"""ffmpeg plumbing: capability probing and a logged runner."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from functools import lru_cache

log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return shutil.which("ffprobe") or "ffprobe"


@lru_cache(maxsize=1)
def _encoders() -> str:
    try:
        return subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        log.warning("could not list ffmpeg encoders: %s", exc)
        return ""


@lru_cache(maxsize=1)
def _filters() -> str:
    try:
        return subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:  # noqa: BLE001
        return ""


def has_filter(name: str) -> bool:
    return f" {name} " in _filters()


@lru_cache(maxsize=32)
def filter_options(name: str) -> frozenset[str]:
    """Which options a filter actually accepts on THIS ffmpeg build.

    Filter presence is not enough. Ubuntu 22.04 ships ffmpeg 4.4, whose
    `gradients` filter exists but has no `type` option — passing it aborts the
    render. Anything version-dependent gets probed here instead of assumed.
    """
    if not has_filter(name):
        return frozenset()
    try:
        out = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-h", f"filter={name}"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        log.debug("could not probe options for %s: %s", name, exc)
        return frozenset()

    opts: set[str] = set()
    in_block = False
    for line in out.splitlines():
        if "AVOptions" in line:
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if not stripped:
                continue
            if not line.startswith(" "):
                break
            opts.add(stripped.split()[0])
    return frozenset(opts)


def filter_supports(name: str, option: str) -> bool:
    return option in filter_options(name)


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    """Is NVENC both compiled in AND actually usable right now?

    Listing the encoder is not enough — inside a container without the `video`
    driver capability the encoder is present but every session fails to open.
    So we do a one-frame trial encode and believe the result.
    """
    if "h264_nvenc" not in _encoders():
        log.info("ffmpeg has no h264_nvenc encoder compiled in")
        return False
    try:
        r = subprocess.run(
            [
                ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                "-c:v", "h264_nvenc", "-frames:v", "1", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            log.info("NVENC is available — using GPU encoding")
            return True
        log.warning(
            "NVENC present but unusable (%s) — falling back to CPU encoding. "
            "On WSL2 check that the compose file grants the 'video' capability.",
            (r.stderr or "").strip().splitlines()[-1] if r.stderr else "unknown",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("NVENC probe failed: %s", exc)
    return False


def encoder_args(preference: str = "auto", crf_cpu: int = 21) -> list[str]:
    """Encoder flags tuned for vertical short-form delivery."""
    want = (preference or "auto").lower()
    use_nvenc = want == "nvenc" or (want == "auto" and nvenc_available())

    if use_nvenc:
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p5",
            "-tune", "hq",
            "-rc", "vbr",
            "-cq", "23",
            "-b:v", "8M",
            "-maxrate", "12M",
            "-bufsize", "16M",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
        ]
    # Older CPUs: 'faster' keeps a 40s vertical render in the low minutes
    # instead of the high ones, at a quality difference nobody will see on a phone.
    return [
        "-c:v", "libx264",
        "-preset", "faster",
        "-crf", str(crf_cpu),
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
    ]


def run(cmd: list[str], *, timeout: int = 3600, what: str = "ffmpeg") -> None:
    log.debug("%s: %s", what, " ".join(cmd))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"{what} timed out after {timeout}s") from exc
    if r.returncode != 0:
        tail = "\n".join((r.stderr or "").strip().splitlines()[-25:])
        raise RenderError(f"{what} failed (exit {r.returncode}):\n{tail}")


def probe_duration(path) -> float:
    try:
        r = subprocess.run(
            [
                ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=60,
        )
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 0.0
