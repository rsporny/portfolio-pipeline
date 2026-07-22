"""Provenance (v0.5): signed entries + a per-entry transparency ledger, each
entry's hash anchored on-chain independently.

Layering:

- :mod:`content` hashes a published devlog entry as the plain ``sha256`` of its
  raw ``<slug>.md`` bytes — reproducible with ``sha256sum`` and ``gpg --verify``.
- :mod:`log` is the append-only ledger (``provenance/log.jsonl``) under
  ``state.root``: one record per entry, with its hash, signature, and anchor.
- :mod:`sign` wraps detached GPG signing/verification (injectable for tests).
- :mod:`anchor` is a pluggable backend (null / file / Cardano testnet) that
  timestamps one entry's hash in a transaction.
- :mod:`verify` recomputes each hash, checks signatures, and optionally reads the
  anchor back — no cumulative root, no merkle proofs.

Nothing here talks to the network by default; the Cardano backend is opt-in and
lazily imported.
"""
