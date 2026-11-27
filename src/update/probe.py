"""``--update-health-probe``: can this build start? (plan 0055 U3, DSYNC-99)

On a machine-wide install the SYSTEM updater decides a new version's health, not the app (the app's accounts cannot
write the updater's state). After an apply it runs ``current\\DistrictSync.exe --update-health-probe`` under supervision
(``myb_desktop_updater.run_health_probe``): this module imports the whole module graph the nightly and the window
need (the ETL, delivery, the scheduler, Flet and the shell, without opening a window) and exits 0. An import that fails
exits non-zero, a hang is killed at the time limit, and two failures restore the kept copy.

Dispatched in ``main``'s module-top block, before the start guard. It touches no profile, log, pin, handshake folder or
keyring, starts no child process, and writes only one word to stdout.
"""

from __future__ import annotations

import importlib
from typing import Final

#: What a working build must be able to import. The nightly: the pipeline and delivery; the window: Flet and the shell.
PROBE_MODULES: Final = (
    "src.etl.pipeline",
    "src.sftp.uploader",
    "src.scheduler.windows",
    "src.update.check",
    "flet",
    "src.ui_flet.shell",
    "src.ui_flet.launcher",
)


def run_probe() -> int:
    """Import every module the app needs to start; 0 when all of them import."""
    for name in PROBE_MODULES:
        importlib.import_module(name)
    print("ok")
    return 0
