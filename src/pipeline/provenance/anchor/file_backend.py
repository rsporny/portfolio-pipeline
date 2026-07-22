"""A local, no-chain backend: the anchor "exists" as the receipt file the CLI
writes under ``provenance/anchors/``. Useful for dev and for exercising the full
sign → anchor → verify --chain flow offline (its ``fetch`` reads that receipt
back). Not a real timestamp — just a self-consistent stand-in for a chain."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .base import AnchorReceipt, receipt_filename


class FileAnchorBackend:
    name = "file"

    def __init__(self, anchors_dir: Path | str) -> None:
        self.anchors_dir = Path(anchors_dir)

    def anchor(self, sha256: str, *, network: str, slug: str) -> AnchorReceipt:
        return AnchorReceipt(
            backend="file",
            network=network or "local",
            tx_id=f"file:{sha256[:16]}",
            sha256=sha256,
            anchored_at=datetime.now(UTC).isoformat(),
        )

    def fetch(self, tx_id: str, *, network: str) -> str | None:
        path = self.anchors_dir / receipt_filename(tx_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        sha = data.get("sha256")
        return str(sha) if sha else None
