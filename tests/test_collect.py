from __future__ import annotations

import json

import httpx

from pipeline.collect import (
    collect_activity,
    is_allowed,
    iso_week,
    resolve_window,
    write_activity,
)
from pipeline.config import Config, ReposConfig
from pipeline.github import GitHubClient

from .conftest import load_fixture

BASE = "https://api.github.com"


def _config(allowlist=None, user="rsporny"):
    return Config(github_user=user, repos=ReposConfig(allowlist=allowlist or ["o/r"]))


def _router(*, empty: bool = False):
    """Return a pytest-httpx callback that serves fixture data per endpoint."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/o/r/commits":
            return httpx.Response(200, json=[] if empty else load_fixture("commits_list.json"))
        if path.startswith("/repos/o/r/commits/"):
            return httpx.Response(200, json=load_fixture("commit_detail.json"))
        if path == "/search/issues":
            q = request.url.params["q"]
            name = "pr_search.json" if "type:pr" in q else "issue_search.json"
            body = {"total_count": 0, "items": []} if empty else load_fixture(name)
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"message": "unexpected path"})

    return handler


# --- Pure helpers -----------------------------------------------------------


def test_resolve_window_defaults_to_seven_days():
    since, until = resolve_window(None, None, "Europe/Warsaw")
    assert (until - since).days == 7


def test_resolve_window_explicit_dates():
    since, until = resolve_window("2026-07-01", "2026-07-08", "Europe/Warsaw")
    assert since.date().isoformat() == "2026-07-01"
    assert until.date().isoformat() == "2026-07-08"


def test_iso_week_format():
    _, until = resolve_window("2026-07-01", "2026-07-08", "Europe/Warsaw")
    week = iso_week(until)
    assert week.startswith("2026-W")
    assert len(week) == len("2026-W28")


def test_is_allowed_rejects_and_warns(caplog):
    with caplog.at_level("WARNING"):
        assert is_allowed("evil/repo", ["o/r"]) is False
    assert "not on the allowlist" in caplog.text
    assert is_allowed("o/r", ["o/r"]) is True


# --- Orchestration (mocked GitHub) ------------------------------------------


def test_collect_assembles_activity(httpx_mock):
    httpx_mock.add_callback(_router(), is_reusable=True)
    client = GitHubClient(token="t", base_url=BASE)
    activity = collect_activity(_config(), client, "2026-07-01", "2026-07-08")

    assert not activity.is_empty
    assert len(activity.repos) == 1
    repo = activity.repos[0]
    assert repo.repo == "o/r"
    assert len(repo.commits) == 2  # one detail fetch per sha in the list
    assert repo.commits[0].files[0].filename == "src/pipeline/config.py"
    assert len(repo.pull_requests) == 1
    assert len(repo.issues) == 1
    assert activity.schema_version == 1


def test_collect_filters_by_author_and_window(httpx_mock):
    httpx_mock.add_callback(_router(), is_reusable=True)
    client = GitHubClient(token="t", base_url=BASE)
    collect_activity(_config(user="rsporny"), client, "2026-07-01", "2026-07-08")

    commits_req = next(r for r in httpx_mock.get_requests() if r.url.path == "/repos/o/r/commits")
    assert commits_req.url.params["author"] == "rsporny"
    # The commit window is a timezone-aware UTC timestamp (Warsaw midnight → prior
    # day 22:00Z), so assert it is ISO-8601 rather than a naive date prefix.
    assert commits_req.url.params["since"].endswith("Z")

    pr_req = next(
        r
        for r in httpx_mock.get_requests()
        if r.url.path == "/search/issues" and "type:pr" in r.url.params["q"]
    )
    assert "author:rsporny" in pr_req.url.params["q"]
    assert "created:2026-07-01..2026-07-08" in pr_req.url.params["q"]


def test_collect_only_touches_allowlisted_repos(httpx_mock):
    httpx_mock.add_callback(_router(), is_reusable=True)
    client = GitHubClient(token="t", base_url=BASE)
    collect_activity(_config(allowlist=["o/r"]), client, "2026-07-01", "2026-07-08")

    for request in httpx_mock.get_requests():
        # Check the decoded form: repo appears as a "o/r" path or a "repo:o/r"
        # search qualifier (the raw URL percent-encodes the slash).
        decoded = request.url.path + " " + request.url.params.get("q", "")
        assert "o/r" in decoded
        assert "evil" not in decoded


def test_collect_empty_activity_writes_file(httpx_mock, tmp_path):
    httpx_mock.add_callback(_router(empty=True), is_reusable=True)
    client = GitHubClient(token="t", base_url=BASE)
    activity = collect_activity(_config(), client, "2026-07-01", "2026-07-08")

    assert activity.is_empty
    out_path = write_activity(activity, tmp_path)
    assert out_path.exists()
    assert out_path.name == "activity.json"
    assert out_path.parent.name == activity.week


def test_write_activity_roundtrip(httpx_mock, tmp_path):
    httpx_mock.add_callback(_router(), is_reusable=True)
    client = GitHubClient(token="t", base_url=BASE)
    activity = collect_activity(_config(), client, "2026-07-01", "2026-07-08")

    out_path = write_activity(activity, tmp_path)
    data = json.loads(out_path.read_text())
    assert data["schema_version"] == 1
    assert data["week"] == activity.week
    assert data["repos"][0]["repo"] == "o/r"
