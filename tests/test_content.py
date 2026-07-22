from __future__ import annotations

import hashlib

from pipeline.frontmatter import dump
from pipeline.provenance.content import PublishedEntry, sha256_hex

_BODY = "# Wiring commits into content\n\nThis week I built the collector.\n"

_FRONT = {
    "type": "weekly-activity",
    "series": "Senior SDET log",
    "slug": "2026-W27",
    "title": "Wiring commits into content",
    "published_at": "2026-07-06",
    "status": "published",
}


def _write(tmp_path, front_extra: dict | None = None, body: str = _BODY):
    front = {**_FRONT, **(front_extra or {})}
    path = tmp_path / "2026-W27.md"
    path.write_text(dump(front, body))
    return path


def test_sha256_is_the_plain_file_hash(tmp_path):
    """The commitment is exactly sha256 of the served bytes — reproducible with
    any off-the-shelf tool, no canonical form."""
    path = _write(tmp_path)
    entry = PublishedEntry.from_path(path)
    assert entry.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert entry.sha256 == sha256_hex(path.read_bytes())


def test_from_path_reads_slug_and_date(tmp_path):
    entry = PublishedEntry.from_path(_write(tmp_path))
    assert entry.slug == "2026-W27"
    assert entry.published_at == "2026-07-06"


def test_any_byte_change_changes_the_hash(tmp_path):
    base = PublishedEntry.from_path(_write(tmp_path)).sha256
    edited_body = PublishedEntry.from_path(_write(tmp_path, body=_BODY + "One more line.\n")).sha256
    edited_front = PublishedEntry.from_path(_write(tmp_path, {"title": "Different"})).sha256
    assert edited_body != base
    assert edited_front != base


def test_hash_is_reproducible_across_reads(tmp_path):
    path = _write(tmp_path)
    assert PublishedEntry.from_path(path).sha256 == PublishedEntry.from_path(path).sha256
