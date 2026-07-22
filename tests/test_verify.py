from __future__ import annotations

import hashlib
from pathlib import Path

from pipeline.frontmatter import dump
from pipeline.provenance import log as plog
from pipeline.provenance.content import PublishedEntry
from pipeline.provenance.log import Anchor
from pipeline.provenance.sign import sign_entry
from pipeline.provenance.verify import verify_all


def _fake_signer(data: bytes) -> str:
    return "SIG:" + hashlib.sha256(data).hexdigest()


def _fake_verifier(data: bytes, sig: str) -> bool:
    return sig == _fake_signer(data)


def _write_entry(site_dir: Path, slug: str, *, title="Title", body=None) -> PublishedEntry:
    body = body or f"# {title}\n\nbody for {slug}\n"
    front = {
        "type": "weekly-activity",
        "series": "Senior SDET log",
        "slug": slug,
        "title": title,
        "published_at": "2026-07-06",
        "status": "published",
    }
    (site_dir / f"{slug}.md").write_text(dump(front, body))
    return PublishedEntry.from_path(site_dir / f"{slug}.md")


def _sign_two(tmp_path) -> tuple[Path, Path]:
    site, prov = tmp_path / "site", tmp_path / "prov"
    site.mkdir()
    for slug in ("2026-W27", "2026-W28"):
        entry = _write_entry(site, slug)
        sign_entry(prov, entry, signer=_fake_signer, fingerprint="FP")
    return site, prov


def test_clean_ledger_verifies(tmp_path):
    site, prov = _sign_two(tmp_path)
    report = verify_all(prov, site, verifier=_fake_verifier)
    assert report.ok
    assert [c.slug for c in report.leaves] == ["2026-W27", "2026-W28"]
    assert all(c.content_ok and c.signature_ok for c in report.leaves)


def test_edited_entry_fails_content(tmp_path):
    site, prov = _sign_two(tmp_path)
    # Tamper with a published entry AFTER signing.
    _write_entry(site, "2026-W27", body="# Title\n\nsecretly edited\n")
    report = verify_all(prov, site, verifier=_fake_verifier)
    assert not report.ok
    bad = next(c for c in report.leaves if c.slug == "2026-W27")
    assert not bad.content_ok


def test_tampered_signature_fails(tmp_path):
    site, prov = _sign_two(tmp_path)
    (prov / "entries" / "2026-W28.md.sig").write_text("SIG:deadbeef")
    report = verify_all(prov, site, verifier=_fake_verifier)
    assert not report.ok
    bad = next(c for c in report.leaves if c.slug == "2026-W28")
    assert bad.content_ok and not bad.signature_ok


def test_missing_entry_fails(tmp_path):
    site, prov = _sign_two(tmp_path)
    (site / "2026-W27.md").unlink()
    report = verify_all(prov, site, verifier=_fake_verifier)
    assert not report.ok
    bad = next(c for c in report.leaves if c.slug == "2026-W27")
    assert not bad.content_ok and not bad.signature_ok


def test_chain_check_matches_and_mismatches(tmp_path):
    site, prov = _sign_two(tmp_path)
    rec = plog.load_log(prov)[0]
    plog.set_anchor(
        prov,
        rec.slug,
        Anchor(
            backend="cardano",
            network="preview",
            tx_id="tx-good",
            sha256=rec.sha256,
            anchored_at="t",
        ),
    )

    good = verify_all(prov, site, verifier=_fake_verifier, anchor_fetch=lambda a: a.sha256)
    assert good.ok and good.anchors[0].ok and good.anchors[0].slug == rec.slug

    bad = verify_all(prov, site, verifier=_fake_verifier, anchor_fetch=lambda a: "00" * 32)
    assert not bad.ok and not bad.anchors[0].ok
