from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# Bump when the on-disk activity.json shape changes.
SCHEMA_VERSION = 1


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
            files=[FileChange.from_api(f) for f in data.get("files", [])],
        )


class PullRequest(BaseModel):
    number: int
    title: str
    description: str = ""
    labels: list[str] = Field(default_factory=list)
    state: str = ""
    created_at: datetime | None = None
    merged_at: datetime | None = None

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
            created_at=data.get("created_at"),
            merged_at=pr.get("merged_at") or data.get("merged_at"),
        )


class Issue(BaseModel):
    number: int
    title: str
    description: str = ""
    closed_at: datetime | None = None

    @classmethod
    def from_api(cls, data: dict) -> Issue:
        return cls(
            number=data["number"],
            title=data.get("title", ""),
            description=data.get("body") or "",
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


class Initiative(BaseModel):
    """One unit of work from the Stage A technical summary."""

    name: str
    what: str
    why_it_matters: str
    tech: list[str] = Field(default_factory=list)


class Initiatives(BaseModel):
    initiatives: list[Initiative]


class Content(BaseModel):
    """Stage B writing output (one document per field)."""

    devlog: str
    linkedin_pl: str
    linkedin_en: str
    highlights: list[str] = Field(default_factory=list)
