"""Persistent Chromium context.

The profile directory is the only thing that proves who you are, and it lives
in a Docker named volume — never in the repo, never in an env var, never in a
file this project writes. Nothing here ever handles a password: you type it
yourself, once, through the noVNC window.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

PROFILE_DIR = Path(os.environ.get("PROFILE_DIR", "/profile"))

# Headful in a virtual display. YouTube Studio's upload flow behaves far
# better with a real window than in headless mode, and it means the noVNC
# view shows exactly what the automation sees.
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--start-maximized",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=Translate,MediaRouter",
]

VIEWPORT = {"width": 1280, "height": 940}
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


async def open_context(playwright, headless: bool = False):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ctx = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        args=LAUNCH_ARGS,
        viewport=VIEWPORT,
        user_agent=UA,
        locale="en-US",
        timezone_id=os.environ.get("TZ", "UTC"),
        accept_downloads=False,
        ignore_default_args=["--enable-automation"],
    )
    ctx.set_default_timeout(45_000)
    return ctx


async def first_visible(page, selectors: list[str], timeout: int = 8000):
    """Return the first selector that actually resolves to a visible element.

    YouTube Studio changes its DOM often, so every interaction here tries a
    list of candidates rather than betting on one selector.
    """
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            return loc
        except Exception:  # noqa: BLE001
            continue
    return None
