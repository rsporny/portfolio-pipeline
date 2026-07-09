from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..config import Config

# The pipeline core knows only this interface. All knowledge about a specific
# website (its file layout, manifest schema, numbering rules) lives inside one
# adapter module. Supporting another site = writing another adapter; nothing
# site-specific may live outside `site_adapter/`.


class AdapterError(RuntimeError):
    """Raised when a configured adapter cannot be resolved."""


@dataclass(frozen=True)
class FileChange:
    """A single file to write into the website checkout. Distinct from
    :class:`pipeline.models.FileChange`, which describes a *changed file* in
    GitHub activity — this one carries content the adapter wants published."""

    path: Path
    content: str


@dataclass(frozen=True)
class DevlogEntry:
    """A devlog entry to publish, described in site-neutral terms.

    Both weekly and custom entries carry the same structured fields — the core
    never hands the adapter a ready-made front-matter dict, so no producer-side
    (transform) decision leaks onto the published site. Everything site- or
    pipeline-specific that isn't part of this neutral core goes in ``meta`` (e.g.
    weeklies pass ``source_initiatives``; customs pass ``kind`` and, when the
    author set one, a per-entry ``series`` override). The adapter decides which,
    if any, of those to surface — it owns the entire output shape."""

    slug: str
    title: str
    body: str  # markdown body, H1 included, for both kinds
    date: str  # publication date, YYYY-MM-DD
    type: str = "weekly-activity"  # or "custom" (a real pipeline distinction)
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RenderContext:
    """Everything the adapter needs beyond the entry itself: where the website's
    devlog dir lives and the pipeline config (the adapter reads whatever it needs
    from it — e.g. the configured series — so no site vocabulary leaks into this
    neutral interface)."""

    site_dir: Path
    config: Config


@runtime_checkable
class SiteAdapter(Protocol):
    """Renders a devlog entry into the file changes a website checkout needs."""

    def render(self, entry: DevlogEntry, ctx: RenderContext) -> list[FileChange]:
        """The entry's markdown file plus the regenerated site manifest."""
        ...
