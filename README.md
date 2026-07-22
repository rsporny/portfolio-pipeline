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
     │        + selective deep context: review discussion & linked issues for PRs
     │          in repos with an active thread (anonymized before it hits raw/)
     ▼
  redact  ──► mask forbidden phrases before any API call + third-party names
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

## Memory: threads & assumptions

Weekly snapshots are forgettable; arcs are not. The pipeline keeps a **memory** —
plain, versioned files under `memory/{org}/{repo}/` — so entries connect into
longer stories instead of isolated weekly dumps. It tracks two things:

- **Threads:** ongoing lines of work (a feature, a refactor, an experiment). Each
  week an *indexer* pass reads the fresh activity and proposes which threads it
  continues or starts, so a later entry can say "the thing I started three weeks
  ago" rather than reintroducing it cold.
- **Assumptions:** a lightweight, dated decision journal per thread — explicit
  bets, revisited later. A falsified assumption is content gold: the writing stage
  is told about it, because "I assumed X, then it broke" is a better story than a
  clean narrative.

Crucially, **the model proposes and code disposes**: the indexer only emits
*proposed* mutations, which validated code applies to `memory/` deterministically.
The model never writes memory files directly. The registry is committed to the
instance (the site repo, under `state.root`), so every arc is transparent and
reviewable in git history.

**Deep context, selectively.** An active thread earns richer signal: for a repo
with an ongoing thread, the collector also pulls each of my PRs' *review
discussion* and *linked issues*, so the writing can draw on the "why" — the intent
a reviewer surfaced, the problem an issue described — not just the commit message.
It is fetched only where an arc exists (cost and noise control), used for
understanding only, and **never quoted**. Because `raw/` is a public repo, every
third-party name (logins, `@mentions`, `Co-authored-by`) is masked to
`[collaborator]` *before* the snapshot is written — the model, and the git
history, only ever see anonymized text.

## Allowlist only (by design)

The collector scans **only** the repositories explicitly listed in
`config.yaml` under `repos.allowlist`. There is no "scan everything" mode, and
the default is deny. Only public/open-source repositories, or my own private
repositories, may be listed.

This is deliberate: a tool that reads your development activity should read
exactly what you tell it to and nothing more. It keeps the blast radius small,
makes runs reproducible, and means the GitHub token can be a fine-grained PAT
with read-only scope on a handful of named repos.

## Evals & content checks

The content policy (no solicitation, never name a collaborator, claim only what
the activity supports) lives in the prompts — but prompts are guidance, not a
guarantee. Since v0.4 a small **pure check library** (`src/pipeline/checks.py`)
verifies the output *structurally*: word limits, hashtag budget, no
solicitation/CTA, no leaked `@mention` or anonymization placeholder, no forbidden
phrase, and **faithfulness** — every URL the devlog cites must exist in that
week's collected activity, and initiatives may invent no links. Each check is
either `error` (hard policy) or `warn` (soft quality).

Two places use it, one implementation:

- **Every real run.** After Stage B, `transform_week` scores the draft, writes a
  `checks.md`/`checks.json` report into the bundle, and **halts on any
  `error`-severity failure** — a solicitation, a leaked name, or an invented
  proof-of-work link never reaches a PR. The offending drafts stay on disk so I
  can see what the model did.
- **Golden runner.** `pipeline eval` runs the real transformer over curated cases
  in `evals/cases/` (a plain weekly entry, a thread that continues, a two-thread
  focus, and a PR carrying anonymized review discussion), scores each with the
  same checks, and writes a committed scorecard at
  [`evals/RESULTS.md`](evals/RESULTS.md).

In CI, `.github/workflows/evals.yml` runs the golden runner on demand
(`workflow_dispatch`) and whenever a prompt, the checks, the model config, or a
golden case changes on `main` — the pre-merge gate for a prompt or model change.
It is the only workflow that holds `ANTHROPIC_API_KEY`, kept behind a protected
`evals` environment with a required reviewer and **never triggered by a pull
request**, so a fork PR can never spend the key (GitHub does not pass secrets to
fork-PR workflows, and this one does not run on them at all).

## Provenance: verify it yourself (v0.5)

Every published entry can be checked two ways — **who** wrote it and **when** it
existed — by anyone, with only public material.

- **Signed entries.** Before merging a devlog PR I GPG-sign the *merged* entry
  (my YubiKey, on the PR branch) — so the signature ships in the same PR and **no
  signing key is ever in CI**. The canonical hash covers only
  `{slug, title, published_at, type, series, body}`, so the badge and sidecar
  can't invalidate it.
- **A merkle transparency log.** Each signed entry is a leaf in an append-only log
  (`provenance/log.jsonl`); a cumulative [RFC 6962](https://datatracker.ietf.org/doc/html/rfc6962)
  root lives in `provenance/root.json`, giving per-entry inclusion proofs.
- **Anchored (testnet).** The current root can be timestamped in a Cardano testnet
  transaction's metadata (a pluggable backend — `null` by default, `file` for dev,
  or `cardano`). Signatures are per-entry; the anchor is cumulative and periodic.

To check it, with only the committed public key:

```bash
pipeline provenance verify           # recompute every leaf + signature + the root (offline)
pipeline provenance verify --chain   # also read each anchor back from the chain
pipeline provenance show --slug 2026-W27   # leaf hash, signature, inclusion proof, anchors
```

`verify` recomputes each leaf from the *actual* published entry, so a post-hoc
edit fails loudly. `.github/workflows/provenance.yml` runs it **offline, with no
secrets** as an integrity gate. The engine never anchors mainnet, never merges,
and never publishes — provenance only adds proof to what a human already reviewed.

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
uv run pipeline transform          # two-stage AI transform → drafts/ (prompts you to pick a focus thread)
uv run pipeline transform --focus <thread-id>   # lead the entry on specific thread(s), no prompt
uv run pipeline run                # collect + transform
uv run pipeline review             # list drafts awaiting review
uv run pipeline publish            # copy approved/ devlog into the sporny.pl repo (manual, no push)
uv run pipeline eval               # score the transformer over the golden cases → evals/RESULTS.md

uv run pytest                      # the tests are part of the story
```

Weekly automation lives in `.github/workflows/weekly.yml` — see SPEC.md
Module 5. Since v0.4.1 the engine is **stateless**: it runs against the site repo
as its `state.root` and opens **one** PR that bundles the rendered devlog with
that week's `raw/` activity snapshot and `memory/` updates — the audit trail and
the seed for next week's continuity, now living with the instance rather than
committed back to the engine repo. Merging the PR publishes. A manual
`workflow_dispatch` run accepts an optional `focus` input to lead the entry on
specific thread(s); the scheduled run lets the model pick.

## Running the weekly Action (fork setup)

To run the automation on your own fork, set three **repository secrets** under
*Settings → Secrets and variables → Actions → New repository secret*:

| Secret | What | Scope to grant |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Claude API key | — |
| `GH_ACTIVITY_TOKEN` | Fine-grained PAT | **read-only** contents/PRs/issues on your allowlisted repos |
| `LANDING_PAGE_TOKEN` | Fine-grained PAT | **write** on your website repo — enough to push a branch and open a PR, **never** merge |

The token scopes are deliberately minimal: the pipeline reads only what you list
in `config.yaml`, and the write token can propose but not publish — the merge
stays a human action.

Two more one-time changes so the Action targets *your* site, not mine:

- **Your instance = your site repo.** The engine is stateless; your instance lives
  in your website repo, which holds its own `config.yaml` (with `state.root: "."`
  and `output.site_repo_path: "."`), its `raw/` + `memory/` state, and `content/`.
  Point `weekly.yml`'s `repository:` and PR steps at that repo, gitignore the
  `drafts/`/`approved/`/`published/` working dirs there, and make sure
  `output.adapter` resolves an adapter that renders *your* site's schema (see
  [Fork it: write an adapter](#fork-it-write-an-adapter)).
- **No engine write-back.** The engine commits nothing to its own repo, so it needs
  no `contents: write` there; the single weekly PR (devlog + `raw/` + `memory/`)
  goes to your site repo via `LANDING_PAGE_TOKEN`, and a human merges to publish.

The schedule is a `cron` in `weekly.yml` (Sundays 16:00 UTC); adjust to taste, or
trigger a run by hand from the Actions tab (*Run workflow*), optionally passing
`since` for a backfill and `focus` to lead on specific thread(s).

**Eval workflow (optional, uses the model).** `evals.yml` calls the paid API. It
reuses the same `ANTHROPIC_API_KEY` **repo secret** as the weekly Action — no
separate secret needed. Because a fork PR never receives repo secrets and this
workflow doesn't run on `pull_request`, an outside contributor can't spend the key
regardless.

The `evals` **environment** the workflow references adds one thing on top: a
manual **approval gate**. Create an environment named `evals` (*Settings →
Environments*) and add a **required reviewer** — the eval job then pauses until you
approve it, so an expensive run never fires unattended (e.g. on a `main` push that
touches a prompt). You do **not** move the key into the environment: the approval
gate comes from the job referencing the environment, while `secrets.ANTHROPIC_API_KEY`
still resolves to your repo secret. (You *may* add a separate environment-scoped
key if you want one you can rotate independently — if you do, remember the
environment copy is the one the eval job uses.) Set a spend cap on the key too.

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
ends with a proof-of-work link. (This first entry baked `#1` into its title;
newer entries emit a bare subtitle and the site adds the `Senior SDET log #N:`
prefix — see below.)

## Devlog manifest

On every publish the pipeline regenerates `content/devlog/index.json` in the
website repo — the list the devlog page reads. It scans every front-mattered
`.md` in the devlog dir and emits one entry each:

```json
{
  "type": "weekly-activity",     // or "custom" for hand-written notes/essays
  "series": "Senior SDET log",   // role identity, emitted per entry
  "n": 1,                        // per-series number, shared by weekly + custom, frozen once set
  "slug": "2026-W27",            // = the .md filename, and the page #hash anchor
  "title": "Senior SDET log #1: turning panics into exit codes, …",
  "date": "2026-07-05"           // drives ordering + the "Published" line
}
```

Entries order by `date`, so hand-written **custom** entries interleave with the
weekly ones. **Numbering is the pipeline's job:** `n` is a single per-series
sequence across both types, assigned by the site adapter and frozen once set
(re-runs never renumber). Weekly titles are a bare subtitle — the site renders
`Senior SDET log #<n>: <subtitle>` — so nothing hand-bakes a number.

To add a custom note or essay, write a plain Markdown file whose first `# H1`
is the title, then run:

```bash
pipeline publish-custom looking-ahead-2036.md --site-repo ~/code/sporny.pl
```

That drops a fully-formed `content/devlog/looking-ahead-2036.md` into the website
repo (front matter, `type: custom`, the next series number) and regenerates
`index.json`. It never commits or pushes — you verify locally and merge, exactly
like the weekly PR. See
[SPEC.md → Module 4](SPEC.md#manifest-schema-contentdevlogindexjson-owned-by-the-website)
for the full schema and authoring details.

## Fork it: write an adapter

Everything specific to sporny.pl — the devlog file layout, the `index.json`
manifest schema, and the per-series numbering rules — lives in **one** module,
`src/pipeline/site_adapter/sporny_pl.py`. The pipeline core knows only a small
interface:

```python
render(entry, ctx) -> list[FileChange]   # the devlog markdown + regenerated manifest
```

`publish` resolves the adapter named in `output.adapter`, calls `render`, and
writes the returned file changes into your website checkout (it never commits or
pushes). To publish to a different site, write another adapter implementing that
interface, register it in `site_adapter/__init__.py`, and point `output.adapter`
at it — no other code changes. Nothing site-specific lives outside that package.

## Stack

Python 3.11+, [`uv`](https://docs.astral.sh/uv/), `typer` (CLI), `httpx`
(GitHub), the official `anthropic` SDK (model `claude-opus-4-8`), `pydantic`
(schemas + validation), `ruff` (lint/format), `pytest` (mocked APIs — zero real
network calls in tests). Full detail in [SPEC.md](SPEC.md).
