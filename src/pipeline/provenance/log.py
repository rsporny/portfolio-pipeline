"""The transparency ledger: an append-only record of published, signed entries,
living under ``state.root/provenance/log.jsonl``.

Each :class:`LedgerRecord` is one entry — its ``sha256`` (the hash of the raw
``<slug>.md``), the detached-signature path, and, once anchored, the per-entry
:class:`Anchor` receipt. There is no merkle tree and no cumulative root: every
entry is an *independent* proof, verifiable on its own by hashing one file and
reading one transaction. Records are keyed by ``slug`` — re-signing an edited
entry updates it in place (and drops a now-stale anchor).

Code owns this file; nothing is trusted from the model. Verification always
recomputes the hash from the actual published entry (see :mod:`verify`); the
stored ``sha256`` is a convenience, not the source of truth.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from .content import PublishedEntry

LOG_NAME = "log.jsonl"


class Anchor(BaseModel):
    backend: str
    network: str
    tx_id: str
    sha256: str  # the per-entry hash this transaction pins
    anchored_at: str


class LedgerRecord(BaseModel):
    slug: str
    published_at: str
    sha256: str  # hex sha256 of the raw <slug>.md
    sig: str  # detached-signature path, relative to the provenance dir
    signed_at: str
    anchor: Anchor | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _order(records: list[LedgerRecord]) -> list[LedgerRecord]:
    return sorted(records, key=lambda r: (r.published_at, r.slug))


def load_log(prov_dir: Path | str) -> list[LedgerRecord]:
    path = Path(prov_dir) / LOG_NAME
    if not path.exists():
        return []
    records = [
        LedgerRecord.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    return _order(records)


def save_log(prov_dir: Path | str, records: list[LedgerRecord]) -> Path:
    directory = Path(prov_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOG_NAME
    path.write_text("".join(r.model_dump_json() + "\n" for r in _order(records)))
    return path


def record_entry(
    prov_dir: Path | str,
    entry: PublishedEntry,
    *,
    sig: str,
    when: str | None = None,
) -> LedgerRecord:
    """Append (or, by slug, update) ``entry``'s record and persist the log. If the
    content hash changed since a prior signing, any anchor is dropped — it pinned
    the old bytes and no longer applies until the entry is re-anchored."""
    when = when or _now()
    records = load_log(prov_dir)
    by_slug = {r.slug: r for r in records}

    if entry.slug in by_slug:
        rec = by_slug[entry.slug]
        if rec.sha256 != entry.sha256:
            rec.anchor = None  # stale — pinned the pre-edit bytes
        rec.published_at = entry.published_at
        rec.sha256 = entry.sha256
        rec.sig = sig
        rec.signed_at = when
    else:
        rec = LedgerRecord(
            slug=entry.slug,
            published_at=entry.published_at,
            sha256=entry.sha256,
            sig=sig,
            signed_at=when,
        )
        records.append(rec)

    save_log(prov_dir, records)
    return rec


def set_anchor(prov_dir: Path | str, slug: str, anchor: Anchor) -> LedgerRecord:
    """Attach ``anchor`` to ``slug``'s record and persist the log."""
    records = load_log(prov_dir)
    by_slug = {r.slug: r for r in records}
    if slug not in by_slug:
        raise KeyError(f"no signed entry {slug!r} to anchor — sign it first")
    rec = by_slug[slug]
    rec.anchor = anchor
    save_log(prov_dir, records)
    return rec
