"""Script validation — the gate between the model and something published."""

from shorttale.pipeline.script import (
    VideoScript,
    mechanical_check,
    sanitize_for_speech,
    word_budget,
)


def test_sanitize_strips_everything_unspeakable():
    dirty = "**Bold** and `code` 🎉 (pause) [SFX] #hashtag <b>tag</b>"
    clean = sanitize_for_speech(dirty)
    for junk in ("*", "`", "🎉", "(pause)", "[SFX]", "#hashtag", "<b>"):
        assert junk not in clean
    assert "Bold" in clean and "code" in clean


def test_word_budget_tracks_duration(tailmailer):
    b = word_budget(tailmailer)
    assert b["min"] < b["target"] < b["max"]
    # 38s target at ~2.7 words/sec, minus the end card
    assert 80 < b["target"] < 120


def test_length_violations_are_caught(tailmailer):
    short = VideoScript(
        hook="Too short.", beats=[], cta="Tailmailer, at tailmailer.com.",
        title="A title", description="d", tags=["a"],
    )
    problems = mechanical_check(short, tailmailer)
    assert any("too short" in p for p in problems)


def test_ad_speak_is_caught(tailmailer):
    s = _valid_script(tailmailer, extra="This is a total game changer honestly.")
    assert any("game changer" in p for p in mechanical_check(s, tailmailer))


def test_forbidden_claim_is_caught(tailmailer):
    # "blocks 100% of spam" is on the campaign's forbidden list.
    s = _valid_script(tailmailer, extra="It blocks 100% of spam forever and always.")
    problems = mechanical_check(s, tailmailer)
    assert any("forbidden claim" in p for p in problems)


def test_missing_product_url_is_caught(tailmailer):
    s = VideoScript(
        hook="A perfectly reasonable hook about inbox spam here.",
        beats=[{"text": " ".join(["word"] * 90), "visual": "none"}],
        cta="Go check it out sometime.",
        title="Title", description="d", tags=["a"],
    )
    assert any("never says" in p for p in mechanical_check(s, tailmailer))


def test_a_good_script_passes_clean(tailmailer):
    assert mechanical_check(_valid_script(tailmailer), tailmailer) == []


def test_lines_are_ordered_hook_beats_cta(tailmailer):
    s = _valid_script(tailmailer)
    lines = s.lines
    assert lines[0][1] == "source_card"
    assert lines[-1][1] == "product_card"
    assert s.narration.startswith(s.hook)


def _valid_script(campaign, extra: str = "") -> VideoScript:
    target = word_budget(campaign)["target"]
    body = "They signed up once and the mail never stopped coming after that day. "
    filler = (body * 12).split()
    need = max(target - 22 - len(extra.split()), 5)
    return VideoScript(
        hook="One newsletter signup, and the inbox was gone.",
        beats=[
            {"text": " ".join(filler[:need]) + (" " + extra if extra else ""),
             "visual": "source_card"},
        ],
        cta="Tailmailer, at tailmailer.com.",
        title="One signup ruined this inbox",
        description="A short about inbox spam.",
        tags=["privacy", "spam"],
    )
