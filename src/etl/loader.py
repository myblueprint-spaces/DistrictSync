import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from src.utils.helpers import ensure_directory

logger = logging.getLogger(__name__)


class DataLoader:
    """Saves transformed DataFrames as CSV files in the output directory.

    The primary write path is ``save_all()``, which commits all entities
    atomically: all files are staged in a temporary directory first, then
    moved into the output directory only after every file writes without
    error.  If anything fails, the temporary directory is deleted and the
    existing output is left untouched.
    """

    # CSVs are written UTF-8 **with BOM** (``utf-8-sig``) so districts can open
    # them in Excel without mojibake. The exception: feeds consumed by a strict
    # machine parser that treats the BOM as part of the (case-sensitive) first
    # header. SpacesEDU's standalone StudentAttendance import is one — a BOM
    # turns ``School Number`` into ``﻿School Number``, so the file is
    # rejected ("Unexpected file" + cascading "Invalid date format"). These
    # entities are written as plain UTF-8 (no BOM).
    _NO_BOM_ENTITIES: frozenset[str] = frozenset({"StudentAttendance"})

    def __init__(self, output_path: Optional[str] = None):
        if output_path:
            self.output_path = Path(output_path)
        else:
            self.output_path = Path("data/output")
        ensure_directory(self.output_path)
        logger.info(f"Output directory set to: {self.output_path.resolve()}")

    # ------------------------------------------------------------------
    # Primary (transactional) write path
    # ------------------------------------------------------------------

    def save_all(
        self,
        outputs: dict[str, pd.DataFrame],
        field_orders: dict[str, list[str]],
    ) -> None:
        """Write all entities atomically — all succeed or none are committed.

        Files are staged under a hidden ``<output_dir>/.tmp_<timestamp>/``
        directory first.  On success every file is moved into
        ``output_path/``.  On any failure the staging directory is removed
        and the existing output files are left untouched.

        Args:
            outputs: Mapping of entity name → transformed DataFrame.
            field_orders: Mapping of entity name → ordered column list.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_dir = self.output_path / f".tmp_{timestamp}"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            for entity_name, df in outputs.items():
                field_order = field_orders.get(entity_name, list(df.columns))
                self._write_csv(df, entity_name, field_order, tmp_dir, staging=True)

            # Commit: move each staged file into the real output directory
            for tmp_file in tmp_dir.iterdir():
                dest = self.output_path / tmp_file.name
                shutil.move(str(tmp_file), str(dest))

            logger.info(f"Committed {len(outputs)} output file(s) to {self.output_path.resolve()}")
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        finally:
            # Guard against partial move leaving tmp_dir behind
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Low-level write (kept public for UI / ad-hoc use)
    # ------------------------------------------------------------------

    def save_to_csv(self, df: pd.DataFrame, entity_name: str, field_order: list[str]) -> None:
        """Write a single DataFrame directly to the output directory.

        Prefer ``save_all()`` for pipeline runs (transactional).  This
        method is retained for the Streamlit UI and testing.
        """
        self._write_csv(df, entity_name, field_order, self.output_path, staging=False)

    # ------------------------------------------------------------------
    # Column selection + validation (single source of truth)
    # ------------------------------------------------------------------

    @staticmethod
    def select_ordered(df: pd.DataFrame, field_order: list[str], entity_name: str) -> pd.DataFrame:
        """Return ``df`` restricted to ``field_order`` (exact contract columns,
        in order), failing **loud** if any ordered column is absent.

        This is the ONE place column selection + the missing-column check live,
        so every write path — the disk/SFTP write (``_write_csv``) and the UI
        download/zip path (``02_Convert.create_zip`` + the per-CSV buttons) —
        raises the SAME ``ValueError`` instead of a raw ``KeyError`` from
        ``df[field_order]``. A ``field_order`` comes from ``field_map`` keys,
        which are not guaranteed to materialize as frame columns (the documented
        ``student_courses.py`` partial-transform debt), so this guard is reachable
        on the download path too — it must surface cleanly, not as a traceback.
        """
        missing_cols = [c for c in field_order if c not in df.columns]
        if missing_cols:
            raise ValueError(f"Cannot write {entity_name}.csv — columns missing from output: {missing_cols}")
        # ``.loc[:, list]`` selects the columns in order as a DataFrame (a list key
        # to ``df[...]`` does too at runtime, but the typed overload is ambiguous).
        return df.loc[:, field_order]

    # ------------------------------------------------------------------
    # Encoding policy (single source of truth for the BOM rule)
    # ------------------------------------------------------------------

    @classmethod
    def csv_encoding(cls, entity_name: str) -> str:
        """Return the CSV encoding for ``entity_name`` — the ONE place the BOM
        rule lives. ``utf-8-sig`` (BOM, Excel-friendly) by default; plain
        ``utf-8`` (no BOM) for ``_NO_BOM_ENTITIES`` whose strict downstream
        parser rejects a BOM-prefixed first header. Both the disk write path
        (``_write_csv``) and the UI download/zip path call this, so the two
        never diverge again (the StudentAttendance-BOM class of bug)."""
        return "utf-8" if entity_name in cls._NO_BOM_ENTITIES else "utf-8-sig"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_csv(
        self,
        df: pd.DataFrame,
        entity_name: str,
        field_order: list[str],
        directory: Path,
        *,
        staging: bool,
    ) -> None:
        try:
            ordered = self.select_ordered(df, field_order, entity_name)
            output_file = directory / f"{entity_name}.csv"
            encoding = self.csv_encoding(entity_name)
            ordered.to_csv(output_file, index=False, encoding=encoding)
            label = "Staged" if staging else "Saved"
            logger.info(f"{label} {entity_name}.csv ({len(df)} rows) → {output_file}")
        except Exception as ex:
            logger.error(f"Failed to write {entity_name}.csv: {ex}")
            raise
