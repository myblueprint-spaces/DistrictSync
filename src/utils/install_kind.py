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

#: Rights on the FOLDER ABOVE the root that would let a holder swap the root out: delete a child, change the parent's
#: DACL or owner, or everything.
PARENT_SWAP_MASK: Final = 0x40 | 0x40000 | 0x80000 | 0x10000000

_ACCESS_ALLOWED_ACE_TYPE: Final = 0
_ACCESS_DENIED_ACE_TYPE: Final = 1
_INHERIT_ONLY_ACE: Final = 0x08


class InstallKind(StrEnum):
    PORTABLE = "portable"
    PER_USER = "per_user"
    MACHINE = "machine"


def _dacl_entries(path: Path) -> tuple[tuple[int, int, int, str], ...]:
    """``((ace type, ace flags, access mask, SID string), ...)`` for ``path``. A seam for tests; raises ``OSError``."""
    from src.utils import paths

    return paths.read_dacl_entries(path)


def _owner_sid(path: Path) -> str:
    """The owner SID string of ``path``. A seam for tests; raises ``OSError``."""
    from src.utils import paths

    return paths._read_dir_security(path)[0]


def writable_by_non_admin(
    path: Path, *, mask_of_concern: int = WRITE_CLASS_MASK, count_inherit_only: bool = False
) -> bool:
    """Whether anyone outside :data:`TRUSTED_WRITERS` can change ``path``: its OWNER, or an ALLOW ace with a right of concern.

    * The owner can always rewrite the DACL (an implicit WRITE_DAC), so an untrusted owner is writing, whatever the DACL says
      (a user who created the folder before an administrator installed into it).
    * INHERIT_ONLY aces are ignored: they apply to children created later (``CREATOR OWNER`` on Program Files), unless
      ``count_inherit_only`` (a folder whose LATER files must be admin-only too, such as the SYSTEM updater's state).
    * A DENY ace never makes a folder writable.
    * Any other ace type (object, callback, conditional) is doubt, and doubt is writing: this reader cannot evaluate it.
    Raises ``OSError`` when the owner or the DACL cannot be read.
    """
    if _owner_sid(path) not in TRUSTED_WRITERS:
        return True
    for ace_type, flags, mask, sid in _dacl_entries(path):
        if (flags & _INHERIT_ONLY_ACE and not count_inherit_only) or ace_type == _ACCESS_DENIED_ACE_TYPE:
            continue
        if ace_type != _ACCESS_ALLOWED_ACE_TYPE:
            return True
        if sid not in TRUSTED_WRITERS and mask & mask_of_concern:
            return True
    return False


def is_machine_wide_root(root: Path) -> bool:
    """Whether ``root`` (a Velopack install root) is a machine-wide install: the MSI marker AND no non-admin writer.

    Checks the root and ``current`` (owner and DACL), and the folder above the root for a right that would let someone
    swap the root out (:data:`PARENT_SWAP_MASK`). Out of scope until machine setup hardens the install (plan 0055 M2): an
    explicit ace on an individual FILE under ``current``. Fails toward ``False``: anything unreadable is not machine-wide.
    """
    if not (root / MSI_MARKER).is_file():
        return False
    try:
        if any(writable_by_non_admin(folder) for folder in (root, root / "current")):
            return False
        return not writable_by_non_admin(root.parent, mask_of_concern=PARENT_SWAP_MASK)
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
