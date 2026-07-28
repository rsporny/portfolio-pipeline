from __future__ import annotations

import json

import pytest

from pipeline.config import Config, ReposConfig
from pipeline.frontmatter import dump, parse
from pipeline.site_adapter import (
    AdapterError,
    DevlogEntry,
    RenderContext,
    SiteAdapter,
    SpornyPlAdapter,
    get_adapter,
)

SERIES = "Senior SDET log"  # the default content.devlog_title_prefix


def _adapter() -> SpornyPlAdapter:
    return SpornyPlAdapter()


def _ctx(site_dir):
    # A minimal config; content.devlog_title_prefix defaults to SERIES.
    config = Config(github_user="rsporny", repos=ReposConfig(allowlist=["o/r"]))
    return RenderContext(site_dir=site_dir, config=config)


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


def test_render_weekly_composes_clean_front_matter(tmp_path):
    """A weekly's site front matter is composed by the adapter from the neutral
    entry (never passed through from the draft), so draft-only keys never leak;
    provenance rides in `meta` and is surfaced. The manifest lists the entry even
    on a first publish, before its .md is on disk."""
    site_dir = _site(tmp_path)
    entry = DevlogEntry(
        slug="2026-W27",
        title="exit codes",
        body="weekly body",
        date="2026-07-05",
        type="weekly-activity",
        meta={"source_initiatives": ["Collector"]},
    )

    changes = _adapter().render(entry, _ctx(site_dir))

    assert [c.path.name for c in changes] == ["2026-W27.md", "index.json"]
    front, body = parse(next(c for c in changes if c.path.name == "2026-W27.md").content)
    # Clean, adapter-built shape — no draft-only keys (e.g. generated_at) leak.
    assert front == {
        "type": "weekly-activity",
        "series": SERIES,
        "slug": "2026-W27",
        "title": "exit codes",
        "published_at": "2026-07-05",
        "status": "published",
        "source_initiatives": ["Collector"],
    }
    assert body.strip() == "weekly body"

    entry_out = json.loads(next(c for c in changes if c.path.name == "index.json").content)[0]
    assert entry_out["slug"] == "2026-W27"
    assert entry_out["type"] == "weekly-activity"
    assert entry_out["series"] == SERIES
    assert entry_out["n"] == 1  # first in the series
    assert entry_out["date"] == "2026-07-05"


def test_render_weekly_carries_topics(tmp_path):
    """`topics` in the neutral entry's meta is written into the site front matter as
    block YAML — the page reads it back to draw per-section category dividers."""
    site_dir = _site(tmp_path)
    topics = [
        {"title": "Provenance", "category": "automation", "repo": "rsporny/portfolio-pipeline"},
        {"title": "Guard", "category": "blockchain", "repo": "midnightntwrk/midnight-node"},
    ]
    entry = DevlogEntry(
        slug="2026-W30",
        title="verifiable authorship",
        body="body",
        date="2026-07-26",
        type="weekly-activity",
        meta={"source_initiatives": ["Provenance", "Committee guard"], "topics": topics},
    )

    changes = _adapter().render(entry, _ctx(site_dir))
    front, _ = parse(next(c for c in changes if c.path.name == "2026-W30.md").content)
    assert front["topics"] == topics


def test_render_weekly_omits_topics_when_absent(tmp_path):
    """No `topics` in meta ⇒ no `topics` key in the front matter (graceful fallback)."""
    site_dir = _site(tmp_path)
    entry = DevlogEntry(
        slug="2026-W27",
        title="exit codes",
        body="body",
        date="2026-07-05",
        type="weekly-activity",
        meta={"source_initiatives": ["Collector"]},
    )

    changes = _adapter().render(entry, _ctx(site_dir))
    front, _ = parse(next(c for c in changes if c.path.name == "2026-W27.md").content)
    assert "topics" not in front


# --- render: custom ---------------------------------------------------------


def test_render_custom_composes_front_matter(tmp_path):
    site_dir = _site(tmp_path)
    entry = DevlogEntry(
        slug="looking-ahead",
        title="Looking ahead",
        body="# Looking ahead\n\nEssay body.\n",
        date="2026-07-10",
        type="custom",
        meta={"kind": "Essay"},
    )

    changes = _adapter().render(entry, _ctx(site_dir))
    md = next(c for c in changes if c.path.name == "looking-ahead.md")
    front, body = parse(md.content)

    # Front matter composed in the manifest's source-schema order.
    assert list(front) == ["type", "series", "slug", "title", "published_at", "status", "kind"]
    assert front["type"] == "custom"
    assert front["series"] == SERIES  # default series when the entry has no override
    assert front["status"] == "published"
    assert front["kind"] == "Essay"
    assert "Essay body." in body

    entry_out = json.loads(next(c for c in changes if c.path.name == "index.json").content)[0]
    assert entry_out["slug"] == "looking-ahead"
    assert entry_out["n"] == 1


def test_render_custom_series_override_from_meta(tmp_path):
    """A per-entry series override in meta wins over the configured default."""
    site_dir = _site(tmp_path)
    entry = DevlogEntry(
        slug="old-note",
        title="Old note",
        body="# Old note\n\nx",
        date="2024-01-01",
        type="custom",
        meta={"series": "Junior tester log"},
    )
    changes = _adapter().render(entry, _ctx(site_dir))
    front, _ = parse(next(c for c in changes if c.path.name == "old-note.md").content)
    assert front["series"] == "Junior tester log"


def test_render_custom_kind_omitted_when_absent(tmp_path):
    site_dir = _site(tmp_path)
    entry = DevlogEntry(
        slug="note", title="Note", body="# Note\n\nx", date="2026-07-10", type="custom"
    )
    changes = _adapter().render(entry, _ctx(site_dir))
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


# --- provenance rendering (v0.5) --------------------------------------------


def _apply(changes):
    for change in changes:
        change.path.write_text(change.content)


def _proof(slug="2026-W27"):
    from pipeline.provenance.proof import EntryProof

    return EntryProof(
        slug=slug,
        sha256="ab" * 32,
        signature="-----BEGIN PGP SIGNATURE-----\nfake\n-----END PGP SIGNATURE-----\n",
        sig_filename=f"{slug}.md.sig",
        pubkey_fingerprint="0123456789ABCDEF",
    )


def test_attach_provenance_writes_sidecar_and_badge(tmp_path):
    site_dir = _site(tmp_path)
    _weekly(site_dir, "2026-W27", "Wiring commits", "2026-07-06", type="weekly-activity")

    proof = _proof("2026-W27")
    _apply(_adapter().attach_provenance("2026-W27", proof, _ctx(site_dir)))

    # sidecar written verbatim next to the entry
    assert (site_dir / "2026-W27.md.sig").read_text() == proof.signature
    # manifest carries the badge fields (hash + signer)
    entry = next(
        e for e in json.loads((site_dir / "index.json").read_text()) if e["slug"] == "2026-W27"
    )
    assert entry["signed"] is True
    assert entry["signature"] == "2026-W27.md.sig"
    assert entry["sha256"] == "ab" * 32
    assert entry["pubkey_fingerprint"] == "0123456789ABCDEF"


def test_attach_provenance_never_touches_the_md(tmp_path):
    """The .md's sha256 is the commitment, so provenance must not rewrite it."""
    site_dir = _site(tmp_path)
    _weekly(site_dir, "2026-W27", "Wiring commits", "2026-07-06", type="weekly-activity")
    before = (site_dir / "2026-W27.md").read_bytes()
    _apply(_adapter().attach_provenance("2026-W27", _proof("2026-W27"), _ctx(site_dir)))
    assert (site_dir / "2026-W27.md").read_bytes() == before


def test_attach_provenance_serves_pubkey_when_configured(tmp_path):
    from pipeline.config import Config, ReposConfig, StateConfig

    site_dir = _site(tmp_path)
    _weekly(site_dir, "2026-W27", "Wiring commits", "2026-07-06", type="weekly-activity")
    (tmp_path / "provenance").mkdir()
    (tmp_path / "provenance" / "pubkey.asc").write_text("PUBKEY")
    config = Config(
        github_user="rsporny",
        repos=ReposConfig(allowlist=["o/r"]),
        state=StateConfig(root=str(tmp_path)),
    )
    ctx = RenderContext(site_dir=site_dir, config=config)
    _apply(_adapter().attach_provenance("2026-W27", _proof("2026-W27"), ctx))
    assert (site_dir / "pubkey.asc").read_text() == "PUBKEY"


def test_other_entries_stay_unbadged(tmp_path):
    site_dir = _site(tmp_path)
    _weekly(site_dir, "2026-W27", "Signed one", "2026-07-06", type="weekly-activity")
    _weekly(site_dir, "2026-W28", "Unsigned one", "2026-07-13", type="weekly-activity")

    _apply(_adapter().attach_provenance("2026-W27", _proof("2026-W27"), _ctx(site_dir)))

    entries = {e["slug"]: e for e in json.loads((site_dir / "index.json").read_text())}
    assert entries["2026-W27"].get("signed") is True
    assert "signed" not in entries["2026-W28"]


def test_badge_survives_a_plain_rebuild(tmp_path):
    """Badge fields live in the manifest, not the .md — a later manifest rebuild
    (a plain re-publish) must carry them forward from the frozen prior manifest."""
    site_dir = _site(tmp_path)
    _weekly(site_dir, "2026-W27", "Signed one", "2026-07-06", type="weekly-activity")
    _apply(_adapter().attach_provenance("2026-W27", _proof("2026-W27"), _ctx(site_dir)))

    _apply([_adapter().manifest(site_dir, SERIES)])  # rebuild from disk + frozen
    entry = next(
        e for e in json.loads((site_dir / "index.json").read_text()) if e["slug"] == "2026-W27"
    )
    assert entry["signed"] is True
    assert entry["sha256"] == "ab" * 32


def test_attach_anchor_adds_anchor_badge(tmp_path):
    from pipeline.provenance.log import Anchor

    site_dir = _site(tmp_path)
    _weekly(site_dir, "2026-W27", "Signed one", "2026-07-06", type="weekly-activity")
    _apply(_adapter().attach_provenance("2026-W27", _proof("2026-W27"), _ctx(site_dir)))

    anchor = Anchor(
        backend="cardano", network="preview", tx_id="deadtx", sha256="ab" * 32, anchored_at="t"
    )
    _apply(_adapter().attach_anchor("2026-W27", anchor, _ctx(site_dir)))

    entry = next(
        e for e in json.loads((site_dir / "index.json").read_text()) if e["slug"] == "2026-W27"
    )
    assert entry["signed"] is True  # sign-time fields preserved
    assert entry["signature"] == "2026-W27.md.sig"
    assert entry["anchor"] == {"network": "preview", "tx_id": "deadtx"}
