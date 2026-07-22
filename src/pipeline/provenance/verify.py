"""End-to-end verification of the transparency log.

For every leaf, recompute the canonical hash from the *actual* published entry
(so a post-hoc edit is caught), verify its detached signature against the
committed public key, then recompute the merkle root and compare it to
``root.json``. With ``--chain``, also read each anchor back from its backend and
confirm the on-chain root matches. All the impure bits — reading entries, the
verifier, the chain fetch — are injectable, so the whole thing is testable
offline with no gpg and no network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import log as plog
from .canonical import CanonicalEntry
from .log import Anchor
from .sign import Verifier

# slug -> published markdown text (or None if the entry is missing).
EntryReader = Callable[[str], str | None]
# an anchor -> the root bytes actually recorded on-chain (or None if unreadable).
AnchorFetch = Callable[[Anchor], bytes | None]


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
    tx_id: str
    ok: bool
    detail: str = ""


@dataclass
class VerifyReport:
    leaves: list[LeafCheck] = field(default_factory=list)
    root_ok: bool = True
    root_detail: str = ""
    anchors: list[AnchorCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.root_ok
            and all(c.ok for c in self.leaves)
            and all(a.ok for a in self.anchors)
        )


def _site_reader(site_dir: Path) -> EntryReader:
    def _read(slug: str) -> str | None:
        path = site_dir / f"{slug}.md"
        return path.read_text() if path.exists() else None

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
        text = read_entry(rec.slug)
        if text is None:
            report.leaves.append(LeafCheck(rec.slug, False, False, "published entry not found"))
            continue
        entry = CanonicalEntry.from_markdown(text)
        content_ok = entry.leaf_hex() == rec.leaf_sha256
        detail = "" if content_ok else "content hash differs from the logged leaf (edited?)"

        sig_path = prov_dir / rec.sig
        signature_ok = False
        if not sig_path.exists():
            detail = (detail + "; " if detail else "") + f"signature missing: {rec.sig}"
        else:
            signature_ok = verifier(entry.to_bytes(), sig_path.read_text())
            if not signature_ok:
                detail = (detail + "; " if detail else "") + "signature does not verify"
        report.leaves.append(LeafCheck(rec.slug, content_ok, signature_ok, detail))

    root_file = plog.load_root(prov_dir)
    recomputed = plog.root_bytes(records).hex()
    if root_file is None:
        report.root_ok = not records
        report.root_detail = "" if not records else "root.json missing"
    else:
        report.root_ok = root_file.root == recomputed and root_file.tree_size == len(records)
        report.root_detail = "" if report.root_ok else "root.json does not match the log"

        if anchor_fetch is not None:
            for anchor in root_file.anchors:
                onchain = anchor_fetch(anchor)
                if onchain is None:
                    report.anchors.append(AnchorCheck(anchor.tx_id, False, "anchor not readable"))
                elif onchain.hex() == anchor.root:
                    report.anchors.append(AnchorCheck(anchor.tx_id, True))
                else:
                    report.anchors.append(
                        AnchorCheck(anchor.tx_id, False, "on-chain root differs")
                    )

    return report


def render(report: VerifyReport) -> str:
    """A human-readable verification report."""
    lines = ["# Provenance verification", ""]
    for c in report.leaves:
        mark = "✓" if c.ok else "✗"
        extra = f" — {c.detail}" if c.detail else ""
        lines.append(f"- {mark} {c.slug} (content {'ok' if c.content_ok else 'BAD'}, "
                     f"signature {'ok' if c.signature_ok else 'BAD'}){extra}")
    root_mark = "✓" if report.root_ok else "✗"
    lines.append(f"- {root_mark} merkle root{'' if report.root_ok else ' — ' + report.root_detail}")
    for a in report.anchors:
        mark = "✓" if a.ok else "✗"
        extra = f" — {a.detail}" if a.detail else ""
        lines.append(f"- {mark} anchor {a.tx_id}{extra}")
    lines.append("")
    lines.append(f"Result: {'PASS' if report.ok else 'FAIL'}")
    return "\n".join(lines) + "\n"
