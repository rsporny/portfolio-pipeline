from __future__ import annotations

import json

import pytest

from pipeline.frontmatter import dump, parse
from pipeline.site_adapter import (
    AdapterError,
    DevlogEntry,
    RenderContext,
    SiteAdapter,
    SpornyPlAdapter,
    get_adapter,
)

SERIES = "Senior SDET log"


def _adapter() -> SpornyPlAdapter:
    return SpornyPlAdapter()


def _site(tmp_path):
    site_dir = tmp_path / "content/devlog"
    site_dir.mkdir(parents=True)
    return site_dir


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


def _manifest(site_dir, series=SERIES):
    change = _adapter().manifest(site_dir, series)
    assert change.path == site_dir / "index.json"
    return json.loads(change.content)


# --- adapter resolution -----------------------------------------------------


def test_get_adapter_resolves_sporny_pl():
    adapter = get_adapter("sporny_pl")
    assert isinstance(adapter, SpornyPlAdapter)
    assert isinstance(adapter, SiteAdapter)  # satisfies the runtime-checkable protocol


def test_get_adapter_unknown_raises():
    with pytest.raises(AdapterError, match="unknown site adapter"):
        get_adapter("wordpress")


# --- render: weekly ---------------------------------------------------------


def test_render_weekly_writes_devlog_and_manifest(tmp_path):
    """A weekly carries its draft front matter verbatim onto the site and the
    manifest lists it — even on a first publish, before its .md is on disk."""
    site_dir = _site(tmp_path)
    front = {
        "title": "exit codes",
        "status": "published",
        "week": "2026-W27",
        "published_at": "2026-07-05",
    }
    entry = DevlogEntry(slug="2026-W27", body="weekly body", front_matter=front)

    changes = _adapter().render(entry, RenderContext(site_dir, SERIES))

    assert [c.path.name for c in changes] == ["2026-W27.md", "index.json"]
    md = next(c for c in changes if c.path.name == "2026-W27.md")
    parsed_front, body = parse(md.content)
    assert parsed_front == front  # verbatim, nothing dropped
    assert body.strip() == "weekly body"

    manifest = json.loads(next(c for c in changes if c.path.name == "index.json").content)
    entry_out = manifest[0]
    assert entry_out["slug"] == "2026-W27"
    assert entry_out["type"] == "weekly-activity"
    assert entry_out["series"] == SERIES
    assert entry_out["n"] == 1  # first in the series
    assert entry_out["date"] == "2026-07-05"


# --- render: custom ---------------------------------------------------------


def test_render_custom_composes_front_matter(tmp_path):
    site_dir = _site(tmp_path)
    entry = DevlogEntry(
        slug="looking-ahead",
        body="# Looking ahead\n\nEssay body.\n",
        type="custom",
        title="Looking ahead",
        series=SERIES,
        published_at="2026-07-10",
        kind="Essay",
    )

    changes = _adapter().render(entry, RenderContext(site_dir, SERIES))
    md = next(c for c in changes if c.path.name == "looking-ahead.md")
    front, body = parse(md.content)

    # Front matter composed in the manifest's source-schema order.
    assert list(front) == ["type", "series", "slug", "title", "published_at", "status", "kind"]
    assert front["type"] == "custom"
    assert front["status"] == "published"
    assert front["kind"] == "Essay"
    assert "Essay body." in body

    entry_out = json.loads(next(c for c in changes if c.path.name == "index.json").content)[0]
    assert entry_out["slug"] == "looking-ahead"
    assert entry_out["n"] == 1


def test_render_custom_kind_omitted_when_absent(tmp_path):
    site_dir = _site(tmp_path)
    entry = DevlogEntry(
        slug="note",
        body="# Note\n\nx",
        type="custom",
        title="Note",
        series=SERIES,
        published_at="2026-07-10",
    )
    changes = _adapter().render(entry, RenderContext(site_dir, SERIES))
    front, _ = parse(next(c for c in changes if c.path.name == "note.md").content)
    assert "kind" not in front


# --- manifest: schema, ordering, series, numbering (golden) -----------------


def test_manifest_new_schema_and_ordering(tmp_path):
    """Every entry carries series/n/slug (no `week`), and entries order by date
    so custom entries interleave with weekly ones."""
    site_dir = _site(tmp_path)
    _weekly(site_dir, "2026-W27", "Senior SDET log #1: exit codes", "2026-07-05")
    _custom(
        site_dir,
        "looking-ahead-2036",
        "Looking ahead: the SDET role in 2036",
        "2026-07-10",
        series=SERIES,
        n=2,
        kind="Essay",
    )

    entries = _manifest(site_dir)

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


def test_manifest_series_emitted_and_frozen(tmp_path):
    """Weeklies get the configured current series; an entry's own recorded series
    (front matter / prior manifest) is never rewritten."""
    site_dir = _site(tmp_path)
    _weekly(site_dir, "2026-W27", "Senior SDET log #1: exit codes", "2026-07-05")
    _weekly(
        site_dir,
        "2026-W20",
        "Junior tester log #1: fixtures",
        "2026-05-18",
        series="Junior tester log",
    )

    # Persist the first manifest — freezing reads the prior on-disk index.json.
    (site_dir / "index.json").write_text(_adapter().manifest(site_dir, SERIES).content)
    by_slug = {e["slug"]: e for e in _manifest(site_dir)}
    # Configured current series for the entry that had none...
    assert by_slug["2026-W27"]["series"] == SERIES
    # ...but the entry with its own series keeps it even under a new config.
    assert by_slug["2026-W20"]["series"] == "Junior tester log"

    # Rewrite under a *changed* config: recorded series stay put (frozen).
    by_slug = {e["slug"]: e for e in _manifest(site_dir, "Principal SDET log")}
    assert by_slug["2026-W27"]["series"] == SERIES
    assert by_slug["2026-W20"]["series"] == "Junior tester log"


def test_manifest_assigns_n_max_plus_one_across_types(tmp_path):
    """`n` is one per-series sequence across weekly + custom: a bare-title weekly
    and a custom without `n` each take max(series n) + 1."""
    site_dir = _site(tmp_path)
    _weekly(site_dir, "2026-W27", "Senior SDET log #1: exit codes", "2026-07-05")
    _custom(site_dir, "looking-ahead", "Looking ahead", "2026-07-10", series=SERIES, n=2)
    _weekly(site_dir, "2026-W28", "no number here", "2026-07-12")  # bare subtitle
    _custom(site_dir, "a-note", "A note", "2026-07-14", series=SERIES)  # no n

    by_slug = {e["slug"]: e for e in _manifest(site_dir)}
    ns = sorted(e["n"] for e in by_slug.values())
    assert ns == [1, 2, 3, 4]  # a single dense sequence, no gaps or duplicates
    assert by_slug["2026-W28"]["n"] == 3  # oldest of the two unnumbered → 3
    assert by_slug["a-note"]["n"] == 4


def test_manifest_n_frozen_across_reruns(tmp_path):
    """Assigned `n` values are stable across repeated runs (idempotent) even when
    a newer, earlier-dated entry appears later."""
    site_dir = _site(tmp_path)
    _weekly(site_dir, "2026-W28", "no number", "2026-07-12")
    (site_dir / "index.json").write_text(_adapter().manifest(site_dir, SERIES).content)
    first = {e["slug"]: e["n"] for e in _manifest(site_dir)}
    assert first == {"2026-W28": 1}

    # A backdated entry shows up; the already-assigned n must not be renumbered.
    _weekly(site_dir, "2026-W20", "older week", "2026-05-18")
    (site_dir / "index.json").write_text(_adapter().manifest(site_dir, SERIES).content)
    second = {e["slug"]: e["n"] for e in _manifest(site_dir)}
    assert second["2026-W28"] == 1  # frozen despite now being the newer entry
    assert second["2026-W20"] == 2


def test_manifest_custom_contract(tmp_path):
    """Custom `.md` files are preserved on disk, mapped per the contract, and
    excluded when not `status: published`."""
    site_dir = _site(tmp_path)
    _custom(
        site_dir,
        "looking-ahead-2036",
        "Looking ahead: the SDET role in 2036",
        "2026-07-10",
        series=SERIES,
        n=2,
        kind="Essay",
    )
    _custom(site_dir, "wip-thoughts", "WIP", "2026-07-20", status="draft", n=9)
    disk_before = (site_dir / "looking-ahead-2036.md").read_text()

    entries = _manifest(site_dir)

    # The draft custom is excluded; the published one maps field-for-field.
    assert [e["slug"] for e in entries] == ["looking-ahead-2036"]
    assert entries[0] == {
        "type": "custom",
        "series": SERIES,
        "n": 2,
        "slug": "looking-ahead-2036",
        "title": "Looking ahead: the SDET role in 2036",
        "date": "2026-07-10",
        "kind": "Essay",
    }
    # The manifest builder never rewrites or deletes hand-authored files.
    assert (site_dir / "looking-ahead-2036.md").read_text() == disk_before
    assert (site_dir / "wip-thoughts.md").exists()


def test_manifest_custom_kind_defaults_to_omitted(tmp_path):
    site_dir = _site(tmp_path)
    _custom(site_dir, "a-note", "A note", "2026-07-14", series=SERIES, n=1)
    entry = _manifest(site_dir)[0]
    assert "kind" not in entry
