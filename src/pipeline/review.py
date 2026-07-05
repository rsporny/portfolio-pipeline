from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .frontmatter import parse

# Rendered supporting material, not an editable draft to review.
_SKIP = {"summary-tech.md"}


@dataclass
class DraftRecord:
    week: str
    file: str
    status: str
    title: str


def list_drafts(drafts_dir: Path | str = "drafts") -> list[DraftRecord]:
    """Scan ``drafts/*/*.md`` and read week / status / title from front matter."""
    records: list[DraftRecord] = []
    for md in sorted(Path(drafts_dir).glob("*/*.md")):
        if md.name in _SKIP:
            continue
        front, _ = parse(md.read_text())
        records.append(
            DraftRecord(
                week=md.parent.name,
                file=md.name,
                status=str(front.get("status", "")),
                title=str(front.get("title", "")),
            )
        )
    return records
