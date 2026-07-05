from __future__ import annotations

import re
from typing import Any

import yaml

_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Split a document into its YAML front matter (a dict) and the body.

    Returns ``({}, text)`` unchanged when there is no front-matter block.
    """
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    front = yaml.safe_load(match.group(1))
    if not isinstance(front, dict):
        return {}, text
    return front, match.group(2)


def dump(front: dict[str, Any], body: str) -> str:
    """Serialize front matter + body back into a document."""
    rendered = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{rendered}\n---\n\n{body.lstrip()}"
