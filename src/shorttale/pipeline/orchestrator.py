"""Runs one job from harvest to a reviewable video.

Stages are strictly sequential, and that is deliberate: on a 12GB card the
language model and Whisper cannot both be resident, so the LLM is evicted
before captions are timed. Every stage records its progress so the review UI
can show where a job is, and a failure leaves the work directory intact for
inspection.
"""

from __future__ import annotations

import json
import logging
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path

from ..campaign import Campaign
from ..config import get_settings
from ..db import session_scope
from ..models import Job, JobStatus, SeenItem
from ..render import backgrounds, cards, subtitles, video
from ..sources.base import Candidate, harvest_all
from ..tts import captions as caption_mod
from ..tts.engine import synth_lines
from .rank import rank, select_best
from .script import VideoScript, generate_script

log = logging.getLogger(__name__)

STAGE_WEIGHTS = {
    "harvest": 0.08,
    "rank": 0.14,
    "script": 0.28,
    "voice": 0.46,
    "captions": 0.58,
    "visuals": 0.66,
    "render": 0.95,
    "done": 1.0,
}


class PipelineError(RuntimeError):
    pass


# ---------------------------------------------------------------------------


def _set(job_id: int, **fields) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if not job:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        job.updated_at = datetime.now(timezone.utc)


def _stage(job_id: int, name: str) -> None:
    log.info("[job %s] stage: %s", job_id, name)
    _set(job_id, stage=name, progress=STAGE_WEIGHTS.get(name, 0.0))


def _seen_keys(campaign_name: str) -> set[str]:
    with session_scope() as s:
        rows = s.query(SeenItem).filter(SeenItem.campaign == campaign_name).all()
        return {f"{r.source_kind}:{r.external_id}" for r in rows}


def _remember(campaign_name: str, candidates: list[Candidate], used: Candidate | None) -> None:
    with session_scope() as s:
        for c in candidates:
            exists = (
                s.query(SeenItem)
                .filter(
                    SeenItem.campaign == campaign_name,
                    SeenItem.source_kind == c.kind,
                    SeenItem.external_id == c.external_id,
                )
                .first()
            )
            if exists:
                if used and c.external_id == used.external_id:
                    exists.used = 1
                continue
            s.add(
                SeenItem(
                    campaign=campaign_name,
                    source_kind=c.kind,
                    external_id=c.external_id,
                    url=c.url,
                    title=c.title[:500],
                    relevance=c.relevance,
                    used=1 if used and c.external_id == used.external_id else 0,
                )
            )


#: Fixtures ship inside the package, not the test tree — the container image
#: does not copy tests/, so --dry-run would have failed there.
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load_fixture_candidates() -> list[Candidate]:
    """Bundled posts used by --dry-run so the pipeline needs no network."""
    data = json.loads((FIXTURES / "candidates.json").read_text(encoding="utf-8"))
    return [Candidate(**row) for row in data]


def _fixture_script() -> VideoScript:
    return VideoScript.model_validate_json(
        (FIXTURES / "script.json").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------


def run_job(job_id: int, campaign: Campaign, dry_run: bool = False) -> dict:
    settings = get_settings()
    settings.ensure_dirs()

    work = settings.work_dir / f"job_{job_id:06d}"
    work.mkdir(parents=True, exist_ok=True)
    _set(job_id, status=JobStatus.RUNNING, work_dir=str(work), error=None)

    llm = None
    try:
        # --- 1. harvest ----------------------------------------------------
        _stage(job_id, "harvest")
        if dry_run:
            candidates = _load_fixture_candidates()
            log.info("[job %s] dry run: %d fixture candidates", job_id, len(candidates))
        else:
            candidates = harvest_all(campaign, seen_ids=_seen_keys(campaign.name))
        if not candidates:
            raise PipelineError(
                "no candidates found. Check the campaign's subreddits and queries, "
                "and that REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set in .env."
            )

        # --- 2. rank -------------------------------------------------------
        _stage(job_id, "rank")
        from ..llm.client import OllamaClient

        llm = OllamaClient()
        if dry_run:
            for c in candidates:
                c.relevance = 90.0
            chosen = candidates[0]
        else:
            candidates = rank(candidates, campaign, llm)
            chosen = select_best(candidates, campaign)
        if chosen is None:
            _remember(campaign.name, candidates, None)
            raise PipelineError(
                f"nothing scored above min_relevance ({campaign.topic.min_relevance}). "
                "Lower it, widen the queries, or try again later."
            )

        _set(
            job_id,
            source_kind=chosen.kind,
            source_id=chosen.external_id,
            source_url=chosen.url,
            source_title=chosen.title[:500],
            relevance=chosen.relevance,
        )
        log.info("[job %s] chose: %s (%.0f) %s", job_id, chosen.title[:60], chosen.relevance or 0, chosen.url)

        # --- 3. script -----------------------------------------------------
        _stage(job_id, "script")
        warnings: list[str] = []
        if dry_run:
            script = _fixture_script()
        else:
            script, warnings = generate_script(chosen, campaign, llm)

        (work / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")

        # The language model is done. Free the VRAM before Whisper loads.
        llm.unload()

        # --- 4. voice ------------------------------------------------------
        _stage(job_id, "voice")
        voice_wav, segments = synth_lines(
            script.lines,
            work / "audio",
            voice=campaign.style.voice or settings.tts_voice,
            rate=campaign.style.speaking_rate,
        )
        voice_len = segments[-1]["end"] if segments else 0.0

        # --- 5. captions ---------------------------------------------------
        _stage(job_id, "captions")
        ass_path = work / "captions.ass"
        if campaign.style.captions.enabled:
            words = caption_mod.align_to_script(
                voice_wav, script.narration.split(), voice_len
            )
            caption_mod.unload_model()
        else:
            words = []
        subtitles.build_ass(words, campaign, ass_path, voice_len)

        # --- 6. visuals ----------------------------------------------------
        _stage(job_id, "visuals")
        source_card = None
        if campaign.style.show_source_card:
            source_card = cards.render_source_card(chosen, campaign, work / "card_source.png")
        product_card = cards.render_product_card(campaign, work / "card_product.png")

        # --- 7. render -----------------------------------------------------
        _stage(job_id, "render")
        total = voice_len + campaign.style.end_card_seconds
        bg = backgrounds.make_background(campaign, total, settings.video_fps, work / "bg.mp4")

        out_name = f"{campaign.name}_{job_id:06d}.mp4"
        out_path = settings.out_dir / out_name
        video.compose(
            background=bg,
            voice=voice_wav,
            ass_file=ass_path,
            source_card=source_card,
            product_card=product_card,
            segments=segments,
            campaign=campaign,
            out=out_path,
            work_dir=work,
        )
        thumb = video.grab_thumbnail(out_path, work / "thumb.jpg")

        # --- 8. finish -----------------------------------------------------
        description = _build_description(script, campaign, chosen)
        tags = _build_tags(script, campaign)

        _remember(campaign.name, candidates, chosen)
        _set(
            job_id,
            status=JobStatus.AWAITING_REVIEW,
            stage="done",
            progress=1.0,
            video_path=str(out_path),
            thumbnail_path=str(thumb),
            duration=round(total, 2),
            title=script.title[: campaign.publish.title_max_chars],
            description=description,
            tags=tags,
            script=json.loads(script.model_dump_json()),
            review_notes="\n".join(warnings) if warnings else None,
        )
        log.info("[job %s] complete -> %s", job_id, out_path)
        return {"ok": True, "video": str(out_path), "warnings": warnings}

    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=6)
        log.error("[job %s] failed at stage: %s", job_id, exc)
        log.debug(tb)
        _set(job_id, status=JobStatus.FAILED, error=f"{exc}\n\n{tb}"[:6000])
        return {"ok": False, "error": str(exc)}
    finally:
        if llm is not None:
            try:
                llm.unload()
            except Exception:  # noqa: BLE001
                pass


def _build_description(script: VideoScript, campaign: Campaign, chosen: Candidate) -> str:
    parts = [script.description.strip()]
    if campaign.publish.description_footer:
        parts.append(campaign.publish.description_footer.strip())
    if chosen.kind == "reddit" and chosen.url:
        parts.append(f"Source: {chosen.url}")
    if campaign.publish.hashtags:
        parts.append(" ".join(campaign.publish.hashtags))
    return "\n\n".join(p for p in parts if p)[:4900]


def _build_tags(script: VideoScript, campaign: Campaign) -> list[str]:
    tags = [t.strip().lstrip("#").lower() for t in (script.tags or []) if t.strip()]
    tags += [t.strip().lstrip("#").lower() for t in campaign.publish.hashtags]
    seen, out = set(), []
    for t in tags:
        if t and t not in seen and len(t) <= 30:
            seen.add(t)
            out.append(t)
    return out[:15]


def cleanup_work(job_id: int, keep: bool = True) -> None:
    if keep:
        return
    work = get_settings().work_dir / f"job_{job_id:06d}"
    shutil.rmtree(work, ignore_errors=True)
