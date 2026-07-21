from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .github import (
    GitHubClient,
    GitHubError,
    closing_issue_numbers,
    timeline_issue_numbers,
)
from .memory import ThreadRegistry, load_registry, repo_memory_dir
from .models import (
    Activity,
    Commit,
    Issue,
    LinkedIssue,
    PullRequest,
    RepoActivity,
    ReviewComment,
)
from .redact import redact_names

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 7

# A GitHub @mention (login rules: 1–39 chars, alphanumeric or single hyphens).
_MENTION_RE = re.compile(r"@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))")
# A `Co-authored-by: Name <email>` commit trailer — capture the display name.
_COAUTHOR_RE = re.compile(r"Co-authored-by:\s*(?P<name>[^<\n]+?)\s*<", re.IGNORECASE)


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


def _has_active_thread(registry: ThreadRegistry) -> bool:
    """Whether a repo has an ongoing arc worth deep-fetching context for (v0.3
    gating). Only ``ongoing`` threads count — ``pivoted``/``done`` are closed."""
    return any(t.status == "ongoing" for t in registry.threads)


def _note_author(raw: dict, github_user: str, participants: set[str]) -> None:
    """Record a comment/review author's login as a third-party name to redact,
    unless it is the owner."""
    login = (raw.get("user") or {}).get("login")
    if login and login.lower() != github_user.lower():
        participants.add(login)


def _fetch_pr_deep_context(
    client: GitHubClient, repo: str, pr: PullRequest, github_user: str, participants: set[str]
) -> tuple[list[ReviewComment], list[LinkedIssue]]:
    """Review summaries, inline review comments, and conversation comments on a
    PR, plus the issues it links. Author logins are recorded in ``participants``
    for redaction; the comment bodies themselves are anonymized later."""
    comments: list[ReviewComment] = []

    for raw in client.list_pr_reviews(repo, pr.number):
        _note_author(raw, github_user, participants)
        if (raw.get("body") or "").strip():  # skip bodiless approvals
            comments.append(ReviewComment.from_api(raw, github_user=github_user, kind="review"))
    for raw in client.list_pr_review_comments(repo, pr.number):
        _note_author(raw, github_user, participants)
        comments.append(ReviewComment.from_api(raw, github_user=github_user, kind="inline"))
    for raw in client.list_issue_comments(repo, pr.number):
        _note_author(raw, github_user, participants)
        comments.append(ReviewComment.from_api(raw, github_user=github_user, kind="conversation"))

    linked = _fetch_linked_issues(client, repo, pr)
    return comments, linked


def _fetch_linked_issues(client: GitHubClient, repo: str, pr: PullRequest) -> list[LinkedIssue]:
    """Issues a PR closes (body closing-keywords) or references (timeline
    cross-references). A closing link wins over a mere reference."""
    closes = closing_issue_numbers(pr.description)
    try:
        references = timeline_issue_numbers(client.list_timeline(repo, pr.number))
    except GitHubError as exc:
        logger.warning("Timeline fetch failed for %s#%d: %s", repo, pr.number, exc)
        references = []
    ordered = [(n, "closes") for n in closes]
    ordered += [(n, "references") for n in references if n not in closes]

    linked: list[LinkedIssue] = []
    for number, relation in ordered:
        try:
            issue = client.get_issue(repo, number)
        except GitHubError as exc:
            logger.warning("Linked-issue fetch failed for %s#%d: %s", repo, number, exc)
            continue
        if "pull_request" in issue:  # the reference was a PR, not an issue
            continue
        linked.append(LinkedIssue.from_api(issue, relation=relation))
    return linked


def _text_participants(text: str, github_user: str) -> set[str]:
    """Third-party names mentioned in free text: @mentions and Co-authored-by
    trailers (the owner is never a third party)."""
    names: set[str] = set()
    for match in _MENTION_RE.finditer(text):
        names.add(match.group(1))
    for match in _COAUTHOR_RE.finditer(text):
        names.add(match.group("name").strip())
    return {n for n in names if n and n.lower() != github_user.lower()}


def _redact_tree(node: object, names: list[str], placeholder: str) -> tuple[object, int]:
    """Recursively mask names in every string leaf of a JSON-shaped tree, leaving
    numbers, booleans, and structure untouched. Redacting the parsed object (not
    its serialized text) means a name can never collide with a bare JSON number
    (e.g. a numeric ``@mention`` vs. a PR ``number``) or eat a structural quote,
    so the round-trip back to :class:`Activity` is always well-formed."""
    if isinstance(node, str):
        return redact_names(node, names, placeholder)
    if isinstance(node, list):
        total = 0
        out_list: list[object] = []
        for item in node:
            redacted, n = _redact_tree(item, names, placeholder)
            out_list.append(redacted)
            total += n
        return out_list, total
    if isinstance(node, dict):
        total = 0
        out_map: dict[object, object] = {}
        for key, value in node.items():
            redacted, n = _redact_tree(value, names, placeholder)
            out_map[key] = redacted
            total += n
        return out_map, total
    return node, 0


def _anonymize(activity: Activity, participants: set[str], config: Config) -> Activity:
    """Mask every third-party name across the assembled activity before it is
    written to the public ``raw/`` (SPEC Module 3, hard constraints 3 & 5). The
    owner's own login is preserved. Returns the activity unchanged when disabled
    or when there is nothing to mask."""
    if not config.redaction.redact_third_party_names:
        return activity
    dumped = activity.model_dump_json()
    names = participants | _text_participants(dumped, config.github_user)
    if not names:
        return activity
    data = activity.model_dump(mode="json")
    redacted, n = _redact_tree(data, sorted(names), config.redaction.role_placeholder)
    if n:
        logger.info("Redacted %d third-party name occurrence(s) before writing raw/", n)
        return Activity.model_validate(redacted)
    return activity


def collect_activity(
    config: Config,
    client: GitHubClient,
    since: str | None = None,
    until: str | None = None,
    memory_root: Path | str | None = None,
) -> Activity:
    """Fetch commits, PRs, and closed issues for every allowlisted repo in the
    window and assemble the versioned :class:`Activity` document.

    Since v0.3, PRs in a repo with an active thread (``memory_root`` →
    ``config.state_dir("memory")``) are enriched with review discussion and
    linked issues, and all third-party names are redacted before the activity is
    returned (and thus before it is written to the public ``raw/``)."""
    since_dt, until_dt = resolve_window(since, until, config.locale.timezone)
    since_iso = since_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    until_iso = until_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    since_date = since_dt.date().isoformat()
    until_date = until_dt.date().isoformat()
    root = Path(memory_root) if memory_root is not None else config.state_dir("memory")
    participants: set[str] = set()

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

        # v0.3 selective deep context: only for repos with an active thread.
        deep = _has_active_thread(load_registry(repo_memory_dir(root, repo)))
        if deep and prs:
            logger.info("Deep context: %s active thread — enriching %d PR(s)", repo, len(prs))
            for pr in prs:
                pr.review_comments, pr.linked_issues = _fetch_pr_deep_context(
                    client, repo, pr, config.github_user, participants
                )

        repos.append(RepoActivity(repo=repo, commits=commits, pull_requests=prs, issues=issues))

    activity = Activity(
        generated_at=datetime.now(UTC),
        since=since_dt,
        until=until_dt,
        week=iso_week(until_dt),
        repos=repos,
    )
    return _anonymize(activity, participants, config)


def write_activity(activity: Activity, raw_dir: Path | str = "raw") -> Path:
    """Write ``activity.json`` under ``raw/YYYY-Wnn/`` (overwriting)."""
    out_dir = Path(raw_dir) / activity.week
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "activity.json"
    out_path.write_text(activity.model_dump_json(indent=2))
    return out_path
