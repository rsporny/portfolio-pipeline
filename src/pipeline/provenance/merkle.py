"""A binary merkle tree over leaf hashes, following RFC 6962 (Certificate
Transparency): a recognizable, well-specified construction that handles odd leaf
counts without the duplicate-leaf second-preimage weakness of Bitcoin-style trees.

Leaves passed in are the per-entry content commitments from :mod:`canonical`
(``d``). The tree hashes them with domain separation — leaf nodes prefixed with
``0x00``, interior nodes with ``0x01`` — so a leaf hash can never be reinterpreted
as an interior node. All functions are pure; there is no I/O here.
"""

from __future__ import annotations

import hashlib

# Domain-separation prefixes (RFC 6962 §2.1).
_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _leaf_node(d: bytes) -> bytes:
    """The tree hash of a single leaf: ``SHA-256(0x00 || d)``."""
    return _sha256(_LEAF_PREFIX + d)


def _interior(left: bytes, right: bytes) -> bytes:
    """The tree hash of an interior node: ``SHA-256(0x01 || left || right)``."""
    return _sha256(_NODE_PREFIX + left + right)


def _split(n: int) -> int:
    """The largest power of two strictly smaller than ``n`` (``n >= 2``)."""
    return 1 << ((n - 1).bit_length() - 1)


def build_root(leaves: list[bytes]) -> bytes:
    """The Merkle Tree Hash of ``leaves`` (each a leaf hash ``d``).

    Empty list → ``SHA-256("")`` (RFC 6962's MTH of the empty tree)."""
    n = len(leaves)
    if n == 0:
        return _sha256(b"")
    if n == 1:
        return _leaf_node(leaves[0])
    k = _split(n)
    return _interior(build_root(leaves[:k]), build_root(leaves[k:]))


def inclusion_proof(leaves: list[bytes], index: int) -> list[bytes]:
    """The audit path proving the leaf at ``index`` is in the tree over
    ``leaves`` — the sibling subtree hashes from the leaf up to the root."""
    n = len(leaves)
    if not 0 <= index < n:
        raise IndexError(f"index {index} out of range for {n} leaves")
    if n == 1:
        return []
    k = _split(n)
    if index < k:
        return inclusion_proof(leaves[:k], index) + [build_root(leaves[k:])]
    return inclusion_proof(leaves[k:], index - k) + [build_root(leaves[:k])]


def verify_proof(leaf: bytes, index: int, tree_size: int, proof: list[bytes], root: bytes) -> bool:
    """Recompute the root from a leaf hash ``d`` and its audit path (RFC 6962
    §2.1.1 verification) and compare it to ``root``."""
    if not 0 <= index < tree_size:
        return False
    node = _leaf_node(leaf)
    fn, sn = index, tree_size - 1
    for sibling in proof:
        if sn == 0:
            return False
        if (fn & 1) or (fn == sn):
            node = _interior(sibling, node)
            if not (fn & 1):
                while not (fn & 1) and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            node = _interior(node, sibling)
        fn >>= 1
        sn >>= 1
    return sn == 0 and node == root
