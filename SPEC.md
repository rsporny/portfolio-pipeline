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

These rules are part of the Stage B prompt and must also be enforced structurally where possible — see redaction, and (since v0.4) the structural check suite that scores every generated draft and **blocks** the run on a hard violation (see Tests / eval suite below).

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

**Selective deep context (v0.3).** For a PR in a repo that has an **active thread** in memory (any thread with `status: ongoing`), the collector also fetches the PR's *review discussion* (review summaries with a body, inline review comments, conversation comments) and its *linked issues* (numbers from body closing-keywords → `relation: closes`; timeline cross-references → `relation: references`). Gating is repo-level and computed at collect time: a brand-new PR's thread membership only exists after Stage A, so instead of a second Stage A pass, deep context is fetched for the owner-PRs of any repo with an ongoing arc — and skipped entirely for repos without one (and always, before any thread exists). Comments carry a structural `author_role` (`owner`|`other`) but **no names**; third-party input informs understanding only and is never quoted (content policy). Deep data is anonymized *before* it reaches `raw/` (see redaction below).

Output: `raw/YYYY-Wnn/activity.json` (versioned schema, currently `schema_version: 3` — v3 added the anonymized deep context on PRs; a `schema_version: 2` file still parses, the new fields default empty), **committed to this repository**. Raw activity is the pipeline's source of truth: it makes every published entry reproducible and auditable. If there is no activity — write an empty file and exit with an informational message (no transform, no PR).

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
state:
  root: "."                            # since v0.4.1: all instance state (raw/, memory/,
                                       # provenance/, drafts/approved/published) resolves here.
                                       # An instance is one tree — the site repo — run from it.
redaction:
  forbidden_phrases: []
  redact_third_party_names: true       # mask non-owner logins/names before raw/ + every model call
  role_placeholder: "[collaborator]"   # what a redacted third-party name becomes
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
  devlog_title_prefix: Senior SDET log   # the series identity; site renders "<series> #N: …"
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
        decision: "Per-entry hash anchored independently, no merkle tree."
        rationale: "Reader can verify one file with sha256sum; the tree bought nothing at this scale."
```

The `assumptions` block is a lightweight decision journal: explicit, dated, revisited. Falsified assumptions are content gold — Stage B is told about them.

**Model proposes, code disposes.** The indexer (below) proposes mutations as JSON; validated code applies them deterministically to `threads.yaml` and rejects anything malformed. The model never writes files. Review-due assumptions are computed by code from `review_by <= current week`, not taken from the model.

## Module 3: Transformer (three steps)

Step 0 — redaction: mask phrases from `redaction.forbidden_phrases` in all input data. Two layers cooperate: (1) **name anonymization** — when `redact_third_party_names` is true, every GitHub login / display name / `@mention` other than `github_user` is replaced with `redaction.role_placeholder` (default `[collaborator]`). The participant set is gathered deterministically from the collected data (comment/review authors, `@mentions`, `Co-authored-by` trailers), so this happens **in the collector, before `raw/` is written** — the public snapshot never carries a third party's name. (2) **phrase redaction** runs in the transformer before every model call over the already-anonymized activity. Log what was redacted (counts only — never the names themselves).

### Stage A — technical summary (memory-aware)

Input: `activity.json` (including any anonymized deep context on PRs — review discussion and linked issues, which the model may use to understand intent but must never quote or attribute) + the repo `descriptions` from config + `context.md` and current `threads.yaml` for each repo with activity. Group the week's work into 2–5 initiatives. Per initiative: `name`, `category` (a domain label a general engineer recognizes), `what` (3–5 technical sentences, English), `why_it_matters`, `tech`, `links` (commit/PR URLs = proof of work), and — if it plausibly continues or affects a known thread — a `thread_ref` (`{id, relation}` where relation ∈ continues | pivots | concludes | contradicts). Cosmetic commits are ignored unless they add up to something.

Expected JSON: `{"initiatives": [{"name", "category", "what", "why_it_matters", "tech": [], "links": [], "thread_ref": {"id", "relation"} | null}]}`.

Output: `drafts/YYYY-Wnn/summary-tech.md` (rendered) + `summary-tech.json` (raw). The durable record is `raw/` + memory; drafts are ephemeral.

### Indexer — memory update

Input: Stage A JSON + current `threads.yaml`. A second model call proposes memory mutations; **code applies them deterministically and validates the schema** (the model never writes files). The model proposes which threads to update (summary, status, assumption status changes, new assumptions) and which new threads to create (only for work that clearly starts something ongoing — one-off chores do not become threads). Be conservative: fewer, well-maintained threads beat many stale ones.

Expected JSON: `{"updates": [...], "new_threads": [...]}`. Code stamps `last_active_week`/`started_week` from the run's week, then computes `reviews_due` (open assumptions whose `review_by <= week`) to pass to Stage B.

CI commits the resulting `memory/` changes to this repository's main branch as a clearly labeled bot commit (derived, regenerable state — acceptable without PR). Locally, `pipeline transform` applies them to the working tree. An indexer failure must not block Stage B — fall back to the previous memory and log a warning.

### Stage B — writing (thread-aware)

Input: Stage A JSON + updated thread data for referenced threads + `reviews_due` + an optional focus directive. The prompt carries the content policy (knowledge sharing, no CTAs/offers/solicitation, never name third parties, claim only what the activity supports) and the thread context (some initiatives continue longer arcs, some contradict earlier assumptions, some assumptions are due for review — weave this in). Continuity framing is **week-relative**: a thread that began in an earlier week is referred back to ("started N weeks ago, then assumed X"); a thread that began *this* week is introduced in the present, never as past history. Continuity over novelty.

**Focus (optional).** After the indexer, code offers the threads *active this week* (`last_active_week == week`) for selection; the chosen thread(s) become a focus directive telling Stage B to lead the title, opening, and social post with them while still covering the rest of the week briefly. Selection is the caller's: `pipeline transform` prompts interactively on a TTY, takes `--focus <thread-id>` (exact id, validated) non-interactively, and auto-picks (model chooses the lead) when neither applies — so CI never blocks. An unknown/inactive `--focus` id is an error.

Produces:
1. `title` — a bare, specific subtitle (the topic only, no series name and no number). The per-series number `N` is assigned by the site manifest (`write_manifest`, see Module 4), not at generation time, and the site renders the heading as `"<series> #N: <subtitle>"`. This keeps a single source of numbering shared across weekly and custom entries.
2. `devlog` (English, 350–550 words) — opens with generalized context, explains the work without assuming repo knowledge (a short example/analogy where it helps), follows problem → decision → outcome with thread continuity where it exists, and ends with a proof-of-work link.
3. `social` (100–180 words, English) — one channel-neutral post (hook first line, one concrete lesson, ≤3 hashtags, no CTA) that draws the reader to the full devlog.
4. `highlights` — notable items worth revisiting, one sentence each, tagged with the initiative/thread.

Respond ONLY with JSON: `{"title", "devlog", "social", "highlights": []}`.

Output: `devlog.md`, `social.md`, `highlights.md` in `drafts/YYYY-Wnn/`, each with front matter (`title`, `status: draft`, `week`, `generated_at`, `source_initiatives`, and `topics`). `topics` is a per-section list (`title` = the `##` heading, `category` = the initiative's category, `repo` = derived from its first GitHub link) that the site renders as category dividers; category/repo are omitted when unknown.

Error handling (all model calls): retry with backoff (3 attempts); JSON validation (strip ```json fences); on failure, save the raw response (workflow artifact / `_failed_raw.txt` locally) and exit with a clear error.

## Module 4: Site adapter and publishing

### The interface (all the pipeline core knows)

The website owns how it presents devlog entries — its file layout, front-matter shape, manifest schema, and numbering are **its** decisions, not the pipeline's. The pipeline meets the site through ONE module (an *adapter*) and a small interface:

```
render(entry, ctx) -> list[FileChange]
```

- **`DevlogEntry`** — a site-neutral entry: `slug`, `title`, `body` (markdown, H1 included), `date` (YYYY-MM-DD), `type` (`weekly-activity` or `custom` — a real pipeline distinction), and `meta` (a dict for anything site- or pipeline-specific: weeklies pass `source_initiatives` and `topics`; customs pass `kind` and an optional per-entry `series`). The core never hands the adapter a ready-made front-matter dict, so no producer-side (transform) decision can leak onto the published site.
- **`RenderContext` (`ctx`)** — `site_dir` (the website's devlog dir) plus the pipeline `config`; the adapter reads whatever it needs from config, so no site vocabulary (e.g. "series") lives in the neutral interface.
- **`FileChange`** — a `(path, content)` pair to write into the website checkout.

The adapter composes **the entire site output** — the entry's markdown file *and* the regenerated manifest — and returns them as `FileChange`s. `publish` (local and CI, via `--site-repo`) resolves the adapter named in **`output.adapter`** (registered in `site_adapter/__init__.py`), calls `render`, and writes the returned changes into the checkout — it never commits or pushes the website repo. **Supporting another site = writing another adapter with a different `render`; no site-specific logic lives anywhere outside `site_adapter/`, and a fork may define any front-matter/manifest schema it likes.** The rest of this section documents the schema the *bundled* adapter happens to produce; it is an example, not a contract the pipeline imposes.

### Reference adapter: `sporny_pl` (example implementation)

This is what the shipped `sporny_pl` adapter produces for sporny.pl. A fork replaces all of it.

**Manifest (`content/devlog/index.json`).** The adapter rebuilds the manifest from **every** front-mattered `.md` in the devlog dir (plus the entry currently being published), ordered by `date` (newest first) so weekly and custom entries interleave chronologically. Each entry has:

| field    | source                                        | notes |
|----------|-----------------------------------------------|-------|
| `type`   | front matter `type`, default `weekly-activity`| `weekly-activity` (pipeline-generated) or `custom` (hand-authored) |
| `series` | see below                                     | role identity, e.g. `Senior SDET log` — emitted **per entry** |
| `n`      | see below                                     | per-series sequence number, **frozen once assigned** |
| `slug`   | the `.md` filename without extension          | the entry id and page `#hash` anchor |
| `title`  | front matter `title`                          | customs use it verbatim; weeklies hold a bare subtitle (the site prepends `<series> #N:`); the one legacy entry still carries its `#N` title, from which its `n` is backfilled |
| `date`   | `published_at` (fallback `generated_at`)      | drives ordering and the "Published" line |
| `kind`   | front matter `kind` (custom only, optional)   | kicker label; omitted ⇒ the page defaults to `Note` |

**Site file front matter.** The adapter *composes* the site `<slug>.md` front matter for both kinds from the neutral entry — `type`, `series`, `slug`, `title`, `published_at`, `status: published`, optional `kind`, and (for weeklies) `source_initiatives` and `topics` surfaced from `meta` as an explicit transparency choice. Draft-only keys (`generated_at`, `week`, …) stay in the pipeline's own `published/` record and never reach the site.

**`series` per entry.** Weeklies emit the caller's configured current series (`content.devlog_title_prefix`); customs carry their own `series` (via `meta`) when the author set one. An entry's own recorded series — its front matter, or a value already in the prior manifest — always wins, so history is **never rewritten** when the owner's role/series changes.

**`n` is one per-series sequence spanning weekly *and* custom entries**, frozen once assigned. Resolution order: reuse the value already in the prior manifest → else front matter `n` → else backfill from a legacy `#N` weekly title → else assign `max(n in that series) + 1` (walking entries oldest-first so assignment is deterministic). Because assigned values are read back from the prior manifest on the next run, numbers are idempotent across re-runs and never renumber when a backdated entry appears. A new series restarts its own sequence at 1.

**Custom entries** are authored by hand directly in the website repo (see below) and are **not** part of this pipeline's `raw/` → `transform` → `published/` flow. The adapter never writes or deletes a custom `.md` on a plain manifest rebuild; it maps front matter → manifest per the table and **skips any custom lacking `status: published`**.

#### Authoring a custom entry (hand-written notes/essays)

Custom entries are hand-written but the pipeline does the mechanical work (front matter + numbering + manifest). The number is **never** hand-set — the adapter assigns it. To publish one:

1. Write a plain Markdown file anywhere, whose **first `# H1` is the entry title**, followed by the body:
   ```markdown
   # Looking ahead: SDET role in the age of AI

   <your essay…>
   ```
2. Run `pipeline publish-custom <file.md> --site-repo <website checkout>` (options: `--slug` — defaults to the filename; `--kind` — kicker label, omit ⇒ the site shows "Note"; `--date` — defaults to today). The adapter writes a complete `content/devlog/<slug>.md` into the website repo — `type: custom`, `series`, `slug`, `title` (the H1), `published_at`, `status: published`, optional `kind`, and **no hand-set `n`** — then regenerates `index.json`, which assigns and freezes the per-series number. It prints the resulting `"<series> #N"`. The command is **file-only**: it never commits or pushes the website repo.
3. Verify locally in the website repo (e.g. run the site's dev server), then commit + merge — Cloudflare deploys on merge, exactly like the weekly PR flow. Re-running `publish-custom` for the same slug updates the file in place and keeps the frozen number (it lives in the manifest, not the file).

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

## Module 6: Provenance (v0.5)

Provenance lets anyone independently confirm two things about a published entry: **who** wrote it (a signature) and **when** it existed (an on-chain timestamp). It is a **local, human, post-merge** flow — never CI — and lives entirely under `state.root/provenance/`.

Each entry is an **independent** proof — one file, one hash, one transaction. There is no merkle tree, cumulative root, or inclusion proof: a reader checks the single entry in front of them, with universal tools (`sha256sum`, `gpg --verify`, or an in-browser hash) and no clone.

**What is attested.** The *merged, published* entry — the prose that actually ships — read back from the site repo, not the pipeline draft (which may have been edited in the PR). The commitment is the **plain `sha256` of the raw `<slug>.md`**, exactly as the site serves it (front matter included). Because that byte string *is* the hash, provenance is never written back into the `.md`: the signature, hash, and anchor live in the manifest and the `.sig` sidecar. Byte-stability is pinned on the site with `.gitattributes` (`content/devlog/*.md text eol=lf`) so git never rewrites the hashed bytes.

**Signed entries.** Signing is a deliberate GPG act (the owner's YubiKey) performed on the devlog PR branch, just before merge — so the signature ships in the same PR as the content and **no signing key ever enters CI**. `pipeline provenance sign` signs the raw file, writes the authoritative detached signature to `provenance/entries/<slug>.md.sig`, records the entry in the ledger, and asks the configured site adapter to render provenance onto the site: a co-located `<slug>.md.sig` sidecar, the public key, and a manifest carrying the verify-badge fields. Because the signature is over the file itself, stock `gpg --verify <slug>.md.sig <slug>.md` checks it. Signing works for weekly and custom entries alike.

**Transparency ledger.** Every signed entry is one record in an append-only ledger (`provenance/log.jsonl`, idempotent by slug: re-signing an edited entry updates its record in place and drops a now-stale anchor). Each record carries the entry's `sha256`, its signature path, and — once anchored — its per-entry anchor receipt.

**Anchoring (pluggable, testnet).** `pipeline provenance anchor` timestamps one entry's `sha256` through a backend selected by `provenance.anchor.backend`: `null` (default — anchors nothing, stays offline), `file` (a local receipt, for dev), or `cardano` (a metadata transaction on a Cardano **testnet** — `preview`/`preprod` — via the optional `pycardano` extra + `BLOCKFROST_PROJECT_ID`/`CARDANO_SIGNING_KEY`, env-only). The metadata is `{slug, sha256, v}`; one transaction per entry (targeted by `--week`/`--slug`, or all not-yet-anchored). Each anchor writes a receipt to `provenance/anchors/<txid>.json`, is recorded on the entry's ledger record, and refreshes the manifest anchor badge. Mainnet is out of scope for v0.5.

**Verification.** `pipeline provenance verify` recomputes each entry's `sha256` from the actual published file (catching any post-hoc edit) and verifies its signature against the committed public key (`provenance/pubkey.asc`) in an ephemeral keyring; `--chain` additionally reads each anchor back and compares the hash. It exits non-zero on any failure. `provenance.yml` runs this **offline, with no secrets** as an integrity gate. The engine never anchors mainnet, never merges, and never publishes — provenance only adds proof to what a human already reviewed and merged.

**Interfaces.** The provenance core is site-neutral: signing produces a neutral `EntryProof` (`{slug, sha256, signature, sig_filename, pubkey_fingerprint}`), and the adapter's `attach_provenance(slug, proof, ctx)` / `attach_anchor(slug, anchor, ctx) -> list[FileChange]` decide how (or whether) to surface it — all site-shape decisions stay inside `site_adapter/`, exactly like `render`. The anchor backend is resolved by name (`get_anchor_backend`), mirroring `get_adapter`; supporting another chain is a new backend.

## Tests (pytest)

- collector: GitHub API response parsing (fixtures), author/time-window filtering, allowlist enforcement (repo not listed → skipped + warning).
- redaction: forbidden phrases + third-party name redaction, case-insensitive.
- memory: threads.yaml schema validation, deterministic application of indexer mutations, rejection of invalid mutations, assumption review-due computation.
- transformer: model-response parsing (valid JSON / fenced / broken → failure path), retry logic (mocked), indexer-failure fallback.
- site adapter: entry + manifest rendering golden tests.
- publish: file operations in tmp_path; CI path and local path share the code under test.
- checks (v0.4): each structural check's pass and fail path over handcrafted output.
- eval runner (v0.4): case discovery, scratch isolation, scorecard rendering, and the
  error/warn tally — driven by a fake LLM (the real-model path runs only in `evals.yml`).
- Zero real network calls in tests.

### Eval suite (v0.4)

The transformer's output is verified two ways, both built on one pure check library
(`src/pipeline/checks.py`): structural **property assertions** — valid schema (Pydantic),
word limits, hashtag budget, no solicitation/CTA, no leaked collaborator (`@mention` /
placeholder), no forbidden phrase, and **faithfulness** (every cited URL exists in the
week's activity; initiatives invent no links). Each check is `error` (hard content
policy) or `warn` (soft quality).

- **In production**, `transform_week` runs the checks after Stage B, writes a
  `checks.md`/`checks.json` report into the draft bundle, logs warnings, and **halts the
  run on any `error`-severity failure** — a solicitation, a leaked name, or an invented
  link never reaches a PR (the drafts stay on disk for inspection).
- **Golden runner**: `pipeline eval` runs the real transformer over curated
  `evals/cases/**` (baseline, thread continuity, focus, anonymized deep context), scores
  each with the same checks, and writes a committed Markdown scorecard (`evals/RESULTS.md`).
- **CI**: `.github/workflows/evals.yml` runs the golden runner on `workflow_dispatch` and
  on `push` to `main` touching the prompt/model surface. It is the only workflow holding
  `ANTHROPIC_API_KEY`, kept behind a protected `evals` environment and never triggered by a
  fork PR, so an untrusted contributor can never spend the key. It fails the job on any
  `error`-severity failure — the pre-merge gate for a prompt or model change.

## README.md

Public, building-in-public repo. Must include: data-flow diagram, the human-in-the-loop philosophy (merge = publish), the memory design (threads, assumptions as a decision journal), why allowlist-only, the adapter architecture ("fork it, write an adapter for your own site"), quickstart, sample entry. Language: English.

## Roadmap (do not implement ahead of schedule)

- **v0.3** ✅ *shipped* — selective deep context: review comments and linked issues fetched only for PRs in repos with an active thread; third-party input used for understanding only, never quoted (policy above already applies). Sub-issue parent/child graph deferred.
- **v0.4** ✅ *shipped* — eval suite for the transformer: golden examples, property assertions (valid JSON, content-policy compliance, word limits, faithfulness to activity), run in CI on every prompt/model change, results published.
- **v0.5** ✅ *shipped* — provenance: each entry committed to by the sha256 of its raw `.md`, GPG-signed, and anchored per-entry on Cardano testnet (no merkle tree). Verifiable with `sha256sum` / `gpg --verify` / an in-browser hash. See Module 6.
- **provenance, next** — (a) move anchoring from testnet to **mainnet** once the mechanism is proven; (b) **reader-side in-browser chain verification**: the entry badge is link-only today (it recomputes the file's hash and links to the transaction); add a same-origin **Cloudflare Worker → Koios** (keyless) proxy so the badge confirms the hash against Cardano live, without exposing an API key or hitting browser CORS limits.
- **reader-altitude guard** — keep each generated section pitched to a reader who doesn't know the repo. A new *advisory* check in `src/pipeline/checks.py` flags sections that reproduce PR-description-level mechanics or lean on undefined domain jargon, and Stage B guidance steers toward "what changed and why it matters" over blow-by-blow internals. Adds an over-detailed golden case to `evals/cases/`. Advisory (scored, reported) rather than a hard block — altitude is a judgement call, unlike the existing content-policy gates.
- **published-entry continuity** ✅ *shipped (v0.6)* — before Stage B, retrieve a handful of *past published entries* related to the current draft and feed them in, so arcs connect across weeks instead of resetting. Cheap Python retrieval (scan `content/devlog/*.md` front-matter — `series`, `source_initiatives` — and score keyword/token overlap against the current threads) picks the top few; only those bodies are loaded, keeping input tokens bounded. Distinct from memory, which is derived thread state — this reads the actual published prose.
- **v1.0** — experiment: ZK-based verifiable claims about private activity (Midnight).

## Status

Per-version "what shipped" record (the numbered build plans that produced each
version are complete and no longer carried here).

**v0.2.0** — memory era. The pipeline tracks work threads per org/repo; the weekly
workflow auto-picks the lead thread (no TTY → no prompt), a `workflow_dispatch`
`focus` input can override it, and the Action commits each week's `raw/` snapshot
and `memory/` updates back to `main` as labeled bot commits — derived, regenerable
state that outlives the ≤90-day artifacts. The Action only *opens* a PR, never merges.

**v0.3.0** — selective deep context. The collector enriches the owner-PRs of any
repo with an ongoing thread with review discussion and linked issues, and the
long-specified third-party name redaction is implemented — anonymization happens in
the collector so `raw/` (public) never carries a collaborator's name, while phrase
redaction still runs before every model call. `schema_version` is now 3 (v2 files
still parse). Deep context reaches the model structurally, guarded by "understanding
only, never quote" instructions across Stage A / indexer / Stage B.

**v0.4.0** — transformer eval suite. A pure structural check library
(`src/pipeline/checks.py`) scores every generated draft on content policy and
faithfulness; `transform_week` runs it after Stage B and **blocks** the run on any
hard (`error`-severity) violation. `pipeline eval` runs the real transformer over
`evals/cases/**` and writes a committed scorecard (`evals/RESULTS.md`); `evals.yml`
drives it in CI on prompt/model changes, behind a protected environment and never
reachable by a fork PR (the only workflow holding `ANTHROPIC_API_KEY`).

**v0.4.1** — engine / state separation. The tool is now a **stateless, forkable
engine**: it ships no committed `raw/`/`memory/`, and every state area resolves
under one configurable `state.root` via `Config.state_dir(name)`. An *instance* is
a single tree — the site repo — holding its own `config.yaml`, state (`raw/`,
`memory/`, later `provenance/`), and `content/`; the engine is run from there and
writes nothing outside `state.root` (a test asserts this). The weekly workflow
installs the engine and runs it against the site checkout, opening **one** PR that
bundles the rendered devlog with its `raw/` + `memory/` state — the engine commits
nothing back to its own repo.

**v0.5.0** — provenance. A new `src/pipeline/provenance/` package: `content` (an
entry's commitment is the plain `sha256` of its raw `<slug>.md`, reproducible with
`sha256sum`), `log` (an append-only `provenance/log.jsonl` under `state.root`,
idempotent by slug, each record carrying the entry's hash, signature, and optional
per-entry anchor), `sign` (injectable detached GPG signing over the file bytes, so
`gpg --verify` works) and `verify` (recompute each file hash + signature, optional
`--chain`). Every entry is an independent proof — no merkle tree or cumulative
root. Anchoring is a pluggable backend (`null` default, `file`, and a lazy
`cardano` testnet backend via `pycardano`+Blockfrost, an optional extra) writing
`{slug, sha256, v}` per entry. Signing is a deliberate local GPG act on the PR
branch before merge — **no key ever enters CI**; the `sporny_pl` adapter's
`attach_provenance`/`attach_anchor` write the `<slug>.md.sig` sidecar + public key
and carry the verify-badge fields in the manifest, never touching the `.md`. New
`pipeline provenance sign|anchor|verify|show`; `provenance.yml` runs an offline,
secret-free `verify` as an integrity gate.

**v0.6.0** — published-entry continuity. A new `src/pipeline/continuity.py` does
cheap Python retrieval (no LLM, no embeddings) over the site's already-published
`content/devlog/*.md`: it scores each entry's front-matter tokens (`series`,
`title`, `topics`, and `source_initiatives` weighted ×2) against the current
draft's vocabulary — the active/referenced work threads plus this week's
initiatives — and loads the top few *bodies* (excerpt-capped, `EXCERPT_CHARS`).
`transform_week` feeds them to Stage B as a "Past published entries" block so the
writer builds on its own earlier prose instead of resetting each week — distinct
from memory (derived thread *state*); this is the actual published *prose*. The
block is redacted before the call like every model input, and the step is
failure-tolerant: a missing/empty site dir or any error yields no context and
never blocks the run. Bounded by `content.continuity_max_entries` (default 3, 0
disables). From this release, versions are git-tagged as signed annotated tags
`vMAJOR.MINOR.PATCH`. Next: v1.0 (see roadmap).
