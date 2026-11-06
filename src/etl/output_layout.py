"""Where each file a run produces lives in the output folder: the ONE layout rule (DSYNC-57).

After a conversion the output folder's TOP LEVEL holds exactly what an admin uploads:

* ``acsv_<district>_<YYYY-MM-DD>.zip``: the Advanced CSV rostering bundle (Students, Staff,
  Family, Classes, Enrollments; whichever this run produced), every CSV at the zip ROOT
  (Product-Integration-Validation ACSV-025). The SpacesEDU importer takes no single CSV
  and no multi-select when Advanced CSV is chosen, so the zip IS the manual-import file.
* the standalone feeds in :data:`STANDALONE_CSV_FILENAMES`, each as its own file.

The loose rostering CSVs live one level down in :data:`ROSTERING_SUBDIR` so the admin can
still open and check them without mistaking them for the upload set.

Imports nothing heavy (no pandas, no paramiko) on purpose: the loader, the SFTP uploader
and the Flet Convert screen all read this, and the uploader sits on the app's boot path.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable
from datetime import date
from pathlib import Path

#: The output-folder subfolder holding the loose rostering CSVs (the zip's sources).
ROSTERING_SUBDIR = "csv"

#: The feeds SpacesEDU ingests as individual files, never as members of the Advanced CSV
#: zip. Product-Integration-Validation ACSV-026 (FAIL): delivered inside the rostering zip,
#: CourseInfo.csv / StudentCourses.csv import NOTHING and no error is raised (owner check
#: against the live importer, 2026-08-27); the importer routes a CSV to attendance and a zip
#: to roster, so StudentAttendance.csv inside the zip is never read as attendance either.
#: ``tests/test_output_layout.py`` pins this set against the loader's entity→filename rule.
STANDALONE_CSV_FILENAMES: frozenset[str] = frozenset(
    {
        "StudentAttendance.csv",
        "CourseInfo.csv",
        "StudentCourses.csv",
    }
)

#: Every Advanced CSV zip this app writes starts with this, the key the stale-zip sweep uses.
ACSV_ZIP_PREFIX = "acsv_"


def district_slug(sis_type: str) -> str:
    """Short filename-safe identifier for a district, derived from its sis_type.

    - sd40myedbc  -> sd40
    - myedbc      -> myedbc   (base config, kept as-is)
    - myBlueprint+ -> myBlueprint  (sanitized for filenames)
    """
    stem = sis_type
    if stem != "myedbc" and stem.endswith("myedbc"):
        stem = stem[: -len("myedbc")]
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or "district"


def acsv_zip_name(sis_type: str, for_date: date | None = None) -> str:
    """The Advanced CSV zip's filename: ``acsv_<district>_<YYYY-MM-DD>.zip``.

    The same name is used on disk and on the SFTP server. The date is the BUILD date, so a
    second run on the same day replaces that day's zip. ``sis_type`` is required: no silent
    district (D9). A blank one is a caller bug and raises rather than minting an
    unattributable name.
    """
    if not (sis_type or "").strip():
        raise ValueError("An Advanced CSV zip needs a district (sis_type) to name it")
    when = (for_date or date.today()).isoformat()
    return f"{ACSV_ZIP_PREFIX}{district_slug(sis_type)}_{when}.zip"


def is_standalone(filename: str) -> bool:
    """Whether ``filename`` ships as its own file (True) or inside the rostering zip (False)."""
    return filename in STANDALONE_CSV_FILENAMES


def csv_relpath(filename: str) -> Path:
    """Where an entity CSV lives, relative to the output folder."""
    return Path(filename) if is_standalone(filename) else Path(ROSTERING_SUBDIR) / filename


def csv_path(output_dir: Path, filename: str) -> Path:
    """Where an entity CSV lives in ``output_dir``."""
    return output_dir / csv_relpath(filename)


def legacy_csv_path(output_dir: Path, filename: str) -> Path:
    """Where a rostering CSV lived before DSYNC-57 (the folder's top level).

    Read only as a fallback baseline (the anomaly check on the first run after an upgrade)
    and by the sweep that moves such files into ``archive_<ts>/``.
    """
    return output_dir / filename


def acsv_zips(output_dir: Path) -> list[Path]:
    """The ``acsv_*.zip`` files at the top level of ``output_dir``, sorted (TOTAL: [] on error).

    Top level only: an ``archive_<ts>/`` copy is never a candidate for delivery.
    """
    try:
        return sorted(p for p in output_dir.glob(f"{ACSV_ZIP_PREFIX}*.zip") if p.is_file())
    except OSError:
        return []


def zip_member_names(zip_path: Path) -> frozenset[str]:
    """The member names of a zip. Raises ``OSError``/``zipfile.BadZipFile`` on an unreadable one."""
    with zipfile.ZipFile(zip_path) as zf:
        return frozenset(zf.namelist())


def write_acsv_zip(zip_path: Path, csv_files: Iterable[Path]) -> None:
    """Write the Advanced CSV zip: each CSV at the ROOT (ACSV-025), in sorted order.

    Refuses a standalone feed (ACSV-026: inside the zip it silently imports nothing) and an
    empty set (an empty bundle is never a delivery).
    """
    files = sorted(csv_files, key=lambda p: p.name)
    if not files:
        raise ValueError("An Advanced CSV zip needs at least one rostering CSV")
    foreign = sorted(p.name for p in files if is_standalone(p.name))
    if foreign:
        raise ValueError(f"Standalone feeds must never go inside the Advanced CSV zip: {foreign}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for csv_file in files:
            zf.write(csv_file, csv_file.name)
