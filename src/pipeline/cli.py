from __future__ import annotations

import typer

app = typer.Typer(help="portfolio-pipeline: commit → content drafts")


@app.command()
def collect(
    since: str | None = typer.Option(None, help="Start date ISO-8601 (YYYY-MM-DD)"),
    until: str | None = typer.Option(None, help="End date ISO-8601 (YYYY-MM-DD)"),
) -> None:
    """Fetch activity from GitHub for repos on the allowlist."""
    typer.echo("collect: not yet implemented")


@app.command()
def transform() -> None:
    """Run two-stage AI transformation on collected activity, write drafts."""
    typer.echo("transform: not yet implemented")


@app.command()
def run(
    since: str | None = typer.Option(None, help="Start date ISO-8601 (YYYY-MM-DD)"),
    until: str | None = typer.Option(None, help="End date ISO-8601 (YYYY-MM-DD)"),
) -> None:
    """Collect + transform in sequence."""
    typer.echo("run: not yet implemented")


@app.command()
def review() -> None:
    """List drafts awaiting editorial review."""
    typer.echo("review: not yet implemented")


@app.command()
def publish() -> None:
    """Copy approved files into the website repo (manual step, no auto-push)."""
    typer.echo("publish: not yet implemented")
