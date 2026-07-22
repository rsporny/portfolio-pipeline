"""The default backend: anchors nothing. Keeps the whole pipeline offline until
the owner opts into a real chain, and makes ``provenance anchor`` a clear no-op
rather than a silent one."""

from __future__ import annotations

from .base import AnchorError, AnchorReceipt


class NullAnchorBackend:
    name = "null"

    def anchor(self, root: bytes, *, network: str, tree_size: int) -> AnchorReceipt:
        raise AnchorError(
            "anchor backend is 'null' — nothing to anchor. Set provenance.anchor.backend "
            "to 'file' (local) or 'cardano' (testnet)."
        )

    def fetch(self, tx_id: str, *, network: str) -> bytes | None:
        return None
