"""What kind of copy is running: the ONE reader (plan 0055 M1, DSYNC-94).

Three kinds:

* ``PORTABLE``: the single-file exe, a source checkout, or a one-folder build that is not installed.
* ``PER_USER``: the per-user ``DistrictSync-Setup.exe`` install (``%LOCALAPPDATA%\\myBlueprint.DistrictSync``), or any
  Velopack layout whose folders a non-administrator can write.
* ``MACHINE``: the machine-wide MSI (plan 0055), installed by an administrator so that only Administrators, SYSTEM and
  TrustedInstaller can change the program.

Why the decision is an ACL check and not a path: the MSI can be installed to any folder (``VELOPACK_INSTALLDIR``), and the
property that matters is who can WRITE the program, not where it is. A ``D:\\Apps`` install inherits ``D:\\``'s default
(Authenticated Users: Modify), so it is ``PER_USER`` here until machine setup hardens it (plan 0055 M2). Every doubt
answers ``PER_USER``, the safer kind: a ``MACHINE`` copy is trusted to be run elevated and, later, to be updated by SYSTEM.

The layout part (``Update.exe`` beside ``current``) is ``src.update.startup.install_root``; this module only adds the MSI
marker and the ACL. It imports nothing heavy at module level: the ACL reader in ``src.utils.paths`` is imported lazily.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Final

from src.update.startup import install_root
from src.utils.build_flavor import is_installed_build

#: The file Velopack's MSI writes beside ``Update.exe`` (plan 0055 spike S0b #2). Absent in a ``Setup.exe`` install.
MSI_MARKER: Final = ".msi-installed"

#: The only accounts that may hold a write-class right on a machine-wide install: SYSTEM, Administrators, TrustedInstaller.
TRUSTED_WRITERS: Final = frozenset(
    {
        "S-1-5-18",
        "S-1-5-32-544",
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
    }
)

#: Rights that let a holder change what runs: write/append data, write EA, delete child, write attributes, DELETE,
#: WRITE_DAC, WRITE_OWNER, GENERIC_WRITE, GENERIC_ALL. Read and execute rights are not in the mask.
WRITE_CLASS_MASK: Final = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000 | 0x40000000 | 0x10000000

_ACCESS_ALLOWED_ACE_TYPE: Final = 0
_INHERIT_ONLY_ACE: Final = 0x08


class InstallKind(StrEnum):
    PORTABLE = "portable"
    PER_USER = "per_user"
    MACHINE = "machine"


def _dacl_entries(path: Path) -> tuple[tuple[int, int, int, str], ...]:
    """``((ace type, ace flags, access mask, SID string), ...)`` for ``path``. A seam for tests; raises ``OSError``."""
    from src.utils import paths

    return paths.read_dacl_entries(path)


def writable_by_non_admin(path: Path) -> bool:
    """Whether any account outside :data:`TRUSTED_WRITERS` holds a write-class ALLOW right on ``path`` itself.

    INHERIT_ONLY aces are ignored: they apply to children created later (``CREATOR OWNER`` on Program Files), not to this
    folder. A DENY ace never makes a folder writable. Raises ``OSError`` when the DACL cannot be read.
    """
    for ace_type, flags, mask, sid in _dacl_entries(path):
        if ace_type != _ACCESS_ALLOWED_ACE_TYPE or flags & _INHERIT_ONLY_ACE:
            continue
        if sid not in TRUSTED_WRITERS and mask & WRITE_CLASS_MASK:
            return True
    return False


def is_machine_wide_root(root: Path) -> bool:
    """Whether ``root`` (a Velopack install root) is a machine-wide install: the MSI marker AND no non-admin writer.

    Checks the root and ``current`` (the program). Fails toward ``False``: an unreadable DACL is not machine-wide.
    """
    if not (root / MSI_MARKER).is_file():
        return False
    try:
        return not any(writable_by_non_admin(folder) for folder in (root, root / "current"))
    except OSError:
        return False


def install_kind(executable: str | None = None) -> InstallKind:
    """The kind of copy ``executable`` (default: this process) is. Total: never raises."""
    if not is_installed_build():
        return InstallKind.PORTABLE
    root = install_root(executable)
    if root is None:
        return InstallKind.PORTABLE
    return InstallKind.MACHINE if is_machine_wide_root(root) else InstallKind.PER_USER
