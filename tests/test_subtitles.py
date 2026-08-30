"""ASS subtitle generation."""

from shorttale.render.subtitles import _ass_color, _ts, build_ass


def test_color_conversion_is_bgr_with_alpha():
    # #3D7DFF -> &H00FF7D3D
    assert _ass_color("#3D7DFF") == "&H00FF7D3D"
    assert _ass_color("#FFFFFF") == "&H00FFFFFF"
    assert _ass_color("#000") == "&H00000000"


def test_timestamp_format():
    assert _ts(0) == "0:00:00.00"
    assert _ts(65.5) == "0:01:05.50"
    assert _ts(-3) == "0:00:00.00"


def test_karaoke_emits_one_event_per_word(tmp_path, tailmailer):
    words = [
        {"word": f"w{i}", "start": i * 0.4, "end": (i + 1) * 0.4} for i in range(9)
    ]
    out = build_ass(words, tailmailer, tmp_path / "c.ass", 3.6)
    body = out.read_text()
    assert body.count("Dialogue:") == 9
    assert "PlayResX: 1080" in body and "PlayResY: 1920" in body
    assert "[V4+ Styles]" in body


def test_block_style_groups_words(tmp_path, tailmailer):
    tailmailer.style.captions.style = "block"
    tailmailer.style.captions.max_words_per_line = 3
    words = [{"word": f"w{i}", "start": i * 0.3, "end": (i + 1) * 0.3} for i in range(9)]
    out = build_ass(words, tailmailer, tmp_path / "c.ass", 2.7)
    assert out.read_text().count("Dialogue:") == 3


def test_disabled_captions_produce_a_header_only(tmp_path, tailmailer):
    tailmailer.style.captions.enabled = False
    out = build_ass([], tailmailer, tmp_path / "c.ass", 3.0)
    assert "Dialogue:" not in out.read_text()


def test_braces_are_escaped(tmp_path, tailmailer):
    """An unescaped { would be read as an ASS override tag and eat the line."""
    words = [{"word": "{evil}", "start": 0.0, "end": 0.5}]
    out = build_ass(words, tailmailer, tmp_path / "c.ass", 0.5)
    assert "\\{evil\\}" in out.read_text()
