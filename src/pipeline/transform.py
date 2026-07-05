from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .config import Config
from .llm import LLMClient, TransformError
from .models import Activity, Content, Initiatives
from .prompts import stage_a_prompt, stage_b_prompt
from .redact import redact

logger = logging.getLogger(__name__)


def find_latest_activity(raw_dir: Path | str = "raw") -> Path:
    """Return the newest ``raw/*/activity.json`` (ISO week sorts lexically)."""
    candidates = sorted(Path(raw_dir).glob("*/activity.json"))
    if not candidates:
        raise FileNotFoundError(f"No activity.json found under {raw_dir}/ — run `collect` first")
    return candidates[-1]


def next_entry_number(published_dir: Path | str = "published") -> int:
    """Next devlog number = count of already-published devlogs + 1."""
    return len(list(Path(published_dir).glob("*/devlog.md"))) + 1


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


def _write_failed(out_dir: Path, raw: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "_failed_raw.txt"
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
        if init.links:
            lines += [f"**Links:** {', '.join(init.links)}", ""]
    return "\n".join(lines).rstrip() + "\n"


def transform_week(
    config: Config,
    llm: LLMClient,
    raw_dir: Path | str = "raw",
    drafts_dir: Path | str = "drafts",
    published_dir: Path | str = "published",
    week: str | None = None,
) -> Path:
    """Run redaction → Stage A → Stage B and write the draft bundle for a week."""
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

    # Stage A — technical summary (redact input before sending).
    repo_context = _repo_context(config, activity)
    redacted_a, n_a = redact(activity.model_dump_json(indent=2), phrases)
    logger.info("Stage A: %d phrase occurrence(s) redacted before the API call", n_a)
    initiatives = _generate(llm, stage_a_prompt(redacted_a, repo_context), Initiatives, out_dir)
    assert isinstance(initiatives, Initiatives)

    (out_dir / "summary-tech.json").write_text(initiatives.model_dump_json(indent=2))
    (out_dir / "summary-tech.md").write_text(_render_summary(week, initiatives))

    # Stage B — writing (redact the Stage A output too, per the hard constraint).
    entry_number = next_entry_number(published_dir)
    redacted_b, n_b = redact(initiatives.model_dump_json(indent=2), phrases)
    logger.info("Stage B: %d phrase occurrence(s) redacted before the API call", n_b)
    prompt_b = stage_b_prompt(redacted_b, config.content.devlog_title_prefix, entry_number)
    content = _generate(llm, prompt_b, Content, out_dir)
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

    return out_dir
