"""Setup surface — a first-run WIZARD that graduates into a flat SETTINGS page (D8).

VIEW glue (coverage-omitted): the trust-critical *decisions* live in the COUNTED pure
modules — ``setup_flow`` (the wizard state machine: resume, per-step gates, finish copy,
the ``task_args_changed`` reconcile predicate, district auto-select), ``filepicker``
(``setup_state``/path validation), ``setup_gates`` (submit gates), ``schedule_status``
(the tri-state schedule truth), ``sftp_copy`` (Test provenance copy). This file only wires
them to controls.

**Two modes, one build entry (``build_setup``):**

* **Wizard mode** — while ``not cfg.has_completed_setup()``: a five-step guided path
  (District → Folders → Delivery → Schedule → Finish) with a "Step N of 5" indicator +
  Back, Enter/Continue gated per step, and focus moved to the new step's first field. The
  Schedule + Delivery steps are **skippable** ("Set up later") and **reconcile** against
  real side effects — a task the read-back already reports LIVE ("already scheduled") and a
  credential already in the keyring ("a delivery password is already saved") are shown
  instead of double-registering. **No step sets ``setup_completed``** — only the explicit
  finish confirmation does, so a mid-wizard abandonment never reads as "set up". Resume
  derives from real state (``setup_flow.derive_flow``), never a stored cursor.
* **Settings mode** — once completed: the flat scroll retitled **"Settings"** with the same
  folders/schedule/SFTP sections, plus **one reconciling Save** — when a task-baked field
  (input/output/district/SFTP flag/run time — ``setup_flow.task_args_changed``) changes and
  a schedule is live, the folders Save re-registers the task through the SAME register flow
  (incl. elevation) so tonight's run uses the new settings. The rail label stays "Setup".

The register/unregister flow (Slice 5/6) and the SFTP test/save flow (Slice 7) are **reused
verbatim** in both modes — the wizard's Schedule/Delivery steps embed the SAME section
builders, so there is exactly one register flow and one keyring-write path.

**Password contracts (I1/I3 schedule · I4/I5 SFTP — security-critical):** unchanged from
Slice 5–7. The Windows account password is a handler-LOCAL variable whose only sink is
``register_task(run_as_password=...)`` (DPAPI elevation handshake / child-env — never argv,
never ``cfg``, never a log/message). The SFTP credential's only sinks are
``store_password`` (Save → keyring) and the transient ``test_connection(password_override=)``
(Test → ``client.connect`` only); a failed Test can never clobber a stored credential (D6).
"""

from __future__ import annotations

import contextlib
import datetime
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import flet as ft

from src.config.app_config import AppConfig
from src.config.loader import available_configs
from src.scheduler import windows
from src.sftp.uploader import LISTING_DENIED_NOTE, SFTPUploader
from src.ui_flet import components, tokens
from src.ui_flet.filepicker import (
    ValidationResult,
    setup_state,
    validate_input_dir,
    validate_output_dir,
)
from src.ui_flet.humanize import friendly_district_name, friendly_sftp_reason
from src.ui_flet.picker_field import PickerField
from src.ui_flet.schedule_status import (
    ScheduleState,
    ScheduleStatus,
    interpret_unregister,
    is_transient_location,
)
from src.ui_flet.setup_errors import classify_schedule_error
from src.ui_flet.setup_flow import (
    TOTAL_STEPS,
    TRANSITION_CUE,
    DeliveryFact,
    FinishSummaryRow,
    FlowInputs,
    SetupStep,
    TaskArgs,
    auto_selected_district,
    can_advance,
    derive_flow,
    finish_copy,
    finish_summary_rows,
    is_skippable,
    next_step,
    prev_step,
    step_number,
    task_args_changed,
)
from src.ui_flet.setup_gates import can_register_schedule, can_save_sftp
from src.ui_flet.sftp_copy import sftp_form_differs_from_saved, sftp_test_copy
from src.ui_flet.verdict import Verdict
from src.utils.validators import ALLOWED_SFTP_HOSTS, validate_run_time

# Surfaced after a successful registration when the running exe lives in a transient dir
# (Downloads/Temp): pinning a scheduled task there risks the "task fires, exe is gone,
# nothing recorded" blind spot (D4). A warning, not a block — the admin may re-register later.
_TRANSIENT_LOCATION_WARNING = (
    "Heads up: DistrictSync is running from a temporary location (like Downloads or Temp). "
    "If you move or delete it, the nightly sync will stop — move it to a permanent folder "
    "and re-register."
)

# Calm fallbacks when an off-thread schedule worker itself raises (D5): the spinner + buttons
# must ALWAYS be released, so the worker marshals one of these instead of stranding the UI.
_WORKER_ERROR_REGISTER = "We couldn't run the schedule registration just now. Please try again."
_WORKER_ERROR_UNREGISTER = "We couldn't run the schedule removal just now. Please try again."

# Plain-language titles for the five wizard steps (the "Step N of 5 · <title>" indicator).
_STEP_TITLES: dict[SetupStep, str] = {
    SetupStep.FOLDERS: "Choose your folders",
    SetupStep.DISTRICT: "Choose your district",
    SetupStep.SCHEDULE: "Set a nightly schedule",
    SetupStep.DELIVERY: "Set up delivery",
    # #5: the step title is a neutral marker so the adaptive banner headline owns the peak moment
    # (avoids stacking "You're all set" twice — step title + banner).
    SetupStep.FINISH: "Finish",
}


def _inflight_row(text: str) -> ft.Control:
    """A spinner + honest waiting line shown while an off-thread schedule op is in flight (D5)."""
    return ft.Row(
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[ft.ProgressRing(width=18, height=18), ft.Text(text, size=13, color=tokens.color_muted)],
    )


def _schedule_readout_line(status: ScheduleStatus) -> ft.Control:
    """A one-line live readout of the REAL schedule state — styled by ATTENTION, not state (finding #3).

    A first-run install's Schedule step is legitimately MISSING ("not set up yet"); painting that
    red/error screams "broken" for a normal not-yet state. So the failed styling is reserved for
    ``attention`` (an expected-but-gone schedule, or a fired-but-no-record contradiction); a calm
    MISSING reads muted/neutral, LIVE reads green, UNKNOWN reads muted.
    """
    if status.attention:
        color, icon = tokens.color_status_failed, ft.Icons.ERROR_OUTLINE_ROUNDED
    elif status.state is ScheduleState.LIVE:
        color, icon = tokens.color_status_healthy, ft.Icons.CHECK_CIRCLE_ROUNDED
    elif status.state is ScheduleState.MISSING:
        color, icon = tokens.color_muted, ft.Icons.EVENT_BUSY_ROUNDED  # calm "not set up yet"
    else:  # UNKNOWN
        color, icon = tokens.color_muted, ft.Icons.HELP_OUTLINE_ROUNDED
    return ft.Row(
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[ft.Icon(icon, size=18, color=color), ft.Text(status.detail, size=13, color=color)],
    )


def _finish_summary_row_control(row: FinishSummaryRow) -> ft.Control:  # pragma: no cover - Flet view glue
    """One checked-summary row: a green ✓ for a configured step, else a subdued "later" cue.

    Uses M3 icons (never raw emoji): ``CHECK_CIRCLE_ROUNDED`` (healthy) for done, a muted
    ``PENDING_OUTLINED`` for a deferred skippable step. The whole deferred row reads subdued so an
    honest "you can do this later" never looks like a failure.
    """
    if row.done:
        icon, icon_color, detail_color = (
            ft.Icons.CHECK_CIRCLE_ROUNDED,
            tokens.color_status_healthy,
            tokens.color_text,
        )
    else:
        icon, icon_color, detail_color = ft.Icons.PENDING_OUTLINED, tokens.color_muted, tokens.color_muted
    return ft.Row(
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(icon, size=20, color=icon_color),
            ft.Text(row.label, size=14, weight=ft.FontWeight.W_700, color=tokens.color_text),
            ft.Text(row.detail, size=14, color=detail_color),
        ],
    )


def _finish_summary_card(rows: list[FinishSummaryRow]) -> ft.Control:  # pragma: no cover - Flet view glue
    """The honest checked-summary card — one row per input step, configured-vs-deferred (no confetti)."""
    return components.card(
        content=ft.Column(
            spacing=14,
            controls=[
                ft.Text("Here's what you set up", size=16, weight=ft.FontWeight.W_800, color=tokens.color_text),
                *[_finish_summary_row_control(row) for row in rows],
            ],
        )
    )


def _district_options() -> list[ft.dropdown.Option]:
    """SIS/district dropdown options — id keyed, ``district_name`` shown (RC2)."""
    return [ft.dropdown.Option(key=sis_id, text=friendly_district_name(sis_id)) for sis_id in available_configs()]


def _folders_valid(input_dir: str, output_dir: str) -> bool:
    """Both the input and output folders pass the boundary validators (the folders-step gate)."""
    return validate_input_dir(input_dir).ok and validate_output_dir(output_dir).ok


def _stored_delivery_present(cfg: AppConfig) -> bool:
    """Whether a delivery credential already sits in the keyring for the saved host/user (reconcile).

    A cheap synchronous keyring read (guarded — a blank/out-of-allowlist host makes the
    ``SFTPUploader`` construction raise, which we treat as "no stored credential"). Used to
    reconcile the Delivery step to "a delivery password is already saved" instead of forcing a
    fresh test, and to seed the wizard's ``DeliveryFact`` on resume.
    """
    if not (cfg.sftp_host and cfg.sftp_username):
        return False
    try:
        uploader = SFTPUploader(
            cfg.sftp_host, int(cfg.sftp_port or 22), cfg.sftp_username, cfg.sftp_remote_path or "/files"
        )
        return bool(uploader.get_stored_password())
    except Exception:  # noqa: BLE001 - any keyring/construction failure → "no stored credential"
        return False


@dataclass
class _ScheduleHandle:
    """A thin handle the Settings reconcile uses to drive the schedule section's register flow."""

    trigger_register: Callable[[], None]
    run_time_value: Callable[[], str]


# --------------------------------------------------------------------------- #
# Entry: wizard while not completed, else the flat Settings scroll.            #
# --------------------------------------------------------------------------- #
def build_setup(page: ft.Page) -> ft.Control:  # pragma: no cover - Flet view glue
    """Build the Setup surface — the first-run wizard, or the Settings page once completed."""
    cfg = AppConfig.load()
    root = ft.Column(spacing=22)
    if not cfg.has_completed_setup():
        _mount_wizard(page, cfg, root)
    else:
        _mount_settings(page, cfg, root, transition_cue=False)
    return root


# --------------------------------------------------------------------------- #
# Wizard mode (D8).                                                            #
# --------------------------------------------------------------------------- #
def _mount_wizard(page: ft.Page, cfg: AppConfig, root: ft.Column) -> None:  # pragma: no cover - Flet view glue
    """Render the five-step first-run wizard into ``root`` (resume derived from real state)."""
    available = available_configs()

    # Shared mutable wizard state. Folders/district selections mirror the config; the schedule
    # status + delivery fact are the injected verification results the finish line + resume read.
    ws: dict[str, object] = {
        "step": SetupStep.DISTRICT,  # placeholder — overwritten by derive_flow(...).resume_step below
        "input": cfg.input_dir,
        "output": cfg.output_dir,
        "sis": cfg.sis_type or auto_selected_district(available),  # D9: auto-select iff one config
        "schedule_skipped": False,
        "schedule_status": None,  # latest ScheduleStatus from the section's read-back
        "delivery": DeliveryFact.STORED_CRED_PRESENT if _stored_delivery_present(cfg) else DeliveryFact.NONE,
        "delivery_host": cfg.sftp_host,
        "delivery_user": cfg.sftp_username,
        "forward_btn": None,  # the current step's forward button (re-gated in place on input change)
    }

    def _inputs() -> FlowInputs:
        return FlowInputs(
            folders_valid=_folders_valid(str(ws["input"]), str(ws["output"])),
            district_chosen=bool(str(ws["sis"]).strip()),
            schedule=ws["schedule_status"],  # type: ignore[arg-type]
            schedule_skipped=bool(ws["schedule_skipped"]),
            delivery=ws["delivery"],  # type: ignore[arg-type]
        )

    # Resume: land on the first step real state says is unsatisfied (no stored cursor).
    ws["step"] = derive_flow(_inputs()).resume_step

    def _step_addressed(step: SetupStep) -> bool:
        """Whether a skippable step is done (LIVE / tested-ok / stored) OR explicitly deferred."""
        if step is SetupStep.SCHEDULE:
            status = ws["schedule_status"]
            return bool(ws["schedule_skipped"]) or (
                status is not None and status.state is ScheduleState.LIVE  # type: ignore[union-attr]
            )
        if step is SetupStep.DELIVERY:
            return ws["delivery"] in {
                DeliveryFact.TESTED_OK,
                DeliveryFact.STORED_CRED_PRESENT,
                DeliveryFact.SKIPPED,
            }
        return False

    def _refresh_footer() -> None:
        """Re-gate / re-label the forward button in place (input change, async status arrival)."""
        btn = ws["forward_btn"]
        if btn is None:
            return
        step = ws["step"]
        if step in (SetupStep.FOLDERS, SetupStep.DISTRICT):
            btn.disabled = not can_advance(step, _inputs())  # type: ignore[union-attr]
        elif is_skippable(step):
            btn.disabled = False
            btn.content = "Continue" if _step_addressed(step) else "Set up later"  # type: ignore[union-attr]
        page.update()

    def _go(step: SetupStep) -> None:
        ws["step"] = step
        _render()

    def _forward() -> None:
        step = ws["step"]
        if not can_advance(step, _inputs()):
            return  # gate closed (folders/district) — Enter/Continue is a no-op, matching the disabled button
        if step is SetupStep.FOLDERS:
            cfg.input_dir = str(ws["input"])
            cfg.output_dir = str(ws["output"])
            cfg.save()
        elif step is SetupStep.DISTRICT:
            cfg.sis_type = str(ws["sis"])
            cfg.save()
        elif is_skippable(step) and not _step_addressed(step):
            # Advancing an unaddressed skippable step defers it ("Set up later") — marked skipped
            # so it counts as satisfied for the finish line WITHOUT asserting anything false.
            if step is SetupStep.SCHEDULE:
                ws["schedule_skipped"] = True
            else:
                ws["delivery"] = DeliveryFact.SKIPPED
        nxt = next_step(step)
        if nxt is not None:
            _go(nxt)

    def _back() -> None:
        prev = prev_step(ws["step"])  # type: ignore[arg-type]
        if prev is not None:
            _go(prev)

    def _finish() -> None:
        # The ONLY completion signal (D8/D4a): reaching the finish line — never any single step —
        # marks the install set up. Then graduate this surface to Settings mode in place.
        cfg.setup_completed = True
        cfg.save()
        _mount_settings(page, AppConfig.load(), root, transition_cue=True)
        page.update()

    def _on_sched_status(status: ScheduleStatus) -> None:
        ws["schedule_status"] = status
        _refresh_footer()

    def _on_delivery(fact: DeliveryFact, host: str, username: str) -> None:
        ws["delivery"] = fact
        ws["delivery_host"] = host
        ws["delivery_user"] = username
        _refresh_footer()

    # ---- per-step body builders ---------------------------------------- #
    def _folders_body() -> ft.Control:
        def _on_input(path: str, _r: ValidationResult) -> None:
            ws["input"] = path
            _refresh_footer()

        def _on_output(path: str, _r: ValidationResult) -> None:
            ws["output"] = path
            _refresh_footer()

        input_field = PickerField(
            page=page,
            label="Input folder (MyEd BC extract)",
            helper="The folder DistrictSync reads your General Data Extract files from.",
            validator=validate_input_dir,
            on_change=_on_input,
            dialog_title="Select the MyEd BC extract folder",
            initial_value=str(ws["input"]),
        )
        output_field = PickerField(
            page=page,
            label="Output folder (SpacesEDU CSVs)",
            helper="Where DistrictSync writes the converted CSV files.",
            validator=validate_output_dir,
            on_change=_on_output,
            dialog_title="Select the output folder",
            initial_value=str(ws["output"]),
        )
        return ft.Column(spacing=22, controls=[input_field, output_field])

    def _district_body() -> ft.Control:
        def _on_pick(e: ft.ControlEvent) -> None:
            ws["sis"] = e.control.value or ""
            _refresh_footer()

        dropdown = ft.Dropdown(
            label="District",
            hint_text="Choose your district",  # D9: no pre-selection; placeholder prompts an explicit pick
            value=str(ws["sis"]) or None,
            options=_district_options(),
            on_select=_on_pick,  # Dropdown's value-change is on_select on 0.85.3 (not on_change)
            border_color=tokens.color_border,
            autofocus=True,  # focus the new step's first field (D8 keyboard flow)
        )
        return ft.Column(
            spacing=12,
            controls=[
                # #4: ONE orientation line on the wizard's FIRST step (now District, 2026-07-15 reorder)
                # — the wizard shouldn't cold-open with zero context. (A fuller welcome screen is a
                # close-out question, not built here.)
                ft.Text(
                    "DistrictSync keeps your MyEd BC roster flowing to SpacesEDU — automatically, every "
                    "night. Let's set it up.",
                    size=14,
                    color=tokens.color_muted,
                ),
                ft.Text(
                    "Pick the district whose MyEd BC layout matches your extract. "
                    "You can switch it later from the Mapping tab.",
                    size=14,
                    color=tokens.color_muted,
                ),
                dropdown,
            ],
        )

    def _schedule_body() -> ft.Control:
        card, _handle = _build_schedule_section(page, cfg, on_status=_on_sched_status)
        return card

    def _delivery_body() -> ft.Control:
        controls: list[ft.Control] = []
        if ws["delivery"] is DeliveryFact.STORED_CRED_PRESENT:
            # Reconcile (D8): a credential is already saved — don't imply a fresh one is required.
            controls.append(
                components.HealthVerdictBanner(
                    Verdict.HEALTHY,
                    headline="A delivery password is already saved",
                    detail="A SpacesEDU credential is already stored on this computer. "
                    "Test it below, or continue to keep using it.",
                )
            )
        controls.append(_build_sftp_section(page, cfg, on_delivery=_on_delivery))
        return ft.Column(spacing=18, controls=controls)

    def _finish_body() -> ft.Control:
        status = ws["schedule_status"]
        schedule_live = (not ws["schedule_skipped"]) and status is not None and status.state is ScheduleState.LIVE  # type: ignore[union-attr]
        district = friendly_district_name(str(ws["sis"])) or str(ws["sis"])
        next_run = status.next_run_display if (schedule_live and status is not None) else None  # type: ignore[union-attr]
        headline, detail = finish_copy(
            schedule_live=bool(schedule_live),
            delivery=ws["delivery"],  # type: ignore[arg-type]  # F1: keyed off PERSISTED delivery, not a transient test
            district=district,
            schedule_time_display=next_run,
            host=str(ws["delivery_host"]),
            username=str(ws["delivery_user"]),
        )
        # The checked summary is derived from the SAME computed facts as the banner copy (single
        # source — no independent re-derivation), so the calm per-step card can never contradict it.
        rows = finish_summary_rows(
            schedule_live=bool(schedule_live),
            delivery=ws["delivery"],  # type: ignore[arg-type]
            district=district,
            schedule_time_display=next_run,
        )
        return ft.Column(
            spacing=18,
            controls=[
                components.HealthVerdictBanner(Verdict.HEALTHY, headline=headline, detail=detail),
                _finish_summary_card(rows),
            ],
        )

    _BODIES: dict[SetupStep, Callable[[], ft.Control]] = {
        SetupStep.FOLDERS: _folders_body,
        SetupStep.DISTRICT: _district_body,
        SetupStep.SCHEDULE: _schedule_body,
        SetupStep.DELIVERY: _delivery_body,
        SetupStep.FINISH: _finish_body,
    }

    def _step_header(step: SetupStep) -> ft.Control:
        # Direction B (0033 Slice 2): the gradient step hero demotes to a compact page header —
        # the step title as the header, "Step N of 5" as the caption. (The 5→4 "Finish
        # unnumbered" count fix is 0032 Tier-1 #10, a separate slice — not folded in here.)
        return components.page_header(
            _STEP_TITLES[step],
            f"Step {step_number(step)} of {TOTAL_STEPS}",
        )

    def _step_footer(step: SetupStep) -> ft.Control:
        controls: list[ft.Control] = []
        if prev_step(step) is not None:
            controls.append(components.secondary_button("Back", lambda _e: _back(), icon=ft.Icons.ARROW_BACK_ROUNDED))

        if step is SetupStep.FINISH:
            forward = components.primary_button(
                "Finish setup",
                lambda _e: _finish(),
                disabled=not can_advance(SetupStep.FINISH, _inputs()),
                disabled_bgcolor=tokens.color_border,
                icon=ft.Icons.CHECK_CIRCLE_ROUNDED,
            )
        elif step in (SetupStep.FOLDERS, SetupStep.DISTRICT):
            forward = components.primary_button(
                "Continue",
                lambda _e: _forward(),
                disabled=not can_advance(step, _inputs()),
                disabled_bgcolor=tokens.color_border,
                icon=ft.Icons.ARROW_FORWARD_ROUNDED,
            )
        else:  # skippable Schedule / Delivery
            forward = components.primary_button(
                "Continue" if _step_addressed(step) else "Set up later",
                lambda _e: _forward(),
                icon=ft.Icons.ARROW_FORWARD_ROUNDED,
            )
        ws["forward_btn"] = forward
        controls.append(forward)
        return ft.Row(spacing=16, controls=controls)

    def _render() -> None:
        step = ws["step"]  # type: ignore[assignment]
        root.controls = [_step_header(step), _BODIES[step](), _step_footer(step)]  # type: ignore[index]
        page.update()

    _render()


# --------------------------------------------------------------------------- #
# Settings mode (D8): the flat scroll + one reconciling Save.                  #
# --------------------------------------------------------------------------- #
def _mount_settings(  # pragma: no cover - Flet view glue
    page: ft.Page, cfg: AppConfig, root: ft.Column, *, transition_cue: bool
) -> None:
    """Render the completed-install Settings scroll into ``root`` (folders + schedule + SFTP)."""
    # The ONE task-args snapshot + reconcile the folders Save AND the SFTP Save both drive (D8/F1):
    # any change to a task-baked field (folders/district/SFTP flag/run time) on a registered
    # schedule re-registers through the SAME flow, so the nightly action can never go stale — and
    # enabling SFTP in Settings finally adds --sftp to an already-registered task (the F1 gap).
    saved = {
        "args": TaskArgs.of(
            input_dir=cfg.input_dir,
            output_dir=cfg.output_dir,
            sis_type=cfg.sis_type,
            sftp_enabled=cfg.sftp_enabled,
            run_time=cfg.schedule_time,
        )
    }

    def _snapshot_args() -> TaskArgs:
        return TaskArgs.of(
            input_dir=cfg.input_dir,
            output_dir=cfg.output_dir,
            sis_type=cfg.sis_type,
            sftp_enabled=cfg.sftp_enabled,
            run_time=cfg.schedule_time,
        )

    def _on_registered() -> None:
        # N1: after ANY successful register (reconcile OR the schedule section's own Register),
        # refresh the snapshot so a later Save doesn't redundantly re-register. cfg.schedule_time
        # was just set to the registered field value, so the snapshot now matches reality.
        saved["args"] = _snapshot_args()

    # Build the schedule section FIRST so both Saves can drive its register flow on a task-arg change.
    schedule_card, sched_handle = _build_schedule_section(page, cfg, on_registered=_on_registered)

    def _reconcile() -> bool:
        pending = TaskArgs.of(
            input_dir=cfg.input_dir,
            output_dir=cfg.output_dir,
            sis_type=cfg.sis_type,
            sftp_enabled=cfg.sftp_enabled,
            run_time=sched_handle.run_time_value(),
        )
        if cfg.schedule_registered and task_args_changed(saved["args"], pending):
            sched_handle.trigger_register()  # on success → _on_registered refreshes the snapshot
            return True
        return False

    sftp_card = _build_sftp_section(page, cfg, on_saved=_reconcile)
    folders_card = _build_settings_folders(page, cfg, reconcile=_reconcile)

    # Direction B (0033 Slice 2): the Settings gradient hero demotes to a slim page header.
    header = components.page_header(
        "Settings",
        "Everything you set up lives here — edit your folders, district, schedule, or delivery anytime.",
    )

    controls: list[ft.Control] = [header]
    if transition_cue:
        controls.append(
            components.HealthVerdictBanner(Verdict.HEALTHY, headline="Setup complete", detail=TRANSITION_CUE)
        )
    # Settings order (user decision 2026-07-15, overriding the earlier #2a "schedule FIRST"): folders
    # & district FIRST (what/where), then schedule (when), then delivery (destination) — the user's
    # stated mental model. Mirrors the wizard's lead-with-identity reorder. Wizard step order lives in
    # setup_flow.STEP_ORDER; this is the flat post-setup scroll.
    controls += [folders_card, schedule_card, sftp_card]
    root.controls = controls
    page.update()


def _build_settings_folders(  # pragma: no cover - Flet view glue
    page: ft.Page, cfg: AppConfig, *, reconcile: Callable[[], bool]
) -> ft.Control:
    """The Settings folders/district card with the ONE reconciling Save (D8).

    Saving persists the folders + district, then calls the shared ``reconcile`` (which re-registers
    the task when a task-baked field changed AND a schedule is registered — the SAME reconcile the
    SFTP Save uses, so the nightly action can never go stale). The Save is still structurally gated
    on valid folders.
    """
    state = {"input": cfg.input_dir, "output": cfg.output_dir, "sis": cfg.sis_type}

    save_btn = components.primary_button(
        "Save settings",
        None,  # wired below
        disabled_bgcolor=tokens.color_border,
        icon=ft.Icons.CHECK_CIRCLE_ROUNDED,
        radius=12,
        text_size=14,
        text_weight=ft.FontWeight.W_700,
    )
    saved_note = ft.Text("", size=13, weight=ft.FontWeight.W_600)

    def _refresh_gate() -> None:
        save_btn.disabled = not setup_state(state["input"], state["output"], state["sis"]).can_save

    def _on_input_change(path: str, _r: ValidationResult) -> None:
        state["input"] = path
        _refresh_gate()
        page.update()

    def _on_output_change(path: str, _r: ValidationResult) -> None:
        state["output"] = path
        _refresh_gate()
        page.update()

    def _on_district_change(e: ft.ControlEvent) -> None:
        state["sis"] = e.control.value or ""
        _refresh_gate()
        page.update()

    def _save(_e: ft.ControlEvent | None = None) -> None:
        if not setup_state(state["input"], state["output"], state["sis"]).can_save:
            return  # structural gate (matches the disabled button)
        cfg.input_dir = state["input"]
        cfg.output_dir = state["output"]
        cfg.sis_type = state["sis"]
        cfg.save()
        # The shared reconcile re-registers the task when a task-baked field changed; the schedule
        # section surfaces its own in-flight + confirmed/failed states when it fires.
        if reconcile():
            saved_note.value = "Saved — updating the nightly schedule to match…"
        else:
            saved_note.value = "Saved."
        saved_note.color = tokens.color_status_healthy
        page.update()

    save_btn.on_click = _save

    input_field = PickerField(
        page=page,
        label="Input folder (MyEd BC extract)",
        helper="The folder DistrictSync reads your General Data Extract files from.",
        validator=validate_input_dir,
        on_change=_on_input_change,
        dialog_title="Select the MyEd BC extract folder",
        initial_value=cfg.input_dir,
    )
    output_field = PickerField(
        page=page,
        label="Output folder (SpacesEDU CSVs)",
        helper="Where DistrictSync writes the converted CSV files.",
        validator=validate_output_dir,
        on_change=_on_output_change,
        dialog_title="Select the output folder",
        initial_value=cfg.output_dir,
    )
    district_dropdown = ft.Dropdown(
        label="District",
        hint_text="Choose your district",
        value=cfg.sis_type or None,
        options=_district_options(),
        on_select=_on_district_change,
        border_color=tokens.color_border,
    )

    _refresh_gate()

    return components.card(
        content=ft.Column(
            spacing=26,
            controls=[
                ft.Text("Folders & district", size=20, weight=ft.FontWeight.W_800, color=tokens.color_text),
                input_field,
                output_field,
                district_dropdown,
                ft.Row(spacing=16, controls=[save_btn, saved_note]),
            ],
        )
    )


# --------------------------------------------------------------------------- #
# Schedule section — reused verbatim by the wizard Schedule step AND Settings.  #
# --------------------------------------------------------------------------- #
def _build_schedule_section(  # pragma: no cover - Flet view glue
    page: ft.Page,
    cfg: AppConfig,
    *,
    on_status: Callable[[ScheduleStatus], None] | None = None,
    on_registered: Callable[[], None] | None = None,
) -> tuple[ft.Control, _ScheduleHandle]:
    """The scheduler section — run time + (Windows) run-as password → register (Slice 5/6).

    Returns the card AND a ``_ScheduleHandle`` so Settings mode can drive re-registration on a
    task-arg change. ``on_status`` (when given) is called with each schedule read-back so the
    wizard can track live-ness for its resume + finish copy. ``on_registered`` (when given) fires
    after a CONFIRMED successful register so Settings can refresh its task-args snapshot (N1). The
    register/unregister flow is UNCHANGED from Slice 5/6 (off-thread, elevation-aware, save-after-success).
    """
    is_windows = sys.platform == "win32"

    run_time_field = ft.TextField(
        label="Daily run time (24-hour, HH:MM)",
        value=cfg.schedule_time or "03:00",
        width=220,
        border_color=tokens.color_border,
        helper="When DistrictSync runs each day — pick a time after your SIS extract lands.",
    )

    # Clock affordance that opens a TimePicker. The TextField stays the SINGLE SOURCE OF TRUTH
    # (all gating — can_register_schedule / validate_run_time / TaskArgs — reads run_time_field.value);
    # the picker only writes HH:MM back and typing remains allowed (an affordance, not a replacement).
    def _seed_time() -> datetime.time:
        """Seed the picker from the field's current HH:MM, falling back to the 03:00 default."""
        raw = (run_time_field.value or "").strip()
        try:
            hours, minutes = raw.split(":")
            return datetime.time(int(hours), int(minutes))
        except (ValueError, TypeError):
            return datetime.time(3, 0)

    def _open_time_picker(_e: ft.ControlEvent | None = None) -> None:
        def _on_time_confirmed(e: ft.ControlEvent) -> None:
            picked = e.control.value  # datetime.time (None if dismissed)
            if picked is None:
                return
            run_time_field.value = f"{picked.hour:02d}:{picked.minute:02d}"
            _refresh_register_gate()  # the SAME handler the field's on_change uses — re-gates + page.update

        # flet 0.85.3: TimePicker is a DialogControl → open via page.show_dialog; value is a
        # datetime.time; confirm fires on_change, cancel fires on_dismiss (see FLET_1.0_CONVENTIONS.md).
        page.show_dialog(
            ft.TimePicker(
                value=_seed_time(),
                help_text="Daily run time",
                confirm_text="Set",
                on_change=_on_time_confirmed,
            )
        )

    time_pick_button = ft.IconButton(
        icon=ft.Icons.ACCESS_TIME_ROUNDED,
        tooltip="Pick a time",
        on_click=_open_time_picker,
    )

    result_slot = ft.Column(spacing=0, controls=[])
    readout_slot = ft.Column(spacing=0, controls=[])

    def _kick_readout_probe() -> None:
        """Fetch the real schedule OFF the UI thread and render the tri-state readout (Windows only)."""
        if not is_windows:
            return

        def _work() -> None:  # runs OFF the UI thread
            from src.ui_flet.schedule_probe import probe_schedule

            status = probe_schedule(
                cfg.schedule_task_name,
                hint_registered=cfg.schedule_registered,
                latest_record_ts=None,
                surface="setup",  # de-circularize the MISSING copy → "add one below" (finding #3)
            )

            async def _apply() -> None:
                readout_slot.controls = [_schedule_readout_line(status)]
                if on_status is not None:
                    on_status(status)
                page.update()

            page.run_task(_apply)

        with contextlib.suppress(Exception):
            page.run_thread(_work)

    def _refresh_readout() -> None:
        if not is_windows:
            return
        readout_slot.controls = [ft.Text("Checking the schedule…", size=13, color=tokens.color_muted)]
        page.update()
        _kick_readout_probe()

    section_controls: list[ft.Control] = [
        ft.Text("Daily schedule", size=20, weight=ft.FontWeight.W_800, color=tokens.color_text),
        ft.Text(
            "Register an unattended nightly sync so the roster keeps flowing without anyone signing in.",
            size=14,
            color=tokens.color_muted,
        ),
    ]
    if is_windows:
        readout_slot.controls = [ft.Text("Checking the schedule…", size=13, color=tokens.color_muted)]
        section_controls.append(readout_slot)
    section_controls.append(
        ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[run_time_field, time_pick_button],
        )
    )

    password_field: ft.TextField | None = None
    if is_windows:
        from src.scheduler.windows import current_run_as_user

        section_controls.append(
            ft.Text(f"This task will run as: {current_run_as_user()}", size=13, color=tokens.color_muted)
        )
        password_field = ft.TextField(
            label="Windows account password",
            password=True,
            can_reveal_password=True,
            width=340,
            border_color=tokens.color_border,
            helper=(
                "Lets the nightly sync run after a reboot with no one logged in. "
                "Used once to register the task — DistrictSync does not store it."
            ),
        )
        section_controls.append(password_field)
        section_controls.append(
            ft.Text(
                "Leave the password blank to schedule a logged-on-only task "
                "(it will not run after a reboot with no one signed in).",
                size=12,
                color=tokens.color_muted,
            )
        )

    def _elevated_now() -> bool:
        if not is_windows:
            return False
        from src.scheduler.windows import is_elevated

        return is_elevated()

    def _register(_e: ft.ControlEvent | None = None) -> None:
        if not can_register_schedule(cfg.is_complete(), run_time_field.value or ""):
            return

        # I1/I3 (see module docstring — password contract): the Windows account password is a
        # handler-LOCAL var whose ONLY sink is register_task (DPAPI elevation handshake / child-env);
        # never cfg, never argv, never a log/message, never stashed past this handler.
        password = password_field.value if password_field is not None else None
        run_time = (run_time_field.value or "").strip()

        try:
            validate_run_time(run_time)
        except ValueError:
            result_slot.controls = [
                components.ErrorCard(
                    "That run time isn't valid",
                    "Enter the time as HH:MM in 24-hour form, e.g. 03:00.",
                ),
            ]
            page.update()
            return

        exe_path = Path(sys.executable)
        transient = is_transient_location(str(exe_path))
        uac_path = is_windows and bool(password) and not _elevated_now()

        def _on_register_success(headline: str, detail: str, *, verdict: Verdict = Verdict.HEALTHY) -> None:
            cfg.schedule_time = run_time
            cfg.schedule_registered = True
            cfg.save()
            if on_registered is not None:
                on_registered()  # N1: refresh Settings' task-args snapshot after a confirmed register
            local_verdict, local_detail = verdict, detail
            if transient:
                local_verdict = Verdict.WARNING
                local_detail = f"{detail} {_TRANSIENT_LOCATION_WARNING}"
            result_slot.controls = [
                components.HealthVerdictBanner(local_verdict, headline=headline, detail=local_detail)
            ]

        async def _apply_result(ok: bool, msg: str) -> None:
            register_btn.disabled = not can_register_schedule(cfg.is_complete(), run_time_field.value or "")
            unregister_btn.disabled = False
            if is_windows:
                if ok and password:
                    from src.scheduler.windows import current_run_as_user

                    _on_register_success(
                        "Nightly sync scheduled",
                        f"Runs as {current_run_as_user()}, whether or not you're logged in, daily at {run_time}.",
                    )
                elif ok:
                    _on_register_success(
                        "Scheduled — logged-on only",
                        "It will only run while you're logged in. Re-register with your "
                        "Windows password for unattended operation across reboots.",
                        verdict=Verdict.WARNING,
                    )
                elif msg == _WORKER_ERROR_REGISTER:
                    result_slot.controls = [components.ErrorCard("Couldn't register the schedule", msg)]
                elif msg in (windows._MSG_ELEVATION_NO_RESULT, windows._MSG_ELEVATION_TIMEOUT):
                    result_slot.controls = [
                        components.ErrorCard(
                            "Couldn't confirm the schedule", classify_schedule_error(msg, _elevated_now())
                        )
                    ]
                else:
                    result_slot.controls = [
                        components.ErrorCard(
                            "Couldn't register the schedule", classify_schedule_error(msg, _elevated_now())
                        )
                    ]
            elif ok:
                _on_register_success("Nightly sync scheduled", f"Runs daily at {run_time}.")
            else:
                detail = _WORKER_ERROR_REGISTER if msg == _WORKER_ERROR_REGISTER else msg
                result_slot.controls = [components.ErrorCard("Couldn't create the schedule", detail)]
            page.update()
            _refresh_readout()

        def _work() -> None:  # runs OFF the UI thread (the register call can block on the UAC prompt)
            try:
                if is_windows:
                    from src.scheduler.windows import register_task

                    ok, msg = register_task(
                        task_name=cfg.schedule_task_name,
                        exe_path=exe_path,
                        sis_type=cfg.sis_type,
                        input_dir=Path(cfg.input_dir),
                        output_dir=Path(cfg.output_dir),
                        run_time=run_time,
                        sftp=cfg.sftp_enabled,
                        run_as_user=None,
                        run_as_password=(password or None),
                    )
                else:
                    from src.scheduler.linux import register_cron

                    ok, msg = register_cron(
                        exe_path,
                        cfg.sis_type,
                        Path(cfg.input_dir),
                        Path(cfg.output_dir),
                        run_time,
                        sftp=cfg.sftp_enabled,
                    )
            except Exception:  # noqa: BLE001 - a worker crash must not strand the spinner
                ok, msg = False, _WORKER_ERROR_REGISTER
            page.run_task(_apply_result, ok, msg)

        register_btn.disabled = True
        unregister_btn.disabled = True
        result_slot.controls = [
            _inflight_row(
                "Asking Windows for permission and registering the schedule…"
                if uac_path
                else "Registering the schedule…"
            )
        ]
        page.update()
        page.run_thread(_work)

    def _unregister(_e: ft.ControlEvent | None = None) -> None:
        async def _apply_unregister(ok: bool, msg: str) -> None:
            register_btn.disabled = not can_register_schedule(cfg.is_complete(), run_time_field.value or "")
            unregister_btn.disabled = False
            if not ok and msg == _WORKER_ERROR_UNREGISTER:
                result_slot.controls = [components.ErrorCard("Couldn't remove the schedule", msg)]
            elif not ok and msg == windows._MSG_ELEVATION_REMOVE_UNCONFIRMED:
                result_slot.controls = [
                    components.ErrorCard(
                        "Couldn't confirm the schedule was removed",
                        "We couldn't confirm the nightly schedule was removed — check the schedule "
                        "status below, then try again if it's still there.",
                    )
                ]
            elif not ok and msg in (windows._MSG_UAC_DECLINED, windows._MSG_ELEVATION_LAUNCH_FAILED):
                result_slot.controls = [
                    components.ErrorCard("Schedule not removed", classify_schedule_error(msg, _elevated_now()))
                ]
            else:
                outcome = interpret_unregister(ok, msg)
                if outcome.success_shaped:
                    cfg.schedule_registered = False
                    cfg.save()
                    result_slot.controls = [
                        components.HealthVerdictBanner(
                            Verdict.HEALTHY, headline=outcome.headline, detail=outcome.detail
                        )
                    ]
                else:
                    result_slot.controls = [components.ErrorCard(outcome.headline, outcome.detail)]
            page.update()
            _refresh_readout()

        def _work() -> None:  # runs OFF the UI thread (an elevated delete can block on UAC)
            try:
                if is_windows:
                    from src.scheduler.windows import delete_task, delete_task_elevated

                    ok, msg = delete_task(cfg.schedule_task_name)
                    if not ok and "access is denied" in (msg or "").lower() and not _elevated_now():
                        ok, msg = delete_task_elevated(cfg.schedule_task_name)
                else:
                    from src.scheduler.linux import delete_cron

                    ok, msg = delete_cron()
            except Exception:  # noqa: BLE001 - a worker crash must not strand the spinner
                ok, msg = False, _WORKER_ERROR_UNREGISTER
            page.run_task(_apply_unregister, ok, msg)

        register_btn.disabled = True
        unregister_btn.disabled = True
        result_slot.controls = [_inflight_row("Removing the schedule…")]
        page.update()
        page.run_thread(_work)

    register_btn = components.primary_button(
        "Register schedule",
        _register,
        disabled=not can_register_schedule(cfg.is_complete(), run_time_field.value or ""),
        icon=ft.Icons.SCHEDULE_ROUNDED,
    )
    unregister_btn = components.secondary_button(
        "Unregister schedule",
        _unregister,
        icon=ft.Icons.EVENT_BUSY_ROUNDED,
    )

    def _refresh_register_gate(_e: ft.ControlEvent | None = None) -> None:
        register_btn.disabled = not can_register_schedule(cfg.is_complete(), run_time_field.value or "")
        page.update()

    run_time_field.on_change = _refresh_register_gate
    run_time_field.on_submit = _register
    if password_field is not None:
        password_field.on_submit = _register

    section_controls.append(ft.Row(spacing=16, controls=[register_btn, unregister_btn]))
    section_controls.append(result_slot)

    _kick_readout_probe()

    card = components.card(content=ft.Column(spacing=18, controls=section_controls))
    handle = _ScheduleHandle(trigger_register=_register, run_time_value=lambda: run_time_field.value or "")
    return card, handle


def _run_as_account() -> str:
    """The account whose keyring must hold the SFTP credential (defensive)."""
    try:
        from src.scheduler.windows import current_run_as_user

        return current_run_as_user()
    except Exception:
        return "this account"


# --------------------------------------------------------------------------- #
# SFTP section — reused verbatim by the wizard Delivery step AND Settings.      #
# --------------------------------------------------------------------------- #
def _build_sftp_section(  # pragma: no cover - Flet view glue
    page: ft.Page,
    cfg: AppConfig,
    *,
    on_delivery: Callable[[DeliveryFact, str, str], None] | None = None,
    on_saved: Callable[[], bool] | None = None,
) -> ft.Control:
    """The SFTP section — store SpacesEDU credentials in the OS keyring + test (Slice 7, D6).

    ``on_delivery`` (when given) reports the Delivery outcome to the wizard: a successful Test →
    ``TESTED_OK``, a failed Test → ``TESTED_FAILED``, a successful Save → ``STORED_CRED_PRESENT``
    (with the host/user). ``on_saved`` (when given) is the Settings reconcile — after a successful
    Save flips/confirms ``sftp_enabled``, it re-registers a live task so the nightly action gains
    (or keeps) ``--sftp`` (the F1 gap: enabling delivery post-registration must reconcile). The
    side-effect-free Test + Save-only keyring writes are UNCHANGED.
    """
    host_dropdown = ft.Dropdown(
        label="SFTP host (SpacesEDU)",
        value=cfg.sftp_host or None,
        options=[ft.dropdown.Option(key=h, text=h) for h in sorted(ALLOWED_SFTP_HOSTS)],
        border_color=tokens.color_border,
    )
    username_field = ft.TextField(
        label="Username", value=cfg.sftp_username or "", width=340, border_color=tokens.color_border
    )
    remote_field = ft.TextField(
        label="Remote path", value=cfg.sftp_remote_path or "/files", width=340, border_color=tokens.color_border
    )
    port_field = ft.TextField(label="Port", value=str(cfg.sftp_port or 22), width=140, border_color=tokens.color_border)
    password_field = ft.TextField(
        label="Password",
        password=True,
        can_reveal_password=True,
        width=340,
        border_color=tokens.color_border,
        helper="Leave blank to keep the existing stored credential.",
    )

    result_slot = ft.Column(spacing=0, controls=[])
    test_spinner = ft.ProgressRing(width=18, height=18, visible=False)

    def _current_fields() -> tuple[str, str, str, str]:
        return (
            (host_dropdown.value or "").strip(),
            (username_field.value or "").strip(),
            (remote_field.value or "").strip(),
            (port_field.value or "").strip(),
        )

    def _save(_e: ft.ControlEvent | None = None) -> None:
        # I4 (see module docstring — password contract): the SFTP credential is a handler-LOCAL var
        # whose ONLY sink on Save is store_password (OS keyring); never cfg, never a log/message.
        password = password_field.value or ""
        host, username, remote_path, port = _current_fields()

        if not can_save_sftp(
            host=host,
            username=username,
            remote_path=remote_path,
            password=password,
            already_configured=cfg.sftp_is_configured(),
        ):
            return

        try:
            uploader = SFTPUploader(host, int(port or 22), username, remote_path)
        except ValueError:
            result_slot.controls = [
                components.ErrorCard(
                    "That SFTP host isn't allowed",
                    "Pick one of the approved SpacesEDU hosts from the dropdown.",
                )
            ]
            page.update()
            return

        if password:
            try:
                uploader.store_password(password)
            except Exception:  # noqa: BLE001 - surface any keyring failure calmly
                result_slot.controls = [
                    components.ErrorCard(
                        "Couldn't save the SFTP credential",
                        "Couldn't save the SFTP credential on this account. Try again, or run "
                        "DistrictSync as the account the nightly task uses.",
                    )
                ]
                page.update()
                return

        read_back = uploader.get_stored_password()
        if not read_back:
            result_slot.controls = [
                components.HealthVerdictBanner(
                    Verdict.FAILED,
                    headline="Couldn't read the SFTP credential back",
                    detail=(
                        "Couldn't read the credential back on this account — SFTP uploads may fail. "
                        "Try again, or run the app as this account."
                    ),
                )
            ]
            page.update()
            return

        cfg.sftp_enabled = True
        cfg.sftp_host = host
        cfg.sftp_port = int(port or 22)
        cfg.sftp_username = username
        cfg.sftp_remote_path = remote_path
        cfg.save()

        detail = f"SFTP credentials stored and readable by {_run_as_account()}."
        # F1 reconcile (Settings only): enabling/confirming delivery must add --sftp to an
        # already-registered nightly task, or tonight builds but never delivers. Routed through the
        # SAME task-args reconcile the folders Save uses; a blank-password re-register keeps the
        # existing visible-WARNING (logged-on-only) behaviour.
        if on_saved is not None and on_saved():
            detail += " Updating the nightly schedule to deliver too…"
        result_slot.controls = [
            components.HealthVerdictBanner(Verdict.HEALTHY, headline="SFTP credentials stored", detail=detail)
        ]
        if on_delivery is not None:
            on_delivery(DeliveryFact.STORED_CRED_PRESENT, host, username)
        page.update()

    save_btn = components.primary_button(
        "Save SFTP credentials",
        _save,
        disabled=True,
        disabled_bgcolor=tokens.color_border,
        icon=ft.Icons.CLOUD_UPLOAD_ROUNDED,
    )

    def _refresh_save_gate(_e: ft.ControlEvent | None = None) -> None:
        host, username, remote_path, _port = _current_fields()
        save_btn.disabled = not can_save_sftp(
            host=host,
            username=username,
            remote_path=remote_path,
            password=(password_field.value or ""),
            already_configured=cfg.sftp_is_configured(),
        )
        page.update()

    host_dropdown.on_select = _refresh_save_gate
    username_field.on_change = _refresh_save_gate
    remote_field.on_change = _refresh_save_gate
    port_field.on_change = _refresh_save_gate
    password_field.on_change = _refresh_save_gate
    username_field.on_submit = _save
    remote_field.on_submit = _save
    port_field.on_submit = _save
    password_field.on_submit = _save

    def _test(_e: ft.ControlEvent) -> None:
        # I4/D6 (see module docstring — password contract): the typed password rides ONLY the
        # transient test_connection(password_override=...) → client.connect(); never the keyring
        # (that is _save's job alone), never a log, never the returned message. A failed Test can
        # therefore never clobber a working stored credential.
        password = password_field.value or ""
        host, username, remote_path, port = _current_fields()

        try:
            uploader = SFTPUploader(host, int(port or 22), username, remote_path)
        except ValueError:
            result_slot.controls = [
                components.ErrorCard(
                    "That SFTP host isn't allowed",
                    "Pick one of the approved SpacesEDU hosts from the dropdown.",
                )
            ]
            page.update()
            return

        provenance = "typed" if password else "stored"
        unsaved_edits = sftp_form_differs_from_saved(
            cfg, host=host, username=username, remote_path=remote_path, port=port
        )

        test_btn.disabled = True
        test_spinner.visible = True
        result_slot.controls = []
        page.update()

        async def _show_result(ok: bool, msg: str) -> None:
            test_btn.disabled = False
            test_spinner.visible = False
            verdict = Verdict.HEALTHY if ok else Verdict.FAILED
            # Listing-denied is a SUCCESS-with-note (auth worked; the account just can't list
            # the remote folder — normal for upload-only delivery accounts). Detected by
            # EQUALITY against the uploader's canonical fixed note.
            listing_denied = ok and msg == LISTING_DENIED_NOTE
            if ok:
                headline, detail = sftp_test_copy(
                    provenance=provenance,
                    unsaved_edits=unsaved_edits,
                    host=host,
                    username=username,
                    listing_denied=listing_denied,
                )
            else:
                headline, detail = "SFTP connection failed", friendly_sftp_reason(msg)
            result_slot.controls = [components.HealthVerdictBanner(verdict, headline=headline, detail=detail)]
            if on_delivery is not None:
                on_delivery(DeliveryFact.TESTED_OK if ok else DeliveryFact.TESTED_FAILED, host, username)
            page.update()

        def _work() -> None:  # runs OFF the UI thread
            try:
                ok, msg = uploader.test_connection(password_override=password)
            except Exception as exc:  # noqa: BLE001 - surface any failure via the banner
                ok, msg = False, str(exc)
            page.run_task(_show_result, ok, msg)

        page.run_thread(_work)

    test_btn = components.secondary_button("Test connection", _test, icon=ft.Icons.WIFI_TETHERING_ROUNDED)

    _refresh_save_gate()

    section_controls: list[ft.Control] = [
        ft.Text("SFTP delivery (SpacesEDU)", size=20, weight=ft.FontWeight.W_800, color=tokens.color_text),
        ft.Text(
            "Store your SpacesEDU SFTP credentials so the nightly sync can deliver the roster. "
            "The password is saved in this computer's credential manager — never in plain files.",
            size=14,
            color=tokens.color_muted,
        ),
        host_dropdown,
        ft.Row(spacing=16, controls=[username_field, port_field]),
        remote_field,
        password_field,
        ft.Row(
            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=[save_btn, test_btn, test_spinner]
        ),
        result_slot,
    ]

    return components.card(content=ft.Column(spacing=18, controls=section_controls))
