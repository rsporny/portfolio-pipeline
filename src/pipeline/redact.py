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
