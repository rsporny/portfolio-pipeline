from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.checks import CheckResult
from pipeline.evals import (
    CaseResult,
    discover_cases,
    has_errors,
    render_scorecard,
    run_case,
)
from pipeline.models import Activity

CASES_DIR = Path(__file__).parent.parent / "evals" / "cases"

# A real github.com URL for the hermetic repo — the indexer is scoped per repo by
# parsing owner/repo from initiative links, so a placeholder host would skip it.
PR_URL = "https://github.com/h/repo/pull/1"


class _FakeLLM:
    """Injectable stand-in returning canned (data, raw) pairs per call."""

    model = "fake-model"
    max_tokens = 1000

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)

    def complete_json(self, prompt: str, system: str | None = None):
        data = self.responses.pop(0)
        return data, json.dumps(data)


def _stage_a(link: str = PR_URL):
    return {
        "initiatives": [
            {
                "name": "Retries",
                "category": "Reliability",
                "what": "Added backoff.",
                "why_it_matters": "Fewer stalls.",
                "tech": ["Python"],
                "links": [link],
            }
        ]
    }


def _indexer():
    return {"updates": [], "new_threads": []}


def _stage_b(**overrides):
    data = {
        "title": "adding backoff to the client",
        "devlog": f"This week I added retries. Proof: {PR_URL}",
        "social": "Added retries with backoff this week. One lesson: give up after three tries.",
        "highlights": ["Backoff after 3 attempts — Retries"],
    }
    data.update(overrides)
    return data


def _hermetic_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "case-x"
    case_dir.mkdir()
    (case_dir / "case.yaml").write_text("description: hermetic\nallowlist: [h/repo]\n")
    activity = {
        "schema_version": 3,
        "generated_at": "2026-07-12T16:00:00Z",
        "since": "2026-07-05T16:00:00Z",
        "until": "2026-07-12T16:00:00Z",
        "week": "2026-W28",
        "repos": [
            {
                "repo": "h/repo",
                "commits": [],
                "pull_requests": [{"number": 1, "title": "Retries", "url": PR_URL}],
                "issues": [],
            }
        ],
    }
    (case_dir / "activity.json").write_text(json.dumps(activity))
    return case_dir


# --- run_case ---------------------------------------------------------------


def test_run_case_scores_a_passing_run(tmp_path):
    case_dir = _hermetic_case(tmp_path)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    result = run_case(case_dir, llm, scratch_root=tmp_path / "scratch")

    assert result.case_id == "case-x"
    assert result.run_error is None
    assert not result.blocked
    names = {r.name for r in result.results}
    assert "faithful_links" in names and "no_solicitation" in names


def test_run_case_flags_error_severity_violation(tmp_path):
    case_dir = _hermetic_case(tmp_path)
    bad = _stage_b(social="Loved this. Contact me if you want help with retries.")
    llm = _FakeLLM([_stage_a(), _indexer(), bad])

    result = run_case(case_dir, llm, scratch_root=tmp_path / "scratch")

    assert result.blocked
    assert "no_solicitation" in {r.name for r in result.errors}


def test_run_case_captures_a_run_error(tmp_path):
    case_dir = _hermetic_case(tmp_path)
    # Stage A returns a non-dict-shaped payload → schema validation fails.
    llm = _FakeLLM([{"initiatives": "not a list"}])

    result = run_case(case_dir, llm, scratch_root=tmp_path / "scratch")

    assert result.blocked and result.run_error is not None
    assert result.results[0].name == "run"


# --- discover_cases + shipped golden data -----------------------------------


def test_discover_cases_finds_all_golden_cases():
    ids = {p.name for p in discover_cases(CASES_DIR)}
    assert {"baseline", "continuity", "focus", "deep-context", "merge-state"} <= ids


def test_discover_cases_rejects_unknown_id():
    with pytest.raises(ValueError, match="unknown case id"):
        discover_cases(CASES_DIR, ["nope"])


def test_shipped_cases_activity_parses():
    for case_dir in discover_cases(CASES_DIR):
        activity = Activity.model_validate_json((case_dir / "activity.json").read_text())
        assert activity.week and activity.repos


# --- scorecard + totals -----------------------------------------------------


def _case(case_id: str, *results: CheckResult, run_error: str | None = None) -> CaseResult:
    return CaseResult(case_id, "desc", list(results), run_error=run_error)


def test_render_scorecard_and_totals():
    cases = [
        _case(
            "ok",
            CheckResult("faithful_links", True, "error"),
            CheckResult("devlog_word_count", False, "warn", "120 words (want 400–750)"),
        ),
        _case(
            "bad",
            CheckResult("no_solicitation", False, "error", "solicitation phrase(s): contact me"),
        ),
    ]
    card = render_scorecard(cases, model="claude-opus-4-8", sha="abc1234")

    assert "claude-opus-4-8" in card and "abc1234" in card
    assert "| check | ok | bad |" in card
    assert "## Failures" in card
    assert "no_solicitation" in card
    # Totals: 1 error-severity failure, 1 warning, 1 blocked case.
    assert "error-severity failures: 1" in card
    assert "warnings: 1" in card
    assert has_errors(cases)


def test_has_errors_false_when_all_pass():
    cases = [_case("ok", CheckResult("faithful_links", True, "error"))]
    assert not has_errors(cases)
