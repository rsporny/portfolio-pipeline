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

ROOT = bytes.fromhex("ab" * 32)


def test_unknown_backend_raises():
    with pytest.raises(AnchorError):
        get_anchor_backend("dogecoin")


def test_null_backend_anchors_nothing():
    be = get_anchor_backend("null")
    with pytest.raises(AnchorError):
        be.anchor(ROOT, network="-", tree_size=1)
    assert be.fetch("whatever", network="-") is None


def test_file_backend_round_trips(tmp_path):
    be = get_anchor_backend("file", anchors_dir=tmp_path)
    receipt = be.anchor(ROOT, network="local", tree_size=3)
    assert receipt.backend == "file"
    assert receipt.root == ROOT.hex()
    # The CLI persists the receipt; simulate that, then read it back.
    (tmp_path / receipt_filename(receipt.tx_id)).write_text(json.dumps(asdict(receipt)))
    assert be.fetch(receipt.tx_id, network="local") == ROOT
    assert be.fetch("file:missing", network="local") is None


def test_file_backend_requires_dir():
    with pytest.raises(AnchorError):
        get_anchor_backend("file")


def test_cardano_backend_is_registered():
    assert isinstance(get_anchor_backend("cardano"), CardanoAnchorBackend)


def test_cardano_anchor_without_env_errors(monkeypatch):
    monkeypatch.delenv("BLOCKFROST_PROJECT_ID", raising=False)
    with pytest.raises(AnchorError):
        get_anchor_backend("cardano").anchor(ROOT, network="preview", tree_size=1)


def test_cardano_unsupported_network_errors(monkeypatch):
    monkeypatch.setenv("BLOCKFROST_PROJECT_ID", "preview_xxx")
    with pytest.raises(AnchorError):
        get_anchor_backend("cardano").anchor(ROOT, network="mainnet", tree_size=1)


def test_cardano_fetch_reads_metadata(monkeypatch, httpx_mock):
    monkeypatch.setenv("BLOCKFROST_PROJECT_ID", "preview_xxx")
    be = CardanoAnchorBackend(metadata_label=8272025)
    httpx_mock.add_response(
        url="https://cardano-preview.blockfrost.io/api/v0/txs/deadtx/metadata",
        json=[{"label": "8272025", "json_metadata": {"root": ROOT.hex(), "n": 1, "v": 1}}],
    )
    assert be.fetch("deadtx", network="preview") == ROOT


def test_cardano_fetch_missing_label_returns_none(monkeypatch, httpx_mock):
    monkeypatch.setenv("BLOCKFROST_PROJECT_ID", "preview_xxx")
    be = CardanoAnchorBackend(metadata_label=8272025)
    httpx_mock.add_response(
        url="https://cardano-preview.blockfrost.io/api/v0/txs/deadtx/metadata",
        json=[{"label": "999", "json_metadata": {"foo": "bar"}}],
    )
    assert be.fetch("deadtx", network="preview") is None
