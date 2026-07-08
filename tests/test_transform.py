from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from pipeline.config import Config, RedactionConfig, ReposConfig
from pipeline.llm import LLMClient, TransformError, parse_json_response, strip_fences
from pipeline.memory import Assumption, Thread, ThreadRegistry, load_registry, save_registry
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
                "category": "Developer tooling",
                "what": "Built the GitHub collector.",
                "why_it_matters": "Reliable weekly data.",
                "tech": ["Python", "httpx"],
                "links": ["https://github.com/o/r/pull/5"],
            }
        ]
    }
    return data, json.dumps(data)


def _stage_a_with_thread_ref(thread_id="collector", relation="continues"):
    data = {
        "initiatives": [
            {
                "name": "Collector",
                "category": "Developer tooling",
                "what": "Extended the GitHub collector.",
                "why_it_matters": "Reliable weekly data.",
                "tech": ["Python"],
                "links": ["https://github.com/o/r/pull/6"],
                "thread_ref": {"id": thread_id, "relation": relation},
            }
        ]
    }
    return data, json.dumps(data)


def _indexer(updates=None, new_threads=None):
    data = {"updates": updates or [], "new_threads": new_threads or []}
    return data, json.dumps(data)


def _stage_b():
    data = {
        "title": "Wiring commits into a content pipeline",
        "devlog": "This week I built the collector.",
        "social": "This week I shipped a collector. Here's what I learned.",
        "highlights": ["Collector — 23 tests passing"],
    }
    return data, json.dumps(data)


def _seed_thread(memory_root, repo="o/r", **thread_kw):
    """Write a threads.yaml for ``repo`` under ``memory_root`` and return its dir."""
    org, _, name = repo.partition("/")
    memory_dir = memory_root / org / name
    base = dict(
        id="collector",
        title="GitHub collector",
        status="ongoing",
        started_week="2026-W25",
        last_active_week="2026-W25",
        summary="Fetches weekly activity from GitHub.",
    )
    base.update(thread_kw)
    save_registry(ThreadRegistry(threads=[Thread(**base)]), memory_dir)
    return memory_dir


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


def _config(forbidden=None, descriptions=None):
    return Config(
        github_user="rsporny",
        repos=ReposConfig(allowlist=["o/r"], descriptions=descriptions or {}),
        redaction=RedactionConfig(forbidden_phrases=forbidden or []),
    )


def test_transform_week_writes_all_drafts(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    out_dir = transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
    )

    assert out_dir == drafts_dir / week
    for name in (
        "summary-tech.md",
        "summary-tech.json",
        "devlog.md",
        "social.md",
        "highlights.md",
    ):
        assert (out_dir / name).exists(), name
    # Polish/LinkedIn-specific drafts are gone.
    assert not (out_dir / "linkedin-pl.md").exists()
    assert not (out_dir / "linkedin-en.md").exists()

    devlog = (out_dir / "devlog.md").read_text()
    assert devlog.startswith("---")
    assert "status: draft" in devlog
    assert f"week: {week}" in devlog
    # The title is a bare subtitle (no series prefix / number) — the site adds
    # "Senior SDET log #N:" from the manifest; it appears in front matter and H1.
    assert "Wiring commits into a content pipeline" in devlog
    assert "# Wiring commits into a content pipeline" in devlog
    assert "#1" not in devlog
    assert "Collector" in devlog  # source_initiatives front matter
    assert "This week I built the collector." in devlog

    social = (out_dir / "social.md").read_text()
    assert "This week I shipped a collector." in social

    summary = (out_dir / "summary-tech.md").read_text()
    assert "## Collector" in summary
    assert "Developer tooling" in summary  # category rendered
    assert "https://github.com/o/r/pull/5" in summary  # proof-of-work link
    assert "- Collector — 23 tests passing" in (out_dir / "highlights.md").read_text()


def test_transform_redacts_input_before_sending(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    transform_week(
        _config(forbidden=["o/r"]),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
    )

    stage_a_prompt_text = llm.prompts[0]
    assert "o/r" not in stage_a_prompt_text
    assert "[REDACTED]" in stage_a_prompt_text


def test_transform_broken_json_writes_failed_raw(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([TransformError("bad json", raw="THIS IS NOT JSON")])

    with pytest.raises(TransformError):
        transform_week(
            _config(),
            llm,
            raw_dir=raw_dir,
            drafts_dir=drafts_dir,
            week=week,
            memory_root=tmp_path / "memory",
        )

    failed = drafts_dir / week / "_failed_raw.txt"
    assert failed.exists()
    assert failed.read_text() == "THIS IS NOT JSON"


def test_transform_schema_mismatch_writes_failed_raw(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    bad = ({"wrong": "shape"}, json.dumps({"wrong": "shape"}))
    llm = _FakeLLM([bad])

    with pytest.raises(TransformError):
        transform_week(
            _config(),
            llm,
            raw_dir=raw_dir,
            drafts_dir=drafts_dir,
            week=week,
            memory_root=tmp_path / "memory",
        )

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


def test_transform_passes_repo_context_to_stage_a(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    transform_week(
        _config(descriptions={"o/r": "A blockchain node in the Cardano ecosystem."}),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
    )

    assert "A blockchain node in the Cardano ecosystem." in llm.prompts[0]


def test_transform_title_prompt_carries_no_number(tmp_path):
    """Numbering is the manifest's job now: the Stage B prompt asks for a bare
    subtitle (no series prefix / number) and the model's title flows through
    verbatim to the draft."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    out_dir = transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
    )

    # Stage B is the third model call now (A → indexer → B). Its prompt instructs
    # a bare subtitle and injects no per-run number.
    assert "no series name and no number" in llm.prompts[2]
    devlog = (out_dir / "devlog.md").read_text()
    assert "# Wiring commits into a content pipeline" in devlog  # title used verbatim


# --- memory-aware transform (v0.2 M3) ---------------------------------------


def test_stage_a_prompt_carries_repo_memory(tmp_path):
    """Stage A sees each active repo's context card and current threads so it can
    connect the week's work back to a known arc via thread_ref."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    _seed_thread(memory_root)
    (memory_root / "o" / "r" / "context.md").write_text("A commit→content pipeline.")
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    stage_a = llm.prompts[0]
    assert "id: collector" in stage_a
    assert "GitHub collector" in stage_a
    assert "A commit→content pipeline." in stage_a


def test_indexer_applies_update_to_working_tree(tmp_path):
    """The indexer proposes; code disposes. A proposed update is applied
    deterministically and written back to threads.yaml, with the week stamped."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)  # 2026-W27
    _seed_thread(memory_root)
    update = {"id": "collector", "summary": "Now paginates and dedupes.", "status": "done"}
    llm = _FakeLLM([_stage_a(), _indexer(updates=[update]), _stage_b()])

    transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    reg = load_registry(memory_root / "o" / "r")
    thread = reg.get("collector")
    assert thread.summary == "Now paginates and dedupes."
    assert thread.status == "done"
    assert thread.last_active_week == week  # stamped by code, not the model


def test_indexer_creates_new_thread(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    new = {"id": "memory-module", "title": "Memory", "summary": "Threads + assumptions."}
    llm = _FakeLLM([_stage_a(), _indexer(new_threads=[new]), _stage_b()])

    transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    reg = load_registry(memory_root / "o" / "r")
    created = reg.get("memory-module")
    assert created is not None
    assert created.started_week == week  # stamped by code
    assert created.last_active_week == week


def test_indexer_no_mutations_writes_no_memory_file(tmp_path):
    """A repo with no prior memory and an empty indexer proposal stays clean —
    we do not litter the tree with empty threads.yaml files."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    assert not (memory_root / "o" / "r" / "threads.yaml").exists()


def test_indexer_failure_does_not_block_stage_b(tmp_path):
    """An indexer error (here: a mutation referencing an unknown thread) must
    fall back to the previous memory and still produce the drafts."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    _seed_thread(memory_root)  # summary: "Fetches weekly activity from GitHub."
    bad_update = {"id": "does-not-exist", "summary": "boom"}
    llm = _FakeLLM([_stage_a(), _indexer(updates=[bad_update]), _stage_b()])

    out_dir = transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    # Stage B still ran → drafts exist.
    assert (out_dir / "devlog.md").exists()
    # Previous memory preserved unchanged.
    reg = load_registry(memory_root / "o" / "r")
    assert reg.get("collector").summary == "Fetches weekly activity from GitHub."
    assert reg.get("collector").last_active_week == "2026-W25"
    # The failed proposal was captured for debugging without aborting the run.
    assert (out_dir / "_indexer_failed_raw.txt").exists()


def test_indexer_model_error_falls_back(tmp_path):
    """A raw model/JSON failure in the indexer is also non-fatal."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    _seed_thread(memory_root)
    llm = _FakeLLM([_stage_a(), TransformError("bad json", raw="NOPE"), _stage_b()])

    out_dir = transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    assert (out_dir / "devlog.md").exists()
    reg = load_registry(memory_root / "o" / "r")
    assert reg.get("collector").last_active_week == "2026-W25"  # untouched


def test_stage_b_prompt_carries_thread_and_review_context(tmp_path):
    """Stage B receives continuity for referenced threads and any assumptions due
    for review, so it can weave the arc in and revisit stale beliefs."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)  # 2026-W27
    _seed_thread(
        memory_root,
        assumptions=[
            Assumption(text="Diffs are unnecessary", made_week="2026-W25", review_by="2026-W27")
        ],
    )
    llm = _FakeLLM([_stage_a_with_thread_ref(), _indexer(), _stage_b()])

    transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    stage_b = llm.prompts[2]
    assert "GitHub collector" in stage_b  # referenced thread
    assert "continues it" in stage_b  # the stated relation
    assert "due for review" in stage_b
    assert "Diffs are unnecessary" in stage_b


def test_thread_ref_rendered_in_summary(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    _seed_thread(memory_root)
    llm = _FakeLLM([_stage_a_with_thread_ref(), _indexer(), _stage_b()])

    out_dir = transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    summary = (out_dir / "summary-tech.md").read_text()
    assert "collector (continues)" in summary
