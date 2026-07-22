from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from pipeline.provenance.anchor import (
    CardanoAnchorBackend,
    get_anchor_backend,
    receipt_filename,
)
from pipeline.provenance.anchor.base import AnchorError

SHA = "ab" * 32


def test_unknown_backend_raises():
    with pytest.raises(AnchorError):
        get_anchor_backend("dogecoin")


def test_null_backend_anchors_nothing():
    be = get_anchor_backend("null")
    with pytest.raises(AnchorError):
        be.anchor(SHA, network="-", slug="2026-W27")
    assert be.fetch("whatever", network="-") is None


def test_file_backend_round_trips(tmp_path):
    be = get_anchor_backend("file", anchors_dir=tmp_path)
    receipt = be.anchor(SHA, network="local", slug="2026-W27")
    assert receipt.backend == "file"
    assert receipt.sha256 == SHA
    # The CLI persists the receipt; simulate that, then read it back.
    (tmp_path / receipt_filename(receipt.tx_id)).write_text(json.dumps(asdict(receipt)))
    assert be.fetch(receipt.tx_id, network="local") == SHA
    assert be.fetch("file:missing", network="local") is None


def test_file_backend_requires_dir():
    with pytest.raises(AnchorError):
        get_anchor_backend("file")


def test_cardano_backend_is_registered():
    assert isinstance(get_anchor_backend("cardano"), CardanoAnchorBackend)


def test_cardano_anchor_without_env_errors(monkeypatch):
    monkeypatch.delenv("BLOCKFROST_PROJECT_ID", raising=False)
    with pytest.raises(AnchorError):
        get_anchor_backend("cardano").anchor(SHA, network="preview", slug="2026-W27")


def test_cardano_unsupported_network_errors(monkeypatch):
    monkeypatch.setenv("BLOCKFROST_PROJECT_ID", "preview_xxx")
    with pytest.raises(AnchorError):
        get_anchor_backend("cardano").anchor(SHA, network="mainnet", slug="2026-W27")


def test_cardano_fetch_reads_metadata(monkeypatch, httpx_mock):
    monkeypatch.setenv("BLOCKFROST_PROJECT_ID", "preview_xxx")
    be = CardanoAnchorBackend(metadata_label=8272025)
    httpx_mock.add_response(
        url="https://cardano-preview.blockfrost.io/api/v0/txs/deadtx/metadata",
        json=[{"label": "8272025", "json_metadata": {"slug": "2026-W27", "sha256": SHA, "v": 1}}],
    )
    assert be.fetch("deadtx", network="preview") == SHA


def test_cardano_fetch_missing_label_returns_none(monkeypatch, httpx_mock):
    monkeypatch.setenv("BLOCKFROST_PROJECT_ID", "preview_xxx")
    be = CardanoAnchorBackend(metadata_label=8272025)
    httpx_mock.add_response(
        url="https://cardano-preview.blockfrost.io/api/v0/txs/deadtx/metadata",
        json=[{"label": "999", "json_metadata": {"foo": "bar"}}],
    )
    assert be.fetch("deadtx", network="preview") is None
