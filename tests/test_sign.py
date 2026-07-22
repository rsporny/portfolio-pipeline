from __future__ import annotations

import hashlib

from pipeline.provenance import log as plog
from pipeline.provenance.canonical import CanonicalEntry
from pipeline.provenance.sign import sign_entry


def _fake_signer(data: bytes) -> str:
    return "SIG:" + hashlib.sha256(data).hexdigest()


def _entry(slug: str, body: str = "# Title\n\nbody") -> CanonicalEntry:
    return CanonicalEntry(
        slug=slug,
        title="Title",
        published_at="2026-07-06",
        type="weekly-activity",
        series="Senior SDET log",
        body=body,
    )


def test_sign_entry_writes_sidecar_records_leaf_and_returns_proof(tmp_path):
    entry = _entry("2026-W27")
    proof = sign_entry(tmp_path, entry, signer=_fake_signer, fingerprint="ABCD1234", when="t1")

    sig_file = tmp_path / "entries" / "2026-W27.sig"
    assert sig_file.exists()
    assert sig_file.read_text() == _fake_signer(entry.to_bytes())

    assert proof.slug == "2026-W27"
    assert proof.leaf_sha256 == entry.leaf_hex()
    assert proof.sig_filename == "2026-W27.sig"
    assert proof.pubkey_fingerprint == "ABCD1234"

    records = plog.load_log(tmp_path)
    assert len(records) == 1
    assert records[0].sig == "entries/2026-W27.sig"
    assert records[0].leaf_sha256 == entry.leaf_hex()


def test_resign_updates_sidecar_in_place(tmp_path):
    sign_entry(tmp_path, _entry("2026-W27"), signer=_fake_signer, fingerprint="FP", when="t1")
    edited = _entry("2026-W27", body="# Title\n\nedited")
    sign_entry(tmp_path, edited, signer=_fake_signer, fingerprint="FP", when="t2")

    records = plog.load_log(tmp_path)
    assert len(records) == 1  # same leaf, updated
    assert records[0].leaf_sha256 == edited.leaf_hex()
    assert (tmp_path / "entries" / "2026-W27.sig").read_text() == _fake_signer(edited.to_bytes())
