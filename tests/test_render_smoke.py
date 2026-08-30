"""End-to-end render smoke test.

Exercises the real ffmpeg path — background generation, ASS burn-in, card
overlays, audio mux — using a synthesised tone instead of TTS. This is the
test that catches a broken filter graph, which is otherwise only discoverable
by watching a job fail on the GPU box.
"""

import shutil
import subprocess
import wave

import numpy as np
import pytest

from shorttale.render import backgrounds, cards, subtitles, video
from shorttale.render.ffmpeg import probe_duration
from shorttale.sources.base import Candidate

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)

DURATION = 4.0
FPS = 24


@pytest.fixture
def voice(tmp_path):
    """A quiet tone standing in for narration."""
    sr = 24000
    t = np.linspace(0, DURATION, int(sr * DURATION), endpoint=False)
    tone = (0.12 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    path = tmp_path / "voice.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((tone * 32767).astype(np.int16).tobytes())
    return path


@pytest.fixture
def candidate():
    return Candidate(
        kind="reddit", external_id="smoke1",
        url="https://example.invalid/p",
        title="Signed up for one newsletter and now I get forty emails a day",
        body="I used my real address to grab a discount code back in March.",
        author="someone", origin="r/privacy",
        score=4820, num_comments=312, created_utc=1750000000.0,
    )


def test_generated_background_renders(tmp_path, demo):
    out = backgrounds.generate_background(demo, DURATION, FPS, tmp_path / "bg.mp4")
    assert out.exists() and out.stat().st_size > 1000
    assert abs(probe_duration(out) - DURATION) < 0.6


def test_every_generated_preset_produces_video(tmp_path, demo):
    """A broken preset must never be discovered mid-job on the GPU box."""
    for preset in ("aurora_drift", "radial_pulse", "plasma_drift", "soft_grain"):
        demo.style.background.preset = preset
        out = backgrounds.generate_background(demo, 1.5, 12, tmp_path / f"{preset}.mp4")
        assert out.stat().st_size > 500, f"{preset} produced nothing"


def test_unknown_preset_falls_back_instead_of_failing(tmp_path, demo):
    demo.style.background.preset = "does_not_exist"
    out = backgrounds.generate_background(demo, 1.5, 12, tmp_path / "fallback.mp4")
    assert out.exists()


def test_make_background_never_raises_with_empty_library(tmp_path, demo):
    demo.style.background.mode = "library_first"
    out = backgrounds.make_background(demo, 1.5, 12, tmp_path / "bg2.mp4")
    assert out.exists()


def test_full_compose(tmp_path, demo, candidate, voice):
    """The whole graph: background + two card overlays + karaoke ASS + audio."""
    work = tmp_path / "work"
    work.mkdir()

    segments = [
        {"index": 0, "text": "Hook line here", "visual": "source_card",
         "start": 0.0, "end": 1.6},
        {"index": 1, "text": "A middle beat", "visual": "none",
         "start": 1.6, "end": 3.0},
        {"index": 2, "text": "Example dot com", "visual": "product_card",
         "start": 3.0, "end": 4.0},
    ]
    words = [
        {"word": w, "start": i * 0.4, "end": (i + 1) * 0.4}
        for i, w in enumerate("one two three four five six seven eight nine ten".split())
    ]

    ass = subtitles.build_ass(words, demo, work / "captions.ass", DURATION)
    src_card = cards.render_source_card(candidate, demo, work / "card_source.png")
    pro_card = cards.render_product_card(demo, work / "card_product.png")
    bg = backgrounds.make_background(demo, DURATION + demo.style.end_card_seconds,
                                     FPS, work / "bg.mp4")

    out = video.compose(
        background=bg, voice=voice, ass_file=ass,
        source_card=src_card, product_card=pro_card,
        segments=segments, campaign=demo,
        out=tmp_path / "final.mp4", work_dir=work,
    )

    assert out.exists()
    expected = DURATION + demo.style.end_card_seconds
    assert abs(probe_duration(out) - expected) < 0.8, "end card was dropped"

    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True,
    ).stdout
    assert "video,1080,1920" in streams, f"wrong dimensions: {streams}"
    assert "audio" in streams, "audio track missing"


def test_thumbnail_is_grabbed(tmp_path, demo, voice):
    work = tmp_path / "w"
    work.mkdir()
    ass = subtitles.build_ass([], demo, work / "c.ass", DURATION)
    bg = backgrounds.make_background(demo, DURATION, FPS, work / "bg.mp4")
    out = video.compose(
        background=bg, voice=voice, ass_file=ass,
        source_card=None, product_card=None, segments=[],
        campaign=demo, out=tmp_path / "v.mp4", work_dir=work,
    )
    thumb = video.grab_thumbnail(out, tmp_path / "t.jpg", at=1.0)
    assert thumb.exists() and thumb.stat().st_size > 500
