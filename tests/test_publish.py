from __future__ import annotations

import json

import pytest

from pipeline.config import Config, OutputConfig, ReposConfig
from pipeline.frontmatter import dump, parse
from pipeline.publish import PublishError, publish_approved, publish_custom, write_manifest


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


def _custom(site_dir, slug, title, date, *, status="published", **extra):
    (site_dir / f"{slug}.md").write_text(
        dump(
            {
                "type": "custom",
                "title": title,
                "slug": slug,
                "published_at": date,
                "status": status,
                **extra,
            },
            "essay",
        )
    )


def test_write_manifest_new_schema_and_ordering(tmp_path):
    """Every entry carries series/n/slug (no `week`), and entries order by date
    so custom entries interleave with weekly ones."""
    site_dir = tmp_path / "content/devlog"
    site_dir.mkdir(parents=True)
    _weekly(site_dir, "2026-W27", "Senior SDET log #1: exit codes", "2026-07-05")
    _custom(
        site_dir,
        "looking-ahead-2036",
        "Looking ahead: the SDET role in 2036",
        "2026-07-10",
        series="Senior SDET log",
        n=2,
        kind="Essay",
    )

    write_manifest(site_dir, "Senior SDET log")
    entries = json.loads((site_dir / "index.json").read_text())

    assert all({"type", "series", "n", "slug"} <= e.keys() for e in entries)
    assert all("week" not in e for e in entries)
    # Newest by date first — the custom entry (2026-07-10) leads the weekly one.
    assert [e["slug"] for e in entries] == ["looking-ahead-2036", "2026-W27"]
    by_slug = {e["slug"]: e for e in entries}
    assert by_slug["looking-ahead-2036"]["type"] == "custom"
    assert by_slug["looking-ahead-2036"]["kind"] == "Essay"
    assert by_slug["2026-W27"]["type"] == "weekly-activity"
    # Custom carries its own n; weekly n backfills from the legacy "#1" title.
    assert by_slug["looking-ahead-2036"]["n"] == 2
    assert by_slug["2026-W27"]["n"] == 1


def test_write_manifest_series_emitted_and_frozen(tmp_path):
    """Weeklies get the configured current series; an entry's own recorded
    series (front matter / prior manifest) is never rewritten."""
    site_dir = tmp_path / "content/devlog"
    site_dir.mkdir(parents=True)
    # Historical weekly with no series recorded, and one that already has one.
    _weekly(site_dir, "2026-W27", "Senior SDET log #1: exit codes", "2026-07-05")
    _weekly(
        site_dir,
        "2026-W20",
        "Junior tester log #1: fixtures",
        "2026-05-18",
        series="Junior tester log",
    )

    write_manifest(site_dir, "Senior SDET log")
    by_slug = {e["slug"]: e for e in json.loads((site_dir / "index.json").read_text())}

    # Configured current series for the entry that had none...
    assert by_slug["2026-W27"]["series"] == "Senior SDET log"
    # ...but the entry with its own series keeps it even under a new config.
    assert by_slug["2026-W20"]["series"] == "Junior tester log"

    # Rewrite under a *changed* config: recorded series stay put (frozen).
    write_manifest(site_dir, "Principal SDET log")
    by_slug = {e["slug"]: e for e in json.loads((site_dir / "index.json").read_text())}
    assert by_slug["2026-W27"]["series"] == "Senior SDET log"
    assert by_slug["2026-W20"]["series"] == "Junior tester log"


def test_write_manifest_assigns_n_max_plus_one_across_types(tmp_path):
    """`n` is one per-series sequence across weekly + custom: a bare-title
    weekly and a custom without `n` each take max(series n) + 1."""
    site_dir = tmp_path / "content/devlog"
    site_dir.mkdir(parents=True)
    # n=1 recoverable from the title; n=2 from front matter; two need assigning.
    _weekly(site_dir, "2026-W27", "Senior SDET log #1: exit codes", "2026-07-05")
    _custom(site_dir, "looking-ahead", "Looking ahead", "2026-07-10", series="Senior SDET log", n=2)
    _weekly(site_dir, "2026-W28", "no number here", "2026-07-12")  # bare subtitle
    _custom(site_dir, "a-note", "A note", "2026-07-14", series="Senior SDET log")  # no n

    write_manifest(site_dir, "Senior SDET log")
    by_slug = {e["slug"]: e for e in json.loads((site_dir / "index.json").read_text())}

    ns = sorted(e["n"] for e in by_slug.values())
    assert ns == [1, 2, 3, 4]  # a single dense sequence, no gaps or duplicates
    assert by_slug["2026-W28"]["n"] == 3  # oldest of the two unnumbered → 3
    assert by_slug["a-note"]["n"] == 4


def test_write_manifest_n_frozen_across_reruns(tmp_path):
    """Assigned `n` values are stable across repeated runs (idempotent) even
    when a newer, earlier-dated entry appears later."""
    site_dir = tmp_path / "content/devlog"
    site_dir.mkdir(parents=True)
    _weekly(site_dir, "2026-W28", "no number", "2026-07-12")
    write_manifest(site_dir, "Senior SDET log")
    first = {e["slug"]: e["n"] for e in json.loads((site_dir / "index.json").read_text())}
    assert first == {"2026-W28": 1}

    # A backdated entry shows up; the already-assigned n must not be renumbered.
    _weekly(site_dir, "2026-W20", "older week", "2026-05-18")
    write_manifest(site_dir, "Senior SDET log")
    second = {e["slug"]: e["n"] for e in json.loads((site_dir / "index.json").read_text())}
    assert second["2026-W28"] == 1  # frozen despite now being the newer entry
    assert second["2026-W20"] == 2

    # A third run changes nothing.
    write_manifest(site_dir, "Senior SDET log")
    third = {e["slug"]: e["n"] for e in json.loads((site_dir / "index.json").read_text())}
    assert third == second


def test_write_manifest_custom_contract(tmp_path):
    """Custom `.md` files are preserved on disk, mapped per the contract, and
    excluded when not `status: published`."""
    site_dir = tmp_path / "content/devlog"
    site_dir.mkdir(parents=True)
    _custom(
        site_dir,
        "looking-ahead-2036",
        "Looking ahead: the SDET role in 2036",
        "2026-07-10",
        series="Senior SDET log",
        n=2,
        kind="Essay",
    )
    _custom(site_dir, "wip-thoughts", "WIP", "2026-07-20", status="draft", n=9)
    disk_before = (site_dir / "looking-ahead-2036.md").read_text()

    write_manifest(site_dir, "Senior SDET log")
    entries = json.loads((site_dir / "index.json").read_text())

    # The draft custom is excluded; the published one maps field-for-field.
    assert [e["slug"] for e in entries] == ["looking-ahead-2036"]
    entry = entries[0]
    assert entry == {
        "type": "custom",
        "series": "Senior SDET log",
        "n": 2,
        "slug": "looking-ahead-2036",
        "title": "Looking ahead: the SDET role in 2036",
        "date": "2026-07-10",
        "kind": "Essay",
    }
    # Hand-authored files are never rewritten or deleted.
    assert (site_dir / "looking-ahead-2036.md").read_text() == disk_before
    assert (site_dir / "wip-thoughts.md").exists()


def test_write_manifest_custom_kind_defaults_to_omitted(tmp_path):
    """`kind` is passed through only when present (the page defaults to Note)."""
    site_dir = tmp_path / "content/devlog"
    site_dir.mkdir(parents=True)
    _custom(site_dir, "a-note", "A note", "2026-07-14", series="Senior SDET log", n=1)

    write_manifest(site_dir, "Senior SDET log")
    entry = json.loads((site_dir / "index.json").read_text())[0]
    assert "kind" not in entry


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

    # A later weekly publishes and would take the next number...
    _weekly(site / "content/devlog", "2026-W30", "some subtitle", "2026-07-26")
    write_manifest(site / "content/devlog", "Senior SDET log")

    # ...but re-publishing the custom keeps its original number.
    src.write_text("# Note\n\nSecond draft, fixed a typo.\n")
    second = publish_custom(_config(site), src)

    assert second.n == first.n
    _, body = parse((site / "content/devlog" / "note.md").read_text())
    assert "Second draft" in body
