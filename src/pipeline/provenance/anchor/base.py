"""The anchor backend interface: timestamp the current merkle root somewhere
durable, and read it back for verification. Backends are pluggable and selected
by ``provenance.anchor.backend`` — mirroring the site-adapter boundary."""

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
    """The outcome of anchoring one root. Maps 1:1 to a ``log.Anchor`` record."""

    backend: str
    network: str
    tx_id: str
    root: str  # hex of the anchored merkle root
    tree_size: int
    anchored_at: str


@runtime_checkable
class AnchorBackend(Protocol):
    name: str

    def anchor(self, root: bytes, *, network: str, tree_size: int) -> AnchorReceipt:
        """Timestamp ``root`` and return a receipt (with the resulting tx id)."""
        ...

    def fetch(self, tx_id: str, *, network: str) -> bytes | None:
        """Read the root previously anchored under ``tx_id`` back, or ``None`` if
        it can't be read. Used by ``verify --chain``."""
        ...
