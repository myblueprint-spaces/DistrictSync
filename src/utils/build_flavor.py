"""Which kind of build this is: ``"installed"`` or ``"portable"`` (plan 0054 S2, DSYNC-61).

``build_flavor()`` is the ONE lookup, in the shape of ``app_version()`` (``src/utils/version.py``):

1. ``src/_build_flavor.py`` — a tiny module stamped at build time (``flet-pack.yml``'s one-folder
   Windows job writes ``flavor = 'installed'``; no other job writes it). It is git-ignored: it
   exists only inside a build.
2. Anything else — an unbuilt source checkout, the one-file exe, the macOS and Linux binaries,
   a stamp whose value is not one of the two names — is ``"portable"``.

A third value, ``"e2e"``, exists for ONE purpose: the unsigned test build the updater's end-to-end proof installs
(``scripts/updater_e2e.py``). It behaves as the installed flavour and additionally trusts a loopback feed and a test key
stamped beside it (``src/update/feed.py``). Only that script's build stamps it; the release build and the one-folder CI job
never do, and tests pin that no release path mentions it.

The default is ``"portable"`` on purpose. The installed flavour is the only one that starts the
Velopack runtime and (in later slices) checks for updates, so an unreadable or unexpected stamp
must fall to the flavour that does neither. Never the other way round.
"""

from __future__ import annotations

from typing import Final

INSTALLED: Final = "installed"
PORTABLE: Final = "portable"
E2E: Final = "e2e"
_FLAVORS: Final = (INSTALLED, PORTABLE, E2E)


def build_flavor() -> str:
    """Return ``"installed"`` when this build was stamped as the installed flavour, else ``"portable"``."""
    try:
        from src._build_flavor import flavor  # stamped at build time; git-ignored
    except ImportError:
        return PORTABLE
    return flavor if flavor in _FLAVORS else PORTABLE


def is_installed_build() -> bool:
    """True for the installed flavour AND the e2e test build (which is the installed flavour plus a test feed)."""
    return build_flavor() in (INSTALLED, E2E)


def is_e2e_build() -> bool:
    return build_flavor() == E2E
