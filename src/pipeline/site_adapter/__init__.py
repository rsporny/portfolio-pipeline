from __future__ import annotations

from .base import AdapterError, DevlogEntry, FileChange, RenderContext, SiteAdapter
from .sporny_pl import SpornyPlAdapter

# The registry of known site adapters, keyed by the name used in
# `output.adapter`. Add a fork's adapter here (and only here).
_ADAPTERS: dict[str, type[SiteAdapter]] = {
    SpornyPlAdapter.name: SpornyPlAdapter,
}


def get_adapter(name: str) -> SiteAdapter:
    """Resolve the site adapter named in config. Raises :class:`AdapterError`
    for an unknown name so misconfiguration fails loudly."""
    try:
        adapter_cls = _ADAPTERS[name]
    except KeyError:
        known = ", ".join(sorted(_ADAPTERS)) or "(none)"
        raise AdapterError(f"unknown site adapter {name!r}; known adapters: {known}") from None
    return adapter_cls()


__all__ = [
    "AdapterError",
    "DevlogEntry",
    "FileChange",
    "RenderContext",
    "SiteAdapter",
    "SpornyPlAdapter",
    "get_adapter",
]
