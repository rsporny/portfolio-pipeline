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
from .site_adapter import DevlogEntry, RenderContext, SiteAdapter, get_adapter

logger = logging.getLogger(__name__)

# Only the devlog is published to the website; the rest of the bundle is moved
# to published/ as the owner's local record.
DEVLOG = "devlog.md"

# First Markdown H1 (``# Title``) in a document body.
_H1 = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.MULTILINE)


class PublishError(RuntimeError):
    """Raised when publishing can't proceed (e.g. the site dir is missing)."""


@dataclass
class PublishResult:
    week: str
    site_files: list[Path] = field(default_factory=list)
    published_files: list[Path] = field(default_factory=list)


@dataclass
class CustomResult:
    slug: str
    n: int
    series: str
    site_file: Path


def _resolve_site_dir(config: Config, site_repo: Path | str | None) -> Path:
    """The website's devlog dir (``site_repo`` overrides config); must exist."""
    site_root = Path(site_repo).expanduser() if site_repo else config.output.site_repo
    site_dir = site_root / config.output.site_devlog_dir
    if not site_dir.exists():
        raise PublishError(
            f"Site devlog dir does not exist: {site_dir} — clone the site repo or "
            "fix output.site_repo_path / output.site_devlog_dir"
        )
    return site_dir


def _write_changes(adapter: SiteAdapter, entry: DevlogEntry, ctx: RenderContext) -> list[dict]:
    """Render an entry through the adapter and write its file changes into the
    website checkout. Returns the parsed manifest so callers can read numbering
    back out. Never commits or pushes the site repo."""
    manifest_entries: list[dict] = []
    for change in adapter.render(entry, ctx):
        change.path.write_text(change.content)
        if change.path.name == "index.json":
            manifest_entries = json.loads(change.content)
    return manifest_entries


def publish_approved(
    config: Config,
    approved_dir: Path | str | None = None,
    published_dir: Path | str | None = None,
    *,
    site_repo: Path | str | None = None,
    dry_run: bool = False,
) -> list[PublishResult]:
    """Copy each approved week's devlog into the site's devlog dir (status
    flipped to ``published``) and move the whole approved bundle to
    ``published/``. Site rendering (the devlog file + the manifest) is delegated
    to the configured adapter; this function never commits or pushes the website.

    ``site_repo`` overrides ``config.output.site_repo_path`` (used by CI to
    target a checked-out copy of the website repo). ``approved_dir`` /
    ``published_dir`` default to ``config.state_dir(…)`` under ``state.root``."""
    approved_dir = Path(approved_dir) if approved_dir is not None else config.state_dir("approved")
    published_dir = (
        Path(published_dir) if published_dir is not None else config.state_dir("published")
    )

    week_dirs = sorted(p for p in approved_dir.glob("*") if p.is_dir())
    if not week_dirs:
        return []

    site_dir = _resolve_site_dir(config, site_repo)
    adapter = get_adapter(config.output.adapter)
    ctx = RenderContext(site_dir=site_dir, config=config)

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
                pub_dest.write_text(text)
                if is_devlog:
                    if front:
                        # Hand the adapter a neutral entry; it composes the site
                        # file's front matter itself. Provenance (source_initiatives)
                        # rides along in meta, and the full-fidelity draft front
                        # matter stays only in the published/ local record above.
                        meta = (
                            {"source_initiatives": front["source_initiatives"]}
                            if front.get("source_initiatives")
                            else {}
                        )
                        # Per-section category dividers ride along the same way.
                        if front.get("topics"):
                            meta["topics"] = front["topics"]
                        entry = DevlogEntry(
                            slug=week,
                            title=str(front.get("title", "")),
                            body=body,
                            date=str(front["published_at"])[:10],
                            type="weekly-activity",
                            meta=meta,
                        )
                        _write_changes(adapter, entry, ctx)
                    else:
                        (site_dir / f"{week}.md").write_text(text)
                f.unlink()
            else:
                shutil.move(str(f), str(pub_dest))

        if not dry_run:
            with contextlib.suppress(OSError):
                week_dir.rmdir()  # remove the now-empty approved/<week>/
        results.append(result)

    return results


def publish_custom(
    config: Config,
    input_md: Path | str,
    *,
    site_repo: Path | str | None = None,
    slug: str | None = None,
    kind: str | None = None,
    date: str | None = None,
) -> CustomResult:
    """Turn a hand-written Markdown file into a ready-to-publish ``custom`` devlog
    entry in the website repo, then let the adapter regenerate the manifest so it
    picks up its per-series number. File-only: never commits or pushes the site.

    The title is taken from the file's first ``# H1``; the body (H1 included) is
    carried through verbatim. The slug defaults to the input filename, ``kind``
    and the date fall back to any front matter then a flag/today, and the number
    ``n`` is assigned by the adapter's manifest — never by hand. Re-running for an
    existing slug updates the file in place while keeping the frozen number."""
    input_md = Path(input_md)
    site_dir = _resolve_site_dir(config, site_repo)
    adapter = get_adapter(config.output.adapter)

    front_in, body = parse(input_md.read_text())
    match = _H1.search(body)
    if not match:
        raise PublishError(
            f"{input_md} has no '# Title' heading — the first H1 becomes the entry title"
        )
    title = match.group(1).strip()

    slug = slug or input_md.stem
    published_at = date or front_in.get("published_at") or datetime.now(UTC).date().isoformat()

    # Site-specific hints (kicker, per-entry series override) ride in meta; the
    # adapter resolves them — publish.py never resolves the series itself.
    meta: dict = {}
    if kind or front_in.get("kind"):
        meta["kind"] = str(kind or front_in["kind"])
    if front_in.get("series"):
        meta["series"] = str(front_in["series"])

    entry = DevlogEntry(
        slug=slug,
        title=title,
        body=body,
        date=str(published_at)[:10],
        type="custom",
        meta=meta,
    )
    ctx = RenderContext(site_dir=site_dir, config=config)
    manifest = _write_changes(adapter, entry, ctx)

    # The adapter owns front matter + numbering; read the assigned number and
    # the resolved series back out of the manifest entry it produced.
    published = next((e for e in manifest if e.get("slug") == slug), {})
    n = published.get("n", 0)
    series = str(published.get("series", ""))
    return CustomResult(slug=slug, n=n, series=series, site_file=site_dir / f"{slug}.md")
