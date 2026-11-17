"""DistrictSync's seat for the shared updater package ``myb_desktop_updater`` (plan 0054 S3, DSYNC-62).

The package is private and shared with the other myBlueprint Windows tool. It owns the signed-manifest
check, the host-locked fetcher, staging, the hand-off to Velopack, the health record and rollback.
DistrictSync owns only what is its own, and this module is the start of that list: the identity it
passes in.

``load_updater()`` imports the package, and only in the installed flavour. Why that guard matters:

* The portable build (the one-file exe, macOS, Linux) must never import it. It is not installed
  there, and ``src.main`` runs on every start.
* The package is imported by NAME through ``importlib`` (a variable, not a literal) so PyInstaller's
  static analysis does not try to collect it into a build that does not carry it. The one-folder job
  names it as an explicit hidden import in the slice that installs it into that build (S4).

An installed build whose package is missing is a packaging fault: ``ImportError`` propagates to the
caller, which logs it and carries on without updates (see ``src.update.startup`` for the same rule).

What is NOT here yet, and why: the feed host and the trusted public keys. Neither exists until the
owner's Azure steps are done (the host name is not chosen; the signing key is not created). Slice S4
adds them, with the host as a loopback test value only until then.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any, Final

from src.utils.build_flavor import is_installed_build

UPDATER_MODULE: Final = "myb_desktop_updater"  # a variable, not a literal: see the module docstring

#: Name in messages and the manifest's ``app`` field; must equal the manifest the release job signs.
APP_NAME: Final = "DistrictSync"
#: Makes ``DISTRICTSYNC_STATE_DIR`` and ``DISTRICTSYNC_UPDATES`` (the package's off switch variable).
ENV_PREFIX: Final = "DISTRICTSYNC"
#: Folder under ``%LOCALAPPDATA%`` the package falls back to for its state. DistrictSync passes an explicit
#: state directory inside its own profile (S4), so this is only the fallback.
STATE_FOLDER: Final = "DistrictSync"
#: The Velopack pack id (install folder ``%LOCALAPPDATA%\myBlueprint.DistrictSync``). Permanent once an
#: installer ships; it must never equal the profile folder name (plan 0054, section 3).
PACK_ID: Final = "myBlueprint.DistrictSync"
#: The updater's state folder inside the profile, and the package's health file in it. One spelling, here, because the
#: start guard (``src.update.entry``) needs them before the app's own heavy imports and must not import ``check``.
UPDATER_STATE_FOLDER: Final = "updater"
HEALTH_FILE: Final = "health.json"


def load_updater() -> ModuleType | None:
    """The shared package, or ``None`` in a portable build. Raises ``ImportError`` if an installed build lacks it."""
    if not is_installed_build():
        return None
    return importlib.import_module(UPDATER_MODULE)


def app_identity(updater: ModuleType | None = None) -> Any | None:
    """DistrictSync's ``AppIdentity``, or ``None`` when there is no package (portable build)."""
    module = load_updater() if updater is None else updater
    if module is None:
        return None
    return module.AppIdentity(APP_NAME, ENV_PREFIX, STATE_FOLDER)
