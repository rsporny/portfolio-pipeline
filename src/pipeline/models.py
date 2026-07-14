from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Bump when the on-disk activity.json shape changes.
# v2: added `url` (GitHub html_url) to commits/PRs/issues for proof-of-work links.
# v3: added anonymized deep context on PRs (review_comments, linked_issues),
#     fetched only for repos with an active thread (v0.3 selective deep context).
SCHEMA_VERSION = 3


class FileChange(BaseModel):
    """A single file touched by a commit, with line stats (no diff contents)."""

    filename: str
    additions: int = 0
    deletions: int = 0
    changes: int = 0

    @classmethod
    def from_api(cls, data: dict) -> FileChange:
        return cls(
            filename=data["filename"],
            additions=data.get("additions", 0),
            deletions=data.get("deletions", 0),
            changes=data.get("changes", 0),
        )


class Commit(BaseModel):
    sha: str
    date: datetime
    message: str
    url: str = ""
    files: list[FileChange] = Field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> Commit:
        """Parse a GitHub *single commit* response (the detail endpoint, which
        carries the ``files`` array with stats)."""
        commit = data.get("commit", {}) or {}
        author = commit.get("author", {}) or {}
        return cls(
            sha=data["sha"],
            date=author["date"],
            message=commit.get("message", ""),
            url=data.get("html_url", ""),
            files=[FileChange.from_api(f) for f in data.get("files", [])],
        )


class ReviewComment(BaseModel):
    """A single comment from a PR's review discussion (v0.3 deep context).

    Anonymized by design: ``author_role`` is structural — it lets the model tell
    the owner's own words from a collaborator's without ever carrying a name, and
    the ``body`` is name-redacted before it is persisted to the public ``raw/``.
    Third-party input informs understanding only; it is never quoted (SPEC).
    """

    body: str = ""
    author_role: Literal["owner", "other"] = "other"
    kind: Literal["review", "inline", "conversation"] = "conversation"

    @classmethod
    def from_api(cls, data: dict, *, github_user: str, kind: str) -> ReviewComment:
        login = (data.get("user") or {}).get("login", "") or ""
        role = "owner" if login and login.lower() == github_user.lower() else "other"
        return cls(body=data.get("body") or "", author_role=role, kind=kind)


class LinkedIssue(BaseModel):
    """An issue a PR closes or references (v0.3 deep context). ``relation`` is
    ``closes`` for a closing-keyword link, ``references`` for a cross-reference."""

    number: int
    title: str = ""
    url: str = ""
    state: str = ""
    relation: Literal["closes", "references"] = "references"

    @classmethod
    def from_api(cls, data: dict, *, relation: str) -> LinkedIssue:
        return cls(
            number=data["number"],
            title=data.get("title", ""),
            url=data.get("html_url", ""),
            state=data.get("state", ""),
            relation=relation,  # type: ignore[arg-type]
        )


class PullRequest(BaseModel):
    number: int
    title: str
    description: str = ""
    labels: list[str] = Field(default_factory=list)
    state: str = ""
    url: str = ""
    created_at: datetime | None = None
    merged_at: datetime | None = None
    # v0.3 deep context — populated only for repos with an active thread, empty
    # otherwise (so a schema-2 activity.json still parses unchanged).
    review_comments: list[ReviewComment] = Field(default_factory=list)
    linked_issues: list[LinkedIssue] = Field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict) -> PullRequest:
        """Parse a PR from the search API (``/search/issues`` with ``type:pr``);
        merge state lives in the nested ``pull_request`` object there."""
        labels = [lbl["name"] for lbl in data.get("labels", []) if isinstance(lbl, dict)]
        pr = data.get("pull_request") or {}
        return cls(
            number=data["number"],
            title=data.get("title", ""),
            description=data.get("body") or "",
            labels=labels,
            state=data.get("state", ""),
            url=data.get("html_url", ""),
            created_at=data.get("created_at"),
            merged_at=pr.get("merged_at") or data.get("merged_at"),
        )


class Issue(BaseModel):
    number: int
    title: str
    description: str = ""
    url: str = ""
    closed_at: datetime | None = None

    @classmethod
    def from_api(cls, data: dict) -> Issue:
        return cls(
            number=data["number"],
            title=data.get("title", ""),
            description=data.get("body") or "",
            url=data.get("html_url", ""),
            closed_at=data.get("closed_at"),
        )


class RepoActivity(BaseModel):
    repo: str
    commits: list[Commit] = Field(default_factory=list)
    pull_requests: list[PullRequest] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.commits or self.pull_requests or self.issues)


class Activity(BaseModel):
    """Top-level, versioned schema written to ``raw/YYYY-Wnn/activity.json``."""

    schema_version: int = SCHEMA_VERSION
    generated_at: datetime
    since: datetime
    until: datetime
    week: str
    repos: list[RepoActivity] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return all(repo.is_empty for repo in self.repos)


# --- Transformer output schemas ---------------------------------------------


# How this week's work relates to a known thread (Stage A memory-awareness).
ThreadRelation = Literal["continues", "pivots", "concludes", "contradicts"]


class ThreadRef(BaseModel):
    """A Stage A initiative's link back to a known work thread."""

    id: str
    relation: ThreadRelation


class Initiative(BaseModel):
    """One unit of work from the Stage A technical summary."""

    name: str
    category: str = ""
    what: str
    why_it_matters: str
    tech: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    # Set when this initiative plausibly continues/affects a known thread.
    thread_ref: ThreadRef | None = None


class Initiatives(BaseModel):
    initiatives: list[Initiative]


class Content(BaseModel):
    """Stage B writing output. One channel-neutral social post (the website
    owns per-platform share buttons)."""

    title: str
    devlog: str
    social: str
    highlights: list[str] = Field(default_factory=list)
