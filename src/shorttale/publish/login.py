"""One-time interactive sign-in.

    docker compose exec publisher python3 -m publisher.login

Then open http://localhost:7900/vnc.html and sign in to Google yourself. This
script never sees your password: it only opens the window and waits for the
session cookie to appear in the profile volume.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from .browser import open_context
from .youtube import STUDIO, check_session

logging.basicConfig(level="INFO", format="%(message)s")
log = logging.getLogger("login")

WAIT_MINUTES = 15


async def main() -> int:
    from playwright.async_api import async_playwright

    log.info("")
    log.info("  Opening a browser on the container's virtual display.")
    log.info("  ---------------------------------------------------------------")
    log.info("  1. Open  http://localhost:7900/vnc.html  in your own browser")
    log.info("  2. Sign in to the Google account that owns the channel")
    log.info("  3. Leave it on the YouTube Studio dashboard")
    log.info("")
    log.info("  Nothing you type is read or stored by this project. The session")
    log.info("  cookie lives in the /profile Docker volume, which is not in the")
    log.info("  repo and cannot be committed.")
    log.info("  ---------------------------------------------------------------")
    log.info("")

    async with async_playwright() as pw:
        ctx = await open_context(pw)
        page = await ctx.new_page()
        await page.goto(STUDIO, wait_until="domcontentloaded", timeout=90_000)

        for remaining in range(WAIT_MINUTES * 60, 0, -10):
            await asyncio.sleep(10)
            url = page.url
            if "studio.youtube.com" in url and "accounts.google.com" not in url:
                await asyncio.sleep(4)
                log.info("signed in — closing the browser and saving the profile")
                await ctx.close()
                break
            if remaining % 60 == 0:
                log.info("  waiting for sign-in… %d minutes left", remaining // 60)
        else:
            log.error("timed out waiting for sign-in")
            await ctx.close()
            return 1

    result = await check_session()
    if result.get("signed_in"):
        log.info("session confirmed. Uploads will work from now on.")
        return 0
    log.error("could not confirm the session — try again")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
