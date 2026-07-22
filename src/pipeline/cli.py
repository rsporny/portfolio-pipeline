from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import typer

from .collect import collect_activity, write_activity
from .config import Config, load_config
from .evals import (
    DEFAULT_CASES_DIR,
    DEFAULT_OUTPUT,
    git_sha,
    has_errors,
    render_scorecard,
    run_cases,
)
from .github import GitHubClient
from .llm import LLMClient, TransformError
from .memory import Thread
from .models import Activity
from .provenance import log as plog
from .provenance import verify as pverify
from .provenance.anchor import AnchorError, get_anchor_backend, receipt_filename
from .provenance.content import PublishedEntry
from .provenance.log import Anchor
from .provenance.sign import GpgError, gpg_signer, pubkey_verifier, sign_entry
from .provenance.sign import fingerprint as gpg_fingerprint
from .publish import PublishError, _resolve_site_dir, publish_approved, publish_custom
from .review import list_drafts
from .site_adapter import RenderContext, get_adapter
from .transform import transform_week

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

app = typer.Typer(help="portfolio-pipeline: commit → content drafts")

CONFIG_OPTION = typer.Option("config.yaml", "--config", help="Path to config.yaml")
SINCE_OPTION = typer.Option(None, help="Start date ISO-8601 (YYYY-MM-DD)")
UNTIL_OPTION = typer.Option(None, help="End date ISO-8601 (YYYY-MM-DD)")
WEEK_OPTION = typer.Option(None, "--week", help="Target ISO week (YYYY-Wnn); default: newest")
FOCUS_OPTION = typer.Option(
    None,
    "--focus",
    help="Thread id to lead the entry on (repeatable). Omit to pick interactively; "
    "a non-interactive run auto-picks.",
)
DRY_RUN_OPTION = typer.Option(False, "--dry-run", help="Preview without writing files")
SITE_REPO_OPTION = typer.Option(
    None, "--site-repo", help="Override output.site_repo_path (e.g. a CI checkout)"
)
SLUG_OPTION = typer.Option(None, "--slug", help="Entry slug (default: the filename)")
KIND_OPTION = typer.Option(None, "--kind", help="Kicker label (default: site shows Note)")
DATE_OPTION = typer.Option(None, "--date", help="Published date YYYY-MM-DD (default: today)")


def _focus_from_flag(focus: list[str], candidates: list[Thread]) -> list[str]:
    """Validate ``--focus`` ids against the threads active this week; a bad id is a
    hard error (exact-id contract) that lists the valid options."""
    ids = {t.id for t in candidates}
    unknown = [f for f in focus if f not in ids]
    if unknown:
        typer.echo(f"unknown --focus thread id(s): {', '.join(unknown)}", err=True)
        typer.echo(f"active this week: {', '.join(sorted(ids)) or '(none)'}", err=True)
        raise typer.Exit(1)
    return focus


def _focus_interactively(candidates: list[Thread]) -> list[str]:
    """Print the threads active this week and let the user pick which lead the
    entry. Empty input means auto (the model picks)."""
    if not candidates:
        return []
    typer.echo(f"\nThreads active this week ({len(candidates)}):")
    for i, thread in enumerate(candidates, 1):
        typer.echo(f"  [{i}] {thread.title} ({thread.id})")
    raw = typer.prompt(
        "Focus which? (comma-separated numbers, empty = let the model pick)",
        default="",
        show_default=False,
    )
    picks: list[str] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        if not part.isdigit() or not 1 <= int(part) <= len(candidates):
            typer.echo(f"ignoring invalid selection: {part!r}", err=True)
            continue
        tid = candidates[int(part) - 1].id
        if tid not in picks:
            picks.append(tid)
    return picks


def _make_focus_selector(focus: list[str] | None) -> Callable[[list[Thread]], list[str]] | None:
    """Resolve how the entry's focus is chosen: an explicit ``--focus`` (validated),
    an interactive prompt on a TTY, or auto (``None``) for non-interactive runs
    such as CI so they never block."""
    if focus:
        return lambda candidates: _focus_from_flag(focus, candidates)
    if sys.stdin.isatty():
        return _focus_interactively
    return None


def _transform(cfg: Config, week: str | None, focus: list[str] | None = None) -> None:
    llm = LLMClient(model=cfg.anthropic.model, max_tokens=cfg.anthropic.max_tokens)
    try:
        out_dir = transform_week(cfg, llm, week=week, focus_selector=_make_focus_selector(focus))
    except (TransformError, FileNotFoundError) as exc:
        typer.echo(f"transform failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote drafts → {out_dir}")


def _report(activity: Activity, out_path: object) -> None:
    if activity.is_empty:
        typer.echo(f"No activity for {activity.week}. Wrote empty file: {out_path}")
        return
    commits = sum(len(r.commits) for r in activity.repos)
    prs = sum(len(r.pull_requests) for r in activity.repos)
    issues = sum(len(r.issues) for r in activity.repos)
    typer.echo(
        f"Collected {commits} commits, {prs} PRs, {issues} issues for {activity.week} → {out_path}"
    )


@app.command()
def collect(
    since: str | None = SINCE_OPTION,
    until: str | None = UNTIL_OPTION,
    config: str = CONFIG_OPTION,
) -> None:
    """Fetch activity from GitHub for repos on the allowlist."""
    cfg = load_config(config)
    with GitHubClient() as client:
        activity = collect_activity(cfg, client, since, until)
    out_path = write_activity(activity, cfg.state_dir("raw"))
    _report(activity, out_path)


@app.command()
def transform(
    week: str | None = WEEK_OPTION,
    focus: list[str] | None = FOCUS_OPTION,
    config: str = CONFIG_OPTION,
) -> None:
    """Run two-stage AI transformation on collected activity, write drafts."""
    _transform(load_config(config), week, focus)


@app.command()
def run(
    since: str | None = SINCE_OPTION,
    until: str | None = UNTIL_OPTION,
    focus: list[str] | None = FOCUS_OPTION,
    config: str = CONFIG_OPTION,
) -> None:
    """Collect + transform in sequence."""
    cfg = load_config(config)
    with GitHubClient() as client:
        activity = collect_activity(cfg, client, since, until)
    out_path = write_activity(activity, cfg.state_dir("raw"))
    _report(activity, out_path)
    _transform(cfg, activity.week, focus)


@app.command()
def review(config: str = CONFIG_OPTION) -> None:
    """List drafts awaiting editorial review."""
    records = list_drafts(load_config(config).state_dir("drafts"))
    if not records:
        typer.echo("No drafts found in drafts/.")
        return
    typer.echo(f"{'WEEK':<10} {'STATUS':<10} {'FILE':<15} TITLE")
    for record in records:
        typer.echo(f"{record.week:<10} {record.status:<10} {record.file:<15} {record.title}")
    typer.echo("\nTo approve: move a week's files into approved/<week>/ and edit freely.")


@app.command()
def publish(
    config: str = CONFIG_OPTION,
    dry_run: bool = DRY_RUN_OPTION,
    site_repo: str | None = SITE_REPO_OPTION,
) -> None:
    """Copy approved files into the website repo (manual step, no auto-push)."""
    cfg = load_config(config)
    try:
        results = publish_approved(cfg, site_repo=site_repo, dry_run=dry_run)
    except PublishError as exc:
        typer.echo(f"publish failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    if not results:
        typer.echo("Nothing to publish (approved/ is empty).")
        return
    verb = "Would publish" if dry_run else "Published"
    for result in results:
        for site_file in result.site_files:
            typer.echo(f"{verb} {result.week} devlog → {site_file}")
        typer.echo(
            f"  {'would move' if dry_run else 'moved'} "
            f"{len(result.published_files)} file(s) to published/{result.week}/"
        )
    if not dry_run:
        typer.echo("\nSite repo not committed or pushed — that stays manual.")


@app.command("publish-custom")
def publish_custom_cmd(
    file: str = typer.Argument(..., help="Markdown file; its first '# H1' is the entry title"),
    slug: str | None = SLUG_OPTION,
    kind: str | None = KIND_OPTION,
    date: str | None = DATE_OPTION,
    config: str = CONFIG_OPTION,
    site_repo: str | None = SITE_REPO_OPTION,
) -> None:
    """Render a hand-written Markdown file into a custom devlog entry in the
    website repo and regenerate the manifest (no auto-push). The per-series
    number is assigned by the pipeline — you never set it by hand."""
    cfg = load_config(config)
    try:
        result = publish_custom(cfg, file, site_repo=site_repo, slug=slug, kind=kind, date=date)
    except PublishError as exc:
        typer.echo(f"publish-custom failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote {result.site_file} → {result.series} #{result.n}")
    typer.echo("Verify locally in the site repo, then commit + merge to publish.")


CASE_OPTION = typer.Option(None, "--case", help="Golden case id to run (repeatable); default all")
CASES_DIR_OPTION = typer.Option(
    str(DEFAULT_CASES_DIR), "--cases-dir", help="Golden cases directory"
)
EVAL_OUT_OPTION = typer.Option(str(DEFAULT_OUTPUT), "--out", help="Scorecard output path")


@app.command("eval")
def eval_cmd(
    case: list[str] | None = CASE_OPTION,
    cases_dir: str = CASES_DIR_OPTION,
    out: str = EVAL_OUT_OPTION,
    config: str = CONFIG_OPTION,
) -> None:
    """Run the transformer over the golden cases and score it with the structural
    checks (v0.4). Writes a Markdown scorecard and exits non-zero if any case has
    an error-severity failure. Requires ANTHROPIC_API_KEY — never runs in unit CI."""
    cfg = load_config(config)
    llm = LLMClient(model=cfg.anthropic.model, max_tokens=cfg.anthropic.max_tokens)
    try:
        cases = run_cases(Path(cases_dir), llm, case_ids=case or None)
    except ValueError as exc:
        typer.echo(f"eval failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    scorecard = render_scorecard(cases, model=cfg.anthropic.model, sha=git_sha())
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(scorecard)

    for case_result in cases:
        status = "BLOCKED" if case_result.blocked else "ok"
        typer.echo(f"  [{status}] {case_result.case_id}: {case_result.description}")
    typer.echo(f"Wrote scorecard → {out_path}")
    if has_errors(cases):
        typer.echo("Eval failed: error-severity check failure(s).", err=True)
        raise typer.Exit(1)


# --- provenance (v0.5): sign entries, anchor the root, verify ----------------

provenance_app = typer.Typer(
    help="Provenance: sign published entries, anchor each entry's hash, and verify."
)
app.add_typer(provenance_app, name="provenance")

WEEK_PROV_OPTION = typer.Option(None, "--week", help="Weekly entry slug (YYYY-Wnn)")
SLUG_PROV_OPTION = typer.Option(None, "--slug", help="Entry slug (weekly or custom)")
CHAIN_OPTION = typer.Option(False, "--chain", help="Also read anchors back from the chain")
BACKEND_OPTION = typer.Option(None, "--backend", help="Override provenance.anchor.backend")


def _resolve_slug(week: str | None, slug: str | None) -> str:
    chosen = slug or week
    if not chosen:
        typer.echo("provide --slug or --week", err=True)
        raise typer.Exit(1)
    return chosen


def _anchor_fetcher(cfg: Config, prov_dir: Path):
    def fetch(anchor: Anchor) -> str | None:
        backend = get_anchor_backend(
            anchor.backend,
            anchors_dir=prov_dir / "anchors",
            metadata_label=cfg.provenance.anchor.metadata_label,
        )
        return backend.fetch(anchor.tx_id, network=anchor.network)

    return fetch


@provenance_app.command("sign")
def provenance_sign(
    week: str | None = WEEK_PROV_OPTION,
    slug: str | None = SLUG_PROV_OPTION,
    config: str = CONFIG_OPTION,
    site_repo: str | None = SITE_REPO_OPTION,
) -> None:
    """Sign a published entry with GPG (run on the PR branch, before merge): sign
    the raw ``<slug>.md`` bytes, write the signature sidecar + public key via the
    adapter, add the verify badge to the manifest, and record the entry in the
    transparency ledger. The entry's ``.md`` is never modified."""
    cfg = load_config(config)
    key = cfg.provenance.signing.gpg_key
    if not key:
        typer.echo("set provenance.signing.gpg_key in config", err=True)
        raise typer.Exit(1)
    entry_slug = _resolve_slug(week, slug)
    try:
        site_dir = _resolve_site_dir(cfg, site_repo)
    except PublishError as exc:
        typer.echo(f"sign failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    md_path = site_dir / f"{entry_slug}.md"
    if not md_path.exists():
        typer.echo(f"no published entry at {md_path}", err=True)
        raise typer.Exit(1)

    entry = PublishedEntry.from_path(md_path)
    prov_dir = cfg.state_dir("provenance")
    try:
        proof = sign_entry(
            prov_dir, entry, signer=gpg_signer(key), fingerprint=gpg_fingerprint(key)
        )
    except GpgError as exc:
        typer.echo(f"signing failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    ctx = RenderContext(site_dir=site_dir, config=cfg)
    for change in get_adapter(cfg.output.adapter).attach_provenance(entry_slug, proof, ctx):
        change.path.write_text(change.content)

    typer.echo(f"Signed {entry_slug} → {proof.sig_filename} (sha256 {proof.sha256[:12]}…)")
    typer.echo(
        "Next: `pipeline provenance anchor` (optional), then commit the entry, its "
        ".md.sig, pubkey.asc, index.json, and provenance/ on the PR branch and merge."
    )


@provenance_app.command("anchor")
def provenance_anchor(
    week: str | None = WEEK_PROV_OPTION,
    slug: str | None = SLUG_PROV_OPTION,
    backend: str | None = BACKEND_OPTION,
    config: str = CONFIG_OPTION,
    site_repo: str | None = SITE_REPO_OPTION,
) -> None:
    """Anchor a signed entry's ``sha256`` on-chain via the configured backend
    (default off). Targets ``--week``/``--slug``, or every not-yet-anchored entry.
    Records the tx in the ledger and refreshes the manifest badge via the adapter."""
    cfg = load_config(config)
    prov_dir = cfg.state_dir("provenance")
    records = plog.load_log(prov_dir)
    if not records:
        typer.echo("nothing to anchor — sign an entry first", err=True)
        raise typer.Exit(1)

    if slug or week:
        target = slug or week
        pending = [r for r in records if r.slug == target]
        if not pending:
            typer.echo(f"no signed entry {target!r} to anchor", err=True)
            raise typer.Exit(1)
    else:
        pending = [r for r in records if r.anchor is None]
        if not pending:
            typer.echo("all signed entries are already anchored.")
            return

    try:
        site_dir = _resolve_site_dir(cfg, site_repo)
    except PublishError as exc:
        typer.echo(f"anchor failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    anchors_dir = prov_dir / "anchors"
    name = backend or cfg.provenance.anchor.backend
    ctx = RenderContext(site_dir=site_dir, config=cfg)
    adapter = get_adapter(cfg.output.adapter)
    try:
        be = get_anchor_backend(
            name, anchors_dir=anchors_dir, metadata_label=cfg.provenance.anchor.metadata_label
        )
        for rec in pending:
            receipt = be.anchor(rec.sha256, network=cfg.provenance.anchor.network, slug=rec.slug)
            anchors_dir.mkdir(parents=True, exist_ok=True)
            (anchors_dir / receipt_filename(receipt.tx_id)).write_text(
                json.dumps(asdict(receipt), indent=2) + "\n"
            )
            anchor = Anchor(**asdict(receipt))
            plog.set_anchor(prov_dir, rec.slug, anchor)
            for change in adapter.attach_anchor(rec.slug, anchor, ctx):
                change.path.write_text(change.content)
            typer.echo(
                f"Anchored {rec.slug} ({rec.sha256[:12]}…) via {receipt.backend} → {receipt.tx_id}"
            )
    except AnchorError as exc:
        typer.echo(f"anchor failed: {exc}", err=True)
        raise typer.Exit(1) from exc


@provenance_app.command("verify")
def provenance_verify(
    chain: bool = CHAIN_OPTION,
    config: str = CONFIG_OPTION,
    site_repo: str | None = SITE_REPO_OPTION,
) -> None:
    """Verify every entry's file hash + signature, and (with ``--chain``) its
    on-chain anchor. Each entry is an independent proof. Exits non-zero on any
    failure."""
    cfg = load_config(config)
    prov_dir = cfg.state_dir("provenance")
    try:
        site_dir = _resolve_site_dir(cfg, site_repo)
    except PublishError as exc:
        typer.echo(f"verify failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    pubkey_path = cfg.state.root_path / cfg.provenance.public_key
    if not pubkey_path.exists():
        typer.echo(f"public key not found: {pubkey_path}", err=True)
        raise typer.Exit(1)

    report = pverify.verify_all(
        prov_dir,
        site_dir,
        verifier=pubkey_verifier(pubkey_path.read_text()),
        anchor_fetch=_anchor_fetcher(cfg, prov_dir) if chain else None,
    )
    typer.echo(pverify.render(report))
    if not report.ok:
        raise typer.Exit(1)


@provenance_app.command("show")
def provenance_show(
    slug: str | None = SLUG_PROV_OPTION,
    config: str = CONFIG_OPTION,
) -> None:
    """Print each entry's file hash, signature, and on-chain anchor."""
    cfg = load_config(config)
    prov_dir = cfg.state_dir("provenance")
    records = plog.load_log(prov_dir)
    if not records:
        typer.echo("transparency ledger is empty (no signed entries yet).")
        return

    chosen = [r for r in records if r.slug == slug] if slug else records
    if slug and not chosen:
        typer.echo(f"no entry for slug {slug!r}", err=True)
        raise typer.Exit(1)

    for r in chosen:
        typer.echo(f"{r.slug}  sha256={r.sha256}")
        typer.echo(f"  sig:    {r.sig}")
        if r.anchor:
            a = r.anchor
            typer.echo(f"  anchor: {a.backend}/{a.network} {a.tx_id}")
        else:
            typer.echo("  anchor: (none)")
