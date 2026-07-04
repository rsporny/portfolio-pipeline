# SPEC.md — portfolio-pipeline: MVP functional specification

## Goal

Once a week, turn real development work into content drafts (devlog, LinkedIn posts, highlights), with a human in the loop. Owner's weekly cost: ≤30 min of editing.

## Data flow

```
collect ──► raw/YYYY-Wnn/activity.json
transform ──► stage A (technical summary) ──► stage B (writing)
          ──► drafts/YYYY-Wnn/{devlog.md, linkedin-pl.md, linkedin-en.md, highlights.md, summary-tech.md}
[human edits, moves files to approved/]
publish ──► copies approved/* into a local clone of the sporny.pl website repo (path in config)
```

## Module 1: Collector

Source: GitHub REST API (token from env `GITHUB_TOKEN`; a fine-grained PAT with minimal scope — public repositories are readable without elevated permissions).

For each repo on the allowlist in `config.yaml`, fetch within the given time window (default 7 days, override with `--since` / `--until`):
- commits authored by the configured user (`github_user: rsporny`): sha, date, message, list of changed files with stats (no diff contents in the MVP — token economy; `include_diffs: false` config flag reserved for the future),
- PRs created/merged: title, description, labels, status,
- issues closed by the user: title, description.

Output: `raw/YYYY-Wnn/activity.json` (versioned schema). If there is no activity — write an empty file and exit with an informational message, not an error.

### Config (`config.yaml`)

```yaml
github_user: rsporny
repos:
  allowlist:
    - rsporny/portfolio-pipeline
    # public/open-source repos the owner contributes to,
    # or the owner's own private repos — nothing else
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
```

## Module 2: Transformer (two-stage)

Step 0 — redaction: remove/mask phrases from `redaction.forbidden_phrases` in the input data, log the number of maskings.

### Stage A — technical summary

Prompt (parameterized with `activity.json` data), expected JSON output:

```
You are an engineer's assistant. Based on the git activity below from a single
week, group the work into 2–5 initiatives. For each: name, what was done
(3–5 sentences, technical, in English), why it matters from an engineering
standpoint, technologies used. Ignore cosmetic commits (typos, formatting)
unless they add up to something bigger. Respond ONLY with valid JSON matching
this schema: {"initiatives": [{"name", "what", "why_it_matters", "tech": []}]}
```

Output: `drafts/YYYY-Wnn/summary-tech.md` (rendered from JSON) + the raw JSON next to it.

### Stage B — writing

Input: JSON from stage A. Prompt:

```
You are helping a senior SDET (15 years in test automation) write about his
week of engineering work. He shares practical, hands-on experience with test
automation and AI in engineering workflows. Audience: experienced engineers
and engineering leaders. Tone: concrete, engineering-minded, first person,
curious rather than promotional, no buzzwords or exclamation marks, numbers
and decisions over tool names. This is knowledge sharing — "what I built and
what I learned" — never a pitch. Based on the initiatives below, generate:

1. DEVLOG (in English, 300–500 words): a weekly "what I built and what I
   learned" entry, in a problem → decision → outcome format.
2. LINKEDIN_PL (100–180 words, in Polish): one post about the most
   interesting initiative, hook in the first line, one concrete observation
   or lesson, no hashtag wall (max 3 hashtags), no call to action.
3. LINKEDIN_EN: an independently written (not 1:1 translated) English
   counterpart.
4. HIGHLIGHTS: a list of notable items from this week worth revisiting later
   (a metric, an architectural decision, a measurable result) — one sentence
   each, tagged with the initiative name.

Respond ONLY with JSON: {"devlog", "linkedin_pl", "linkedin_en",
"highlights": []}
```

Output: separate `.md` files in `drafts/YYYY-Wnn/`, each with front matter (`status: draft`, `week`, `generated_at`, `source_initiatives`).

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
