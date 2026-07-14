from __future__ import annotations

import os
import re

import httpx

GITHUB_API = "https://api.github.com"

# GitHub's closing keywords (close/fix/resolve + inflections) followed by `#N`.
# https://docs.github.com/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue
_CLOSING_KEYWORDS_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s+#(\d+)", re.IGNORECASE
)


def closing_issue_numbers(body: str | None) -> list[int]:
    """Issue numbers a PR body says it closes (deduped, in first-seen order)."""
    seen: list[int] = []
    for match in _CLOSING_KEYWORDS_RE.finditer(body or ""):
        number = int(match.group(1))
        if number not in seen:
            seen.append(number)
    return seen


def timeline_issue_numbers(events: list[dict]) -> list[int]:
    """Issue numbers cross-referenced or connected to a PR, from its timeline
    (deduped, in first-seen order). These are *references*, not closes."""
    seen: list[int] = []
    for event in events:
        if event.get("event") not in ("cross-referenced", "connected"):
            continue
        source = (event.get("source") or {}).get("issue") or {}
        # A cross-reference from another PR is not an issue link; skip PRs.
        if "pull_request" in source:
            continue
        number = source.get("number")
        if isinstance(number, int) and number not in seen:
            seen.append(number)
    return seen


class GitHubError(RuntimeError):
    """Raised on a non-2xx GitHub API response. Never carries the token."""


class GitHubClient:
    """Thin httpx wrapper over the GitHub REST API.

    The token is read from the ``GH_ACTIVITY_TOKEN`` environment variable (or
    passed explicitly for tests) and only ever placed in the ``Authorization``
    header — never in a URL, log line, or exception message. Public repositories
    are readable unauthenticated, so a missing token is not fatal.
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str = GITHUB_API,
        timeout: float = 30.0,
    ) -> None:
        self._token = token if token is not None else os.environ.get("GH_ACTIVITY_TOKEN")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        resp = self._client.get(url, params=params)
        if resp.status_code >= 400:
            try:
                message = resp.json().get("message", "")
            except ValueError:
                message = resp.text
            raise GitHubError(f"GitHub API error {resp.status_code} for {url}: {message}")
        return resp

    def _paginate(self, path: str, params: dict | None = None) -> list[dict]:
        """Follow ``Link: rel="next"`` and flatten results. Handles both list
        endpoints and the search endpoints (which wrap results in ``items``)."""
        results: list[dict] = []
        resp = self._get(path, params=params)
        while True:
            data = resp.json()
            if isinstance(data, dict) and "items" in data:
                results.extend(data["items"])
            elif isinstance(data, list):
                results.extend(data)
            next_url = resp.links.get("next", {}).get("url")
            if not next_url:
                break
            resp = self._get(next_url)
        return results

    def list_commits(self, repo: str, author: str, since: str, until: str) -> list[dict]:
        """List commits by ``author`` in the window. ISO-8601 timestamps.

        Note: this endpoint does not include file stats — call
        :meth:`get_commit` per sha to get the ``files`` array.
        """
        params = {"author": author, "since": since, "until": until, "per_page": 100}
        return self._paginate(f"/repos/{repo}/commits", params)

    def get_commit(self, repo: str, sha: str) -> dict:
        """Fetch a single commit (includes ``files`` with per-file stats)."""
        return self._get(f"/repos/{repo}/commits/{sha}").json()

    def search_pull_requests(self, repo: str, author: str, since: str, until: str) -> list[dict]:
        """PRs authored by ``author`` and created in the window (dates YYYY-MM-DD)."""
        q = f"repo:{repo} type:pr author:{author} created:{since}..{until}"
        return self._paginate("/search/issues", {"q": q, "per_page": 100})

    def search_issues(self, repo: str, assignee: str, since: str, until: str) -> list[dict]:
        """Issues assigned to ``assignee`` and closed in the window (dates)."""
        q = f"repo:{repo} type:issue assignee:{assignee} is:closed closed:{since}..{until}"
        return self._paginate("/search/issues", {"q": q, "per_page": 100})

    # --- v0.3 deep context (fetched only for repos with an active thread) -----

    def list_pr_review_comments(self, repo: str, number: int) -> list[dict]:
        """Inline review comments left on a PR's diff."""
        return self._paginate(f"/repos/{repo}/pulls/{number}/comments", {"per_page": 100})

    def list_pr_reviews(self, repo: str, number: int) -> list[dict]:
        """Review summaries on a PR (each carries a ``state`` and optional body)."""
        return self._paginate(f"/repos/{repo}/pulls/{number}/reviews", {"per_page": 100})

    def list_issue_comments(self, repo: str, number: int) -> list[dict]:
        """Conversation comments on a PR (a PR is an issue for this endpoint)."""
        return self._paginate(f"/repos/{repo}/issues/{number}/comments", {"per_page": 100})

    def list_timeline(self, repo: str, number: int) -> list[dict]:
        """A PR's timeline events (used for cross-referenced/connected issues)."""
        return self._paginate(f"/repos/{repo}/issues/{number}/timeline", {"per_page": 100})

    def get_issue(self, repo: str, number: int) -> dict:
        """Fetch a single issue (for a linked issue's title/state/url)."""
        return self._get(f"/repos/{repo}/issues/{number}").json()
