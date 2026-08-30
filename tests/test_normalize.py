"""Text normalisation — the difference between a natural read and a robot."""

from shorttale.tts.normalize import normalize


def test_product_domain_becomes_words():
    assert "tailmailer dot com" in normalize("Try tailmailer.com today").lower()


def test_camelcase_domains_are_split():
    assert "dot com" in normalize("Visit MyCoolSite.com").lower()


def test_subreddits_read_naturally():
    assert "the privacy subreddit" in normalize("Someone on r/privacy said this")


def test_usernames_are_not_read_aloud():
    out = normalize("u/some_person wrote this")
    assert "some_person" not in out
    assert "someone" in out.lower()


def test_money_and_percent():
    assert "20 dollars" in normalize("It cost $20")
    assert "50 percent" in normalize("About 50% of them")


def test_emails_are_generalised():
    assert "@" not in normalize("Write to bob@example.com now")


def test_sentence_gets_terminal_punctuation():
    assert normalize("no full stop here").endswith(".")


def test_empty_input_is_safe():
    assert normalize("") == ""
