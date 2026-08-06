from __future__ import annotations

import pytest
import typer

from pipeline import cli
from pipeline.memory import Thread
from pipeline.transform import FocusCandidate, InitiativeWork, WorkItem


def _candidates() -> list[FocusCandidate]:
    return [
        FocusCandidate(
            thread=Thread(
                id="bridge-network",
                title="Bridge-funded local network",
                started_week="2026-W20",
                summary="Fund a local devnet from the bridge so integration tests run offline.",
            ),
            relation="continues",
            age_weeks=4,
            repo="midnightntwrk/midnight-node",
            work=[
                InitiativeWork(
                    name="Local devnet funding via the bridge",
                    items=[
                        WorkItem(
                            kind="pr",
                            ref="#1934",
                            title="fix: reserve-contracts CLI change-federated-ops on local-env",
                        ),
                        WorkItem(kind="pr", ref="#1936", title="chore: bump indexer", note="open"),
                        WorkItem(
                            kind="commit", ref="3f1a9c2", title="test: add block-production guard"
                        ),
                    ],
                )
            ],
        ),
        # No initiative cited this one — it exercises the evidence-free fallback.
        FocusCandidate(
            thread=Thread(id="node-robustness", title="Node & toolkit robustness"),
            relation="new this week",
            age_weeks=0,
            repo="midnightntwrk/midnight-node",
        ),
    ]


# --- --focus flag validation ------------------------------------------------


def test_focus_from_flag_accepts_known_ids():
    assert cli._focus_from_flag(["node-robustness"], _candidates()) == ["node-robustness"]


def test_focus_from_flag_keeps_multiple_ids_in_order():
    # The weekly workflow's `focus` dispatch input builds repeatable --focus args;
    # order is the lead order, so it must be preserved verbatim.
    assert cli._focus_from_flag(["node-robustness", "bridge-network"], _candidates()) == [
        "node-robustness",
        "bridge-network",
    ]


def test_focus_from_flag_rejects_unknown_id():
    with pytest.raises(typer.Exit):
        cli._focus_from_flag(["ghost"], _candidates())


# --- interactive index parsing ----------------------------------------------


def test_focus_interactively_maps_indices_and_dedupes(monkeypatch):
    # "1,2,1" → the two threads, in order, no duplicate.
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "1,2,1")
    assert cli._focus_interactively(_candidates()) == ["bridge-network", "node-robustness"]


def test_focus_interactively_empty_means_auto(monkeypatch):
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "")
    assert cli._focus_interactively(_candidates()) == []


def test_focus_interactively_ignores_out_of_range(monkeypatch):
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "1,9,x")
    assert cli._focus_interactively(_candidates()) == ["bridge-network"]


def test_focus_interactively_no_candidates_returns_empty():
    assert cli._focus_interactively([]) == []


def test_focus_interactively_shows_meta_and_the_cited_work(monkeypatch, capsys):
    # Thread titles/summaries are model-written and generalised, so the block is
    # carried by the owner's own PR/issue/commit titles, grouped under the
    # initiative that cited them. Meta (repo/status/age/relation) stays on one line,
    # and the id is printed as a ready-to-paste --focus argument.
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "")
    cli._focus_interactively(_candidates())
    out = capsys.readouterr().out
    assert "midnightntwrk/midnight-node · ongoing · 4 weeks old · this week: continues" in out
    assert "new this week" in out
    assert "> Local devnet funding via the bridge" in out
    assert "PR #1934" in out
    assert "fix: reserve-contracts CLI change-federated-ops on local-env" in out
    assert "commit 3f1a9c2" in out
    assert "(open)" in out  # an unmerged PR never reads as shipped work
    assert "--focus bridge-network" in out
    assert "run offline" not in out  # the rolling summary is no longer shown


def test_focus_interactively_says_new_this_week_once(monkeypatch, capsys):
    # A thread that just started reports "new this week" as both its age and its
    # relation; the meta line must not say it twice.
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "")
    cli._focus_interactively(_candidates())
    out = capsys.readouterr().out
    assert "new this week" in out
    assert "this week: new this week" not in out


def test_focus_interactively_says_when_no_work_was_cited(monkeypatch, capsys):
    # A thread the indexer touched that no initiative cited says so plainly —
    # with the summary dropped, silence would leave the block unexplained.
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "")
    cli._focus_interactively(_candidates())
    out = capsys.readouterr().out
    assert "(no work cited for this thread this week)" in out


def test_focus_interactively_caps_work_per_initiative(monkeypatch, capsys):
    # A busy initiative must not push the other candidates off the screen.
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "")
    items = [WorkItem(kind="pr", ref=f"#{n}", title=f"Title {n}") for n in range(1, 8)]
    cand = FocusCandidate(
        thread=Thread(id="busy", title="Busy"),
        relation="continues",
        age_weeks=1,
        work=[InitiativeWork(name="Lots", items=items)],
    )
    cli._focus_interactively([cand])
    out = capsys.readouterr().out
    assert "PR #4" in out
    assert "PR #5" not in out
    assert f"+ {7 - cli._WORK_PER_INITIATIVE} more" in out


def test_focus_interactively_ellipsises_a_long_title(monkeypatch, capsys):
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "")
    cand = FocusCandidate(
        thread=Thread(id="long", title="Long"),
        relation="continues",
        age_weeks=1,
        work=[InitiativeWork(name="One", items=[WorkItem(kind="pr", ref="#1", title="x" * 200)])],
    )
    cli._focus_interactively([cand])
    out = capsys.readouterr().out
    assert "x" * 200 not in out
    assert "…" in out


# --- selector resolution ----------------------------------------------------


def test_make_selector_non_interactive_is_auto(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli._make_focus_selector(None) is None


def test_make_selector_tty_is_interactive(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    assert cli._make_focus_selector(None) is cli._focus_interactively


def test_make_selector_with_flag_validates(monkeypatch):
    # A --focus flag takes precedence over the TTY check and validates ids.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    selector = cli._make_focus_selector(["node-robustness"])
    assert selector(_candidates()) == ["node-robustness"]
    with pytest.raises(typer.Exit):
        cli._make_focus_selector(["ghost"])(_candidates())


# --- provenance sub-app wiring (v0.5) ---------------------------------------

import yaml  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

_runner = CliRunner()


def _prov_config(tmp_path, **provenance):
    cfg = {
        "github_user": "rsporny",
        "repos": {"allowlist": ["o/r"]},
        "state": {"root": str(tmp_path)},
        "output": {"site_repo_path": str(tmp_path), "site_devlog_dir": "content/devlog"},
    }
    if provenance:
        cfg["provenance"] = provenance
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    (tmp_path / "content/devlog").mkdir(parents=True)
    return path


def test_provenance_subapp_registered():
    result = _runner.invoke(cli.app, ["provenance", "--help"])
    assert result.exit_code == 0
    for cmd in ("sign", "anchor", "verify", "show"):
        assert cmd in result.output


def test_provenance_anchor_without_log_errors(tmp_path):
    cfg = _prov_config(tmp_path)
    result = _runner.invoke(cli.app, ["provenance", "anchor", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "nothing to anchor" in result.output


def test_provenance_show_empty_log(tmp_path):
    cfg = _prov_config(tmp_path)
    result = _runner.invoke(cli.app, ["provenance", "show", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "empty" in result.output


def test_provenance_sign_without_key_errors(tmp_path):
    cfg = _prov_config(tmp_path)  # no provenance.signing.gpg_key
    result = _runner.invoke(
        cli.app, ["provenance", "sign", "--week", "2026-W27", "--config", str(cfg)]
    )
    assert result.exit_code == 1
    assert "gpg_key" in result.output


def test_provenance_verify_missing_pubkey_errors(tmp_path):
    cfg = _prov_config(tmp_path)
    result = _runner.invoke(cli.app, ["provenance", "verify", "--config", str(cfg)])
    assert result.exit_code == 1
    assert "public key not found" in result.output
