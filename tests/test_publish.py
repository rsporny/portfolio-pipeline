from __future__ import annotations

import json

import pytest

from pipeline.config import Config, OutputConfig, ReposConfig
from pipeline.frontmatter import dump, parse
from pipeline.publish import PublishError, publish_approved, publish_custom


def _config(site_repo, devlog_dir="content/devlog"):
    return Config(
        github_user="rsporny",
        repos=ReposConfig(allowlist=["o/r"]),
        output=OutputConfig(site_repo_path=str(site_repo), site_devlog_dir=devlog_dir),
    )


def _approved(approved_dir, week="2026-W27"):
    week_dir = approved_dir / week
    week_dir.mkdir(parents=True)
    (week_dir / "devlog.md").write_text(
        dump({"title": "Log #1", "status": "draft", "week": week}, "Devlog body.")
    )
    (week_dir / "social.md").write_text(
        dump({"title": "Log #1", "status": "draft", "week": week}, "Social body.")
    )
    (week_dir / "highlights.md").write_text(
        dump({"status": "draft", "week": week}, "- a highlight")
    )
    (week_dir / "summary-tech.json").write_text('{"initiatives": []}')
    return week


def _site(tmp_path, devlog_dir="content/devlog"):
    site = tmp_path / "site"
    (site / devlog_dir).mkdir(parents=True)
    return site


def test_publish_copies_devlog_to_site_and_moves_bundle(tmp_path):
    approved, published = tmp_path / "approved", tmp_path / "published"
    site = _site(tmp_path)
    week = _approved(approved)

    results = publish_approved(_config(site), approved_dir=approved, published_dir=published)

    # Devlog on the site as <week>.md, with status flipped to published.
    site_file = site / "content/devlog" / f"{week}.md"
    assert site_file.exists()
    assert parse(site_file.read_text())[0]["status"] == "published"
    # Only the devlog goes to the site.
    assert not (site / "content/devlog" / "social.md").exists()
    assert not (site / "content/devlog" / "highlights.md").exists()

    # The whole bundle moved to published/, approved/<week> cleaned up.
    for name in ("devlog.md", "social.md", "highlights.md", "summary-tech.json"):
        assert (published / week / name).exists(), name
    assert parse((published / week / "devlog.md").read_text())[0]["status"] == "published"
    assert not (approved / week).exists()

    assert results[0].week == week
    assert len(results[0].site_files) == 1


def test_publish_writes_site_manifest(tmp_path):
    approved, published = tmp_path / "approved", tmp_path / "published"
    site = _site(tmp_path)
    week = _approved(approved)

    publish_approved(_config(site), approved_dir=approved, published_dir=published)

    manifest = site / "content/devlog" / "index.json"
    assert manifest.exists()
    entries = json.loads(manifest.read_text())
    # `slug` replaced the old `week` key.
    assert entries[0]["slug"] == week
    assert "week" not in entries[0]
    assert entries[0]["title"] == "Log #1"
    # GitHub-derived entries default to the weekly-activity type.
    assert entries[0]["type"] == "weekly-activity"
    # The configured series is emitted per entry.
    assert entries[0]["series"] == "Senior SDET log"
    # publish stamps a publication date (YYYY-MM-DD) that the manifest carries
    assert entries[0]["date"]
    assert "published_at" in parse((published / week / "devlog.md").read_text())[0]


def _weekly(site_dir, slug, title, date, **extra):
    (site_dir / f"{slug}.md").write_text(
        dump({"title": title, "week": slug, "published_at": date, **extra}, "weekly")
    )


def test_publish_site_repo_override(tmp_path):
    approved, published = tmp_path / "approved", tmp_path / "published"
    week = _approved(approved)
    # config points at a bogus site; the override wins (CI-style checkout).
    ci_site = tmp_path / "ci-checkout"
    (ci_site / "content/devlog").mkdir(parents=True)
    cfg = _config(tmp_path / "unused")

    publish_approved(cfg, approved_dir=approved, published_dir=published, site_repo=ci_site)

    assert (ci_site / "content/devlog" / f"{week}.md").exists()
    assert (ci_site / "content/devlog" / "index.json").exists()


def test_publish_dry_run_changes_nothing(tmp_path):
    approved, published = tmp_path / "approved", tmp_path / "published"
    site = _site(tmp_path)
    week = _approved(approved)

    results = publish_approved(
        _config(site), approved_dir=approved, published_dir=published, dry_run=True
    )

    assert results  # planned actions reported
    assert (approved / week / "devlog.md").exists()  # nothing moved
    assert not (published / week).exists()
    assert not (site / "content/devlog" / f"{week}.md").exists()


def test_publish_missing_site_dir_raises(tmp_path):
    approved, published = tmp_path / "approved", tmp_path / "published"
    _approved(approved)
    with pytest.raises(PublishError):
        publish_approved(
            _config(tmp_path / "nonexistent"), approved_dir=approved, published_dir=published
        )


def test_publish_nothing_when_approved_empty(tmp_path):
    site = _site(tmp_path)
    results = publish_approved(
        _config(site), approved_dir=tmp_path / "approved", published_dir=tmp_path / "published"
    )
    assert results == []


# --- publish-custom -------------------------------------------------------


def test_publish_custom_creates_entry_and_assigns_number(tmp_path):
    """A hand-written .md becomes a published custom entry: H1 → title, filename
    → slug, series from config, status: published, and n assigned by the manifest
    (never hand-set) continuing the series past the existing weekly."""
    site = _site(tmp_path)
    # An existing weekly already holds #1 in the series.
    _weekly(site / "content/devlog", "2026-W27", "Senior SDET log #1: exit codes", "2026-07-05")
    src = tmp_path / "looking-ahead-2036.md"
    src.write_text("# Looking ahead: the SDET role in 2036\n\nMy essay body.\n")

    result = publish_custom(_config(site), src, date="2026-07-10")

    site_file = site / "content/devlog" / "looking-ahead-2036.md"
    assert site_file.exists()
    front, body = parse(site_file.read_text())
    assert front["type"] == "custom"
    assert front["series"] == "Senior SDET log"
    assert front["slug"] == "looking-ahead-2036"
    assert front["title"] == "Looking ahead: the SDET role in 2036"
    assert front["status"] == "published"
    assert front["published_at"] == "2026-07-10"
    assert "n" not in front  # numbering lives in the manifest, not the file
    assert "My essay body." in body  # body carried through verbatim (H1 included)

    # Manifest picked it up with the next series number (weekly #1 → custom #2).
    entries = json.loads((site / "content/devlog" / "index.json").read_text())
    entry = next(e for e in entries if e["slug"] == "looking-ahead-2036")
    assert entry["n"] == 2
    assert result.n == 2
    assert result.series == "Senior SDET log"
    assert result.site_file == site_file


def test_publish_custom_slug_kind_and_date_overrides(tmp_path):
    site = _site(tmp_path)
    src = tmp_path / "draft-note.md"
    src.write_text("# A quick note\n\nBody.\n")

    result = publish_custom(_config(site), src, slug="hello-world", kind="Essay", date="2026-08-01")

    site_file = site / "content/devlog" / "hello-world.md"
    assert site_file.exists()
    assert not (site / "content/devlog" / "draft-note.md").exists()  # --slug wins over filename
    front, _ = parse(site_file.read_text())
    assert front["kind"] == "Essay"
    assert front["published_at"] == "2026-08-01"
    assert result.slug == "hello-world"


def test_publish_custom_kind_omitted_by_default(tmp_path):
    site = _site(tmp_path)
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nBody.\n")

    publish_custom(_config(site), src)

    front, _ = parse((site / "content/devlog" / "note.md").read_text())
    assert "kind" not in front


def test_publish_custom_missing_h1_raises(tmp_path):
    site = _site(tmp_path)
    src = tmp_path / "note.md"
    src.write_text("No heading here, just prose.\n")

    with pytest.raises(PublishError):
        publish_custom(_config(site), src)


def test_publish_custom_missing_site_dir_raises(tmp_path):
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nBody.\n")

    with pytest.raises(PublishError):
        publish_custom(_config(tmp_path / "nonexistent"), src)


def test_publish_custom_rerun_keeps_frozen_number(tmp_path):
    """Re-running for the same slug updates the file in place but keeps the
    already-assigned number (it lives in the manifest, not the file)."""
    site = _site(tmp_path)
    src = tmp_path / "note.md"
    src.write_text("# Note\n\nFirst draft.\n")
    first = publish_custom(_config(site), src)

    # A later weekly lands and would take the next number when the manifest is
    # next regenerated...
    _weekly(site / "content/devlog", "2026-W30", "some subtitle", "2026-07-26")

    # ...but re-publishing the custom (which regenerates the manifest) keeps its
    # original, frozen number while the weekly picks up the next one.
    src.write_text("# Note\n\nSecond draft, fixed a typo.\n")
    second = publish_custom(_config(site), src)

    assert second.n == first.n
    _, body = parse((site / "content/devlog" / "note.md").read_text())
    assert "Second draft" in body
