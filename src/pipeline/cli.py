from __future__ import annotations

import logging

import typer

from .collect import collect_activity, write_activity
from .config import Config, load_config
from .github import GitHubClient
from .llm import LLMClient, TransformError
from .models import Activity
from .publish import PublishError, publish_approved
from .review import list_drafts
from .transform import transform_week

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

app = typer.Typer(help="portfolio-pipeline: commit → content drafts")

CONFIG_OPTION = typer.Option("config.yaml", "--config", help="Path to config.yaml")
SINCE_OPTION = typer.Option(None, help="Start date ISO-8601 (YYYY-MM-DD)")
UNTIL_OPTION = typer.Option(None, help="End date ISO-8601 (YYYY-MM-DD)")
WEEK_OPTION = typer.Option(None, "--week", help="Target ISO week (YYYY-Wnn); default: newest")
DRY_RUN_OPTION = typer.Option(False, "--dry-run", help="Preview without writing files")
SITE_REPO_OPTION = typer.Option(
    None, "--site-repo", help="Override output.site_repo_path (e.g. a CI checkout)"
)


def _transform(cfg: Config, week: str | None) -> None:
    llm = LLMClient(model=cfg.anthropic.model, max_tokens=cfg.anthropic.max_tokens)
    try:
        out_dir = transform_week(cfg, llm, week=week)
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
    out_path = write_activity(activity)
    _report(activity, out_path)


@app.command()
def transform(week: str | None = WEEK_OPTION, config: str = CONFIG_OPTION) -> None:
    """Run two-stage AI transformation on collected activity, write drafts."""
    _transform(load_config(config), week)


@app.command()
def run(
    since: str | None = SINCE_OPTION,
    until: str | None = UNTIL_OPTION,
    config: str = CONFIG_OPTION,
) -> None:
    """Collect + transform in sequence."""
    cfg = load_config(config)
    with GitHubClient() as client:
        activity = collect_activity(cfg, client, since, until)
    out_path = write_activity(activity)
    _report(activity, out_path)
    _transform(cfg, activity.week)


@app.command()
def review() -> None:
    """List drafts awaiting editorial review."""
    records = list_drafts()
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
