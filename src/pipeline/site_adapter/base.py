from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

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

    Weeklies arrive already front-mattered from the draft bundle, so they carry
    ``front_matter`` verbatim (preserving keys like ``source_initiatives``).
    Custom entries carry their semantic fields and the adapter composes the
    front matter (the manifest's source schema is the adapter's business)."""

    slug: str
    body: str
    type: str = "weekly-activity"  # or "custom"
    title: str | None = None
    series: str | None = None
    published_at: str | None = None
    kind: str | None = None
    status: str = "published"
    front_matter: dict | None = None


@dataclass(frozen=True)
class RenderContext:
    """Everything the adapter needs beyond the entry itself: where the website's
    devlog dir lives and the caller's configured current series."""

    site_dir: Path
    series: str


@runtime_checkable
class SiteAdapter(Protocol):
    """Renders a devlog entry into the file changes a website checkout needs."""

    def render(self, entry: DevlogEntry, ctx: RenderContext) -> list[FileChange]:
        """The entry's markdown file plus the regenerated site manifest."""
        ...
