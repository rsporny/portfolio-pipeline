"""Anchor the merkle root in a Cardano **testnet** transaction's metadata.

Opt-in and isolated: ``pycardano`` is an optional extra imported lazily (only
here), and the two secrets are env-only (never config/logs):

- ``BLOCKFROST_PROJECT_ID`` — a Blockfrost project id for the chosen testnet.
- ``CARDANO_SIGNING_KEY`` — path to a payment signing key (``.skey``) funded with
  test ADA to pay the tiny fee.

``anchor`` builds/submits the tx via ``pycardano``; ``fetch`` reads the metadata
back over Blockfrost's HTTP API (``httpx``), so verification needs only the
project id. Testnet only — mainnet is out of scope for v0.5.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx

from .base import AnchorError, AnchorReceipt

_TESTNETS = ("preview", "preprod")


def _host(network: str) -> str:
    if network not in _TESTNETS:
        raise AnchorError(
            f"unsupported Cardano network {network!r}; use 'preview' or 'preprod' (testnet only)"
        )
    return f"https://cardano-{network}.blockfrost.io"


def _context_base(network: str) -> str:
    # BlockFrostChainContext / the blockfrost SDK append the API version, so the
    # base must stop at ``/api`` (matching blockfrost.ApiUrls). Passing ``/api/v0``
    # doubles the version → "Invalid path" 400.
    return f"{_host(network)}/api"


def _http_base(network: str) -> str:
    # Our own read-back calls the REST API directly, so it needs the version.
    return f"{_host(network)}/api/v0"


def _project_id() -> str:
    pid = os.environ.get("BLOCKFROST_PROJECT_ID")
    if not pid:
        raise AnchorError("BLOCKFROST_PROJECT_ID is not set")
    return pid


class CardanoAnchorBackend:
    name = "cardano"

    def __init__(self, metadata_label: int) -> None:
        self.metadata_label = metadata_label

    def anchor(self, root: bytes, *, network: str, tree_size: int) -> AnchorReceipt:
        context_base = _context_base(network)
        project_id = _project_id()
        skey_path = os.environ.get("CARDANO_SIGNING_KEY")
        if not skey_path:
            raise AnchorError("CARDANO_SIGNING_KEY is not set (path to a funded testnet .skey)")

        try:
            from pycardano import (
                Address,
                AuxiliaryData,
                BlockFrostChainContext,
                Metadata,
                Network,
                PaymentSigningKey,
                PaymentVerificationKey,
                TransactionBuilder,
                TransactionOutput,
            )
        except ModuleNotFoundError as exc:  # optional extra
            raise AnchorError(
                "the 'cardano' extra is not installed — run `uv sync --extra cardano`"
            ) from exc

        try:
            context = BlockFrostChainContext(project_id, base_url=context_base)
            skey = PaymentSigningKey.load(skey_path)
            vkey = PaymentVerificationKey.from_signing_key(skey)
            address = Address(vkey.hash(), network=Network.TESTNET)

            metadata = Metadata({self.metadata_label: {"root": root.hex(), "n": tree_size, "v": 1}})
            builder = TransactionBuilder(context)
            builder.add_input_address(address)
            builder.auxiliary_data = AuxiliaryData(metadata)
            # A minimal self-payment carries the metadata; change returns to us.
            builder.add_output(TransactionOutput(address, 1_000_000))
            signed = builder.build_and_sign([skey], change_address=address)
            context.submit_tx(signed.to_cbor())
            tx_id = str(signed.id)
        except AnchorError:
            raise
        except Exception as exc:  # pycardano / network errors
            raise AnchorError(f"Cardano anchoring failed: {exc}") from exc

        return AnchorReceipt(
            backend="cardano",
            network=network,
            tx_id=tx_id,
            root=root.hex(),
            tree_size=tree_size,
            anchored_at=datetime.now(UTC).isoformat(),
        )

    def fetch(self, tx_id: str, *, network: str) -> bytes | None:
        base = _http_base(network)
        try:
            resp = httpx.get(
                f"{base}/txs/{tx_id}/metadata",
                headers={"project_id": _project_id()},
                timeout=30,
            )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        for item in resp.json():
            if str(item.get("label")) == str(self.metadata_label):
                root = (item.get("json_metadata") or {}).get("root")
                if root:
                    try:
                        return bytes.fromhex(root)
                    except ValueError:
                        return None
        return None
