"""The ONE list of ``--hidden-import`` modules a ``flet pack`` of DistrictSync needs (DSYNC-60).

The one-folder Windows job in ``flet-pack.yml`` reads this list. The long-standing one-file
pack step still spells its imports inline (the owner's priority is that the job building the
exe districts use today is not touched while the installer is being built), so
``tests/test_flet_pack_args_parity.py`` ties the two together: every module here appears in
that step and every module that step names appears here. When the one-file step is next
edited, make it read this script too and delete the parity test's inline half.

Usage (workflow, bash)::

    flet pack src/main.py ... $(python scripts/flet_pack_args.py windows)

prints ``--hidden-import <module>`` pairs separated by spaces, one OS at a time. Standard
library only: the workflow runs it before anything is installed beyond the pinned stack.
"""

from __future__ import annotations

import sys

# Every OS. Order is the order the one-file step lists them.
COMMON: tuple[str, ...] = (
    "flet",
    "flet_desktop",
    "src.ui_flet.launcher",
    "src.ui_flet.shell",
    "src.ui_flet.nav",
    "src.ui_flet.tokens",
    "src.ui_flet.theme",
    "tkinter",
    "pandas",
    "pydantic",
    "pydantic_core",
    "yaml",
    "logging.config",
    "src.etl.transformers.registry",
    "src.etl.transformers.context",
    "src.etl.transformers.base",
    "src.etl.transformers.students",
    "src.etl.transformers.staff",
    "src.etl.transformers.family",
    "src.etl.transformers.classes",
    "src.etl.transformers.enrollments",
    "src.etl.transformers.blended",
    "src.etl.transformers.course_info",
    "src.etl.transformers.student_courses",
    "src.etl.transformers.student_attendance",
    "src.config.app_config",
    "src.config.loader",
    "src.utils.paths",
    "src.utils.validators",
    "src.utils.logger",
    "src.utils.version",
    "src._version",
    "src.scheduler.linux",  # imported on every OS (scheduler/__init__ imports both adapters)
)

_PER_OS: dict[str, tuple[str, ...]] = {
    "windows": (
        "src.scheduler.windows",
        "keyring.backends.Windows",
        "src.scheduler.task_com",
        "src.scheduler.elevated_apply",
        "win32com",
        "win32com.client",
        "pythoncom",
        "win32timezone",
    ),
    "macos": ("keyring.backends.macOS",),
    "linux": ("keyring.backends.SecretService", "keyring.backends.libsecret"),
}


# Extra modules only the INSTALLED (one-folder, Velopack) Windows flavour needs. Deliberately NOT
# in COMMON/_PER_OS: `all_modules()` is what the one-file step's text must contain, and the
# portable build must not carry the updater runtime (plan 0054 S2).
INSTALLED_EXTRA: tuple[str, ...] = (
    "src._build_flavor",
    "src.update",
    "src.update.startup",
    "velopack",
)

# The e2e test build also carries the stamped feed target (scripts/updater_e2e.py only; no release path).
E2E_EXTRA: tuple[str, ...] = ("src._e2e_target",)


def hidden_imports(os_name: str) -> tuple[str, ...]:
    """The modules for ``os_name`` (``windows`` / ``macos`` / ``linux``). Raises on anything else."""
    if os_name not in _PER_OS:
        raise ValueError(f"unknown OS {os_name!r}; expected one of {sorted(_PER_OS)}")
    # Linux and macOS also bundle the POSIX scheduler adapter, which COMMON already carries;
    # Windows bundles its own adapter on top of it.
    return (*COMMON, *_PER_OS[os_name])


def all_modules() -> frozenset[str]:
    """Every module named for any OS (what the one-file step's text must also contain)."""
    return frozenset(COMMON).union(*_PER_OS.values())


def as_flags(os_name: str, *, installed: bool = False) -> str:
    modules = hidden_imports(os_name)
    if installed:
        if os_name != "windows":
            raise ValueError("the installed flavour exists for windows only")
        modules = (*modules, *INSTALLED_EXTRA)
    return " ".join(f"--hidden-import {m}" for m in modules)


def main(argv: list[str]) -> int:
    installed = argv[1:] == ["--installed"]
    if len(argv) != (2 if installed else 1):
        print("usage: flet_pack_args.py windows|macos|linux [--installed]", file=sys.stderr)
        return 2
    try:
        print(as_flags(argv[0], installed=installed))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
