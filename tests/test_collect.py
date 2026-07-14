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
from pipeline.memory import Thread, ThreadRegistry, repo_memory_dir, save_registry

from .conftest import load_fixture

BASE = "https://api.github.com"


def _seed_active_thread(memory_root, repo="o/r", status="ongoing"):
    """Write a minimal threads.yaml so a repo counts as having an active thread."""
    registry = ThreadRegistry(threads=[Thread(id="t1", title="Arc", status=status)])
    save_registry(registry, repo_memory_dir(memory_root, repo))


def _deep_router(*, empty: bool = False):
    """Like ``_router`` but also serves the v0.3 deep-context endpoints."""

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
        if path == "/repos/o/r/pulls/5/reviews":
            return httpx.Response(200, json=load_fixture("pr_reviews.json"))
        if path == "/repos/o/r/pulls/5/comments":
            return httpx.Response(200, json=load_fixture("pr_review_comments.json"))
        if path == "/repos/o/r/issues/5/comments":
            return httpx.Response(200, json=load_fixture("issue_comments.json"))
        if path == "/repos/o/r/issues/5/timeline":
            return httpx.Response(200, json=load_fixture("pr_timeline.json"))
        if path in ("/repos/o/r/issues/99", "/repos/o/r/issues/42"):
            number = int(path.rsplit("/", 1)[1])
            return httpx.Response(
                200,
                json={
                    "number": number,
                    "title": f"Issue {number}",
                    "html_url": f"https://github.com/o/r/issues/{number}",
                    "state": "closed",
                },
            )
        return httpx.Response(404, json={"message": f"unexpected path {path}"})

    return handler


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
    assert activity.schema_version == 3
    # No memory → no active thread → no deep context fetched.
    assert repo.pull_requests[0].review_comments == []
    assert repo.pull_requests[0].linked_issues == []


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
    assert data["schema_version"] == 3
    assert data["week"] == activity.week
    assert data["repos"][0]["repo"] == "o/r"


# --- v0.3 selective deep context --------------------------------------------


def test_no_deep_context_without_active_thread(httpx_mock, tmp_path):
    # Empty memory root → no active thread → deep endpoints are never called
    # (the plain router would 404 on them if they were).
    httpx_mock.add_callback(_router(), is_reusable=True)
    client = GitHubClient(token="t", base_url=BASE)
    activity = collect_activity(_config(), client, "2026-07-01", "2026-07-08", memory_root=tmp_path)

    pr = activity.repos[0].pull_requests[0]
    assert pr.review_comments == []
    assert pr.linked_issues == []
    assert not any("/pulls/" in str(r.url) for r in httpx_mock.get_requests())


def test_deep_context_fetched_for_active_thread(httpx_mock, tmp_path):
    _seed_active_thread(tmp_path)
    httpx_mock.add_callback(_deep_router(), is_reusable=True)
    client = GitHubClient(token="t", base_url=BASE)
    activity = collect_activity(_config(), client, "2026-07-01", "2026-07-08", memory_root=tmp_path)

    pr = activity.repos[0].pull_requests[0]
    # review (with body) + 2 inline + 2 conversation = 5; the bodiless approval
    # review is dropped.
    assert len(pr.review_comments) == 5
    kinds = {c.kind for c in pr.review_comments}
    assert kinds == {"review", "inline", "conversation"}
    assert any(c.author_role == "owner" for c in pr.review_comments)
    assert any(c.author_role == "other" for c in pr.review_comments)
    # Two linked issues from the timeline (99, 42), the referenced PR is skipped.
    assert [li.number for li in pr.linked_issues] == [99, 42]
    assert all(li.relation == "references" for li in pr.linked_issues)


def test_deep_context_anonymizes_third_parties(httpx_mock, tmp_path):
    _seed_active_thread(tmp_path)
    httpx_mock.add_callback(_deep_router(), is_reusable=True)
    client = GitHubClient(token="t", base_url=BASE)
    activity = collect_activity(_config(), client, "2026-07-01", "2026-07-08", memory_root=tmp_path)

    dumped = activity.model_dump_json()
    # The @reviewer1 mention in a collaborator's comment is masked; a maintainer's
    # login never leaks; the owner's own @mention survives.
    assert "reviewer1" not in dumped
    assert "maintainer2" not in dumped
    assert "[collaborator]" in dumped
    assert "@rsporny" in dumped


def test_deep_context_off_when_disabled(httpx_mock, tmp_path):
    _seed_active_thread(tmp_path)
    httpx_mock.add_callback(_deep_router(), is_reusable=True)
    cfg = _config()
    cfg.redaction.redact_third_party_names = False
    client = GitHubClient(token="t", base_url=BASE)
    activity = collect_activity(cfg, client, "2026-07-01", "2026-07-08", memory_root=tmp_path)

    # Deep context still fetched, but names are NOT masked when the flag is off.
    dumped = activity.model_dump_json()
    assert "reviewer1" in dumped
