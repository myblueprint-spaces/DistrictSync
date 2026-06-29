"""Application version — single source of truth.

Wraps ``importlib.metadata.version("districtsync")`` so callers (the UI
shells, future surfaces) read the installed package version without
re-implementing the lookup + fallback. When the package isn't installed
(running straight from a source checkout that was never ``pip install``-ed)
the lookup raises ``PackageNotFoundError`` and we report ``"dev"``.

NOTE: ``src/main.py:196-199`` still inlines the same lookup for the CLI's
``--version`` flag; DRY-ing it onto this helper is a tracked ROADMAP
follow-up (deferred to keep PLAT-1's CLI branch untouched).
"""

from __future__ import annotations

import importlib.metadata

_PACKAGE_NAME = "districtsync"
_DEV_FALLBACK = "dev"


def app_version() -> str:
    """Return the installed DistrictSync version, or ``"dev"`` if not packaged."""
    try:
        return importlib.metadata.version(_PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return _DEV_FALLBACK
