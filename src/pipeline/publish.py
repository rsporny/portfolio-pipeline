from __future__ import annotations

import contextlib
import logging
import shutil
from dataclasses import dataclass, field
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


def publish_approved(
    config: Config,
    approved_dir: Path | str = "approved",
    published_dir: Path | str = "published",
    *,
    dry_run: bool = False,
) -> list[PublishResult]:
    """Copy each approved week's devlog into the site's devlog dir (status
    flipped to ``published``) and move the whole approved bundle to
    ``published/``. Never commits or pushes the website repo."""
    approved_dir = Path(approved_dir)
    published_dir = Path(published_dir)
    site_dir = config.output.site_repo / config.output.site_devlog_dir

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

    return results
