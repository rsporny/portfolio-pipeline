from datetime import UTC, datetime

from pipeline.checks import (
    CheckContext,
    activity_links,
    build_context,
    check_content,
    check_continuity,
    check_initiatives,
    failures,
)
from pipeline.models import (
    Activity,
    Commit,
    Content,
    Initiative,
    Initiatives,
    Issue,
    LinkedIssue,
    PullRequest,
    RepoActivity,
    ThreadRef,
)

LINK = "https://github.com/o/r/pull/5"
COMMIT_URL = "https://github.com/o/r/commit/abc123"


def _result(results, name):
    return next(r for r in results if r.name == name)


def _words(n: int) -> str:
    return " ".join(["word"] * n)


def _content(**overrides) -> Content:
    base = dict(
        title="a bare subtitle",
        devlog=f"{_words(450)} see {LINK}",
        social=_words(120),
        highlights=["Shipped the collector — collector-thread"],
    )
    base.update(overrides)
    return Content(**base)


def _ctx(**overrides) -> CheckContext:
    base = dict(activity_links={LINK, COMMIT_URL})
    base.update(overrides)
    return CheckContext(**base)


# --- activity_links ---------------------------------------------------------


def test_activity_links_collects_all_urls():
    activity = Activity(
        generated_at=datetime.now(UTC),
        since=datetime.now(UTC),
        until=datetime.now(UTC),
        week="2026-W27",
        repos=[
            RepoActivity(
                repo="o/r",
                commits=[Commit(sha="a", date=datetime.now(UTC), message="m", url=COMMIT_URL)],
                pull_requests=[
                    PullRequest(
                        number=5,
                        title="t",
                        url=LINK,
                        linked_issues=[
                            LinkedIssue(number=9, url="https://github.com/o/r/issues/9")
                        ],
                    )
                ],
                issues=[Issue(number=3, title="i", url="https://github.com/o/r/issues/3")],
            )
        ],
    )
    assert activity_links(activity) == {
        COMMIT_URL,
        LINK,
        "https://github.com/o/r/issues/9",
        "https://github.com/o/r/issues/3",
    }


def test_build_context_from_activity_and_config():
    activity = Activity(
        generated_at=datetime.now(UTC),
        since=datetime.now(UTC),
        until=datetime.now(UTC),
        week="2026-W27",
        repos=[
            RepoActivity(
                repo="o/r",
                pull_requests=[PullRequest(number=5, title="t", url=LINK)],
            )
        ],
    )
    ctx = build_context(
        activity, forbidden_phrases=["secret"], placeholder="[collaborator]", thread_ids={"t1"}
    )
    assert ctx.activity_links == {LINK}
    assert ctx.forbidden_phrases == ["secret"]
    assert ctx.thread_ids == frozenset({"t1"})


# --- check_content: passing baseline ----------------------------------------


def test_content_all_pass():
    results = check_content(_content(), _ctx())
    assert failures(results) == []


# --- check_content: each check's failure path -------------------------------


def test_devlog_word_count_out_of_range_warns():
    results = check_content(_content(devlog=f"short {LINK}"), _ctx())
    r = _result(results, "devlog_word_count")
    assert not r.passed and r.severity == "warn"


def test_social_word_count_out_of_range_warns():
    results = check_content(_content(social=_words(5)), _ctx())
    assert not _result(results, "social_word_count").passed


def test_too_many_hashtags_warns():
    results = check_content(_content(social=f"{_words(120)} #a #b #c #d"), _ctx())
    r = _result(results, "social_hashtags")
    assert not r.passed and r.severity == "warn"


def test_exclamation_marks_warn():
    results = check_content(_content(social=f"{_words(119)} amazing!"), _ctx())
    assert not _result(results, "no_exclamation").passed


def test_solicitation_is_error():
    results = check_content(_content(social=f"{_words(118)} please contact me here"), _ctx())
    r = _result(results, "no_solicitation")
    assert not r.passed and r.severity == "error"


def test_collaborator_placeholder_leak_is_error():
    results = check_content(_content(devlog=f"{_words(449)} [collaborator] said {LINK}"), _ctx())
    r = _result(results, "no_collaborator_leak")
    assert not r.passed and r.severity == "error"


def test_at_mention_leak_is_error():
    results = check_content(_content(social=f"{_words(118)} thanks @octocat"), _ctx())
    assert not _result(results, "no_collaborator_leak").passed


def test_email_does_not_trip_mention_check():
    results = check_content(
        _content(social=f"{_words(117)} reachable at me at foo dot com"), _ctx()
    )
    assert _result(results, "no_collaborator_leak").passed


def test_forbidden_phrase_leak_is_error():
    ctx = _ctx(forbidden_phrases=["Project Nimbus"])
    results = check_content(_content(social=f"{_words(117)} about Project Nimbus"), ctx)
    r = _result(results, "no_forbidden_phrase")
    assert not r.passed and r.severity == "error"


def test_invented_link_is_error():
    bad = "https://evil.example.com/made-up"
    results = check_content(_content(devlog=f"{_words(449)} see {bad}"), _ctx())
    r = _result(results, "faithful_links")
    assert not r.passed and r.severity == "error"


def test_url_with_trailing_period_still_faithful():
    results = check_content(_content(devlog=f"{_words(449)} see {LINK}."), _ctx())
    assert _result(results, "faithful_links").passed


def test_missing_proof_of_work_warns():
    results = check_content(_content(devlog=_words(450)), _ctx())
    r = _result(results, "proof_of_work_present")
    assert not r.passed and r.severity == "warn"


# --- check_initiatives ------------------------------------------------------


def _initiative(**overrides) -> Initiative:
    base = dict(name="Collector", what="Built it.", why_it_matters="Reliable data.", links=[LINK])
    base.update(overrides)
    return Initiative(**base)


def test_initiatives_all_pass():
    inits = Initiatives(
        initiatives=[_initiative(), _initiative(name="Indexer", links=[COMMIT_URL])]
    )
    results = check_initiatives(inits, _ctx())
    assert failures(results) == []


def test_initiative_count_out_of_range_warns():
    inits = Initiatives(initiatives=[_initiative()])
    r = _result(check_initiatives(inits, _ctx()), "initiative_count")
    assert not r.passed and r.severity == "warn"


def test_initiative_invented_link_is_error():
    inits = Initiatives(
        initiatives=[_initiative(), _initiative(links=["https://evil.example.com/x"])]
    )
    r = _result(check_initiatives(inits, _ctx()), "initiative_faithful_links")
    assert not r.passed and r.severity == "error"


def test_unknown_thread_ref_is_error():
    inits = Initiatives(
        initiatives=[
            _initiative(),
            _initiative(thread_ref=ThreadRef(id="ghost", relation="continues")),
        ]
    )
    ctx = _ctx(thread_ids=frozenset({"known"}))
    r = _result(check_initiatives(inits, ctx), "valid_thread_ref")
    assert not r.passed and r.severity == "error"


def test_known_thread_ref_passes():
    inits = Initiatives(
        initiatives=[
            _initiative(),
            _initiative(thread_ref=ThreadRef(id="known", relation="continues")),
        ]
    )
    ctx = _ctx(thread_ids=frozenset({"known"}))
    assert _result(check_initiatives(inits, ctx), "valid_thread_ref").passed


def test_failures_filters_by_severity():
    results = check_content(_content(social=f"{_words(3)} contact me"), _ctx())
    assert {r.name for r in failures(results, "error")} == {"no_solicitation"}
    assert "social_word_count" in {r.name for r in failures(results, "warn")}


# --- check_continuity (presented-as-new advisory) ---------------------------


def _inits_with_ref(thread_id="collector", link=LINK):
    return Initiatives(
        initiatives=[
            _initiative(thread_ref=ThreadRef(id=thread_id, relation="continues"), links=[link])
        ]
    )


def test_continuity_flags_reset_of_covered_thread():
    """A section that continues a previously-published thread but frames it as new
    is flagged (warn). The section is joined to its thread by its proof-of-work
    link, so ``prior_thread_ids`` carries the covered thread's id."""
    content = _content(devlog=f"## The collector\n\nAlso new here. {_words(20)} {LINK}")
    ctx = _ctx(prior_thread_ids=frozenset({"collector"}))
    r = _result(check_continuity(content, _inits_with_ref(), ctx), "continuity_not_reset")
    assert not r.passed and r.severity == "warn"
    assert "collector" in r.detail and "new here" in r.detail


def test_continuity_passes_when_covered_thread_not_framed_as_new():
    content = _content(
        devlog=f"## The collector\n\nContinuing last week's work. {_words(20)} {LINK}"
    )
    ctx = _ctx(prior_thread_ids=frozenset({"collector"}))
    assert _result(check_continuity(content, _inits_with_ref(), ctx), "continuity_not_reset").passed


def test_continuity_passes_for_genuinely_new_thread():
    """Novelty phrasing is fine for a thread with no prior published coverage."""
    content = _content(devlog=f"## The collector\n\nThis is the first time. {_words(20)} {LINK}")
    ctx = _ctx(prior_thread_ids=frozenset())  # nothing covered
    assert _result(check_continuity(content, _inits_with_ref(), ctx), "continuity_not_reset").passed


def test_continuity_only_flags_the_covered_thread_section():
    """Two sections, only one continues a covered thread — the other's novelty
    phrasing (a legitimately new thread) is left alone."""
    other = "https://github.com/o/r/pull/6"
    inits = Initiatives(
        initiatives=[
            _initiative(
                name="Collector",
                thread_ref=ThreadRef(id="collector", relation="continues"),
                links=[LINK],
            ),
            _initiative(
                name="Indexer",
                thread_ref=ThreadRef(id="indexer", relation="continues"),
                links=[other],
            ),
        ]
    )
    devlog = (
        f"## The collector\n\nAlso new here. {_words(10)} {LINK}\n\n"
        f"## The indexer\n\nBrand new this week. {_words(10)} {other}"
    )
    ctx = _ctx(activity_links={LINK, other}, prior_thread_ids=frozenset({"collector"}))
    r = _result(check_continuity(_content(devlog=devlog), inits, ctx), "continuity_not_reset")
    assert not r.passed
    assert "collector" in r.detail and "indexer" not in r.detail


def test_continuity_inert_without_prior_coverage():
    # No coverage set → always a passing row (keeps the scorecard column stable).
    r = _result(check_continuity(_content(), _inits_with_ref(), _ctx()), "continuity_not_reset")
    assert r.passed and r.detail == ""
