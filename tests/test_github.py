from __future__ import annotations

import pytest

from pipeline.github import GitHubClient, GitHubError
from pipeline.models import Commit, Issue, PullRequest

from .conftest import load_fixture

BASE = "https://api.github.com"


# --- Response parsing (fixtures, no network) --------------------------------


def test_commit_from_api_parses_stats():
    commit = Commit.from_api(load_fixture("commit_detail.json"))
    assert commit.sha == "abc123"
    assert commit.message.startswith("Add config loader")
    assert commit.date.year == 2026
    assert commit.url == "https://github.com/o/r/commit/abc123"
    assert len(commit.files) == 1
    assert commit.files[0].filename == "src/pipeline/config.py"
    assert commit.files[0].changes == 45


def test_pull_request_from_api_parses_labels_and_merge():
    item = load_fixture("pr_search.json")["items"][0]
    pr = PullRequest.from_api(item)
    assert pr.number == 5
    assert pr.labels == ["enhancement", "milestone-2"]
    assert pr.merged_at is not None
    assert pr.url == "https://github.com/o/r/pull/5"
    assert pr.description.startswith("Fetches commits")


def test_issue_from_api():
    item = load_fixture("issue_search.json")["items"][0]
    issue = Issue.from_api(item)
    assert issue.number == 7
    assert issue.title == "Flaky test in CI"
    assert issue.url == "https://github.com/o/r/issues/7"
    assert issue.closed_at is not None


# --- Client behaviour (mocked httpx) ----------------------------------------


def test_list_commits_sends_auth_and_filters(httpx_mock):
    httpx_mock.add_response(json=load_fixture("commits_list.json"))
    client = GitHubClient(token="secret-token", base_url=BASE)
    result = client.list_commits("o/r", "rsporny", "2026-07-01T00:00:00Z", "2026-07-08T00:00:00Z")

    assert [c["sha"] for c in result] == ["abc123", "def456"]
    request = httpx_mock.get_requests()[0]
    # Token travels only in the header, never in the URL.
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in str(request.url)
    assert request.url.params["author"] == "rsporny"
    assert request.url.params["since"] == "2026-07-01T00:00:00Z"


def test_search_pull_requests_builds_query(httpx_mock):
    httpx_mock.add_response(json=load_fixture("pr_search.json"))
    client = GitHubClient(token="t", base_url=BASE)
    client.search_pull_requests("o/r", "rsporny", "2026-07-01", "2026-07-08")

    q = httpx_mock.get_requests()[0].url.params["q"]
    assert "repo:o/r" in q
    assert "type:pr" in q
    assert "author:rsporny" in q
    assert "created:2026-07-01..2026-07-08" in q


def test_search_issues_builds_query(httpx_mock):
    httpx_mock.add_response(json=load_fixture("issue_search.json"))
    client = GitHubClient(token="t", base_url=BASE)
    client.search_issues("o/r", "rsporny", "2026-07-01", "2026-07-08")

    q = httpx_mock.get_requests()[0].url.params["q"]
    assert "type:issue" in q
    assert "assignee:rsporny" in q
    assert "is:closed" in q
    assert "closed:2026-07-01..2026-07-08" in q


def test_error_response_raises_without_token(httpx_mock):
    httpx_mock.add_response(status_code=404, json={"message": "Not Found"})
    client = GitHubClient(token="secret-token", base_url=BASE)
    with pytest.raises(GitHubError) as excinfo:
        client.get_commit("o/r", "deadbeef")
    assert "404" in str(excinfo.value)
    assert "secret-token" not in str(excinfo.value)


# --- token from environment -------------------------------------------------


def test_client_reads_token_from_gh_activity_token_env(monkeypatch):
    monkeypatch.setenv("GH_ACTIVITY_TOKEN", "env-token")
    client = GitHubClient(base_url=BASE)
    assert client._client.headers["Authorization"] == "Bearer env-token"


def test_client_is_unauthenticated_without_token(monkeypatch):
    monkeypatch.delenv("GH_ACTIVITY_TOKEN", raising=False)
    client = GitHubClient(base_url=BASE)
    assert "Authorization" not in client._client.headers
