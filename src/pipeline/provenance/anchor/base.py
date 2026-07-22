"""The anchor backend interface: timestamp one entry's ``sha256`` somewhere
durable, and read it back for verification. Backends are pluggable and selected
by ``provenance.anchor.backend`` — mirroring the site-adapter boundary.

Each anchor is per-entry and independent — there is no cumulative root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class AnchorError(RuntimeError):
    """Raised when anchoring can't proceed (bad backend, missing env, …)."""


def receipt_filename(tx_id: str) -> str:
    """A filesystem-safe name for an anchor receipt (``provenance/anchors/…``)."""
    return tx_id.replace(":", "_").replace("/", "_") + ".json"


@dataclass(frozen=True)
class AnchorReceipt:
    """The outcome of anchoring one entry. Maps 1:1 to a ``log.Anchor`` record."""

    backend: str
    network: str
    tx_id: str
    sha256: str  # hex of the anchored entry hash
    anchored_at: str


@runtime_checkable
class AnchorBackend(Protocol):
    name: str

    def anchor(self, sha256: str, *, network: str, slug: str) -> AnchorReceipt:
        """Timestamp ``sha256`` (the entry ``slug``'s hash) and return a receipt
        (with the resulting tx id)."""
        ...

    def fetch(self, tx_id: str, *, network: str) -> str | None:
        """Read the hex ``sha256`` previously anchored under ``tx_id`` back, or
        ``None`` if it can't be read. Used by ``verify --chain``."""
        ...
