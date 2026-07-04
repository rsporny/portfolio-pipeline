from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .github import GitHubClient
from .models import Activity, Commit, Issue, PullRequest, RepoActivity

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 7


def _parse_date(value: str, tz: ZoneInfo, *, end_of_day: bool = False) -> datetime:
    dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=tz)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def resolve_window(since: str | None, until: str | None, tz_name: str) -> tuple[datetime, datetime]:
    """Resolve the collection window. Defaults to the last 7 days ending now,
    in the configured timezone. ``since``/``until`` are ISO dates (YYYY-MM-DD)."""
    tz = ZoneInfo(tz_name)
    until_dt = _parse_date(until, tz, end_of_day=True) if until else datetime.now(tz)
    since_dt = _parse_date(since, tz) if since else until_dt - timedelta(days=DEFAULT_WINDOW_DAYS)
    return since_dt, until_dt


def iso_week(dt: datetime) -> str:
    """ISO week label, e.g. ``2026-W27``."""
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def is_allowed(repo: str, allowlist: list[str]) -> bool:
    """Allowlist guard (default deny). A non-listed repo is skipped with a warning."""
    if repo not in allowlist:
        logger.warning("Repository %s is not on the allowlist; skipping", repo)
        return False
    return True


def collect_activity(
    config: Config,
    client: GitHubClient,
    since: str | None = None,
    until: str | None = None,
) -> Activity:
    """Fetch commits, PRs, and closed issues for every allowlisted repo in the
    window and assemble the versioned :class:`Activity` document."""
    since_dt, until_dt = resolve_window(since, until, config.locale.timezone)
    since_iso = since_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    until_iso = until_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    since_date = since_dt.date().isoformat()
    until_date = until_dt.date().isoformat()

    repos: list[RepoActivity] = []
    for repo in config.repos.allowlist:
        if not is_allowed(repo, config.repos.allowlist):
            continue

        commits = [
            Commit.from_api(client.get_commit(repo, raw["sha"]))
            for raw in client.list_commits(repo, config.github_user, since_iso, until_iso)
        ]
        prs = [
            PullRequest.from_api(d)
            for d in client.search_pull_requests(repo, config.github_user, since_date, until_date)
        ]
        issues = [
            Issue.from_api(d)
            for d in client.search_issues(repo, config.github_user, since_date, until_date)
        ]
        repos.append(RepoActivity(repo=repo, commits=commits, pull_requests=prs, issues=issues))

    return Activity(
        generated_at=datetime.now(UTC),
        since=since_dt,
        until=until_dt,
        week=iso_week(until_dt),
        repos=repos,
    )


def write_activity(activity: Activity, raw_dir: Path | str = "raw") -> Path:
    """Write ``activity.json`` under ``raw/YYYY-Wnn/`` (overwriting)."""
    out_dir = Path(raw_dir) / activity.week
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "activity.json"
    out_path.write_text(activity.model_dump_json(indent=2))
    return out_path
