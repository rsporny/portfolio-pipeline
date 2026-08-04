from __future__ import annotations

# Published-entry continuity (SPEC roadmap): before Stage B, retrieve a handful
# of the owner's OWN past published writing related to the current draft and feed
# it in, so narrative arcs connect across weeks instead of resetting.
#
# Retrieval is at the *section* (`##`) level, not the whole entry. A weekly entry
# usually spans several unrelated topics — one `##` section per initiative/thread
# — so feeding a top-of-entry excerpt would miss the section that actually
# continues the current thread (it may sit thousands of characters down). Scoring
# each section on its own heading keeps retrieval precise and the fed text small.
#
# This is deliberately distinct from memory. Memory (`memory/{org}/{repo}/`) is
# derived thread *state* — summaries and assumptions. This reads the actual
# published *prose*. Cheap Python — plain token overlap, no LLM, no embeddings.
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import parse

# Cap on how much of each matched section's body reaches Stage B. A module
# constant (not config) to bound input tokens without widening the config surface.
EXCERPT_CHARS = 1500

# A rendered `##` heading — the section title the site keys its dividers off, and
# the strong per-section signal retrieval scores on.
_SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
# A leading `# H1` line (the entry title), stripped from a single-section body.
_H1_RE = re.compile(r"^#[ \t]+.+?(?:\n|$)")
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

# Minimum heading-token overlap for a past section to count as prior published
# *coverage* of a thread (used by the presented-as-new check). Three shared,
# non-stopword heading tokens is a deliberately conservative bar: on real data a
# genuine topical match shares many (6+), while an incidental generic overlap
# ("state", "memory") tends to be one or two — so this keeps the advisory check
# from false-flagging a thread's legitimate first appearance.
COVERAGE_MIN_HEAD_OVERLAP = 3


def tokenize(text: str) -> set[str]:
    """Lowercase word tokens, minus stopwords, sub-3-char tokens, and pure
    numbers — the shared vocabulary retrieval scores overlap on."""
    return {
        tok
        for tok in _WORD.findall(text.lower())
        if len(tok) >= 3 and tok not in _STOPWORDS and not tok.isdigit()
    }


def thread_query_tokens(thread: object) -> set[str]:
    """A thread's vocabulary: its title, summary, and assumption texts. Duck-typed
    so this module need not import the memory models."""
    tokens = tokenize(getattr(thread, "title", "") or "")
    tokens |= tokenize(getattr(thread, "summary", "") or "")
    for assumption in getattr(thread, "assumptions", []) or []:
        tokens |= tokenize(getattr(assumption, "text", "") or "")
    return tokens


def query_tokens_for(threads: Iterable[object], initiatives: Iterable[object]) -> set[str]:
    """The query vocabulary describing the current draft: the current work threads
    plus this week's initiatives (name / category)."""
    tokens: set[str] = set()
    for thread in threads:
        tokens |= thread_query_tokens(thread)
    for init in initiatives:
        tokens |= tokenize(getattr(init, "name", "") or "")
        tokens |= tokenize(getattr(init, "category", "") or "")
    return tokens


@dataclass
class Section:
    """One `##` section of a past published entry, with its heading/body tokens
    precomputed once so scoring against several queries stays cheap."""

    slug: str
    series: str
    date: str
    entry_title: str
    heading: str
    body: str
    head_tokens: set[str] = field(default_factory=set)
    body_tokens: set[str] = field(default_factory=set)


@dataclass
class RelatedSection:
    """A past published section selected for continuity, with a bounded excerpt of
    its prose and the score that ranked it."""

    slug: str
    series: str
    date: str
    entry_title: str
    heading: str
    body: str
    score: int


def _excerpt(body: str, limit: int = EXCERPT_CHARS) -> str:
    """First ``limit`` chars of a body, cut on a word boundary, with an ellipsis
    when truncated."""
    body = body.strip()
    if len(body) <= limit:
        return body
    cut = body[:limit]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip() + " …"


def _split_sections(body: str) -> list[tuple[str, str]]:
    """Split an entry body into ``(heading, text)`` per `##` section. A body with
    no `##` headings is one section whose heading is empty and text is the body
    minus its leading `# H1` (a single flowing entry). Any preamble before the
    first `##` (the H1 + intro) is dropped for a multi-section entry."""
    heads = list(_SECTION_RE.finditer(body))
    if not heads:
        return [("", _H1_RE.sub("", body.strip(), count=1).strip())]
    sections: list[tuple[str, str]] = []
    for i, match in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        sections.append((match.group(1).strip(), body[match.end() : end].strip()))
    return sections


def load_sections(
    site_dir: Path | str | None,
    *,
    exclude: set[str] | None = None,
    excerpt_chars: int = EXCERPT_CHARS,
) -> list[Section]:
    """Parse every published ``content/devlog/*.md`` into scored-ready sections.
    A missing/None ``site_dir`` (a fresh instance) returns ``[]``; files that
    don't parse or aren't published are skipped, never raised.

    For a single-section (flowing) entry the entry's ``source_initiatives`` are
    folded into the section's heading tokens, since its heading is just the entry
    title; multi-section entries score on their specific `##` heading only, so a
    query for one thread doesn't match every section of the same entry."""
    if site_dir is None:
        return []
    site_dir = Path(site_dir)
    if not site_dir.exists():
        return []
    exclude = exclude or set()

    out: list[Section] = []
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
        series = str(front.get("series", ""))
        date = str(front.get("published_at") or front.get("date") or "")
        entry_title = str(front.get("title", slug))
        source_tokens: set[str] = set()
        for name in front.get("source_initiatives") or []:
            source_tokens |= tokenize(str(name))
        topic_cat = _topic_categories(front)

        parts = _split_sections(body)
        flowing = len(parts) == 1 and parts[0][0] == ""
        for heading, text in parts:
            head_tokens = tokenize(heading) | topic_cat.get(heading, set())
            if flowing:
                head_tokens |= source_tokens | tokenize(entry_title)
            out.append(
                Section(
                    slug=slug,
                    series=series,
                    date=date,
                    entry_title=entry_title,
                    heading=heading or entry_title,
                    body=_excerpt(text, excerpt_chars),
                    head_tokens=head_tokens,
                    body_tokens=tokenize(text),
                )
            )
    return out


def _topic_categories(front: dict) -> dict[str, set[str]]:
    """Map each `topics[].title` to its category tokens, so a section can pick up
    its own category as a weak extra signal (headings are the topic titles)."""
    cats: dict[str, set[str]] = {}
    for topic in front.get("topics") or []:
        if isinstance(topic, dict) and topic.get("title"):
            cats[str(topic["title"])] = tokenize(str(topic.get("category", "")))
    return cats


def score_sections(
    sections: Iterable[Section],
    query_tokens: set[str],
    *,
    max_entries: int = 3,
) -> list[RelatedSection]:
    """Rank past sections by overlap with ``query_tokens`` and return the top
    ``max_entries``. A section must overlap the query on its *heading* (its topic
    must relate to the current draft) — this is what keeps a multi-topic entry
    from matching on an unrelated section. Heading matches weigh ×2 over body
    matches; ties break by recency. ``max_entries <= 0`` or an empty query returns
    ``[]``."""
    if not query_tokens or max_entries <= 0:
        return []
    scored: list[RelatedSection] = []
    for sec in sections:
        matched_head = query_tokens & sec.head_tokens
        if not matched_head:
            continue
        matched_body = query_tokens & sec.body_tokens
        score = 2 * len(matched_head) + len(matched_body)
        scored.append(
            RelatedSection(
                slug=sec.slug,
                series=sec.series,
                date=sec.date,
                entry_title=sec.entry_title,
                heading=sec.heading,
                body=sec.body,
                score=score,
            )
        )
    scored.sort(key=lambda s: (s.score, s.date), reverse=True)
    return scored[:max_entries]


def covered_thread_ids(
    sections: Iterable[Section],
    threads: Iterable[object],
    *,
    min_head_overlap: int = COVERAGE_MIN_HEAD_OVERLAP,
) -> set[str]:
    """The ids of threads that already have prior published *coverage* — a past
    section whose heading overlaps the thread's own vocabulary by at least
    ``min_head_overlap`` tokens. Distinct from a thread merely existing in memory:
    a thread can be weeks old yet never written about, so this reads the published
    prose. Used to flag an entry that re-introduces such a thread as brand new."""
    section_list = list(sections)
    covered: set[str] = set()
    for thread in threads:
        tid = getattr(thread, "id", None)
        if not tid:
            continue
        tokens = thread_query_tokens(thread)
        if not tokens:
            continue
        for sec in section_list:
            if len(tokens & sec.head_tokens) >= min_head_overlap:
                covered.add(tid)
                break
    return covered
