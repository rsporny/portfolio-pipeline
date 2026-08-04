from __future__ import annotations

import pytest
import yaml

from pipeline.memory import (
    Assumption,
    IndexerMutations,
    MemoryValidationError,
    ProposedAssumption,
    ProposedKeyDecision,
    ProposedThread,
    Thread,
    ThreadRegistry,
    ThreadUpdate,
    add_weeks,
    apply_mutations,
    load_context,
    load_registry,
    repo_memory_dir,
    reviews_due,
    save_registry,
    weeks_between,
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
                new_assumptions=[ProposedAssumption(text="Search API is enough")],
            )
        ]
    )
    out = apply_mutations(reg, muts, week="2026-W29")
    assumptions = out.get("collector").assumptions
    assert assumptions[0].status == "falsified"
    assert assumptions[1].text == "Search API is enough"
    assert assumptions[1].made_week == "2026-W29"  # stamped by code, not the model


def test_apply_new_key_decision_on_existing_thread():
    """v0.7 (a): the indexer can record a decision (e.g. a closed-unmerged PR is a
    postponement) on an EXISTING thread — not only on a brand-new one — and code
    stamps the run week."""
    reg = ThreadRegistry(threads=[_thread()])
    muts = IndexerMutations(
        updates=[
            ThreadUpdate(
                id="collector",
                new_key_decisions=[
                    ProposedKeyDecision(
                        decision="Postponed pagination refactor",
                        rationale="PR closed unmerged; approach needs rethinking",
                    )
                ],
            )
        ]
    )
    out = apply_mutations(reg, muts, week="2026-W29")
    decisions = out.get("collector").key_decisions
    assert decisions[-1].decision == "Postponed pagination refactor"
    assert decisions[-1].week == "2026-W29"  # stamped by code, not the model


def test_apply_new_thread_stamps_weeks():
    reg = ThreadRegistry()
    new = ProposedThread(id="memory-module", title="Memory", summary="Threads + assumptions.")
    out = apply_mutations(reg, IndexerMutations(new_threads=[new]), week="2026-W29")
    created = out.get("memory-module")
    assert created.started_week == "2026-W29"
    assert created.last_active_week == "2026-W29"


def test_new_thread_proposal_carries_no_weeks_and_is_stamped():
    """Regression (W28 midnight-node): the indexer proposes assumptions and
    key_decisions with NO week field — the week is not part of its contract, so
    it cannot send a null or a stray boolean there. The proposal validates and
    apply_mutations stamps the run week into every nested record."""
    # The proposal models expose only genuinely-editorial fields: no week (code
    # stamps it), no status on a new assumption (always born "open"), and no
    # review_by date (code derives it from a horizon in weeks).
    assert "made_week" not in ProposedAssumption.model_fields
    assert "status" not in ProposedAssumption.model_fields
    assert "review_by" not in ProposedAssumption.model_fields
    assert "week" not in ProposedKeyDecision.model_fields

    new = ProposedThread(
        id="bridge-network",
        title="Bridge-funded local network",
        assumptions=[ProposedAssumption(text="Bridge is reproducible locally")],
        key_decisions=[ProposedKeyDecision(decision="Use earthly", rationale="Containerized")],
    )
    out = apply_mutations(ThreadRegistry(), IndexerMutations(new_threads=[new]), week="2026-W28")
    created = out.get("bridge-network")
    assert created.assumptions[0].made_week == "2026-W28"
    assert created.key_decisions[0].week == "2026-W28"


def test_indexer_mutations_validate_from_json_without_weeks():
    """The real path: mutations arrive as parsed JSON with no week keys (as the
    prompt now instructs). model_validate must accept it — this is exactly the
    payload that used to raise before the proposal contract dropped the fields."""
    data = {
        "new_threads": [
            {
                "id": "t",
                "title": "T",
                # a stray code-owned field (made_week / an invented status) must be
                # ignored, not crash the whole proposal — resilience, not fragility.
                "assumptions": [{"text": "a", "made_week": True, "status": "proposed"}],
                "key_decisions": [{"decision": "d", "rationale": "r"}],
            }
        ]
    }
    muts = IndexerMutations.model_validate(data)  # must not raise
    out = apply_mutations(ThreadRegistry(), muts, week="2026-W28")
    created = out.get("t")
    assert created.assumptions[0].made_week == "2026-W28"
    assert created.assumptions[0].status == "open"  # stray "proposed" dropped
    assert created.key_decisions[0].week == "2026-W28"


def test_add_weeks_arithmetic_and_year_boundary():
    assert add_weeks("2026-W28", 8) == "2026-W36"
    assert add_weeks("2026-W28", 0) == "2026-W28"
    # real calendar math, not string surgery: 2026 is a 53-week ISO year,
    assert add_weeks("2026-W50", 3) == "2026-W53"  # so W53 exists,
    assert add_weeks("2026-W53", 1) == "2027-W01"  # and W53+1 rolls into 2027.
    # 2025 is a 52-week year — its boundary lands one week earlier:
    assert add_weeks("2025-W52", 2) == "2026-W02"


def test_weeks_between():
    assert weeks_between("2026-W25", "2026-W27") == 2
    assert weeks_between("2026-W28", "2026-W28") == 0
    assert weeks_between("2025-W52", "2026-W01") == 1  # 52-week year boundary
    assert weeks_between("2026-W27", "2026-W25") == -2  # reversed → negative


def test_review_after_weeks_becomes_future_review_by():
    """The horizon fix: the model proposes weeks-from-now; code turns it into an
    absolute review_by strictly AFTER made_week (never a past date, W28 bug)."""
    new = ProposedThread(
        id="t",
        title="T",
        assumptions=[ProposedAssumption(text="a", review_after_weeks=8)],
    )
    out = apply_mutations(ThreadRegistry(), IndexerMutations(new_threads=[new]), week="2026-W28")
    assumption = out.get("t").assumptions[0]
    assert assumption.review_by == "2026-W36"
    assert assumption.review_by > assumption.made_week


def test_no_horizon_means_no_review():
    for horizon in (None, 0, -3):
        proposed = ProposedAssumption(text="a", review_after_weeks=horizon)
        new = ProposedThread(id="t", title="T", assumptions=[proposed])
        muts = IndexerMutations(new_threads=[new])
        out = apply_mutations(ThreadRegistry(), muts, week="2026-W28")
        assert out.get("t").assumptions[0].review_by is None


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
    dup = ProposedThread(id="collector", title="GitHub collector")
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
