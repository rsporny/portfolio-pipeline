from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


def add_weeks(week: str, n: int) -> str:
    """Add ``n`` ISO weeks to an ISO-week string (``YYYY-Www``), returning the
    same ``YYYY-Www`` format. Real calendar arithmetic, so year boundaries and
    53-week years are handled correctly."""
    year_str, _, week_str = week.partition("-W")
    monday = date.fromisocalendar(int(year_str), int(week_str), 1)
    iso = (monday + timedelta(weeks=n)).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"

# --- schema (mirrors memory/{org}/{repo}/threads.yaml) ----------------------

AssumptionStatus = Literal["open", "confirmed", "falsified"]
ThreadStatus = Literal["ongoing", "pivoted", "done"]


class Assumption(BaseModel):
    text: str
    # Stamped by apply_mutations when omitted; the indexer model may leave it "".
    made_week: str = ""
    status: AssumptionStatus = "open"
    review_by: str | None = None


class KeyDecision(BaseModel):
    # Stamped by apply_mutations when omitted; the indexer model may leave it "".
    week: str = ""
    decision: str
    rationale: str


class Thread(BaseModel):
    id: str
    title: str
    status: ThreadStatus = "ongoing"
    # Stamped by apply_mutations for new threads; the indexer model may omit them.
    started_week: str = ""
    last_active_week: str = ""
    summary: str = ""
    assumptions: list[Assumption] = Field(default_factory=list)
    key_decisions: list[KeyDecision] = Field(default_factory=list)


class ThreadRegistry(BaseModel):
    threads: list[Thread] = Field(default_factory=list)

    def get(self, thread_id: str) -> Thread | None:
        return next((t for t in self.threads if t.id == thread_id), None)

    @property
    def ids(self) -> set[str]:
        return {t.id for t in self.threads}


# --- indexer mutation schema (the model proposes these; code applies them) --
# SPEC leaves the exact shape open; this is the concrete contract the indexer
# must produce and that apply_mutations validates.


class AssumptionUpdate(BaseModel):
    text: str  # matches an existing assumption on the thread, by exact text
    status: AssumptionStatus


# The indexer proposes assumptions / decisions / threads carrying ONLY the fields
# it genuinely decides. Code-owned or derived fields are deliberately absent from
# the proposal contract so the model cannot set them (hard constraint 6): the week
# (code stamps the current one), a new assumption's status (always "open" —
# confirming/falsifying happens later via assumption_updates on an existing one),
# and the review_by DATE (the model proposes a horizon in weeks; code computes the
# absolute week from it — the model is unreliable at ISO-week arithmetic).
# apply_mutations converts these into the real Assumption / KeyDecision / Thread.


class ProposedAssumption(BaseModel):
    text: str
    # How many weeks from now to revisit this belief; code turns it into an
    # absolute review_by week. A non-positive/absent value means no review.
    review_after_weeks: int | None = None

    def stamp(self, week: str) -> Assumption:
        review_by = (
            add_weeks(week, self.review_after_weeks)
            if self.review_after_weeks and self.review_after_weeks > 0
            else None
        )
        return Assumption(text=self.text, made_week=week, review_by=review_by)


class ProposedKeyDecision(BaseModel):
    decision: str
    rationale: str

    def stamp(self, week: str) -> KeyDecision:
        return KeyDecision(week=week, decision=self.decision, rationale=self.rationale)


class ProposedThread(BaseModel):
    id: str
    title: str
    status: ThreadStatus = "ongoing"
    summary: str = ""
    assumptions: list[ProposedAssumption] = Field(default_factory=list)
    key_decisions: list[ProposedKeyDecision] = Field(default_factory=list)

    def stamp(self, week: str) -> Thread:
        return Thread(
            id=self.id,
            title=self.title,
            status=self.status,
            started_week=week,
            last_active_week=week,
            summary=self.summary,
            assumptions=[a.stamp(week) for a in self.assumptions],
            key_decisions=[d.stamp(week) for d in self.key_decisions],
        )


class ThreadUpdate(BaseModel):
    id: str  # must reference an existing thread
    summary: str | None = None
    status: ThreadStatus | None = None
    assumption_updates: list[AssumptionUpdate] = Field(default_factory=list)
    new_assumptions: list[ProposedAssumption] = Field(default_factory=list)


class IndexerMutations(BaseModel):
    updates: list[ThreadUpdate] = Field(default_factory=list)
    new_threads: list[ProposedThread] = Field(default_factory=list)


class MemoryValidationError(RuntimeError):
    """Raised when a proposed mutation is invalid (unknown id, duplicate, …)."""


# --- paths + IO -------------------------------------------------------------


def repo_memory_dir(root: Path | str, repo: str) -> Path:
    """``root`` + ``owner/name`` → ``root/owner/name`` (nested org → repo)."""
    org, _, name = repo.partition("/")
    if not org or not name:
        raise MemoryValidationError(f"repo must be 'owner/name', got {repo!r}")
    return Path(root) / org / name


def load_context(memory_dir: Path | str) -> str:
    path = Path(memory_dir) / "context.md"
    return path.read_text() if path.exists() else ""


def load_registry(memory_dir: Path | str) -> ThreadRegistry:
    path = Path(memory_dir) / "threads.yaml"
    if not path.exists():
        return ThreadRegistry()
    data = yaml.safe_load(path.read_text()) or {}
    return ThreadRegistry.model_validate(data)


def save_registry(registry: ThreadRegistry, memory_dir: Path | str) -> Path:
    directory = Path(memory_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "threads.yaml"
    dumped = registry.model_dump(mode="json", exclude_none=True)
    path.write_text(yaml.safe_dump(dumped, sort_keys=False, allow_unicode=True))
    return path


# --- deterministic mutation application (code disposes) ---------------------


def apply_mutations(
    registry: ThreadRegistry, mutations: IndexerMutations, *, week: str
) -> ThreadRegistry:
    """Apply the indexer's proposed mutations deterministically, validating as
    we go. Returns a new registry; the input is not modified. Any unknown
    thread id, unknown assumption, or duplicate/colliding new thread raises
    :class:`MemoryValidationError` — nothing partial is committed."""
    result = registry.model_copy(deep=True)
    by_id = {t.id: t for t in result.threads}

    for update in mutations.updates:
        thread = by_id.get(update.id)
        if thread is None:
            raise MemoryValidationError(f"update references unknown thread id {update.id!r}")
        if update.summary is not None:
            thread.summary = update.summary
        if update.status is not None:
            thread.status = update.status
        for change in update.assumption_updates:
            assumption = next((a for a in thread.assumptions if a.text == change.text), None)
            if assumption is None:
                raise MemoryValidationError(
                    f"assumption {change.text!r} not found on thread {update.id!r}"
                )
            assumption.status = change.status
        thread.assumptions.extend(proposed.stamp(week) for proposed in update.new_assumptions)
        thread.last_active_week = week  # touched this week

    seen_new: set[str] = set()
    for new_thread in mutations.new_threads:
        if new_thread.id in by_id or new_thread.id in seen_new:
            raise MemoryValidationError(f"new thread id {new_thread.id!r} already exists")
        seen_new.add(new_thread.id)
        result.threads.append(new_thread.stamp(week))

    return result


def reviews_due(registry: ThreadRegistry, week: str) -> list[tuple[str, Assumption]]:
    """Open assumptions whose ``review_by`` is at or before ``week`` (ISO-week
    strings sort lexically because they're zero-padded, e.g. 2026-W09)."""
    due: list[tuple[str, Assumption]] = []
    for thread in registry.threads:
        for assumption in thread.assumptions:
            if (
                assumption.status == "open"
                and assumption.review_by is not None
                and assumption.review_by <= week
            ):
                due.append((thread.id, assumption))
    return due
