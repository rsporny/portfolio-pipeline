from __future__ import annotations

import json

import pytest

from pipeline.config import Config, OutputConfig, ReposConfig
from pipeline.frontmatter import dump, parse
from pipeline.publish import PublishError, publish_approved


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
    assert entries[0]["week"] == week
    assert entries[0]["title"] == "Log #1"
    # publish stamps a publication date (YYYY-MM-DD) that the manifest carries
    assert entries[0]["date"]
    assert "published_at" in parse((published / week / "devlog.md").read_text())[0]


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
