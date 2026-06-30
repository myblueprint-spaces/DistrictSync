"""``PickerField`` — a themed Browse-a-folder control for the Flet UI.

VIEW glue (coverage-omitted in ``pyproject.toml``): this is a thin Flet view over
the trust-critical logic that lives COUNTED + tested in
``src/ui_flet/filepicker.py`` (the async picker wrapper + boundary validation).
``PickerField`` only wires a label + a "Browse…" button + a chosen-path display
+ an inline valid/invalid line, calling those tested helpers.

Reuse: every later file-picking surface (Convert, Mapping) drops this one
component in — one picker component, no duplication, no tkinter port (the
tkinter ``src/ui/folder_picker.py`` is deleted with Streamlit at CUT-1).

Follows the PROVEN Flet 0.85.3 control forms from ``src/ui_flet/shell.py`` /
``docs/FLET_1.0_CONVENTIONS.md`` — typed dataclass forms, ``ft.FilledButton``,
``ft.Padding`` (NOT the gone 0.2x ``ft.padding.*`` helpers).
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from src.ui_flet import components, tokens
from src.ui_flet.filepicker import ValidationResult, pick_directory


def _pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


class PickerField(ft.Column):  # pragma: no cover - Flet view glue (exercised via DISTRICTSYNC_UI=flet)
    """A labelled folder picker: Browse… → validate → show path + valid/invalid.

    Args:
        page: the Flet page (the picker registers a service on it).
        label: the field's plain-language label (e.g. "Input folder").
        helper: a one-line description under the label.
        validator: a pure path validator from ``filepicker`` (returns a
            ``ValidationResult``); the field renders its ``message`` and reports
            ``ok`` upward.
        on_change: called with ``(path, result)`` whenever the user picks a path
            (the surface uses it to re-evaluate the structural Save gate).
        dialog_title: native dialog title.
        initial_value: a pre-existing saved path (so re-opening Setup shows it).
    """

    def __init__(
        self,
        *,
        page: ft.Page,
        label: str,
        helper: str,
        validator: Callable[[str], ValidationResult],
        on_change: Callable[[str, ValidationResult], None],
        dialog_title: str,
        initial_value: str = "",
    ) -> None:
        self._page = page
        self._validator = validator
        self._on_change = on_change
        self._dialog_title = dialog_title
        self.value: str = initial_value

        self._path_text = ft.Text(
            initial_value or "No folder chosen yet",
            size=13,
            color=tokens.color_text if initial_value else tokens.color_muted,
            selectable=True,
        )
        self._status_text = ft.Text("", size=12, weight=ft.FontWeight.W_600)

        browse_btn = components.primary_button(
            "Browse…",
            self._on_browse,
            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
            radius=12,
            text_size=13,
            text_weight=ft.FontWeight.W_700,
        )

        super().__init__(
            spacing=8,
            controls=[
                ft.Text(label, size=14, weight=ft.FontWeight.W_700, color=tokens.color_text),
                ft.Text(helper, size=12, color=tokens.color_muted),
                ft.Row(
                    spacing=14,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        browse_btn,
                        ft.Container(
                            expand=True,
                            content=self._path_text,
                            bgcolor=tokens.page_bg,
                            border_radius=10,
                            padding=_pad_sym(12, 10),
                        ),
                    ],
                ),
                self._status_text,
            ],
        )

        # Reflect the initial (saved) value's validity on first paint.
        if initial_value:
            self._reflect(self._validator(initial_value))

    async def _on_browse(self, _e: ft.ControlEvent) -> None:
        chosen = await pick_directory(self._page, dialog_title=self._dialog_title, initial_directory=self.value or None)
        if chosen is None:
            return  # cancelled — keep the current value, never crash
        self.value = chosen
        self._path_text.value = chosen
        self._path_text.color = tokens.color_text
        result = self._validator(chosen)
        self._reflect(result)
        self._on_change(chosen, result)
        self._page.update()

    def _reflect(self, result: ValidationResult) -> None:
        self._status_text.value = ("✓ " if result.ok else "⚠ ") + result.message
        self._status_text.color = tokens.color_status_healthy if result.ok else tokens.color_status_failed
