"""End-to-end provenance: sign → attach → anchor (file backend) → verify, wiring
the same pieces the CLI does but with a fake signer/verifier (no gpg, no chain)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from pipeline.config import Config, OutputConfig, ReposConfig, StateConfig
from pipeline.provenance import log as plog
from pipeline.provenance.anchor import get_anchor_backend, receipt_filename
from pipeline.provenance.content import PublishedEntry
from pipeline.provenance.log import Anchor
from pipeline.provenance.sign import sign_entry
from pipeline.provenance.verify import verify_all
from pipeline.site_adapter import RenderContext, get_adapter
from pipeline.site_adapter.sporny_pl import SpornyPlAdapter


def _fake_signer(data: bytes) -> str:
    return "SIG:" + hashlib.sha256(data).hexdigest()


def _fake_verifier(data: bytes, sig: str) -> bool:
    return sig == _fake_signer(data)


def _apply(changes):
    for change in changes:
        change.path.write_text(change.content)


def _config(root) -> Config:
    return Config(
        github_user="rsporny",
        repos=ReposConfig(allowlist=["o/r"]),
        state=StateConfig(root=str(root)),
        output=OutputConfig(site_repo_path=str(root), site_devlog_dir="content/devlog"),
    )


def test_sign_anchor_verify_end_to_end(tmp_path):
    cfg = _config(tmp_path)
    site_dir = tmp_path / "content" / "devlog"
    site_dir.mkdir(parents=True)
    prov_dir = tmp_path / "provenance"
    prov_dir.mkdir()
    (prov_dir / "pubkey.asc").write_text("PUBKEY")

    # A published entry on the site.
    md = site_dir / "2026-W30.md"
    md.write_text(
        "---\ntype: weekly-activity\nseries: Senior SDET log\nslug: 2026-W30\n"
        "title: exit codes\npublished_at: '2026-07-22'\nstatus: published\n---\n\n"
        "# exit codes\n\nThis week I wired provenance.\n"
    )
    before = md.read_bytes()

    adapter: SpornyPlAdapter = get_adapter(cfg.output.adapter)
    ctx = RenderContext(site_dir=site_dir, config=cfg)

    # 1. sign
    entry = PublishedEntry.from_path(md)
    proof = sign_entry(prov_dir, entry, signer=_fake_signer, fingerprint="FP15AC")
    _apply(adapter.attach_provenance("2026-W30", proof, ctx))

    # the .md is byte-identical; the served sidecar + key + badge exist
    assert md.read_bytes() == before
    assert (site_dir / "2026-W30.md.sig").read_text() == _fake_signer(before)
    assert (site_dir / "pubkey.asc").read_text() == "PUBKEY"

    # 2. anchor (file backend), then reflect it in the manifest badge
    be = get_anchor_backend("file", anchors_dir=prov_dir / "anchors")
    receipt = be.anchor(proof.sha256, network="local", slug="2026-W30")
    (prov_dir / "anchors").mkdir(exist_ok=True)
    (prov_dir / "anchors" / receipt_filename(receipt.tx_id)).write_text(json.dumps(asdict(receipt)))
    anchor = Anchor(**asdict(receipt))
    plog.set_anchor(prov_dir, "2026-W30", anchor)
    _apply(adapter.attach_anchor("2026-W30", anchor, ctx))

    entry_json = json.loads((site_dir / "index.json").read_text())[0]
    assert entry_json["signed"] is True
    assert entry_json["sha256"] == proof.sha256
    assert entry_json["signature"] == "2026-W30.md.sig"
    assert entry_json["anchor"]["tx_id"] == receipt.tx_id

    # 3. verify everything, including the anchor read back from the file backend
    report = verify_all(
        prov_dir,
        site_dir,
        verifier=_fake_verifier,
        anchor_fetch=lambda a: be.fetch(a.tx_id, network=a.network),
    )
    assert report.ok
    assert report.leaves[0].content_ok and report.leaves[0].signature_ok
    assert report.anchors[0].ok
