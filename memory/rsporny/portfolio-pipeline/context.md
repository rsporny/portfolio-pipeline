# rsporny/portfolio-pipeline

**What it is:** Turns a week of real engineering work into content drafts, with
a human gate at the end. Once a week it collects the owner's public GitHub
activity (commits, PRs, closed issues) and runs a two-stage Claude transform
(Stage A technical summary → Stage B writing) to produce a titled **devlog**
entry, one channel-neutral **social** post, and a list of **highlights**. Since
v0.2 it keeps this per-repo thread registry so entries connect into longer arcs.
Output feeds the devlog on sporny.pl.

**Data flow:** collect → redact → transform (Stage A/B) → drafts/ → weekly
GitHub Action opens a PR against the sporny.pl repo → human reviews and merges →
Cloudflare deploys. Nothing publishes automatically; the merge is the approval.

**Design commitments:** allowlist-only collection (default deny, no "scan all");
secrets via environment only; content is knowledge-sharing, never a pitch;
third-party names redacted by default. Meant to be forkable — site-specific
logic lives only in the site adapter.

**Stack:** Python 3.11+, `uv`, `typer` (CLI), `httpx` (GitHub REST), official
`anthropic` SDK (`claude-opus-4-8`), `pydantic`, `ruff`, `pytest` (mocked APIs,
zero real network calls). Full detail in SPEC.md.

**Owner's role:** Radosław Sporny — sole author and maintainer, Senior SDET.
Also the human-in-the-loop reviewer. This is the pipeline observing its own
development, so its early devlog entries are about building it.
