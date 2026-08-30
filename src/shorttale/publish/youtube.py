"""YouTube Shorts upload driven through YouTube Studio.

A caveat worth being honest about: this drives the Studio web UI, which is not
a supported automation surface. It works, but Google changes that UI without
notice, so treat a broken upload as expected maintenance rather than a bug in
your setup. The selector lists below are deliberately redundant for that
reason, and docs/PUBLISHING.md explains the officially supported Data API
route if you ever want to switch.

A video under 60 seconds with a 9:16 aspect ratio is classified as a Short by
YouTube automatically — there is no "make this a Short" switch to toggle.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from .browser import first_visible, open_context

log = logging.getLogger(__name__)

STUDIO = "https://studio.youtube.com/"

SEL = {
    "create": [
        "ytcp-button#create-icon",
        "#create-icon",
        "button[aria-label='Create']",
        "ytcp-button[aria-label='Create']",
    ],
    "upload_item": [
        "tp-yt-paper-item#text-item-0",
        "#text-item-0",
        "tp-yt-paper-item:has-text('Upload videos')",
    ],
    "file_input": ["input[type='file']"],
    "title": [
        "ytcp-social-suggestions-textbox#title-textarea #textbox",
        "#title-textarea #textbox",
        "div[aria-label*='Add a title']",
        "#title #textbox",
    ],
    "description": [
        "ytcp-social-suggestions-textbox#description-textarea #textbox",
        "#description-textarea #textbox",
        "div[aria-label*='Tell viewers about your video']",
    ],
    "not_for_kids": [
        "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
        "#audience tp-yt-paper-radio-button:nth-of-type(2)",
        "tp-yt-paper-radio-button:has-text('No, it')",
    ],
    "for_kids": [
        "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_MFK']",
        "tp-yt-paper-radio-button:has-text('Yes, it')",
    ],
    "next": [
        "ytcp-button#next-button",
        "#next-button",
        "button[aria-label='Next']",
    ],
    "visibility": {
        "public": [
            "tp-yt-paper-radio-button[name='PUBLIC']",
            "tp-yt-paper-radio-button:has-text('Public')",
        ],
        "unlisted": [
            "tp-yt-paper-radio-button[name='UNLISTED']",
            "tp-yt-paper-radio-button:has-text('Unlisted')",
        ],
        "private": [
            "tp-yt-paper-radio-button[name='PRIVATE']",
            "tp-yt-paper-radio-button:has-text('Private')",
        ],
    },
    "done": [
        "ytcp-button#done-button",
        "#done-button",
        "button[aria-label='Publish']",
        "button[aria-label='Done']",
    ],
    "video_link": ["a.ytcp-video-info", "a[href*='youtu.be/']", "a[href*='/watch?v=']"],
    "signin": ["input[type='email']", "#identifierId", "a:has-text('Sign in')"],
}

_VIDEO_ID = re.compile(r"(?:youtu\.be/|watch\?v=|/shorts/)([A-Za-z0-9_-]{11})")


class PublishError(RuntimeError):
    pass


class NotSignedIn(PublishError):
    pass


async def check_session() -> dict:
    """Is the stored profile still signed in to a YouTube channel?"""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        ctx = await open_context(pw)
        try:
            page = await ctx.new_page()
            await page.goto(STUDIO, wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(3)
            url = page.url
            if "accounts.google.com" in url or await first_visible(page, SEL["signin"], 3000):
                return {"signed_in": False, "url": url}
            channel = None
            m = re.search(r"/channel/([A-Za-z0-9_-]+)", url)
            if m:
                channel = m.group(1)
            return {"signed_in": True, "url": url, "channel": channel}
        finally:
            await ctx.close()


async def upload(
    video: Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    visibility: str = "public",
    made_for_kids: bool = False,
    timeout_minutes: int = 25,
) -> dict:
    """Upload one video and return {url, video_id}."""
    from playwright.async_api import async_playwright

    if not video.exists():
        raise PublishError(f"video not found: {video}")

    async with async_playwright() as pw:
        ctx = await open_context(pw)
        try:
            page = await ctx.new_page()
            await page.goto(STUDIO, wait_until="domcontentloaded", timeout=90_000)
            await asyncio.sleep(3)

            if "accounts.google.com" in page.url or await first_visible(page, SEL["signin"], 3000):
                raise NotSignedIn(
                    "the browser profile is not signed in. Run `make login`, open "
                    "http://localhost:7900/vnc.html and sign in by hand — it is "
                    "stored in the profile volume and only needs doing once."
                )

            # --- open the upload dialog --------------------------------------
            btn = await first_visible(page, SEL["create"], 20_000)
            if not btn:
                raise PublishError("could not find the Create button in Studio")
            await btn.click()
            await asyncio.sleep(1.2)

            item = await first_visible(page, SEL["upload_item"], 12_000)
            if item:
                await item.click()
            await asyncio.sleep(2)

            # --- attach the file --------------------------------------------
            file_input = page.locator(SEL["file_input"][0]).first
            await file_input.wait_for(state="attached", timeout=30_000)
            await file_input.set_input_files(str(video))
            log.info("uploading %s (%.1f MB)", video.name, video.stat().st_size / 1e6)
            await asyncio.sleep(6)

            # --- metadata ----------------------------------------------------
            tbox = await first_visible(page, SEL["title"], 90_000)
            if not tbox:
                raise PublishError("the title field never appeared — upload may have failed")
            await tbox.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await tbox.type(title[:100], delay=12)
            await asyncio.sleep(0.8)

            body = description or ""
            if tags:
                body = f"{body}\n\n" + " ".join(f"#{t.lstrip('#')}" for t in tags[:8])
            dbox = await first_visible(page, SEL["description"], 15_000)
            if dbox and body.strip():
                await dbox.click()
                await dbox.type(body[:4900], delay=3)
            await asyncio.sleep(0.8)

            kids = await first_visible(
                page, SEL["for_kids"] if made_for_kids else SEL["not_for_kids"], 15_000
            )
            if kids:
                await kids.click()
            else:
                log.warning("could not set the made-for-kids choice — check it by hand")
            await asyncio.sleep(0.6)

            # --- three Next screens ------------------------------------------
            for step in range(3):
                nxt = await first_visible(page, SEL["next"], 20_000)
                if not nxt:
                    log.warning("Next button missing on step %d", step + 1)
                    break
                await nxt.click()
                await asyncio.sleep(1.6)

            # --- visibility ---------------------------------------------------
            vis = await first_visible(
                page, SEL["visibility"].get(visibility, SEL["visibility"]["private"]), 20_000
            )
            if vis:
                await vis.click()
            else:
                raise PublishError(f"could not select visibility {visibility!r}")
            await asyncio.sleep(1.0)

            # --- wait for processing, then publish ----------------------------
            video_url = await _wait_for_link(page, timeout_minutes)

            done = await first_visible(page, SEL["done"], 30_000)
            if not done:
                raise PublishError("could not find the Publish button")
            await done.click()
            await asyncio.sleep(6)

            vid = _VIDEO_ID.search(video_url or "")
            result = {
                "url": f"https://youtube.com/shorts/{vid.group(1)}" if vid else video_url,
                "video_id": vid.group(1) if vid else None,
                "visibility": visibility,
            }
            log.info("published: %s", result["url"])
            return result

        finally:
            await ctx.close()


async def _wait_for_link(page, timeout_minutes: int) -> str:
    """Poll for the video link Studio shows once the upload has landed."""
    deadline = asyncio.get_event_loop().time() + timeout_minutes * 60
    while asyncio.get_event_loop().time() < deadline:
        link = await first_visible(page, SEL["video_link"], 2500)
        if link:
            href = await link.get_attribute("href")
            if href and _VIDEO_ID.search(href):
                return href
        await asyncio.sleep(5)
    log.warning("never saw the video link — publishing anyway")
    return ""
