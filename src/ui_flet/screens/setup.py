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

**Scope (IA-4a + IA-4b):** folders + the *scheduler* section (run time + the
Windows run-as password → ``register_task``/``register_cron`` UNCHANGED → a
verdict-first result via the relocated ``classify_schedule_error`` +
``is_elevated``) + the *SFTP* section (allowlist host dropdown + credentials →
``SFTPUploader.store_password`` (OS keyring) + a ``get_stored_password``
round-trip → verdict; a marshalled "Test connection" via
``page.run_thread``/``page.run_task``). The full first-time setup flow (folders
→ schedule → SFTP) is a sectioned single scroll — no cross-step password parking.

**Password contract (I1/I3, security-critical — schedule):** in the schedule
handler the Windows account password is a **handler-LOCAL variable** whose ONLY
sink is ``register_task(run_as_password=...)`` (which the core routes to a
child-env ``DSYNC_TASK_PW``, never argv). It is NEVER assigned to ``AppConfig``,
NEVER logged, NEVER echoed in a banner/message, and NEVER stashed beyond the
handler's scope. Only the non-sensitive ``schedule_time`` reaches ``cfg.save()``.

**Password contract (I4/I5, security-critical — SFTP):** in the SFTP handlers the
credential is a **handler-LOCAL variable** whose ONLY sink is
``SFTPUploader.store_password(...)`` (OS keyring). It is NEVER assigned to
``AppConfig``, NEVER logged, NEVER echoed in a banner/message. Only the five
non-sensitive settings (``sftp_enabled``/``sftp_host``/``sftp_port``/
``sftp_username``/``sftp_remote_path``) reach ``cfg.save()``. The host is
restricted to ``ALLOWED_SFTP_HOSTS`` structurally (the dropdown IS the allowlist)
AND at the boundary (``SFTPUploader.__init__`` runs ``validate_sftp_host``).

**Test-connection marshalling (I6, concurrency):** ``test_connection`` is a
blocking ~30s network call — it runs OFF the UI thread via ``page.run_thread``;
the result banner + button/spinner teardown mutate controls ONLY inside a
``page.run_task`` callback (never from the worker thread). ``test_connection``
returns ``(bool, str)`` and does NOT raise ``SystemExit`` (unlike
``run_pipeline``), so a plain ``except Exception`` in the worker suffices.
"""

from __future__ import annotations

import sys
from pathlib import Path

import flet as ft

from src.config.app_config import AppConfig
from src.config.loader import available_configs
from src.sftp.uploader import SFTPUploader
from src.ui_flet import components, tokens
from src.ui_flet.filepicker import (
    ValidationResult,
    setup_state,
    validate_input_dir,
    validate_output_dir,
)
from src.ui_flet.humanize import friendly_district_name, friendly_sftp_reason
from src.ui_flet.picker_field import PickerField
from src.ui_flet.setup_errors import classify_schedule_error
from src.ui_flet.verdict import Verdict
from src.utils.validators import ALLOWED_SFTP_HOSTS, validate_run_time


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
        # ft.Dropdown's value-change event on flet 0.85.3 is on_select — there is NO
        # on_change (that raises TypeError at construction). See FLET_1.0_CONVENTIONS.md.
        on_select=_on_district_change,
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
    sftp_card = _build_sftp_section(page, cfg)

    return ft.Column(spacing=22, controls=[header, form_card, schedule_card, sftp_card])


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
        helper="When DistrictSync runs each day — pick a time after your SIS extract lands.",
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
        except ValueError:
            # Privacy/voice: name the fix, never echo the raw ValueError (which repeats
            # the admin's own input) — the dropdown-free HH:MM hint is all they need.
            result_slot.controls = [
                components.ErrorCard(
                    "That run time isn't valid",
                    "Enter the time as HH:MM in 24-hour form, e.g. 03:00.",
                ),
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


def _run_as_account() -> str:
    """The account whose keyring must hold the SFTP credential (defensive).

    The nightly scheduled task runs as this account, so it is the account whose
    OS credential store must be readable. ``current_run_as_user`` can raise on a
    non-Windows host / unusual environment — fall back to a plain label rather
    than crash the SFTP section (mirrors the Streamlit page's guarded lookup).
    """
    try:
        from src.scheduler.windows import current_run_as_user

        return current_run_as_user()
    except Exception:
        return "this account"


def _build_sftp_section(page: ft.Page, cfg: AppConfig) -> ft.Control:  # pragma: no cover - Flet view glue
    """The SFTP section — store SpacesEDU credentials in the OS keyring + test.

    Calls ``SFTPUploader`` UNCHANGED. The password is a handler-LOCAL variable
    whose ONLY sink is ``SFTPUploader.store_password`` (OS keyring) — never
    ``cfg``, never a log, never a message (I4). The host is restricted to
    ``ALLOWED_SFTP_HOSTS`` structurally (the dropdown IS the allowlist) AND at the
    boundary (``__init__``'s ``validate_sftp_host`` — belt-and-suspenders, I5).
    "Test connection" is a blocking ~30s network call → marshalled OFF the UI
    thread via ``page.run_thread`` / ``page.run_task`` (I6).
    """
    host_dropdown = ft.Dropdown(
        label="SFTP host (SpacesEDU)",
        value=cfg.sftp_host or None,
        options=[ft.dropdown.Option(key=h, text=h) for h in sorted(ALLOWED_SFTP_HOSTS)],
        border_color=tokens.color_border,
    )
    username_field = ft.TextField(
        label="Username",
        value=cfg.sftp_username or "",
        width=340,
        border_color=tokens.color_border,
    )
    remote_field = ft.TextField(
        label="Remote path",
        value=cfg.sftp_remote_path or "/files",
        width=340,
        border_color=tokens.color_border,
    )
    port_field = ft.TextField(
        label="Port",
        value=str(cfg.sftp_port or 22),
        width=140,
        border_color=tokens.color_border,
    )
    password_field = ft.TextField(
        label="Password",
        password=True,
        can_reveal_password=True,
        width=340,
        border_color=tokens.color_border,
        helper="Leave blank to keep the existing stored credential.",
    )

    # Result surface — swapped to a verdict banner / error card on save or test.
    result_slot = ft.Column(spacing=0, controls=[])

    # Spinner shown only while a Test-connection is in flight (marshalled).
    test_spinner = ft.ProgressRing(width=18, height=18, visible=False)

    def _current_fields() -> tuple[str, str, str, str]:
        return (
            (host_dropdown.value or "").strip(),
            (username_field.value or "").strip(),
            (remote_field.value or "").strip(),
            (port_field.value or "").strip(),
        )

    def _save(_e: ft.ControlEvent) -> None:
        # Read the credential fresh; local var, sole sink is store_password (I4).
        password = password_field.value or ""
        host, username, remote_path, port = _current_fields()

        # [I5] Belt-and-suspenders: the dropdown already restricts host to the
        # allowlist, but SFTPUploader.__init__ re-validates and raises ValueError.
        try:
            uploader = SFTPUploader(host, int(port or 22), username, remote_path)
        except ValueError:
            # The dropdown already constrains the host to the allowlist; name the fix,
            # never echo the raw ValueError (voice + no input echo).
            result_slot.controls = [
                components.ErrorCard(
                    "That SFTP host isn't allowed",
                    "Pick one of the approved SpacesEDU hosts from the dropdown.",
                )
            ]
            page.update()
            return

        # [gate #3] store_password re-raises on keyring failure — wrap it and surface a
        # calm, FIXED category card. The raw keyring exception (which can carry OS/backend
        # detail) NEVER reaches the admin — category prose only, no str(e).
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

        # Keyring round-trip: verify the credential is readable by this account
        # (the scheduled task runs as the same account — mirrors Streamlit Step 4).
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

        # Persist NON-sensitive settings ONLY. [I4] the password is never here.
        cfg.sftp_enabled = True
        cfg.sftp_host = host
        cfg.sftp_port = int(port or 22)
        cfg.sftp_username = username
        cfg.sftp_remote_path = remote_path
        cfg.save()

        result_slot.controls = [
            components.HealthVerdictBanner(
                Verdict.HEALTHY,
                headline="SFTP credentials stored",
                detail=f"SFTP credentials stored and readable by {_run_as_account()}.",
            )
        ]
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
        has_required = bool(host and username and remote_path)
        # First-time (no stored credential yet) also needs a password; on re-open
        # a stored credential exists so the password may be left blank to keep it.
        first_time = not cfg.sftp_is_configured()
        has_password = bool(password_field.value)
        save_btn.disabled = not (has_required and (has_password or not first_time))
        page.update()

    host_dropdown.on_select = _refresh_save_gate  # Dropdown value-change is on_select (0.85.3)
    username_field.on_change = _refresh_save_gate
    remote_field.on_change = _refresh_save_gate
    port_field.on_change = _refresh_save_gate
    password_field.on_change = _refresh_save_gate

    # ------------------------------------------------------------------ #
    # Test connection — marshalled OFF the UI thread (I6).                 #
    # ------------------------------------------------------------------ #
    def _test(_e: ft.ControlEvent) -> None:
        # Read the credential fresh; local var, sole sink is store_password (I4).
        password = password_field.value or ""
        host, username, remote_path, port = _current_fields()

        # Construction + optional store happen BEFORE the thread so a ValueError
        # (out-of-allowlist) or keyring failure surfaces calmly, not on the worker.
        try:
            uploader = SFTPUploader(host, int(port or 22), username, remote_path)
            if password:
                uploader.store_password(password)
        except ValueError:
            result_slot.controls = [
                components.ErrorCard(
                    "That SFTP host isn't allowed",
                    "Pick one of the approved SpacesEDU hosts from the dropdown.",
                )
            ]
            page.update()
            return
        except Exception:  # noqa: BLE001 - a keyring failure before the network call
            result_slot.controls = [
                components.ErrorCard(
                    "Couldn't save the SFTP credential",
                    "Couldn't save the SFTP credential on this account. Try again, or run "
                    "DistrictSync as the account the nightly task uses.",
                )
            ]
            page.update()
            return

        # Disable the button + show the spinner; the ~30s timeout bounds a hung
        # connection so the window never freezes (degrade-gracefully guarantee).
        test_btn.disabled = True
        test_spinner.visible = True
        result_slot.controls = []
        page.update()

        async def _show_result(ok: bool, msg: str) -> None:
            # UI mutation ONLY inside this coroutine the loop owns — never from
            # the worker thread (FLET_1.0_CONVENTIONS §Worker-thread).
            #
            # [gate #1] PRIVACY: `msg` on the failure path is the CORE's raw
            # `test_connection` return (a raw paramiko/socket string that can carry
            # host/socket/path detail). It is mapped to a bounded category reason
            # via `friendly_sftp_reason` BEFORE it reaches the banner — the raw
            # string NEVER renders. The success path shows a fixed reassurance.
            test_btn.disabled = False
            test_spinner.visible = False
            verdict = Verdict.HEALTHY if ok else Verdict.FAILED
            headline = "SFTP connection succeeded" if ok else "SFTP connection failed"
            detail = "Your SFTP credentials work — the nightly sync can deliver." if ok else friendly_sftp_reason(msg)
            result_slot.controls = [components.HealthVerdictBanner(verdict, headline=headline, detail=detail)]
            page.update()

        def _work() -> None:  # runs OFF the UI thread
            # test_connection returns (bool, str) and does NOT raise SystemExit
            # (unlike run_pipeline) — a plain except Exception suffices here. Any raw
            # exception is sanitized to a category reason in `_show_result` (never
            # rendered raw), so passing str(exc) here is safe — it never reaches a card.
            try:
                ok, msg = uploader.test_connection()
            except Exception as exc:  # noqa: BLE001 - surface any failure via the banner
                ok, msg = False, str(exc)
            page.run_task(_show_result, ok, msg)

        page.run_thread(_work)

    test_btn = components.secondary_button(
        "Test connection",
        _test,
        icon=ft.Icons.WIFI_TETHERING_ROUNDED,
    )

    _refresh_save_gate()  # paint the gate for the saved (possibly configured) state

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
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[save_btn, test_btn, test_spinner],
        ),
        result_slot,
    ]

    return components.card(content=ft.Column(spacing=18, controls=section_controls))
