from __future__ import annotations

import json
import re
from pathlib import Path

from ..frontmatter import dump, parse
from .base import DevlogEntry, FileChange, RenderContext

# The sporny.pl adapter. Everything the pipeline knows about that website — its
# devlog file layout, the index.json manifest schema, and the per-series
# numbering rules — lives here and only here.

# The default series identity, used when neither the caller's configured series
# nor an entry's own recorded series (front matter / prior manifest) supplies one.
DEFAULT_SERIES = "Senior SDET log"

# Backfill: recover a weekly's frozen number from a legacy "… #N: …" title.
_TITLE_NUMBER = re.compile(r"#(\d+)")

MANIFEST_NAME = "index.json"


class SpornyPlAdapter:
    """Renders devlog entries and the site manifest for sporny.pl."""

    name = "sporny_pl"

    def render(self, entry: DevlogEntry, ctx: RenderContext) -> list[FileChange]:
        """Return the file changes to publish one entry: its ``<slug>.md`` plus
        the regenerated ``index.json`` (which incorporates this entry and every
        existing entry under ``ctx.site_dir``)."""
        default_series = ctx.config.content.devlog_title_prefix
        front = self._front_matter(entry, default_series)
        markdown = FileChange(ctx.site_dir / f"{entry.slug}.md", dump(front, entry.body))
        # Inject the entry being published so the manifest reflects it even on a
        # first publish (its .md is not on disk until the core writes it).
        manifest = self._manifest(ctx.site_dir, default_series, extra={entry.slug: front})
        return [markdown, manifest]

    def manifest(self, site_dir: Path, series: str = DEFAULT_SERIES) -> FileChange:
        """Rebuild ``index.json`` purely from the ``.md`` files already on disk."""
        return self._manifest(site_dir, series, extra={})

    # --- front matter --------------------------------------------------------
    # The adapter composes the site file's front matter for BOTH entry kinds
    # from the neutral entry — nothing is passed through from the draft.

    def _front_matter(self, entry: DevlogEntry, default_series: str) -> dict:
        front: dict = {
            "type": entry.type,
            "series": entry.meta.get("series") or default_series,
            "slug": entry.slug,
            "title": entry.title,
            "published_at": entry.date,
            "status": "published",
        }
        if entry.meta.get("kind"):
            front["kind"] = str(entry.meta["kind"])
        # Weeklies carry their provenance onto the public site file — an explicit
        # transparency choice of this adapter, not a passthrough of draft state.
        if entry.meta.get("source_initiatives"):
            front["source_initiatives"] = entry.meta["source_initiatives"]
        return front

    # --- manifest ------------------------------------------------------------

    def _manifest(self, site_dir: Path, series: str, extra: dict[str, dict]) -> FileChange:
        """(Re)build the manifest the website reads to list entries.

        Each entry carries: ``type`` (``weekly-activity`` or ``custom``, from
        front matter); ``series`` (the caller's configured series for weeklies,
        but an entry's own recorded series — front matter or prior manifest —
        always wins so history is never rewritten); ``n`` (a per-series sequence
        shared across both types, frozen once assigned: reused from the prior
        manifest or front matter, else backfilled from a legacy ``#N`` title,
        else ``max(n in series) + 1``); ``slug`` (the filename, also the page
        anchor); ``title`` / ``date`` (from ``published_at``); and an optional
        ``kind`` kicker for customs. A custom lacking ``status: published`` is
        excluded; custom ``.md`` files are never written or deleted here.
        Entries order by ``date`` so weekly and custom interleave chronologically."""
        manifest_path = site_dir / MANIFEST_NAME
        frozen = _load_frozen(manifest_path)

        fronts: dict[str, dict] = {}
        for md in sorted(site_dir.glob("*.md")):
            front, _ = parse(md.read_text())
            if front:
                fronts[md.stem] = front
        fronts.update(extra)  # the entry being published wins (fresh front matter)

        entries: list[dict] = []
        for slug in sorted(fronts):
            front = fronts[slug]
            etype = str(front.get("type", "weekly-activity"))
            # Hand-authored entries only appear once published; weeklies are
            # always published by the time they reach the site dir.
            if etype == "custom" and str(front.get("status", "")) != "published":
                continue

            prior = frozen.get(slug, {})
            published = front.get("published_at") or front.get("generated_at")

            # series/n resolution: frozen (never rewritten) → front matter → derive.
            entry_series = str(prior.get("series") or front.get("series") or series)
            n = prior.get("n")
            if n is None:
                n = front.get("n")
            if n is None and etype == "weekly-activity":
                match = _TITLE_NUMBER.search(str(front.get("title", "")))
                if match:
                    n = int(match.group(1))

            entry = {
                "type": etype,
                "series": entry_series,
                "n": int(n) if n is not None else None,
                "slug": slug,
                "title": str(front.get("title", "")),
                "date": str(published)[:10] if published else "",
            }
            if front.get("kind"):
                entry["kind"] = str(front["kind"])
            entries.append(entry)

        _assign_numbers(entries)
        entries.sort(key=lambda e: (e["date"], e["slug"]), reverse=True)
        return FileChange(manifest_path, json.dumps(entries, indent=2, ensure_ascii=False) + "\n")


def _load_frozen(manifest_path: Path) -> dict[str, dict]:
    """Read the previous manifest into a ``slug -> entry`` map, the freeze store
    for ``series`` and ``n``: once an entry has recorded values, later runs reuse
    them verbatim so numbers never shift or renumber on re-run."""
    if not manifest_path.exists():
        return {}
    try:
        prior = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    frozen: dict[str, dict] = {}
    for entry in prior if isinstance(prior, list) else []:
        if isinstance(entry, dict):
            # Accept legacy "week" as the slug so first-run backfill is idempotent.
            slug = entry.get("slug") or entry.get("week")
            if slug:
                frozen[str(slug)] = entry
    return frozen


def _assign_numbers(entries: list[dict]) -> None:
    """Fill in any missing ``n`` in place: for each series, the next number is
    ``max(existing n in that series) + 1``. Assignment walks entries oldest first
    (by date, then slug) so the sequence is deterministic and stable."""
    series_max: dict[str, int] = {}
    for entry in entries:
        if entry["n"] is not None:
            series_max[entry["series"]] = max(series_max.get(entry["series"], 0), entry["n"])
    for entry in sorted(entries, key=lambda e: (e["date"], e["slug"])):
        if entry["n"] is None:
            nxt = series_max.get(entry["series"], 0) + 1
            entry["n"] = nxt
            series_max[entry["series"]] = nxt
