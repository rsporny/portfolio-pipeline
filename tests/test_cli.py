from __future__ import annotations

import pytest
import typer

from pipeline import cli
from pipeline.memory import Thread
from pipeline.transform import FocusCandidate


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
        ),
        FocusCandidate(
            thread=Thread(id="node-robustness", title="Node & toolkit robustness"),
            relation="new this week",
            age_weeks=0,
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


def test_focus_interactively_labels_status_age_relation_snippet(monkeypatch, capsys):
    # The label must carry enough to tell terse/near-identical titles apart:
    # status, age, this week's relation, and a summary snippet (v0.7 (d)).
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "")
    cli._focus_interactively(_candidates())
    out = capsys.readouterr().out
    assert "4 weeks old" in out
    assert "new this week" in out
    assert "this week: continues" in out
    assert "ongoing" in out  # thread.status
    assert "run offline" in out  # from the summary snippet


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
