"""v0.4 golden eval runner.

Runs the REAL transformer over curated activity fixtures and scores the output
with the structural check library (:mod:`pipeline.checks`). Drives the
``pipeline eval`` command and the ``evals.yml`` workflow — it needs
``ANTHROPIC_API_KEY`` and is never part of unit CI. The runner *logic* here is
unit-tested offline with a fake LLM; only the real-model path needs the key.

A golden case is a directory under ``evals/cases/<id>/``:

    case.yaml        # description, github_user, allowlist, descriptions, focus
    activity.json    # the week's collected activity (any schema version)
    memory/          # optional seed thread registry (memory/{org}/{repo}/…)

The runner copies the case into a throwaway scratch dir, runs ``transform_week``
with ``enforce_checks=False`` (so a failing case is scored, not aborted), and
reads back the ``checks.json`` the transform wrote.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .checks import CheckResult, failures
from .config import AnthropicConfig, Config, RedactionConfig, ReposConfig
from .llm import LLMClient, TransformError
from .models import Activity
from .transform import transform_week

logger = logging.getLogger(__name__)

DEFAULT_CASES_DIR = Path("evals/cases")
DEFAULT_OUTPUT = Path("evals/RESULTS.md")


@dataclass
class CaseResult:
    """One golden case's outcome: the check results, or a ``run`` error if the
    transform itself failed (e.g. a model/JSON error)."""

    case_id: str
    description: str
    results: list[CheckResult]
    run_error: str | None = None

    @property
    def errors(self) -> list[CheckResult]:
        return failures(self.results, "error")

    @property
    def warnings(self) -> list[CheckResult]:
        return failures(self.results, "warn")

    @property
    def blocked(self) -> bool:
        return self.run_error is not None or bool(self.errors)


def _load_case_config(case: dict, llm: LLMClient) -> Config:
    """Build a :class:`Config` from a case.yaml, defaulting anything unset."""
    return Config(
        github_user=case.get("github_user", "rsporny"),
        repos=ReposConfig(
            allowlist=case.get("allowlist") or ["o/r"],
            descriptions=case.get("descriptions") or {},
        ),
        redaction=RedactionConfig(forbidden_phrases=case.get("forbidden_phrases") or []),
        anthropic=AnthropicConfig(model=llm.model, max_tokens=llm.max_tokens),
    )


def run_case(case_dir: Path, llm: LLMClient, *, scratch_root: Path | None = None) -> CaseResult:
    """Run the transformer over one golden case and return its scored result."""
    case = yaml.safe_load((case_dir / "case.yaml").read_text()) or {}
    case_id = case_dir.name
    description = case.get("description", "")

    activity = Activity.model_validate_json((case_dir / "activity.json").read_text())
    focus_ids = list(case.get("focus") or [])

    tmp = Path(scratch_root or tempfile.mkdtemp(prefix=f"eval-{case_id}-"))
    raw_week = tmp / "raw" / activity.week
    raw_week.mkdir(parents=True, exist_ok=True)
    shutil.copy(case_dir / "activity.json", raw_week / "activity.json")
    seed_memory = case_dir / "memory"
    memory_root = tmp / "memory"
    if seed_memory.is_dir():
        shutil.copytree(seed_memory, memory_root)

    config = _load_case_config(case, llm)
    selector = (lambda _candidates: focus_ids) if focus_ids else None

    try:
        out_dir = transform_week(
            config,
            llm,
            raw_dir=tmp / "raw",
            drafts_dir=tmp / "drafts",
            week=activity.week,
            memory_root=memory_root,
            focus_selector=selector,
            enforce_checks=False,
        )
    except (TransformError, FileNotFoundError) as exc:
        logger.warning("Case %s failed to run: %s", case_id, exc)
        return CaseResult(
            case_id,
            description,
            results=[CheckResult("run", passed=False, severity="error", detail=str(exc))],
            run_error=str(exc),
        )

    raw = json.loads((out_dir / "checks.json").read_text())
    results = [CheckResult(**item) for item in raw]
    return CaseResult(case_id, description, results=results)


def discover_cases(cases_dir: Path, case_ids: list[str] | None = None) -> list[Path]:
    """The case directories to run, sorted by id. ``case_ids`` filters and, when
    given, an unknown id is an error."""
    available = {p.name: p for p in sorted(cases_dir.iterdir()) if (p / "case.yaml").exists()}
    if case_ids is None:
        return list(available.values())
    unknown = [cid for cid in case_ids if cid not in available]
    if unknown:
        raise ValueError(f"unknown case id(s): {', '.join(unknown)}; have: {', '.join(available)}")
    return [available[cid] for cid in case_ids]


def run_cases(
    cases_dir: Path = DEFAULT_CASES_DIR,
    llm: LLMClient | None = None,
    case_ids: list[str] | None = None,
) -> list[CaseResult]:
    if llm is None:  # pragma: no cover - convenience for the CLI
        raise ValueError("run_cases requires an LLMClient")
    return [run_case(case_dir, llm) for case_dir in discover_cases(cases_dir, case_ids)]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover
        return "unknown"


def _mark(result: CheckResult) -> str:
    if result.passed:
        return "✓"
    return "✗" if result.severity == "error" else "!"


def render_scorecard(cases: list[CaseResult], *, model: str, sha: str | None = None) -> str:
    """A compact, committable Markdown scorecard: a check × case matrix plus a
    failures detail list and totals."""
    sha = sha if sha is not None else git_sha()
    n_err = sum(len(c.errors) for c in cases)
    n_warn = sum(len(c.warnings) for c in cases)
    n_blocked = sum(1 for c in cases if c.blocked)

    lines = [
        "# Eval results",
        "",
        f"- Model: `{model}`",
        f"- Commit: `{sha}`",
        f"- Generated: {datetime.now(UTC).isoformat()}",
        f"- Cases: {len(cases)} ({n_blocked} blocked) | "
        f"error-severity failures: {n_err} | warnings: {n_warn}",
        "",
    ]

    # Matrix: rows = check names (union, stable order of first appearance), cols = cases.
    check_names: list[str] = []
    for case in cases:
        for r in case.results:
            if r.name not in check_names:
                check_names.append(r.name)
    header = "| check | " + " | ".join(c.case_id for c in cases) + " |"
    divider = "|---|" + "|".join([":-:"] * len(cases)) + "|"
    lines += [header, divider]
    for name in check_names:
        cells = []
        for case in cases:
            found = next((r for r in case.results if r.name == name), None)
            cells.append(_mark(found) if found else "·")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")

    failed = [(c, r) for c in cases for r in c.results if not r.passed]
    if failed:
        lines += ["", "## Failures", ""]
        for case, r in failed:
            lines.append(f"- `{case.case_id}` / **{r.name}** [{r.severity}] — {r.detail}")

    lines += ["", "_✓ pass · ✗ error-severity failure · ! warning_", ""]
    return "\n".join(lines)


def has_errors(cases: list[CaseResult]) -> bool:
    """True if any case had an error-severity failure or failed to run — the
    signal the eval CI job exits non-zero on."""
    return any(c.blocked for c in cases)
