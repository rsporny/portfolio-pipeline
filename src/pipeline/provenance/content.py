"""Hash a *published* devlog entry as the plain ``sha256`` of its raw ``<slug>.md``
bytes — exactly the file the site serves.

This is deliberately the whole file (front matter + body), so anyone can
reproduce the commitment with universal tools and no clone::

    curl -s https://sporny.pl/content/devlog/2026-W30.md | sha256sum
    gpg --verify 2026-W30.md.sig 2026-W30.md

The consequence — and the one rule the rest of provenance depends on — is that
**provenance metadata is never written back into the ``.md``**: doing so would
change the file's hash after the fact (a circular, unverifiable commitment). The
signature, hash, and anchor live in the manifest and the ``.sig`` sidecar instead.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..frontmatter import parse


def sha256_hex(data: bytes) -> str:
    """The hex SHA-256 of ``data`` — the per-entry content commitment."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class PublishedEntry:
    """A published entry as the exact bytes that get hashed and signed, plus the
    ``slug`` / ``published_at`` read from its front matter for the ledger."""

    slug: str
    published_at: str  # YYYY-MM-DD
    data: bytes  # the verbatim <slug>.md bytes served by the site

    @classmethod
    def from_path(cls, path: Path | str) -> PublishedEntry:
        """Read a site ``<slug>.md`` file as the entry to commit to."""
        path = Path(path)
        data = path.read_bytes()
        front, _ = parse(data.decode("utf-8"))
        return cls(
            slug=str(front.get("slug", path.stem)),
            published_at=str(front.get("published_at", ""))[:10],
            data=data,
        )

    @property
    def sha256(self) -> str:
        return sha256_hex(self.data)
