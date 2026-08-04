"""v0.4 eval suite — structural content-policy and quality checks over transformer
output.

The library is *pure*: every function returns CheckResults and never raises.
Callers decide what a failure means — production (``transform_week``) blocks on
``error``-severity results and warns on the rest; the eval runner only tallies
them. This keeps one check implementation shared by both.

SPEC Module 3 (Stage B content policy) and hard constraint 3 are enforced as
prose in the prompts; here we add the structural backstop SPEC line 17 promised
("must also be enforced structurally where possible … automated checks arrive in
v0.4"). These are best-effort property assertions, not proofs — human review of
the PR remains the final gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .models import Activity, Content, Initiatives

Severity = Literal["error", "warn"]

# Stage B word ranges (SPEC Module 3). Enforced as soft-quality warnings.
DEVLOG_MIN, DEVLOG_MAX = 400, 750
SOCIAL_MIN, SOCIAL_MAX = 100, 180
MAX_HASHTAGS = 3
INITIATIVE_MIN, INITIATIVE_MAX = 2, 5

# Heuristic solicitation / call-to-action markers. The content policy forbids any
# CTA, service offer, availability announcement, or solicitation. This regex net
# catches the common phrasings; it is deliberately conservative and is not a
# substitute for review — but a hit is a hard (error-severity) policy violation.
_SOLICITATION = [
    r"contact me",
    r"hire me",
    r"\bhiring\b",
    r"available for (?:hire|work|freelance|consulting|projects)",
    r"reach out",
    r"get in touch",
    r"\bdm me\b",
    r"let'?s connect",
    r"sign up",
    r"subscribe",
    r"book a call",
    r"follow me",
    r"check out my",
]
_SOLICITATION_RE = re.compile("|".join(_SOLICITATION), re.IGNORECASE)

_URL_RE = re.compile(r"https?://[^\s\)\]\}>\"']+")
# A GitHub @mention: an "@" that starts a handle and is not part of an email.
_MENTION_RE = re.compile(r"(?<![\w/])@[A-Za-z0-9][A-Za-z0-9-]*")
_HASHTAG_RE = re.compile(r"#\w+")
_SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_GITHUB_REPO_RE = re.compile(r"github\.com/([^/\s]+/[^/\s]+)")

# Phrasings that frame a topic as appearing for the first time in the devlog. A
# section that continues a thread already written about in a past published entry
# must not use them — it should refer back instead. Advisory (a judgement call),
# so this is a warn, not a hard gate.
_NOVELTY = [
    r"\bnew here\b",
    r"\bthis is the first time\b",
    r"\bfirst time (?:this|it) \w+ (?:appears|shows up|comes up)",
    r"\bfirst appears? (?:here|in these)\b",
    r"\bfirst appearance\b",
    r"\bbrand[-\s]new\b",
    r"\bmakes its (?:first )?(?:appearance|debut)\b",
    r"\bfor the first time here\b",
]
_NOVELTY_RE = re.compile("|".join(_NOVELTY), re.IGNORECASE)


@dataclass(frozen=True)
class CheckResult:
    """One property assertion's outcome. ``severity`` decides how a caller reacts:
    ``error`` = hard content-policy violation (production halts), ``warn`` =
    soft-quality issue (logged, non-blocking)."""

    name: str
    passed: bool
    severity: Severity
    detail: str = ""


@dataclass(frozen=True)
class CheckContext:
    """The evidence the checks judge output against, built once per run."""

    activity_links: set[str] = field(default_factory=set)
    forbidden_phrases: list[str] = field(default_factory=list)
    placeholder: str = "[collaborator]"
    thread_ids: frozenset[str] = frozenset()
    # Ids of threads referenced this week that already have prior published
    # coverage (a past entry wrote about them) — so re-introducing them as new is
    # a continuity slip. Populated by the continuity retrieval; empty otherwise.
    prior_thread_ids: frozenset[str] = frozenset()


def activity_links(activity: Activity) -> set[str]:
    """Every proof-of-work URL present in the week's activity — the set an
    initiative or devlog may faithfully cite. A URL in the output that is not in
    this set is invented (a faithfulness violation)."""
    links: set[str] = set()
    for repo in activity.repos:
        for commit in repo.commits:
            if commit.url:
                links.add(commit.url)
        for pr in repo.pull_requests:
            if pr.url:
                links.add(pr.url)
            for issue in pr.linked_issues:
                if issue.url:
                    links.add(issue.url)
        for issue in repo.issues:
            if issue.url:
                links.add(issue.url)
    return links


def build_context(
    activity: Activity,
    *,
    forbidden_phrases: list[str] | None = None,
    placeholder: str = "[collaborator]",
    thread_ids: set[str] | frozenset[str] | None = None,
    prior_thread_ids: set[str] | frozenset[str] | None = None,
) -> CheckContext:
    """Assemble a :class:`CheckContext` from a week's activity and config values."""
    return CheckContext(
        activity_links=activity_links(activity),
        forbidden_phrases=list(forbidden_phrases or []),
        placeholder=placeholder,
        thread_ids=frozenset(thread_ids or ()),
        prior_thread_ids=frozenset(prior_thread_ids or ()),
    )


def _word_count(text: str) -> int:
    return len(text.split())


def _extract_urls(text: str) -> set[str]:
    # Strip trailing prose punctuation a URL may pick up in a sentence.
    return {m.rstrip(".,;:") for m in _URL_RE.findall(text)}


def _bounds(name: str, value: int, low: int, high: int, unit: str) -> CheckResult:
    ok = low <= value <= high
    detail = f"{value} {unit}" + ("" if ok else f" (want {low}–{high})")
    return CheckResult(name, ok, "warn", detail)


def check_content(content: Content, ctx: CheckContext) -> list[CheckResult]:
    """Structural checks over a Stage B :class:`Content` result."""
    devlog, social = content.devlog, content.social
    combined = f"{devlog}\n{social}"
    published = "\n".join([devlog, social, *content.highlights])
    results: list[CheckResult] = []

    # Word limits (soft quality).
    results.append(
        _bounds("devlog_word_count", _word_count(devlog), DEVLOG_MIN, DEVLOG_MAX, "words")
    )
    results.append(
        _bounds("social_word_count", _word_count(social), SOCIAL_MIN, SOCIAL_MAX, "words")
    )

    # Hashtag budget in the social post (soft).
    tags = _HASHTAG_RE.findall(social)
    results.append(
        CheckResult(
            "social_hashtags",
            len(tags) <= MAX_HASHTAGS,
            "warn",
            f"{len(tags)} hashtag(s) (max {MAX_HASHTAGS})",
        )
    )

    # Tone: no exclamation marks (soft).
    excl = combined.count("!")
    results.append(CheckResult("no_exclamation", excl == 0, "warn", f"{excl} exclamation mark(s)"))

    # Solicitation / CTA (hard policy).
    hits = sorted({m.group(0).lower() for m in _SOLICITATION_RE.finditer(combined)})
    results.append(
        CheckResult(
            "no_solicitation",
            not hits,
            "error",
            f"solicitation phrase(s): {', '.join(hits)}" if hits else "",
        )
    )

    # Collaborator leak: the anonymization placeholder or a raw @mention must
    # never surface in published text (hard policy — deep context is for
    # understanding only, never quoted or attributed).
    leaks: list[str] = []
    if ctx.placeholder and ctx.placeholder in published:
        leaks.append(f"placeholder {ctx.placeholder!r}")
    mentions = sorted({m.group(0) for m in _MENTION_RE.finditer(published)})
    if mentions:
        leaks.append(f"mention(s) {', '.join(mentions)}")
    results.append(CheckResult("no_collaborator_leak", not leaks, "error", "; ".join(leaks)))

    # Forbidden-phrase leak (hard). Phrases are masked in the input before every
    # model call, so a hit here means the model produced one itself.
    lowered = published.lower()
    present = [p for p in ctx.forbidden_phrases if p and p.lower() in lowered]
    results.append(
        CheckResult(
            "no_forbidden_phrase",
            not present,
            "error",
            f"{len(present)} forbidden phrase(s) present" if present else "",
        )
    )

    # Faithful links: every URL cited must exist in the activity (hard).
    cited = _extract_urls("\n".join([devlog, *content.highlights]))
    invented = sorted(cited - ctx.activity_links)
    results.append(
        CheckResult(
            "faithful_links",
            not invented,
            "error",
            f"invented URL(s): {', '.join(invented)}" if invented else "",
        )
    )

    # Proof-of-work present (soft): the devlog should cite at least one real link
    # (skipped when the activity carried no URLs at all).
    has_pow = bool(_extract_urls(devlog) & ctx.activity_links)
    results.append(
        CheckResult(
            "proof_of_work_present",
            has_pow or not ctx.activity_links,
            "warn",
            "" if has_pow else "no proof-of-work link in the devlog",
        )
    )

    return results


def check_initiatives(initiatives: Initiatives, ctx: CheckContext) -> list[CheckResult]:
    """Structural checks over a Stage A :class:`Initiatives` result."""
    items = initiatives.initiatives
    results: list[CheckResult] = []

    n = len(items)
    results.append(
        CheckResult(
            "initiative_count",
            INITIATIVE_MIN <= n <= INITIATIVE_MAX,
            "warn",
            f"{n} initiative(s) (want {INITIATIVE_MIN}–{INITIATIVE_MAX})",
        )
    )

    invented = sorted(
        {link for init in items for link in init.links if link and link not in ctx.activity_links}
    )
    results.append(
        CheckResult(
            "initiative_faithful_links",
            not invented,
            "error",
            f"invented link(s): {', '.join(invented)}" if invented else "",
        )
    )

    bad_refs = sorted(
        {
            init.thread_ref.id
            for init in items
            if init.thread_ref and init.thread_ref.id not in ctx.thread_ids
        }
    )
    results.append(
        CheckResult(
            "valid_thread_ref",
            not bad_refs,
            "error",
            f"unknown thread_ref id(s): {', '.join(bad_refs)}" if bad_refs else "",
        )
    )

    return results


def _section_initiative(text: str, initiatives: Initiatives):
    """The initiative a devlog section describes, matched by the proof-of-work URL
    it cites (exact link first, then owner/repo). Mirrors the engine's section↔
    initiative join so the check attributes a section to the right thread. Returns
    ``None`` when it can't be attributed (then the section is left unflagged)."""
    urls = _extract_urls(text)
    for init in initiatives.initiatives:
        if urls & set(init.links):
            return init
    repos = {m.group(1) for u in urls for m in [_GITHUB_REPO_RE.search(u)] if m}
    for init in initiatives.initiatives:
        init_repos = {m.group(1) for u in init.links for m in [_GITHUB_REPO_RE.search(u)] if m}
        if repos & init_repos:
            return init
    return None


def check_continuity(
    content: Content, initiatives: Initiatives, ctx: CheckContext
) -> list[CheckResult]:
    """Advisory: a devlog section that continues a thread already written about in
    a past published entry must not frame it as brand new. Each `##` section is
    joined to its initiative by the proof-of-work link it cites; if that
    initiative's ``thread_ref`` is in ``ctx.prior_thread_ids`` and the section uses
    first-appearance phrasing, that is a continuity slip (warn, not a hard gate —
    altitude/framing is a judgement call). Inert when there is no prior coverage
    (e.g. a fresh instance, or the eval runner with no published history)."""
    if not ctx.prior_thread_ids:
        return [CheckResult("continuity_not_reset", True, "warn", "")]

    devlog = content.devlog
    heads = list(_SECTION_RE.finditer(devlog))
    spans: list[tuple[str, str]] = []
    if heads:
        for i, m in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(devlog)
            spans.append((m.group(1).strip(), devlog[m.end() : end]))
    else:
        spans = [("", devlog)]

    offenders: list[str] = []
    for heading, text in spans:
        init = _section_initiative(text, initiatives)
        if init is None or init.thread_ref is None:
            continue
        if init.thread_ref.id not in ctx.prior_thread_ids:
            continue
        hit = _NOVELTY_RE.search(text)
        if hit:
            where = f'"{heading}"' if heading else "the entry"
            offenders.append(
                f"{where} frames thread {init.thread_ref.id} as new ({hit.group(0)!r})"
            )

    return [
        CheckResult(
            "continuity_not_reset",
            not offenders,
            "warn",
            "; ".join(offenders),
        )
    ]


def failures(results: list[CheckResult], severity: Severity | None = None) -> list[CheckResult]:
    """The failed results, optionally filtered to one severity."""
    return [r for r in results if not r.passed and (severity is None or r.severity == severity)]
