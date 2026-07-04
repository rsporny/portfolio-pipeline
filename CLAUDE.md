# CLAUDE.md — portfolio-pipeline

## What this project is

An automated "commit → content" pipeline: once a week it collects the owner's public development activity (commits, PRs from GitHub), then uses the Claude API in a two-stage process to turn it into content drafts: a devlog entry, LinkedIn posts (PL + EN), and a list of highlights worth revisiting later. Drafts land in an editorial queue — a human always reviews and approves before anything is published.

This is a building-in-public project: a practical experiment in wiring AI into a real engineering workflow, with a human in the loop. The repo itself is the subject of its own first devlog entries. It is public and represents the owner's work — code quality, tests, and the README matter as much as the tool itself.

## Owner and context

- Radosław Sporny, Senior SDET (15 years in test automation), GitHub: `rsporny`, website: sporny.pl (static HTML, Cloudflare Pages, deployed from GitHub).
- Writes and shares practical experience about test automation and AI in QA. Audience: experienced engineers and engineering leaders.
- Content tone: concrete, engineering-minded, first person, zero marketing fluff; numbers and decisions over tool names. Curious rather than promotional — "here's what I built and what I learned", never a sales pitch.

## Hard constraints (NEVER violate)

1. **Allowlist only:** the pipeline scans ONLY repositories explicitly listed in `config.yaml`. Default is deny. Only public/open-source repositories, or the owner's own private repositories, may be listed. Do not implement any exceptions or a "scan all" mode.
2. **Human-in-the-loop:** no content is ever published automatically. The pipeline's job ends at generating drafts in `drafts/`. Publishing is a separate, manually invoked command that operates only on files in `approved/`.
3. **Secrets:** environment variables only (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`). Never in code, config, or logs. The GitHub token is expected to be a fine-grained PAT with minimal scope; public repos are read without elevated permissions.
4. **Data redaction:** before sending anything to the Claude API, run it through a redaction step (configurable list of forbidden phrases). Log what was redacted.

## Stack and conventions

- Python 3.11+, dependency management: `uv` (fallback: pip + requirements.txt).
- CLI: `typer`. HTTP: `httpx`. Claude: official `anthropic` SDK, model `claude-sonnet-4-6`.
- Structure: `src/pipeline/` (package), `tests/` (pytest, mocked APIs — no real API calls in tests), `drafts/`, `approved/`, `published/`, `config.yaml`, `.github/workflows/weekly.yml`.
- Full type hints, `ruff` as linter/formatter.
- Commits: conventional commits, in English.
- Write tests alongside code, not after. This repo is public and its author is an SDET — the tests are part of the story it tells.

## Commands

- `uv run pipeline collect` — fetch activity from the last 7 days (or `--since`)
- `uv run pipeline transform` — two-stage AI transformation, writes drafts
- `uv run pipeline run` — collect + transform
- `uv run pipeline review` — list drafts awaiting editorial review
- `uv run pipeline publish` — copy approved files into the website repo (manual)
- `uv run pytest` — tests

## Detailed specification

See `SPEC.md` in the repo root.
