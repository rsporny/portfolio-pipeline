from __future__ import annotations

import pytest

from pipeline.provenance import log as plog
from pipeline.provenance.content import PublishedEntry
from pipeline.provenance.log import Anchor


def _entry(slug: str, *, body: bytes = b"# Title\n\nbody") -> PublishedEntry:
    data = b"---\nslug: " + slug.encode() + b"\npublished_at: 2026-07-06\n---\n\n" + body
    return PublishedEntry(slug=slug, published_at="2026-07-06", data=data)


def _anchor(sha: str, tx: str = "tx1") -> Anchor:
    return Anchor(backend="file", network="local", tx_id=tx, sha256=sha, anchored_at="t")


def test_records_are_keyed_and_ordered_by_slug(tmp_path):
    plog.record_entry(tmp_path, _entry("2026-W28"), sig="entries/2026-W28.md.sig", when="t2")
    plog.record_entry(tmp_path, _entry("2026-W27"), sig="entries/2026-W27.md.sig", when="t1")

    records = plog.load_log(tmp_path)
    assert [r.slug for r in records] == ["2026-W27", "2026-W28"]
    assert records[0].sha256 == _entry("2026-W27").sha256


def test_resign_is_idempotent_by_slug(tmp_path):
    plog.record_entry(tmp_path, _entry("2026-W27"), sig="entries/2026-W27.md.sig", when="t1")
    edited = _entry("2026-W27", body=b"# Title\n\nedited after review")
    plog.record_entry(tmp_path, edited, sig="entries/2026-W27.md.sig", when="t2")

    records = plog.load_log(tmp_path)
    assert len(records) == 1  # updated in place, no duplicate
    assert records[0].sha256 == edited.sha256


def test_set_anchor_attaches_to_record(tmp_path):
    entry = _entry("2026-W27")
    plog.record_entry(tmp_path, entry, sig="entries/2026-W27.md.sig", when="t1")
    plog.set_anchor(tmp_path, "2026-W27", _anchor(entry.sha256))
    rec = plog.load_log(tmp_path)[0]
    assert rec.anchor is not None
    assert rec.anchor.tx_id == "tx1"
    assert rec.anchor.sha256 == entry.sha256


def test_resign_after_edit_drops_stale_anchor(tmp_path):
    entry = _entry("2026-W27")
    plog.record_entry(tmp_path, entry, sig="entries/2026-W27.md.sig", when="t1")
    plog.set_anchor(tmp_path, "2026-W27", _anchor(entry.sha256))

    edited = _entry("2026-W27", body=b"# Title\n\nedited")
    plog.record_entry(tmp_path, edited, sig="entries/2026-W27.md.sig", when="t2")
    rec = plog.load_log(tmp_path)[0]
    assert rec.anchor is None  # pinned the old bytes → dropped


def test_resign_without_edit_keeps_anchor(tmp_path):
    entry = _entry("2026-W27")
    plog.record_entry(tmp_path, entry, sig="entries/2026-W27.md.sig", when="t1")
    plog.set_anchor(tmp_path, "2026-W27", _anchor(entry.sha256))
    plog.record_entry(tmp_path, entry, sig="entries/2026-W27.md.sig", when="t2")
    assert plog.load_log(tmp_path)[0].anchor is not None


def test_set_anchor_without_entry_raises(tmp_path):
    with pytest.raises(KeyError):
        plog.set_anchor(tmp_path, "2026-W99", _anchor("00"))
