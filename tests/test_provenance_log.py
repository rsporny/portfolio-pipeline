from __future__ import annotations

from pipeline.provenance import log as plog
from pipeline.provenance import merkle
from pipeline.provenance.canonical import CanonicalEntry
from pipeline.provenance.log import Anchor


def _entry(slug: str, *, title: str = "Title", body: str = "# Title\n\nbody") -> CanonicalEntry:
    return CanonicalEntry(
        slug=slug,
        title=title,
        published_at="2026-07-06",
        type="weekly-activity",
        series="Senior SDET log",
        body=body,
    )


def test_append_assigns_sequential_indices_and_root(tmp_path):
    e1, e2 = _entry("2026-W27"), _entry("2026-W28", title="Second")
    plog.record_entry(tmp_path, e1, sig="entries/2026-W27.sig", when="t1")
    _, root = plog.record_entry(tmp_path, e2, sig="entries/2026-W28.sig", when="t2")

    records = plog.load_log(tmp_path)
    assert [r.leaf_index for r in records] == [0, 1]
    assert [r.slug for r in records] == ["2026-W27", "2026-W28"]
    expected = merkle.build_root([e1.leaf_hash(), e2.leaf_hash()])
    assert root.root == expected.hex()
    assert root.tree_size == 2


def test_resign_is_idempotent_by_slug_and_updates_root(tmp_path):
    plog.record_entry(tmp_path, _entry("2026-W27"), sig="entries/2026-W27.sig", when="t1")
    _, root1 = plog.record_entry(
        tmp_path, _entry("2026-W28"), sig="entries/2026-W28.sig", when="t2"
    )

    edited = _entry("2026-W27", body="# Title\n\nedited after review")
    _, root2 = plog.record_entry(tmp_path, edited, sig="entries/2026-W27.sig", when="t3")

    records = plog.load_log(tmp_path)
    assert len(records) == 2  # no new leaf — updated in place
    w27 = next(r for r in records if r.slug == "2026-W27")
    assert w27.leaf_index == 0
    assert w27.leaf_sha256 == edited.leaf_hex()
    assert root2.root != root1.root  # content changed → root moved
    assert root2.tree_size == 2


def test_record_entry_preserves_existing_anchors(tmp_path):
    plog.record_entry(tmp_path, _entry("2026-W27"), sig="entries/2026-W27.sig", when="t1")
    plog.add_anchor(
        tmp_path,
        Anchor(
            backend="cardano",
            network="preview",
            tx_id="abc123",
            root="deadbeef",
            tree_size=1,
            anchored_at="t2",
        ),
    )
    _, root = plog.record_entry(tmp_path, _entry("2026-W28"), sig="entries/2026-W28.sig", when="t3")
    assert [a.tx_id for a in root.anchors] == ["abc123"]
    assert plog.load_root(tmp_path).anchors[0].tree_size == 1


def test_add_anchor_without_root_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        plog.add_anchor(
            tmp_path,
            Anchor(backend="null", network="-", tx_id="x", root="00", tree_size=0, anchored_at="t"),
        )
