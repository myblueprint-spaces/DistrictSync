"""Assert a PyInstaller build carries the named modules (DSYNC-67).

Pure-Python modules live inside the executable's compressed archive, not as files in the folder, so a ``find`` for them is
blind: this reads the archive's table of contents. A module the app imports by NAME at run time (the shared updater packages)
is collected only if the build asked for it as a hidden import, and its absence would otherwise be silent.

Usage: ``python scripts/ci_assert_frozen_modules.py <exe> <module> [<module> ...]``; exit 1 naming any that are missing.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path


def frozen_module_names(exe: Path) -> set[str]:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(exe))
    return set(archive.open_embedded_archive("PYZ.pyz").toc)


def missing_modules(present: Iterable[str], wanted: Iterable[str]) -> list[str]:
    have = set(present)
    return [m for m in wanted if m not in have]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    exe, wanted = Path(argv[0]), argv[1:]
    names = frozen_module_names(exe)
    gone = missing_modules(names, wanted)
    print(f"{len(names)} modules in {exe.name}; wanted {len(wanted)}")
    for module in gone:
        print(f"FAIL: {module} is not in the build")
    return 1 if gone else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
