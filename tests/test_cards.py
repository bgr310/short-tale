"""Card rendering."""

from PIL import Image

from shorttale.render.cards import render_product_card, render_source_card
from shorttale.sources.base import Candidate


def _candidate(**kw) -> Candidate:
    base = dict(
        kind="reddit", external_id="x1", url="https://example.invalid/p",
        title="Signed up for one newsletter and now I get forty emails a day",
        body="I used my real address to grab a discount code back in March.",
        author="real_person_handle", origin="r/privacy",
        score=4820, num_comments=312, created_utc=1750000000.0,
    )
    base.update(kw)
    return Candidate(**base)


def test_source_card_renders(tmp_path, tailmailer):
    out = render_source_card(_candidate(), tailmailer, tmp_path / "card.png")
    img = Image.open(out)
    assert img.mode == "RGBA"
    assert img.width == 1020          # 940 card + shadow margin
    assert 400 < img.height < 1400


def test_author_is_anonymised_by_default(tailmailer):
    assert tailmailer.style.anonymize_authors is True


def test_long_titles_do_not_blow_up_the_card(tmp_path, tailmailer):
    huge = _candidate(title="word " * 200, body="body " * 400)
    out = render_source_card(huge, tailmailer, tmp_path / "big.png")
    # Title caps at 4 lines and body at 5, so height stays bounded.
    assert Image.open(out).height < 1300


def test_missing_body_still_renders(tmp_path, tailmailer):
    out = render_source_card(_candidate(body=""), tailmailer, tmp_path / "nobody.png")
    assert Image.open(out).height > 200


def test_product_card_renders(tmp_path, tailmailer):
    out = render_product_card(tailmailer, tmp_path / "p.png")
    img = Image.open(out)
    assert (img.width, img.height) == (940, 520)


def test_text_that_fits_gets_no_ellipsis(tmp_path, tailmailer):
    """A folded YAML scalar leaves a trailing space; that must not add '…'."""
    from PIL import ImageDraw, Image
    from shorttale.render.cards import _fit_lines
    from shorttale.render.fonts import load

    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _fit_lines(d, "short enough to fit ", load("DejaVuSans", 30, "regular"), 800, 3)
    assert not lines[-1].endswith("…")


def test_overlong_text_does_get_an_ellipsis(tmp_path, tailmailer):
    from PIL import ImageDraw, Image
    from shorttale.render.cards import _fit_lines
    from shorttale.render.fonts import load

    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _fit_lines(d, "word " * 300, load("DejaVuSans", 30, "regular"), 400, 3)
    assert len(lines) == 3 and lines[-1].endswith("…")


def test_a_single_unbreakable_word_does_not_hang(tmp_path, tailmailer):
    from PIL import ImageDraw, Image
    from shorttale.render.cards import _fit_lines
    from shorttale.render.fonts import load

    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _fit_lines(d, "A" * 400, load("DejaVuSans", 30, "regular"), 300, 2)
    assert 1 <= len(lines) <= 2
