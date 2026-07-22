"""End-to-end verification of the transparency ledger.

For every entry, recompute the ``sha256`` from the *actual* published
``<slug>.md`` (so a post-hoc edit is caught), verify its detached signature
against the committed public key, and — with ``--chain`` — read the anchored hash
back from its backend and confirm it matches. Every entry is an independent
proof; there is no cumulative root to check. All the impure bits — reading
entries, the verifier, the chain fetch — are injectable, so the whole thing is
testable offline with no gpg and no network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import log as plog
from .content import sha256_hex
from .log import Anchor
from .sign import Verifier

# slug -> the raw published markdown bytes (or None if the entry is missing).
EntryReader = Callable[[str], bytes | None]
# an anchor -> the hex sha256 actually recorded on-chain (or None if unreadable).
AnchorFetch = Callable[[Anchor], str | None]


@dataclass
class LeafCheck:
    slug: str
    content_ok: bool
    signature_ok: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.content_ok and self.signature_ok


@dataclass
class AnchorCheck:
    slug: str
    tx_id: str
    ok: bool
    detail: str = ""


@dataclass
class VerifyReport:
    leaves: list[LeafCheck] = field(default_factory=list)
    anchors: list[AnchorCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.leaves) and all(a.ok for a in self.anchors)


def _site_reader(site_dir: Path) -> EntryReader:
    def _read(slug: str) -> bytes | None:
        path = site_dir / f"{slug}.md"
        return path.read_bytes() if path.exists() else None

    return _read


def verify_all(
    prov_dir: Path | str,
    site_dir: Path | str,
    *,
    verifier: Verifier,
    read_entry: EntryReader | None = None,
    anchor_fetch: AnchorFetch | None = None,
) -> VerifyReport:
    prov_dir = Path(prov_dir)
    read_entry = read_entry or _site_reader(Path(site_dir))
    records = plog.load_log(prov_dir)
    report = VerifyReport()

    for rec in records:
        data = read_entry(rec.slug)
        if data is None:
            report.leaves.append(LeafCheck(rec.slug, False, False, "published entry not found"))
            continue
        content_ok = sha256_hex(data) == rec.sha256
        detail = "" if content_ok else "file hash differs from the ledger (edited?)"

        sig_path = prov_dir / rec.sig
        signature_ok = False
        if not sig_path.exists():
            detail = (detail + "; " if detail else "") + f"signature missing: {rec.sig}"
        else:
            signature_ok = verifier(data, sig_path.read_text())
            if not signature_ok:
                detail = (detail + "; " if detail else "") + "signature does not verify"
        report.leaves.append(LeafCheck(rec.slug, content_ok, signature_ok, detail))

        if anchor_fetch is not None and rec.anchor is not None:
            onchain = anchor_fetch(rec.anchor)
            if onchain is None:
                report.anchors.append(
                    AnchorCheck(rec.slug, rec.anchor.tx_id, False, "anchor not readable")
                )
            elif onchain == rec.sha256:
                report.anchors.append(AnchorCheck(rec.slug, rec.anchor.tx_id, True))
            else:
                report.anchors.append(
                    AnchorCheck(rec.slug, rec.anchor.tx_id, False, "on-chain hash differs")
                )

    return report


def render(report: VerifyReport) -> str:
    """A human-readable verification report."""
    lines = ["# Provenance verification", ""]
    for c in report.leaves:
        mark = "✓" if c.ok else "✗"
        extra = f" — {c.detail}" if c.detail else ""
        lines.append(
            f"- {mark} {c.slug} (content {'ok' if c.content_ok else 'BAD'}, "
            f"signature {'ok' if c.signature_ok else 'BAD'}){extra}"
        )
    for a in report.anchors:
        mark = "✓" if a.ok else "✗"
        extra = f" — {a.detail}" if a.detail else ""
        lines.append(f"- {mark} anchor {a.slug}: {a.tx_id}{extra}")
    lines.append("")
    lines.append(f"Result: {'PASS' if report.ok else 'FAIL'}")
    return "\n".join(lines) + "\n"
