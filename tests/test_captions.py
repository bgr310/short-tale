"""Caption timing — including the fallbacks that keep a job alive."""

from shorttale.tts.captions import _even_spacing, _fill_gaps


def test_even_spacing_covers_the_duration():
    words = "one two three four five".split()
    out = _even_spacing(words, 5.0)
    assert len(out) == 5
    assert out[0]["start"] == 0.0
    assert abs(out[-1]["end"] - 5.0) < 0.01
    assert [w["word"] for w in out] == words


def test_even_spacing_weights_by_word_length():
    out = _even_spacing(["a", "extraordinarily"], 4.0)
    assert (out[1]["end"] - out[1]["start"]) > (out[0]["end"] - out[0]["start"])


def test_gaps_between_matched_words_are_interpolated():
    words = ["alpha", "bravo", "charlie", "delta"]
    timed = [
        {"word": "alpha", "start": 0.0, "end": 0.5},
        None,
        None,
        {"word": "delta", "start": 2.0, "end": 2.5},
    ]
    out = _fill_gaps(timed, words, 3.0)
    assert len(out) == 4
    assert all("start" in w and "end" in w for w in out)
    # bravo and charlie share the 0.5 -> 2.0 gap
    assert 0.5 <= out[1]["start"] < out[2]["start"] < 2.0


def test_timings_never_go_backwards():
    words = [f"w{i}" for i in range(8)]
    timed = [None] * 8
    timed[3] = {"word": "w3", "start": 1.0, "end": 1.2}
    out = _fill_gaps(timed, words, 4.0)
    for a, b in zip(out, out[1:]):
        assert a["end"] <= b["start"] + 1e-6
        assert b["end"] > b["start"]


def test_leading_and_trailing_runs_are_filled():
    words = ["a", "b", "c"]
    timed = [None, {"word": "b", "start": 1.0, "end": 1.4}, None]
    out = _fill_gaps(timed, words, 2.5)
    assert out[0]["start"] == 0.0
    assert out[2]["end"] > out[2]["start"]
