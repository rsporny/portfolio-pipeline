from __future__ import annotations

import pytest
import typer

from pipeline import cli
from pipeline.memory import Thread


def _candidates() -> list[Thread]:
    return [
        Thread(id="bridge-network", title="Bridge-funded local network"),
        Thread(id="node-robustness", title="Node & toolkit robustness"),
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
