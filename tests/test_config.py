from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from pipeline.config import load_config


@pytest.fixture
def config_file(tmp_path: Path):
    def _write(data: dict[str, Any]) -> Path:
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(data))
        return p

    return _write


MINIMAL = {
    "github_user": "rsporny",
    "repos": {"allowlist": ["rsporny/portfolio-pipeline"]},
}


def test_load_valid_config(config_file):
    cfg = load_config(config_file(MINIMAL))
    assert cfg.github_user == "rsporny"
    assert "rsporny/portfolio-pipeline" in cfg.repos.allowlist


def test_defaults_are_applied(config_file):
    cfg = load_config(config_file(MINIMAL))
    assert cfg.anthropic.model == "claude-opus-4-8"
    assert cfg.anthropic.max_tokens == 4000
    assert cfg.locale.timezone == "Europe/Warsaw"
    assert cfg.redaction.forbidden_phrases == []


def test_empty_allowlist_raises(config_file):
    data = {"github_user": "rsporny", "repos": {"allowlist": []}}
    with pytest.raises(Exception, match="allowlist"):
        load_config(config_file(data))


def test_missing_github_user_raises(config_file):
    data = {"repos": {"allowlist": ["rsporny/portfolio-pipeline"]}}
    with pytest.raises(Exception):
        load_config(config_file(data))


def test_custom_forbidden_phrases(config_file):
    data = {**MINIMAL, "redaction": {"forbidden_phrases": ["secret", "internal"]}}
    cfg = load_config(config_file(data))
    assert cfg.redaction.forbidden_phrases == ["secret", "internal"]


def test_output_site_repo_expands_tilde(config_file):
    data = {**MINIMAL, "output": {"site_repo_path": "~/repos/landing-page"}}
    cfg = load_config(config_file(data))
    assert not str(cfg.output.site_repo).startswith("~")


def test_multiple_repos_on_allowlist(config_file):
    data = {
        **MINIMAL,
        "repos": {"allowlist": ["rsporny/portfolio-pipeline", "midnightntwrk/midnight-node"]},
    }
    cfg = load_config(config_file(data))
    assert len(cfg.repos.allowlist) == 2
    assert "midnightntwrk/midnight-node" in cfg.repos.allowlist
