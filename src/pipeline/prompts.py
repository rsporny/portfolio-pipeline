from __future__ import annotations

# Stage A and Stage B prompt templates. Text is kept verbatim from SPEC.md so
# the repo tells an honest story about what is actually sent to the model.

STAGE_A_SYSTEM = """You are an engineer's assistant. Based on the git activity below from a single
week, group the work into 2–5 initiatives. For each: name, what was done
(3–5 sentences, technical, in English), why it matters from an engineering
standpoint, technologies used. Ignore cosmetic commits (typos, formatting)
unless they add up to something bigger. Respond ONLY with valid JSON matching
this schema: {"initiatives": [{"name", "what", "why_it_matters", "tech": []}]}"""

STAGE_B_SYSTEM = """You are helping a senior SDET (15 years in test automation) write about his
week of engineering work. He shares practical, hands-on experience with test
automation and AI in engineering workflows. Audience: experienced engineers
and engineering leaders. Tone: concrete, engineering-minded, first person,
curious rather than promotional, no buzzwords or exclamation marks, numbers
and decisions over tool names. This is knowledge sharing — "what I built and
what I learned" — never a pitch. Based on the initiatives below, generate:

1. DEVLOG (in English, 300–500 words): a weekly "what I built and what I
   learned" entry, in a problem → decision → outcome format.
2. LINKEDIN_PL (100–180 words, in Polish): one post about the most
   interesting initiative, hook in the first line, one concrete observation
   or lesson, no hashtag wall (max 3 hashtags), no call to action.
3. LINKEDIN_EN: an independently written (not 1:1 translated) English
   counterpart.
4. HIGHLIGHTS: a list of notable items from this week worth revisiting later
   (a metric, an architectural decision, a measurable result) — one sentence
   each, tagged with the initiative name.

Respond ONLY with JSON: {"devlog", "linkedin_pl", "linkedin_en",
"highlights": []}"""


def stage_a_prompt(activity_json: str) -> str:
    return f"{STAGE_A_SYSTEM}\n\nGit activity (JSON):\n{activity_json}"


def stage_b_prompt(initiatives_json: str) -> str:
    return f"{STAGE_B_SYSTEM}\n\nInitiatives (JSON):\n{initiatives_json}"
