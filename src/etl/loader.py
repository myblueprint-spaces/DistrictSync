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

            logger.info(
                f"Committed {len(outputs)} output file(s) to {self.output_path.resolve()}"
            )
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
            missing_cols = [c for c in field_order if c not in df.columns]
            if missing_cols:
                raise ValueError(
                    f"Cannot write {entity_name}.csv — "
                    f"columns missing from output: {missing_cols}"
                )
            output_file = directory / f"{entity_name}.csv"
            df[field_order].to_csv(output_file, index=False, encoding="utf-8-sig")
            label = "Staged" if staging else "Saved"
            logger.info(f"{label} {entity_name}.csv ({len(df)} rows) → {output_file}")
        except Exception as ex:
            logger.error(f"Failed to write {entity_name}.csv: {ex}")
            raise
