"""Build an output folder in the post-DSYNC-57 layout, for delivery tests.

The rostering CSVs go in ``csv/`` and are zipped into ``acsv_<district>_<date>.zip`` at the
top level; the standalone feeds go at the top level. This is the shape ``DataLoader.save_all``
commits, built without pandas so SFTP / Convert tests can stay file-light.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.etl import output_layout

DEFAULT_DISTRICT = "sd40myedbc"
DEFAULT_DATE = date(2026, 11, 6)


def zip_name(district: str = DEFAULT_DISTRICT, for_date: date = DEFAULT_DATE) -> str:
    """The zip name :func:`build_output_tree` writes for these arguments."""
    return output_layout.acsv_zip_name(district, for_date)


def build_output_tree(
    output_dir: Path,
    entities: list[str] | tuple[str, ...],
    *,
    district: str = DEFAULT_DISTRICT,
    for_date: date = DEFAULT_DATE,
    body: str = "col\nv\n",
) -> Path | None:
    """Write each entity's CSV at its layout path, then the zip. Returns the zip path (or None)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rostering: list[Path] = []
    for entity in entities:
        filename = f"{entity}.csv"
        path = output_layout.csv_path(output_dir, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        if not output_layout.is_standalone(filename):
            rostering.append(path)
    if not rostering:
        return None
    zip_path = output_dir / zip_name(district, for_date)
    output_layout.write_acsv_zip(zip_path, rostering)
    return zip_path
