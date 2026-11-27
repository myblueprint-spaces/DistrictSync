"""``--update-health-probe``: can this build start? (plan 0055 U3, DSYNC-99)

On a machine-wide install the SYSTEM updater decides a new version's health, not the app (the app's accounts cannot
write the updater's state). After an apply it runs ``current\\DistrictSync.exe --update-health-probe`` under supervision
(``myb_desktop_updater.run_health_probe``). This probe:

* imports every ``src`` module a start reaches (``src.main`` itself as a module, so its dispatch blocks do not run, the
  ETL, delivery, the scheduler, the update modules, Flet and the shell), without opening a window;
* checks two things a missing hidden import or data file would break and an import alone would not show: a platform
  keyring backend imports, and the bundled mapping configs are there.

It exits 0 when all of that holds, non-zero otherwise (an import error, or ``SystemExit(2)`` for a check), and a hang is
killed at the time limit. **What it does not prove**: that delivery logs in, that the Flet desktop client starts, or that
a normal account can open the window (it runs as SYSTEM); those are the lab's (T12) and the next nightly's evidence.

Dispatched in ``main``'s module-top block, before the start guard. It touches no profile, log, pin, handshake folder or
keyring store, starts no child process, and writes only one word to stdout.
"""

from __future__ import annotations

import importlib
import sys
from typing import Final

#: Every module a start reaches: ``src.main`` (its module-level imports, ``machine_setup`` included), the nightly's
#: pipeline, delivery and health, the window's Flet, shell, launcher and Home's update line.
PROBE_MODULES: Final = (
    "src.main",
    "src.etl.pipeline",
    "src.sftp.uploader",
    "src.scheduler.windows",
    "src.scheduler.machine_setup",
    "src.update.check",
    "src.update.health",
    "src.update.setting",
    "flet",
    "src.ui_flet.shell",
    "src.ui_flet.launcher",
)

#: The keyring backend a delivery night needs, per platform (the PyInstaller hidden imports in ``flet_pack_args.py``).
KEYRING_BACKENDS: Final = {"win32": "keyring.backends.Windows"}


def _checks() -> list[str]:
    """The failed checks' names (no path, no text from the system)."""
    failed: list[str] = []
    backend = KEYRING_BACKENDS.get(sys.platform)
    if backend is not None:
        try:
            importlib.import_module(backend)
        except Exception:  # noqa: BLE001 - a failed check, named
            failed.append("keyring_backend")
    from src.utils.paths import bundle_mappings_dir

    try:
        if not any(bundle_mappings_dir().glob("*_mapping.yaml")):
            failed.append("bundled_mappings")
    except OSError:
        failed.append("bundled_mappings")
    return failed


def run_probe() -> int:
    """Import every module a start needs and run the two checks; 0 when all hold."""
    for name in PROBE_MODULES:
        importlib.import_module(name)
    failed = _checks()
    if failed:
        print("failed: " + ",".join(failed))
        return 2
    print("ok")
    return 0
