from __future__ import annotations

# Published-entry continuity (SPEC roadmap): before Stage B, retrieve a handful
# of the owner's OWN past published entries related to the current draft and feed
# their prose in, so narrative arcs connect across weeks instead of resetting.
#
# This is deliberately distinct from memory. Memory (`memory/{org}/{repo}/`) is
# derived thread *state* — summaries and assumptions the indexer maintains. This
# reads the actual published *prose* of `content/devlog/*.md`. Retrieval is cheap
# Python — plain token overlap, no LLM and no embeddings — and bounded: only the
# top few matching bodies are loaded, each excerpt-capped.
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .frontmatter import parse

# Cap on how much of each matched entry's body reaches Stage B. Kept a module
# constant (not config) to bound input tokens without widening the config surface.
EXCERPT_CHARS = 1500

_WORD = re.compile(r"[a-z0-9]+")

# Small English/domain stopword set — enough to keep token overlap meaningful
# without pulling in a dependency. Not exhaustive by design.
_STOPWORDS = frozenset(
    """
    the a an and or but if then else for to of in on at by with from into over under
    is are was were be been being this that these those it its as no not so than too very
    i you he she we they me him her us them my your his our their mine yours ours theirs
    do does did done doing have has had having will would can could should may might must
    about after again all also any because before between both each few how more most other
    some such only own same up down out off out own here there when where which who whom why
    what week work built build make made new use used using via per get got new one two
    """.split()
)


@dataclass
class RelatedEntry:
    """A past published entry selected for continuity, with an excerpt of its
    prose (not the whole file — input tokens stay bounded)."""

    slug: str
    title: str
    series: str
    date: str  # published_at / date front matter (ISO strings sort lexically)
    body: str
    score: int


def tokenize(text: str) -> set[str]:
    """Lowercase word tokens, minus stopwords, sub-3-char tokens, and pure
    numbers — the shared vocabulary retrieval scores overlap on."""
    return {
        tok
        for tok in _WORD.findall(text.lower())
        if len(tok) >= 3 and tok not in _STOPWORDS and not tok.isdigit()
    }


def query_tokens_for(
    threads: Iterable[object],
    initiatives: Iterable[object],
) -> set[str]:
    """The query vocabulary describing the current draft: the current work
    threads (title / summary / assumption texts) plus this week's initiatives
    (name / category). Duck-typed so callers can pass ``Thread`` /``Initiative``
    objects without this module importing them."""
    tokens: set[str] = set()
    for thread in threads:
        tokens |= tokenize(getattr(thread, "title", "") or "")
        tokens |= tokenize(getattr(thread, "summary", "") or "")
        for assumption in getattr(thread, "assumptions", []) or []:
            tokens |= tokenize(getattr(assumption, "text", "") or "")
    for init in initiatives:
        tokens |= tokenize(getattr(init, "name", "") or "")
        tokens |= tokenize(getattr(init, "category", "") or "")
    return tokens


def _excerpt(body: str, limit: int = EXCERPT_CHARS) -> str:
    """First ``limit`` chars of a body, cut on a word boundary, with an ellipsis
    when truncated. Leading/trailing whitespace trimmed."""
    body = body.strip()
    if len(body) <= limit:
        return body
    cut = body[:limit]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip() + " …"


def _entry_tokens(front: dict) -> tuple[set[str], set[str]]:
    """(base_tokens, source_tokens) for a published entry's front matter. Source
    tokens come from ``source_initiatives`` — the SPEC's primary retrieval key —
    and are scored with extra weight by the caller."""
    source: set[str] = set()
    for name in front.get("source_initiatives") or []:
        source |= tokenize(str(name))
    base: set[str] = set()
    base |= tokenize(str(front.get("series", "")))
    base |= tokenize(str(front.get("title", "")))
    for topic in front.get("topics") or []:
        if isinstance(topic, dict):
            base |= tokenize(str(topic.get("title", "")))
            base |= tokenize(str(topic.get("category", "")))
    return base, source


def retrieve_related(
    site_dir: Path | str | None,
    query_tokens: set[str],
    *,
    exclude: set[str] | None = None,
    max_entries: int = 3,
    excerpt_chars: int = EXCERPT_CHARS,
) -> list[RelatedEntry]:
    """Scan a site's published ``content/devlog/*.md``, score each entry's token
    overlap against ``query_tokens`` (``source_initiatives`` matches weighted ×2),
    and return the top ``max_entries`` bodies (excerpt-capped, zero-score dropped),
    ranked by score then recency.

    Safe by construction: a missing/None ``site_dir`` or ``max_entries <= 0``
    returns ``[]`` so the caller need not special-case a fresh instance. Files
    that don't parse are skipped, never raised."""
    if not query_tokens or max_entries <= 0 or site_dir is None:
        return []
    site_dir = Path(site_dir)
    if not site_dir.exists():
        return []
    exclude = exclude or set()

    scored: list[RelatedEntry] = []
    for path in sorted(site_dir.glob("*.md")):
        try:
            front, body = parse(path.read_text())
        except (OSError, ValueError):
            continue
        slug = str(front.get("slug") or path.stem)
        if slug in exclude:
            continue
        status = front.get("status")
        if status is not None and status != "published":
            continue
        base, source = _entry_tokens(front)
        matched = query_tokens & (base | source)
        if not matched:
            continue
        # +1 per match, +1 extra for a source_initiatives match ⇒ those count ×2.
        score = len(matched) + len(matched & source)
        scored.append(
            RelatedEntry(
                slug=slug,
                title=str(front.get("title", slug)),
                series=str(front.get("series", "")),
                date=str(front.get("published_at") or front.get("date") or ""),
                body=_excerpt(body, excerpt_chars),
                score=score,
            )
        )

    scored.sort(key=lambda e: (e.score, e.date), reverse=True)
    return scored[:max_entries]
