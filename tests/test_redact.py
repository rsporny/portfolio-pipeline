from __future__ import annotations

from pipeline.redact import redact, redact_names


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


# --- redact_names -----------------------------------------------------------


def test_redact_names_masks_login_and_mention():
    out, n = redact_names("thanks octocat and @hubber for the review", ["octocat", "hubber"])
    assert "octocat" not in out
    assert "hubber" not in out
    assert "@" not in out  # the @mention is consumed with the login
    assert out == "thanks [collaborator] and [collaborator] for the review"
    assert n == 2


def test_redact_names_is_case_insensitive():
    out, n = redact_names("Octocat opened it", ["octocat"])
    assert out == "[collaborator] opened it"
    assert n == 1


def test_redact_names_masks_display_name_before_login_substring():
    # "ada" is a substring of the display name; longest-first avoids a partial hit.
    out, n = redact_names("reviewed by Ada Lovelace (ada)", ["ada", "Ada Lovelace"])
    assert "Ada Lovelace" not in out
    assert out == "reviewed by [collaborator] ([collaborator])"
    assert n == 2


def test_redact_names_respects_word_boundaries():
    out, n = redact_names("coal and shoal", ["al"])
    assert out == "coal and shoal"
    assert n == 0


def test_redact_names_empty_set_is_noop():
    out, n = redact_names("nothing to mask here", [])
    assert out == "nothing to mask here"
    assert n == 0


def test_redact_names_custom_placeholder():
    out, n = redact_names("ping @carol", ["carol"], placeholder="[reviewer]")
    assert out == "ping [reviewer]"
    assert n == 1
