from __future__ import annotations

import pytest
import yaml

from pipeline.memory import (
    Assumption,
    IndexerMutations,
    MemoryValidationError,
    Thread,
    ThreadRegistry,
    ThreadUpdate,
    apply_mutations,
    load_context,
    load_registry,
    repo_memory_dir,
    reviews_due,
    save_registry,
)


def _thread(**kw):
    base = dict(
        id="collector",
        title="GitHub collector",
        status="ongoing",
        started_week="2026-W27",
        last_active_week="2026-W27",
        summary="Fetches weekly activity.",
    )
    base.update(kw)
    return Thread(**base)


# --- paths ------------------------------------------------------------------


def test_repo_memory_dir_nests_org_and_repo(tmp_path):
    d = repo_memory_dir(tmp_path / "memory", "midnightntwrk/midnight-node")
    assert d == tmp_path / "memory" / "midnightntwrk" / "midnight-node"


def test_repo_memory_dir_rejects_bad_slug(tmp_path):
    with pytest.raises(MemoryValidationError):
        repo_memory_dir(tmp_path, "not-a-slug")


# --- load / save ------------------------------------------------------------


def test_load_registry_missing_is_empty(tmp_path):
    assert load_registry(tmp_path).threads == []


def test_load_context_missing_is_empty(tmp_path):
    assert load_context(tmp_path) == ""


def test_registry_roundtrip(tmp_path):
    reg = ThreadRegistry(
        threads=[
            _thread(
                assumptions=[Assumption(text="Diffs unneeded", made_week="2026-W27")],
            )
        ]
    )
    save_registry(reg, tmp_path)
    reloaded = load_registry(tmp_path)
    assert reloaded.threads[0].id == "collector"
    assert reloaded.threads[0].assumptions[0].text == "Diffs unneeded"
    assert reloaded.threads[0].assumptions[0].status == "open"


def test_load_invalid_status_raises(tmp_path):
    (tmp_path / "threads.yaml").write_text(
        yaml.safe_dump(
            {
                "threads": [
                    {
                        "id": "x",
                        "title": "X",
                        "status": "bogus",
                        "started_week": "2026-W27",
                        "last_active_week": "2026-W27",
                    }
                ]
            }
        )
    )
    with pytest.raises(Exception):  # pydantic ValidationError
        load_registry(tmp_path)


# --- apply_mutations --------------------------------------------------------


def test_apply_update_changes_fields_and_bumps_week():
    reg = ThreadRegistry(threads=[_thread(status="ongoing")])
    muts = IndexerMutations(
        updates=[ThreadUpdate(id="collector", summary="Now paginates.", status="done")]
    )
    out = apply_mutations(reg, muts, week="2026-W29")
    t = out.get("collector")
    assert t.summary == "Now paginates."
    assert t.status == "done"
    assert t.last_active_week == "2026-W29"
    # original untouched
    assert reg.get("collector").summary == "Fetches weekly activity."


def test_apply_assumption_status_change_and_new_assumption():
    reg = ThreadRegistry(
        threads=[_thread(assumptions=[Assumption(text="Diffs unneeded", made_week="2026-W27")])]
    )
    muts = IndexerMutations(
        updates=[
            ThreadUpdate(
                id="collector",
                assumption_updates=[{"text": "Diffs unneeded", "status": "falsified"}],
                new_assumptions=[Assumption(text="Search API is enough", made_week="2026-W29")],
            )
        ]
    )
    out = apply_mutations(reg, muts, week="2026-W29")
    assumptions = out.get("collector").assumptions
    assert assumptions[0].status == "falsified"
    assert assumptions[1].text == "Search API is enough"


def test_apply_new_thread_defaults_weeks():
    reg = ThreadRegistry()
    new = Thread(
        id="memory-module",
        title="Memory",
        status="ongoing",
        started_week="",
        last_active_week="",
        summary="Threads + assumptions.",
    )
    out = apply_mutations(reg, IndexerMutations(new_threads=[new]), week="2026-W29")
    created = out.get("memory-module")
    assert created.started_week == "2026-W29"
    assert created.last_active_week == "2026-W29"


def test_apply_rejects_unknown_thread():
    with pytest.raises(MemoryValidationError, match="unknown thread"):
        apply_mutations(
            ThreadRegistry(),
            IndexerMutations(updates=[ThreadUpdate(id="ghost")]),
            week="2026-W29",
        )


def test_apply_rejects_unknown_assumption():
    reg = ThreadRegistry(threads=[_thread()])
    muts = IndexerMutations(
        updates=[
            ThreadUpdate(
                id="collector", assumption_updates=[{"text": "nope", "status": "confirmed"}]
            )
        ]
    )
    with pytest.raises(MemoryValidationError, match="not found"):
        apply_mutations(reg, muts, week="2026-W29")


def test_apply_rejects_duplicate_new_thread():
    reg = ThreadRegistry(threads=[_thread(id="collector")])
    dup = _thread(id="collector")
    with pytest.raises(MemoryValidationError, match="already exists"):
        apply_mutations(reg, IndexerMutations(new_threads=[dup]), week="2026-W29")


# --- reviews_due ------------------------------------------------------------


def test_reviews_due_includes_open_and_due():
    reg = ThreadRegistry(
        threads=[
            _thread(
                assumptions=[
                    Assumption(text="due+open", made_week="2026-W27", review_by="2026-W29"),
                    Assumption(
                        text="due+confirmed",
                        made_week="2026-W27",
                        status="confirmed",
                        review_by="2026-W29",
                    ),
                    Assumption(text="future", made_week="2026-W27", review_by="2026-W40"),
                    Assumption(text="no-review", made_week="2026-W27"),
                ]
            )
        ]
    )
    due = reviews_due(reg, "2026-W29")
    texts = {a.text for _, a in due}
    assert texts == {"due+open"}


def test_reviews_due_zero_padded_week_ordering():
    reg = ThreadRegistry(
        threads=[
            _thread(assumptions=[Assumption(text="a", made_week="2026-W09", review_by="2026-W09")])
        ]
    )
    assert reviews_due(reg, "2026-W10")  # W09 <= W10 lexically
    assert not reviews_due(reg, "2026-W08")
