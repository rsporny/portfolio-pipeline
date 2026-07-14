from __future__ import annotations

from pipeline.models import SCHEMA_VERSION, Activity, LinkedIssue, PullRequest, ReviewComment


def test_schema_version_is_3():
    assert SCHEMA_VERSION == 3


def test_schema_2_activity_still_parses():
    """A pre-v0.3 activity.json (no deep-context fields, schema_version 2) must
    still validate — the new PR fields default to empty."""
    legacy = {
        "schema_version": 2,
        "generated_at": "2026-01-01T00:00:00Z",
        "since": "2025-12-25T00:00:00Z",
        "until": "2026-01-01T00:00:00Z",
        "week": "2026-W01",
        "repos": [
            {
                "repo": "acme/widget",
                "commits": [],
                "pull_requests": [{"number": 7, "title": "Add thing", "url": "https://x/pr/7"}],
                "issues": [],
            }
        ],
    }
    activity = Activity.model_validate(legacy)
    assert activity.schema_version == 2
    pr = activity.repos[0].pull_requests[0]
    assert pr.review_comments == []
    assert pr.linked_issues == []


def test_deep_context_fields_round_trip():
    pr = PullRequest(
        number=7,
        title="Add thing",
        review_comments=[
            ReviewComment(body="lgtm", author_role="other", kind="review"),
            ReviewComment(body="fixed", author_role="owner", kind="conversation"),
        ],
        linked_issues=[LinkedIssue(number=3, title="Bug", url="https://x/3", relation="closes")],
    )
    reparsed = PullRequest.model_validate_json(pr.model_dump_json())
    assert reparsed.review_comments[0].author_role == "other"
    assert reparsed.review_comments[1].kind == "conversation"
    assert reparsed.linked_issues[0].relation == "closes"


def test_review_comment_from_api_marks_owner_role():
    owner = ReviewComment.from_api(
        {"user": {"login": "rsporny"}, "body": "done"}, github_user="rsporny", kind="conversation"
    )
    other = ReviewComment.from_api(
        {"user": {"login": "Reviewer1"}, "body": "nit"}, github_user="rsporny", kind="inline"
    )
    assert owner.author_role == "owner"
    assert other.author_role == "other"
    assert other.kind == "inline"


def test_linked_issue_from_api():
    li = LinkedIssue.from_api(
        {"number": 42, "title": "Crash", "html_url": "https://x/42", "state": "closed"},
        relation="closes",
    )
    assert li.number == 42
    assert li.url == "https://x/42"
    assert li.relation == "closes"
