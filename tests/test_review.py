from __future__ import annotations

from pipeline.frontmatter import dump
from pipeline.review import list_drafts


def _draft(drafts_dir, week, name, status="draft", title="T"):
    week_dir = drafts_dir / week
    week_dir.mkdir(parents=True, exist_ok=True)
    (week_dir / name).write_text(dump({"title": title, "status": status, "week": week}, "body"))


def test_list_drafts_reads_front_matter(tmp_path):
    drafts = tmp_path / "drafts"
    _draft(drafts, "2026-W27", "devlog.md", title="Senior SDET log #1")
    _draft(drafts, "2026-W27", "social.md")
    _draft(drafts, "2026-W27", "highlights.md")
    (drafts / "2026-W27" / "summary-tech.md").write_text("# Technical summary")  # skipped

    records = list_drafts(drafts)
    files = {r.file for r in records}
    assert files == {"devlog.md", "social.md", "highlights.md"}
    assert "summary-tech.md" not in files

    devlog = next(r for r in records if r.file == "devlog.md")
    assert devlog.week == "2026-W27"
    assert devlog.status == "draft"
    assert devlog.title == "Senior SDET log #1"


def test_list_drafts_empty(tmp_path):
    assert list_drafts(tmp_path / "drafts") == []
