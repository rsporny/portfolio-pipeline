from __future__ import annotations

from pipeline.frontmatter import dump
from pipeline.provenance.canonical import CanonicalEntry

_BODY = "# Wiring commits into content\n\nThis week I built the collector.\n"

_FRONT = {
    "type": "weekly-activity",
    "series": "Senior SDET log",
    "slug": "2026-W27",
    "title": "Wiring commits into content",
    "published_at": "2026-07-06",
    "status": "published",
}


def _md(front_extra: dict | None = None, body: str = _BODY) -> str:
    front = {**_FRONT, **(front_extra or {})}
    return dump(front, body)


def test_from_markdown_extracts_canonical_subset():
    entry = CanonicalEntry.from_markdown(_md())
    assert entry.slug == "2026-W27"
    assert entry.title == "Wiring commits into content"
    assert entry.published_at == "2026-07-06"
    assert entry.type == "weekly-activity"
    assert entry.series == "Senior SDET log"


def test_hash_is_stable_across_front_matter_key_order():
    reordered = {
        "title": "Wiring commits into content",
        "status": "published",
        "slug": "2026-W27",
        "published_at": "2026-07-06",
        "series": "Senior SDET log",
        "type": "weekly-activity",
    }
    assert (
        CanonicalEntry.from_markdown(dump(reordered, _BODY)).leaf_hash()
        == CanonicalEntry.from_markdown(_md()).leaf_hash()
    )


def test_hash_ignores_excluded_front_matter():
    """Attaching the provenance sidecar / badge fields, a kind, or source
    initiatives must NOT change the leaf — that's what keeps the signature valid."""
    base = CanonicalEntry.from_markdown(_md()).leaf_hash()
    with_extra = CanonicalEntry.from_markdown(
        _md(
            {
                "kind": "Note",
                "source_initiatives": ["Collector"],
                "provenance": {"signature": "2026-W27.sig", "leaf_sha256": "deadbeef"},
            }
        )
    ).leaf_hash()
    assert with_extra == base


def test_hash_normalizes_newlines_and_trailing_space():
    crlf = CanonicalEntry.from_markdown(_md(body=_BODY.replace("\n", "\r\n")))
    trailing = CanonicalEntry.from_markdown(_md(body=_BODY + "\n\n  "))
    assert (
        crlf.leaf_hash() == trailing.leaf_hash() == CanonicalEntry.from_markdown(_md()).leaf_hash()
    )


def test_hash_changes_on_body_or_title_edit():
    base = CanonicalEntry.from_markdown(_md()).leaf_hash()
    edited_body = CanonicalEntry.from_markdown(_md(body=_BODY + "\nOne more line.\n")).leaf_hash()
    edited_title = CanonicalEntry.from_markdown(_md({"title": "Different"})).leaf_hash()
    assert edited_body != base
    assert edited_title != base
