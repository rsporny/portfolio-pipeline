from __future__ import annotations

from types import SimpleNamespace

from pipeline.continuity import (
    RelatedEntry,
    query_tokens_for,
    retrieve_related,
    tokenize,
)
from pipeline.frontmatter import dump


def _write_entry(
    site_dir,
    slug: str,
    *,
    series: str = "Senior SDET log",
    title: str = "",
    published_at: str = "2026-06-01",
    source_initiatives: tuple[str, ...] = (),
    topics: tuple[dict, ...] = (),
    status: str | None = "published",
    body: str = "Body prose.",
):
    front: dict = {
        "type": "weekly-activity",
        "series": series,
        "slug": slug,
        "title": title or slug,
        "published_at": published_at,
        "source_initiatives": list(source_initiatives),
    }
    if status is not None:
        front["status"] = status
    if topics:
        front["topics"] = list(topics)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / f"{slug}.md").write_text(dump(front, body))


# --- tokenize ----------------------------------------------------------------


def test_tokenize_drops_stopwords_short_and_numbers():
    tokens = tokenize("The provenance ledger has 42 signed entries a b")
    assert "provenance" in tokens
    assert "ledger" in tokens
    assert "signed" in tokens
    assert "entries" in tokens
    assert "the" not in tokens  # stopword
    assert "has" not in tokens  # stopword
    assert "42" not in tokens  # pure number
    assert "a" not in tokens and "b" not in tokens  # sub-3-char


def test_tokenize_is_lowercased_and_split_on_nonalnum():
    assert tokenize("Cardano-testnet Anchor!") == {"cardano", "testnet", "anchor"}


# --- query_tokens_for --------------------------------------------------------


def test_query_tokens_for_uses_threads_and_initiatives():
    thread = SimpleNamespace(
        title="Provenance ledger",
        summary="Anchoring hashes on chain",
        assumptions=[SimpleNamespace(text="Testnet suffices for now")],
    )
    init = SimpleNamespace(name="Signed feed", category="Cryptography")
    tokens = query_tokens_for([thread], [init])
    # from the thread
    assert {"provenance", "ledger", "anchoring", "hashes", "chain", "testnet"} <= tokens
    # from the initiative
    assert {"signed", "feed", "cryptography"} <= tokens


def test_query_tokens_for_tolerates_missing_attrs():
    # Duck-typed: objects without summary/assumptions/category must not raise.
    thread = SimpleNamespace(title="Collector")
    init = SimpleNamespace(name="Metadata")
    tokens = query_tokens_for([thread], [init])
    assert {"collector", "metadata"} <= tokens


# --- retrieve_related: ranking ----------------------------------------------


def test_ranks_by_overlap_then_recency(tmp_path):
    site = tmp_path / "content" / "devlog"
    _write_entry(site, "high", source_initiatives=("Provenance ledger", "Signing"))
    _write_entry(site, "low", source_initiatives=("Provenance",))
    _write_entry(site, "none", source_initiatives=("Cooking",))

    query = tokenize("provenance ledger signing")
    got = retrieve_related(site, query, max_entries=5)

    slugs = [e.slug for e in got]
    assert slugs == ["high", "low"]  # "none" scores 0 and is dropped
    assert got[0].score > got[1].score


def test_recency_breaks_score_ties(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(site, "older", source_initiatives=("Provenance",), published_at="2026-05-01")
    _write_entry(site, "newer", source_initiatives=("Provenance",), published_at="2026-07-01")

    got = retrieve_related(site, tokenize("provenance"), max_entries=5)
    assert [e.slug for e in got] == ["newer", "older"]


def test_source_initiatives_weighted_double(tmp_path):
    site = tmp_path / "devlog"
    # Same matching token, but one has it in source_initiatives, the other only
    # in the title. The source match scores higher.
    _write_entry(site, "in-source", title="Weekly notes", source_initiatives=("Provenance",))
    _write_entry(site, "in-title", title="Provenance work", source_initiatives=("Notes",))

    got = retrieve_related(site, tokenize("provenance"), max_entries=5)
    by_slug = {e.slug: e.score for e in got}
    assert by_slug["in-source"] > by_slug["in-title"]


def test_max_entries_caps_result(tmp_path):
    site = tmp_path / "devlog"
    for i in range(5):
        _write_entry(site, f"e{i}", source_initiatives=("Provenance",))
    got = retrieve_related(site, tokenize("provenance"), max_entries=2)
    assert len(got) == 2


# --- retrieve_related: filtering & guards ------------------------------------


def test_zero_score_entries_dropped(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(site, "unrelated", source_initiatives=("Gardening",))
    assert retrieve_related(site, tokenize("provenance"), max_entries=5) == []


def test_exclude_slug(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(site, "keep", source_initiatives=("Provenance",))
    _write_entry(site, "skip", source_initiatives=("Provenance",))
    got = retrieve_related(site, tokenize("provenance"), exclude={"skip"}, max_entries=5)
    assert [e.slug for e in got] == ["keep"]


def test_skips_non_published_status(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(site, "draft-one", source_initiatives=("Provenance",), status="draft")
    _write_entry(site, "published-one", source_initiatives=("Provenance",), status="published")
    got = retrieve_related(site, tokenize("provenance"), max_entries=5)
    assert [e.slug for e in got] == ["published-one"]


def test_entry_without_status_is_included(tmp_path):
    # A published file lacking a status field still counts (status filter only
    # excludes an explicit non-published value).
    site = tmp_path / "devlog"
    _write_entry(site, "no-status", source_initiatives=("Provenance",), status=None)
    got = retrieve_related(site, tokenize("provenance"), max_entries=5)
    assert [e.slug for e in got] == ["no-status"]


def test_missing_dir_returns_empty(tmp_path):
    assert retrieve_related(tmp_path / "nope", tokenize("provenance")) == []


def test_none_dir_returns_empty():
    assert retrieve_related(None, tokenize("provenance")) == []


def test_zero_max_entries_returns_empty(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(site, "e", source_initiatives=("Provenance",))
    assert retrieve_related(site, tokenize("provenance"), max_entries=0) == []


def test_empty_query_returns_empty(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(site, "e", source_initiatives=("Provenance",))
    assert retrieve_related(site, set()) == []


# --- excerpt capping ---------------------------------------------------------


def test_body_excerpt_is_capped_on_word_boundary(tmp_path):
    site = tmp_path / "devlog"
    long_body = "word " * 1000  # ~5000 chars, well over the cap
    _write_entry(site, "big", source_initiatives=("Provenance",), body=long_body)

    got = retrieve_related(site, tokenize("provenance"), max_entries=5, excerpt_chars=100)
    assert len(got) == 1
    entry = got[0]
    assert entry.body.endswith("…")
    # Capped near the limit and never splits a word ("word" is 4 chars + space).
    assert len(entry.body) <= 100 + 2
    assert "wor…" not in entry.body


def test_short_body_not_truncated(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(site, "small", source_initiatives=("Provenance",), body="Short and sweet.")
    got = retrieve_related(site, tokenize("provenance"), max_entries=5)
    assert got[0].body == "Short and sweet."
    assert isinstance(got[0], RelatedEntry)
