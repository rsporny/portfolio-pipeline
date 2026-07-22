"""Canonicalization: turn a *published* devlog entry into stable bytes and a
domain-separated leaf hash — the per-entry commitment the transparency log and
signatures cover.

The canonical form is a deterministic JSON object over a deliberately narrow
subset of the entry: ``slug``, ``title``, ``published_at``, ``type``, ``series``,
and the markdown ``body`` (H1 included). It **excludes** the injected
``provenance:`` block, ``status``, ``kind``, ``source_initiatives``, and anything
manifest-derived — so attaching the signature sidecar / badge front matter to the
entry does not change the hash, and the signature stays valid. The included
fields are all frozen by the site adapter once an entry is published, so the hash
reproduces across re-renders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..frontmatter import parse

# Bump if the canonical form ever changes, so old and new hashes never collide.
_LEAF_DOMAIN = b"pp-leaf:v1\x00"


def _normalize_body(body: str) -> str:
    """LF line endings, no leading/trailing whitespace — so trivial editor churn
    (a stray CRLF or a trailing newline) doesn't change a leaf."""
    return body.replace("\r\n", "\n").replace("\r", "\n").strip()


@dataclass(frozen=True)
class CanonicalEntry:
    """The stable identity of a published entry, independent of presentation."""

    slug: str
    title: str
    published_at: str  # YYYY-MM-DD
    type: str
    series: str
    body: str

    @classmethod
    def from_markdown(cls, text: str) -> CanonicalEntry:
        """Extract the canonical subset from a site ``<slug>.md`` document."""
        front, body = parse(text)
        return cls(
            slug=str(front.get("slug", "")),
            title=str(front.get("title", "")),
            published_at=str(front.get("published_at", ""))[:10],
            type=str(front.get("type", "weekly-activity")),
            series=str(front.get("series", "")),
            body=body,
        )

    def to_bytes(self) -> bytes:
        """Deterministic canonical bytes: sorted-key, compact UTF-8 JSON."""
        obj = {
            "slug": self.slug,
            "title": self.title,
            "published_at": self.published_at,
            "type": self.type,
            "series": self.series,
            "body": _normalize_body(self.body),
        }
        return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )

    def leaf_hash(self) -> bytes:
        """The domain-separated content commitment (a merkle leaf)."""
        return hashlib.sha256(_LEAF_DOMAIN + self.to_bytes()).digest()

    def leaf_hex(self) -> str:
        return self.leaf_hash().hex()
