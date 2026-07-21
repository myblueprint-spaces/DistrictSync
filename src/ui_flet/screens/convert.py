"""Convert surface — the admin's manual "run it now" flow (IA model IA-5).

VIEW glue (coverage-omitted): the trust-critical logic lives COUNTED in the pure
modules — ``job_runner`` (the single-flight state machine + the ``SystemExit``/
``Exception`` routing seam) and ``convert_result`` (``ConvertResult`` +
``summarize``). This file wires them to controls + holds the thin ``convert_job``
orchestration the ``JobRunner`` runs off the UI thread.

**The adapter, not ``run_pipeline`` (parity lock):** ``convert_job`` mirrors the
Streamlit ``02_Convert.run_conversion`` + the parity test's ``_run_ui_path`` —
``load_config → to_raw_dict → load_from_bytes → run_transform → save_all`` — so the
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
never the raw exception / host / path (privacy). A ``save_all`` / ``load_config``
failure in the earlier steps still PROPAGATES to ``on_error`` (fail-loud); the upload
catch never widens over the build. A failed delivery never rolls back the build —
the files stay written and the admin can retry.

**Deliver from disk (0034 Slice 2):** EVERY deliver action — the post-build card, the
BUILT_NOT_DELIVERED retry, and the standalone "Deliver the files in your output folder"
card — runs ``deliver_job``, which uploads the ALREADY-COMMITTED top-level output CSVs
(``save_all`` is atomic, so that set is never torn) and NEVER re-transforms. A
between-build-and-deliver input change therefore cannot alter what ships, and the
anomaly write-gate is untouched (it guards WRITES; a delivery writes nothing — the old
deliver-by-rebuild's hardcoded ``anomaly_ack=True`` bypass is gone with the rebuild).
The confirm dialog names the server + local folder once and carries the honest
freshness fact ("Files last built …", from the newest on-disk CSV's mtime).

**Cold-state + interaction sweep (0035 W3b):** pre-setup, the screen leads with the
routed "Finish setup first" card (pure ``show_setup_first_card``/``setup_first_copy``;
``on_navigate`` injected by the shell, defensive without it); the unset-output caption is
mode-aware (wizard vs Settings); an amber ``district_mismatch_note`` flags a per-run pick
that differs from the saved district; and every busy/idle disabled flag paints the pure
``interaction_state`` table (inputs lock while a job runs — no dead clicks, no
double-start, no mid-run edits). The ``on_error`` cards render the fixed
``convert_error_copy``/``deliver_error_copy`` pairs, each ending with a concrete next step.

**Write-in-flight close guard (IA-5b, C6):** a module-level flag
(``_WRITE_IN_FLIGHT``) is set immediately before ``save_all`` and cleared in a
``finally``; ``is_write_in_flight()`` exposes it for ``shell._on_leave``. It is
REASSURANCE-ONLY — the loader's backup-and-restore ``save_all`` atomicity is the real
safety net (a torn commit rolls back); ``_on_leave`` reads the flag but does NOT block
the atomic close.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from pathlib import Path

import flet as ft

from src.config.app_config import AppConfig
from src.config.loader import available_configs, load_config
from src.etl.extractor import DataExtractor
from src.etl.loader import DataLoader
from src.etl.pipeline import (
    RunErrorCategory,
    build_run_record,
    check_delivery_integrity,
    compute_anomalies,
    configured_entity_order,
    extract_required_files,
    run_transform,
)
from src.history.store import write_run_record
from src.quality.report import DataQualityReport
from src.sftp.uploader import SFTPUploader
from src.ui_flet import components, tokens
from src.ui_flet.convert_output import (
    DeliverableFiles,
    DeliverReadiness,
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
    open_folder,
    output_dir_is_set,
    resolved_output_caption,
    run_identity,
    setup_first_copy,
    show_setup_first_card,
    standalone_deliver_state,
)
from src.ui_flet.convert_result import (
    ConvertResult,
    ConvertStatus,
    convert_error_copy,
    deliver_error_copy,
    status_for_integrity_fault,
    summarize,
)
from src.ui_flet.filepicker import validate_input_dir
from src.ui_flet.home_status import ENTITY_LABELS
from src.ui_flet.humanize import friendly_district_name
from src.ui_flet.job_runner import JobRunner
from src.ui_flet.picker_field import PickerField

# GDE files are CSV or TXT (varies by district).
_GDE_SUFFIXES: tuple[str, ...] = (".csv", ".txt")

# Write-in-flight flag (C6): True while an atomic `save_all` is committing. Read by
# `shell._on_leave` (reassurance-only — the loader's atomicity is the real net; the
# flag never blocks the close). A plain module-level bool: the ETL is single-flight
# (the JobRunner's state machine), so no lock is needed for this reassurance read.
_WRITE_IN_FLIGHT: bool = False


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
    ``load_config → load_from_bytes → run_transform → check_delivery_integrity →
    compute_anomalies`` and — only when clear or acknowledged — ``save_all`` +
    stale-output archival + the quality report, then (only when ``sftp_requested``)
    an SFTP delivery.

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
    (fail-loud). The SFTP leg is the ONLY step whose failure is caught in-job: the
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
        raise ValueError("No output folder is configured — set one in Settings before converting.")
    output_dir = Path(output_dir_value)

    raw = config.to_raw_dict()
    mappings = raw.get("mappings", {})
    global_config = raw.get("global_config", {})

    # Collect explicit headers for headerless files (exactly as run_conversion does).
    file_headers: dict[str, list[str]] = {}
    for entity_cfg in mappings.values():
        for filename, header_list in entity_cfg.get("headers", {}).items():
            file_headers[filename] = header_list

    # Read the GDE files' bytes from the picked folder, keyed by filename so config
    # source_files resolve identically to a disk / CLI run.
    sources = _read_gde_bytes(Path(input_dir))
    raw_data = DataExtractor("").load_from_bytes(sources, file_headers)
    if not raw_data:
        return ConvertResult(status=ConvertStatus.NO_INPUT)

    outputs, field_orders, data_errors = run_transform(raw_data, mappings, global_config)

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
        )
        _record_manual_run(
            refused,
            sis_type=config_name,
            elapsed=time.monotonic() - t0,
            status="failed",
            error_category=integrity_fault.category,
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
        )

    # Atomic write (raises ValueError on a missing field-map column → on_error).
    # The write-in-flight flag (C6) is raised around the commit ONLY — the loader's
    # backup-and-restore atomicity is the real net; the flag is reassurance for
    # `shell._on_leave`. A `save_all` failure PROPAGATES (fail-loud), and the flag
    # is cleared in the `finally` either way.
    loader = DataLoader(str(output_dir))
    global _WRITE_IN_FLIGHT
    _WRITE_IN_FLIGHT = True
    try:
        loader.save_all(outputs, field_orders)
    finally:
        _WRITE_IN_FLIGHT = False

    # Archive (non-destructive) entity CSVs left in the output dir that this run
    # did NOT produce — mirrors run_pipeline: a stale CSV must never ship in an
    # SFTP zip (the delivery leg below, or a later deliver-from-disk, globs the
    # top-level *.csv set). Moving them into archive_<ts>/ (a SUBfolder) excludes
    # them without deleting anything; best-effort — never fails a committed build.
    loader.archive_stale_outputs(set(outputs))

    quality_text = DataQualityReport().analyze(outputs).to_text()
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
            built_not_delivered = ConvertResult(
                status=ConvertStatus.BUILT_NOT_DELIVERED,
                entity_counts=entity_counts,
                data_errors_total=errors_total,
                sftp_attempted=True,
                sftp_ok=False,
                quality_text=quality_text,
            )
            _record_manual_run(built_not_delivered, sis_type=config_name, elapsed=time.monotonic() - t0)
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
        )
        _record_manual_run(delivered, sis_type=config_name, elapsed=time.monotonic() - t0)
        return delivered

    status = ConvertStatus.BUILT_WITH_DATA_ERRORS if errors_total > 0 else ConvertStatus.DELIVERED
    built = ConvertResult(
        status=status,
        entity_counts=entity_counts,
        data_errors_total=errors_total,
        quality_text=quality_text,
    )
    _record_manual_run(built, sis_type=config_name, elapsed=time.monotonic() - t0)
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
        failed = ConvertResult(status=ConvertStatus.BUILT_NOT_DELIVERED, sftp_attempted=True, sftp_ok=False)
        _record_manual_run(failed, sis_type=sis_type, elapsed=time.monotonic() - t0, delivery_only=True)
        return failed
    delivered = ConvertResult(status=ConvertStatus.DELIVERED_FROM_DISK, sftp_attempted=True, sftp_ok=True)
    _record_manual_run(delivered, sis_type=sis_type, elapsed=time.monotonic() - t0, delivery_only=True)
    return delivered


def _read_gde_bytes(input_dir: Path) -> dict[str, bytes]:
    """Read every GDE file (``.csv``/``.txt``) in the folder as bytes, keyed by filename.

    Mirrors the disk/CLI run's folder read: source_files in the config resolve by
    filename. A folder that isn't readable / has no GDE files yields ``{}`` (→ the
    extractor returns nothing → ``NO_INPUT``), never a crash.
    """
    sources: dict[str, bytes] = {}
    try:
        entries = sorted(input_dir.iterdir())
    except OSError:
        return sources
    for entry in entries:
        if entry.is_file() and entry.suffix.lower() in _GDE_SUFFIXES:
            try:
                sources[entry.name] = entry.read_bytes()
            except OSError:
                continue
    return sources


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
    configs = available_configs()
    # D9: NO silent fallback — prefill only from a valid SAVED district; otherwise leave the
    # dropdown on its "Choose your district" placeholder and keep Run disabled until chosen.
    # (The old `configs[0]` alphabetical guess is gone.)
    default_district: str | None = cfg.sis_type if cfg.sis_type in configs else None

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
    selected: dict[str, str | None] = {"district": default_district}
    # The run the on-screen anomaly card is asking about, or None when no card is up (FIX-2).
    # Held here (not in the card closure) because BOTH the interaction table and the ack
    # handler need it: the inputs freeze while it is set, and the re-run is launched from it.
    pending_ack: dict[str, RunIdentity | None] = {"identity": None}

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
    district_dropdown = ft.Dropdown(
        label="District",
        value=default_district,
        hint_text="Choose your district",
        options=[ft.dropdown.Option(key=c, text=friendly_district_name(c)) for c in configs],
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
    output_caption = ft.Text(
        resolved_output_caption(output_dir_value, setup_completed=setup_done),
        size=13,
        color=tokens.color_muted if output_set else tokens.color_status_warning,
    )

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
        page.update()

        def _on_done(result: ConvertResult) -> None:
            _set_running(False)
            _render_result(result, identity)
            page.update()

        def _on_error(_exc: BaseException) -> None:
            # Privacy: the raw exception (may carry a path / column) is NEVER surfaced —
            # fixed category copy only (the raw error belongs to the log). The copy ends
            # with a concrete next step (0035 W3b — no dead-end failures).
            _set_running(False)
            result_slot.controls = [components.ErrorCard(*convert_error_copy())]
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

    def _start_deliver() -> None:
        """Run ``deliver_job`` through the shared runner — the ONE deliver code path.

        Deliver-from-disk (0034 Slice 2): the post-build card, the BUILT_NOT_DELIVERED
        retry, and the standalone card all land here. The zip is named from the per-run
        district when one is chosen, else the saved district (a standalone delivery may
        have no dropdown pick). Honest progress + a terminal verdict banner; a pre-upload
        failure (unset output folder) routes to the calm ``on_error`` card.
        """
        sis = _deliver_district()  # the SAME resolution the readiness gate used (FIX-4)
        runner.state.reset()
        _set_running(True, caption="Delivering… sending your files to SpacesEDU.")
        deliver_slot.controls = []
        result_slot.controls = []
        page.update()

        def _on_done(result: ConvertResult) -> None:
            _set_running(False)
            _render_result(result, run_identity(selected["district"], input_field.value))
            page.update()

        def _on_error(_exc: BaseException) -> None:
            # Privacy: the raw exception is NEVER surfaced — fixed category copy only,
            # ending with a concrete next step (0035 W3b — no dead-end failures).
            _set_running(False)
            result_slot.controls = [components.ErrorCard(*deliver_error_copy())]
            page.update()

        started = runner.run(page, lambda: deliver_job(sis), on_done=_on_done, on_error=_on_error)
        if not started:  # already running — the single-flight guard held (C4)
            _set_running(True, caption="Delivering… sending your files to SpacesEDU.")
            page.update()

    def _confirm_and_deliver() -> None:
        """Deliver pre-flight confirm: labelled Server / Folder facts + the freshness fact.

        A SEPARATE explicit action — never an auto-run side effect. Names the SFTP host
        ONCE and the resolved local output folder (config values, never a secret), plus
        the honest vintage of what would ship ("Files last built …", from the newest
        on-disk CSV's mtime). On Deliver, runs ``deliver_job`` — uploads the committed
        files from disk, NEVER a re-transform (the old rebuild path, with its hardcoded
        ``anomaly_ack=True`` bypass, is gone). The exit-3 result renders the FAILED
        "built but not delivered" verdict from booleans (never ``on_error``).
        """
        cfg = AppConfig.load()
        local_folder = (cfg.output_dir or "").strip()
        # The vintage of what WOULD SHIP — the narrowed set, not the folder's newest *.csv
        # (a parked spreadsheet must never be quoted as the roster's build time — FIX-4).
        vintage = freshness_fact(deliverable_files(_deliver_district(cfg), local_folder).newest_mtime_iso)

        def _on_deliver(_e: ft.ControlEvent) -> None:
            page.pop_dialog()
            _start_deliver()

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

    def _render_result(result: ConvertResult, identity: RunIdentity) -> None:
        if result.status is ConvertStatus.NEEDS_ANOMALY_ACK:
            # The card is a question about THIS run — remember which one, and freeze the
            # inputs while it waits so the answer can't drift onto another (FIX-2).
            pending_ack["identity"] = identity
            result_slot.controls = [_anomaly_ack_card(result, identity)]
            _apply_interaction(job_running=False)
            return
        verdict, headline, detail = summarize(result)
        controls: list[ft.Control] = [
            components.HealthVerdictBanner(verdict, headline=headline, detail=detail),
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
        sftp_cfg = AppConfig.load()
        if sftp_cfg.sftp_is_configured():
            deliverable_local = (
                result.status in (ConvertStatus.DELIVERED, ConvertStatus.BUILT_WITH_DATA_ERRORS)
                and not result.sftp_attempted
            )
            retry_delivery = result.status is ConvertStatus.BUILT_NOT_DELIVERED
            if deliverable_local or retry_delivery:
                # Deliver-gate (0031): SFTP is configured, but delivery ALSO needs a stored
                # password for THIS Windows account. Present → the deliver/retry card; absent
                # or unreadable → a calm "route to Setup" card instead (no transient password
                # entry in Convert — Setup is the single credential home).
                if _sftp_credential_present(sftp_cfg):
                    controls.append(_deliver_action(retry=retry_delivery))
                else:
                    controls.append(_delivery_not_ready_card())
        result_slot.controls = controls

    def _deliver_action(*, retry: bool = False) -> ft.Control:
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
                                lambda _e: _confirm_and_deliver(),
                                icon=ft.Icons.CLOUD_UPLOAD_ROUNDED,
                            )
                        ]
                    ),
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
            page.update()
            _start_convert(anomaly_ack=identity)

        def _on_cancel(_e: ft.ControlEvent) -> None:
            runner.state.reset()
            pending_ack["identity"] = None  # question withdrawn — the inputs are editable again
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
                                lambda _e: _confirm_and_deliver(),
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
        """Re-derive the standalone deliver card for the CURRENT district pick (FIX-4).

        Suppressed once a run flow owns the deliver affordance (``result_slot`` non-empty):
        the post-run result card carries its own deliver/retry action, and re-populating the
        standalone slot underneath it would put two deliver buttons on screen. A district
        change can't reach here mid-run or mid-acknowledgement anyway — ``interaction_state``
        disables the dropdown in both — so this only guards the settled post-result view.
        """
        if result_slot.controls:
            return
        deliver_slot.controls = _standalone_deliver_controls()

    _refresh_district_note()
    _refresh_files()
    _refresh_convert_gate()
    _refresh_deliver_slot()

    # Direction B page header (0033 Slice 2): the gradient hero demotes to a slim header; the
    # saved district identity rides in the header's right slot as a ``district_chip`` (the
    # per-run selection stays the dropdown below — the chip reflects the configured district).
    header = components.page_header(
        "Convert",
        "Build your roster now from your MyEd BC extract files",
        trailing=components.district_chip(friendly_district_name(default_district)) if default_district else None,
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
    ``extract_required_files``, any expected-but-absent file (a plain amber chip +
    a one-line warning). A bad config / folder degrades to an empty list, never a
    crash.
    """
    present = _present_gde_files(input_dir)
    expected = _expected_files(config_name)

    controls: list[ft.Control] = []
    if present:
        controls.append(ft.Text("Files found", size=13, weight=ft.FontWeight.W_700, color=tokens.color_text))
        controls.append(ft.Row(spacing=10, wrap=True, controls=[components.FileChip(name) for name in present]))

    missing = [f for f in expected if f not in present]
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
    """The config's required source files (empty on any config error — never crashes)."""
    try:
        return extract_required_files(load_config(config_name))
    except Exception:  # noqa: BLE001 - a config error degrades to "no expectation", never a crash
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
