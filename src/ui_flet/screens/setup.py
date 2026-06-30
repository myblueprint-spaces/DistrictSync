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

**Honest scope (IA-4):** this is the *folders* step only; scheduling / SFTP /
keyring arrive next (IA-4 extends this same surface). The in-surface note says
so — it is not a dead control.
"""

from __future__ import annotations

import logging

import flet as ft

from src.config.app_config import AppConfig
from src.config.loader import available_configs, load_config
from src.ui_flet import tokens
from src.ui_flet.filepicker import (
    ValidationResult,
    setup_state,
    validate_input_dir,
    validate_output_dir,
)
from src.ui_flet.picker_field import PickerField

logger = logging.getLogger(__name__)


def _pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def _b_all(width: float, color: str) -> ft.Border:
    side = ft.BorderSide(width, color)
    return ft.Border(top=side, bottom=side, left=side, right=side)


def _district_options() -> list[ft.dropdown.Option]:
    """SIS/district dropdown options — id keyed, ``district_name`` shown (RC2).

    Sourced from ``available_configs()`` (the existing single-source enumerator);
    a config that fails to load falls back to its raw id rather than vanishing.
    """
    options: list[ft.dropdown.Option] = []
    for sis_id in available_configs():
        try:
            friendly = load_config(sis_id).district_name or sis_id
        except Exception as exc:  # noqa: BLE001 - never let one bad config blank the whole list
            logger.warning("Could not load district_name for %r: %s", sis_id, exc)
            friendly = sis_id
        options.append(ft.dropdown.Option(key=sis_id, text=friendly))
    return options


def build_setup(page: ft.Page) -> ft.Control:  # pragma: no cover - Flet view glue
    """Build the Setup folders surface, bound to ``page`` (via ``partial`` in shell)."""
    cfg = AppConfig.load()

    # Mutable selection state mirrored from the saved config.
    state = {"input": cfg.input_dir, "output": cfg.output_dir, "sis": cfg.sis_type}

    save_btn = ft.FilledButton(
        text="Save setup",
        icon=ft.Icons.CHECK_CIRCLE_ROUNDED,
        on_click=lambda _e: _save(),
    )
    saved_note = ft.Text("", size=13, weight=ft.FontWeight.W_600)

    def _refresh_gate() -> None:
        s = setup_state(state["input"], state["output"], state["sis"])
        save_btn.disabled = not s.can_save
        save_btn.style = ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DEFAULT: tokens.color_action_primary,
                ft.ControlState.DISABLED: tokens.color_border,
            },
            color=tokens.color_on_action,
            shape=ft.RoundedRectangleBorder(radius=12),
            text_style=ft.TextStyle(size=14, weight=ft.FontWeight.W_700),
        )

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

    header = ft.Container(
        gradient=ft.LinearGradient(
            begin=ft.Alignment(-1, -1),
            end=ft.Alignment(1, 1),
            colors=[tokens.color_action_primary_strong, tokens.color_action_primary],
        ),
        padding=_pad_sym(32, 26),
        border_radius=18,
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
    )

    card = ft.Container(
        bgcolor=tokens.color_surface,
        padding=32,
        border_radius=16,
        border=_b_all(1, tokens.color_border),
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

    next_note = ft.Container(
        bgcolor=tokens.page_bg,
        padding=_pad_sym(20, 16),
        border_radius=12,
        content=ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.SCHEDULE_ROUNDED, color=tokens.color_action_primary, size=20),
                ft.Text(
                    "Next: scheduling and SFTP delivery. Those steps are on their way.",
                    size=13,
                    color=tokens.color_muted,
                ),
            ],
        ),
    )

    return ft.Column(spacing=22, controls=[header, card, next_note])
