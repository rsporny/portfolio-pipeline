from __future__ import annotations

import hashlib

import pytest

from pipeline.provenance import merkle


def _leaves(n: int) -> list[bytes]:
    return [hashlib.sha256(bytes([i])).digest() for i in range(n)]


def test_empty_root_is_sha256_of_empty():
    assert merkle.build_root([]) == hashlib.sha256(b"").digest()


def test_single_leaf_root_is_leaf_node():
    d = _leaves(1)[0]
    assert merkle.build_root([d]) == hashlib.sha256(b"\x00" + d).digest()


def test_root_is_deterministic_and_order_sensitive():
    leaves = _leaves(5)
    assert merkle.build_root(leaves) == merkle.build_root(list(leaves))
    swapped = [leaves[1], leaves[0], *leaves[2:]]
    assert merkle.build_root(swapped) != merkle.build_root(leaves)


@pytest.mark.parametrize("size", range(1, 10))
def test_inclusion_proofs_verify_for_every_index(size):
    leaves = _leaves(size)
    root = merkle.build_root(leaves)
    for i in range(size):
        proof = merkle.inclusion_proof(leaves, i)
        assert merkle.verify_proof(leaves[i], i, size, proof, root) is True


def test_tampered_leaf_fails():
    leaves = _leaves(6)
    root = merkle.build_root(leaves)
    proof = merkle.inclusion_proof(leaves, 3)
    forged = hashlib.sha256(b"forged").digest()
    assert merkle.verify_proof(forged, 3, 6, proof, root) is False


def test_wrong_index_fails():
    leaves = _leaves(6)
    root = merkle.build_root(leaves)
    proof = merkle.inclusion_proof(leaves, 3)
    assert merkle.verify_proof(leaves[3], 4, 6, proof, root) is False


def test_tampered_root_fails():
    leaves = _leaves(7)
    proof = merkle.inclusion_proof(leaves, 2)
    bad_root = hashlib.sha256(b"nope").digest()
    assert merkle.verify_proof(leaves[2], 2, 7, proof, bad_root) is False


def test_tampered_proof_element_fails():
    leaves = _leaves(8)
    root = merkle.build_root(leaves)
    proof = merkle.inclusion_proof(leaves, 5)
    assert proof, "an 8-leaf proof has siblings"
    proof[0] = hashlib.sha256(b"swapped").digest()
    assert merkle.verify_proof(leaves[5], 5, 8, proof, root) is False


def test_index_out_of_range_raises_and_verify_is_false():
    leaves = _leaves(3)
    with pytest.raises(IndexError):
        merkle.inclusion_proof(leaves, 3)
    assert merkle.verify_proof(leaves[0], 3, 3, [], merkle.build_root(leaves)) is False
