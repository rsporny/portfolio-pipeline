# CLAUDE.md — portfolio-pipeline

## What this project is

An automated "commit → content" pipeline: once a week it collects the owner's public development activity (commits, PRs, closed issues from GitHub) and uses the Claude API to turn it into content drafts — a titled, auto-numbered devlog entry, one channel-neutral English social post, and a list of highlights.
The pipeline has **memory**: a registry of work threads per organization/repository (`memory/{org}/{repo}/`), so weekly entries connect into longer arcs — features, assumptions, pivots — instead of isolated snapshots.
For a repo with an active thread the collector adds **selective deep context** — each PR's review discussion and linked issues (`activity.json` is `schema_version: 4`, each PR carrying a derived `outcome` so a closed-unmerged PR reads as a postponement, not shipped work). That input is anonymized in the collector — third-party logins/names/`@mentions`/`Co-authored-by` are masked to `[collaborator]` before `raw/` is written — so it informs understanding only and is never quoted or attributed.
The transformer runs in two model passes: **Stage A** distills a week's activity into technical *initiatives* (memory-aware), and **Stage B** writes the drafts — devlog, social post, highlights — from them.
It runs a **structural check suite** (`src/pipeline/checks.py`): every generated draft is scored for content-policy compliance and faithfulness, and `transform_week` blocks the run on a hard violation (solicitation, a leaked collaborator, an invented proof-of-work link). `pipeline eval` runs the same checks over golden cases (`evals/cases/`) and publishes a scorecard.
Before Stage B the transformer also pulls in **published-entry continuity** (`src/pipeline/continuity.py`): a few related *past published* devlog bodies (cheap token-overlap retrieval, bounded by `content.continuity_max_entries`), so arcs build on the actual published prose — distinct from memory's derived state.
The engine is **stateless**: it ships no committed `raw/`/`memory/`; every state area (`raw/`, `memory/`, `provenance/`, and the `drafts/`/`approved/`/`published/` working dirs) resolves under one configurable **`state.root`** via `Config.state_dir(name)`. An *instance* is a single tree — the site repo — holding its own `config.yaml`, state, and `content/`; the engine is run from there and writes nothing outside `state.root`.
Published entries carry **provenance** (`src/pipeline/provenance/`): each is committed to by the **plain `sha256` of its raw `<slug>.md`** (reproducible with `sha256sum`), is **GPG-signed over that same file** (a deliberate local act on the PR branch before merge — no key ever in CI, and `gpg --verify` works with stock gpg), and its hash is **anchored per-entry on Cardano testnet** (pluggable backend, `null` by default; metadata `{slug, sha256, v}`). Every entry is an *independent* proof — there is no merkle tree or cumulative root. State is an append-only ledger `provenance/log.jsonl`; the served commitment lives in the site manifest (never in the `.md`, whose bytes are the hash). `pipeline provenance verify` recomputes each file hash + signature (and, with `--chain`, the anchor); the `sporny_pl` adapter's `attach_provenance`/`attach_anchor` write the `.md.sig` sidecar + public key and carry a verify badge in `index.json`.
The per-version record of how these capabilities landed — and the roadmap for what's next — lives in SPEC.md's Status and Roadmap sections.

Publishing is a pull request against the owner's website repo; **merge = publish** (Cloudflare deploys on merge).
The pipeline never merges — a human always reviews first.

This is a building-in-public project: a practical experiment in wiring AI into a real engineering workflow, with a human in the loop.
The repo is public and represents the owner's work — code quality, tests, and the README matter as much as the tool itself.
It is designed to be forkable: the website owns its own presentation schema (site file front matter, the `index.json` manifest, per-series numbering), and all of it lives inside one adapter under `src/pipeline/site_adapter/`.
The pipeline core knows only the adapter's `render(entry, ctx) -> list[FileChange]` interface and a site-neutral `DevlogEntry`; no site-specific logic lives anywhere else.

## Owner and context

- Radosław Sporny, Senior SDET (15 years in test automation), GitHub: `rsporny`, website: sporny.pl (static HTML, Cloudflare Pages, deployed from GitHub).
- Writes and shares practical experience about test automation and AI in QA. Audience: experienced engineers and engineering leaders.
- Content tone: concrete, engineering-minded, first person, zero marketing fluff; numbers and decisions over tool names. Curious rather than promotional — "here's what I built and what I learned", never a sales pitch.

## Hard constraints (NEVER violate)

1. **Allowlist only:** the pipeline scans ONLY repositories explicitly listed in `config.yaml`. Default is deny. Only public/open-source repositories, or the owner's own private repositories, may be listed. No exceptions, no "scan all" mode.
2. **Human-in-the-loop:** the pipeline never publishes. CI only *opens* a PR against the website repo and never merges it. No code path may merge, auto-approve, or push to the website's main branch.
3. **Content policy:** generated content is knowledge sharing — never a pitch. No calls to action, no service offers, no solicitation. Never quote or name third parties; their input may inform context only, and names are redacted by default. Claim only what the collected activity supports.
4. **Secrets:** environment variables only (`ANTHROPIC_API_KEY`, `GH_ACTIVITY_TOKEN` read-only on allowlisted repos, `LANDING_PAGE_TOKEN` write-but-not-merge on the website repo). Never in code, config, or logs.
5. **Redaction before every model call:** forbidden phrases masked, third-party names redacted; log what was redacted.
6. **Model proposes, code disposes:** the indexer's memory mutations are applied deterministically by validated code — the model never writes files directly.

## Stack and conventions

- Python 3.11+, dependency management: `uv` (fallback: pip + requirements.txt).
- CLI: `typer`. HTTP: `httpx`. Claude: official `anthropic` SDK, model `claude-opus-4-8`.
- Structure: `src/pipeline/` (engine package), `src/pipeline/site_adapter/` (all site-specific logic), `tests/` (pytest, mocked APIs — no real API calls in tests), `evals/` (the engine's golden cases — engine test data, not instance state), `config.yaml` (an example; a real instance's config lives in its site repo), `.github/workflows/`. State dirs (`raw/`, `memory/`, `provenance/`, working dirs) belong to the *instance* under `state.root`, not the engine repo — they are gitignored here.
- Full type hints, `ruff` as linter/formatter.
- Commits: conventional commits, in English. Releases are git-tagged from v0.6 onward as signed annotated tags `vMAJOR.MINOR.PATCH`. The weekly CI opens one PR against the site repo bundling the rendered devlog with its `raw/` + `memory/` state; it commits nothing back to this repo.
- Write tests alongside code, not after. This repo is public and its author is an SDET — the tests are part of the story it tells.

## Commands

- `uv run pipeline collect` — fetch activity from the last 7 days (or `--since`)
- `uv run pipeline transform` — Stage A → indexer → Stage B, writes drafts and memory updates. Add `--focus <thread-id>` (repeatable) to lead the entry on specific thread(s); omit it for an interactive pick (a non-interactive run auto-picks)
- `uv run pipeline run` — collect + transform
- `uv run pipeline review` — list drafts awaiting review (local/offline flow)
- `uv run pipeline publish --site-repo <path>` — render via the site adapter into a website checkout (used by CI and locally)
- `uv run pipeline publish-custom <file.md> --site-repo <path>` — turn a hand-written Markdown file (first `# H1` = title) into a `type: custom` devlog entry in the website repo + regenerate the manifest (numbering assigned by the pipeline; file-only, no push)
- `uv run pipeline eval` — v0.4 eval suite: run the transformer over the golden cases in `evals/cases/` and score the output with the structural check library, writing a scorecard to `evals/RESULTS.md`. Requires `ANTHROPIC_API_KEY`; exits non-zero on any error-severity check failure. `--case <id>` (repeatable) runs a subset
- `uv run pipeline provenance sign --week <YYYY-Wnn>` — v0.5: GPG-sign a published entry's raw `.md` (run on the PR branch before merge), write the `<slug>.md.sig` sidecar + `pubkey.asc` via the adapter, add the verify badge to the manifest, and record the entry in the ledger. The `.md` is never modified. Also `--slug` for custom entries
- `uv run pipeline provenance anchor [--week <YYYY-Wnn>|--slug <slug>]` — anchor an entry's `sha256` on-chain via `provenance.anchor.backend` (`null`/`file`/`cardano` testnet); no target ⇒ every not-yet-anchored entry. Refreshes the manifest anchor badge
- `uv run pipeline provenance verify [--chain]` — recompute each entry's file hash + signature (offline); `--chain` also reads each anchor back. Non-zero on any failure
- `uv run pipeline provenance show [--slug <slug>]` — print each entry's file hash, signature, and anchor
- `uv run pytest` — tests

## Detailed specification

See `SPEC.md` in the repo root. Do not implement roadmap items (v0.5+) ahead of schedule.
