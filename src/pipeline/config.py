from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator


class RedactionConfig(BaseModel):
    forbidden_phrases: list[str] = []
    # When true, GitHub logins / display names / @mentions of anyone other than
    # github_user are masked to role_placeholder before any model call and before
    # raw/ is written (raw/ is a public repo). See SPEC Module 3, constraints 3 & 5.
    redact_third_party_names: bool = True
    role_placeholder: str = "[collaborator]"


class OutputConfig(BaseModel):
    # Defaults to the current dir: an instance is a single tree (the site repo)
    # holding config.yaml, state (state.root), and content/. Override to target a
    # separate site checkout (e.g. from CI, or when the engine runs elsewhere).
    site_repo_path: str = "."
    site_devlog_dir: str = "content/devlog"
    # Selects the site-specific renderer in src/pipeline/site_adapter/. All
    # landing-page knowledge lives there; forking means writing another adapter.
    adapter: str = "sporny_pl"

    @property
    def site_repo(self) -> Path:
        return Path(self.site_repo_path).expanduser()


class AnthropicConfig(BaseModel):
    model: str = "claude-opus-4-8"
    max_tokens: int = 4000


class LocaleConfig(BaseModel):
    timezone: str = "Europe/Warsaw"


class ContentConfig(BaseModel):
    # The series identity. Weeklies emit a bare subtitle; the site manifest
    # assigns the per-series number and the site renders "Senior SDET log #N: …".
    devlog_title_prefix: str = "Senior SDET log"


class StateConfig(BaseModel):
    # Root under which ALL instance state resolves: raw/, memory/, provenance/,
    # drafts/, approved/, published/. The engine is stateless — it ships none of
    # these and writes nothing outside this root. Defaults to the current dir so
    # an instance (the site repo) can hold its own config + state + content, and
    # the engine is simply run from there. A fork points this wherever it likes.
    root: str = "."

    @property
    def root_path(self) -> Path:
        return Path(self.root).expanduser()


class ProvenanceSigningConfig(BaseModel):
    method: str = "gpg"
    gpg_key: str = ""  # fingerprint or uid passed to `gpg --local-user`


class ProvenanceAnchorConfig(BaseModel):
    backend: str = "null"  # null | file | cardano
    network: str = "preview"  # preview | preprod (testnet only in v0.5)
    metadata_label: int = 8272025

    @field_validator("backend", mode="before")
    @classmethod
    def _coerce_yaml_null(cls, v: object) -> object:
        # `backend: null` in YAML parses to None, not the string "null" — accept
        # both so the obvious config isn't a footgun.
        return "null" if v is None else v


class ProvenanceConfig(BaseModel):
    # v0.5: signed entries + a per-entry transparency ledger, each hash anchored
    # on-chain. Absent or disabled ⇒ dormant; the pipeline behaves as before.
    enabled: bool = False
    public_key: str = "provenance/pubkey.asc"  # armored pubkey, relative to state.root
    signing: ProvenanceSigningConfig = ProvenanceSigningConfig()
    anchor: ProvenanceAnchorConfig = ProvenanceAnchorConfig()


class ReposConfig(BaseModel):
    allowlist: list[str]
    # Optional per-repo domain context, used to categorize and generalize the
    # work for a broad audience (e.g. "…/midnight-node": "blockchain node …").
    descriptions: dict[str, str] = {}

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
    content: ContentConfig = ContentConfig()
    state: StateConfig = StateConfig()
    provenance: ProvenanceConfig = ProvenanceConfig()

    def state_dir(self, name: str) -> Path:
        """Resolve a state subdir (``raw``, ``memory``, ``drafts``, ``approved``,
        ``published``, ``provenance``) under ``state.root``. The single place the
        engine turns a logical state area into a path — nothing hardcodes these."""
        return self.state.root_path / name


def load_config(path: Path | str | None = None) -> Config:
    if path is None:
        path = Path("config.yaml")
    with Path(path).open() as f:
        data = yaml.safe_load(f)
    return Config.model_validate(data)
