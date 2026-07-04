"""Setup → folders surface (the first real Flet surface).

VIEW glue (coverage-omitted): an admin picks the input (GDE) folder and the
output folder, chooses their district, and Saves — persisting to ``AppConfig``
(``input_dir``/``output_dir``/``sis_type``), which flips ``is_complete()``. The
trust-critical decisions are the COUNTED pure helpers in ``filepicker``
(``validate_input_dir``/``validate_output_dir``/``setup_state``); this file only
wires them to controls.

**Structural Save gate (RC3, security):** the Save button is *disabled* until
BOTH paths validate (``setup_state(...).can_save``) — an invalid path can never
reach ``AppConfig.save()`` (which would flip a false ``is_complete()`` and feed
``run_pipeline`` a path it ``sys.exit(1)``s on). Validation gates persistence
structurally, not just via an inline message.

**SIS source (RC2, DRY):** the district dropdown is sourced from the existing
``src.config.loader.available_configs()`` — the same enumerator the Streamlit
pages use — and shows each config's ``district_name`` (via ``load_config``), not
a raw id, so an admin never picks a meaningless ``sd48myedbc`` from a bare list.

**Scope (IA-4a):** folders + the *scheduler* section (run time + the Windows
run-as password → ``register_task``/``register_cron`` UNCHANGED → a verdict-first
result via the relocated ``classify_schedule_error`` + ``is_elevated``). The SFTP
/ keyring section arrives in IA-4b (extends this same surface). The in-surface
note points at what's next — it is not a dead control.

**Password contract (I1/I3, security-critical):** in the schedule handler the
Windows account password is a **handler-LOCAL variable** whose ONLY sink is
``register_task(run_as_password=...)`` (which the core routes to a child-env
``DSYNC_TASK_PW``, never argv). It is NEVER assigned to ``AppConfig``, NEVER
logged, NEVER echoed in a banner/message, and NEVER stashed beyond the handler's
scope. Only the non-sensitive ``schedule_time`` reaches ``cfg.save()``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import flet as ft

from src.config.app_config import AppConfig
from src.config.loader import available_configs
from src.ui_flet import components, tokens
from src.ui_flet.filepicker import (
    ValidationResult,
    setup_state,
    validate_input_dir,
    validate_output_dir,
)
from src.ui_flet.humanize import friendly_district_name
from src.ui_flet.picker_field import PickerField
from src.ui_flet.setup_errors import classify_schedule_error
from src.ui_flet.verdict import Verdict
from src.utils.validators import validate_run_time


def _pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def _district_options() -> list[ft.dropdown.Option]:
    """SIS/district dropdown options — id keyed, ``district_name`` shown (RC2).

    Sourced from ``available_configs()`` (the existing single-source enumerator);
    the id→friendly-name mapping (with the raw-id fallback + warning log) is the
    single-sourced ``humanize.friendly_district_name`` — DRY, one place to change.
    """
    return [ft.dropdown.Option(key=sis_id, text=friendly_district_name(sis_id)) for sis_id in available_configs()]


def build_setup(page: ft.Page) -> ft.Control:  # pragma: no cover - Flet view glue
    """Build the Setup folders surface, bound to ``page`` (via ``partial`` in shell)."""
    cfg = AppConfig.load()

    # Mutable selection state mirrored from the saved config.
    state = {"input": cfg.input_dir, "output": cfg.output_dir, "sis": cfg.sis_type}

    # The security Save-gate (RC3): structurally disabled until both paths validate.
    # `disabled_bgcolor=color_border` carries the disabled fill — the factory
    # reproduces the exact prior styling (DEFAULT primary / DISABLED border, radius
    # 12, size-14 W_700, CHECK_CIRCLE icon); `_refresh_gate` re-toggles `disabled`.
    save_btn = components.primary_button(
        "Save setup",
        lambda _e: _save(),
        disabled_bgcolor=tokens.color_border,
        icon=ft.Icons.CHECK_CIRCLE_ROUNDED,
        radius=12,
        text_size=14,
        text_weight=ft.FontWeight.W_700,
    )
    saved_note = ft.Text("", size=13, weight=ft.FontWeight.W_600)

    def _refresh_gate() -> None:
        s = setup_state(state["input"], state["output"], state["sis"])
        save_btn.disabled = not s.can_save

    def _on_input_change(path: str, _result: ValidationResult) -> None:
        state["input"] = path
        _refresh_gate()
        page.update()

    def _on_output_change(path: str, _result: ValidationResult) -> None:
        state["output"] = path
        _refresh_gate()
        page.update()

    def _on_district_change(e: ft.ControlEvent) -> None:
        state["sis"] = e.control.value or ""
        _refresh_gate()
        page.update()

    def _save() -> None:
        # Structural guard: can_save MUST hold here (button is disabled otherwise),
        # but re-check so an invalid path can never reach AppConfig.save().
        s = setup_state(state["input"], state["output"], state["sis"])
        if not s.can_save:
            return
        cfg.input_dir = state["input"]
        cfg.output_dir = state["output"]
        cfg.sis_type = state["sis"]
        cfg.save()
        saved_note.value = (
            "Saved — DistrictSync is set up." if cfg.is_complete() else "Saved, but some settings still need attention."
        )
        saved_note.color = tokens.color_status_healthy if cfg.is_complete() else tokens.color_status_failed
        page.update()

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
        value=cfg.sis_type or None,
        options=_district_options(),
        on_change=_on_district_change,
        border_color=tokens.color_border,
    )

    _refresh_gate()  # paint the gate for the saved (possibly already-valid) state

    header = components.card(
        content=ft.Column(
            spacing=4,
            controls=[
                ft.Text("Setup", size=26, weight=ft.FontWeight.W_800, color=tokens.color_on_action),
                ft.Text(
                    "Pick your folders and district, then Save. We'll remember it.",
                    size=14,
                    color=ft.Colors.with_opacity(0.85, tokens.color_on_action),
                ),
            ],
        ),
        gradient=components.hero_gradient(),
        padding=_pad_sym(32, 26),
        border_radius=18,
    )

    form_card = components.card(
        content=ft.Column(
            spacing=26,
            controls=[
                input_field,
                output_field,
                district_dropdown,
                ft.Row(spacing=16, controls=[save_btn, saved_note]),
            ],
        ),
    )

    schedule_card = _build_schedule_section(page, cfg)

    next_note = ft.Container(
        bgcolor=tokens.page_bg,
        padding=_pad_sym(20, 16),
        border_radius=12,
        content=ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.CLOUD_UPLOAD_ROUNDED, color=tokens.color_action_primary, size=20),
                ft.Text(
                    "Next: SFTP delivery to SpacesEDU. That step is on its way.",
                    size=13,
                    color=tokens.color_muted,
                ),
            ],
        ),
    )

    return ft.Column(spacing=22, controls=[header, form_card, schedule_card, next_note])


def _build_schedule_section(page: ft.Page, cfg: AppConfig) -> ft.Control:  # pragma: no cover - Flet view glue
    """The scheduler section — run time + (Windows) run-as password → register.

    Calls ``register_task``/``register_cron`` UNCHANGED; a failure is mapped by
    the relocated ``classify_schedule_error`` (Windows) + ``is_elevated``. The
    password is a handler-LOCAL variable whose ONLY sink is
    ``register_task(run_as_password=...)`` (I1/I3): never ``cfg``, never a log,
    never a message, never stashed after the handler returns.
    """
    is_windows = sys.platform == "win32"

    run_time_field = ft.TextField(
        label="Daily run time (24-hour, HH:MM)",
        value=cfg.schedule_time or "03:00",
        width=220,
        border_color=tokens.color_border,
        helper_text="When DistrictSync runs each day — pick a time after your SIS extract lands.",
    )

    # Result surface — swapped to a verdict banner / error card on register.
    result_slot = ft.Column(spacing=0, controls=[])

    section_controls: list[ft.Control] = [
        ft.Text("Daily schedule", size=20, weight=ft.FontWeight.W_800, color=tokens.color_text),
        ft.Text(
            "Register an unattended nightly sync so the roster keeps flowing without anyone signing in.",
            size=14,
            color=tokens.color_muted,
        ),
        run_time_field,
    ]

    password_field: ft.TextField | None = None
    if is_windows:
        from src.scheduler.windows import current_run_as_user

        section_controls.append(
            ft.Text(
                f"This task will run as: {current_run_as_user()}",
                size=13,
                color=tokens.color_muted,
            )
        )
        password_field = ft.TextField(
            label="Windows account password",
            password=True,
            can_reveal_password=True,
            width=340,
            border_color=tokens.color_border,
            helper_text=(
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

    def _register(_e: ft.ControlEvent) -> None:
        # Read the password fresh at register-time; local var, never stashed (I3).
        password = password_field.value if password_field is not None else None

        run_time = (run_time_field.value or "").strip()

        # [gate #5] validate_run_time RAISES ValueError on bad input (and returns
        # a (hour, minute) tuple we discard) — call for the raise-gate, then pass
        # the ORIGINAL run_time string downstream (register_task re-validates and
        # needs the raw string for PowerShell ParseExact).
        try:
            validate_run_time(run_time)
        except ValueError as exc:
            result_slot.controls = [
                components.ErrorCard("That run time isn't valid", str(exc)),
            ]
            page.update()
            return

        cfg.schedule_time = run_time
        cfg.save()

        exe_path = Path(sys.executable)

        if is_windows:
            from src.scheduler.windows import is_elevated, register_task

            ok, msg = register_task(
                task_name=cfg.schedule_task_name,
                exe_path=exe_path,
                sis_type=cfg.sis_type,
                input_dir=Path(cfg.input_dir),
                output_dir=Path(cfg.output_dir),
                run_time=cfg.schedule_time,
                sftp=cfg.sftp_enabled,
                run_as_user=None,
                run_as_password=(password or None),
            )
            if ok and password:
                from src.scheduler.windows import current_run_as_user

                cfg.schedule_registered = True
                cfg.save()
                result_slot.controls = [
                    components.HealthVerdictBanner(
                        Verdict.HEALTHY,
                        headline="Nightly sync scheduled",
                        detail=(
                            f"Runs as {current_run_as_user()}, whether or not you're logged in, "
                            f"daily at {cfg.schedule_time}."
                        ),
                    )
                ]
            elif ok:
                cfg.schedule_registered = True
                cfg.save()
                result_slot.controls = [
                    components.HealthVerdictBanner(
                        Verdict.WARNING,
                        headline="Scheduled — logged-on only",
                        detail=(
                            "It will only run while you're logged in. Re-register with your "
                            "Windows password for unattended operation across reboots."
                        ),
                    )
                ]
            else:
                elevated = is_elevated()
                result_slot.controls = [
                    components.ErrorCard(
                        "Couldn't register the schedule",
                        classify_schedule_error(msg, elevated),
                    )
                ]
        else:
            from src.scheduler.linux import register_cron

            ok, msg = register_cron(
                exe_path,
                cfg.sis_type,
                Path(cfg.input_dir),
                Path(cfg.output_dir),
                cfg.schedule_time,
                sftp=cfg.sftp_enabled,
            )
            if ok:
                cfg.schedule_registered = True
                cfg.save()
                result_slot.controls = [
                    components.HealthVerdictBanner(
                        Verdict.HEALTHY,
                        headline="Nightly sync scheduled",
                        detail=f"Runs daily at {cfg.schedule_time}.",
                    )
                ]
            else:
                result_slot.controls = [
                    components.ErrorCard("Couldn't create the schedule", msg),
                ]

        page.update()

    register_btn = components.primary_button(
        "Register schedule",
        _register,
        disabled=not (cfg.is_complete() and bool((run_time_field.value or "").strip())),
        icon=ft.Icons.SCHEDULE_ROUNDED,
    )

    def _refresh_register_gate(_e: ft.ControlEvent | None = None) -> None:
        register_btn.disabled = not (cfg.is_complete() and bool((run_time_field.value or "").strip()))
        page.update()

    run_time_field.on_change = _refresh_register_gate

    section_controls.append(register_btn)
    section_controls.append(result_slot)

    return components.card(content=ft.Column(spacing=18, controls=section_controls))
