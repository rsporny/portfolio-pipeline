from __future__ import annotations

import contextlib
import json
import logging
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


class PublishError(RuntimeError):
    """Raised when publishing can't proceed (e.g. the site dir is missing)."""


@dataclass
class PublishResult:
    week: str
    site_files: list[Path] = field(default_factory=list)
    published_files: list[Path] = field(default_factory=list)


def write_manifest(site_dir: Path) -> Path:
    """(Re)build ``index.json`` in the site devlog dir from the published
    ``<week>.md`` files — the manifest the website reads to list entries."""
    entries = []
    for md in site_dir.glob("*.md"):
        front, _ = parse(md.read_text())
        if not front:
            continue
        published = front.get("published_at") or front.get("generated_at")
        entries.append(
            {
                "week": str(front.get("week") or md.stem),
                "title": str(front.get("title", "")),
                "date": str(published)[:10] if published else "",
            }
        )
    entries.sort(key=lambda e: e["week"], reverse=True)
    manifest = site_dir / "index.json"
    manifest.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    return manifest


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
        write_manifest(site_dir)

    return results
