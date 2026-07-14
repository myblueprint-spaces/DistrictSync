"""DistrictSync — Flet UI bake-off prototype (THROWAWAY spike).

Two screens (Home dashboard + Convert) on a shared app shell, built in Flet 0.85.3.
MOCK DATA ONLY — no real ETL, no SFTP, no tests. The point is to evaluate Flet's
production look, native lifecycle, native file dialog, and packaging.

Run modes
---------
  Native desktop (default):   <venvpy> app.py
  Web (headless screenshots): SPIKE_WEB=1 <venvpy> app.py   ->  http://localhost:8701

On startup prints ``SPIKE_PID=<pid>`` so a harness can capture the process tree.
Closing the native window destroys the window and the process exits cleanly.

Note on the Flet 0.85 API: the old convenience helpers (``ft.padding.symmetric``,
``ft.border.all``, …) were removed; this version uses the ``Padding`` / ``Border`` /
``BorderRadius`` dataclasses directly. The thin ``pad()`` / ``b_all()`` / ``b_only()``
helpers below restore readable call sites.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import flet as ft

# --------------------------------------------------------------------------- #
# Brand tokens (myBlueprint / SpacesEDU)                                       #
# --------------------------------------------------------------------------- #
NAVY = "#0F2D6B"
PRIMARY = "#1D5BB5"
SKY = "#0EA5E9"
SUCCESS = "#16A34A"
PAGE_TINT = "#F0F6FF"
BORDER = "#DBEAFE"
TEXT = "#0F172A"
MUTED = "#64748B"
WHITE = "#FFFFFF"
SUCCESS_TINT = "#ECFDF3"  # soft green wash for the health card
SUCCESS_BORDER = "#BBF7D0"

WEB_PORT = 8701

# Wordmark lives in the real repo; copied next to app.py as a served asset if present.
_REPO_WORDMARK = Path(
    "C:/Users/shan.peiris/Documents/Integrations/DistrictSync/docs/assets/spacesedu-wordmark.png"
)
_LOCAL_ASSETS = Path(__file__).parent / "assets"
_WORDMARK_ASSET = "spacesedu-wordmark.png"  # served path when asset exists


# --------------------------------------------------------------------------- #
# Flet 0.85 layout helpers (the old ft.padding.* / ft.border.* funcs are gone) #
# --------------------------------------------------------------------------- #
def pad(*, left=0, top=0, right=0, bottom=0) -> ft.Padding:
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)


def pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def b_all(width: float, color: str) -> ft.Border:
    side = ft.BorderSide(width, color)
    return ft.Border(top=side, bottom=side, left=side, right=side)


def b_only(*, top=None, right=None, bottom=None, left=None) -> ft.Border:
    return ft.Border(top=top, right=right, bottom=bottom, left=left)


# --------------------------------------------------------------------------- #
# Mock data                                                                    #
# --------------------------------------------------------------------------- #
DISTRICTS = [
    ("sd40", "New Westminster (sd40)"),
    ("sd48", "Sea to Sky (sd48)"),
    ("sd54", "Bulkley Valley (sd54)"),
]
MOCK_FILES = [
    "StudentDemographics.csv",
    "StudentSchedule.csv",
    "StaffInformation.csv",
    "CourseInformation.csv",
]
MOCK_RESULTS = [
    ("Students", "4,821"),
    ("Staff", "312"),
    ("Family", "5,120"),
    ("Classes", "642"),
    ("Enrollments", "11,890"),
]


# --------------------------------------------------------------------------- #
# Small reusable UI helpers                                                    #
# --------------------------------------------------------------------------- #
def card(content: ft.Control, *, padding: int = 24, expand: bool | int = False) -> ft.Container:
    """A rounded, bordered, soft-shadowed white card — the base surface unit."""
    return ft.Container(
        content=content,
        bgcolor=WHITE,
        padding=padding,
        border_radius=16,
        border=b_all(1, BORDER),
        expand=expand,
        shadow=ft.BoxShadow(
            blur_radius=18,
            spread_radius=0,
            color=ft.Colors.with_opacity(0.06, NAVY),
            offset=ft.Offset(0, 6),
        ),
    )


def metric_tile(label: str, value: str, *, accent: str = PRIMARY, icon: str | None = None) -> ft.Control:
    """A compact KPI tile: small muted label over a bold value, with a left accent bar."""
    head = ft.Row(
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(icon, size=16, color=accent) if icon else ft.Container(width=0),
            ft.Text(label.upper(), size=11, weight=ft.FontWeight.W_700, color=MUTED),
        ],
    )
    body = ft.Column(
        spacing=6,
        controls=[head, ft.Text(value, size=20, weight=ft.FontWeight.W_700, color=TEXT)],
    )
    return ft.Container(
        content=ft.Row(
            spacing=14,
            controls=[
                ft.Container(width=4, height=44, bgcolor=accent, border_radius=4),
                body,
            ],
        ),
        bgcolor=WHITE,
        padding=pad_sym(18, 16),
        border_radius=14,
        border=b_all(1, BORDER),
        expand=True,
    )


def primary_button(text: str, on_click, *, icon: str | None = None, disabled: bool = False) -> ft.FilledButton:
    """The single primary action per screen — brand-blue, generous hit area."""
    return ft.FilledButton(
        text,
        icon=icon,
        on_click=on_click,
        disabled=disabled,
        style=ft.ButtonStyle(
            bgcolor={ft.ControlState.DEFAULT: PRIMARY, ft.ControlState.DISABLED: "#9DB6DC"},
            color=WHITE,
            padding=pad_sym(26, 20),
            shape=ft.RoundedRectangleBorder(radius=12),
            text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_700),
        ),
    )


def secondary_link(text: str, on_click, *, icon: str | None = None) -> ft.TextButton:
    return ft.TextButton(
        text,
        icon=icon,
        on_click=on_click,
        style=ft.ButtonStyle(
            color=PRIMARY,
            text_style=ft.TextStyle(size=14, weight=ft.FontWeight.W_600),
        ),
    )


def section_label(number: str, title: str) -> ft.Control:
    """A numbered step header for the Convert flow."""
    return ft.Row(
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Container(
                content=ft.Text(number, size=14, weight=ft.FontWeight.W_700, color=WHITE),
                width=28,
                height=28,
                bgcolor=PRIMARY,
                border_radius=14,
                alignment=ft.Alignment(0, 0),
            ),
            ft.Text(title, size=16, weight=ft.FontWeight.W_700, color=TEXT),
        ],
    )


def lockup() -> ft.Control:
    """SpacesEDU / myBlueprint lockup — wordmark image if available, else a text lockup."""
    if (_LOCAL_ASSETS / _WORDMARK_ASSET).exists():
        return ft.Container(
            content=ft.Image(src=_WORDMARK_ASSET, height=22, fit=ft.BoxFit.CONTAIN),
            bgcolor=WHITE,
            padding=pad_sym(10, 6),
            border_radius=8,
        )
    return ft.Container(
        content=ft.Text("SpacesEDU — by myBlueprint", size=12, weight=ft.FontWeight.W_600, color=WHITE),
        padding=pad_sym(10, 6),
        border=b_all(1, ft.Colors.with_opacity(0.5, WHITE)),
        border_radius=8,
    )


def header_band(subtitle: str) -> ft.Control:
    """The branded navy->blue gradient header at the top of each screen's content."""
    return ft.Container(
        gradient=ft.LinearGradient(
            begin=ft.Alignment(-1, -1),
            end=ft.Alignment(1, 1),
            colors=[NAVY, PRIMARY],
        ),
        padding=pad_sym(32, 26),
        border_radius=18,
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Column(
                    spacing=4,
                    controls=[
                        ft.Text("DistrictSync", size=26, weight=ft.FontWeight.W_800, color=WHITE),
                        ft.Text(subtitle, size=14, color=ft.Colors.with_opacity(0.85, WHITE)),
                    ],
                ),
                lockup(),
            ],
        ),
    )


# --------------------------------------------------------------------------- #
# Screen 1 — Home (health dashboard)                                          #
# --------------------------------------------------------------------------- #
def build_home(go_to_run_history) -> ft.Control:
    verdict_card = ft.Container(
        bgcolor=SUCCESS_TINT,
        border=b_all(1, SUCCESS_BORDER),
        border_radius=18,
        padding=28,
        content=ft.Row(
            spacing=22,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    content=ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=40, color=WHITE),
                    width=72,
                    height=72,
                    bgcolor=SUCCESS,
                    border_radius=36,
                    alignment=ft.Alignment(0, 0),
                ),
                ft.Column(
                    spacing=6,
                    expand=True,
                    controls=[
                        ft.Text(
                            "Healthy — last night's sync succeeded",
                            size=22,
                            weight=ft.FontWeight.W_800,
                            color=TEXT,
                        ),
                        ft.Text(
                            "All five rostering files were delivered to SpacesEDU. No action needed.",
                            size=14,
                            color=MUTED,
                        ),
                    ],
                ),
                ft.Container(
                    content=ft.Text("ALL SYSTEMS GO", size=11, weight=ft.FontWeight.W_700, color=SUCCESS),
                    bgcolor=WHITE,
                    padding=pad_sym(12, 8),
                    border_radius=20,
                    border=b_all(1, SUCCESS_BORDER),
                ),
            ],
        ),
    )

    tiles = ft.Row(
        spacing=16,
        controls=[
            metric_tile("Last run", "Today 3:02 AM", accent=PRIMARY, icon=ft.Icons.HISTORY_ROUNDED),
            metric_tile("Next run", "Tonight 3:00 AM", accent=SKY, icon=ft.Icons.SCHEDULE_ROUNDED),
            metric_tile("Students delivered", "4,821", accent=NAVY, icon=ft.Icons.GROUPS_ROUNDED),
            metric_tile("SFTP", "Delivered ✓", accent=SUCCESS, icon=ft.Icons.CLOUD_DONE_ROUNDED),
        ],
    )

    district_strip = ft.Row(
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Icon(ft.Icons.LOCATION_CITY_ROUNDED, size=18, color=PRIMARY),
            ft.Text("District:", size=14, color=MUTED),
            ft.Text("New Westminster (sd40)", size=14, weight=ft.FontWeight.W_700, color=TEXT),
        ],
    )

    return ft.Column(
        spacing=22,
        controls=[
            header_band("Your nightly roster sync, at a glance."),
            district_strip,
            verdict_card,
            ft.Text("At a glance", size=13, weight=ft.FontWeight.W_700, color=MUTED),
            tiles,
            ft.Row(
                alignment=ft.MainAxisAlignment.END,
                controls=[
                    secondary_link("View run history", go_to_run_history, icon=ft.Icons.ARROW_FORWARD_ROUNDED),
                ],
            ),
        ],
    )


# --------------------------------------------------------------------------- #
# Screen 2 — Convert (ad-hoc run)                                             #
# --------------------------------------------------------------------------- #
class ConvertScreen:
    """Guided 4-step convert flow with native file picker + mock async run."""

    def __init__(self, page: ft.Page):
        self.page = page
        self.selected_files: list[str] = []
        self.running = False

        # File picker is a *service* in Flet 0.85 — registered on the page.
        self.file_picker = ft.FilePicker()
        if self.file_picker not in page.services:
            page.services.append(self.file_picker)

        # --- step 1: district dropdown ---
        self.district = ft.Dropdown(
            value="sd40",
            options=[ft.dropdown.Option(key=k, text=t) for k, t in DISTRICTS],
            leading_icon=ft.Icons.LOCATION_CITY_ROUNDED,
            border_color=BORDER,
            focused_border_color=PRIMARY,
            border_radius=12,
            width=420,
        )

        # --- step 2: files (chips) ---
        self.chips_row = ft.Row(wrap=True, spacing=8, run_spacing=8, controls=[])
        self.files_empty = ft.Text(
            "No files selected yet — choose your MyEd BC extract files.",
            size=13,
            color=MUTED,
            italic=True,
        )

        # --- step 3: run + progress ---
        self.run_btn = primary_button("Run conversion", self._on_run, icon=ft.Icons.PLAY_ARROW_ROUNDED)
        self.progress = ft.Row(
            visible=False,
            spacing=14,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.ProgressRing(width=22, height=22, stroke_width=3, color=PRIMARY),
                ft.Text("Converting… transforming entities and writing CSVs", size=14, color=MUTED),
            ],
        )

        # --- step 4: results (hidden until a run completes) ---
        self.results_container = ft.Container(visible=False)

    # ---- file picking -------------------------------------------------- #
    async def _pick(self, _e):
        files = await self.file_picker.pick_files(
            dialog_title="Select MyEd BC extract files",
            allow_multiple=True,
            allowed_extensions=["csv", "txt"],
        )
        if files:
            for f in files:
                name = Path(f.path).name if f.path else "selected file"
                if name not in self.selected_files:
                    self.selected_files.append(name)
        self._render_chips()
        self.page.update()

    def _use_mock_files(self, _e):
        # Convenience for headless/demo: seed plausible filenames without a dialog.
        for name in MOCK_FILES:
            if name not in self.selected_files:
                self.selected_files.append(name)
        self._render_chips()
        self.page.update()

    def _remove_file(self, name: str):
        def handler(_e):
            self.selected_files = [f for f in self.selected_files if f != name]
            self._render_chips()
            self.page.update()

        return handler

    def _render_chips(self):
        if not self.selected_files:
            self.chips_row.controls = [self.files_empty]
            return
        chips = []
        for name in self.selected_files:
            chips.append(
                ft.Container(
                    bgcolor=PAGE_TINT,
                    border=b_all(1, BORDER),
                    border_radius=20,
                    padding=pad(left=14, right=8, top=6, bottom=6),
                    content=ft.Row(
                        spacing=8,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(ft.Icons.DESCRIPTION_ROUNDED, size=15, color=PRIMARY),
                            ft.Text(name, size=13, weight=ft.FontWeight.W_600, color=TEXT),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE_ROUNDED,
                                icon_size=14,
                                icon_color=MUTED,
                                on_click=self._remove_file(name),
                                tooltip="Remove",
                            ),
                        ],
                    ),
                )
            )
        self.chips_row.controls = chips

    # ---- run flow ------------------------------------------------------ #
    async def _on_run(self, _e):
        if self.running:
            return
        self.running = True
        self.results_container.visible = False
        self.run_btn.disabled = True
        self.progress.visible = True
        self.page.update()

        await asyncio.sleep(1.5)  # mock ETL work

        self._build_results()
        self.progress.visible = False
        self.run_btn.disabled = False
        self.results_container.visible = True
        self.running = False
        self.page.update()

    def _build_results(self):
        table = ft.DataTable(
            heading_row_color=PAGE_TINT,
            heading_row_height=44,
            data_row_max_height=48,
            column_spacing=40,
            border=b_all(1, BORDER),
            border_radius=12,
            columns=[
                ft.DataColumn(ft.Text("Output entity", weight=ft.FontWeight.W_700, color=TEXT)),
                ft.DataColumn(ft.Text("Rows", weight=ft.FontWeight.W_700, color=TEXT), numeric=True),
            ],
            rows=[
                ft.DataRow(
                    cells=[
                        ft.DataCell(
                            ft.Row(
                                spacing=8,
                                controls=[
                                    ft.Icon(ft.Icons.TABLE_ROWS_ROUNDED, size=15, color=PRIMARY),
                                    ft.Text(entity, color=TEXT),
                                ],
                            )
                        ),
                        ft.DataCell(ft.Text(count, weight=ft.FontWeight.W_600, color=TEXT)),
                    ]
                )
                for entity, count in MOCK_RESULTS
            ],
        )

        verdict = ft.Container(
            bgcolor=SUCCESS_TINT,
            border=b_all(1, SUCCESS_BORDER),
            border_radius=12,
            padding=pad_sym(18, 14),
            content=ft.Row(
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=22, color=SUCCESS),
                    ft.Text(
                        "Looks healthy — no problems found.",
                        size=15,
                        weight=ft.FontWeight.W_700,
                        color=TEXT,
                    ),
                ],
            ),
        )

        download = ft.FilledButton(
            "Download CSVs",
            icon=ft.Icons.DOWNLOAD_ROUNDED,
            on_click=self._on_download,
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.DEFAULT: NAVY},
                color=WHITE,
                padding=pad_sym(22, 18),
                shape=ft.RoundedRectangleBorder(radius=12),
                text_style=ft.TextStyle(size=14, weight=ft.FontWeight.W_700),
            ),
        )

        self.results_container.content = card(
            ft.Column(
                spacing=18,
                controls=[
                    section_label("4", "Results"),
                    verdict,
                    table,
                    ft.Row(alignment=ft.MainAxisAlignment.END, controls=[download]),
                ],
            )
        )

    def _on_download(self, _e):
        self.page.show_dialog(
            ft.SnackBar(
                content=ft.Text("Saved 5 CSV files to your output folder (mock).", color=WHITE),
                bgcolor=SUCCESS,
            )
        )

    # ---- assembly ------------------------------------------------------ #
    def build(self) -> ft.Control:
        self._render_chips()

        step1 = card(
            ft.Column(
                spacing=14,
                controls=[
                    section_label("1", "Choose your district"),
                    ft.Text("We'll load that district's column mappings.", size=13, color=MUTED),
                    self.district,
                ],
            )
        )

        pick_buttons = ft.Row(
            spacing=12,
            controls=[
                ft.OutlinedButton(
                    "Choose files…",
                    icon=ft.Icons.UPLOAD_FILE_ROUNDED,
                    on_click=self._pick,
                    style=ft.ButtonStyle(
                        color=PRIMARY,
                        side=ft.BorderSide(1.5, PRIMARY),
                        padding=pad_sym(20, 18),
                        shape=ft.RoundedRectangleBorder(radius=12),
                        text_style=ft.TextStyle(size=14, weight=ft.FontWeight.W_700),
                    ),
                ),
                ft.TextButton(
                    "Use sample files",
                    icon=ft.Icons.AUTO_AWESOME_ROUNDED,
                    on_click=self._use_mock_files,
                    style=ft.ButtonStyle(color=MUTED),
                ),
            ],
        )

        step2 = card(
            ft.Column(
                spacing=14,
                controls=[
                    section_label("2", "Add your extract files"),
                    ft.Text(
                        "Opens your computer's native file dialog. CSV or TXT.",
                        size=13,
                        color=MUTED,
                    ),
                    pick_buttons,
                    ft.Container(content=self.chips_row, padding=pad_sym(0, 4)),
                ],
            )
        )

        step3 = card(
            ft.Column(
                spacing=16,
                controls=[
                    section_label("3", "Run the conversion"),
                    ft.Text(
                        "Transforms your extracts into the SpacesEDU rostering CSVs.",
                        size=13,
                        color=MUTED,
                    ),
                    ft.Row(
                        spacing=20,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[self.run_btn, self.progress],
                    ),
                ],
            )
        )

        return ft.Column(
            spacing=22,
            controls=[
                header_band("Run a conversion now — guided, in four short steps."),
                step1,
                step2,
                step3,
                self.results_container,
            ],
        )


# --------------------------------------------------------------------------- #
# Placeholder screen for inert nav items                                      #
# --------------------------------------------------------------------------- #
def build_placeholder(title: str, icon: str) -> ft.Control:
    return ft.Column(
        spacing=22,
        controls=[
            header_band("This area is part of the full DistrictSync app."),
            card(
                ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=14,
                    controls=[
                        ft.Container(
                            content=ft.Icon(icon, size=36, color=PRIMARY),
                            width=80,
                            height=80,
                            bgcolor=PAGE_TINT,
                            border_radius=40,
                            alignment=ft.Alignment(0, 0),
                        ),
                        ft.Text(title, size=20, weight=ft.FontWeight.W_700, color=TEXT),
                        ft.Text(
                            "Available in the full application. Not part of this prototype.",
                            size=14,
                            color=MUTED,
                        ),
                    ],
                ),
                padding=48,
            ),
        ],
    )


# --------------------------------------------------------------------------- #
# App shell + lifecycle                                                        #
# --------------------------------------------------------------------------- #
def main(page: ft.Page):
    page.title = "DistrictSync"
    page.padding = 0
    page.bgcolor = PAGE_TINT
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(
        color_scheme_seed=PRIMARY,
        use_material3=True,
        color_scheme=ft.ColorScheme(
            primary=PRIMARY,
            secondary=SKY,
            surface=WHITE,
            on_primary=WHITE,
        ),
        font_family="Segoe UI",
        visual_density=ft.VisualDensity.COMFORTABLE,
    )

    # --- window sizing + icon (native mode only; harmless in web) ---------- #
    try:
        page.window.width = 1180
        page.window.height = 860
        page.window.min_width = 940
        page.window.min_height = 680
        # NOTE: page.window.center() is a coroutine in Flet 0.85; calling it
        # synchronously triggers a RuntimeWarning. The window centers by default,
        # so we simply rely on that rather than awaiting here.
        if (_LOCAL_ASSETS / _WORDMARK_ASSET).exists():
            page.window.icon = _WORDMARK_ASSET
    except Exception:
        pass

    # --- content host ------------------------------------------------------ #
    content_host = ft.Container(expand=True, padding=pad_sym(36, 28))
    convert = ConvertScreen(page)

    def render(index: int):
        if index == 0:
            inner = build_home(lambda _e: select(2))
        elif index == 1:
            inner = convert.build()
        elif index == 2:
            inner = build_placeholder("Run History", ft.Icons.HISTORY_ROUNDED)
        elif index == 3:
            inner = build_placeholder("Setup", ft.Icons.SETTINGS_ROUNDED)
        else:
            inner = build_placeholder("Help", ft.Icons.HELP_OUTLINE_ROUNDED)
        # Scrollable content column so tall screens never clip.
        content_host.content = ft.Column(controls=[inner], scroll=ft.ScrollMode.AUTO, expand=True)

    def select(index: int):
        rail.selected_index = index
        render(index)
        page.update()

    def on_nav_change(e: ft.ControlEvent):
        select(e.control.selected_index)

    # --- Exit affordance --------------------------------------------------- #
    def do_exit(_e=None):
        # Destroy the native window -> Flet tears down the local server and the
        # process exits. In web mode there is no window; this is a no-op there.
        try:
            page.window.destroy()
        except Exception:
            os._exit(0)

    exit_btn = ft.Container(
        content=ft.TextButton(
            "Exit",
            icon=ft.Icons.LOGOUT_ROUNDED,
            on_click=do_exit,
            style=ft.ButtonStyle(color=MUTED, text_style=ft.TextStyle(size=12, weight=ft.FontWeight.W_600)),
        ),
        padding=pad(bottom=12),
    )

    # --- left navigation rail ---------------------------------------------- #
    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=104,
        min_extended_width=180,
        bgcolor=WHITE,
        indicator_color=ft.Colors.with_opacity(0.14, PRIMARY),
        on_change=on_nav_change,
        leading=ft.Container(
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
                controls=[
                    ft.Container(
                        content=ft.Icon(ft.Icons.SYNC_ROUNDED, color=WHITE, size=22),
                        width=42,
                        height=42,
                        bgcolor=PRIMARY,
                        border_radius=12,
                        alignment=ft.Alignment(0, 0),
                    ),
                    ft.Text("District", size=11, weight=ft.FontWeight.W_700, color=NAVY),
                    ft.Text("Sync", size=11, weight=ft.FontWeight.W_700, color=PRIMARY),
                ],
            ),
            padding=pad(top=14, bottom=18),
        ),
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.HOME_OUTLINED, selected_icon=ft.Icons.HOME_ROUNDED, label="Home"
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SYNC_ALT_OUTLINED, selected_icon=ft.Icons.SYNC_ALT_ROUNDED, label="Convert"
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.HISTORY_OUTLINED, selected_icon=ft.Icons.HISTORY_ROUNDED, label="Run History"
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SETTINGS_OUTLINED, selected_icon=ft.Icons.SETTINGS_ROUNDED, label="Setup"
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.HELP_OUTLINE_ROUNDED, selected_icon=ft.Icons.HELP_ROUNDED, label="Help"
            ),
        ],
        trailing=ft.Container(
            expand=True,
            content=ft.Column(
                alignment=ft.MainAxisAlignment.END,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
                controls=[exit_btn],
            ),
        ),
    )

    rail_wrap = ft.Container(
        content=rail,
        bgcolor=WHITE,
        border=b_only(right=ft.BorderSide(1, BORDER)),
    )

    page.add(ft.Row(spacing=0, expand=True, controls=[rail_wrap, content_host]))

    # --- graceful window-close handling (native) --------------------------- #
    def on_window_event(e):
        # Flet 0.85 delivers window events as WindowEventType; "close" => tear down.
        etype = getattr(e, "type", None)
        if etype == ft.WindowEventType.CLOSE or getattr(e, "data", None) == "close":
            try:
                page.window.destroy()
            except Exception:
                os._exit(0)

    try:
        # prevent_close=False -> the OS close button tears the app down on its own;
        # we still bind the handler so an explicit close path always destroys cleanly.
        page.window.prevent_close = False
        page.window.on_event = on_window_event
    except Exception:
        pass

    # When the desktop client disconnects, make sure the host process doesn't orphan.
    def on_disconnect(_e):
        if os.environ.get("SPIKE_WEB") != "1":
            os._exit(0)

    page.on_disconnect = on_disconnect

    render(0)
    page.update()


def _prepare_assets():
    """Copy the brand wordmark next to app.py so Flet can serve it as an asset."""
    try:
        _LOCAL_ASSETS.mkdir(exist_ok=True)
        dest = _LOCAL_ASSETS / _WORDMARK_ASSET
        if _REPO_WORDMARK.exists() and not dest.exists():
            shutil.copyfile(_REPO_WORDMARK, dest)
    except Exception:
        pass  # text lockup is the fallback


if __name__ == "__main__":
    _prepare_assets()
    # Harness hook: capture our PID for process-tree teardown verification.
    print(f"SPIKE_PID={os.getpid()}", flush=True)

    assets_dir = str(_LOCAL_ASSETS) if _LOCAL_ASSETS.exists() else None

    if os.environ.get("SPIKE_WEB") == "1":
        # Headless web mode on the fixed bake-off port; do not auto-open a browser.
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=WEB_PORT, assets_dir=assets_dir)
    else:
        # Default: native desktop window. Closing it exits the process cleanly.
        ft.run(main, assets_dir=assets_dir)
