# SPEC.md — portfolio-pipeline: MVP functional specification

## Goal

Once a week, turn real development work into content drafts (a titled devlog, one channel-neutral social post, highlights), with a human in the loop. Owner's weekly cost: ≤30 min of editing.

## Data flow

```
collect ──► raw/YYYY-Wnn/activity.json
transform ──► stage A (technical summary) ──► stage B (writing)
          ──► drafts/YYYY-Wnn/{devlog.md, social.md, highlights.md, summary-tech.md (+ summary-tech.json)}
[human edits, moves files to approved/]
publish ──► copies approved/* into a local clone of the sporny.pl website repo (path in config)
```

Content is written for a broad engineering audience (readers who don't know the
specific repos): the work is categorized and generalized, and the devlog ends
with a proof-of-work link. Social output is one channel-neutral English post;
the website owns the per-platform (LinkedIn / X / …) share buttons.

## Module 1: Collector

Source: GitHub REST API (token from env `GITHUB_TOKEN`; a fine-grained PAT with minimal scope — public repositories are readable without elevated permissions).

For each repo on the allowlist in `config.yaml`, fetch within the given time window (default 7 days, override with `--since` / `--until`):
- commits authored by the configured user (`github_user: rsporny`): sha, date, message, `url`, list of changed files with stats (no diff contents in the MVP — token economy; `include_diffs: false` config flag reserved for the future),
- PRs created by the user: title, description, labels, status, `url`,
- issues assigned to the user and closed in the window: title, description, `url`.

The `url` (GitHub `html_url`) is captured so drafts can cite proof of work.

Output: `raw/YYYY-Wnn/activity.json` (versioned schema, currently `schema_version: 2`). If there is no activity — write an empty file and exit with an informational message, not an error.

### Config (`config.yaml`)

```yaml
github_user: rsporny
repos:
  allowlist:
    - rsporny/portfolio-pipeline
    # public/open-source repos the owner contributes to,
    # or the owner's own private repos — nothing else
  descriptions:                        # optional per-repo domain context
    rsporny/portfolio-pipeline: "Commit→content pipeline (Python)."
redaction:
  forbidden_phrases: []
output:
  site_repo_path: ~/code/sporny.pl   # target of the publish command
  site_devlog_dir: content/devlog
anthropic:
  model: claude-opus-4-8
  max_tokens: 4000
locale:
  timezone: Europe/Warsaw
content:
  devlog_title_prefix: Senior SDET log   # devlog title becomes "<prefix> #N: …"
```

## Module 2: Transformer (two-stage)

Step 0 — redaction: remove/mask phrases from `redaction.forbidden_phrases` in the input data, log the number of maskings.

### Stage A — technical summary

Prompt (parameterized with `activity.json` data **and** the repo descriptions
from config, so the work can be categorized and generalized). Per initiative:
`name`, `category` (a domain label a general engineer recognizes), `what`,
`why_it_matters`, `tech`, and `links` (commit/PR URLs = proof of work). Expected
JSON: `{"initiatives": [{"name", "category", "what", "why_it_matters", "tech": [], "links": []}]}`.

Output: `drafts/YYYY-Wnn/summary-tech.md` (rendered from JSON) + `summary-tech.json` (the raw JSON) next to it.

### Stage B — writing

Input: JSON from stage A. The prompt targets a broad engineering audience that
does not know the specific repos, and produces:

1. `title` — an auto-numbered devlog title `"<content.devlog_title_prefix> #N: <subtitle>"`, where N = (devlogs already in `published/`) + 1.
2. `devlog` (English, 350–550 words) — opens with generalized context (what domain, why a general engineer should care), explains the work without assuming repo knowledge (a short example/analogy where it helps), follows problem → decision → outcome, and ends with a proof-of-work link.
3. `social` (100–180 words, English) — one channel-neutral post (hook first line, one concrete lesson, ≤3 hashtags, no CTA) that draws the reader to the full devlog. The website owns per-platform share buttons.
4. `highlights` — notable items worth revisiting, one sentence each, tagged with the initiative name.

Respond ONLY with JSON: `{"title", "devlog", "social", "highlights": []}`.

Output: `devlog.md`, `social.md`, `highlights.md` in `drafts/YYYY-Wnn/`, each with front matter (`title`, `status: draft`, `week`, `generated_at`, `source_initiatives`).

Error handling: retry with backoff (3 attempts) on API errors; JSON validation (strip ```json fences); on failure, save the raw response to `drafts/YYYY-Wnn/_failed_raw.txt` and exit with a clear error.

## Module 3: Review and Publish

- `pipeline review`: a table of drafts (week, file, status) based on front matter; the human changes status by moving the file to `approved/YYYY-Wnn/` and editing the content freely.
- `pipeline publish`: copies files from `approved/` to `output.site_repo_path/output.site_devlog_dir`, sets `status: published`, moves them locally to `published/`. It does NOT commit and does NOT push the website repo — that stays deliberately manual.

## Module 4: Automation

GitHub Actions `.github/workflows/weekly.yml`:
- cron: Sunday 16:00 UTC (18:00 CEST),
- runs `pipeline run`,
- commits new `raw/` and `drafts/` files to a `drafts/YYYY-Wnn` branch and opens a PR — the PR is the editorial queue,
- secrets: `ANTHROPIC_API_KEY`, `GITHUB_TOKEN` (fine-grained, repo-scoped),
- `workflow_dispatch` with a `since` input for manual runs.

## Tests (pytest)

- collector: parsing GitHub API responses (fixtures with sample JSON), filtering by author and time window, allowlist enforcement (repo not on the list → skipped + warning).
- redaction: phrase masking, case-insensitive.
- transformer: validating/parsing model responses (valid JSON, JSON in fences, broken JSON → failure path), retry logic (mocked).
- review/publish: file operations in tmp_path, front-matter round-trip.
- Zero real network calls in tests.

## README.md

This is a public, building-in-public repo. The README must include: a data-flow diagram, the human-in-the-loop philosophy, a section explaining the allowlist-only design (why the pipeline deliberately reads only explicitly listed public repos), a quickstart, and a sample draft. Framing: a personal experiment in AI-assisted engineering workflows — a tool anyone can fork to automate their own devlog. Language: English.

## Out of scope for the MVP (do not implement now)

- Diff content analysis, voice notes as a source, long-form article generator, auto-push to the website repo, web dashboard, multi-user support.

## Implementation order (milestones)

1. Project skeleton: structure, config loader, CLI, CI with ruff + pytest.
2. Collector + tests (mocked GitHub API).
3. Transformer stage A + B + redaction + tests.
4. Review + publish + tests.
5. GitHub Actions workflow + README.

After each milestone: a working state, green tests, a commit.
