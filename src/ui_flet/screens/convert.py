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

**The anomaly-ack write-gate:** ``convert_job`` computes anomalies AFTER transform
and, when a >20% drop fires without an ``anomaly_ack``, returns
``NEEDS_ANOMALY_ACK`` **WITHOUT writing**. The view then shows a plain-language
WARNING + an explicit "I've reviewed this — convert anyway" CTA (which re-invokes
with ``anomaly_ack=True``) and a Cancel (which writes nothing). A silent 20% roster
drop is structurally impossible.

**SFTP delivery (IA-5b):** ``convert_job`` gains an ``sftp_requested`` leg — after a
successful build, an explicit pre-flight-confirmed delivery. The ``upload_csvs`` call
is wrapped TIGHTLY (only around the upload): a failure folds into a
``BUILT_NOT_DELIVERED`` result (the exit-3 shape — read from booleans by
``summarize``, NEVER routed through ``on_error``), carrying a fault CATEGORY only —
never the raw exception / host / path (privacy). A ``save_all`` / ``load_config``
failure in the earlier steps still PROPAGATES to ``on_error`` (fail-loud); the upload
catch never widens over the build. A failed delivery never rolls back the build —
the files stay written and the admin can retry.

**Write-in-flight close guard (IA-5b, C6):** a module-level flag
(``_WRITE_IN_FLIGHT``) is set immediately before ``save_all`` and cleared in a
``finally``; ``is_write_in_flight()`` exposes it for ``shell._on_leave``. It is
REASSURANCE-ONLY — the loader's backup-and-restore ``save_all`` atomicity is the real
safety net (a torn commit rolls back); ``_on_leave`` reads the flag but does NOT block
the atomic close.
"""

from __future__ import annotations

from pathlib import Path

import flet as ft

from src.config.app_config import AppConfig
from src.config.loader import available_configs, load_config
from src.etl.extractor import DataExtractor
from src.etl.loader import DataLoader
from src.etl.pipeline import (
    compute_anomalies,
    extract_required_files,
    run_transform,
)
from src.quality.report import DataQualityReport
from src.sftp.uploader import SFTPUploader
from src.ui_flet import components, tokens
from src.ui_flet.convert_result import ConvertResult, ConvertStatus, summarize
from src.ui_flet.filepicker import validate_input_dir
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


# Plain-language tile labels for the entity-count keys (mirrors home._ENTITY_LABELS).
_ENTITY_LABELS: dict[str, str] = {
    "Students": "Students",
    "Staff": "Staff",
    "Family": "Family",
    "Classes": "Classes",
    "Enrollments": "Enrollments",
    "CourseInfo": "Courses",
    "StudentCourses": "Student courses",
    "StudentAttendance": "Attendance",
}


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
    anomaly_ack: bool = False,
    sftp_requested: bool = False,
) -> ConvertResult:
    """Run the parity-locked convert adapter and return a PII-free ``ConvertResult``.

    Off the UI thread (``JobRunner`` runs it via ``page.run_thread``):
    ``load_config → load_from_bytes → run_transform → compute_anomalies`` and — only
    when clear or acknowledged — ``save_all`` + the quality report, then (only when
    ``sftp_requested``) an SFTP delivery. When a >20% anomaly fires without
    ``anomaly_ack`` it returns ``NEEDS_ANOMALY_ACK`` **without writing**.

    ETL-level failures (a missing field-map column → ``save_all``'s ``ValueError``,
    ``load_config``'s errors) propagate as exceptions → the runner's ``on_error``
    (fail-loud). The SFTP leg is the ONLY step whose failure is caught in-job: the
    ``upload_csvs`` call is wrapped TIGHTLY and folded into a ``BUILT_NOT_DELIVERED``
    result (the exit-3 shape — a CATEGORY only, never the raw exception/host/path),
    so a build failure is never mis-labelled "SFTP failed" and a failed delivery
    never discards the written files. NO DataFrame is returned (privacy).
    """
    config = load_config(config_name)
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
    if not outputs:
        return ConvertResult(status=ConvertStatus.NO_OUTPUT)

    output_dir = Path(AppConfig.load().output_dir or input_dir)

    # Anomaly gate BEFORE writing: a >20% drop, un-acknowledged, withholds the write.
    anomalies = compute_anomalies(outputs, output_dir)
    if anomalies and not anomaly_ack:
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
    global _WRITE_IN_FLIGHT
    _WRITE_IN_FLIGHT = True
    try:
        DataLoader(str(output_dir)).save_all(outputs, field_orders)
    finally:
        _WRITE_IN_FLIGHT = False

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
            ).upload_csvs(output_dir, sis_type=config_name)
        except Exception:  # noqa: BLE001 - exit-3: a failed delivery is a RESULT, not on_error
            return ConvertResult(
                status=ConvertStatus.BUILT_NOT_DELIVERED,
                entity_counts=entity_counts,
                data_errors_total=errors_total,
                sftp_attempted=True,
                sftp_ok=False,
                quality_text=quality_text,
            )
        # Data errors are a SEPARATE axis — a successful delivery must NOT erase the
        # "N records had field problems" warning (fail-loud; mirrors home_status).
        delivered_status = ConvertStatus.DELIVERED_WITH_DATA_ERRORS if errors_total > 0 else ConvertStatus.DELIVERED
        return ConvertResult(
            status=delivered_status,
            entity_counts=entity_counts,
            data_errors_total=errors_total,
            sftp_attempted=True,
            sftp_ok=True,
            quality_text=quality_text,
        )

    status = ConvertStatus.BUILT_WITH_DATA_ERRORS if errors_total > 0 else ConvertStatus.DELIVERED
    return ConvertResult(
        status=status,
        entity_counts=entity_counts,
        data_errors_total=errors_total,
        quality_text=quality_text,
    )


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


# --------------------------------------------------------------------------- #
# The view.                                                                     #
# --------------------------------------------------------------------------- #
def build_convert(page: ft.Page) -> ft.Control:  # pragma: no cover - Flet view glue
    """Build the Convert surface, bound to ``page`` (via ``partial`` in the shell)."""
    cfg = AppConfig.load()
    configs = available_configs()
    default_district = cfg.sis_type if cfg.sis_type in configs else (configs[0] if configs else cfg.sis_type)

    runner = JobRunner()
    selected = {"district": default_district}

    # ------------------------------------------------------------------ #
    # District select (read-only caption if only one option, else a dropdown).
    # ------------------------------------------------------------------ #
    district_dropdown = ft.Dropdown(
        label="District",
        value=default_district,
        options=[ft.dropdown.Option(key=c, text=friendly_district_name(c)) for c in configs],
        width=340,
    )

    # ------------------------------------------------------------------ #
    # File chips + missing-file warning (recomputed when the folder changes).
    # ------------------------------------------------------------------ #
    files_slot = ft.Column(spacing=10)
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

    def _refresh_convert_gate() -> None:
        valid_input = validate_input_dir(input_field.value).ok
        convert_btn.disabled = not (valid_input and runner.state.can_start)
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
        _refresh_files()

    district_dropdown.on_change = _on_district_change

    # ------------------------------------------------------------------ #
    # Run + result rendering (all UI mutation on the loop, never the worker).
    # ------------------------------------------------------------------ #
    def _set_running(running: bool) -> None:
        convert_btn.disabled = running or not validate_input_dir(input_field.value).ok
        convert_spinner.visible = running
        convert_caption.visible = running
        convert_caption.value = "Converting… this can take a moment for large extracts." if running else ""

    def _start_convert(*, anomaly_ack: bool = False, sftp_requested: bool = False) -> None:
        district = selected["district"]
        input_dir = input_field.value
        _set_running(True)
        result_slot.controls = []
        page.update()

        def _on_done(result: ConvertResult) -> None:
            _set_running(False)
            _render_result(result, district, input_dir)
            page.update()

        def _on_error(_exc: BaseException) -> None:
            # Privacy: the raw exception (may carry a path / column) is NEVER surfaced —
            # a fixed category message only (the raw error belongs to the log).
            _set_running(False)
            result_slot.controls = [
                components.ErrorCard(
                    "The conversion couldn't finish",
                    "Something went wrong while building your roster. Your existing files were not changed.",
                )
            ]
            page.update()

        started = runner.run(
            page,
            lambda: convert_job(district, input_dir, anomaly_ack=anomaly_ack, sftp_requested=sftp_requested),
            on_done=_on_done,
            on_error=_on_error,
        )
        if not started:  # already running — the single-flight guard held (C4)
            _set_running(True)
            page.update()

    def _confirm_and_deliver() -> None:
        """SFTP pre-flight confirm: name the destination (config values, never a secret).

        A SEPARATE explicit action after a successful build (mirrors the Streamlit
        page's separate "Upload via SFTP" step) — never an auto-run side effect. On
        Deliver, re-runs the ONE job with ``sftp_requested=True`` (re-transform +
        write + upload). The exit-3 result renders the FAILED "built but not
        delivered" verdict from booleans (never ``on_error``).
        """
        cfg = AppConfig.load()
        destination = f"{cfg.sftp_host}:{cfg.sftp_port}{cfg.sftp_remote_path}"

        def _on_deliver(_e: ft.ControlEvent) -> None:
            page.pop_dialog()
            runner.state.reset()
            result_slot.controls = []
            page.update()
            _start_convert(anomaly_ack=True, sftp_requested=True)

        def _on_cancel(_e: ft.ControlEvent) -> None:
            page.pop_dialog()

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Deliver to SpacesEDU?"),
                content=ft.Text(
                    f"Deliver the converted roster to {cfg.sftp_host}?\n\nDestination: {destination}",
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

    def _render_result(result: ConvertResult, district: str, input_dir: str) -> None:
        if result.status is ConvertStatus.NEEDS_ANOMALY_ACK:
            result_slot.controls = [_anomaly_ack_card(result, district, input_dir)]
            return
        verdict, headline, detail = summarize(result)
        controls: list[ft.Control] = [
            components.HealthVerdictBanner(verdict, headline=headline, detail=detail),
        ]
        if result.entity_counts:
            controls.append(_entity_tiles_row(result.entity_counts))
        if result.quality_text:
            controls.append(_quality_expander(result.quality_text))
        # SFTP delivery action: shown when SFTP is configured AND either (a) a successful
        # local build hasn't been delivered yet, or (b) a delivery FAILED
        # (BUILT_NOT_DELIVERED) and can be retried — the exit-3 banner's copy promises
        # exactly this retry, so the failure screen must offer it (no forced rebuild).
        # A DELIVERED / DELIVERED_WITH_DATA_ERRORS run (sftp_attempted=True) is already
        # delivered, so it is NOT offered again.
        if AppConfig.load().sftp_is_configured():
            deliverable_local = (
                result.status in (ConvertStatus.DELIVERED, ConvertStatus.BUILT_WITH_DATA_ERRORS)
                and not result.sftp_attempted
            )
            retry_delivery = result.status is ConvertStatus.BUILT_NOT_DELIVERED
            if deliverable_local or retry_delivery:
                controls.append(_deliver_action(retry=retry_delivery))
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

    def _anomaly_ack_card(result: ConvertResult, district: str, input_dir: str) -> ft.Control:
        verdict, headline, detail = summarize(result)
        anomaly_lines = [ft.Text(f"• {line}", size=13, color=tokens.color_text) for line in result.anomalies]

        def _on_ack(_e: ft.ControlEvent) -> None:
            # A fresh run with the ack set — re-transforms (one path, no PII frames held).
            runner.state.reset()
            result_slot.controls = []
            page.update()
            _start_convert(anomaly_ack=True)

        def _on_cancel(_e: ft.ControlEvent) -> None:
            runner.state.reset()
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

    _refresh_files()
    _refresh_convert_gate()

    hero = components.card(
        content=ft.Column(
            spacing=6,
            controls=[
                ft.Text("Convert now", size=26, weight=ft.FontWeight.W_800, color=tokens.color_on_action),
                ft.Text(
                    "Build your roster on demand — pick the folder with your MyEd BC extract and convert.",
                    size=15,
                    color=ft.Colors.with_opacity(0.9, tokens.color_on_action),
                ),
            ],
        ),
        gradient=components.hero_gradient(),
        padding=_pad_sym(32, 26),
        border_radius=18,
    )

    form = components.card(
        content=ft.Column(
            spacing=20,
            controls=[
                district_dropdown,
                input_field,
                files_slot,
                ft.Row(
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[convert_btn, convert_spinner, convert_caption],
                ),
            ],
        ),
    )

    return ft.Column(spacing=22, controls=[hero, form, result_slot])


# --------------------------------------------------------------------------- #
# View helpers                                                                  #
# --------------------------------------------------------------------------- #
def _build_file_chips(config_name: str, input_dir: str) -> list[ft.Control]:  # pragma: no cover - Flet view glue
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
        controls.append(
            ft.Text(
                "Expected files not found in this folder:",
                size=13,
                weight=ft.FontWeight.W_700,
                color=tokens.color_status_warning,
            )
        )
        controls.append(
            ft.Row(spacing=10, wrap=True, controls=[components.FileChip(name, present=False) for name in missing])
        )
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
        components.metric_tile(_ENTITY_LABELS.get(name, name), f"{count:,}") for name, count in entity_counts.items()
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
