"""Convert surface — the admin's manual "run it now" flow (IA model IA-5).

VIEW glue (coverage-omitted): the trust-critical logic lives COUNTED in the pure
modules — ``job_runner`` (the single-flight state machine + the ``SystemExit``/
``Exception`` routing seam) and ``convert_result`` (``ConvertResult`` +
``summarize``). This file wires them to controls + holds the thin ``convert_job``
orchestration the ``JobRunner`` runs off the UI thread.

**The adapter, not ``run_pipeline`` (parity lock):** ``convert_job`` mirrors the
Streamlit ``02_Convert.run_conversion`` + the parity test's ``_run_ui_path`` —
``load_config → to_raw_dict → load_data → run_transform → save_all`` — so the
UI's output stays byte-for-byte identical to the CLI (locked by
``tests/test_pipeline_parity.py``). It is CALLED unchanged; nothing in
``src/etl``/``src/config`` is touched.

**Concurrency (C1–C5, see the plan's concurrency contract):** the blocking pandas
work runs inside ``JobRunner.run`` (``page.run_thread``); no control is mutated
from the worker thread — the result renders inside the ``on_done``/``on_error``
handlers the loop owns; a double-click can't launch two conversions (the state
machine's single-flight ``start()``); a failure surfaces as a calm FAILED banner
with the button re-enabled.

**The delivery-integrity gate (FIX-2):** ``convert_job`` calls the SAME
``etl.pipeline.check_delivery_integrity`` the CLI does — never a second implementation —
BEFORE ``save_all``/``archive_stale_outputs``/``upload_csvs`` and before the anomaly gate.
An output set the gate cannot vouch for (nothing produced; or the roster anchor missing
while dependent entities were built) is refused with a terminal status, the previous
output set untouched, and a ``status="failed"`` run record carrying the gate's bounded
category — so the manual path can no longer report a green night that delivered enrolments
for zero students. Tier configs with no anchor by design (``mbponly``/``mbp_core``/
``sd51attendance``) are judged by their CONFIGURED entity set and never fire it.

**The anomaly-ack write-gate:** ``convert_job`` computes anomalies AFTER transform
and, when a >20% drop fires without an authorizing ``anomaly_ack``, returns
``NEEDS_ANOMALY_ACK`` **WITHOUT writing**. The view then shows a plain-language
WARNING + an explicit "I've reviewed this — convert anyway" CTA and a Cancel (which
writes nothing). A silent 20% roster drop is structurally impossible.

**The ack is run-scoped, not a bare yes (FIX-2):** ``anomaly_ack`` is a
``convert_output.RunIdentity`` TOKEN naming the ``(district, input folder)`` that was
reviewed, and ``ack_authorizes`` honours it only for that run — so an approval given for
one folder can never be spent on another. Two layers, one counted: the view freezes those
inputs while the card is up (``interaction_state(awaiting_ack=True)``) and replays the
reviewed identity on click; ``convert_job`` re-checks the token at the gate itself, so a
future view edit cannot reopen the hole.

**SFTP delivery (IA-5b):** ``convert_job`` gains an ``sftp_requested`` leg — after a
successful build, an explicit pre-flight-confirmed delivery. The ``upload_csvs`` call
is wrapped TIGHTLY (only around the upload): a failure folds into a
``BUILT_NOT_DELIVERED`` result (the exit-3 shape — read from booleans by
``summarize``, NEVER routed through ``on_error``), carrying a fault CATEGORY only —
never the raw exception / host / path (privacy). A ``load_config`` failure, or a
``save_all`` ``ValueError`` (a missing field-map column), still PROPAGATES to
``on_error`` (fail-loud); the upload catch never widens over the build. A failed
delivery never rolls back the build — the files stay written and the admin can retry.

**The output-folder pre-flight (plan 0050):** ``convert_job`` calls
``etl.loader.output_target_problem`` FIRST — before ``to_raw_dict`` and before a single
roster byte is read — and an unusable folder returns ``OUTPUT_FOLDER_UNUSABLE`` with no
ETL work done and no run record. The write is additionally wrapped in ``except OSError``
(the Excel-lock / drive-drops-mid-run window a pre-check structurally cannot see) and
folds into the SAME status. Both exist because every one of these faults used to surface
as the retired ``convert_error_copy``, which told the admin to check their *input* folder.

**Deliver from disk (0034 Slice 2):** EVERY deliver action — the post-build card, the
BUILT_NOT_DELIVERED retry, and the standalone "Deliver the files in your output folder"
card — runs ``deliver_job``, which uploads the ALREADY-COMMITTED top-level output CSVs
(``save_all`` is atomic, so that set is never torn) and NEVER re-transforms. A
between-build-and-deliver input change therefore cannot alter what ships, and the
anomaly write-gate is untouched (it guards WRITES; a delivery writes nothing — the old
deliver-by-rebuild's hardcoded ``anomaly_ack=True`` bypass is gone with the rebuild).
The confirm dialog names the server + local folder once and carries the honest
freshness fact ("Files last built …", from the newest on-disk CSV's mtime).

**One district per deliver, chosen once (FIX-5):** the district that names the zip
reaching SpacesEDU is resolved by the ACTION and threaded down through
``_confirm_and_deliver`` → ``_start_deliver`` → ``deliver_job`` — never re-read from the
live dropdown mid-flow. The post-run result card binds to its run's ``RunIdentity`` (the
files it built are the only ones it may send, under the only name they may carry); the
standalone card, which has no prior run, binds the current pick at click. That distinction
is load-bearing: rostering configs write byte-identical filenames, so a click-time re-read
after a dropdown change did not fail — it SUCCEEDED, shipping ``sd74myedbc``'s roster as
``districtsync_sd40_<date>.zip``. ``_refresh_deliver_slot`` now re-gates the result card
too (``convert_output.result_deliver_state``), and ``_confirm_and_deliver`` refuses an
empty ``deliverable_files`` before the confirm dialog can quote a vintage for files that
do not exist — one choke point every present and future deliver route inherits.

**Cold-state + interaction sweep (0035 W3b):** pre-setup, the screen leads with the
routed "Finish setup first" card (pure ``show_setup_first_card``/``setup_first_copy``;
``on_navigate`` injected by the shell, defensive without it); the unset-output caption is
mode-aware (wizard vs Settings); an amber ``district_mismatch_note`` flags a per-run pick
that differs from the saved district; and every busy/idle disabled flag paints the pure
``interaction_state`` table (inputs lock while a job runs — no dead clicks, no
double-start, no mid-run edits). The ``on_error`` cards render bounded, fixed copy ending with
a concrete next step: a failed BUILD renders ``failure_copy.error_card_copy(exc)`` — the
exception's category (by TYPE, never its text) worded by the same table Home and Run History
read (plan 0053 S3) — and a failed deliver pre-flight renders ``deliver_error_copy``.

**Write-in-flight close guard (IA-5b, C6):** a module-level flag
(``_WRITE_IN_FLIGHT``) is set immediately before ``save_all`` and cleared in a
``finally``; ``is_write_in_flight()`` exposes it for ``shell._on_leave``. It is
REASSURANCE-ONLY — the loader's backup-and-restore ``save_all`` atomicity is the real
safety net (a torn commit rolls back); ``_on_leave`` reads the flag but does NOT block
the atomic close.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path

import flet as ft

from src.config.app_config import AppConfig
from src.config.loader import load_config
from src.etl.errors import OutputFolderUnsetError, RunErrorCategory
from src.etl.extractor import DataExtractor
from src.etl.loader import DataLoader, output_target_problem
from src.etl.outcomes import EntityOutcome, OutcomeLedger
from src.etl.pipeline import (
    advisory_expected_files,
    build_run_record,
    check_delivery_integrity,
    compute_anomalies,
    configured_entity_order,
    extract_required_files,
    has_no_usable_input,
    run_transform,
)
from src.history.store import write_run_record
from src.quality.report import DataQualityReport, declared_blank_fields
from src.sftp.uploader import SFTPUploader
from src.ui_flet import components, tokens
from src.ui_flet.convert_output import (
    DeliverableFiles,
    DeliverReadiness,
    ResultDeliverReadiness,
    RunIdentity,
    ack_authorizes,
    can_run_convert,
    configured_output_entities,
    deliverable_files,
    deliverable_manifest,
    district_mismatch_note,
    freshness_fact,
    interaction_state,
    missing_files_copy,
    nothing_to_deliver_copy,
    open_folder,
    output_dir_is_set,
    resolved_output_caption,
    result_deliver_state,
    result_district_changed_copy,
    run_identity,
    setup_first_copy,
    show_setup_first_card,
    standalone_deliver_state,
    this_run_label,
)
from src.ui_flet.convert_result import (
    ConvertResult,
    ConvertStatus,
    deliver_error_copy,
    status_for_integrity_fault,
    summarize,
)
from src.ui_flet.failure_copy import error_card_copy
from src.ui_flet.filepicker import validate_input_dir
from src.ui_flet.humanize import ENTITY_LABELS, friendly_district_name
from src.ui_flet.identity_gate import stored_identity_domain
from src.ui_flet.job_runner import JobRunner
from src.ui_flet.mapping_catalog import disambiguated_labels, filtered_catalog
from src.ui_flet.picker_field import PickerField
from src.ui_flet.verdict import Verdict

# GDE files are CSV or TXT (varies by district).
_GDE_SUFFIXES: tuple[str, ...] = (".csv", ".txt")

# Write-in-flight flag (C6): True while an atomic `save_all` is committing. Read by
# `shell._on_leave` (reassurance-only — the loader's atomicity is the real net; the
# flag never blocks the close). A plain module-level bool: the ETL is single-flight
# (the JobRunner's state machine), so no lock is needed for this reassurance read.
_WRITE_IN_FLIGHT: bool = False


# Convert had NO logger at all until QA 2026-08-18: every failure path here is deliberately
# PRIVACY-BOUNDED on screen (a fixed category card, never the raw exception — which may carry a
# path or a column name), and with nothing writing the other half to disk a crash left literally
# no trace to diagnose. An admin's output CSV held open in Excel surfaced as a calm card and an
# empty log. The screen's own "Open log folder" affordance promises this file has the detail;
# these calls are what make that true.
logger = logging.getLogger(__name__)


def is_write_in_flight() -> bool:
    """Whether a Convert atomic write is committing right now (read by ``shell._on_leave``).

    Reassurance-only: the loader's backup-and-restore ``save_all`` atomicity is the
    real safety net for a mid-write close — this flag never blocks the close, it only
    lets the leave-point note the in-flight write.
    """
    return _WRITE_IN_FLIGHT


def _pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


# --------------------------------------------------------------------------- #
# The boundary orchestration the JobRunner runs OFF the UI thread.             #
# Thin + single-purpose; mirrors run_conversion + _run_ui_path (parity-locked).#
# Returns a PII-free ConvertResult (NO DataFrames escape this function).       #
# --------------------------------------------------------------------------- #
def convert_job(
    config_name: str,
    input_dir: str,
    *,
    anomaly_ack: RunIdentity | None = None,
    sftp_requested: bool = False,
) -> ConvertResult:
    """Run the parity-locked convert adapter and return a PII-free ``ConvertResult``.

    Off the UI thread (``JobRunner`` runs it via ``page.run_thread``):
    ``load_config → load_data → run_transform → check_delivery_integrity →
    compute_anomalies`` and — only when clear or acknowledged — ``save_all`` +
    stale-output archival + the quality report, then (only when ``sftp_requested``)
    an SFTP delivery.

    **The output-folder pre-flight comes FIRST (plan 0050),** before ``to_raw_dict`` and
    before the input read: :func:`~src.etl.loader.output_target_problem` refuses an
    unreachable / unwritable output folder with ``OUTPUT_FOLDER_UNUSABLE`` having done NO
    ETL work, and the write below is wrapped in ``except OSError`` for the window a
    pre-check cannot see. Neither writes a run record (see :func:`_record_manual_run`):
    nothing was produced, and the admin is watching the surface that already shows it.

    **The outcome ledger (plan 0053 S2)** is built right after ``to_raw_dict`` — the SAME
    point as ``run_pipeline``'s: the output folder has passed its pre-flight and nothing has
    been read yet — and every result from there on carries its outcomes, so a Convert
    result (and its record) says per entity exactly what the CLI would. ``NO_INPUT`` carries
    every entity NOT_RUN (``finalize_aborted``), as the CLI's failure sink records for the
    same folder. The pre-flight refusal carries ``None``: no ledger existed.

    **The entity bulkhead (plan 0053 S4)** lives inside the shared ``run_transform``, so a
    Convert whose ISOLATABLE entity fails (Family on a plain contacts report) COMPLETES
    exactly as the CLI does: that entity is recorded FAILED and absent from the outputs, and
    the steps below run in the CLI's order — the integrity gate, the anomaly gate (whose
    prompt names why the file is missing), the write, the archive of the entity's previous
    CSV, the delivery — with the outcomes on every result, which ``summarize`` shows as the
    PARTIAL warning. A CRITICAL entity's raise still propagates to ``on_error`` unchanged.

    **Two pre-write gates, in the CLI's order.**

    1. :func:`~src.etl.pipeline.check_delivery_integrity` — the SAME way-OUT gate
       ``run_pipeline`` calls, not a second implementation. It refuses an output set that
       cannot be vouched for (nothing produced at all, or the roster anchor missing while
       dependent entities were built) and returns the matching terminal status
       (:func:`status_for_integrity_fault`). It sits FIRST, so an anomaly acknowledgement
       can never buy delivery of an anchor-less payload: "I've reviewed the smaller files"
       is consent about SIZE, never consent to ship enrolments for students that will not
       be delivered. Refusing here — before ``save_all`` and before
       ``archive_stale_outputs`` — leaves the previous, self-consistent output set intact;
       the admin fixes the export and re-runs, with nothing to un-archive.
    2. The anomaly write-gate. ``anomaly_ack`` is a :class:`RunIdentity` TOKEN, not a
       boolean: it authorizes the write only when it names the run actually executing
       (:func:`ack_authorizes`), so an approval given for one district/folder can never be
       spent on another. A pending anomaly without a matching token returns
       ``NEEDS_ANOMALY_ACK`` **without writing**.

    ETL-level failures (a missing field-map column → ``save_all``'s ``ValueError``,
    ``load_config``'s errors) propagate as exceptions → the runner's ``on_error``
    (fail-loud). Exactly TWO steps catch in-job, each scoped tightly: the write's
    ``OSError`` (→ ``OUTPUT_FOLDER_UNUSABLE``, above) and the SFTP leg — the
    ``upload_csvs`` call is wrapped TIGHTLY and folded into a ``BUILT_NOT_DELIVERED``
    result (the exit-3 shape — a CATEGORY only, never the raw exception/host/path),
    so a build failure is never mis-labelled "SFTP failed" and a failed delivery
    never discards the written files. NO DataFrame is returned (privacy).

    A committed run (built + optionally delivered) is recorded to the run-history store
    tagged ``source="manual"`` via :func:`_record_manual_run` — best-effort, never fatal;
    so is a delivery-integrity REFUSAL, as ``status="failed"`` carrying the gate's bounded
    ``error_category`` (never ``success`` — Home and Run History must not paint a night
    green that delivered nothing, and this is the CLI's behaviour on the same fault).
    """
    t0 = time.monotonic()
    config = load_config(config_name)

    # Resolve the output folder up front and FAIL LOUD if it's unset (D10): the view gate
    # (`can_run_convert`) blocks a run with no output folder, so reaching here empty is a
    # programming/gate error — never silently write into the *input* folder (the old
    # `AppConfig.load().output_dir or input_dir` fallback is gone). Fail-fast, before any I/O.
    output_dir_value = (AppConfig.load().output_dir or "").strip()
    if not output_dir_value:
        raise OutputFolderUnsetError("No output folder is configured — set one in Settings before converting.")
    output_dir = Path(output_dir_value)

    # Gate 0 — the OUTPUT-FOLDER PRE-FLIGHT (plan 0050). HERE, not at the `DataLoader(...)`
    # line below, which sits AFTER the whole ETL (see the docstring). The free-text reason
    # is LOG-ONLY — it can carry a path; the admin gets the bounded zero-arg copy.
    output_problem = output_target_problem(output_dir_value)
    if output_problem is not None:
        logger.error("Output folder is not usable for district %r: %s", config_name, output_problem)
        return ConvertResult(
            status=ConvertStatus.OUTPUT_FOLDER_UNUSABLE, entity_outcomes=None, delivery_requested=sftp_requested
        )

    raw = config.to_raw_dict()
    mappings = raw.get("mappings", {})
    global_config = raw.get("global_config", {})

    # The outcome ledger (plan 0053 S2) — the same point as `run_pipeline`'s, before the input
    # is read, so the two entry points report the same outcomes for the same fault.
    ledger = OutcomeLedger(configured_entity_order(mappings, global_config))

    # Collect explicit headers for headerless files (exactly as run_conversion does).
    file_headers: dict[str, list[str]] = {}
    for entity_cfg in mappings.values():
        for filename, header_list in entity_cfg.get("headers", {}).items():
            file_headers[filename] = header_list

    # Read EXACTLY the files this config names, through the SAME disk path the CLI uses
    # (plan 0051). Convert used to read every `.csv`/`.txt` in the picked folder and parse
    # the lot, so an extract DistrictSync never reads could decide the run: SD67's died on
    # an empty `AccidentInformation.txt` while their nightly over the same folder
    # succeeded. Going through `load_data` also inherits the disk path's case-insensitive
    # resolution and its case-COLLISION raise, which the bytes path never had.
    required_files = extract_required_files(config)
    raw_data = DataExtractor(str(input_dir)).load_data(required_files, file_headers=file_headers)

    # NOT `not raw_data`: `load_data` inserts an EMPTY frame per file it cannot find, so a
    # folder with nothing in it yields a FULL dict and the bare truthiness test would never
    # fire again. Shared with `run_pipeline` so both paths answer this the same way.
    if has_no_usable_input(raw_data):
        # Every entity NOT_RUN — what the CLI records when it raises `NoUsableInputError` here.
        return ConvertResult(
            status=ConvertStatus.NO_INPUT, entity_outcomes=ledger.finalize_aborted(), delivery_requested=sftp_requested
        )

    transform_outputs = run_transform(raw_data, mappings, global_config, ledger=ledger)
    outputs = transform_outputs.outputs
    field_orders = transform_outputs.field_orders
    data_errors = transform_outputs.data_errors
    sy_determination = transform_outputs.school_year
    entity_outcomes = transform_outputs.outcomes

    # What this run was CONFIGURED to produce (enabled-entities-derived, NEVER raw
    # `mappings.keys()`). Computed once and shared by both pre-write gates, exactly as
    # `run_pipeline` does — so a tier config with no roster anchor (mbponly, mbp_core,
    # sd51attendance) is judged by ITS configured set and never false-positives.
    expected_entities = configured_entity_order(mappings, global_config)

    # Gate 1 — delivery integrity, BEFORE the write and before the anomaly gate. The same
    # pure check the CLI raises on; here its bounded category becomes a terminal status.
    integrity_fault = check_delivery_integrity(outputs, expected_entities)
    if integrity_fault is not None:
        refused = ConvertResult(
            status=status_for_integrity_fault(integrity_fault.category),
            entity_counts={name: len(df) for name, df in outputs.items()},
            data_errors_total=_data_errors_total(data_errors),
            entity_outcomes=entity_outcomes,
            delivery_requested=sftp_requested,
        )
        _record_manual_run(
            refused,
            sis_type=config_name,
            elapsed=time.monotonic() - t0,
            status="failed",
            error_category=integrity_fault.category,
            entity_outcomes=entity_outcomes,
        )
        return refused

    # Gate 2 — anomalies: a >20% drop, or an entity this run was configured to produce
    # that vanished (present→absent / N→0). Withholds the write unless the pending
    # acknowledgement was given for THIS run (district + input folder).
    anomalies = compute_anomalies(outputs, output_dir, expected_entities)
    if anomalies and not ack_authorizes(anomaly_ack, run_identity(config_name, input_dir)):
        return ConvertResult(
            status=ConvertStatus.NEEDS_ANOMALY_ACK,
            entity_counts={name: len(df) for name, df in outputs.items()},
            data_errors_total=_data_errors_total(data_errors),
            anomalies=tuple(anomalies),
            entity_outcomes=entity_outcomes,
            delivery_requested=sftp_requested,
        )

    # Atomic write. Plan 0050 REVERSES the old "a `save_all` failure PROPAGATES" rule,
    # deliberately and for `OSError` ONLY: that is the same output-folder fault the
    # pre-flight refuses, so it gets the same honest result instead of the input-folder
    # `on_error` card. A `ValueError` (a missing field-map column) is a DATA fault and
    # still propagates. No NEW output is written, so no run record either; what survives
    # of the previous set is the loader's BEST-EFFORT rollback. `_WRITE_IN_FLIGHT` (C6)
    # is still cleared in the `finally` on both paths.
    global _WRITE_IN_FLIGHT
    try:
        loader = DataLoader(str(output_dir))
        _WRITE_IN_FLIGHT = True
        try:
            loader.save_all(outputs, field_orders)
        finally:
            _WRITE_IN_FLIGHT = False
    except OSError:
        # A RESULT on screen, but still a failure worth a trace — the card is bounded and
        # category-only, so the raw path lives here or nowhere.
        logger.error("Could not write to the output folder (district %r).", config_name, exc_info=True)
        return ConvertResult(
            status=ConvertStatus.OUTPUT_FOLDER_UNUSABLE,
            entity_outcomes=entity_outcomes,
            delivery_requested=sftp_requested,
        )

    # Archive (non-destructive) entity CSVs left in the output dir that this run
    # did NOT produce — mirrors run_pipeline: a stale CSV must never ship in an
    # SFTP zip (the delivery leg below, or a later deliver-from-disk, globs the
    # top-level *.csv set). Moving them into archive_<ts>/ (a SUBfolder) excludes
    # them without deleting anything; best-effort — never fails a committed build.
    loader.archive_stale_outputs(set(outputs))

    # Columns the config declares fixed-blank ({value: ""}) skip the missing-field check —
    # blank by design is not a finding (see quality/report.py; same rule as run_pipeline).
    quality_text = (
        DataQualityReport()
        .analyze(outputs, declared_blank=declared_blank_fields(raw), school_year=sy_determination)
        .to_text()
    )
    entity_counts = {name: len(df) for name, df in outputs.items()}
    errors_total = _data_errors_total(data_errors)

    # SFTP delivery leg (IA-5b): only after a successful build, only when requested.
    # The `upload_csvs` catch is scoped TIGHTLY around the upload — a build failure
    # (steps 1–5) already propagated to `on_error` above; this catch never widens.
    # A failure folds into the exit-3 BUILT_NOT_DELIVERED result (a CATEGORY via the
    # booleans — `summarize` maps it to fixed copy; the raw error is NEVER carried).
    # The build stays written; the admin can retry delivery.
    if sftp_requested:
        cfg = AppConfig.load()
        try:
            SFTPUploader(
                host=cfg.sftp_host,
                port=cfg.sftp_port,
                username=cfg.sftp_username,
                remote_path=cfg.sftp_remote_path,
            ).upload_csvs(
                output_dir,
                sis_type=config_name,
                # THIS build's committed CSVs — never the folder's *.csv glob (a stray
                # admin backup/spreadsheet in the output folder must not reach SpacesEDU).
                manifest=DataLoader.output_filenames(outputs),
            )
        except Exception:  # noqa: BLE001 - exit-3: a failed delivery is a RESULT, not on_error
            # A RESULT on screen, but still a failure worth a trace: this is the branch where
            # the roster was built and NOT delivered, and "why not" is only answerable here.
            logger.error("Delivery failed after a successful build (district %r).", config_name, exc_info=True)
            built_not_delivered = ConvertResult(
                status=ConvertStatus.BUILT_NOT_DELIVERED,
                entity_counts=entity_counts,
                data_errors_total=errors_total,
                sftp_attempted=True,
                sftp_ok=False,
                quality_text=quality_text,
                entity_outcomes=entity_outcomes,
                delivery_requested=sftp_requested,
            )
            _record_manual_run(
                built_not_delivered,
                sis_type=config_name,
                elapsed=time.monotonic() - t0,
                entity_outcomes=entity_outcomes,
            )
            return built_not_delivered
        # Data errors are a SEPARATE axis — a successful delivery must NOT erase the
        # "N records had field problems" warning (fail-loud; mirrors home_status).
        delivered_status = ConvertStatus.DELIVERED_WITH_DATA_ERRORS if errors_total > 0 else ConvertStatus.DELIVERED
        delivered = ConvertResult(
            status=delivered_status,
            entity_counts=entity_counts,
            data_errors_total=errors_total,
            sftp_attempted=True,
            sftp_ok=True,
            quality_text=quality_text,
            entity_outcomes=entity_outcomes,
            delivery_requested=sftp_requested,
        )
        _record_manual_run(
            delivered, sis_type=config_name, elapsed=time.monotonic() - t0, entity_outcomes=entity_outcomes
        )
        return delivered

    status = ConvertStatus.BUILT_WITH_DATA_ERRORS if errors_total > 0 else ConvertStatus.DELIVERED
    built = ConvertResult(
        status=status,
        entity_counts=entity_counts,
        data_errors_total=errors_total,
        quality_text=quality_text,
        entity_outcomes=entity_outcomes,
        delivery_requested=sftp_requested,
    )
    _record_manual_run(built, sis_type=config_name, elapsed=time.monotonic() - t0, entity_outcomes=entity_outcomes)
    return built


def deliver_job(sis_type: str) -> ConvertResult:
    """Deliver the ALREADY-COMMITTED output CSVs from disk — never a re-transform (0034 Slice 2).

    Off the UI thread (the same ``JobRunner`` seam as ``convert_job``). Uploads the
    ACTIVE CONFIG's entity CSVs that the last committed build left in the resolved output
    folder (``save_all``'s atomic commit means that set is never torn); the input folder is
    NEVER read, so a between-build-and-deliver input change cannot alter what ships, and
    the anomaly write-gate is untouched (it guards WRITES; a delivery writes nothing).

    **The authoritative set, with no ``outputs`` to vouch for.** A delivery-only run never
    transformed anything, so "what this run produced" doesn't exist. The honest substitute
    is *what the active district config would produce* — ``configured_entity_order``
    (enabled-entities-derived, never raw ``mappings.keys()``) intersected with the folder,
    via :func:`deliverable_manifest`. A stray ``*.csv`` an admin dropped in the output
    folder is therefore NOT delivered, and a CSV owned by a DIFFERENT config (e.g. a
    ``CourseInfo.csv`` left by an ``mbp_all`` run before the district was switched) is not
    delivered under this config either — matching ``archive_stale_outputs``' semantics.

    Outcomes fold into the result shapes ``summarize`` already maps: success →
    ``DELIVERED_FROM_DISK``; a failed upload (including a raced-away empty folder —
    ``upload_csvs`` fails loud on nothing to send) → ``BUILT_NOT_DELIVERED`` (the exit-3
    shape, a CATEGORY only — never the raw exception / host / path). An unset output folder,
    an unset district (no config ⇒ no authoritative set — never fall back to "ship the
    folder"), or an unloadable config is a gate/programming error → fail loud to
    ``on_error``. Both outcomes are recorded to the run store as a ``delivery_only`` record
    (``source="manual"``) carrying NO build entity counts — a delivery ships an earlier
    build, it isn't one.
    """
    t0 = time.monotonic()
    cfg = AppConfig.load()
    output_dir_value = (cfg.output_dir or "").strip()
    if not output_dir_value:
        raise ValueError("No output folder is configured — set one in Settings before delivering.")

    district = (sis_type or "").strip()
    if not district:
        raise ValueError("No district is selected — choose your district in Settings before delivering.")

    # Resolved BEFORE the delivery try-block: a config problem is a fail-loud setup fault,
    # not a delivery failure, and must not be mislabelled "we couldn't send your files".
    # `configured_output_entities` is the SAME district→entity step the screen's readiness
    # gate reads (FIX-4), so what was offered and what ships can never disagree.
    manifest = deliverable_manifest(configured_output_entities(district), output_dir_value)

    try:
        SFTPUploader(
            host=cfg.sftp_host,
            port=cfg.sftp_port,
            username=cfg.sftp_username,
            remote_path=cfg.sftp_remote_path,
        ).upload_csvs(Path(output_dir_value), sis_type=district, manifest=manifest)
    except Exception:  # noqa: BLE001 - exit-3 shape: a failed delivery is a RESULT, not on_error
        logger.error("Delivery-only run failed (district %r).", district, exc_info=True)
        failed = ConvertResult(
            status=ConvertStatus.BUILT_NOT_DELIVERED,
            sftp_attempted=True,
            sftp_ok=False,
            entity_outcomes=None,
            delivery_requested=True,
        )
        _record_manual_run(
            failed, sis_type=sis_type, elapsed=time.monotonic() - t0, delivery_only=True, entity_outcomes=None
        )
        return failed
    delivered = ConvertResult(
        status=ConvertStatus.DELIVERED_FROM_DISK,
        sftp_attempted=True,
        sftp_ok=True,
        entity_outcomes=None,
        delivery_requested=True,
    )
    _record_manual_run(
        delivered, sis_type=sis_type, elapsed=time.monotonic() - t0, delivery_only=True, entity_outcomes=None
    )
    return delivered


def _data_errors_total(data_errors: list[dict]) -> int:
    """Total non-fatal per-row transform errors (mirrors the Streamlit sum)."""
    return sum(int(e.get("failed_rows", 0)) for e in (data_errors or []))


def _record_manual_run(
    result: ConvertResult,
    *,
    sis_type: str,
    elapsed: float,
    delivery_only: bool = False,
    status: str = "success",
    error_category: str = RunErrorCategory.NONE.value,
    entity_outcomes: tuple[EntityOutcome, ...] | None,
) -> None:
    """Write a manual Convert run to the run-history store (source="manual"), best-effort.

    Manual runs used to never appear in Run History (``convert_job`` bypasses
    ``run_pipeline``/``_emit_run_log`` by design). This writes the SAME flat record shape
    through the SAME ``build_run_record`` + ``write_run_record`` seam the pipeline uses, so a
    manual run finally shows up tagged ``manual``. A committed Convert always BUILT
    successfully → the default ``status="success"``; a failed SFTP delivery is the separate
    ``sftp_*`` axis (the exit-3 shape). Strictly non-fatal — never changes the returned
    ``ConvertResult``.

    ``status`` / ``error_category`` (FIX-2) are the truthful-refusal axis: a delivery-integrity
    refusal passes ``"failed"`` plus the gate's BOUNDED :class:`RunErrorCategory` value, so
    Home's "did the roster sync?" verdict and the Run History row read the refusal as the
    failure it is instead of a green night. The privacy split is unchanged — the category is
    a closed-set enum value; the free-text detail never reaches the store.

    ``delivery_only`` (0034 Slice 2) marks a ``deliver_job`` attempt: the record's flat count
    keys stay zeros (a delivery ships an earlier build — its counts belong to that build's
    record, never repeated here) and the rider lets ``home_status``/``run_history`` render it
    as a delivery, not a 0-row build.

    ``entity_outcomes`` (plan 0053 S2) is REQUIRED keyword-only with no default: the build's
    per-entity outcomes (the same the CLI records for the same fault), or ``None`` for a
    delivery-only record — a delivery ships an earlier build's files and has no ledger of its
    own. It rides the record as ``entity_outcomes`` through the shared ``build_run_record``.

    Deliberate asymmetry with ``run_pipeline``: ``NO_INPUT`` and ``NEEDS_ANOMALY_ACK`` write
    nothing — the first is "you picked the wrong folder" and the second is a question, not an
    outcome, and the admin is watching the surface where both are already shown. What IS
    recorded is every run that either produced output or was REFUSED by the delivery gate:
    those look like a normal night from the outside, so the ledger has to carry them.
    """
    record = build_run_record(
        status=status,
        elapsed=elapsed,
        entity_counts=result.entity_counts,
        sftp_attempted=result.sftp_attempted,
        sftp_ok=result.sftp_ok,
        anomalies=[],  # a manual run's anomaly was reviewed + acknowledged in the UI, not a standing warning
        data_errors={"total": result.data_errors_total} if result.data_errors_total else {},
        source="manual",
        sis_type=sis_type,
        error_category=error_category,
        entity_outcomes=entity_outcomes,
    )
    if delivery_only:
        record["delivery_only"] = True  # rides free in the store's JSON blob (additive, no schema change)
    # Recording a manual run is best-effort — never fail the conversion. ``write_run_record``
    # already swallows sqlite/OS errors; suppress anything else too (belt-and-suspenders).
    with contextlib.suppress(Exception):
        write_run_record(record, source="manual")


# --------------------------------------------------------------------------- #
# The view.                                                                     #
# --------------------------------------------------------------------------- #
def build_convert(
    page: ft.Page,
    on_navigate: Callable[[str], None] | None = None,
) -> ft.Control:  # pragma: no cover - Flet view glue
    """Build the Convert surface, bound to ``page`` (via ``partial`` in the shell).

    ``on_navigate`` (Home's exact injection pattern — the shell passes ``select_by_id``)
    powers the pre-setup "Finish setup first" card's routed "Open Setup" action (0035
    W3b). Optional + defensive: an un-wired mount still renders the card's copy (which
    stands alone), just without the routing button.
    """
    cfg = AppConfig.load()
    # The per-run pick, declared HERE (before the first catalog build) so the filter can carry
    # it. It starts empty — the prefill below is derived FROM the catalog, so it cannot also be
    # an input to it — and that first build loses nothing: `saved_sis` already carries the
    # saved district, which is the only value the prefill can ever take.
    selected: dict[str, str | None] = {"district": None}

    def _catalog():  # noqa: ANN202 - a FilteredCatalog; annotating adds an import for one line
        # 0038 S5: the district rows scoped to this admin's stored address, unconditionally
        # since 2026-08-04 (the per-surface show-all row retired). `picked_sis` is the LIVE
        # per-run selection, so any re-derivation keeps the district this run is set to convert.
        return filtered_catalog(
            stored_identity_domain(cfg),
            saved_sis=cfg.sis_type,
            picked_sis=selected["district"] or "",
        )

    catalog = _catalog()
    # D9: NO silent fallback — prefill only from a valid SAVED district; otherwise leave the
    # dropdown on its "Choose your district" placeholder and keep Run disabled until chosen.
    # (The old `configs[0]` alphabetical guess is gone.) The membership test now runs against
    # the VISIBLE ids, which is safe by construction: `filtered_catalog` carries the saved
    # district unconditionally when it exists, so scoping can never demote a valid saved
    # district to "unset" — and a saved id that names no real config still correctly fails
    # the test, exactly as it did against `available_configs()`.
    visible_ids = [s.sis_type for s in catalog.summaries]
    default_district: str | None = cfg.sis_type if cfg.sis_type in visible_ids else None
    selected["district"] = default_district

    # D10: capture the resolved output folder ONCE at build (screens rebuild fresh per
    # navigation, so a Settings change is picked up on the next visit). The gate + caption
    # + post-run "Open folder" row all read this one value — never a hidden input-dir fallback.
    output_dir_value = cfg.output_dir
    output_set = output_dir_is_set(output_dir_value)
    # 0035 W3b: the mode axis for the cold state — before setup completes, the unset-output
    # caption routes to the Setup WIZARD (there is no Settings scroll yet), and the screen
    # may lead with the routed "Finish setup first" card (pure decision below).
    setup_done = cfg.has_completed_setup()

    runner = JobRunner()
    # The run the on-screen anomaly card is asking about, or None when no card is up (FIX-2).
    # Held here (not in the card closure) because BOTH the interaction table and the ack
    # handler need it: the inputs freeze while it is set, and the re-run is launched from it.
    pending_ack: dict[str, RunIdentity | None] = {"identity": None}
    # The (result, identity) currently painted in `result_slot`, or None when the slot holds
    # something else (an error card, the anomaly question, nothing) — FIX-5. The result card
    # carries a deliver action whose readiness is DISTRICT-derived, so a mid-visit dropdown
    # change has to be able to re-render it; keeping the pair here is what makes that
    # possible without the screen re-deriving a result it no longer has.
    rendered: dict[str, tuple[ConvertResult, RunIdentity] | None] = {"value": None}

    def _deliver_district(app_cfg: AppConfig | None = None) -> str:
        """The district a deliver-from-disk action would use — ONE resolution, two readers.

        FIX-4: the readiness GATE and ``_start_deliver`` must key off the same district, or
        the screen offers a delivery derived from a config the action won't use. Both call
        THIS, so the expression can't drift. The per-run pick wins (a standalone delivery may
        have no pick — then the saved district), read from the PERSISTED config rather than
        the build-time snapshot so a Settings/Mapping change made in another surface during
        this visit is honoured, exactly as ``_start_deliver`` always did.

        ``app_cfg`` lets a caller that already loaded the config reuse it (one JSON read).
        """
        return (selected["district"] or (app_cfg or AppConfig.load()).sis_type or "").strip()

    # ------------------------------------------------------------------ #
    # District select — a "Choose your district" placeholder until chosen (D9).
    # ------------------------------------------------------------------ #
    def _district_options(cat) -> list[ft.dropdown.Option]:  # noqa: ANN001 - a FilteredCatalog
        # Labels via `disambiguated_labels`: two rows reading identically would make the
        # highest-consequence wrong click in this product a coin flip.
        labels = disambiguated_labels(cat.summaries)
        return [ft.dropdown.Option(key=s.sis_type, text=labels[s.sis_type]) for s in cat.summaries]

    district_dropdown = ft.Dropdown(
        label="District",
        value=default_district,
        hint_text="Choose your district",
        options=_district_options(catalog),
        width=340,
    )

    # The amber saved-vs-picked heads-up (0035 W3b): visible ONLY when the per-run pick
    # differs from the saved district (pure `district_mismatch_note` decides + words it).
    # Amber text on the white card is the same painted pair the unset-output caption uses.
    district_note = ft.Text("", size=13, color=tokens.color_status_warning, visible=False)

    def _refresh_district_note() -> None:
        note = district_mismatch_note(selected["district"], cfg.sis_type)
        district_note.value = note or ""
        district_note.visible = note is not None

    # Read-only pre-run visibility: where files will be written (or the routed blocked
    # message when no output folder is set — wizard-aware before setup completes).
    # Warning-toned when unset so the blocked state reads.
    output_caption = ft.Text(size=13)

    def _refresh_output_caption(*, refused: bool = False) -> None:
        """Paint the output-folder caption for the state now on screen (plan 0050).

        The ONE place the caption's value AND its tone are decided — the control above is
        constructed blank and this call (immediately below) does the initial paint, so the
        expression is never hand-repeated.

        The control is built ONCE and lives for the whole mount, so the caption is a
        long-lived ASSERTION: after a refusal it would otherwise keep promising "Files
        will be written to <folder>" in the same viewport as the band that just disproved
        it — and, inverted, a refusal's wording would survive onto the next SUCCESSFUL
        run. Four call sites cover every way the slot repaints: both job STARTS (so a
        refusal's wording does not ride through the next run's spinner), ``_render_result``
        on all of its branches, and both ``on_error`` handlers. (The anomaly-ack
        accept/cancel handlers do not — they are unreachable from a refusal, which
        returns a terminal status with no card to answer.)
        """
        output_caption.value = resolved_output_caption(output_dir_value, setup_completed=setup_done, refused=refused)
        output_caption.color = tokens.color_muted if (output_set and not refused) else tokens.color_status_warning

    _refresh_output_caption()

    # ------------------------------------------------------------------ #
    # File chips + missing-file warning (recomputed when the folder changes).
    # ------------------------------------------------------------------ #
    files_slot = ft.Column(spacing=10)
    deliver_slot = ft.Column(spacing=18)
    result_slot = ft.Column(spacing=18)
    convert_spinner = ft.ProgressRing(width=20, height=20, visible=False)
    convert_caption = ft.Text("", size=13, color=tokens.color_muted, visible=False)

    def _refresh_files() -> None:
        files_slot.controls = _build_file_chips(selected["district"], input_field.value)
        page.update()

    convert_btn = components.primary_button(
        "Convert now",
        lambda _e: _start_convert(),
        disabled=True,
        disabled_bgcolor=tokens.color_border,
        icon=ft.Icons.PLAY_ARROW_ROUNDED,
    )

    def _apply_interaction(*, job_running: bool) -> None:
        """Paint the pure ``interaction_state`` table (0035 W3b) onto the controls.

        The single place the busy/idle disabled flags land: the Convert button mirrors the
        ``JobRunner`` single-flight guard (no dead click can start a second job), and the
        district + input-folder controls lock while a job runs (the job snapshotted them
        at start — mid-run edits would desynchronize the form from the work in flight) or
        while an anomaly acknowledgement is pending (FIX-2 — the card asks about ONE run).
        """
        state = interaction_state(
            gates_ok=can_run_convert(
                district_chosen=bool(selected["district"]),
                output_dir_set=output_set,
                input_valid=validate_input_dir(input_field.value).ok,
            ),
            job_running=job_running,
            awaiting_ack=pending_ack["identity"] is not None,
        )
        convert_btn.disabled = state.convert_disabled
        district_dropdown.disabled = state.inputs_disabled
        input_field.disabled = state.inputs_disabled

    def _refresh_convert_gate() -> None:
        _apply_interaction(job_running=runner.state.is_running)
        page.update()

    def _on_input_change(_path: str, _result: object) -> None:
        _refresh_files()
        _refresh_convert_gate()

    input_field = PickerField(
        page=page,
        label="Input folder",
        helper="The folder that holds your MyEd BC extract files.",
        validator=validate_input_dir,
        on_change=_on_input_change,
        dialog_title="Select the folder with your MyEd BC extract files",
        initial_value=cfg.input_dir,
    )

    def _on_district_change(_e: ft.ControlEvent) -> None:
        selected["district"] = district_dropdown.value or default_district
        _refresh_district_note()  # the amber differs-from-saved heads-up follows the pick
        _refresh_header()  # ...and so does the header's "This run: <district>" pill (0038 S5)
        _refresh_files()
        # FIX-4: the deliver card's readiness is DISTRICT-derived (which CSVs on disk this
        # config would actually ship), so a pick change must re-gate it — otherwise the card
        # built for the previous district lingers and offers a delivery this one can't make.
        _refresh_deliver_slot()
        _refresh_convert_gate()  # D9: district is part of the run-gate now — re-check on pick

    district_dropdown.on_select = _on_district_change  # Dropdown value-change is on_select (0.85.3)

    # ------------------------------------------------------------------ #
    # Run + result rendering (all UI mutation on the loop, never the worker).
    # ------------------------------------------------------------------ #
    def _set_running(running: bool, *, caption: str = "Converting… this can take a moment for large extracts.") -> None:
        # `running or runner.state.is_running`: callers flag the transition BEFORE the
        # runner flips to RUNNING (start) and read the settled state after (done/error) —
        # either signal means "a job is in flight", and the pure table paints the rest.
        _apply_interaction(job_running=running or runner.state.is_running)
        convert_spinner.visible = running
        convert_caption.visible = running
        convert_caption.value = caption if running else ""

    def _start_convert(*, anomaly_ack: RunIdentity | None = None, sftp_requested: bool = False) -> None:
        """Launch a conversion for ONE identified run (FIX-2).

        The run's identity is resolved ONCE, here: an acknowledged re-run replays the exact
        ``(district, input folder)`` the admin reviewed, so the work in flight can never be
        a different run than the card described. A fresh run reads the live controls. Either
        way the identity is what the job receives AND what the result is rendered against —
        the old "snapshot it, then re-read the widgets anyway" split is gone.
        """
        identity = anomaly_ack or run_identity(selected["district"], input_field.value)
        pending_ack["identity"] = None  # a new job supersedes any card on screen
        _set_running(True)
        # The pre-run standalone deliver card retires once a job starts — from here the
        # result flow owns every deliver affordance (one affordance at a time, and the
        # card's build-time freshness fact can never go stale on screen).
        deliver_slot.controls = []
        result_slot.controls = []
        rendered["value"] = None
        _refresh_output_caption()  # a new run supersedes any previous refusal's wording
        page.update()

        def _on_done(result: ConvertResult) -> None:
            _set_running(False)
            _render_result(result, identity)
            page.update()

        def _on_error(exc: BaseException) -> None:
            # Privacy: the raw exception (may carry a path / column) is NEVER surfaced —
            # fixed category copy only (the raw error belongs to the log). The copy ends
            # with a concrete next step (0035 W3b — no dead-end failures). Plan 0053 S3: the
            # category is the exception's TYPE (`error_card_copy` → `classify_error_category`,
            # never `str(exc)`), so a missing column no longer reads as "check your input folder".
            #
            # "Belongs to the log" was aspirational until QA 2026-08-18 — nothing wrote it
            # there. The split is the whole point: the CARD stays category-only, and the
            # TRACE goes to the file the card's own "Open log folder" button opens.
            logger.error("Convert failed for district %r.", identity.district, exc_info=exc)
            _set_running(False)
            result_slot.controls = [components.ErrorCard(*error_card_copy(exc, delivery_requested=sftp_requested))]
            rendered["value"] = None
            _refresh_output_caption()  # a render path too: no refusal wording may survive here
            page.update()

        started = runner.run(
            page,
            lambda: convert_job(
                identity.district,
                identity.input_dir,
                anomaly_ack=anomaly_ack,
                sftp_requested=sftp_requested,
            ),
            on_done=_on_done,
            on_error=_on_error,
        )
        if not started:  # already running — the single-flight guard held (C4)
            _set_running(True)
            page.update()

    def _start_deliver(district: str) -> None:
        """Run ``deliver_job`` for ONE explicitly-named district — the ONE deliver code path.

        Deliver-from-disk (0034 Slice 2): the post-build card, the BUILT_NOT_DELIVERED
        retry, and the standalone card all land here. ``district`` is resolved ONCE by the
        caller and passed down (FIX-5) rather than re-read from the live dropdown here —
        the district names the ZIP that reaches SpacesEDU, so re-resolving it at this depth
        is how a mid-flight pick change could ship one district's roster under another's
        identity. Honest progress + a terminal verdict banner; a pre-upload failure (unset
        output folder) routes to the calm ``on_error`` card.
        """
        runner.state.reset()
        _set_running(True, caption="Delivering… sending your files to SpacesEDU.")
        deliver_slot.controls = []
        result_slot.controls = []
        rendered["value"] = None
        _refresh_output_caption()  # a new run supersedes any previous refusal's wording
        page.update()

        def _on_done(result: ConvertResult) -> None:
            # The delivery's own district — NOT the live dropdown: a BUILT_NOT_DELIVERED
            # result renders a RETRY, and that retry must inherit this delivery's binding.
            _set_running(False)
            _render_result(result, run_identity(district, input_field.value))
            page.update()

        def _on_error(exc: BaseException) -> None:
            # Privacy: the raw exception is NEVER surfaced — fixed category copy only,
            # ending with a concrete next step (0035 W3b — no dead-end failures). The trace
            # goes to the log (QA 2026-08-18), same split as the convert path above.
            logger.error("Delivery failed for district %r.", district, exc_info=exc)
            _set_running(False)
            result_slot.controls = [components.ErrorCard(*deliver_error_copy())]
            rendered["value"] = None
            _refresh_output_caption()  # a render path too: no refusal wording may survive here
            page.update()

        started = runner.run(page, lambda: deliver_job(district), on_done=_on_done, on_error=_on_error)
        if not started:  # already running — the single-flight guard held (C4)
            _set_running(True, caption="Delivering… sending your files to SpacesEDU.")
            page.update()

    def _confirm_and_deliver(district: str) -> None:
        """The deliver CHOKE POINT: every deliver action passes through here (FIX-5).

        A SEPARATE explicit action — never an auto-run side effect. Names the SFTP host
        ONCE and the resolved local output folder (config values, never a secret), plus
        the honest vintage of what would ship ("Files last built …", from the newest
        on-disk CSV's mtime). On Deliver, runs ``deliver_job`` — uploads the committed
        files from disk, NEVER a re-transform (the old rebuild path, with its hardcoded
        ``anomaly_ack=True`` bypass, is gone). The exit-3 result renders the FAILED
        "built but not delivered" verdict from booleans (never ``on_error``).

        **The last gate, and the freshness line's precondition.** ``district`` is resolved
        by the CALLER (the run's identity for the result card; the live pick for the
        standalone card) and this function re-derives ``deliverable_files`` for it. An
        EMPTY set never reaches the confirm dialog: the vintage line would otherwise assert
        "Files last built …" for files that do not exist, and ``upload_csvs`` would refuse
        the empty manifest with a ``RuntimeError`` mislabelled as an upload failure and
        persisted as a FAILED delivery record the admin could never clear. Both cards
        already gate on the same fact — this is defence in depth against the render→click
        race AND the guarantee any FUTURE deliver entry point inherits for free.
        """
        cfg = AppConfig.load()
        local_folder = (cfg.output_dir or "").strip()
        # The set that WOULD SHIP — the narrowed set, not the folder's newest *.csv
        # (a parked spreadsheet must never be quoted as the roster's build time — FIX-4).
        files = deliverable_files(district, local_folder)
        if not files.present:
            _show_nothing_to_deliver(district)
            return
        vintage = freshness_fact(files.newest_mtime_iso)

        def _on_deliver(_e: ft.ControlEvent) -> None:
            page.pop_dialog()
            _start_deliver(district)

        def _on_cancel(_e: ft.ControlEvent) -> None:
            page.pop_dialog()

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Deliver to SpacesEDU?"),
                content=ft.Column(
                    tight=True,
                    spacing=8,
                    controls=[
                        ft.Text(f"Server: {cfg.sftp_host}", size=13, color=tokens.color_text),
                        ft.Text(f"Folder: {local_folder}", size=13, color=tokens.color_text),
                        ft.Text(vintage, size=13, color=tokens.color_muted),
                    ],
                ),
                actions=[
                    components.text_button("Cancel", _on_cancel),
                    components.primary_button(
                        "Deliver",
                        _on_deliver,
                        icon=ft.Icons.CLOUD_UPLOAD_ROUNDED,
                    ),
                ],
            )
        )

    def _show_nothing_to_deliver(district: str) -> None:
        """The choke point's refusal dialog — a plain reason and a Close, never a Deliver.

        Deliberately a DIALOG (not an inline card): the admin just clicked a button and is
        owed an immediate answer in the place they were looking. It states what could not
        be sent and why in the admin's own words (``nothing_to_deliver_copy``), and offers
        no delivery action at all — there is nothing to deliver, so offering one would be
        the dead end this whole fix removes.
        """
        title, body = nothing_to_deliver_copy(district)

        def _on_close(_e: ft.ControlEvent) -> None:
            page.pop_dialog()

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(title),
                content=ft.Column(
                    tight=True,
                    spacing=8,
                    controls=[ft.Text(body, size=13, color=tokens.color_text)],
                ),
                actions=[components.text_button("Close", _on_close)],
            )
        )

    def _render_result(result: ConvertResult, identity: RunIdentity) -> None:
        """Paint one run's result — every affordance bound to the run it describes (FIX-5).

        ``identity`` is the run this card is ABOUT, and it is the only district the card's
        deliver action will ever use. Re-rendering with the same pair is also how a
        mid-visit district change re-gates the card (``_refresh_deliver_slot``), so the
        pair is remembered in ``rendered`` for exactly as long as it is on screen.
        """
        rendered["value"] = None
        # Set on EVERY branch below, not just the refusal one — see `_refresh_output_caption`.
        _refresh_output_caption(refused=result.status is ConvertStatus.OUTPUT_FOLDER_UNUSABLE)
        if result.status is ConvertStatus.NEEDS_ANOMALY_ACK:
            # The card is a question about THIS run — remember which one, and freeze the
            # inputs while it waits so the answer can't drift onto another (FIX-2). The
            # frozen dropdown is also why this branch needs no re-gate: the pick cannot move.
            pending_ack["identity"] = identity
            result_slot.controls = [_anomaly_ack_card(result, identity)]
            _apply_interaction(job_running=False)
            return
        verdict, headline, detail = summarize(result)
        # The ONE routed fix this banner offers (plan 0050): DESIGN_SYSTEM principle 5
        # asks a failed band for the concrete fix, and the output folder's fix is Settings.
        # The TEXT tier, painted `color_on_failed_tint` — the band's own AA-gated on-tint
        # colour (8.77:1). NOT filled: principle 2 (one filled primary per screen) outranks
        # principle 5's "filled", and this screen's primary is "Convert now", the re-run.
        # NOT outlined either: an outlined border on the failed tint measures 1.58:1, under
        # WCAG 2.2 SC 1.4.11's 3:1 for a component boundary, and `secondary_button` is not
        # one of the two tiers the banner's `trailing` slot sanctions. Gated on
        # `on_navigate` (the shell injects it): absent ⇒ no affordance, never a dead one —
        # the copy's "Check the output folder in Settings" stands alone without it.
        # Labelled for the RAIL ITEM ("Setup"), never "Settings", which the rail lacks.
        trailing: ft.Control | None = None
        if result.status is ConvertStatus.OUTPUT_FOLDER_UNUSABLE and on_navigate is not None:
            trailing = components.text_button(
                "Open Setup",
                lambda _e: on_navigate("setup"),
                color=tokens.color_on_failed_tint,
            )
        controls: list[ft.Control] = [
            components.HealthVerdictBanner(verdict, headline=headline, detail=detail, trailing=trailing),
        ]
        if result.entity_counts:
            controls.append(_entity_tiles_row(result.entity_counts))
        if result.quality_text:
            controls.append(_quality_expander(result.quality_text))
        # D10 post-run visibility: a committed run names WHERE its files are + an "Open folder"
        # button. The path is app-owned config (never PII) → view layer only; `ConvertResult`
        # stays path-free. `output_set` guards the (unreachable) empty-output case defensively.
        if result.status in _WROTE_OUTPUT and output_set:
            controls.append(_output_folder_row(output_dir_value))
        # SFTP delivery action: shown when SFTP is configured AND either (a) a successful
        # local build hasn't been delivered yet, or (b) a delivery FAILED
        # (BUILT_NOT_DELIVERED) and can be retried — the exit-3 banner's copy promises
        # exactly this retry, so the failure screen must offer it (no forced rebuild).
        # A DELIVERED / DELIVERED_WITH_DATA_ERRORS run (sftp_attempted=True) is already
        # delivered, so it is NOT offered again.
        #
        # FIX-5: the offer is gated by the pure `result_deliver_state`, keyed on THIS RUN's
        # district — the files it built, and the only identity they may ever ship under.
        # `deliverable_files` narrows to what `deliver_job` would actually send (never a
        # bare folder glob), and the pick comparison catches the drift the old status-only
        # gate could not see.
        sftp_cfg = AppConfig.load()
        retry_delivery = result.status is ConvertStatus.BUILT_NOT_DELIVERED
        offerable = retry_delivery or (
            result.status in (ConvertStatus.DELIVERED, ConvertStatus.BUILT_WITH_DATA_ERRORS)
            and not result.sftp_attempted
        )
        configured = sftp_cfg.sftp_is_configured()
        # Only derived when it can matter — a config load + a directory read are cheap, but
        # an unoffered card should cost neither.
        files_present = offerable and configured and deliverable_files(identity.district, output_dir_value).present
        state = result_deliver_state(
            offerable=offerable,
            sftp_configured=configured,
            files_present=files_present,
            district_matches_pick=identity.district == _deliver_district(sftp_cfg),
            # Deliver-gate (0031): SFTP is configured, but delivery ALSO needs a stored
            # password for THIS Windows account. Present → the deliver/retry card; absent
            # or unreadable → a calm "route to Setup" card instead (no transient password
            # entry in Convert — Setup is the single credential home). Probed only when the
            # earlier, cheaper facts already say the card is on offer (kwargs evaluate
            # eagerly, so the guard lives here, not in the gate's branch order).
            credential_present=files_present and _sftp_credential_present(sftp_cfg),
        )
        if state is ResultDeliverReadiness.READY:
            controls.append(_deliver_action(identity.district, retry=retry_delivery))
        elif state is ResultDeliverReadiness.DISTRICT_CHANGED:
            controls.append(_district_changed_card(identity.district))
        elif state is ResultDeliverReadiness.NEEDS_CREDENTIAL:
            controls.append(_delivery_not_ready_card())
        result_slot.controls = controls
        rendered["value"] = (result, identity)

    def _deliver_action(district: str, *, retry: bool = False) -> ft.Control:
        """The result card's deliver / retry action, BOUND to ``district`` (FIX-5).

        The district is captured in the closure at RENDER time — not re-read from the
        dropdown on click — so this button can only ever deliver the run it belongs to.
        Rostering districts share entity filenames (``sd74myedbc`` and ``sd40myedbc`` write
        the same five CSVs), so a click-time re-read did not fail loudly: it succeeded, and
        shipped one district's students under the other's zip name.
        """
        heading = "Try delivering again" if retry else "Deliver to SpacesEDU"
        body = (
            "The upload didn't go through. Your files are saved — send them to SpacesEDU again."
            if retry
            else "Your roster is built and saved. Send it to SpacesEDU when you're ready."
        )
        return components.card(
            content=ft.Column(
                spacing=12,
                controls=[
                    ft.Text(heading, size=15, weight=ft.FontWeight.W_700, color=tokens.color_text),
                    ft.Text(body, size=13, color=tokens.color_muted),
                    ft.Row(
                        controls=[
                            components.secondary_button(
                                heading,
                                lambda _e: _confirm_and_deliver(district),
                                icon=ft.Icons.CLOUD_UPLOAD_ROUNDED,
                            )
                        ]
                    ),
                ],
            ),
        )

    def _district_changed_card(run_district: str) -> ft.Control:
        """Replaces the deliver action once the pick has moved off the run's district (FIX-5).

        NOT a silent disappearance (that is the dead end the trust bar forbids) and NOT a
        live button under a dropdown naming someone else (that is an assertion nobody
        checked). A plain statement of what happened plus both ways forward — switch the
        district back, or convert for the one now picked — each a control already on screen,
        so the card needs no action of its own.
        """
        title, body = result_district_changed_copy(run_district)
        return components.card(
            content=ft.Column(
                spacing=12,
                controls=[
                    ft.Text(title, size=15, weight=ft.FontWeight.W_700, color=tokens.color_text),
                    ft.Text(body, size=13, color=tokens.color_muted),
                ],
            ),
        )

    def _anomaly_ack_card(result: ConvertResult, identity: RunIdentity) -> ft.Control:
        verdict, headline, detail = summarize(result)
        anomaly_lines = [ft.Text(f"• {line}", size=13, color=tokens.color_text) for line in result.anomalies]

        def _on_ack(_e: ft.ControlEvent) -> None:
            # A fresh run carrying the acknowledgement TOKEN for the run that was reviewed
            # (FIX-2) — re-transforms (one path, no PII frames held). `_start_convert` replays
            # that identity rather than re-reading the controls, and `convert_job` honours the
            # token only when it matches the run it is executing.
            runner.state.reset()
            result_slot.controls = []
            rendered["value"] = None
            page.update()
            _start_convert(anomaly_ack=identity)

        def _on_cancel(_e: ft.ControlEvent) -> None:
            runner.state.reset()
            pending_ack["identity"] = None  # question withdrawn — the inputs are editable again
            rendered["value"] = None
            result_slot.controls = [
                ft.Text(
                    "No files were written. Review your input, then convert again when you're ready.",
                    size=13,
                    color=tokens.color_muted,
                )
            ]
            _refresh_convert_gate()

        return components.card(
            content=ft.Column(
                spacing=16,
                controls=[
                    components.HealthVerdictBanner(verdict, headline=headline, detail=detail),
                    ft.Column(spacing=4, controls=anomaly_lines),
                    ft.Text(
                        "Nothing has been written yet. If this drop is expected, you can convert anyway.",
                        size=13,
                        color=tokens.color_muted,
                    ),
                    ft.Row(
                        spacing=14,
                        controls=[
                            components.secondary_button(
                                "I've reviewed this — convert anyway",
                                _on_ack,
                                icon=ft.Icons.CHECK_ROUNDED,
                            ),
                            components.text_button("Cancel", _on_cancel),
                        ],
                    ),
                ],
            ),
        )

    def _standalone_deliver_card(files: DeliverableFiles) -> ft.Control:
        """The pre-run "Deliver the files in your output folder" card (0034 Slice 2).

        The deliver-what's-on-disk affordance — also the post-navigation retry path after
        a failed delivery (screens rebuild per visit, so the in-result retry card doesn't
        survive navigation; this one re-derives from disk every visit). Carries the honest
        freshness fact so the admin always knows the vintage of what would ship. Secondary
        tier — "Convert now" stays the screen's one filled primary.

        ``files`` is the SAME set the gate approved (FIX-4) — passed in rather than
        re-derived, so the card can't describe a different vintage than the one that
        earned it the READY state.
        """
        fact = freshness_fact(files.newest_mtime_iso)
        return components.card(
            content=ft.Column(
                spacing=12,
                controls=[
                    ft.Text(
                        "Deliver the files in your output folder",
                        size=15,
                        weight=ft.FontWeight.W_700,
                        color=tokens.color_text,
                    ),
                    ft.Text(
                        "Send the roster files already saved in your output folder to SpacesEDU — nothing is rebuilt.",
                        size=13,
                        color=tokens.color_muted,
                    ),
                    ft.Text(fact, size=13, color=tokens.color_muted),
                    ft.Row(
                        controls=[
                            components.secondary_button(
                                "Deliver to SpacesEDU",
                                # Unlike the result card (bound to the run that BUILT the
                                # files), this card means "send what's in the folder as the
                                # district currently picked" — there is no prior run to bind
                                # to, so the pick is resolved at click. `_refresh_deliver_slot`
                                # re-gates it on every change and `_confirm_and_deliver` is the
                                # choke point behind a click that races that re-render.
                                lambda _e: _confirm_and_deliver(_deliver_district()),
                                icon=ft.Icons.CLOUD_UPLOAD_ROUNDED,
                            )
                        ]
                    ),
                ],
            ),
        )

    def _standalone_deliver_controls() -> list[ft.Control]:
        """Render the pure ``standalone_deliver_state`` gate: hidden / not-ready / the card.

        The readiness fact is ``deliverable_files(...).present`` — the district-narrowed set
        ``deliver_job`` would actually ship, for the district ``_start_deliver`` will actually
        use (FIX-4). Offering the action off a bare folder glob rendered a READY card whose
        click could only fail, mislabelled as an upload failure and recorded as one.

        The keyring probe (``_sftp_credential_present``) runs only when delivery is
        configured AND something is deliverable — never a pointless credential read on an
        unconfigured install. The config load + disk facts are cheap render-path reads and
        TOTAL (a broken partner config degrades to "nothing to deliver", never a crashed
        screen); the SFTP CONNECT stays strictly on the ``JobRunner`` worker.
        """
        sftp_cfg = AppConfig.load()
        configured = sftp_cfg.sftp_is_configured()
        files = deliverable_files(_deliver_district(sftp_cfg), output_dir_value)
        state = standalone_deliver_state(
            sftp_configured=configured,
            credential_present=configured and files.present and _sftp_credential_present(sftp_cfg),
            csvs_present=files.present,
        )
        if state is DeliverReadiness.READY:
            return [_standalone_deliver_card(files)]
        if state is DeliverReadiness.NEEDS_CREDENTIAL:
            return [_delivery_not_ready_card()]
        return []

    def _refresh_deliver_slot() -> None:
        """Re-gate WHICHEVER deliver affordance is on screen for the current pick (FIX-4/FIX-5).

        Exactly one deliver affordance exists at a time, and this is the single place a
        district change re-derives it:

        * a run flow owns the slot (``result_slot`` non-empty) → re-render THAT result with
          its own unchanged identity. The standalone slot stays suppressed (two deliver
          buttons on screen would be worse than none), and the result card re-gates itself:
          same district ⇒ same card, moved-on ⇒ the "you changed district" explanation.
          Skipping this (FIX-4 returned here) left the post-run card — the DEFAULT post-run
          state — as the one deliver route no pick change could touch.
        * otherwise → the standalone pre-run card, re-derived from disk for the new pick.

        A run in flight or a pending acknowledgement can't reach here at all
        (``interaction_state`` disables the dropdown in both), so this only ever repaints a
        settled view.
        """
        if result_slot.controls:
            pending = rendered["value"]
            if pending is not None:
                _render_result(*pending)
            return
        deliver_slot.controls = _standalone_deliver_controls()

    # Direction B page header (0033 Slice 2): the gradient hero demotes to a slim header; the
    # saved district identity rides in the header's right slot as a ``district_chip`` (the
    # per-run selection stays the dropdown below — the chip reflects the configured district).
    #
    # 0038 S5 closes the P1 leftover beside it: the chip alone asserted the SAVED district
    # while the run would use the PICKED one, so the header described a different conversion
    # from the one the button below would start. A "This run: <district>" pill joins it on
    # DIVERGENCE only, with a text-tier hop to Mapping (where a saved district is changed for
    # good). Multi-control trailing follows Home's header precedent.
    #
    # It is a LABEL, not a gate: `can_run_convert` is untouched, and no path here can block
    # a conversion the admin has explicitly set up.
    header_trailing = ft.Row(
        spacing=tokens.space_md,
        tight=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[],
    )

    def _refresh_header() -> None:
        controls: list[ft.Control] = []
        if default_district:
            controls.append(components.district_chip(friendly_district_name(default_district)))
        run_label = this_run_label(selected["district"], cfg.sis_type)
        if run_label is not None:
            controls.append(components.status_pill(f"This run: {run_label}", Verdict.WARNING))
            if on_navigate is not None:
                controls.append(components.text_button("Change mapping", lambda _e: on_navigate("mapping")))
        header_trailing.controls = controls

    _refresh_district_note()
    _refresh_header()
    _refresh_files()
    _refresh_convert_gate()
    _refresh_deliver_slot()

    header = components.page_header(
        "Convert",
        "Build your roster now from your MyEd BC extract files",
        trailing=header_trailing,
    )

    form = components.card(
        content=ft.Column(
            spacing=20,
            controls=[
                district_dropdown,
                district_note,
                input_field,
                files_slot,
                output_caption,
                ft.Row(
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[convert_btn, convert_spinner, convert_caption],
                ),
            ],
        ),
    )

    # Pre-setup cold state (0035 W3b): when the run essentials are missing AND setup never
    # completed, lead with the calm routed card — the fix lives in Setup, not in a disabled
    # button. The form stays rendered beneath (a partially-set-up install remains usable).
    top: list[ft.Control] = [header]
    if show_setup_first_card(
        setup_completed=setup_done,
        output_dir_set=output_set,
        district_saved=default_district is not None,
    ):
        top.append(_setup_first_card(on_navigate))

    return ft.Column(spacing=22, controls=[*top, form, deliver_slot, result_slot])


# --------------------------------------------------------------------------- #
# View helpers                                                                  #
# --------------------------------------------------------------------------- #
# Convert statuses whose run COMMITTED files to disk → "Open folder" is meaningful.
# NO_INPUT / NO_OUTPUT wrote nothing; NEEDS_ANOMALY_ACK is handled before this point.
_WROTE_OUTPUT: frozenset[ConvertStatus] = frozenset(
    {
        ConvertStatus.DELIVERED,
        ConvertStatus.DELIVERED_WITH_DATA_ERRORS,
        ConvertStatus.BUILT_WITH_DATA_ERRORS,
        ConvertStatus.BUILT_NOT_DELIVERED,
    }
)


def _sftp_credential_present(cfg: AppConfig) -> bool:  # pragma: no cover - Flet view glue
    """Whether a delivery password is stored + readable for the saved host/user on THIS account.

    Building the uploader also re-validates the host against ``ALLOWED_SFTP_HOSTS`` (a
    belt-and-suspenders check on top of ``sftp_is_configured``). ANY failure — a disallowed
    host, an unreadable keyring — reads as "no credential" (defensive, never raises): the view
    then routes to Setup instead of offering a deliver button that would immediately fail.
    Mirrors ``screens/setup._stored_delivery_present``.
    """
    try:
        uploader = SFTPUploader(
            cfg.sftp_host,
            int(cfg.sftp_port or 22),
            cfg.sftp_username,
            cfg.sftp_remote_path,
        )
        return bool(uploader.get_stored_password())
    except Exception:  # noqa: BLE001 - any construction/keyring failure → treat as no credential
        # WARN, not ERROR: the fallback is defined and safe ("no credential"). But it is also
        # INVISIBLE — the screen simply offers no delivery — so an unreadable keyring would
        # otherwise look identical to one that was never set up.
        logger.warning("Could not read the stored delivery credential; treating it as absent.", exc_info=True)
        return False


def _setup_first_card(on_navigate: Callable[[str], None] | None) -> ft.Control:  # pragma: no cover - Flet view glue
    """The calm pre-setup "Finish setup first" card — routed, never a dead end (0035 W3b).

    Copy is the pure ``setup_first_copy`` pair; the "Open Setup" action renders only when
    the shell injected ``on_navigate`` (Home's pattern) — without it the body still tells
    the admin where to go. Secondary tier: "Convert now" keeps the screen's one filled
    primary even while gated.
    """
    title, body = setup_first_copy()
    rows: list[ft.Control] = [
        ft.Text(title, size=15, weight=ft.FontWeight.W_700, color=tokens.color_text),
        ft.Text(body, size=13, color=tokens.color_muted),
    ]
    if on_navigate is not None:
        rows.append(
            ft.Row(
                controls=[
                    components.secondary_button(
                        "Open Setup",
                        lambda _e: on_navigate("setup"),
                        icon=ft.Icons.ARROW_FORWARD_ROUNDED,
                    )
                ]
            )
        )
    return components.card(content=ft.Column(spacing=12, controls=rows))


def _delivery_not_ready_card() -> ft.Control:  # pragma: no cover - Flet view glue
    """Calm info card: SFTP is configured but no password is stored on this Windows account.

    Delivery is one credential away — route the admin to Setup rather than block the build
    (rendered above, untouched) or offer a deliver button that would fail. No button: Setup
    is one rail-click away. Shown both post-build and as the standalone deliver card's
    gated state (0034 Slice 2), so the copy routes back HERE to deliver, not to a rebuild.
    """
    return components.card(
        content=ft.Column(
            spacing=12,
            controls=[
                ft.Text(
                    "Delivery isn't ready on this account",
                    size=15,
                    weight=ft.FontWeight.W_700,
                    color=tokens.color_text,
                ),
                ft.Text(
                    "SFTP delivery is set up, but no password is stored for this Windows account. "
                    "Add it in Setup → SFTP delivery, then come back here to deliver.",
                    size=13,
                    color=tokens.color_muted,
                ),
            ],
        ),
    )


def _output_folder_row(output_dir: str) -> ft.Control:  # pragma: no cover - Flet view glue
    """A view-layer row naming the resolved output folder + an "Open folder" button (D10).

    The output path is app-owned config (never student PII), so it lives HERE at the view
    layer and never enters the PII-free ``ConvertResult``. "Open folder" dispatches per-OS
    via ``convert_output.open_folder`` (best-effort, never raises).
    """
    return components.card(
        content=ft.Column(
            spacing=12,
            controls=[
                ft.Text("Where your files are", size=15, weight=ft.FontWeight.W_700, color=tokens.color_text),
                ft.Text(output_dir, size=13, color=tokens.color_muted, selectable=True),
                ft.Row(
                    controls=[
                        components.secondary_button(
                            "Open folder",
                            lambda _e: open_folder(output_dir),
                            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                        )
                    ]
                ),
            ],
        ),
    )


def _build_file_chips(config_name: str | None, input_dir: str) -> list[ft.Control]:  # pragma: no cover - Flet view glue
    """FileChips for the GDE files found in the folder + a missing-file warning.

    Lists the resolved GDE files present in the picked folder and, from
    ``advisory_expected_files``, any expected-but-absent file (a plain amber chip +
    a one-line warning). A bad config / folder degrades to an empty list, never a
    crash.
    """
    present = _present_gde_files(input_dir)
    expected = _expected_files(config_name)

    controls: list[ft.Control] = []
    if present:
        controls.append(ft.Text("Files found", size=13, weight=ft.FontWeight.W_700, color=tokens.color_text))
        controls.append(ft.Row(spacing=10, wrap=True, controls=[components.FileChip(name) for name in present]))

    # Case-INSENSITIVE, matching what the extractor actually does on disk. A district's
    # `students.txt` against a mapping's `Students.txt` was reported here as missing while
    # the ETL loaded it perfectly well — a false alarm on the screen an admin uses to decide
    # whether their extract is complete. The MAPPING's spelling is what gets listed, since
    # that is the name to fix if the file really is absent.
    present_folded = {name.lower() for name in present}
    missing = [f for f in expected if f.lower() not in present_folded]
    if missing:
        # Softened copy (0035 W3b): a missing source file is legitimate (per-entity
        # skip-on-empty), so the heading observes calmly and the muted reassurance line
        # states the honest consequence — pure `missing_files_copy` owns the words.
        heading, reassurance = missing_files_copy()
        controls.append(
            ft.Text(
                heading,
                size=13,
                weight=ft.FontWeight.W_700,
                color=tokens.color_status_warning,
            )
        )
        controls.append(
            ft.Row(spacing=10, wrap=True, controls=[components.FileChip(name, present=False) for name in missing])
        )
        controls.append(ft.Text(reassurance, size=13, color=tokens.color_muted))
    return controls


def _present_gde_files(input_dir: str) -> list[str]:  # pragma: no cover - Flet view glue
    """Sorted GDE filenames present in the folder (empty on a bad/empty folder)."""
    if not input_dir:
        return []
    try:
        entries = sorted(Path(input_dir).iterdir())
    except OSError:
        return []
    return [e.name for e in entries if e.is_file() and e.suffix.lower() in _GDE_SUFFIXES]


def _expected_files(config_name: str) -> list[str]:  # pragma: no cover - Flet view glue
    """The config's advisory "usually include" source files (empty on any config
    error — never crashes). Deliberately NOT ``extract_required_files`` — that one
    is what the extractor actually loads and must stay grade-scope-agnostic; this
    is UI-only and narrows further for a fully homeroom-scoped district."""
    try:
        return advisory_expected_files(load_config(config_name))
    except Exception:  # noqa: BLE001 - a config error degrades to "no expectation", never a crash
        logger.warning(
            "Could not read the district mapping %r; the expected-files chips are omitted.",
            config_name,
        )
        return []


def _entity_tiles_row(entity_counts: dict[str, int]) -> ft.Control:  # pragma: no cover - Flet view glue
    """A row of metric tiles for the produced entities (reuses ``components.metric_tile``)."""
    tiles = [
        components.metric_tile(ENTITY_LABELS.get(name, name), f"{count:,}") for name, count in entity_counts.items()
    ]
    return ft.Row(spacing=16, wrap=True, controls=tiles)


def _quality_expander(quality_text: str) -> ft.Control:  # pragma: no cover - Flet view glue
    """A collapsible data-quality report (one existing ``DataQualityReport`` call)."""
    return ft.ExpansionTile(
        title=ft.Text("Data quality report", size=14, weight=ft.FontWeight.W_700, color=tokens.color_text),
        controls=[
            ft.Container(
                padding=_pad_sym(16, 12),
                content=ft.Text(quality_text, size=12, color=tokens.color_text, selectable=True),
            )
        ],
    )
