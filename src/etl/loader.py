import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from src.utils.helpers import ensure_directory

logger = logging.getLogger(__name__)


class DataLoader:
    """Saves transformed DataFrames as CSV files in the output directory.

    The primary write path is ``save_all()``, whose commit is **backup-and-
    restore atomic**: every entity is staged in a hidden ``.tmp_<ts>/`` first,
    then committed one file at a time — each existing target is moved aside into
    ``.bak_<ts>/`` and the staged file promoted into place with ``os.replace``
    (an atomic same-filesystem overwrite).  If any commit step fails, the
    already-committed files are rolled back (new files removed, prior files
    restored from ``.bak_<ts>/``) so the output directory is left **exactly as
    it was before the call** — never a torn mix of new and stale files.
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
        directory first.  Commit then promotes the staged files one at a time
        via :meth:`_commit_staged`, which moves each existing target aside into
        ``<output_dir>/.bak_<timestamp>/`` and rolls back every promoted file on
        any failure (so the output directory is left exactly as before the call).
        The ``finally`` block removes both the staging and backup directories —
        and runs **only after** rollback has finished restoring originals from
        the backup directory (rollback lives inside :meth:`_commit_staged`'s
        ``except`` and re-raises, so ``finally`` cannot delete a backup that is
        still needed for restore).

        Args:
            outputs: Mapping of entity name → transformed DataFrame.
            field_orders: Mapping of entity name → ordered column list.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_dir = self.output_path / f".tmp_{timestamp}"
        backup_dir = self.output_path / f".bak_{timestamp}"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            for entity_name, df in outputs.items():
                field_order = field_orders.get(entity_name, list(df.columns))
                self._write_csv(df, entity_name, field_order, tmp_dir, staging=True)

            # Commit: materialise a sorted (deterministic) list of staged files
            # up front, then promote them atomically with backup-and-restore.
            staged_files = sorted(tmp_dir.iterdir())
            self._commit_staged(staged_files, backup_dir)

            logger.info(f"Committed {len(outputs)} output file(s) to {self.output_path.resolve()}")
        finally:
            # Safe to run unconditionally: on failure _commit_staged has already
            # restored originals out of backup_dir before re-raising, so removing
            # both dirs here never destroys a backup that is still needed.
            shutil.rmtree(tmp_dir, ignore_errors=True)
            shutil.rmtree(backup_dir, ignore_errors=True)

    def _commit_staged(self, staged_files: list[Path], backup_dir: Path) -> None:
        """Promote each staged file into ``output_path`` with backup-and-restore
        atomicity — all promoted or, on any failure, all rolled back.

        Per file (in ``staged_files`` order): if ``dest`` already exists, move it
        aside into ``backup_dir`` via ``os.replace`` (created lazily on first
        need); record ``(dest, backup-or-None)`` in ``applied`` **before**
        promoting (so the in-flight file is covered by rollback); then promote
        the staged file into ``dest`` via ``os.replace`` (an atomic same-fs
        overwrite — no half-written destination).

        On any exception, roll back in reverse ``applied`` order — per file in
        its own ``try/except OSError`` so one restore failure logs an ERROR and
        does **not** abort the rest or mask the cause: remove the new ``dest``
        (``unlink(missing_ok=True)``) then, if a backup exists, restore it via
        ``os.replace(backup, dest)``.  A new entity (no prior file → ``backup``
        is ``None``) rolls back to *absent* (unlink only, never
        ``os.replace(None, dest)``).  After restoring, **re-raise the original**.

        Invariant: rollback completes here, inside this ``except``, **before**
        the caller's ``finally`` removes ``backup_dir`` — so backups are never
        deleted while still needed for restore.  ``os.replace`` requires the same
        filesystem; guaranteed because ``staged_files``, ``backup_dir`` and each
        ``dest`` are all children of ``output_path``.
        """
        applied: list[tuple[Path, Optional[Path]]] = []
        try:
            for tmp_file in staged_files:
                dest = self.output_path / tmp_file.name
                backup: Optional[Path] = None
                if dest.exists():
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    backup = backup_dir / tmp_file.name
                    os.replace(dest, backup)  # move existing target aside (atomic)
                applied.append((dest, backup))  # record BEFORE promote
                os.replace(tmp_file, dest)  # promote staged file (atomic overwrite)
        except Exception:
            for dest, backup in reversed(applied):
                try:
                    dest.unlink(missing_ok=True)
                    if backup is not None:
                        os.replace(backup, dest)  # restore original
                except OSError as restore_err:
                    logger.error(f"Rollback failed to restore {dest}: {restore_err}")
            raise

    # ------------------------------------------------------------------
    # Low-level write (kept public for UI / ad-hoc use)
    # ------------------------------------------------------------------

    def save_to_csv(self, df: pd.DataFrame, entity_name: str, field_order: list[str]) -> None:
        """Write a single DataFrame directly to the output directory.

        Prefer ``save_all()`` for pipeline runs (transactional).  This
        method is retained for the UI and testing.
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
    # Stale-output detection + archival (NON-DESTRUCTIVE)
    # ------------------------------------------------------------------

    def detect_stale_outputs(self, emitted: set[str]) -> list[str]:
        """Return sorted entity CSV filenames in the output dir NOT produced this run.

        Lists every ``<EntityName>.csv`` present in ``output_path`` whose stem is a
        recognized entity name (the transformer registry — the single source of
        the known-entity set) but is **not** in ``emitted`` (the entities this run
        wrote). Such a file is possibly stale — left by a prior or different run
        whose entity is no longer produced — and would ship unrefreshed in the
        next SFTP zip.

        **DETECTION ONLY — deletes nothing.** Pure, registry-keyed detection,
        independently testable; the destructive decision lives in
        :meth:`archive_stale_outputs`, which uses this. A broad registry known-set
        is safe HERE precisely because nothing is removed (the caller archives,
        never deletes — see :meth:`archive_stale_outputs`).

        Args:
            emitted: Entity names this run produced (e.g. ``set(outputs)``).

        Returns:
            Sorted list of stale ``<EntityName>.csv`` filenames (empty if none).
        """
        from src.etl.transformers.registry import TRANSFORMER_REGISTRY

        known_entities = set(TRANSFORMER_REGISTRY)
        stale: list[str] = []
        for csv_path in self.output_path.glob("*.csv"):
            stem = csv_path.stem
            if stem in known_entities and stem not in emitted:
                stale.append(csv_path.name)
        return sorted(stale)

    def archive_stale_outputs(self, emitted: set[str]) -> list[str]:
        """Move stale entity CSVs aside into ``archive_<ts>/`` — **non-destructive**.

        Detects stale entity CSVs via :meth:`detect_stale_outputs` (output-dir
        ``<EntityName>.csv`` files this run did NOT produce). If none, returns
        ``[]`` and creates **no** archive directory. Otherwise lazily creates
        ``<output_dir>/archive_<timestamp>/`` and **moves** each stale file into
        it with ``os.replace`` (atomic same-filesystem rename — both the source
        and the archive subdir are children of ``output_path``).

        **Archives, never deletes — for cross-config safety.** Under ``_base``
        config inheritance the safe allow-list for a *delete* is unknowable
        (a registry/``mappings.keys()`` set would erase a DIFFERENT config's
        inherited-but-disabled entity CSV sharing this dir; an ``enabled_entities``
        set would miss a config-dropped entity AND wipe an enabled entity's
        last-good CSV on an empty-input run). Moving the file aside surfaces the
        stale entity without any data-loss path: the original bytes are preserved
        in the archive subdir, recoverable by hand.

        **Excluded from SFTP delivery.** The SFTP uploader globs
        ``output_dir.glob("*.csv")`` — **top-level only, non-recursive** — so a
        file inside the ``archive_<ts>/`` SUBfolder is automatically left out of
        the next zip (a stale CSV can no longer ship).

        **Best-effort — never fails the run.** The roster has already been
        committed and (if requested) delivered before this runs, so a per-file
        move hiccup is logged at ERROR and skipped rather than raised — an archive
        problem must not turn a successful run into a failure.

        Args:
            emitted: Entity names this run produced (e.g. ``set(outputs)``).

        Returns:
            Sorted list of stale ``<EntityName>.csv`` filenames actually moved into
            the archive subdir (empty if there was nothing stale).
        """
        stale = self.detect_stale_outputs(emitted)
        if not stale:
            return []

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_dir = self.output_path / f"archive_{timestamp}"
        archive_dir.mkdir(parents=True, exist_ok=True)

        archived: list[str] = []
        for name in stale:
            try:
                os.replace(self.output_path / name, archive_dir / name)
                archived.append(name)
            except OSError as ex:
                # Best-effort: an archive hiccup must not fail an already
                # committed + delivered run. Log loud and continue.
                logger.error(f"Failed to archive stale output {name}: {ex}")
        return archived

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
