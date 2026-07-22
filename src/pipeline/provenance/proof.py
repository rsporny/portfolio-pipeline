"""The neutral proof value produced by signing and consumed by the site adapter.

Defined here (in provenance, the core) rather than in the adapter, so the adapter
is a plugin that depends on the core — never the other way round."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntryProof:
    """Everything a site adapter needs to render provenance for one entry."""

    slug: str
    sha256: str  # hex sha256 of the raw <slug>.md file
    signature: str  # ASCII-armored detached GPG signature over those file bytes
    sig_filename: str  # e.g. "2026-W27.md.sig" (the sidecar name in the site dir)
    pubkey_fingerprint: str  # who signed — for the verify badge
