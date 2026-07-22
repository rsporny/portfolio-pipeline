"""Anchor backend registry — resolve the backend named in
``provenance.anchor.backend`` (mirrors ``site_adapter.get_adapter``)."""

from __future__ import annotations

from pathlib import Path

from .base import AnchorBackend, AnchorError, AnchorReceipt, receipt_filename
from .cardano_backend import CardanoAnchorBackend
from .file_backend import FileAnchorBackend
from .null_backend import NullAnchorBackend

DEFAULT_METADATA_LABEL = 8272025


def get_anchor_backend(
    name: str,
    *,
    anchors_dir: Path | str | None = None,
    metadata_label: int = DEFAULT_METADATA_LABEL,
) -> AnchorBackend:
    if name == "null":
        return NullAnchorBackend()
    if name == "file":
        if anchors_dir is None:
            raise AnchorError("the 'file' anchor backend needs an anchors_dir")
        return FileAnchorBackend(anchors_dir)
    if name == "cardano":
        return CardanoAnchorBackend(metadata_label)
    raise AnchorError(f"unknown anchor backend {name!r}; known: null, file, cardano")


__all__ = [
    "AnchorBackend",
    "AnchorError",
    "AnchorReceipt",
    "CardanoAnchorBackend",
    "FileAnchorBackend",
    "NullAnchorBackend",
    "DEFAULT_METADATA_LABEL",
    "get_anchor_backend",
    "receipt_filename",
]
