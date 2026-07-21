from __future__ import annotations

import logging
import sys
from collections.abc import Callable
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
from .publish import PublishError, publish_approved, publish_custom
from .review import list_drafts
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
