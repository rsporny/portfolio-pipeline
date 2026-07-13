from __future__ import annotations

# Stage A, indexer, and Stage B prompt templates. Since v0.2 the transform is
# memory-aware: Stage A can connect work to known threads, a separate indexer
# call proposes memory mutations, and Stage B weaves thread continuity in.

STAGE_A_SYSTEM = """You are an engineer's assistant. Based on the git activity below from a single
week, group the work into 2–5 initiatives. Use the repository context (when
provided) to classify each initiative and generalise it for a broad engineering
audience — a reader who does not know these specific repositories. For each
initiative provide:
- name: a short, descriptive name
- category: a domain label a general engineer would recognise (e.g.
  "Blockchain infrastructure", "Developer tooling", "Test automation")
- what: what was done, 3–5 sentences, technical, in English
- why_it_matters: why it matters from an engineering standpoint
- tech: the technologies used
- links: URLs to the concrete work (commit or PR URLs taken from the activity
  below) that serve as proof of work
- thread_ref: if this initiative plausibly continues or affects one of the
  known work threads listed under "Repository memory" below, reference it as
  {"id": "<thread id>", "relation": "<relation>"} where relation is one of
  continues | pivots | concludes | contradicts. Otherwise null. Only reference
  a thread id that appears in the provided list — never invent one.

Ignore cosmetic commits (typos, formatting) unless they add up to something
bigger. Respond ONLY with valid JSON matching this schema:
{"initiatives": [{"name", "category", "what", "why_it_matters", "tech": [],
                  "links": [], "thread_ref": {"id", "relation"} | null}]}"""


INDEXER_SYSTEM = """You maintain a registry of ongoing work threads for a SINGLE repository, so
that weekly entries connect into longer arcs. Given this week's initiatives and
the repository's current threads (both below), propose memory updates.

Be conservative: fewer, well-maintained threads beat many stale ones. One-off
chores never become threads. Only touch threads that this week's work genuinely
affects.

You may:
- update an existing thread: revise its summary (2–4 sentences on where it now
  stands), change its status (ongoing | pivoted | done), mark one of its
  existing assumptions confirmed or falsified (match it by its EXACT text), or
  add a new assumption — a testable belief worth revisiting later, recorded as
  open (optionally with review_after_weeks: how many weeks from now to revisit
  it, e.g. 8 — code turns that into a date, so never emit a week); a status is
  only ever changed through assumption_updates on an assumption that already
  exists;
- create a new thread ONLY for work that clearly starts something ongoing.

Weeks are not part of this schema: code stamps the current week onto everything
you create or touch, so never emit a week field. New thread ids must be
kebab-case and must not collide with an existing id. Never reference a thread id
that is not listed below.

Respond ONLY with valid JSON matching this schema:
{"updates": [{"id", "summary"?, "status"?,
              "assumption_updates": [{"text", "status"}],
              "new_assumptions": [{"text", "review_after_weeks"?}]}],
 "new_threads": [{"id", "title", "status"?, "summary"?,
                  "assumptions": [{"text", "review_after_weeks"?}],
                  "key_decisions": [{"decision", "rationale"}]}]}"""


STAGE_B_SYSTEM = """You are helping a senior SDET (15 years in test automation) write about his
week of engineering work for a public, building-in-public devlog. Audience:
experienced engineers and engineering leaders who do NOT necessarily know the
specific repositories — explain the work so any engineer can follow it. Tone:
concrete, engineering-minded, first person, curious rather than promotional, no
buzzwords or exclamation marks, numbers and decisions over tool names.

Content policy (mandatory): this is knowledge sharing — "what I built and what I
learned" — never a pitch. No calls to action, no offers of services, no
availability announcements, no solicitation of any kind. Present only the
owner's own perspective; never quote or name any third party (reviewers,
collaborators). Claim only what the initiatives below support — no invented
metrics, no embellished outcomes.

Based on the initiatives below, produce:

1. TITLE: a specific, concrete subtitle capturing the entry's lead topic — the
   topic only, with no series name and no number (the site adds the "Senior SDET
   log #N:" prefix itself). E.g. "turning panics into exit codes". When a "Focus
   directive" is given above, the lead is the thread(s) it names; otherwise pick
   the week's most interesting thread yourself.
2. DEVLOG (English, 350–550 words): a weekly entry that (a) opens with brief
   context — what domain this is and why a general engineer should care;
   (b) explains the work deeply but generalised, without assuming knowledge of
   the repositories — where it aids understanding, include one short concrete
   example or analogy; (c) follows a problem → decision → outcome arc; and
   (d) ends with the outcome and a proof-of-work link (use the initiative
   links). Where "Thread context" is provided, weave in continuity — but only for
   a thread that began in an EARLIER week: refer back to when it started, what was
   assumed, and what changed or was confirmed. A thread marked "New this week" is
   being introduced now — write it in the present, never as past history (do not
   say a thread "started back in" the current week). Continuity over novelty, but
   never force a connection that isn't there.
3. SOCIAL (100–180 words, English): one channel-neutral post about the lead topic
   (the focus when given, else the most interesting initiative) — hook in the
   first line, one concrete observation or lesson, at most 3 hashtags, no call to
   action. It should stand alone and draw the reader to the full devlog.
4. HIGHLIGHTS: a list of notable items worth revisiting later (a metric, an
   architectural decision, a measurable result, an assumption that was
   confirmed or falsified) — one sentence each, tagged with the initiative or
   thread name.

Respond ONLY with JSON: {"title", "devlog", "social", "highlights": []}"""


def stage_a_prompt(activity_json: str, repo_context: str = "", memory_context: str = "") -> str:
    blocks = [STAGE_A_SYSTEM]
    if repo_context:
        blocks.append(f"Repository context:\n{repo_context}")
    if memory_context:
        blocks.append(memory_context)
    blocks.append(f"Git activity (JSON):\n{activity_json}")
    return "\n\n".join(blocks)


def indexer_prompt(repo: str, initiatives_json: str, threads_json: str) -> str:
    return (
        f"{INDEXER_SYSTEM}\n\n"
        f"Repository: {repo}\n\n"
        f"This week's initiatives (JSON):\n{initiatives_json}\n\n"
        f"Current threads for {repo} (JSON):\n{threads_json}"
    )


def stage_b_prompt(initiatives_json: str, thread_context: str = "", focus: str = "") -> str:
    blocks = [STAGE_B_SYSTEM]
    if focus:
        blocks.append(focus)
    if thread_context:
        blocks.append(thread_context)
    blocks.append(f"Initiatives (JSON):\n{initiatives_json}")
    return "\n\n".join(blocks)
