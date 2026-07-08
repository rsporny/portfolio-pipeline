from __future__ import annotations

# Stage A and Stage B prompt templates.

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

Ignore cosmetic commits (typos, formatting) unless they add up to something
bigger. Respond ONLY with valid JSON matching this schema:
{"initiatives": [{"name", "category", "what", "why_it_matters", "tech": [], "links": []}]}"""

STAGE_B_SYSTEM = """You are helping a senior SDET (15 years in test automation) write about his
week of engineering work for a public, building-in-public devlog. Audience:
experienced engineers and engineering leaders who do NOT necessarily know the
specific repositories — explain the work so any engineer can follow it. Tone:
concrete, engineering-minded, first person, curious rather than promotional, no
buzzwords or exclamation marks, numbers and decisions over tool names. This is
knowledge sharing — "what I built and what I learned" — never a pitch.

Based on the initiatives below, produce:

1. TITLE: a specific, concrete subtitle capturing the week's most interesting
   thread — the topic only, with no series name and no number (the site adds the
   "Senior SDET log #N:" prefix itself). E.g. "turning panics into exit codes".
2. DEVLOG (English, 350–550 words): a weekly entry that (a) opens with brief
   context — what domain this is and why a general engineer should care;
   (b) explains the work deeply but generalised, without assuming knowledge of
   the repositories — where it aids understanding, include one short concrete
   example or analogy; (c) follows a problem → decision → outcome arc; and
   (d) ends with the outcome and a proof-of-work link (use the initiative
   links).
3. SOCIAL (100–180 words, English): one channel-neutral post about the most
   interesting initiative — hook in the first line, one concrete observation or
   lesson, at most 3 hashtags, no call to action. It should stand alone and
   draw the reader to the full devlog.
4. HIGHLIGHTS: a list of notable items worth revisiting later (a metric, an
   architectural decision, a measurable result) — one sentence each, tagged
   with the initiative name.

Respond ONLY with JSON: {"title", "devlog", "social", "highlights": []}"""


def stage_a_prompt(activity_json: str, repo_context: str = "") -> str:
    context = f"Repository context:\n{repo_context}\n\n" if repo_context else ""
    return f"{STAGE_A_SYSTEM}\n\n{context}Git activity (JSON):\n{activity_json}"


def stage_b_prompt(initiatives_json: str) -> str:
    return f"{STAGE_B_SYSTEM}\n\nInitiatives (JSON):\n{initiatives_json}"
