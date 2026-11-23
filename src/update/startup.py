"""The Velopack start-up call (plan 0054 S2, DSYNC-61).

``run_velopack_startup()`` runs the shared package's ``run_startup_hook`` (Velopack's ``App().run()`` with the installer's
own auto-apply OFF) at the top of a normal start, and only
when this process is the installed flavour *running from a Velopack install*. Why each guard:

* **Flavour.** The portable build must never start Velopack. Outside an install ``App().run()``
  prints ``VelopackApp: Error loading manager/locator`` to the console (S0 finding 4), and that
  line would reach every CLI user of the one-file exe. The check is the build stamp, not a
  guess at the environment.
* **Install layout.** A Velopack install is ``<root>\\current\\DistrictSync.exe`` with
  ``<root>\\Update.exe`` beside ``current``. The one-folder CI artifact, unzipped and run
  directly, is the installed flavour but is not installed; with no ``Update.exe`` there is
  nothing for the runtime to find, so it is skipped and the console stays clean.
* **Apply at start is OFF**, done inside the package's hook. An update is applied only by the
  package's hand-off after the process exits (S5); a package that is merely waiting in the install
  folder must never apply itself at the next start (plan 0054 R3, G-097).

Hooks: Velopack starts the app with ``--veloapp-install``, ``--veloapp-updated`` and
``--veloapp-obsolete`` and expects ``run()`` to handle them and exit. ``src.main`` calls this
before any other work for exactly that reason, but AFTER the three dispatch-first modes
(``--elevated-apply``, ``--schedule-check``, ``--diagnose``), which must not start it (R17).

The package's hook module is imported by NAME through ``importlib`` so PyInstaller's static analysis does not
collect it into the portable build; the one-folder job names it (and ``velopack``) as explicit hidden imports.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

from src.utils.build_flavor import is_installed_build

_HOOK_MODULE = "myb_desktop_updater.velopack_installer"  # a variable, not a literal: see the module docstring
_UPDATE_EXE = "Update.exe"
#: Velopack starts the app with ``--veloapp-install`` / ``-updated`` / ``-obsolete`` / ``-uninstall``.
VELOPACK_HOOK_PREFIX = "--veloapp-"

logger = logging.getLogger(__name__)


def install_root(executable: str | None = None) -> Path | None:
    """The Velopack install root for ``executable`` (default: this process), else ``None``.

    The root is the folder holding ``Update.exe``, one level above the ``current`` folder the
    app runs from.
    """
    exe = Path(sys.executable if executable is None else executable)
    root = exe.parent.parent
    return root if (root / _UPDATE_EXE).is_file() else None


def run_velopack_startup() -> bool:
    """Start the Velopack runtime when, and only when, this is an installed build under an install.

    The call itself is the shared package's ``run_startup_hook``: it runs ``App().run()`` with the installer's own
    auto-apply switched OFF (left on, a package waiting in the install folder is applied at the next start, even one
    nobody asked to apply: plan 0054 G-097). Returns ``True`` if the hook was called (it returns normally for an ordinary
    start; for a Velopack hook argument it handles it and exits the process), ``False`` if it was skipped.
    """
    if not is_installed_build():
        return False
    if install_root() is None:
        return False
    # Plan 0055 M1 (spike S0b #3): on a MACHINE-wide install an unelevated App().run() prints "Failed to open directory
    # for writing ... Access is denied" to the console on every start, which every service-account nightly and IT command
    # line would show. With apply-at-start off it does nothing an ordinary start needs, so it runs there for Velopack's
    # own hook arguments only.
    from src.utils.install_kind import InstallKind, install_kind  # lazy: install_kind imports this module

    # The hook test comes FIRST: a hook never reads the install's DACL (which would import src.utils.paths).
    if not is_velopack_hook(sys.argv[1:]) and install_kind() is InstallKind.MACHINE:
        return False
    try:
        hook = importlib.import_module(_HOOK_MODULE).run_startup_hook
    except ImportError:
        # An installed build that cannot import its own updater is a packaging fault. Say so loudly (error level, with
        # the cause) and carry on: a nightly conversion must not stop because the updater is missing. The
        # installed-build CI smoke fails on this, so it cannot ship quietly.
        logger.error("installed build could not import the updater; updates are unavailable", exc_info=True)
        return False
    hook()
    return True


def is_velopack_hook(args: list[str]) -> bool:
    """Whether this invocation is one of Velopack's own hooks (install, update, obsolete, uninstall)."""
    return any(arg.startswith(VELOPACK_HOOK_PREFIX) for arg in args)


def run_velopack_hook() -> int:
    """Plan 0055 M1: the module-top dispatch for a Velopack hook, BEFORE the start guard, the profile or any log.

    Windows Installer runs the hooks from the MSI, and a Group Policy or Intune install runs them as SYSTEM: anything that
    resolves a profile here would create one under ``systemprofile``. So the hook does exactly one thing, Velopack's
    ``App().run()`` (which handles the hook and exits the process), and touches nothing of DistrictSync's own: no profile,
    no settings, no secret, no task, no registry. It runs for EVERY install kind (the per-user ``Setup.exe`` runs hooks too),
    so a hook no longer passes through the start guard; a hook that raises returns 1 here instead of the guard's handling,
    so the installer is never left waiting on an error dialog. Returns 0 if nothing handled it: a portable build given a hook
    argument exits 0 quietly (it used to be an argparse usage error, exit 2).
    """
    try:
        run_velopack_startup()
    except Exception:  # noqa: BLE001 - an installer hook must end, never hang on an error dialog
        return 1
    return 0
