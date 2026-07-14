"""Application version — single source of truth.

``app_version()`` is the ONE version lookup for the whole app: the Flet UI
surfaces AND the CLI ``--version`` flag (``src/main.py``) both call it.

Resolution order:
1. ``src/_version.py`` — a tiny module stamped from the git tag at build time
   (``flet-pack.yml`` writes ``version = '<tag>'`` before PyInstaller runs, and
   bundles it via ``--hidden-import src._version``). This is the ONLY source a
   frozen one-file exe can read: a PyInstaller build never ships the package's
   installed metadata, so importlib always misses and would report ``"dev"``.
   ``src/_version.py`` is git-ignored — it exists only inside a build.
2. ``importlib.metadata.version("districtsync")`` — an editable / ``pip install``
   from a source checkout that WAS installed.
3. ``"dev"`` — an unbuilt, uninstalled source checkout.
"""

from __future__ import annotations

import importlib.metadata

_PACKAGE_NAME = "districtsync"
_DEV_FALLBACK = "dev"


def app_version() -> str:
    """Return the DistrictSync version: build-stamped tag, else installed
    package metadata, else ``"dev"`` for an unbuilt source checkout."""
    try:
        from src._version import version  # stamped at build time; git-ignored

        return version
    except ImportError:
        pass
    try:
        return importlib.metadata.version(_PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return _DEV_FALLBACK
