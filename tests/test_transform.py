from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from pipeline.config import Config, RedactionConfig, ReposConfig
from pipeline.llm import LLMClient, TransformError, parse_json_response, strip_fences
from pipeline.models import Activity, Commit, RepoActivity
from pipeline.transform import find_latest_activity, transform_week

# --- JSON parsing -----------------------------------------------------------


def test_parse_plain_json():
    assert parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_fenced_json():
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_bare_fenced_json():
    assert parse_json_response('```\n{"a": 1}\n```') == {"a": 1}


def test_parse_broken_json_raises_with_raw():
    with pytest.raises(TransformError) as excinfo:
        parse_json_response("not json at all")
    assert excinfo.value.raw == "not json at all"


def test_parse_non_object_raises():
    with pytest.raises(TransformError):
        parse_json_response("[1, 2, 3]")


def test_strip_fences_plain_passthrough():
    assert strip_fences('{"a": 1}') == '{"a": 1}'


# --- LLM client retry behaviour ---------------------------------------------


def _text_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class _FakeMessages:
    def __init__(self, behaviors: list) -> None:
        self.behaviors = list(behaviors)
        self.calls = 0

    def create(self, **kwargs):
        behavior = self.behaviors[self.calls]
        self.calls += 1
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


class _FakeAnthropic:
    def __init__(self, behaviors: list) -> None:
        self.messages = _FakeMessages(behaviors)


def _conn_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )


def test_llm_retries_then_succeeds():
    sleeps: list[float] = []
    fake = _FakeAnthropic([_conn_error(), _conn_error(), _text_response('{"ok": true}')])
    llm = LLMClient(model="m", max_tokens=100, client=fake, base_delay=0, sleep=sleeps.append)

    data, raw = llm.complete_json("prompt")
    assert data == {"ok": True}
    assert fake.messages.calls == 3
    assert len(sleeps) == 2  # slept between the three attempts


def test_llm_gives_up_after_max_attempts():
    fake = _FakeAnthropic([_conn_error(), _conn_error(), _conn_error()])
    llm = LLMClient(model="m", max_tokens=100, client=fake, base_delay=0, sleep=lambda _: None)

    with pytest.raises(TransformError):
        llm.complete("prompt")
    assert fake.messages.calls == 3


# --- transform_week orchestration -------------------------------------------


class _FakeLLM:
    """Injectable stand-in returning canned (data, raw) pairs per call."""

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, system: str | None = None):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _stage_a():
    data = {
        "initiatives": [
            {
                "name": "Collector",
                "what": "Built the GitHub collector.",
                "why_it_matters": "Reliable weekly data.",
                "tech": ["Python", "httpx"],
            }
        ]
    }
    return data, json.dumps(data)


def _stage_b():
    data = {
        "devlog": "This week I built the collector.",
        "linkedin_pl": "W tym tygodniu zbudowałem kolektor.",
        "linkedin_en": "This week I shipped a collector.",
        "highlights": ["Collector — 23 tests passing"],
    }
    return data, json.dumps(data)


def _write_activity(raw_dir, week: str = "2026-W27") -> str:
    now = datetime.now(UTC)
    activity = Activity(
        generated_at=now,
        since=now,
        until=now,
        week=week,
        repos=[
            RepoActivity(
                repo="o/r",
                commits=[Commit(sha="a1", date=now, message="Add collector", files=[])],
            )
        ],
    )
    week_dir = raw_dir / week
    week_dir.mkdir(parents=True)
    (week_dir / "activity.json").write_text(activity.model_dump_json())
    return week


def _config(forbidden=None):
    return Config(
        github_user="rsporny",
        repos=ReposConfig(allowlist=["o/r"]),
        redaction=RedactionConfig(forbidden_phrases=forbidden or []),
    )


def test_transform_week_writes_all_drafts(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _stage_b()])

    out_dir = transform_week(_config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week)

    assert out_dir == drafts_dir / week
    for name in (
        "summary-tech.md",
        "summary-tech.json",
        "devlog.md",
        "linkedin-pl.md",
        "linkedin-en.md",
        "highlights.md",
    ):
        assert (out_dir / name).exists(), name

    devlog = (out_dir / "devlog.md").read_text()
    assert devlog.startswith("---")
    assert "status: draft" in devlog
    assert f"week: {week}" in devlog
    assert "Collector" in devlog  # source_initiatives front matter
    assert "This week I built the collector." in devlog

    assert "## Collector" in (out_dir / "summary-tech.md").read_text()
    assert "- Collector — 23 tests passing" in (out_dir / "highlights.md").read_text()


def test_transform_redacts_input_before_sending(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _stage_b()])

    transform_week(
        _config(forbidden=["o/r"]), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week
    )

    stage_a_prompt_text = llm.prompts[0]
    assert "o/r" not in stage_a_prompt_text
    assert "[REDACTED]" in stage_a_prompt_text


def test_transform_broken_json_writes_failed_raw(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([TransformError("bad json", raw="THIS IS NOT JSON")])

    with pytest.raises(TransformError):
        transform_week(_config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week)

    failed = drafts_dir / week / "_failed_raw.txt"
    assert failed.exists()
    assert failed.read_text() == "THIS IS NOT JSON"


def test_transform_schema_mismatch_writes_failed_raw(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    bad = ({"wrong": "shape"}, json.dumps({"wrong": "shape"}))
    llm = _FakeLLM([bad])

    with pytest.raises(TransformError):
        transform_week(_config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week)

    failed = drafts_dir / week / "_failed_raw.txt"
    assert failed.exists()
    assert "wrong" in failed.read_text()


def test_find_latest_activity_picks_newest(tmp_path):
    raw_dir = tmp_path / "raw"
    for wk in ("2026-W25", "2026-W27", "2026-W26"):
        _write_activity(raw_dir, wk)
    assert find_latest_activity(raw_dir).parent.name == "2026-W27"


def test_find_latest_activity_raises_when_empty(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_latest_activity(tmp_path / "raw")
