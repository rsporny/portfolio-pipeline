from __future__ import annotations

import hashlib

from pipeline.provenance import log as plog
from pipeline.provenance.content import PublishedEntry
from pipeline.provenance.sign import sign_entry


def _fake_signer(data: bytes) -> str:
    return "SIG:" + hashlib.sha256(data).hexdigest()


def _entry(slug: str, body: bytes = b"# Title\n\nbody") -> PublishedEntry:
    data = b"---\nslug: " + slug.encode() + b"\npublished_at: 2026-07-06\n---\n\n" + body
    return PublishedEntry(slug=slug, published_at="2026-07-06", data=data)


def test_sign_entry_writes_sidecar_records_entry_and_returns_proof(tmp_path):
    entry = _entry("2026-W27")
    proof = sign_entry(tmp_path, entry, signer=_fake_signer, fingerprint="ABCD1234", when="t1")

    sig_file = tmp_path / "entries" / "2026-W27.md.sig"
    assert sig_file.exists()
    assert sig_file.read_text() == _fake_signer(entry.data)  # signs the raw file bytes

    assert proof.slug == "2026-W27"
    assert proof.sha256 == entry.sha256
    assert proof.sig_filename == "2026-W27.md.sig"
    assert proof.pubkey_fingerprint == "ABCD1234"

    records = plog.load_log(tmp_path)
    assert len(records) == 1
    assert records[0].sig == "entries/2026-W27.md.sig"
    assert records[0].sha256 == entry.sha256


def test_resign_updates_sidecar_in_place(tmp_path):
    sign_entry(tmp_path, _entry("2026-W27"), signer=_fake_signer, fingerprint="FP", when="t1")
    edited = _entry("2026-W27", body=b"# Title\n\nedited")
    sign_entry(tmp_path, edited, signer=_fake_signer, fingerprint="FP", when="t2")

    records = plog.load_log(tmp_path)
    assert len(records) == 1  # same slug, updated
    assert records[0].sha256 == edited.sha256
    assert (tmp_path / "entries" / "2026-W27.md.sig").read_text() == _fake_signer(edited.data)
