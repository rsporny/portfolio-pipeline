# Changelog — portfolio-pipeline

Per-version "what shipped" record. The numbered build plans that produced each
version are complete and no longer carried here; the roadmap for what's next
lives in `SPEC.md`. Releases are git-tagged as signed annotated tags
`vMAJOR.MINOR.PATCH` from v0.6 onward.

## v0.7.0 — indexer & thread-memory hardening

From live-instance runs. Four fixes to how the registry — the pipeline's
long-term memory — is maintained and surfaced.

- **(a) Merge-state awareness:** `activity.json` is now `schema_version: 4`,
  each PR carrying a derived `outcome` (`merged` | `closed_unmerged` | `open`,
  backfilled for v3 files); a `closed_unmerged` PR is a decision/postponement,
  so Stage A/B frame it as such (never shipped) and the indexer can record it as
  a `key_decision` on an *existing* thread — `ThreadUpdate` gained
  `new_key_decisions` (before this only a brand-new thread could carry
  decisions).
- **(b) Goal-oriented summaries:** the indexer now *rewrites* a thread summary
  toward its goal (anchored to the parent issue/epic from deep-context
  `linked_issues`), superseding the prior one rather than appending each week.
- **(c) Premise-level assumptions:** the indexer prompt draws a clear line —
  assumptions are conservative, testable beliefs about a thread's premise;
  events and decisions go to `key_decisions`, not assumptions.
- **(d) Focus picker:** candidates are deduped by thread id (`_active_threads`)
  and each is offered as a `FocusCandidate` labelled with status, age, this
  week's relation, and a summary snippet, so terse or near-identical titles are
  distinguishable.

No new thread-registry *fields* beyond `new_key_decisions` (goal-orientation and
postponement framing are prompt-driven, reusing existing summary/decision
fields). A `merge-state` golden case exercises the postponement framing under
the check suite.

## v0.6.2 — per-repo indexer scoping (memory-correctness fix)

The indexer runs once per repo, but Stage A emits a single cross-repo initiative
list and every repo's indexer received *all* of it — so an unrelated repo's
indexer would create a thread for another repo's work in its own registry. Live
symptom: one thread id (`reader-output-and-publishing-automation`) written into
three registries, each with a different title, which then surfaced the same id
several times in one week's focus candidates. `transform_week` now scopes each
repo's indexer to only the initiatives whose work is in that repo
(`_initiatives_for_repo`, matching `owner/repo` parsed from each initiative's
links); a repo with no initiatives of its own is skipped entirely (subsuming the
"skip inactive repo" idea). This stops the indexer from creating or referencing
a foreign thread.

## v0.6.1 — section-level continuity retrieval

A weekly entry spans several `##` topics; v0.6.0 fed Stage B a top-of-entry
excerpt, so the section continuing the current thread — often thousands of
characters down — was missed and the entry re-introduced the topic as new.
`continuity.py` now splits each past entry by `##`, scores per section (a
section must overlap the query on its *heading*, keeping a multi-topic entry
from matching on an unrelated section), and feeds the relevant section(s) whole.
It also derives `covered_thread_ids` — the referenced threads that already have
prior published coverage — which drives a new **advisory** check
`continuity_not_reset` (`checks.py`): a section that continues a covered thread
but frames it as brand new (*"new here", "first time", …*) is a warn, not a hard
gate (framing is a judgement call).

## v0.6.0 — published-entry continuity

A new `src/pipeline/continuity.py` does cheap Python retrieval (no LLM, no
embeddings) over the site's already-published `content/devlog/*.md`: it scores
each entry's front-matter tokens (`series`, `title`, `topics`, and
`source_initiatives` weighted ×2) against the current draft's vocabulary — the
active/referenced work threads plus this week's initiatives — and loads the top
few *bodies* (excerpt-capped, `EXCERPT_CHARS`). `transform_week` feeds them to
Stage B as a "Past published entries" block so the writer builds on its own
earlier prose instead of resetting each week — distinct from memory (derived
thread *state*); this is the actual published *prose*. The block is redacted
before the call like every model input, and the step is failure-tolerant: a
missing/empty site dir or any error yields no context and never blocks the run.
Bounded by `content.continuity_max_entries` (default 3, 0 disables). From this
release, versions are git-tagged as signed annotated tags `vMAJOR.MINOR.PATCH`.

## v0.5.0 — provenance

A new `src/pipeline/provenance/` package: `content` (an entry's commitment is
the plain `sha256` of its raw `<slug>.md`, reproducible with `sha256sum`), `log`
(an append-only `provenance/log.jsonl` under `state.root`, idempotent by slug,
each record carrying the entry's hash, signature, and optional per-entry
anchor), `sign` (injectable detached GPG signing over the file bytes, so
`gpg --verify` works) and `verify` (recompute each file hash + signature,
optional `--chain`). Every entry is an independent proof — no merkle tree or
cumulative root. Anchoring is a pluggable backend (`null` default, `file`, and a
lazy `cardano` testnet backend via `pycardano`+Blockfrost, an optional extra)
writing `{slug, sha256, v}` per entry. Signing is a deliberate local GPG act on
the PR branch before merge — **no key ever enters CI**; the `sporny_pl`
adapter's `attach_provenance`/`attach_anchor` write the `<slug>.md.sig` sidecar
and public key and carry the verify-badge fields in the manifest, never touching
the `.md`. New `pipeline provenance sign|anchor|verify|show`; `provenance.yml`
runs an offline, secret-free `verify` as an integrity gate.

## v0.4.1 — engine / state separation

The tool is now a **stateless, forkable engine**: it ships no committed
`raw/`/`memory/`, and every state area resolves under one configurable
`state.root` via `Config.state_dir(name)`. An *instance* is a single tree — the
site repo — holding its own `config.yaml`, state (`raw/`, `memory/`, later
`provenance/`), and `content/`; the engine is run from there and writes nothing
outside `state.root` (a test asserts this). The weekly workflow installs the
engine and runs it against the site checkout, opening **one** PR that bundles
the rendered devlog with its `raw/` + `memory/` state — the engine commits
nothing back to its own repo.

## v0.4.0 — transformer eval suite

A pure structural check library (`src/pipeline/checks.py`) scores every
generated draft on content policy and faithfulness; `transform_week` runs it
after Stage B and **blocks** the run on any hard (`error`-severity) violation.
`pipeline eval` runs the real transformer over `evals/cases/**` and writes a
committed scorecard (`evals/RESULTS.md`); `evals.yml` drives it in CI on
prompt/model changes, behind a protected environment and never reachable by a
fork PR (the only workflow holding `ANTHROPIC_API_KEY`).

## v0.3.0 — selective deep context

The collector enriches the owner-PRs of any repo with an ongoing thread with
review discussion and linked issues, and the long-specified third-party name
redaction is implemented — anonymization happens in the collector so `raw/`
(public) never carries a collaborator's name, while phrase redaction still runs
before every model call. `schema_version` is now 3 (v2 files still parse). Deep
context reaches the model structurally, guarded by "understanding only, never
quote" instructions across Stage A / indexer / Stage B.

## v0.2.0 — memory era

The pipeline tracks work threads per org/repo; the weekly workflow auto-picks
the lead thread (no TTY → no prompt), a `workflow_dispatch` `focus` input can
override it, and the Action commits each week's `raw/` snapshot and `memory/`
updates back to `main` as labeled bot commits — derived, regenerable state that
outlives the ≤90-day artifacts. The Action only *opens* a PR, never merges.
