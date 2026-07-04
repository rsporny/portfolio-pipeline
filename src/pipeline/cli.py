from __future__ import annotations

import logging

import typer

from .collect import collect_activity, write_activity
from .config import load_config
from .github import GitHubClient
from .models import Activity

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

app = typer.Typer(help="portfolio-pipeline: commit → content drafts")

CONFIG_OPTION = typer.Option("config.yaml", "--config", help="Path to config.yaml")
SINCE_OPTION = typer.Option(None, help="Start date ISO-8601 (YYYY-MM-DD)")
UNTIL_OPTION = typer.Option(None, help="End date ISO-8601 (YYYY-MM-DD)")


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
def transform() -> None:
    """Run two-stage AI transformation on collected activity, write drafts."""
    typer.echo("transform: not yet implemented (milestone 3)")


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
    typer.echo("transform: not yet implemented (milestone 3)")


@app.command()
def review() -> None:
    """List drafts awaiting editorial review."""
    typer.echo("review: not yet implemented")


@app.command()
def publish() -> None:
    """Copy approved files into the website repo (manual step, no auto-push)."""
    typer.echo("publish: not yet implemented")
