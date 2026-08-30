"""Command line entry point.

    shorttale serve     run the review UI + scheduler
    shorttale work      run the pipeline worker
    shorttale run       generate one video now
    shorttale doctor    check the environment without running anything
    shorttale campaigns list what's configured
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

from .campaign import load_campaigns
from .config import get_settings
from .db import session_scope
from .models import Job, JobStatus

log = logging.getLogger("shorttale")


def setup_logging(level: str | None = None) -> None:
    lvl = (level or get_settings().log_level or "INFO").upper()
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "httpcore", "urllib3", "praw", "prawcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def cmd_serve(args) -> int:
    import uvicorn

    settings = get_settings()
    settings.ensure_dirs()
    _start_scheduler()
    log.info("review UI on http://0.0.0.0:%d", args.port)
    uvicorn.run(
        "shorttale.web.app:app",
        host="0.0.0.0",
        port=args.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    return 0


def _start_scheduler() -> None:
    """Enqueue jobs on each campaign's cron schedule."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    settings = get_settings()
    campaigns = load_campaigns(settings.config_dir)
    scheduled = [c for c in campaigns.values() if c.enabled and c.publish.schedule]
    if not scheduled:
        log.info("no campaigns have a schedule — generation is manual only")
        return

    sched = BackgroundScheduler(timezone=settings.tz)
    for c in scheduled:
        try:
            sched.add_job(
                _enqueue,
                CronTrigger.from_crontab(c.publish.schedule, timezone=settings.tz),
                args=[c.name],
                id=f"campaign_{c.name}",
                replace_existing=True,
                misfire_grace_time=3600,
                coalesce=True,
                max_instances=1,
            )
            log.info("scheduled %s: %s (%s)", c.name, c.publish.schedule, settings.tz)
        except Exception as exc:  # noqa: BLE001
            log.error("bad schedule %r for %s: %s", c.publish.schedule, c.name, exc)
    sched.start()


def _enqueue(campaign_name: str) -> int:
    with session_scope() as s:
        # Don't pile up: skip if something for this campaign is already moving.
        active = (
            s.query(Job)
            .filter(
                Job.campaign == campaign_name,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            .count()
        )
        if active:
            log.info("skipping scheduled run for %s — one is already in flight", campaign_name)
            return -1
        job = Job(campaign=campaign_name, status=JobStatus.QUEUED, stage="harvest")
        s.add(job)
        s.flush()
        log.info("scheduled run queued job %s for %s", job.id, campaign_name)
        return job.id


# ---------------------------------------------------------------------------
# work
# ---------------------------------------------------------------------------


def cmd_work(args) -> int:
    from .pipeline.orchestrator import run_job
    from .pipeline.publish_stage import publish_job

    settings = get_settings()
    settings.ensure_dirs()
    log.info("worker up — polling every %ds", args.interval)

    while True:
        try:
            campaigns = load_campaigns(settings.config_dir)

            # 1. render queued jobs
            with session_scope() as s:
                job = (
                    s.query(Job)
                    .filter(Job.status == JobStatus.QUEUED)
                    .order_by(Job.id.asc())
                    .first()
                )
                job_id = job.id if job else None
                job_campaign = job.campaign if job else None

            if job_id:
                campaign = campaigns.get(job_campaign)
                if not campaign:
                    _mark_failed(job_id, f"campaign {job_campaign!r} is no longer configured")
                else:
                    log.info("picking up job %s (%s)", job_id, job_campaign)
                    run_job(job_id, campaign)
                    _auto_approve_if_configured(job_id, campaign)
                continue  # go straight round for the next one

            # 2. publish approved jobs
            with session_scope() as s:
                job = (
                    s.query(Job)
                    .filter(Job.status == JobStatus.APPROVED)
                    .order_by(Job.id.asc())
                    .first()
                )
                pub_id = job.id if job else None
                pub_campaign = job.campaign if job else None

            if pub_id:
                campaign = campaigns.get(pub_campaign)
                if campaign:
                    publish_job(pub_id, campaign)
                else:
                    _mark_failed(pub_id, f"campaign {pub_campaign!r} is no longer configured")
                continue

        except KeyboardInterrupt:
            log.info("worker stopping")
            return 0
        except Exception as exc:  # noqa: BLE001
            log.exception("worker loop error (continuing): %s", exc)

        time.sleep(args.interval)


def _auto_approve_if_configured(job_id: int, campaign) -> None:
    """Campaigns in an auto mode skip the review gate."""
    if campaign.publish.mode == "review":
        return
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job and job.status == JobStatus.AWAITING_REVIEW:
            job.status = JobStatus.APPROVED
            log.info(
                "job %s auto-approved (campaign publish.mode=%s)",
                job_id, campaign.publish.mode,
            )


def _mark_failed(job_id: int, error: str) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job:
            job.status = JobStatus.FAILED
            job.error = error
    log.error("job %s: %s", job_id, error)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def cmd_run(args) -> int:
    from .pipeline.orchestrator import run_job

    settings = get_settings()
    settings.ensure_dirs()
    campaigns = load_campaigns(settings.config_dir)
    campaign = campaigns.get(args.campaign)
    if not campaign:
        log.error(
            "unknown campaign %r. Available: %s",
            args.campaign, ", ".join(sorted(campaigns)) or "(none)",
        )
        return 2

    with session_scope() as s:
        job = Job(campaign=campaign.name, status=JobStatus.QUEUED, stage="harvest")
        s.add(job)
        s.flush()
        job_id = job.id

    log.info("running job %s for %s%s", job_id, campaign.name, " (dry run)" if args.dry_run else "")
    result = run_job(job_id, campaign, dry_run=args.dry_run)

    if result.get("ok"):
        print(f"\n  done — job #{job_id}")
        print(f"  video:  {result['video']}")
        if result.get("warnings"):
            print("  notes:")
            for w in result["warnings"]:
                print(f"    · {w}")
        print(f"  review: http://localhost:8080/job/{job_id}\n")
        return 0
    print(f"\n  job #{job_id} failed: {result.get('error')}\n")
    return 1


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def cmd_doctor(_args) -> int:
    from .llm.client import OllamaClient
    from .render.ffmpeg import ffmpeg_bin, nvenc_available
    import shutil
    import subprocess

    settings = get_settings()
    settings.ensure_dirs()
    ok = True

    def line(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "  ok  " if good else " FAIL "
        print(f"[{mark}] {label:<26} {detail}")
        if not good:
            ok = False

    def warn(label: str, detail: str) -> None:
        print(f"[ note ] {label:<26} {detail}")

    print("\n  short-tale environment check\n" + "  " + "-" * 62)

    # --- GPU ---
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi:
        try:
            out = subprocess.run(
                [nvsmi, "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader"],
                capture_output=True, text=True, timeout=20,
            ).stdout.strip()
            line("GPU", bool(out), out or "no device reported")
        except Exception as exc:  # noqa: BLE001
            line("GPU", False, str(exc))
    else:
        warn("GPU", "nvidia-smi not found — everything will run on CPU (slow but works)")

    # --- ffmpeg / NVENC ---
    line("ffmpeg", bool(shutil.which(ffmpeg_bin())), ffmpeg_bin())
    if nvenc_available():
        line("NVENC", True, "h264_nvenc usable — GPU encoding")
    else:
        warn("NVENC", "unavailable — encoding on CPU. Check the 'video' driver capability.")

    # --- Ollama ---
    llm = OllamaClient()
    healthy = llm.health()
    line("Ollama", healthy, llm.host)
    if healthy:
        models = llm.available_models()
        have = any(m.startswith(settings.ollama_model.split(":")[0]) for m in models)
        line("LLM model", have, settings.ollama_model if have
             else f"{settings.ollama_model} not pulled — run 'make models'")
        if models:
            warn("models present", ", ".join(models[:6]))

    # --- speech ---
    try:
        from .tts.engine import get_engine

        eng = get_engine()
        line("speech engine", True, eng.name)
    except Exception as exc:  # noqa: BLE001
        line("speech engine", False, str(exc).splitlines()[0])

    # --- whisper ---
    try:
        from .tts.captions import _pick_device

        dev, ct = _pick_device(settings.whisper_device)
        warn("whisper", f"{settings.whisper_model} on {dev} ({ct})")
    except Exception as exc:  # noqa: BLE001
        warn("whisper", f"could not probe: {exc}")

    # --- credentials (never printed, only presence) ---
    line("reddit credentials", settings.has_reddit_credentials,
         "set" if settings.has_reddit_credentials else "missing — add them to .env")

    # --- publisher ---
    try:
        from .publish import client as pub

        h = pub.health()
        line("publisher service", bool(h.get("ok")), settings.publisher_url)
        try:
            sess = pub.session_status()
            if sess.get("signed_in"):
                line("YouTube session", True, "signed in")
            elif sess.get("busy"):
                warn("YouTube session", "browser busy, could not check")
            else:
                warn("YouTube session", "not signed in — run 'make login' before publishing")
        except Exception as exc:  # noqa: BLE001
            warn("YouTube session", str(exc)[:80])
    except Exception as exc:  # noqa: BLE001
        warn("publisher service", f"unreachable: {str(exc)[:70]}")

    # --- campaigns ---
    campaigns = load_campaigns(settings.config_dir)
    line("campaigns", bool(campaigns), ", ".join(sorted(campaigns)) or "none loaded")

    # --- assets ---
    broll = list((settings.assets_dir / "broll").glob("*")) if (settings.assets_dir / "broll").is_dir() else []
    clips = [p for p in broll if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}]
    warn("b-roll clips", f"{len(clips)} in assets/broll (generated backgrounds need none)")

    print("  " + "-" * 62)
    print(f"  {'ready' if ok else 'not ready — fix the FAIL lines above'}\n")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# campaigns
# ---------------------------------------------------------------------------


def cmd_campaigns(_args) -> int:
    campaigns = load_campaigns(get_settings().config_dir)
    if not campaigns:
        print("no campaigns in config/campaigns/")
        return 1
    for c in campaigns.values():
        print(f"\n  {c.name}{'' if c.enabled else '  (disabled)'}")
        print(f"    topic     {c.topic.description.strip()[:100]}")
        print(f"    sources   reddit={c.sources.reddit.enabled} rss={c.sources.rss.enabled}")
        print(f"    product   {c.product.name} — {c.product.url}")
        print(f"    style     {c.style.format}, ~{c.style.duration.target_seconds:.0f}s, "
              f"bg={c.style.background.mode}/{c.style.background.preset}")
        print(f"    publish   {c.publish.mode} → {', '.join(c.publish.platforms)} "
              f"({c.publish.visibility}), max {c.publish.max_per_day}/day")
        print(f"    schedule  {c.publish.schedule or 'manual'}")
    print()
    return 0


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shorttale", description=__doc__)
    parser.add_argument("--log-level", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="review UI + scheduler")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("work", help="pipeline worker")
    p.add_argument("--interval", type=int, default=10)
    p.set_defaults(func=cmd_work)

    p = sub.add_parser("run", help="generate one video now")
    p.add_argument("--campaign", required=True)
    p.add_argument("--dry-run", action="store_true",
                   help="use bundled fixtures: no network, no LLM, no credentials")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("doctor", help="check the environment")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("campaigns", help="list configured campaigns")
    p.set_defaults(func=cmd_campaigns)

    args = parser.parse_args(argv)
    setup_logging(args.log_level)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
