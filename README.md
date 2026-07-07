# portfolio-pipeline

Turn a week of real engineering work into content drafts — with a human in the loop.

Once a week this collects my public development activity (commits, PRs, closed
issues from GitHub), then uses the Claude API in a two-stage process to write a
**devlog** entry, a **social post**, and a list of **highlights**. Drafts land in
an editorial queue as a pull request; nothing is published until I review and
merge. The output feeds the devlog on [sporny.pl](https://sporny.pl/devlog.html).

This is a building-in-public experiment: a practical look at wiring AI into a
real engineering workflow, with a human gate at the end. The repo is the subject
of its own first devlog entries — and it's meant to be **forkable**, so anyone
can automate their own devlog.

## Data flow

```
  collect ──► raw/YYYY-Wnn/activity.json          (GitHub activity, versioned schema)
     │
     ▼
  redact  ──► mask forbidden phrases before any API call   (default: none)
     │
     ▼
  transform ─► Stage A: technical summary (initiatives + categories + proof-of-work links)
     │        Stage B: writing (titled devlog, one social post, highlights)
     ▼
  drafts/YYYY-Wnn/{devlog.md, social.md, highlights.md, summary-tech.md (+ .json)}
     │
     │   ── weekly GitHub Action ──►  pull request to the sporny.pl repo
     │                                (devlog entry + manifest; social/highlights in the PR body)
     │
  [ human reviews the PR, edits if needed, merges ]  ──►  Cloudflare deploys
     │
     │   ── or, locally ──►  review → move to approved/ → publish → published/
```

## Human in the loop

**Nothing is ever published automatically.** The pipeline's job ends at proposing
drafts. In the automated flow, the weekly Action only *opens* a pull request — it
never merges. The merge is the manual approval, and it's where I review the actual
content (and can edit it) before Cloudflare deploys. In the local flow, `publish`
operates only on files I've deliberately moved into `approved/`.

## Allowlist only (by design)

The collector scans **only** the repositories explicitly listed in
`config.yaml` under `repos.allowlist`. There is no "scan everything" mode, and
the default is deny. Only public/open-source repositories, or my own private
repositories, may be listed.

This is deliberate: a tool that reads your development activity should read
exactly what you tell it to and nothing more. It keeps the blast radius small,
makes runs reproducible, and means the GitHub token can be a fine-grained PAT
with read-only scope on a handful of named repos.

## Quickstart

```bash
# 1. Install (uv-managed)
uv sync

# 2. Configure — copy config.yaml and edit repos.allowlist + descriptions
$EDITOR config.yaml

# 3. Secrets via environment only (never in code/config/logs)
export GH_ACTIVITY_TOKEN=github_pat_... # fine-grained, read-only on allowlisted repos
export ANTHROPIC_API_KEY=sk-ant-...

# 4. Run
uv run pipeline collect            # fetch the last 7 days (or --since / --until)
uv run pipeline transform          # two-stage AI transform → drafts/
uv run pipeline run                # collect + transform
uv run pipeline review             # list drafts awaiting review
uv run pipeline publish            # copy approved/ devlog into the sporny.pl repo (manual, no push)

uv run pytest                      # the tests are part of the story
```

Weekly automation lives in `.github/workflows/weekly.yml` — see SPEC.md
Module 4. It opens a PR against the website repo; merging it publishes.

## Sample draft

A real Stage B devlog opening (from `2026-W27`):

```markdown
# Senior SDET log #1: turning panics into exit codes, and a pipeline that
  writes about my own week

Most of my week sat at the boundary between test automation and the systems
under test — blockchain node tooling written in Rust, plus a side project that
turns my own engineering activity into readable drafts. ...

The most satisfying thread was crash resilience in a node's command-line
toolkit. Fault-injection testing kept surfacing runtime panics ... The decision
was to stop treating expected conditions as bugs ... Think of it as the
difference between a vending machine that shows "out of stock" versus one that
catches fire. Outcome: 109 tests passing and the "No Rust Panics" property
holding under fault injection. PR: https://github.com/midnightntwrk/midnight-node/pull/1822
```

The work is categorized and generalized for a broad audience, and each thread
ends with a proof-of-work link.

## Stack

Python 3.11+, [`uv`](https://docs.astral.sh/uv/), `typer` (CLI), `httpx`
(GitHub), the official `anthropic` SDK (model `claude-opus-4-8`), `pydantic`
(schemas + validation), `ruff` (lint/format), `pytest` (mocked APIs — zero real
network calls in tests). Full detail in [SPEC.md](SPEC.md).
