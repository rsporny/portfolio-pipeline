from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator


class RedactionConfig(BaseModel):
    forbidden_phrases: list[str] = []


class OutputConfig(BaseModel):
    site_repo_path: str = "~/code/sporny.pl"
    site_devlog_dir: str = "content/devlog"

    @property
    def site_repo(self) -> Path:
        return Path(self.site_repo_path).expanduser()


class AnthropicConfig(BaseModel):
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 4000


class LocaleConfig(BaseModel):
    timezone: str = "Europe/Warsaw"


class ReposConfig(BaseModel):
    allowlist: list[str]

    @field_validator("allowlist")
    @classmethod
    def must_not_be_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("repos.allowlist must contain at least one repository")
        return v


class Config(BaseModel):
    github_user: str
    repos: ReposConfig
    redaction: RedactionConfig = RedactionConfig()
    output: OutputConfig = OutputConfig()
    anthropic: AnthropicConfig = AnthropicConfig()
    locale: LocaleConfig = LocaleConfig()


def load_config(path: Path | str | None = None) -> Config:
    if path is None:
        path = Path("config.yaml")
    with Path(path).open() as f:
        data = yaml.safe_load(f)
    return Config.model_validate(data)
