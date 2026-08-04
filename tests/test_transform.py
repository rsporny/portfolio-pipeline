from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from pipeline.config import Config, RedactionConfig, ReposConfig, StateConfig
from pipeline.llm import LLMClient, TransformError, parse_json_response, strip_fences
from pipeline.memory import Assumption, Thread, ThreadRegistry, load_registry, save_registry
from pipeline.models import (
    Activity,
    Commit,
    LinkedIssue,
    PullRequest,
    RepoActivity,
    ReviewComment,
)
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


def test_parse_object_with_trailing_prose():
    """The model sometimes appends a note after a valid object despite being told
    to emit JSON only (the W28 indexer failure). Salvage the object."""
    text = '{"updates": [], "new_threads": []}\n\nNote: the other repo was left untouched.'
    assert parse_json_response(text) == {"updates": [], "new_threads": []}


def test_parse_object_with_leading_prose():
    assert parse_json_response('Sure, here you go:\n{"a": 1}') == {"a": 1}


def test_parse_no_object_still_raises_with_raw():
    with pytest.raises(TransformError) as excinfo:
        parse_json_response("still no object here")
    assert excinfo.value.raw == "still no object here"


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
        "devlog": (
            "## Collector\n\n"
            "This week I built the collector.\n\n"
            "Proof of work: https://github.com/o/r/pull/5"
        ),
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
                commits=[
                    Commit(
                        sha="a1",
                        date=now,
                        message="Add collector",
                        url="https://github.com/o/r/commit/a1",
                        files=[],
                    )
                ],
                pull_requests=[
                    PullRequest(
                        number=5, title="Add collector", url="https://github.com/o/r/pull/5"
                    ),
                    PullRequest(
                        number=6, title="Extend collector", url="https://github.com/o/r/pull/6"
                    ),
                ],
            )
        ],
    )
    week_dir = raw_dir / week
    week_dir.mkdir(parents=True)
    (week_dir / "activity.json").write_text(activity.model_dump_json())
    return week


def _write_activity_with_deep_pr(raw_dir, week: str = "2026-W27") -> str:
    """An activity whose PR carries v0.3 deep context (already anonymized)."""
    now = datetime.now(UTC)
    pr = PullRequest(
        number=5,
        title="Add collector",
        url="https://github.com/o/r/pull/5",
        description="Closes #42",
        review_comments=[
            ReviewComment(body="Gate this on active threads.", author_role="other", kind="review"),
            ReviewComment(body="Done, gated it.", author_role="owner", kind="conversation"),
        ],
        linked_issues=[
            LinkedIssue(number=42, title="Flaky window", url="https://x/42", relation="closes")
        ],
    )
    activity = Activity(
        generated_at=now,
        since=now,
        until=now,
        week=week,
        repos=[RepoActivity(repo="o/r", pull_requests=[pr])],
    )
    week_dir = raw_dir / week
    week_dir.mkdir(parents=True)
    (week_dir / "activity.json").write_text(activity.model_dump_json())
    return week


def _config(forbidden=None, descriptions=None, state_root=None):
    return Config(
        github_user="rsporny",
        repos=ReposConfig(allowlist=["o/r"], descriptions=descriptions or {}),
        redaction=RedactionConfig(forbidden_phrases=forbidden or []),
        state=StateConfig(root=str(state_root)) if state_root is not None else StateConfig(),
    )


def test_transform_week_writes_only_under_state_root(tmp_path):
    """The engine is stateless: with no explicit dirs, raw/drafts/memory all
    resolve under state.root and the run writes nothing outside it."""
    instance = tmp_path / "instance"
    cfg = _config(state_root=instance)
    week = _write_activity(cfg.state_dir("raw"))
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    out_dir = transform_week(cfg, llm, week=week)  # no raw_dir/drafts_dir/memory_root

    assert out_dir == cfg.state_dir("drafts") / week
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written, "expected the run to write draft files"
    assert all(instance in p.parents for p in written), (
        f"engine wrote outside state.root: {[p for p in written if instance not in p.parents]}"
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
    # `topics:` block: per-section title/category/repo (repo derived from links),
    # parseable back out as structured YAML for the site's category dividers.
    from pipeline.frontmatter import parse as _parse

    front, _ = _parse(devlog)
    assert front["topics"] == [
        {"title": "Collector", "category": "Developer tooling", "repo": "o/r"}
    ]

    social = (out_dir / "social.md").read_text()
    assert "This week I shipped a collector." in social

    summary = (out_dir / "summary-tech.md").read_text()
    assert "## Collector" in summary
    assert "Developer tooling" in summary  # category rendered
    assert "https://github.com/o/r/pull/5" in summary  # proof-of-work link
    assert "- Collector — 23 tests passing" in (out_dir / "highlights.md").read_text()
    # Every run drops a checks report into the bundle (SPEC line 17).
    assert (out_dir / "checks.md").exists()


def _stage_b_bad(**overrides):
    data = {
        "title": "Wiring commits into a content pipeline",
        "devlog": "This week I built the collector. See https://github.com/o/r/pull/5",
        "social": "This week I shipped a collector.",
        "highlights": ["Collector — 23 tests passing"],
    }
    data.update(overrides)
    return data, json.dumps(data)


def test_warn_only_violation_still_writes_drafts(tmp_path):
    # A short devlog trips the word-count WARN but must not block the run.
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

    assert (out_dir / "devlog.md").exists()
    checks = (out_dir / "checks.md").read_text()
    assert "devlog_word_count" in checks


def test_solicitation_blocks_run_but_persists_drafts(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    bad = _stage_b_bad(social="Loved building this. Contact me to learn more.")
    llm = _FakeLLM([_stage_a(), _indexer(), bad])

    with pytest.raises(TransformError, match="content policy checks failed"):
        transform_week(
            _config(),
            llm,
            raw_dir=raw_dir,
            drafts_dir=drafts_dir,
            week=week,
            memory_root=tmp_path / "memory",
        )

    out_dir = drafts_dir / week
    assert (out_dir / "devlog.md").exists()  # drafts persisted for inspection
    assert "no_solicitation" in (out_dir / "checks.md").read_text()


def test_invented_link_blocks_run(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    bad = _stage_b_bad(devlog="Built it. See https://evil.example.com/made-up")
    llm = _FakeLLM([_stage_a(), _indexer(), bad])

    with pytest.raises(TransformError, match="content policy checks failed"):
        transform_week(
            _config(),
            llm,
            raw_dir=raw_dir,
            drafts_dir=drafts_dir,
            week=week,
            memory_root=tmp_path / "memory",
        )
    assert "faithful_links" in (drafts_dir / week / "checks.md").read_text()


def test_transform_redacts_input_before_sending(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    transform_week(
        _config(forbidden=["Add collector"]),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
    )

    stage_a_prompt_text = llm.prompts[0]
    assert "Add collector" not in stage_a_prompt_text
    assert "[REDACTED]" in stage_a_prompt_text


def test_stage_a_prompt_has_deep_context_guardrail(tmp_path):
    """The Stage A system prompt tells the model deep context is for understanding
    only and must never be quoted."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
    )

    stage_a = llm.prompts[0]
    assert "review_comments" in stage_a
    assert "never quote" in stage_a


def test_transform_runs_with_deep_context(tmp_path):
    """A PR carrying review discussion + linked issues flows through to Stage A
    structurally (it rides on the activity JSON) and the run still completes."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_activity_with_deep_pr(raw_dir)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    out_dir = transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
    )

    assert (out_dir / "devlog.md").exists()
    stage_a = llm.prompts[0]
    assert "Gate this on active threads." in stage_a  # deep context reached the model
    assert "Flaky window" in stage_a  # linked-issue title too


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
    # a bare subtitle and injects no per-run number (assert wrap-independently).
    stage_b = llm.prompts[2]
    assert "no series name" in stage_b
    assert "no number" in stage_b
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


# --- indexer scoping (one repo's indexer sees only its own initiatives) ------


def _write_two_repo_activity(raw_dir, week: str = "2026-W27") -> str:
    now = datetime.now(UTC)
    activity = Activity(
        generated_at=now,
        since=now,
        until=now,
        week=week,
        repos=[
            RepoActivity(
                repo="o/r",
                pull_requests=[
                    PullRequest(number=5, title="A", url="https://github.com/o/r/pull/5")
                ],
            ),
            RepoActivity(
                repo="o/other",
                pull_requests=[
                    PullRequest(number=9, title="B", url="https://github.com/o/other/pull/9")
                ],
            ),
        ],
    )
    week_dir = raw_dir / week
    week_dir.mkdir(parents=True)
    (week_dir / "activity.json").write_text(activity.model_dump_json())
    return week


def _stage_a_two():
    """Two initiatives, one per repo, each citing its own repo's PR link."""
    data = {
        "initiatives": [
            {
                "name": "Reader output",
                "category": "Developer tooling",
                "what": "Pipeline work.",
                "why_it_matters": "y",
                "tech": [],
                "links": ["https://github.com/o/r/pull/5"],
            },
            {
                "name": "Bridge funding",
                "category": "Blockchain",
                "what": "Node work.",
                "why_it_matters": "y",
                "tech": [],
                "links": ["https://github.com/o/other/pull/9"],
            },
        ]
    }
    return data, json.dumps(data)


def test_indexer_scoped_to_repos_own_initiatives(tmp_path):
    """Each repo's indexer receives ONLY the initiatives whose work is in that repo,
    so it can't create/reference another repo's thread (the cross-repo pollution
    bug). Stage A emits one combined list; scoping splits it by repo."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_two_repo_activity(raw_dir)
    llm = _FakeLLM([_stage_a_two(), _indexer(), _indexer(), _stage_b()])

    transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
    )

    idx_or = next(p for p in llm.prompts if "Repository: o/r" in p)
    idx_other = next(p for p in llm.prompts if "Repository: o/other" in p)
    assert "Reader output" in idx_or and "Bridge funding" not in idx_or
    assert "Bridge funding" in idx_other and "Reader output" not in idx_other


def test_indexer_skipped_for_repo_with_no_own_initiatives(tmp_path):
    """A repo with activity but no initiative of its own is skipped entirely — no
    indexer call (so no chance to hallucinate a foreign thread). Only one initiative
    (o/r) is produced, so o/other never reaches the indexer; if it did, the fake
    LLM would run out of canned responses."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    week = _write_two_repo_activity(raw_dir)
    # _stage_a() yields a single o/r initiative; o/other has no initiative.
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    out_dir = transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
    )

    assert (out_dir / "devlog.md").exists()
    assert any("Repository: o/r" in p for p in llm.prompts)
    assert not any("Repository: o/other" in p for p in llm.prompts)


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


# --- temporal framing (a thread born this week is not "past") ----------------


def test_stage_b_frames_same_week_thread_in_present(tmp_path):
    """A thread that began THIS week is introduced in the present tense — no
    "started N weeks ago" framing that reads the current week as history (the W28
    'started back in W28' bug)."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)  # 2026-W27
    _seed_thread(memory_root, started_week=week, last_active_week=week)
    llm = _FakeLLM([_stage_a_with_thread_ref(), _indexer(), _stage_b()])

    transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    stage_b = llm.prompts[2]
    assert "New this week" in stage_b
    assert "weeks ago" not in stage_b
    assert f"Started {week}" not in stage_b


def test_stage_b_frames_prior_week_thread_with_age(tmp_path):
    """A thread from an earlier week keeps continuity framing with its age."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)  # 2026-W27
    _seed_thread(memory_root, started_week="2026-W25", last_active_week="2026-W25")
    llm = _FakeLLM([_stage_a_with_thread_ref(), _indexer(), _stage_b()])

    transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    assert "Started 2026-W25 (2 weeks ago)" in llm.prompts[2]


# --- focus control -----------------------------------------------------------


def test_focus_selector_directs_stage_b(tmp_path):
    """The caller's focus selection reaches Stage B as a lead directive naming the
    chosen thread, and the selector is offered the threads active this week."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    new_thread = {"id": "collector-rewrite", "title": "Collector rewrite", "summary": "s"}
    llm = _FakeLLM([_stage_a(), _indexer(new_threads=[new_thread]), _stage_b()])

    offered: dict = {}

    def selector(candidates):
        offered["ids"] = [t.id for t in candidates]
        return ["collector-rewrite"]

    transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=memory_root,
        focus_selector=selector,
    )

    # The just-created thread is active this week and thus a candidate.
    assert offered["ids"] == ["collector-rewrite"]
    stage_b = llm.prompts[-1]
    # Marker unique to the injected block (the system prompt also says the words
    # "Focus directive" when describing the feature).
    assert "covers ONLY these threads" in stage_b
    assert "Collector rewrite" in stage_b


def test_focus_unknown_id_raises(tmp_path):
    """A focus id that is not active this week is a hard error, not a silent no-op."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    with pytest.raises(TransformError, match="not active this week"):
        transform_week(
            _config(),
            llm,
            raw_dir=raw_dir,
            drafts_dir=drafts_dir,
            week=week,
            memory_root=memory_root,
            focus_selector=lambda candidates: ["ghost"],
        )


def test_no_focus_selector_leaves_stage_b_unforced(tmp_path):
    """Default path (no selector): Stage B gets no focus directive and picks the
    lead itself — preserving the pre-focus behavior for CI."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    transform_week(
        _config(), llm, raw_dir=raw_dir, drafts_dir=drafts_dir, week=week, memory_root=memory_root
    )

    assert "covers ONLY these threads" not in llm.prompts[-1]


def test_focus_directive_follows_picked_order(tmp_path):
    """The directive lists threads in the order the caller picked (not candidate
    order), and names the first pick as the primary — so '4,3,1' leads on 4."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    new_threads = [
        {"id": "alpha", "title": "Alpha work", "summary": "a"},
        {"id": "beta", "title": "Beta work", "summary": "b"},
    ]
    llm = _FakeLLM([_stage_a(), _indexer(new_threads=new_threads), _stage_b()])

    transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=memory_root,
        focus_selector=lambda candidates: ["beta", "alpha"],  # reverse of candidate order
    )

    stage_b = llm.prompts[-1]
    # Primary is the first pick, and Beta is listed before Alpha.
    assert 'the primary, "Beta work"' in stage_b
    assert stage_b.index('1. "Beta work"') < stage_b.index('2. "Alpha work"')


def test_focus_directive_restricts_and_asks_per_topic_proof(tmp_path):
    """The directive tells Stage B to cover only the picked threads, give each its
    own proof-of-work link, and not force a single narrative."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    week = _write_activity(raw_dir)
    new_thread = {"id": "alpha", "title": "Alpha work", "summary": "a"}
    llm = _FakeLLM([_stage_a(), _indexer(new_threads=[new_thread]), _stage_b()])

    transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=memory_root,
        focus_selector=lambda candidates: ["alpha"],
    )

    stage_b = llm.prompts[-1]
    assert "covers ONLY these threads" in stage_b
    assert "own proof-of-work link" in stage_b
    assert "not listed here" in stage_b
    assert "separate things done this week" in stage_b


def test_active_threads_dedupes_by_id():
    """v0.7 (d): a thread id is unique identity, so even if the same id is active in
    two registries it is offered once (defensive net after v0.6.2)."""
    from pipeline.transform import RepoMemory, _active_threads

    def _mem(repo, title):
        reg = ThreadRegistry(
            threads=[Thread(id="shared", title=title, last_active_week="2026-W27")]
        )
        return RepoMemory(repo=repo, memory_dir=None, context="", registry=reg)

    active = _active_threads([_mem("o/a", "A"), _mem("o/b", "B")], "2026-W27")
    assert [t.id for t in active] == ["shared"]


def test_focus_candidates_carry_relation_and_age():
    """Each candidate is labelled with this week's relation (from an initiative's
    thread_ref, else new/active) and its age in whole weeks."""
    from pipeline.models import ThreadRef
    from pipeline.transform import _focus_candidates

    threads = [
        Thread(id="old", title="Old", started_week="2026-W23", last_active_week="2026-W27"),
        Thread(id="fresh", title="Fresh", started_week="2026-W27", last_active_week="2026-W27"),
    ]
    inits = _inits(("Work", "cat"))
    inits.initiatives[0].thread_ref = ThreadRef(id="old", relation="continues")

    cands = {c.id: c for c in _focus_candidates(threads, inits, "2026-W27")}
    assert cands["old"].relation == "continues"
    assert cands["old"].age_weeks == 4
    assert cands["old"].age_label == "4 weeks old"
    assert cands["fresh"].relation == "new this week"  # started this week, unreferenced
    assert cands["fresh"].age_weeks == 0


# --- topics: front matter (category dividers) -------------------------------


def test_repo_from_links_takes_first_github_owner_repo():
    from pipeline.transform import _repo_from_links

    assert _repo_from_links(["https://github.com/midnightntwrk/midnight-node/pull/1"]) == (
        "midnightntwrk/midnight-node"
    )
    # Non-GitHub links are skipped; the first GitHub one wins.
    mixed = ["https://example.com/x", "https://github.com/a/b/commit/deadbeef"]
    assert _repo_from_links(mixed) == "a/b"
    assert _repo_from_links([]) == ""
    assert _repo_from_links(["https://example.com/no-repo"]) == ""


def _inits(*specs):
    from pipeline.models import Initiative, Initiatives

    return Initiatives(
        initiatives=[
            Initiative(name=n, category=c, what="w", why_it_matters="y", links=list(links))
            for (n, c, *links) in specs
        ]
    )


def test_topics_title_is_the_rendered_heading_not_the_initiative_name():
    # The bug: the site keys its dividers off the ``##`` heading text, but the model
    # paraphrases headings away from the Stage A initiative name. ``title`` must be
    # the heading, with category/repo joined to the initiative by its PoW link.
    from pipeline.transform import _topics

    inits = _inits(
        (
            "Blockchain consensus regression testing and governance tooling",
            "Blockchain infrastructure",
            "https://github.com/midnightntwrk/midnight-node/pull/1934",
        ),
    )
    devlog = (
        "## Governance transaction CLI for local/federated operations\n\n"
        "Body text.\n\n"
        "Proof of work: https://github.com/midnightntwrk/midnight-node/pull/1934\n"
    )
    assert _topics(inits, devlog) == [
        {
            "title": "Governance transaction CLI for local/federated operations",
            "category": "Blockchain infrastructure",
            "repo": "midnightntwrk/midnight-node",
        }
    ]


def test_topics_one_per_rendered_section_not_per_initiative():
    # Fewer sections than initiatives: topics track the sections actually written.
    from pipeline.transform import _topics

    inits = _inits(
        ("Provenance", "Cryptography", "https://github.com/rsporny/portfolio-pipeline/pull/5"),
        ("Metadata", "Developer tooling", "https://github.com/rsporny/portfolio-pipeline/pull/7"),
        ("Unwritten", "Other", "https://github.com/rsporny/portfolio-pipeline/pull/9"),
    )
    # Two same-repo sections — the exact PoW URL disambiguates their categories.
    devlog = (
        "## Structured metadata for content presentation\n\n"
        "PoW: https://github.com/rsporny/portfolio-pipeline/pull/7\n\n"
        "## Cryptographic provenance for published content\n\n"
        "PoW: https://github.com/rsporny/portfolio-pipeline/pull/5\n"
    )
    assert _topics(inits, devlog) == [
        {
            "title": "Structured metadata for content presentation",
            "category": "Developer tooling",
            "repo": "rsporny/portfolio-pipeline",
        },
        {
            "title": "Cryptographic provenance for published content",
            "category": "Cryptography",
            "repo": "rsporny/portfolio-pipeline",
        },
    ]


def test_topics_omits_category_and_repo_for_unmatched_section():
    from pipeline.transform import _topics

    inits = _inits(("Signed feed", "automation", "https://github.com/rsporny/pp/pull/6"))
    devlog = "## A heading with no proof-of-work link\n\nJust prose.\n"
    assert _topics(inits, devlog) == [{"title": "A heading with no proof-of-work link"}]


def test_topics_empty_for_single_flowing_entry_without_sections():
    from pipeline.transform import _topics

    inits = _inits(("Signed feed", "automation", "https://github.com/rsporny/pp/pull/6"))
    assert _topics(inits, "A single weekly entry with no ## headings at all.\n") == []


def test_front_matter_topics_roundtrips_as_yaml():
    from pipeline.frontmatter import parse
    from pipeline.transform import _front_matter

    topics = [{"title": "A: with colon", "category": "blockchain", "repo": "o/r"}]
    fm = _front_matter("2026-W30", "2026-07-26T00:00:00+00:00", "t", ["A: with colon"], topics)
    front, _ = parse(fm + "body")
    assert front["topics"] == topics


def test_front_matter_omits_topics_when_none():
    from pipeline.transform import _front_matter

    fm = _front_matter("2026-W30", "gen", "t", ["Only"], None)
    assert "topics:" not in fm


# --- published-entry continuity ---------------------------------------------


def _write_published(site_dir, slug, source_initiatives, body, *, published_at="2026-06-01"):
    """Write a published devlog entry into a site dir (mirrors the adapter's
    front matter: series/slug/title/published_at/status/source_initiatives)."""
    from pipeline.frontmatter import dump

    front = {
        "type": "weekly-activity",
        "series": "Senior SDET log",
        "slug": slug,
        "title": slug.replace("-", " "),
        "published_at": published_at,
        "status": "published",
        "source_initiatives": list(source_initiatives),
    }
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / f"{slug}.md").write_text(dump(front, body))


def test_stage_b_receives_published_continuity(tmp_path):
    """A related past published entry's prose is fed into Stage B so arcs connect
    across weeks (matched here via the shared 'Collector' initiative name)."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    site_dir = tmp_path / "content" / "devlog"
    week = _write_activity(raw_dir)
    _write_published(site_dir, "prior-week", ["Collector"], "MARKER earlier collector prose.")
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
        site_dir=site_dir,
    )

    stage_b = llm.prompts[2]
    assert "your own earlier writing on related threads" in stage_b
    assert "MARKER earlier collector prose." in stage_b


def test_continuity_disabled_with_zero_max_entries(tmp_path):
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    site_dir = tmp_path / "content" / "devlog"
    week = _write_activity(raw_dir)
    _write_published(site_dir, "prior-week", ["Collector"], "MARKER earlier collector prose.")
    cfg = _config()
    cfg.content.continuity_max_entries = 0
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    transform_week(
        cfg,
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
        site_dir=site_dir,
    )

    stage_b = llm.prompts[2]
    assert "your own earlier writing on related threads" not in stage_b
    assert "MARKER earlier collector prose." not in stage_b


def test_missing_site_dir_does_not_break_continuity(tmp_path):
    """A fresh instance with no published content transforms cleanly — retrieval
    yields no context rather than erroring."""
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
        site_dir=tmp_path / "does-not-exist",
    )

    assert (out_dir / "devlog.md").exists()
    assert "your own earlier writing on related threads" not in llm.prompts[2]


def test_continuity_check_flags_reset_in_transform(tmp_path):
    """End to end: a thread with prior published coverage that Stage B frames as
    new trips the advisory continuity_not_reset check in the draft's checks."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    memory_root = tmp_path / "memory"
    site_dir = tmp_path / "content" / "devlog"
    week = _write_activity(raw_dir)
    _seed_thread(memory_root)  # thread 'collector' (started W25), referenced below
    _write_published(site_dir, "past", ["GitHub collector", "weekly activity"], "Collector prose.")
    # Stage B continues the 'collector' thread (cites its initiative link, pull/6)
    # but frames it as brand new.
    reset_b = _stage_b_bad(
        devlog=f"## Collector\n\nAlso new here. {_words_local(20)} https://github.com/o/r/pull/6"
    )
    llm = _FakeLLM([_stage_a_with_thread_ref(), _indexer(), reset_b])

    out_dir = transform_week(
        _config(),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=memory_root,
        site_dir=site_dir,
    )

    data = json.loads((out_dir / "checks.json").read_text())
    r = next(c for c in data if c["name"] == "continuity_not_reset")
    assert r["passed"] is False and r["severity"] == "warn"
    assert "collector" in r["detail"] and "new here" in r["detail"]


def _words_local(n: int) -> str:
    return " ".join(["word"] * n)


def test_published_continuity_is_redacted_before_stage_b(tmp_path):
    """The retrieved prose is redacted before the model call like every other
    input (hard constraint 5) — a forbidden phrase never reaches Stage B."""
    raw_dir, drafts_dir = tmp_path / "raw", tmp_path / "drafts"
    site_dir = tmp_path / "content" / "devlog"
    week = _write_activity(raw_dir)
    _write_published(site_dir, "prior-week", ["Collector"], "The SECRET collector detail.")
    llm = _FakeLLM([_stage_a(), _indexer(), _stage_b()])

    transform_week(
        _config(forbidden=["SECRET"]),
        llm,
        raw_dir=raw_dir,
        drafts_dir=drafts_dir,
        week=week,
        memory_root=tmp_path / "memory",
        site_dir=site_dir,
    )

    stage_b = llm.prompts[2]
    assert "your own earlier writing on related threads" in stage_b
    assert "SECRET" not in stage_b
    assert "[REDACTED]" in stage_b
