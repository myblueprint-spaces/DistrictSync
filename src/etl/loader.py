import logging
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from src.etl import output_layout
from src.utils.helpers import ensure_directory

logger = logging.getLogger(__name__)

# Aged-staging sweep threshold (see ``DataLoader._reconcile_output_dir``):
# ``.tmp_*`` dirs are pure, re-creatable staging — deletable once clearly
# abandoned. ``.bak_*`` (an interrupted run's pre-commit originals) and
# ``archive_*`` (stale-output / recovered data) are DATA and are never
# auto-deleted — a stranded ``.bak_*`` is MOVED into an archive instead.
_STALE_TMP_MAX_AGE_DAYS = 7

# A ``.bak_*`` younger than this may belong to a LIVE concurrent run's in-flight
# commit (scheduled run + manual Convert into the same folder) — moving it would
# break that run's rollback (restore-before-cleanup invariant). A real commit
# completes in seconds; one hour is a >1000x margin, so anything older is
# genuinely stranded by a hard-killed run.
_STALE_BAK_MIN_AGE_SECONDS = 3600

# The output-folder pre-flight's own scratch dir (see :func:`output_target_problem`).
# A DISTINCT prefix, deliberately: it must be invisible to every other output-dir sweep
# and glob — ``_reconcile_output_dir`` branches on ``.bak_``/``.tmp_`` only,
# ``detect_stale_outputs``/``archive_stale_outputs``/``output_layout.acsv_zips``/
# ``convert_output._on_disk_deliverables`` are all file-name-keyed — and it must be reapable by
# name so a stranded one cannot accumulate.
_PROBE_PREFIX = ".dsync_probe_"


def _reap_stale_probes(directory: Path) -> None:
    """Remove ``.dsync_probe_*`` dirs a previous pre-flight failed to clean up.

    BEST-EFFORT and TOTAL — it can never raise, because a failure to tidy an OLD
    leftover must not make a usable output folder report itself unusable. Every other
    leftover class in an output folder already has a reaper at a chosen age
    (:data:`_STALE_TMP_MAX_AGE_DAYS`, :data:`_STALE_BAK_MIN_AGE_SECONDS`); a probe dir
    left by a failed ``rmdir`` would have **none, at any age** — one stray dir per failed
    run, unbounded, on exactly the flaky network shares the pre-flight targets. Reaping
    the glob before creating a new probe is what makes the check self-healing.

    No age gate, unlike the ``.tmp_``/``.bak_`` sweeps — and the reason is NOT that the
    reap cannot touch a live probe. It can, and does: a CONCURRENT run's live probe
    (scheduled run + manual Convert into the same folder, the very hazard
    :data:`_STALE_BAK_MIN_AGE_SECONDS` exists for) matches this glob and IS removed.
    MEASURED, not assumed. That is harmless only for a specific reason: the owning call
    then fails its own ``rmdir`` with ``FileNotFoundError``, which
    :func:`output_target_problem` treats as already-clean — so no run is refused, no
    warning is logged, and the probe proved what it needed to before it vanished.

    **Therefore: anything that ever puts DATA in a probe dir must add an age gate FIRST.**
    :func:`output_target_problem` deliberately signposts widening the probe (it does not
    prove a FILE write today); doing that on top of this reap would mean a concurrent
    ``rmtree`` deleting a directory another process is writing into.
    """
    try:
        candidates = list(directory.glob(f"{_PROBE_PREFIX}*"))
    except OSError as exc:
        logger.warning(f"Could not scan the output folder for stale pre-flight probes: {exc}")
        return
    for entry in candidates:
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
        except OSError as exc:
            logger.warning(f"Could not remove stale pre-flight probe {entry.name}: {exc}")


def output_target_problem(path: str) -> Optional[str]:
    """Is this output folder usable? ``None`` when it is, else a **log-only** reason.

    The pre-flight both entry points run BEFORE any ETL work (``run_pipeline`` and the
    Convert screen's ``convert_job``), so an output-folder fault is never reported to an
    admin as an input-folder or mapping fault. It lives beside :class:`DataLoader` because
    it must stay in step with how the loader actually uses the directory —
    ``ensure_directory`` (``mkdir(parents=True, exist_ok=True)``), then ``.tmp_<ts>_<uid>/``
    staging (:meth:`save_all`) — a check written anywhere else could drift into a false
    green.

    **What it proves, precisely:** *subdirectory creation* in the output folder. It does
    NOT prove writing a file inside that staging dir, nor ``os.replace`` over an existing
    ``Students.csv`` held open in Excel. Those windows stay open by construction (a
    pre-check cannot see a lock taken later) and are covered by the callers' write-time
    ``OSError`` catch, not by a bigger probe.

    **One ``try``, not a ladder.** ``mkdir(parents=True, exist_ok=True)`` re-raises
    ``FileExistsError`` when the leaf exists and is not a directory, so a separate
    not-a-file step is subsumed; a ``resolve()`` failure is subsumed by the same
    ``except``. The distinct reason strings a ladder would buy are worthless for a value
    that is log-only and never parsed — the ``WinError`` is already self-describing.

    Returns:
        ``None`` when the folder is usable, else a free-text reason for the DIAGNOSTIC
        LOG only. It is never shown to an admin and never stored: the callers surface a
        bounded, zero-argument category instead (the privacy split — the reason can carry
        a path).
    """
    if not path or not path.strip():
        return "No output folder is configured."
    try:
        resolved = Path(path.strip()).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        _reap_stale_probes(resolved)
        probe = Path(tempfile.mkdtemp(prefix=_PROBE_PREFIX, dir=resolved))
    except (OSError, ValueError) as exc:
        # ValueError: a path the OS cannot even express (e.g. an embedded NUL) never
        # reaches a syscall — it is exactly as unusable as one that does and fails.
        return f"The output folder cannot be used ({type(exc).__name__}: {exc})"
    try:
        probe.rmdir()
    except FileNotFoundError:
        # Already gone — a concurrent run's `_reap_stale_probes` swept it (see that
        # function). Nothing to clean up and nothing to report: warning here would send a
        # support engineer after a benign race on a folder that just proved it works.
        pass
    except OSError as exc:
        # Our own scratch dir — a cleanup hiccup must not fail a folder that just proved
        # it works. The next call's reap removes it.
        logger.warning(f"Could not remove the output-folder pre-flight probe {probe.name}: {exc}")
    return None


class DataLoader:
    """Saves transformed DataFrames as CSV files in the output directory.

    The primary write path is ``save_all()``, whose commit is **backup-and-
    restore atomic**: every entity is staged in a hidden ``.tmp_<ts>_<uid>/``
    first (the ``uuid4`` suffix keeps same-second runs from sharing dirs), then
    committed one file at a time — each existing target is moved aside into
    ``.bak_<ts>_<uid>/`` and the staged file promoted into place with ``os.replace``
    (an atomic same-filesystem overwrite).  If any commit step fails, the
    already-committed files are rolled back (new files removed, prior files
    restored from ``.bak_<ts>_<uid>/``) so the output directory is left **exactly
    as it was before the call** — never a torn mix of new and stale files.
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
        *,
        zip_name: str,
    ) -> str | None:
        """Write all entities, plus the Advanced CSV zip, atomically — all or none committed.

        **Layout (DSYNC-57, :mod:`src.etl.output_layout`).** The rostering CSVs land in
        ``<output_dir>/csv/`` and are bundled, at the zip root, into ``zip_name`` at the
        folder's top level. The standalone feeds (attendance, the myBlueprint+ course
        files) land at the top level beside it. The zip is staged and committed in the
        SAME backup-and-restore transaction as the CSVs, so a failed run never leaves a
        zip that disagrees with the CSVs next to it.

        ``zip_name`` is required keyword-only (``output_layout.acsv_zip_name(sis_type)``):
        the caller owns the district, and there is no unnamed default.

        First reconciles interrupted-run leftovers (:meth:`_reconcile_output_dir`:
        a stranded ``.bak_*`` warns loudly and is archived; aged ``.tmp_*``
        staging is swept). Files are then staged under a hidden
        ``<output_dir>/.tmp_<timestamp>_<uid>/`` directory.  Commit promotes the
        staged files one at a time via :meth:`_commit_staged`, which moves each
        existing target aside into ``<output_dir>/.bak_<timestamp>_<uid>/`` and
        rolls back every promoted file on any failure (so the output directory is
        left exactly as before the call). The ``finally`` block removes both the
        staging and backup directories — and runs **only after** rollback has
        finished restoring originals from the backup directory (rollback lives
        inside :meth:`_commit_staged`'s ``except`` and re-raises, so ``finally``
        cannot delete a backup that is still needed for restore).

        The staging/backup names share a per-call ``uuid4`` suffix and are
        created with ``exist_ok=False``: two runs landing in the same second can
        never share — or ``rmtree`` — each other's dirs, and any residual name
        collision fails loud instead of interleaving commits.

        Args:
            outputs: Mapping of entity name → transformed DataFrame.
            field_orders: Mapping of entity name → ordered column list.
            zip_name: The Advanced CSV zip's filename.

        Returns:
            The zip's filename, or ``None`` when this run produced no rostering entity
            (an attendance-only or course-only run writes no zip).
        """
        self._reconcile_output_dir()

        stamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        tmp_dir = self.output_path / f".tmp_{stamp}"
        backup_dir = self.output_path / f".bak_{stamp}"
        tmp_dir.mkdir(parents=True, exist_ok=False)

        try:
            for entity_name, df in outputs.items():
                field_order = field_orders.get(entity_name, list(df.columns))
                self._write_csv(df, entity_name, field_order, tmp_dir, staging=True)

            rostering = sorted((tmp_dir / output_layout.ROSTERING_SUBDIR).glob("*.csv"))
            written_zip: str | None = None
            if rostering:
                output_layout.write_acsv_zip(tmp_dir / zip_name, rostering)
                written_zip = zip_name
                logger.info(f"Staged {zip_name} with {len(rostering)} rostering CSV(s)")

            # Commit: materialise a sorted (deterministic) list of staged files, as paths
            # relative to the staging dir, then promote them with backup-and-restore.
            staged = sorted(p.relative_to(tmp_dir) for p in tmp_dir.rglob("*") if p.is_file())
            self._commit_staged(tmp_dir, staged, backup_dir)

            logger.info(f"Committed {len(staged)} output file(s) to {self.output_path.resolve()}")
            return written_zip
        finally:
            # Safe to run unconditionally: on failure _commit_staged has already
            # restored originals out of backup_dir before re-raising, so removing
            # both dirs here never destroys a backup that is still needed.
            shutil.rmtree(tmp_dir, ignore_errors=True)
            shutil.rmtree(backup_dir, ignore_errors=True)

    def _reconcile_output_dir(self) -> None:
        """Reconcile leftovers a hard-killed previous run may have stranded.

        Runs at the START of :meth:`save_all`, before this run creates its own
        staging/backup dirs, so everything found here predates this call:

        * ``.bak_*`` — an interrupted run's pre-commit originals (DATA; an
          in-process failure restores and removes its backup dir, so one still
          on disk means the process died mid-commit). Only dirs older than
          :data:`_STALE_BAK_MIN_AGE_SECONDS` are touched — a younger one may be
          a LIVE concurrent run's in-flight backup, and moving it would break
          that run's rollback. Past the gate: warn loudly and MOVE each into
          ``archive_<ts>_recovered/`` — preserved for hand recovery and out
          of the way of future commits. Never deleted.
        * ``.tmp_*`` — pure staging (re-creatable scratch). Dirs older than
          :data:`_STALE_TMP_MAX_AGE_DAYS` days are deleted and logged; younger
          ones are left untouched (they may belong to a live concurrent run).
        * ``archive_*`` — data; never touched.

        Best-effort throughout: a reconcile hiccup logs at ERROR and is skipped —
        cleaning up an OLD run must never block THIS run's write.
        """
        try:
            entries = sorted(self.output_path.iterdir())
        except OSError as ex:
            logger.error(f"Could not scan output directory for interrupted-run leftovers: {ex}")
            return

        recovered_dir: Optional[Path] = None
        cutoff = time.time() - _STALE_TMP_MAX_AGE_DAYS * 86400
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name.startswith(".bak_"):
                try:
                    if entry.stat().st_mtime >= time.time() - _STALE_BAK_MIN_AGE_SECONDS:
                        continue  # may be a live concurrent run's in-flight backup
                except OSError:
                    continue
                logger.warning(
                    f"A previous run appears to have been interrupted mid-commit: leftover backup "
                    f"{entry.name} found in {self.output_path}. Moving it into an "
                    f"archive_*_recovered/ folder — nothing is deleted."
                )
                try:
                    if recovered_dir is None:
                        candidate = self.output_path / f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}_recovered"
                        candidate.mkdir(parents=True, exist_ok=False)
                        recovered_dir = candidate
                    os.replace(entry, recovered_dir / entry.name)
                except OSError as ex:
                    logger.error(f"Could not move leftover backup {entry.name} aside: {ex}")
            elif entry.name.startswith(".tmp_"):
                try:
                    if entry.stat().st_mtime < cutoff:
                        shutil.rmtree(entry)
                        logger.info(
                            f"Removed abandoned staging directory {entry.name} "
                            f"(older than {_STALE_TMP_MAX_AGE_DAYS} days; staging is re-creatable scratch)"
                        )
                except OSError as ex:
                    logger.error(f"Could not sweep abandoned staging directory {entry.name}: {ex}")

    def _commit_staged(self, tmp_dir: Path, staged: list[Path], backup_dir: Path) -> None:
        """Promote each staged file into ``output_path`` with backup-and-restore
        atomicity — all promoted or, on any failure, all rolled back.

        ``staged`` holds paths RELATIVE to ``tmp_dir``; each lands at the same relative
        path under ``output_path`` (so ``csv/Students.csv`` stays in ``csv/``), and its
        backup at the same relative path under ``backup_dir``. Parent folders are
        created as needed and are never removed by rollback (an empty ``csv/`` is
        harmless; deleting a folder an admin may have opened is not).

        Per file (in ``staged`` order): if ``dest`` already exists, move it
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
        filesystem; guaranteed because ``tmp_dir``, ``backup_dir`` and each
        ``dest`` are all under ``output_path``.
        """
        applied: list[tuple[Path, Optional[Path]]] = []
        try:
            for rel in staged:
                dest = self.output_path / rel
                backup: Optional[Path] = None
                if dest.exists():
                    if not backup_dir.exists():
                        # Lazily, once; exist_ok=False so a collision fails loud.
                        backup_dir.mkdir(parents=True, exist_ok=False)
                    backup = backup_dir / rel
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(dest, backup)  # move existing target aside (atomic)
                applied.append((dest, backup))  # record BEFORE promote
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp_dir / rel, dest)  # promote staged file (atomic overwrite)
        except Exception:  # noqa: BLE001 — re-raised; the rollback restores every original first, so it stays broad (failure-policy §11)
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
        so every write path raises the SAME ``ValueError`` instead of a raw
        ``KeyError`` from ``df[field_order]``. Both of today's consumers reach it
        through :meth:`_write_csv`: the transactional pipeline write
        (:meth:`save_all` — used by the CLI *and* the Flet Convert screen, which
        delegates to the same ``run_transform -> save_all`` path) and the direct
        single-entity write (:meth:`save_to_csv`, retained for ad-hoc/test use).
        The retired Streamlit ``02_Convert.create_zip`` download path this
        docstring used to name is gone — there is no longer a second write path
        with its own column selection, which is exactly the outcome this
        single-source guard was introduced to reach.

        A ``field_order`` comes from ``field_map`` keys, which are not guaranteed
        to materialize as frame columns (the documented ``student_courses.py``
        partial-transform debt), so this guard is genuinely reachable — it must
        surface cleanly, not as a traceback. The emitted column set and order it
        enforces are published in ``docs/developer/output-contract.md``.
        """
        missing_cols = [c for c in field_order if c not in df.columns]
        if missing_cols:
            raise ValueError(f"Cannot write {entity_name}.csv — columns missing from output: {missing_cols}")
        # ``.loc[:, list]`` selects the columns in order as a DataFrame (a list key
        # to ``df[...]`` does too at runtime, but the typed overload is ambiguous).
        return df.loc[:, field_order]

    # ------------------------------------------------------------------
    # Naming policy (single source of truth for entity → filename)
    # ------------------------------------------------------------------

    @staticmethod
    def csv_filename(entity_name: str) -> str:
        """Return the output CSV filename for ``entity_name`` — the ONE spelling
        of the entity→filename rule.

        Every consumer of "what file does this entity live in?" routes through
        here: the write path (``_write_csv``), the stale-output detector
        (:meth:`detect_stale_outputs`) and the SFTP delivery manifest built by
        the pipeline / Convert callers (``SFTPUploader.upload_csvs(manifest=…)``).
        A second copy of ``f"{entity}.csv"`` anywhere would let the delivered set
        drift from the written set — the exact class of bug the manifest closes.
        """
        return f"{entity_name}.csv"

    @classmethod
    def output_filenames(cls, entity_names: Iterable[str]) -> set[str]:
        """The set of output CSV filenames for ``entity_names`` (via :meth:`csv_filename`).

        The delivery-manifest builder: ``DataLoader.output_filenames(outputs)`` turns
        the entities a run produced into exactly the files it vouches for.
        """
        return {cls.csv_filename(name) for name in entity_names}

    # ------------------------------------------------------------------
    # Encoding policy (single source of truth for the BOM rule)
    # ------------------------------------------------------------------

    @classmethod
    def csv_encoding(cls, entity_name: str) -> str:
        """Return the CSV encoding for ``entity_name`` — the ONE place the BOM
        rule lives. ``utf-8-sig`` (BOM, Excel-friendly) by default; plain
        ``utf-8`` (no BOM) for :data:`_NO_BOM_ENTITIES`, whose strict downstream
        parser rejects a BOM-prefixed first header.

        :meth:`_write_csv` is the one place that consults this, which makes it
        exactly ONE encoding decision in the tree today — the
        StudentAttendance-BOM class of bug is structurally avoided as long as
        that holds; nothing mechanically forbids a new ``to_csv`` call site, so
        a second write path must route here. The published statement of this
        rule, with the incident that motivated it, is the BOM matrix in
        ``docs/developer/output-contract.md``; ``tests/contract_schema.py``
        mirrors it and a policy test pins the two to this method."""
        return "utf-8" if entity_name in cls._NO_BOM_ENTITIES else "utf-8-sig"

    # ------------------------------------------------------------------
    # Stale-output detection + archival (NON-DESTRUCTIVE)
    # ------------------------------------------------------------------

    def detect_stale_outputs(self, emitted: set[str], *, keep_zip: str | None) -> list[str]:
        """Return the output-folder files this run did NOT refresh, as sorted relative paths.

        Three kinds, all **detection only** (the move lives in :meth:`archive_stale_outputs`):

        * an entity CSV at its layout path (``csv/<Entity>.csv`` for rostering, the top
          level for a standalone feed) whose entity is a recognized one (the transformer
          registry) but is **not** in ``emitted``: left by a prior or different run, it
          would otherwise ship unrefreshed;
        * a rostering CSV still at the folder's TOP level: the pre-DSYNC-57 layout, now
          superseded by ``csv/`` (it would sit beside the zip looking like an upload file);
        * an ``acsv_*.zip`` at the top level other than ``keep_zip`` (this run's zip, or
          ``None`` when this run wrote none): an earlier day's bundle.

        A broad registry known-set is safe HERE precisely because nothing is removed
        (the caller archives, never deletes).

        Args:
            emitted: Entity names this run produced (e.g. ``set(outputs)``).
            keep_zip: This run's zip filename (``save_all``'s return), never archived.

        Returns:
            Sorted POSIX-style paths relative to the output folder (empty if none).
        """
        from src.etl.transformers.registry import TRANSFORMER_REGISTRY

        known_files = self.output_filenames(TRANSFORMER_REGISTRY)
        emitted_files = self.output_filenames(emitted)
        stale: set[Path] = set()
        for name in known_files:
            current = output_layout.csv_relpath(name)
            if name not in emitted_files and (self.output_path / current).is_file():
                stale.add(current)
            if (
                not output_layout.is_standalone(name)
                and output_layout.legacy_csv_path(self.output_path, name).is_file()
            ):
                stale.add(Path(name))
        for zip_path in output_layout.acsv_zips(self.output_path):
            if zip_path.name != keep_zip:
                stale.add(Path(zip_path.name))
        return sorted(p.as_posix() for p in stale)

    def archive_stale_outputs(self, emitted: set[str], *, keep_zip: str | None) -> list[str]:
        """Move stale output files aside into ``archive_<ts>/``: **non-destructive**.

        Detects them via :meth:`detect_stale_outputs`. If none, returns ``[]`` and creates
        **no** archive directory. Otherwise lazily creates
        ``<output_dir>/archive_<timestamp>/`` and **moves** each stale file into it at the
        same relative path (``csv/Family.csv`` → ``archive_<ts>/csv/Family.csv``) with
        ``os.replace`` (an atomic same-filesystem rename).

        **Archives, never deletes, for cross-config safety.** Under ``_base`` config
        inheritance the safe allow-list for a *delete* is unknowable (a
        registry/``mappings.keys()`` set would erase a DIFFERENT config's
        inherited-but-disabled entity CSV sharing this dir; an ``enabled_entities`` set
        would miss a config-dropped entity AND wipe an enabled entity's last-good CSV on an
        empty-input run). Moving the file aside preserves the original bytes, recoverable
        by hand.

        **Excluded from SFTP delivery.** Delivery reads only the top-level zip and the
        top-level standalone feeds, so nothing inside ``archive_<ts>/`` can ship.

        **Best-effort: never fails the run.** The roster has already been committed (and,
        if requested, delivered) before this runs, so a per-file move hiccup is logged at
        ERROR and skipped rather than raised.

        Args:
            emitted: Entity names this run produced (e.g. ``set(outputs)``).
            keep_zip: This run's zip filename (``save_all``'s return), never archived.

        Returns:
            Sorted relative paths actually moved into the archive subdir.
        """
        stale = self.detect_stale_outputs(emitted, keep_zip=keep_zip)
        if not stale:
            return []

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_dir = self.output_path / f"archive_{timestamp}"
        archive_dir.mkdir(parents=True, exist_ok=True)

        archived: list[str] = []
        for rel in stale:
            try:
                target = archive_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(self.output_path / rel, target)
                archived.append(rel)
            except OSError as ex:
                # Best-effort: an archive hiccup must not fail an already
                # committed + delivered run. Log loud and continue.
                logger.error(f"Failed to archive stale output {rel}: {ex}")
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
            output_file = output_layout.csv_path(directory, self.csv_filename(entity_name))
            output_file.parent.mkdir(parents=True, exist_ok=True)
            encoding = self.csv_encoding(entity_name)
            ordered.to_csv(output_file, index=False, encoding=encoding)
            label = "Staged" if staging else "Saved"
            logger.info(f"{label} {entity_name}.csv ({len(df)} rows) → {output_file}")
        except Exception as ex:  # noqa: BLE001 — re-raised; logs which CSV failed first
            logger.error(f"Failed to write {entity_name}.csv: {ex}")
            raise
