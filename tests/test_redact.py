from __future__ import annotations

from pipeline.redact import redact


def test_masks_case_insensitive():
    out, n = redact("The API_KEY is here", ["api_key"])
    assert "API_KEY" not in out
    assert "[REDACTED]" in out
    assert n == 1


def test_counts_multiple_occurrences_and_phrases():
    out, n = redact("foo BAR foo baz", ["foo", "bar"])
    assert out == "[REDACTED] [REDACTED] [REDACTED] baz"
    assert n == 3


def test_empty_phrase_list_is_noop():
    out, n = redact("unchanged", [])
    assert out == "unchanged"
    assert n == 0


def test_absent_phrase_leaves_text_untouched():
    out, n = redact("hello world", ["secret"])
    assert out == "hello world"
    assert n == 0


def test_skips_empty_phrase_strings():
    out, n = redact("hello", ["", "x"])
    assert out == "hello"
    assert n == 0


def test_custom_mask():
    out, n = redact("drop token here", ["token"], mask="***")
    assert out == "drop *** here"
    assert n == 1
