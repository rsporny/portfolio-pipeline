"""The transparency log: an append-only record of published entries plus the
current merkle root, both living under ``state.root/provenance/``.

- ``log.jsonl`` — one :class:`LeafRecord` per published entry, ordered by
  ``leaf_index``. Appends are idempotent by ``slug``: re-signing an edited entry
  updates its record in place and keeps its leaf index.
- ``root.json`` — the current :class:`RootFile`: the merkle root over every
  leaf, the tree size, and the history of on-chain anchors.

Code owns these files; nothing is trusted from the model. Verification always
recomputes leaf hashes from the actual published entries (see :mod:`verify`) —
the stored ``leaf_sha256`` is a convenience for rebuilding the root, not the
source of truth.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from . import merkle
from .canonical import CanonicalEntry

LOG_NAME = "log.jsonl"
ROOT_NAME = "root.json"


class LeafRecord(BaseModel):
    leaf_index: int
    slug: str
    published_at: str
    leaf_sha256: str  # hex of the domain-separated leaf hash (a merkle leaf)
    sig: str  # detached-signature path, relative to the provenance dir
    signed_at: str


class Anchor(BaseModel):
    backend: str
    network: str
    tx_id: str
    root: str  # the hex merkle root this anchor pins
    tree_size: int
    anchored_at: str


class RootFile(BaseModel):
    root: str
    tree_size: int
    updated_at: str
    anchors: list[Anchor] = Field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def load_log(prov_dir: Path | str) -> list[LeafRecord]:
    path = Path(prov_dir) / LOG_NAME
    if not path.exists():
        return []
    records = [
        LeafRecord.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    return sorted(records, key=lambda r: r.leaf_index)


def save_log(prov_dir: Path | str, records: list[LeafRecord]) -> Path:
    directory = Path(prov_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOG_NAME
    ordered = sorted(records, key=lambda r: r.leaf_index)
    path.write_text("".join(r.model_dump_json() + "\n" for r in ordered))
    return path


def load_root(prov_dir: Path | str) -> RootFile | None:
    path = Path(prov_dir) / ROOT_NAME
    if not path.exists():
        return None
    return RootFile.model_validate_json(path.read_text())


def save_root(prov_dir: Path | str, root: RootFile) -> Path:
    directory = Path(prov_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ROOT_NAME
    path.write_text(root.model_dump_json(indent=2) + "\n")
    return path


def root_bytes(records: list[LeafRecord]) -> bytes:
    """The merkle root over every leaf hash, in leaf-index order."""
    leaves = [bytes.fromhex(r.leaf_sha256) for r in sorted(records, key=lambda r: r.leaf_index)]
    return merkle.build_root(leaves)


def record_entry(
    prov_dir: Path | str,
    entry: CanonicalEntry,
    *,
    sig: str,
    when: str | None = None,
) -> tuple[LeafRecord, RootFile]:
    """Append (or, by slug, update) ``entry``'s leaf, recompute the root, and
    persist both files. Anchors already in ``root.json`` are preserved — they pin
    earlier roots at earlier tree sizes and remain historically valid."""
    when = when or _now()
    records = load_log(prov_dir)
    by_slug = {r.slug: r for r in records}
    leaf_hex = entry.leaf_hex()

    if entry.slug in by_slug:
        rec = by_slug[entry.slug]
        rec.published_at = entry.published_at
        rec.leaf_sha256 = leaf_hex
        rec.sig = sig
        rec.signed_at = when
    else:
        rec = LeafRecord(
            leaf_index=len(records),
            slug=entry.slug,
            published_at=entry.published_at,
            leaf_sha256=leaf_hex,
            sig=sig,
            signed_at=when,
        )
        records.append(rec)

    prior = load_root(prov_dir)
    root = RootFile(
        root=root_bytes(records).hex(),
        tree_size=len(records),
        updated_at=when,
        anchors=prior.anchors if prior else [],
    )
    save_log(prov_dir, records)
    save_root(prov_dir, root)
    return rec, root


def add_anchor(prov_dir: Path | str, anchor: Anchor, *, when: str | None = None) -> RootFile:
    """Append an anchor receipt to ``root.json`` (leaving the current root/size)."""
    root = load_root(prov_dir)
    if root is None:
        raise FileNotFoundError(f"no {ROOT_NAME} to anchor — sign an entry first")
    root.anchors.append(anchor)
    root.updated_at = when or _now()
    save_root(prov_dir, root)
    return root
