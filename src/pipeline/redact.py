from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

MASK = "[REDACTED]"


def redact(text: str, phrases: list[str], mask: str = MASK) -> tuple[str, int]:
    """Mask every (case-insensitive) occurrence of each forbidden phrase.

    Returns the redacted text and the total number of maskings. Each phrase's
    hit count is logged (the phrases come from config, not from secrets, so
    logging them is safe and satisfies the "log what was redacted" constraint).
    """
    total = 0
    for phrase in phrases:
        if not phrase:
            continue
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        text, count = pattern.subn(mask, text)
        if count:
            logger.info("Redacted %d occurrence(s) of forbidden phrase %r", count, phrase)
            total += count
    return text, total


def redact_names(
    text: str, names: list[str], placeholder: str = "[collaborator]"
) -> tuple[str, int]:
    """Mask third-party GitHub logins / display names (and their ``@mention``
    form) wherever they appear, case-insensitively, replacing each with a role
    placeholder. The caller supplies the name set — everyone but ``github_user``
    who participated in the activity (SPEC Module 3, hard constraints 3 & 5).

    Names are masked longest-first so a display name is redacted before a bare
    login that is a substring of it. Word boundaries keep a short login from
    matching inside an unrelated word. Returns the text and the total maskings.
    """
    total = 0
    # Longest first: a full "Ada Lovelace" is masked before the login "ada".
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        pattern = re.compile(r"@?\b" + re.escape(name) + r"\b", re.IGNORECASE)
        text, count = pattern.subn(placeholder, text)
        if count:
            logger.info("Redacted %d occurrence(s) of third-party name", count)
            total += count
    return text, total
