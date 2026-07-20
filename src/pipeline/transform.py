from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .checks import CheckResult, build_context, check_content, check_initiatives, failures
from .config import Config
from .llm import LLMClient, TransformError
from .memory import (
    Assumption,
    IndexerMutations,
    MemoryValidationError,
    Thread,
    ThreadRegistry,
    apply_mutations,
    load_context,
    load_registry,
    repo_memory_dir,
    reviews_due,
    save_registry,
    weeks_between,
)
from .models import Activity, Content, Initiatives
from .prompts import indexer_prompt, stage_a_prompt, stage_b_prompt
from .redact import redact

logger = logging.getLogger(__name__)


def find_latest_activity(raw_dir: Path | str = "raw") -> Path:
    """Return the newest ``raw/*/activity.json`` (ISO week sorts lexically)."""
    candidates = sorted(Path(raw_dir).glob("*/activity.json"))
    if not candidates:
        raise FileNotFoundError(f"No activity.json found under {raw_dir}/ — run `collect` first")
    return candidates[-1]


@dataclass
class RepoMemory:
    """A repo's memory as loaded for a transform run: its directory, its
    hand-written context card, and its (possibly updated) thread registry."""

    repo: str
    memory_dir: Path
    context: str
    registry: ThreadRegistry
    changed: bool = False


def _repo_context(config: Config, activity: Activity) -> str:
    """Domain descriptions for the repos present in this week's activity."""
    descriptions = config.repos.descriptions
    lines: list[str] = []
    seen: set[str] = set()
    for repo_activity in activity.repos:
        repo = repo_activity.repo
        desc = descriptions.get(repo)
        if desc and repo not in seen:
            seen.add(repo)
            lines.append(f"- {repo}: {desc}")
    return "\n".join(lines)


def _load_repo_memories(activity: Activity, memory_root: Path) -> list[RepoMemory]:
    """Load context + thread registry for every repo with activity this week."""
    memories: list[RepoMemory] = []
    for repo_activity in activity.repos:
        repo = repo_activity.repo
        memory_dir = repo_memory_dir(memory_root, repo)
        memories.append(
            RepoMemory(
                repo=repo,
                memory_dir=memory_dir,
                context=load_context(memory_dir),
                registry=load_registry(memory_dir),
            )
        )
    return memories


def _render_memory_context(memories: list[RepoMemory]) -> str:
    """A compact, human-readable memory brief for the Stage A prompt: each repo's
    context card plus its current threads (id, status, summary, open
    assumptions). Empty when there is nothing to connect to."""
    blocks: list[str] = []
    for mem in memories:
        if not mem.context and not mem.registry.threads:
            continue
        lines = [f"## {mem.repo}"]
        if mem.context:
            lines.append(mem.context.strip())
        if mem.registry.threads:
            lines.append("Threads:")
            for thread in mem.registry.threads:
                lines.append(f"- id: {thread.id} | status: {thread.status} | {thread.title}")
                if thread.summary:
                    lines.append(f"  summary: {thread.summary}")
                open_assumptions = [a for a in thread.assumptions if a.status == "open"]
                for assumption in open_assumptions:
                    lines.append(
                        f"  assumption ({assumption.status}, made {assumption.made_week}): "
                        f"{assumption.text}"
                    )
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    header = (
        "Repository memory (known work threads — connect this week's work to them "
        "via thread_ref where it genuinely continues or affects one):"
    )
    return header + "\n\n" + "\n\n".join(blocks)


def _find_thread(memories: list[RepoMemory], thread_id: str) -> Thread | None:
    for mem in memories:
        thread = mem.registry.get(thread_id)
        if thread is not None:
            return thread
    return None


def _thread_timing(thread: Thread, week: str) -> str:
    """How a referenced thread sits in time relative to the entry's own week. A
    thread that began this week is being introduced now (present tense, not
    history); only an older thread warrants "started N weeks ago" continuity."""
    if thread.started_week == week or not thread.started_week:
        return (
            "New this week — introduce it in the present; this is its first entry, "
            "not prior history."
        )
    ago = weeks_between(thread.started_week, week)
    span = "1 week ago" if ago == 1 else f"{ago} weeks ago"
    return f"Started {thread.started_week} ({span})"


def _render_thread_context(
    initiatives: Initiatives,
    memories: list[RepoMemory],
    due: list[tuple[str, Assumption]],
    week: str,
) -> str:
    """Stage B thread brief: the threads this week's initiatives reference (with
    the stated relation) plus any assumptions now due for review."""
    lines: list[str] = []

    seen: set[str] = set()
    referenced: list[str] = []
    for init in initiatives.initiatives:
        ref = init.thread_ref
        if ref is None:
            continue
        thread = _find_thread(memories, ref.id)
        if thread is None or thread.id in seen:
            continue
        seen.add(thread.id)
        referenced.append(thread.id)
        lines.append(
            f'- "{thread.title}" (id: {thread.id}) — this week {ref.relation} it. '
            f"{_thread_timing(thread, week)}, status {thread.status}."
        )
        if thread.summary:
            lines.append(f"  Where it stood: {thread.summary}")
        for assumption in thread.assumptions:
            lines.append(
                f"  Assumption [{assumption.status}, made {assumption.made_week}]: "
                f"{assumption.text}"
            )

    due_lines: list[str] = []
    for thread_id, assumption in due:
        review = f", review_by {assumption.review_by}" if assumption.review_by else ""
        due_lines.append(
            f'- {thread_id}: "{assumption.text}" (made {assumption.made_week}{review})'
        )

    if not lines and not due_lines:
        return ""

    blocks: list[str] = []
    if lines:
        blocks.append(
            "Thread context (weave continuity into the writing; do not force it):\n"
            + "\n".join(lines)
        )
    if due_lines:
        blocks.append(
            "Assumptions now due for review (revisit whether they still hold):\n"
            + "\n".join(due_lines)
        )
    return "\n\n".join(blocks)


def _active_threads(memories: list[RepoMemory], week: str) -> list[Thread]:
    """Threads the indexer created or touched this week — the candidates a human
    can pick from to focus the entry (``apply_mutations`` stamps
    ``last_active_week = week`` on every thread it creates or touches)."""
    return [t for mem in memories for t in mem.registry.threads if t.last_active_week == week]


def _render_focus(selected: list[Thread]) -> str:
    """A Stage B directive naming the thread(s) the entry covers, in the caller's
    chosen order — the first is the primary lead. Empty when nothing is selected —
    the model then picks the lead itself over the whole week (default)."""
    if not selected:
        return ""
    listed = "\n".join(f'{i}. "{t.title}"' for i, t in enumerate(selected, 1))
    primary = selected[0].title
    return (
        "Focus directive — the entry covers ONLY these threads, in this order (the "
        "first is the primary):\n"
        f"{listed}\n\n"
        f'The title and opening center on the primary, "{primary}". Give each listed '
        "thread its own section (the primary the deepest), and end each section with "
        "that work's own proof-of-work link taken from the matching initiative below. "
        "Do NOT write up any initiative that is not listed here. Do not strain to tie "
        "the topics into one narrative — they can simply be separate things done this "
        "week."
    )


def _write_failed(out_dir: Path, raw: str, name: str = "_failed_raw.txt") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(raw)
    logger.error("Saved raw model response to %s", path)
    return path


def _generate(llm: LLMClient, prompt: str, model_cls: type[BaseModel], out_dir: Path) -> BaseModel:
    """Call the model, parse JSON, and validate against ``model_cls``. On any
    failure, persist the raw response to ``_failed_raw.txt`` and re-raise."""
    try:
        data, raw = llm.complete_json(prompt)
    except TransformError as exc:
        _write_failed(out_dir, exc.raw or "")
        raise
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        _write_failed(out_dir, raw)
        raise TransformError(
            f"{model_cls.__name__} schema validation failed: {exc}", raw=raw
        ) from exc


def _run_indexer(
    llm: LLMClient,
    initiatives: Initiatives,
    mem: RepoMemory,
    week: str,
    phrases: list[str],
    out_dir: Path,
) -> None:
    """Propose + deterministically apply memory mutations for one repo. The model
    proposes, code disposes (validated by :func:`apply_mutations`). An indexer
    failure must NOT block Stage B: on any error we keep the previous memory and
    log a warning (SPEC Module 3 / hard constraint 6)."""
    redacted_inits, _ = redact(initiatives.model_dump_json(indent=2), phrases)
    redacted_threads, _ = redact(mem.registry.model_dump_json(indent=2), phrases)
    prompt = indexer_prompt(mem.repo, redacted_inits, redacted_threads)
    try:
        data, raw = llm.complete_json(prompt)
        mutations = IndexerMutations.model_validate(data)
        updated = apply_mutations(mem.registry, mutations, week=week)
    except (TransformError, ValidationError, MemoryValidationError) as exc:
        _write_failed(out_dir, getattr(exc, "raw", None) or str(exc), "_indexer_failed_raw.txt")
        logger.warning("Indexer failed for %s: %s — keeping previous memory", mem.repo, exc)
        return
    if mutations.updates or mutations.new_threads:
        mem.registry = updated
        mem.changed = True


def _render_checks(results: list[CheckResult]) -> str:
    """A human-readable checks report for the draft bundle. Ordered errors first
    so a reviewer sees the blocking violations at the top."""
    lines = ["# Content checks", ""]
    ranked = sorted(results, key=lambda r: (r.passed, r.severity != "error"))
    for r in ranked:
        mark = "✓" if r.passed else ("✗" if r.severity == "error" else "!")
        detail = f" — {r.detail}" if r.detail else ""
        lines.append(f"- {mark} [{r.severity}] {r.name}{detail}")
    return "\n".join(lines) + "\n"


def _run_checks(
    content: Content,
    initiatives: Initiatives,
    activity: Activity,
    config: Config,
    thread_ids: set[str],
    out_dir: Path,
) -> list[CheckResult]:
    """Structural content-policy checks on the generated output (SPEC line 17).
    Persists the report (``checks.md`` for humans, ``checks.json`` for the eval
    runner), logs ``warn``-severity issues, and returns the results. It does NOT
    decide policy — the caller enforces (production blocks on ``error``, the eval
    runner only tallies)."""
    ctx = build_context(
        activity,
        forbidden_phrases=config.redaction.forbidden_phrases,
        placeholder=config.redaction.role_placeholder,
        thread_ids=thread_ids,
    )
    results = check_content(content, ctx) + check_initiatives(initiatives, ctx)
    (out_dir / "checks.md").write_text(_render_checks(results))
    (out_dir / "checks.json").write_text(json.dumps([asdict(r) for r in results], indent=2))
    for warning in failures(results, "warn"):
        logger.warning("Check %s: %s", warning.name, warning.detail)
    return results


def _front_matter(week: str, generated_at: str, title: str, source_initiatives: list[str]) -> str:
    lines = [
        "---",
        f"title: {json.dumps(title)}",
        "status: draft",
        f"week: {week}",
        f"generated_at: {generated_at}",
        "source_initiatives:",
    ]
    lines += [f"  - {json.dumps(name)}" for name in source_initiatives]
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _render_summary(week: str, initiatives: Initiatives) -> str:
    lines = [f"# Technical summary — {week}", ""]
    for init in initiatives.initiatives:
        heading = f"## {init.name}"
        if init.category:
            heading += f"  ·  _{init.category}_"
        lines += [
            heading,
            "",
            f"**What:** {init.what}",
            "",
            f"**Why it matters:** {init.why_it_matters}",
            "",
            f"**Tech:** {', '.join(init.tech) if init.tech else '—'}",
            "",
        ]
        if init.thread_ref:
            lines += [
                f"**Thread:** {init.thread_ref.id} ({init.thread_ref.relation})",
                "",
            ]
        if init.links:
            lines += [f"**Links:** {', '.join(init.links)}", ""]
    return "\n".join(lines).rstrip() + "\n"


def transform_week(
    config: Config,
    llm: LLMClient,
    raw_dir: Path | str = "raw",
    drafts_dir: Path | str = "drafts",
    week: str | None = None,
    memory_root: Path | str | None = None,
    focus_selector: Callable[[list[Thread]], list[str]] | None = None,
    enforce_checks: bool = True,
) -> Path:
    """Run redaction → Stage A (memory-aware) → indexer → Stage B (thread-aware)
    and write the draft bundle for a week. Memory mutations are applied to the
    working tree under ``memory_root`` (default: ``config.memory.root``).

    ``focus_selector`` (optional) is called after the indexer with the threads
    active this week and returns the ids the entry should lead on; the caller owns
    how they are chosen (interactive prompt, ``--focus`` flag, …). Omitted or an
    empty return means the model picks the lead itself."""
    if week:
        activity_path = Path(raw_dir) / week / "activity.json"
        if not activity_path.exists():
            raise FileNotFoundError(f"{activity_path} not found — run `collect` for {week} first")
    else:
        activity_path = find_latest_activity(raw_dir)

    activity = Activity.model_validate_json(activity_path.read_text())
    week = activity.week
    out_dir = Path(drafts_dir) / week
    out_dir.mkdir(parents=True, exist_ok=True)
    phrases = config.redaction.forbidden_phrases
    root = Path(memory_root) if memory_root is not None else Path(config.memory.root)

    memories = _load_repo_memories(activity, root)

    # Stage A — technical summary, memory-aware (redact input before sending).
    repo_context = _repo_context(config, activity)
    memory_context = _render_memory_context(memories)
    redacted_a, n_a = redact(activity.model_dump_json(indent=2), phrases)
    logger.info("Stage A: %d phrase occurrence(s) redacted before the API call", n_a)
    initiatives = _generate(
        llm, stage_a_prompt(redacted_a, repo_context, memory_context), Initiatives, out_dir
    )
    assert isinstance(initiatives, Initiatives)

    (out_dir / "summary-tech.json").write_text(initiatives.model_dump_json(indent=2))
    (out_dir / "summary-tech.md").write_text(_render_summary(week, initiatives))

    # Indexer — model proposes, code disposes. Failure-tolerant per repo.
    for mem in memories:
        _run_indexer(llm, initiatives, mem, week, phrases, out_dir)
        if mem.changed:
            path = save_registry(mem.registry, mem.memory_dir)
            logger.info("Indexer updated memory → %s", path)
    due = [pair for mem in memories for pair in reviews_due(mem.registry, week)]

    # Focus — let the caller pick which thread(s) this week's entry leads on.
    candidates = _active_threads(memories, week)
    selected_ids = list(focus_selector(candidates)) if focus_selector else []
    candidate_ids = {t.id for t in candidates}
    unknown = [tid for tid in selected_ids if tid not in candidate_ids]
    if unknown:
        raise TransformError(
            f"focus references thread id(s) not active this week: {', '.join(unknown)}. "
            f"Active this week: {', '.join(sorted(candidate_ids)) or '(none)'}"
        )
    # Preserve the order the caller picked — the first is the primary lead.
    by_id = {t.id: t for t in candidates}
    focus = _render_focus([by_id[tid] for tid in selected_ids])

    # Stage B — writing, thread-aware (redact the Stage A output too).
    # The devlog number is assigned by the site adapter's manifest, not here —
    # the title is a bare subtitle and the site renders "<series> #N:".
    thread_context = _render_thread_context(initiatives, memories, due, week)
    redacted_b, n_b = redact(initiatives.model_dump_json(indent=2), phrases)
    logger.info("Stage B: %d phrase occurrence(s) redacted before the API call", n_b)
    content = _generate(llm, stage_b_prompt(redacted_b, thread_context, focus), Content, out_dir)
    assert isinstance(content, Content)

    generated_at = datetime.now(UTC).isoformat()
    names = [init.name for init in initiatives.initiatives]
    front = _front_matter(week, generated_at, content.title, names)

    (out_dir / "devlog.md").write_text(
        front + f"# {content.title}\n\n" + content.devlog.rstrip() + "\n"
    )
    (out_dir / "social.md").write_text(front + content.social.rstrip() + "\n")
    highlights = "\n".join(f"- {item}" for item in content.highlights)
    (out_dir / "highlights.md").write_text(front + highlights + "\n")

    # Structural content-policy checks — drafts are already on disk, so a blocking
    # violation still leaves them (and the report) for inspection. Any
    # error-severity failure (solicitation, collaborator leak, forbidden phrase,
    # invented link) HALTS the run so it never reaches a PR; the eval runner sets
    # enforce_checks=False to score failing cases without aborting.
    thread_ids = {t.id for mem in memories for t in mem.registry.threads}
    results = _run_checks(content, initiatives, activity, config, thread_ids, out_dir)
    errors = failures(results, "error")
    if errors:
        summary = "; ".join(f"{e.name} ({e.detail})" for e in errors)
        logger.error("Content checks failed (%d error-severity): %s", len(errors), summary)
        if enforce_checks:
            raise TransformError(f"content policy checks failed: {summary}")

    return out_dir
