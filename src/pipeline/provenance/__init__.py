"""Provenance (v0.5): signed entries + a cumulative merkle transparency log,
whose root is anchored on-chain.

Layering:

- :mod:`canonical` turns a published devlog entry into stable bytes and a
  domain-separated leaf hash — the per-entry content commitment.
- :mod:`merkle` builds an RFC 6962 tree over those leaf hashes, with inclusion
  proofs, so a single entry can be proven against one anchored root.
- :mod:`log` is the append-only transparency log (``provenance/log.jsonl`` +
  ``root.json``) under ``state.root``.
- :mod:`sign` wraps detached GPG signing/verification (injectable for tests).
- :mod:`anchor` is a pluggable backend (null / file / Cardano testnet) that
  timestamps the current root.
- :mod:`verify` recomputes leaves, checks signatures + the root, and optionally
  reads the anchor back.

Nothing here talks to the network by default; the Cardano backend is opt-in and
lazily imported.
"""
