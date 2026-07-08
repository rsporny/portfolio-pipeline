from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .frontmatter import dump, parse

logger = logging.getLogger(__name__)

# Only the devlog is published to the website; the rest of the bundle is moved
# to published/ as the owner's local record.
DEVLOG = "devlog.md"

# The default series identity, used when neither the config nor an entry's own
# recorded series (front matter / prior manifest) supplies one.
DEFAULT_SERIES = "Senior SDET log"

# Backfill: recover a weekly's frozen number from a legacy "… #N: …" title.
_TITLE_NUMBER = re.compile(r"#(\d+)")


class PublishError(RuntimeError):
    """Raised when publishing can't proceed (e.g. the site dir is missing)."""


@dataclass
class PublishResult:
    week: str
    site_files: list[Path] = field(default_factory=list)
    published_files: list[Path] = field(default_factory=list)


def _load_frozen(manifest_path: Path) -> dict[str, dict]:
    """Read the previous manifest into a ``slug -> entry`` map, the freeze store
    for ``series`` and ``n``: once an entry has recorded values, later runs
    reuse them verbatim so numbers never shift or renumber on re-run."""
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


def write_manifest(site_dir: Path, series: str = DEFAULT_SERIES) -> Path:
    """(Re)build ``index.json`` in the site devlog dir from the ``<slug>.md``
    files — the manifest the website reads to list entries.

    The manifest schema is owned by the website. Each entry carries:

    - ``type`` — ``weekly-activity`` for the GitHub-derived devlogs, ``custom``
      for hand-authored entries (essays/notes), read from front matter and
      defaulting to ``weekly-activity``;
    - ``series`` — the role identity (e.g. ``Senior SDET log``); ``series`` for
      weeklies is the caller's configured current series, but an entry's own
      recorded series (front matter or prior manifest) always wins so history
      is never rewritten when the role changes;
    - ``n`` — a per-series sequence number shared across weekly and custom
      entries, **frozen once assigned**: reused from the prior manifest or front
      matter, else backfilled from a legacy ``#N`` title, else the next
      ``max(n in series) + 1``;
    - ``slug`` — the ``.md`` filename without extension (also the page anchor);
    - ``title`` / ``date`` — the heading and the "Published" date (custom dates
      come from ``published_at``);
    - ``kind`` — optional custom kicker label, passed through when present.

    Custom entries are authored by hand in the website repo and are never
    written or deleted here; one lacking ``status: published`` is excluded.
    Entries are ordered by ``date`` so weekly and custom entries interleave
    chronologically."""
    manifest = site_dir / "index.json"
    frozen = _load_frozen(manifest)

    entries: list[dict] = []
    for md in sorted(site_dir.glob("*.md")):
        front, _ = parse(md.read_text())
        if not front:
            continue
        etype = str(front.get("type", "weekly-activity"))
        # Hand-authored entries only appear once published; weeklies are always
        # published by the time they reach the site dir.
        if etype == "custom" and str(front.get("status", "")) != "published":
            continue

        slug = md.stem  # the filename is the canonical slug (and the #hash anchor)
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
    manifest.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    return manifest


def _assign_numbers(entries: list[dict]) -> None:
    """Fill in any missing ``n`` in place: for each series, the next number is
    ``max(existing n in that series) + 1``. Assignment walks entries oldest
    first (by date, then slug) so the sequence is deterministic and stable."""
    series_max: dict[str, int] = {}
    for entry in entries:
        if entry["n"] is not None:
            series_max[entry["series"]] = max(series_max.get(entry["series"], 0), entry["n"])
    for entry in sorted(entries, key=lambda e: (e["date"], e["slug"])):
        if entry["n"] is None:
            nxt = series_max.get(entry["series"], 0) + 1
            entry["n"] = nxt
            series_max[entry["series"]] = nxt


def publish_approved(
    config: Config,
    approved_dir: Path | str = "approved",
    published_dir: Path | str = "published",
    *,
    site_repo: Path | str | None = None,
    dry_run: bool = False,
) -> list[PublishResult]:
    """Copy each approved week's devlog into the site's devlog dir (status
    flipped to ``published``) and move the whole approved bundle to
    ``published/``. Never commits or pushes the website repo.

    ``site_repo`` overrides ``config.output.site_repo_path`` (used by CI to
    target a checked-out copy of the website repo)."""
    approved_dir = Path(approved_dir)
    published_dir = Path(published_dir)
    site_root = Path(site_repo).expanduser() if site_repo else config.output.site_repo
    site_dir = site_root / config.output.site_devlog_dir

    week_dirs = sorted(p for p in approved_dir.glob("*") if p.is_dir())
    if not week_dirs:
        return []

    if not site_dir.exists():
        raise PublishError(
            f"Site devlog dir does not exist: {site_dir} — clone the site repo or "
            "fix output.site_repo_path / output.site_devlog_dir"
        )

    results: list[PublishResult] = []
    for week_dir in week_dirs:
        week = week_dir.name
        result = PublishResult(week=week)
        for f in sorted(p for p in week_dir.iterdir() if p.is_file()):
            pub_dest = published_dir / week / f.name
            is_devlog = f.name == DEVLOG

            if is_devlog:
                result.site_files.append(site_dir / f"{week}.md")
            result.published_files.append(pub_dest)
            if dry_run:
                continue

            pub_dest.parent.mkdir(parents=True, exist_ok=True)
            if f.suffix == ".md":
                front, body = parse(f.read_text())
                if front:
                    front["status"] = "published"
                    front.setdefault("published_at", datetime.now(UTC).date().isoformat())
                    text = dump(front, body)
                else:
                    text = f.read_text()
                if is_devlog:
                    (site_dir / f"{week}.md").write_text(text)
                pub_dest.write_text(text)
                f.unlink()
            else:
                shutil.move(str(f), str(pub_dest))

        if not dry_run:
            with contextlib.suppress(OSError):
                week_dir.rmdir()  # remove the now-empty approved/<week>/
        results.append(result)

    if not dry_run and any(r.site_files for r in results):
        write_manifest(site_dir, config.content.devlog_title_prefix)

    return results
