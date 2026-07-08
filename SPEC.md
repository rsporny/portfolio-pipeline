# SPEC.md — portfolio-pipeline v0.2: functional specification

## Goal

Once a week, turn real development work into content drafts (a titled devlog entry, one channel-neutral social post, highlights) with a human in the loop — and, new in v0.2, with **memory**: the pipeline tracks ongoing threads of work per organization/repository, so entries connect into larger arcs (how a feature evolved, what was assumed, what pivoted) instead of being isolated weekly snapshots.

Owner's weekly cost: ≤30 min of PR review.

## Content policy (applies to every generated artifact)

This project produces knowledge-sharing content: "what I built and what I learned." All generated content MUST:
- be written first person, concrete, engineering-minded, curious rather than promotional;
- contain no calls to action, no offers of services, no availability announcements, and no solicitation of any kind;
- present only the owner's own perspective; input from other people (review comments, discussions) may inform understanding but is never quoted or attributed — third-party names are redacted by default;
- claim only what is supported by the collected activity (no invented metrics, no embellished outcomes).

These rules are part of the Stage B prompt and must also be enforced structurally where possible (see redaction; automated checks arrive in v0.4).

Content language and format (decided in v0.1, retained): the devlog and social post are written in **English**; there is a single, channel-neutral **social** post (the website owns per-platform share buttons). The work is categorized and generalized for a broad engineering audience, and the devlog ends with a proof-of-work link.

## Data flow

```
collect ──► raw/YYYY-Wnn/activity.json          (committed to this repo — audit & reproducibility)
transform:
  stage A (technical summary, memory-aware)
  indexer (updates memory/ threads)             (committed to this repo by CI)
  stage B (writing, thread-aware)
publish (CI):
  site_adapter renders entry + site manifest
  PR opened against the landing-page repo       (merge = publish via Cloudflare)
  social post + highlights in the PR description
```

The local flow remains: `transform` writes `drafts/`, `review` lists them, and `publish --site-repo <path>` renders into a website checkout through the same adapter the CI uses.

## Module 1: Collector

Source: GitHub REST API (token from env `GH_ACTIVITY_TOKEN`; fine-grained PAT, read-only contents/PRs/issues on the allowlisted repos — public repositories are readable without elevated permissions).

For each repo on the allowlist in `config.yaml`, fetch within the given time window (default 7 days, override with `--since` / `--until`):
- commits authored by the configured user (`github_user: rsporny`): sha, date, message, `url`, changed files with stats (no diff contents — `include_diffs: false` reserved for a future version),
- PRs created by the user: title, description, labels, status, `url`,
- issues assigned to the user and closed in the window: title, description, `url`.

The `url` (GitHub `html_url`) is captured so drafts can cite proof of work.

Output: `raw/YYYY-Wnn/activity.json` (versioned schema, currently `schema_version: 2`), **committed to this repository**. Raw activity is the pipeline's source of truth: it makes every published entry reproducible and auditable. If there is no activity — write an empty file and exit with an informational message (no transform, no PR).

### Config (`config.yaml`)

```yaml
github_user: rsporny
repos:
  allowlist:
    - rsporny/portfolio-pipeline
    # public/open-source repos the owner contributes to,
    # or the owner's own private repos — nothing else
  descriptions:                        # optional per-repo domain context (categorization)
    rsporny/portfolio-pipeline: "Commit→content pipeline (Python)."
memory:
  root: memory/
redaction:
  forbidden_phrases: []
  redact_third_party_names: true
site:
  repo: rsporny/landing-page          # PR target (owner/name)
  devlog_dir: content/devlog
  adapter: sporny_pl                  # site-specific rendering lives ONLY in the adapter
anthropic:
  model: claude-opus-4-8
  max_tokens: 4000
locale:
  timezone: Europe/Warsaw
content:
  devlog_title_prefix: Senior SDET log   # devlog title becomes "<prefix> #N: …"
```

## Module 2: Memory

Purpose: connect weekly activity into longer arcs. Memory is plain files in this repository — transparent, reviewable, versioned.

Layout (nested org → repo):

```
memory/
  {org}/
    {repo}/
      context.md      # hand-written, updated rarely by the owner
      threads.yaml    # machine-maintained by the indexer
```

`context.md` — a short human-written card: what the project is, the stack, the owner's role, anything the model can't infer from commits. Never modified by the pipeline.

`threads.yaml` — schema:

```yaml
threads:
  - id: kebab-case-stable-id
    title: "Human-readable thread name"
    status: ongoing | pivoted | done
    started_week: 2026-W27
    last_active_week: 2026-W29
    summary: "2–4 sentences: what this thread is about and where it stands."
    assumptions:
      - text: "Skipping diff contents will be sufficient for good summaries."
        made_week: 2026-W27
        status: open | confirmed | falsified
        review_by: 2026-W31        # optional; code flags overdue reviews
    key_decisions:
      - week: 2026-W28
        decision: "One anchor per week (merkle root) instead of per-entry."
        rationale: "Cost and simplicity."
```

The `assumptions` block is a lightweight decision journal: explicit, dated, revisited. Falsified assumptions are content gold — Stage B is told about them.

**Model proposes, code disposes.** The indexer (below) proposes mutations as JSON; validated code applies them deterministically to `threads.yaml` and rejects anything malformed. The model never writes files. Review-due assumptions are computed by code from `review_by <= current week`, not taken from the model.

## Module 3: Transformer (three steps)

Step 0 — redaction: mask phrases from `redaction.forbidden_phrases` in all input data; when `redact_third_party_names` is true, replace GitHub logins/names other than `github_user` with role placeholders (e.g., `[reviewer]`). Log what was redacted. Runs before every model call.

### Stage A — technical summary (memory-aware)

Input: `activity.json` + the repo `descriptions` from config + `context.md` and current `threads.yaml` for each repo with activity. Group the week's work into 2–5 initiatives. Per initiative: `name`, `category` (a domain label a general engineer recognizes), `what` (3–5 technical sentences, English), `why_it_matters`, `tech`, `links` (commit/PR URLs = proof of work), and — if it plausibly continues or affects a known thread — a `thread_ref` (`{id, relation}` where relation ∈ continues | pivots | concludes | contradicts). Cosmetic commits are ignored unless they add up to something.

Expected JSON: `{"initiatives": [{"name", "category", "what", "why_it_matters", "tech": [], "links": [], "thread_ref": {"id", "relation"} | null}]}`.

Output: `drafts/YYYY-Wnn/summary-tech.md` (rendered) + `summary-tech.json` (raw). The durable record is `raw/` + memory; drafts are ephemeral.

### Indexer — memory update

Input: Stage A JSON + current `threads.yaml`. A second model call proposes memory mutations; **code applies them deterministically and validates the schema** (the model never writes files). The model proposes which threads to update (summary, status, assumption status changes, new assumptions) and which new threads to create (only for work that clearly starts something ongoing — one-off chores do not become threads). Be conservative: fewer, well-maintained threads beat many stale ones.

Expected JSON: `{"updates": [...], "new_threads": [...]}`. Code stamps `last_active_week`/`started_week` from the run's week, then computes `reviews_due` (open assumptions whose `review_by <= week`) to pass to Stage B.

CI commits the resulting `memory/` changes to this repository's main branch as a clearly labeled bot commit (derived, regenerable state — acceptable without PR). Locally, `pipeline transform` applies them to the working tree. An indexer failure must not block Stage B — fall back to the previous memory and log a warning.

### Stage B — writing (thread-aware)

Input: Stage A JSON + updated thread data for referenced threads + `reviews_due`. The prompt carries the content policy (knowledge sharing, no CTAs/offers/solicitation, never name third parties, claim only what the activity supports) and the thread context (some initiatives continue longer arcs, some contradict earlier assumptions, some assumptions are due for review — weave this in: refer back to when a thread started, what was assumed, what changed; continuity over novelty).

Produces:
1. `title` — an auto-numbered devlog title `"<content.devlog_title_prefix> #N: <subtitle>"`, where N = (devlogs already in the site) + 1.
2. `devlog` (English, 350–550 words) — opens with generalized context, explains the work without assuming repo knowledge (a short example/analogy where it helps), follows problem → decision → outcome with thread continuity where it exists, and ends with a proof-of-work link.
3. `social` (100–180 words, English) — one channel-neutral post (hook first line, one concrete lesson, ≤3 hashtags, no CTA) that draws the reader to the full devlog.
4. `highlights` — notable items worth revisiting, one sentence each, tagged with the initiative/thread.

Respond ONLY with JSON: `{"title", "devlog", "social", "highlights": []}`.

Output: `devlog.md`, `social.md`, `highlights.md` in `drafts/YYYY-Wnn/`, each with front matter (`title`, `status: draft`, `week`, `generated_at`, `source_initiatives`).

Error handling (all model calls): retry with backoff (3 attempts); JSON validation (strip ```json fences); on failure, save the raw response (workflow artifact / `_failed_raw.txt` locally) and exit with a clear error.

## Module 4: Site adapter and publishing

All knowledge about the landing page lives in ONE module: `src/pipeline/site_adapter/sporny_pl.py`, implementing a small interface:

```
render(entry, metadata) -> list[FileChange]   # devlog markdown + regenerated site manifest
```

The pipeline core knows only the interface. Supporting another site = writing another adapter. No site-specific logic anywhere else. `publish` (local and CI, via `--site-repo`) resolves the adapter named in `site.adapter`, calls `render`, and writes the resulting `FileChange`s into the website checkout — it never commits or pushes the website repo.

### Manifest schema (`content/devlog/index.json`, owned by the website)

`write_manifest` rebuilds the manifest from **every** front-mattered `.md` in the devlog dir, ordered by `date` (newest first) so weekly and custom entries interleave chronologically. Each entry has:

| field    | source                                        | notes |
|----------|-----------------------------------------------|-------|
| `type`   | front matter `type`, default `weekly-activity`| `weekly-activity` (pipeline-generated) or `custom` (hand-authored) |
| `series` | see below                                     | role identity, e.g. `Senior SDET log` — emitted **per entry** |
| `n`      | see below                                     | per-series sequence number, **frozen once assigned** |
| `slug`   | the `.md` filename without extension          | the entry id and page `#hash` anchor (renamed from the old `week` key) |
| `title`  | front matter `title`                          | customs use it verbatim as the heading; weeklies keep their `#N` title |
| `date`   | `published_at` (fallback `generated_at`)      | drives ordering and the "Published" line |
| `kind`   | front matter `kind` (custom only, optional)   | kicker label; omitted ⇒ the page defaults to `Note` |

**`series` per entry.** Weeklies emit the caller's configured current series (`content.devlog_title_prefix`); customs carry their own `series` in front matter. An entry's own recorded series — front matter, or a value already in the prior manifest — always wins, so history is **never rewritten** when the owner's role/series changes.

**`n` is one per-series sequence spanning weekly *and* custom entries**, frozen once assigned. Resolution order: reuse the value already in the prior manifest → else front matter `n` → else backfill from a legacy `#N` weekly title → else assign `max(n in that series) + 1` (walking entries oldest-first so assignment is deterministic). Because assigned values are read back from the prior manifest on the next run, numbers are idempotent across re-runs and never renumber when a backdated entry appears. A new series restarts its own sequence at 1.

**Custom entries** are authored by hand directly in the website repo (see "Authoring a custom entry" below) and are **not** part of this pipeline's `raw/` → `transform` → `published/` flow. `write_manifest` never writes or deletes a custom `.md`; it maps front matter → manifest per the table and **skips any custom lacking `status: published`**.

### Authoring a custom entry (hand-written notes/essays)

Custom entries live only in the **website repo**, never in this pipeline. To publish one:

1. Add `content/devlog/<slug>.md` in the website repo (the filename is the `slug` and the page anchor — choose it deliberately, e.g. `looking-ahead-2036.md`).
2. Give it front matter:
   ```yaml
   ---
   type: custom
   series: Senior SDET log      # same string as the weekly series it shares a sequence with
   n: 2                         # per-series number; pick the next free one (shared with weeklies)
   slug: looking-ahead-2036     # = filename without .md
   title: "Looking ahead: SDET role in the age of AI"   # used verbatim as the heading
   published_at: 2026-07-07     # drives ordering + the "Published" date
   status: published            # omit/anything else ⇒ excluded from the manifest (safe drafting)
   kind: Essay                  # optional kicker; omit ⇒ the page shows "Note"
   ---
   ```
3. Open a PR against the website repo. On the next weekly run (or any `publish`), `write_manifest` regenerates `index.json` and folds the entry in; drafts (no `status: published`) stay invisible until you flip the flag. Once `n` lands in the manifest it is frozen — don't renumber existing entries.

## Module 5: Automation (CI)

GitHub Actions `.github/workflows/weekly.yml`:
- cron: Sunday 16:00 UTC (18:00 CEST); `workflow_dispatch` with a `since` input for manual runs,
- collects the week, and only if there was activity runs the transform (Stage A → indexer → Stage B),
- commits `raw/YYYY-Wnn/activity.json` and `memory/` updates to this repo (clearly labeled bot commits),
- opens a pull request **against the landing-page (sporny.pl) repo** containing the rendered devlog entry (`content/devlog/YYYY-Wnn.md`) and the regenerated manifest, on branch `devlog/YYYY-Wnn` (re-runs for the same week update the same branch — one PR per week),
- the social post and highlights go in the PR description (they are not site content),
- intermediate drafts and raw model responses are uploaded as workflow artifacts for short-term debugging (artifacts expire ≤90 days — which is why `raw/` and `memory/` are committed),
- **merge = publish**: Cloudflare deploys the site on merge to `main`. The Action only *opens* the PR — it never merges; a human always reviews, edits the PR if needed, and approves before anything goes live,
- secrets: `ANTHROPIC_API_KEY`; `GH_ACTIVITY_TOKEN` (fine-grained PAT, read-only contents/PRs/issues on the allowlisted repos); `LANDING_PAGE_TOKEN` (fine-grained PAT, write on the website repo — enough to push a branch and open a PR, not merge).

The local `review` / `publish` commands remain for manual/offline operation; `publish --site-repo <path>` targets a checked-out copy of the website through the same code path.

## Tests (pytest)

- collector: GitHub API response parsing (fixtures), author/time-window filtering, allowlist enforcement (repo not listed → skipped + warning).
- redaction: forbidden phrases + third-party name redaction, case-insensitive.
- memory: threads.yaml schema validation, deterministic application of indexer mutations, rejection of invalid mutations, assumption review-due computation.
- transformer: model-response parsing (valid JSON / fenced / broken → failure path), retry logic (mocked), indexer-failure fallback.
- site adapter: entry + manifest rendering golden tests.
- publish: file operations in tmp_path; CI path and local path share the code under test.
- Zero real network calls in tests.

## README.md

Public, building-in-public repo. Must include: data-flow diagram, the human-in-the-loop philosophy (merge = publish), the memory design (threads, assumptions as a decision journal), why allowlist-only, the adapter architecture ("fork it, write an adapter for your own site"), quickstart, sample entry. Language: English.

## Roadmap (do not implement ahead of schedule)

- **v0.3** — selective deep context: review comments and linked/parent/child issues fetched only for PRs belonging to active threads; third-party input used for understanding only, never quoted (policy above already applies).
- **v0.4** — eval suite for the transformer: golden examples, property assertions (valid JSON, content-policy compliance, word limits, faithfulness to activity), run in CI on every prompt/model change, results published.
- **v0.5** — provenance: signed entries + weekly hash anchoring (merkle root) on Cardano.
- **v1.0** — experiment: ZK-based verifiable claims about private activity (Midnight).

## Implementation order for v0.2 (milestones)

1. Memory module: schema, loader/validator, deterministic mutation application + tests.
2. Collector change: commit `raw/` (schema unchanged) + tests.
3. Transformer: memory-aware Stage A, indexer, thread-aware Stage B + tests.
4. Site adapter extraction (`sporny_pl`) + golden tests.
5. CI workflow: cross-repo PR flow, bot commits, artifacts + README update.

After each milestone: a working state, green tests, a commit.
