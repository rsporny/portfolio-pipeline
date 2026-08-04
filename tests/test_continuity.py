from __future__ import annotations

from types import SimpleNamespace

from pipeline.continuity import (
    Section,
    covered_thread_ids,
    load_sections,
    query_tokens_for,
    score_sections,
    thread_query_tokens,
    tokenize,
)
from pipeline.frontmatter import dump


def _thread(id, title="", summary="", assumptions=()):
    return SimpleNamespace(
        id=id,
        title=title,
        summary=summary,
        assumptions=[SimpleNamespace(text=t) for t in assumptions],
    )


def _write_entry(
    site_dir,
    slug: str,
    *,
    title: str = "",
    published_at: str = "2026-06-01",
    source_initiatives: tuple[str, ...] = (),
    topics: tuple[dict, ...] = (),
    status: str | None = "published",
    sections: tuple[tuple[str, str], ...] = (),
    body: str | None = None,
):
    """Write a published entry. Either pass ``sections`` (list of (heading, text)
    → a multi-section entry with an intro) or ``body`` (a single flowing entry)."""
    front: dict = {
        "type": "weekly-activity",
        "series": "Senior SDET log",
        "slug": slug,
        "title": title or slug,
        "published_at": published_at,
        "source_initiatives": list(source_initiatives),
    }
    if status is not None:
        front["status"] = status
    if topics:
        front["topics"] = list(topics)
    if sections:
        parts = [f"# {front['title']}", "", "Intro paragraph spanning the week."]
        for heading, text in sections:
            parts += [f"## {heading}", "", text]
        doc_body = "\n\n".join(parts)
    else:
        doc_body = f"# {front['title']}\n\n{body or 'Flowing body prose.'}"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / f"{slug}.md").write_text(dump(front, doc_body))


# --- tokenize ----------------------------------------------------------------


def test_tokenize_drops_stopwords_short_and_numbers():
    tokens = tokenize("The provenance ledger has 42 signed entries a b")
    assert {"provenance", "ledger", "signed", "entries"} <= tokens
    assert "the" not in tokens and "has" not in tokens
    assert "42" not in tokens
    assert "a" not in tokens and "b" not in tokens


def test_tokenize_splits_on_nonalnum_and_lowercases():
    assert tokenize("Cardano-testnet Anchor!") == {"cardano", "testnet", "anchor"}


# --- query token builders ----------------------------------------------------


def test_thread_query_tokens_covers_title_summary_assumptions():
    t = _thread(
        "x", title="Consensus guard", summary="committee rotation", assumptions=("finality holds",)
    )
    assert {
        "consensus",
        "guard",
        "committee",
        "rotation",
        "finality",
        "holds",
    } <= thread_query_tokens(t)


def test_query_tokens_for_includes_initiatives():
    t = _thread("x", title="Collector")
    init = SimpleNamespace(name="Signed feed", category="Cryptography")
    tokens = query_tokens_for([t], [init])
    assert {"collector", "signed", "feed", "cryptography"} <= tokens


# --- load_sections -----------------------------------------------------------


def test_load_sections_splits_by_heading_and_drops_intro(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(
        site,
        "w1",
        sections=(("Alpha topic", "alpha body"), ("Beta topic", "beta body")),
    )
    secs = load_sections(site)
    headings = {s.heading for s in secs}
    assert headings == {"Alpha topic", "Beta topic"}
    # The intro/H1 preamble is not a section.
    assert all("Intro paragraph" not in s.body for s in secs)
    alpha = next(s for s in secs if s.heading == "Alpha topic")
    assert alpha.body == "alpha body"
    assert "alpha" in alpha.head_tokens  # heading tokenized


def test_load_sections_flowing_entry_is_one_section(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(
        site, "flow", title="Weekly note", source_initiatives=("Provenance",), body="the prose"
    )
    secs = load_sections(site)
    assert len(secs) == 1
    # Flowing entry folds source_initiatives + title into head tokens.
    assert {"provenance", "weekly", "note"} <= secs[0].head_tokens
    assert secs[0].body == "the prose"


def test_load_sections_skips_non_published_and_excluded(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(site, "pub", sections=(("Topic", "b"),))
    _write_entry(site, "draft", sections=(("Topic", "b"),), status="draft")
    _write_entry(site, "skipme", sections=(("Topic", "b"),))
    slugs = {s.slug for s in load_sections(site, exclude={"skipme"})}
    assert slugs == {"pub"}


def test_load_sections_missing_or_none_dir(tmp_path):
    assert load_sections(tmp_path / "nope") == []
    assert load_sections(None) == []


# --- score_sections ----------------------------------------------------------


def test_scores_the_relevant_section_not_the_top(tmp_path):
    """The 0.6.0 regression: a multi-topic entry's relevant section sits far below
    the top, so a top-of-entry excerpt missed it. Section scoring must return the
    matching section's own body regardless of its position in the entry."""
    site = tmp_path / "devlog"
    _write_entry(
        site,
        "w30",
        sections=(
            ("Cryptographic provenance for published content", "sha256 signing anchor prose"),
            ("Automated quality gate for LLM output", "eval scorecard prose"),
            ("Stateless engine and forkable state layout", "state root prose"),
            ("Consensus committee regression guard", "MARKER committee finality authorship prose"),
            ("Headless VM dev-environment fixes", "vm fixes prose"),
        ),
    )
    query = tokenize("consensus committee regression guard finality")
    got = score_sections(load_sections(site), query, max_entries=3)
    assert got[0].heading == "Consensus committee regression guard"
    assert "MARKER committee finality authorship prose" in got[0].body


def test_requires_heading_overlap(tmp_path):
    # A section whose body mentions the query but whose heading does not must not
    # match — that precision is what stops cross-section leakage.
    site = tmp_path / "devlog"
    _write_entry(
        site, "w1", sections=(("Totally unrelated heading", "mentions provenance in body"),)
    )
    assert score_sections(load_sections(site), tokenize("provenance"), max_entries=5) == []


def test_ranks_by_score_then_recency(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(site, "older", published_at="2026-05-01", sections=(("Provenance ledger", "b"),))
    _write_entry(site, "newer", published_at="2026-07-01", sections=(("Provenance ledger", "b"),))
    _write_entry(site, "weak", published_at="2026-06-01", sections=(("Provenance only", "b"),))
    got = score_sections(load_sections(site), tokenize("provenance ledger"), max_entries=5)
    # Two full-heading matches tie on score → newer first; weaker (1 token) last.
    assert [s.slug for s in got[:2]] == ["newer", "older"]


def test_max_entries_and_empty_query(tmp_path):
    site = tmp_path / "devlog"
    for i in range(4):
        _write_entry(site, f"e{i}", sections=(("Provenance ledger", "b"),))
    secs = load_sections(site)
    assert len(score_sections(secs, tokenize("provenance ledger"), max_entries=2)) == 2
    assert score_sections(secs, set(), max_entries=5) == []


def test_body_excerpt_capped(tmp_path):
    site = tmp_path / "devlog"
    _write_entry(site, "big", sections=(("Provenance ledger", "word " * 1000),))
    got = score_sections(
        load_sections(site, excerpt_chars=100), tokenize("provenance ledger"), max_entries=1
    )
    assert got[0].body.endswith("…")
    assert len(got[0].body) <= 100 + 2


# --- covered_thread_ids ------------------------------------------------------


def test_covered_requires_strong_heading_overlap(tmp_path):
    site = tmp_path / "devlog"
    # A section that clearly covers the consensus thread (many shared tokens)...
    _write_entry(site, "w30", sections=(("Consensus committee regression guard", "b"),))
    secs = load_sections(site)
    consensus = _thread(
        "consensus",
        title="Consensus committee sizing and finality",
        summary="regression guard block authorship",
    )
    unrelated = _thread(
        "gardening", title="Backyard tomato irrigation schedule", summary="drip watering"
    )
    covered = covered_thread_ids(secs, [consensus, unrelated])
    assert covered == {"consensus"}


def test_covered_ignores_incidental_two_token_overlap(tmp_path):
    # Generic overlap below the threshold (default 3) is not coverage.
    site = tmp_path / "devlog"
    _write_entry(site, "w1", sections=(("Memory layer hardening", "b"),))
    secs = load_sections(site)
    # Shares only "memory"/"hardening" incidentally — must not count as covered.
    cnight = _thread(
        "cnight", title="Nightly reliability", summary="in-memory window hardening of the faucet"
    )
    assert covered_thread_ids(secs, [cnight]) == set()


def test_covered_empty_when_no_sections():
    assert covered_thread_ids([], [_thread("x", title="anything at all here")]) == set()
    assert isinstance(
        Section(slug="s", series="", date="", entry_title="", heading="", body=""), Section
    )
