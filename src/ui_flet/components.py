"""Design-system component factories for the Flet UI — the reusable view layer.

**Single source for the Direction B ("Branded Professional") design system**
(``docs/DESIGN_SYSTEM.md`` + the ``districtsync-design`` skill). Screens NEVER
hand-roll a button/card/band/pill/chip — they call these factories, which size
against the ``tokens`` scales (``space_*`` / ``radius_*`` / ``type_*``) and paint
the AA-gated Direction B roles. Changing a style = edit a token + a factory here,
once; the inline hex/size never leaks into a screen.

VIEW glue (coverage-omitted in ``pyproject.toml``): the trust-critical *intent*
lives COUNTED in the pure modules (``tokens`` values, ``verdict`` mapping); this
file only assembles ``ft.Control``s from them.

The component inventory (see ``docs/DESIGN_SYSTEM.md`` for usage rules):
  * ``page_header`` — the slim white page header (title + sub + optional right
    slot). Replaces the gradient hero as the top-of-screen element (Slice 2).
  * ``section_label`` — a muted uppercased caps label introducing a group (the
    mockup's "Latest roster" over a tile row).
  * ``primary_button`` — the ONE filled brand-blue action per screen
    (``FilledButton``; hover/pressed/focused states; ``disabled_bgcolor`` carries
    the security Save-gate fill in ``setup.py``).
  * ``secondary_button`` — an **OUTLINED** action (white bg, soft-blue border,
    MB_PRIMARY text). Direction B inverts the old filled-navy secondary so the
    single filled primary keeps the visual weight.
  * ``text_button`` — the tertiary text-tier action (MB_PRIMARY text).
  * ``card`` — the bordered white ``Container`` (radius ``radius_lg`` + a 1dp
    shadow) or, with a ``gradient``, a hero header (onboarding only after Slice 2).
  * ``metric_tile`` — a calm tile: a navy ``type_metric`` numeral over a muted caps
    caption.
  * ``HealthVerdictBanner`` — the verdict-first band: a toned tint + 1px line + a
    SOLID status-colour icon disc + deep on-tint headline/detail + optional trailing.
  * ``district_chip`` / ``status_pill`` — the rounded pills (district identity /
    a toned status marker).
  * ``FileChip`` / ``run_table`` / ``ErrorCard`` — the file chip, run-history table,
    and never-crash error surface.

Follows the PROVEN Flet 0.85.3 forms from ``shell.py`` verbatim
(``ft.Padding``/``ft.Border``/``ft.ButtonStyle``/``ft.ControlState``/``ft.BoxShadow``
/``ft.OutlinedButton`` — NOT the gone 0.2x helpers; see
``docs/FLET_1.0_CONVENTIONS.md``). Light-only.
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from src.ui_flet import tokens
from src.ui_flet.home_status import (
    _MYBLUEPRINT_ENTITIES,
    _ROSTERING_ENTITIES,
    ENTITY_LABELS,
)
from src.ui_flet.run_history import RunRow, SftpDelivery
from src.ui_flet.verdict import Verdict, verdict_visuals


# --------------------------------------------------------------------------- #
# Layout helpers — verbatim from shell.py (the 0.2x ft.padding.*/ft.border.* gone) #
# --------------------------------------------------------------------------- #
def _pad_sym(h: float = 0, v: float = 0) -> ft.Padding:
    return ft.Padding(left=h, top=v, right=h, bottom=v)


def _b_all(width: float, color: str) -> ft.Border:
    side = ft.BorderSide(width, color)
    return ft.Border(top=side, bottom=side, left=side, right=side)


def _card_shadow() -> ft.BoxShadow:
    """The subtle 1dp card shadow (Direction B ``box-shadow: 0 1px 2px rgba(15,23,42,.05)``)."""
    return ft.BoxShadow(
        blur_radius=2,
        offset=ft.Offset(0, 1),
        color=ft.Colors.with_opacity(0.05, tokens.MB_TEXT),
    )


# --------------------------------------------------------------------------- #
# Buttons                                                                       #
# --------------------------------------------------------------------------- #
def _state_map(
    default: str,
    *,
    hover: str | None = None,
    pressed: str | None = None,
    focused: str | None = None,
    disabled: str | None = None,
) -> dict[ft.ControlState, str]:
    """A ``ft.ControlState`` → colour map (DEFAULT + any supplied interaction states)."""
    state_map: dict[ft.ControlState, str] = {ft.ControlState.DEFAULT: default}
    if hover is not None:
        state_map[ft.ControlState.HOVERED] = hover
    if pressed is not None:
        state_map[ft.ControlState.PRESSED] = pressed
    if focused is not None:
        state_map[ft.ControlState.FOCUSED] = focused
    if disabled is not None:
        state_map[ft.ControlState.DISABLED] = disabled
    return state_map


def _filled_button(
    text: str,
    on_click: Callable[..., None] | None,
    *,
    bgcolor: str,
    hover_bgcolor: str,
    pressed_bgcolor: str,
    focused_bgcolor: str,
    disabled: bool,
    disabled_bgcolor: str | None,
    icon: str | None,
    radius: float,
    text_size: float,
    text_weight: ft.FontWeight,
) -> ft.FilledButton:
    """Shared ``FilledButton`` shape — the ONE filled action, with interaction states.

    Hover/pressed/focused darken the brand-blue fill (Direction B "one filled primary
    that reacts"); ``disabled_bgcolor`` (when given) carries the security Save-gate fill.
    Flet 0.85.3: the button label is ``content`` (``text=`` raises — see
    ``docs/FLET_1.0_CONVENTIONS.md``); per-state colours key off ``ft.ControlState``.
    """
    return ft.FilledButton(
        content=text,
        icon=icon,
        on_click=on_click,
        disabled=disabled,
        style=ft.ButtonStyle(
            bgcolor=_state_map(
                bgcolor,
                hover=hover_bgcolor,
                pressed=pressed_bgcolor,
                focused=focused_bgcolor,
                disabled=disabled_bgcolor,
            ),
            color=tokens.color_on_action,
            shape=ft.RoundedRectangleBorder(radius=radius),
            text_style=ft.TextStyle(size=text_size, weight=text_weight),
        ),
    )


def _outlined_button(
    text: str,
    on_click: Callable[..., None] | None,
    *,
    disabled: bool,
    disabled_bgcolor: str | None,
    icon: str | None,
    radius: float,
    text_size: float,
    text_weight: ft.FontWeight,
) -> ft.OutlinedButton:
    """Shared OUTLINED shape — white bg, soft-blue border, MB_PRIMARY label/icon.

    Direction B's secondary tier (``ft.OutlinedButton``, confirmed on 0.85.3 to take
    ``content``/``icon``/``style`` like ``FilledButton``): the border firms to
    MB_PRIMARY on hover with a faint chip-blue overlay. ``disabled_bgcolor`` (when
    given) still keys a DISABLED fill so a gated secondary reads as inert. The label
    is ``content`` (same 0.85.3 rule as ``FilledButton``).
    """
    return ft.OutlinedButton(
        content=text,
        icon=icon,
        on_click=on_click,
        disabled=disabled,
        style=ft.ButtonStyle(
            color=tokens.color_action_primary,
            icon_color=tokens.color_action_primary,
            bgcolor=_state_map(tokens.color_surface, disabled=disabled_bgcolor),
            side=_side_map(
                default=ft.BorderSide(1, tokens.color_action_outline),
                hover=ft.BorderSide(1, tokens.color_action_primary),
            ),
            overlay_color=_state_map(ft.Colors.TRANSPARENT, hover=tokens.color_chip_bg),
            shape=ft.RoundedRectangleBorder(radius=radius),
            text_style=ft.TextStyle(size=text_size, weight=text_weight),
        ),
    )


def _side_map(*, default: ft.BorderSide, hover: ft.BorderSide) -> dict[ft.ControlState, ft.BorderSide]:
    """A ``ft.ControlState`` → ``ft.BorderSide`` map (DEFAULT + HOVERED) for outlined buttons."""
    return {ft.ControlState.DEFAULT: default, ft.ControlState.HOVERED: hover}


def primary_button(
    text: str,
    on_click: Callable[..., None] | None = None,
    *,
    disabled: bool = False,
    disabled_bgcolor: str | None = None,
    icon: str | None = None,
    radius: float = tokens.radius_sm,
    text_size: float = tokens.type_emphasis,
    text_weight: ft.FontWeight = ft.FontWeight.W_700,
) -> ft.FilledButton:
    """The ONE filled (brand-blue) primary action per screen — verdict-first CTA.

    Darkens MB_DARK-ward on hover/focus (``color_action_primary_hover``) and to the
    rail navy on press. ``disabled_bgcolor`` is the disabled-state fill — pass it to
    reproduce a gated button's disabled appearance (the security Save-gate in
    ``setup.py`` passes ``tokens.color_border``); omitting it leaves no DISABLED entry
    (call-sites that never disable, e.g. ``picker_field``'s Browse).
    """
    return _filled_button(
        text,
        on_click,
        bgcolor=tokens.color_action_primary,
        hover_bgcolor=tokens.color_action_primary_hover,
        pressed_bgcolor=tokens.color_action_primary_strong,
        focused_bgcolor=tokens.color_action_primary_hover,
        disabled=disabled,
        disabled_bgcolor=disabled_bgcolor,
        icon=icon,
        radius=radius,
        text_size=text_size,
        text_weight=text_weight,
    )


def secondary_button(
    text: str,
    on_click: Callable[..., None] | None = None,
    *,
    disabled: bool = False,
    disabled_bgcolor: str | None = None,
    icon: str | None = None,
    radius: float = tokens.radius_sm,
    text_size: float = tokens.type_emphasis,
    text_weight: ft.FontWeight = ft.FontWeight.W_700,
) -> ft.OutlinedButton:
    """A secondary action — Direction B OUTLINED (white bg, soft-blue border, blue text).

    Global hierarchy fix: the old filled-navy secondary competed with the single filled
    primary. Same signature (``disabled_bgcolor`` etc. preserved) so every existing
    caller keeps working — only the visual tier changes.
    """
    return _outlined_button(
        text,
        on_click,
        disabled=disabled,
        disabled_bgcolor=disabled_bgcolor,
        icon=icon,
        radius=radius,
        text_size=text_size,
        text_weight=text_weight,
    )


def text_button(
    text: str,
    on_click: Callable[..., None] | None = None,
    *,
    icon: str | None = None,
    color: str = tokens.color_action_primary,
    text_size: float = tokens.type_body,
    text_weight: ft.FontWeight = ft.FontWeight.W_600,
) -> ft.TextButton:
    """The tertiary text-tier action (MB_PRIMARY text; e.g. the shell's Exit, Cancel).

    ``color`` is overridable so a surface on a coloured ground (e.g. Slice 2's navy
    rail) can pass its own on-ground colour without a new factory.
    """
    return ft.TextButton(
        text,
        icon=icon,
        on_click=on_click,
        style=ft.ButtonStyle(
            color=color,
            text_style=ft.TextStyle(size=text_size, weight=text_weight),
        ),
    )


# --------------------------------------------------------------------------- #
# Cards                                                                          #
# --------------------------------------------------------------------------- #
def card(
    content: ft.Control,
    *,
    gradient: ft.Gradient | None = None,
    bgcolor: str = tokens.color_surface,
    padding: ft.Padding | float = tokens.space_2xl,
    border_radius: float = tokens.radius_lg,
    bordered: bool = True,
) -> ft.Container:
    """A surface container: a bordered white card (Direction B), or a gradient hero.

    Default = a white ``radius_lg`` card with an MB_BORDER hairline and the subtle 1dp
    shadow the mockup uses (cards *float* a hair above the content wash). ``gradient``
    (when given) paints a hero header instead — border dropped, no shadow (the
    onboarding hero after Slice 2). ``metric_tile`` builds on this exact surface.
    """
    is_hero = gradient is not None
    border = _b_all(1, tokens.color_border) if (bordered and not is_hero) else None
    return ft.Container(
        content=content,
        gradient=gradient,
        bgcolor=None if is_hero else bgcolor,
        padding=padding,
        border_radius=border_radius,
        border=border,
        shadow=None if is_hero else _card_shadow(),
    )


def hero_gradient() -> ft.LinearGradient:
    """The brand diagonal navy→blue hero gradient (shell placeholder, Setup header)."""
    return ft.LinearGradient(
        begin=ft.Alignment(-1, -1),
        end=ft.Alignment(1, 1),
        colors=[tokens.color_action_primary_strong, tokens.color_action_primary],
    )


# --------------------------------------------------------------------------- #
# Metric tile — one big value over a muted caption (shared by Home + Convert)   #
# --------------------------------------------------------------------------- #
def metric_tile(label: str, value: str) -> ft.Container:
    """One calm metric tile: a navy ``type_metric`` numeral over a muted caps caption.

    Direction B: the value is the screen's quiet focal point — a 26px MB_DARK numeral
    (letter-spaced tight, mimicking the mockup's tabular figures; Flet 0.85.3 ``TextStyle``
    has no ``font-variant-numeric``, so the tabular look is approximated via the tight
    tracking). The label is an uppercased, letter-spaced ``type_caption`` in muted slate.
    The single source of the entity-count / status tile shape (Home + Convert render a
    row of these).
    """
    return card(
        content=ft.Column(
            spacing=tokens.space_xs,
            controls=[
                ft.Text(
                    value,
                    size=tokens.type_metric,
                    weight=ft.FontWeight.W_700,
                    color=tokens.MB_DARK,
                    style=ft.TextStyle(letter_spacing=-0.3),
                ),
                ft.Text(
                    label.upper(),
                    size=tokens.type_caption,
                    weight=ft.FontWeight.W_700,
                    color=tokens.color_muted,
                    style=ft.TextStyle(letter_spacing=0.8),
                ),
            ],
        ),
        padding=_pad_sym(tokens.space_lg, tokens.space_md),
    )


# --------------------------------------------------------------------------- #
# Verdict → toned-surface mapping (shared by the band + the status pill)         #
# --------------------------------------------------------------------------- #
# Each verdict's calm surface triplet: (tint bg, 1px line, deep on-tint text). The
# SOLID icon-disc colour stays ``verdict_visuals(v).color`` (the saturated status
# hue) — so the band/pill background is never a saturated fill behind body text
# (Direction B: "toned bands, not saturated fills"). Every pair here is AA-gated in
# ``tokens.UI_CONTRAST_PAIRS``.
_VERDICT_TINTS: dict[Verdict, tuple[str, str, str]] = {
    Verdict.HEALTHY: (
        tokens.color_status_healthy_tint,
        tokens.color_status_healthy_line,
        tokens.color_on_healthy_tint,
    ),
    Verdict.WARNING: (
        tokens.color_status_warning_tint,
        tokens.color_status_warning_line,
        tokens.color_on_warning_tint,
    ),
    Verdict.FAILED: (
        tokens.color_status_failed_tint,
        tokens.color_status_failed_line,
        tokens.color_on_failed_tint,
    ),
}


# --------------------------------------------------------------------------- #
# Page header — the slim white top-of-screen block (replaces the gradient hero) #
# --------------------------------------------------------------------------- #
def page_header(
    title: str,
    subtitle: str | None = None,
    trailing: ft.Control | None = None,
) -> ft.Control:
    """The slim page header: a ``type_title`` title, optional ``type_body`` sub, right slot.

    Direction B's top-of-screen element — quiet and white/transparent (NO gradient, NO
    card), so the verdict band directly under it owns the colour. ``trailing`` (a
    ``district_chip`` / a ``secondary_button`` / etc.) sits pushed to the right. Slice 2
    wires this into every screen in place of the old gradient hero (the hero gradient is
    reserved for the first-run onboarding).
    """
    left = ft.Column(
        spacing=tokens.space_xs // 2,
        controls=[ft.Text(title, size=tokens.type_title, weight=ft.FontWeight.W_700, color=tokens.color_text)],
    )
    if subtitle:
        left.controls.append(ft.Text(subtitle, size=tokens.type_body, color=tokens.color_muted))

    row_controls: list[ft.Control] = [ft.Container(content=left, expand=True)]
    if trailing is not None:
        row_controls.append(trailing)

    return ft.Container(
        padding=_pad_sym(0, tokens.space_sm),
        content=ft.Row(
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=row_controls,
        ),
    )


def section_label(text: str) -> ft.Control:
    """A small uppercased caps label introducing a group (Direction B ``.section-label``).

    An uppercased, letter-spaced ``type_caption`` in muted slate — the quiet header the mockup
    puts above a metric-tile row ("Latest roster"). Muted-on-wash / muted-on-white are both
    AA-gated (``UI_CONTRAST_PAIRS``). Assembly, not styling: screens call this rather than
    hand-rolling the caps ``ft.Text``.
    """
    return ft.Container(
        padding=ft.Padding(left=tokens.space_xs // 2, top=0, right=0, bottom=0),
        content=ft.Text(
            text.upper(),
            size=tokens.type_caption,
            weight=ft.FontWeight.W_700,
            color=tokens.color_muted,
            style=ft.TextStyle(letter_spacing=0.8),
        ),
    )


# --------------------------------------------------------------------------- #
# Chips & pills — rounded identity / status markers                             #
# --------------------------------------------------------------------------- #
def district_chip(label: str) -> ft.Container:
    """A rounded district-identity pill: a small building glyph + the friendly district.

    Direction B's page-header right-slot marker (``color_chip_bg`` fill, MB_BORDER
    border, fully rounded). The label is a config-derived friendly district name (never
    PII), shown verbatim.
    """
    return ft.Container(
        bgcolor=tokens.color_chip_bg,
        border=_b_all(1, tokens.color_border),
        border_radius=999,
        padding=ft.Padding(
            left=tokens.space_sm, top=tokens.space_xs + 1, right=tokens.space_md, bottom=tokens.space_xs + 1
        ),
        content=ft.Row(
            spacing=tokens.space_sm - 1,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.APARTMENT_ROUNDED, size=tokens.type_emphasis, color=tokens.color_action_primary),
                ft.Text(label, size=tokens.type_caption, weight=ft.FontWeight.W_600, color=tokens.color_text),
            ],
        ),
    )


def status_pill(label: str, status: Verdict) -> ft.Container:
    """A rounded status pill: a status-colour glyph + a deep on-tint label, toned per status.

    The compact sibling of the verdict band (Direction B: the Home schedule "Confirmed"
    marker). A non-colour cue is always present — the ``verdict_visuals`` icon rides
    alongside the text (never colour-alone). ``status`` is a :class:`Verdict`, so the
    three tints/lines/on-tint text are the single-sourced ones the band uses.
    """
    tint, line, on_tint = _VERDICT_TINTS[status]
    visual = verdict_visuals(status)
    icon = getattr(ft.Icons, visual.icon, ft.Icons.INFO_ROUNDED)
    return ft.Container(
        bgcolor=tint,
        border=_b_all(1, line),
        border_radius=999,
        padding=ft.Padding(left=tokens.space_sm, top=tokens.space_xs, right=tokens.space_md, bottom=tokens.space_xs),
        content=ft.Row(
            spacing=tokens.space_xs + 2,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(icon, size=tokens.type_body, color=visual.color),
                ft.Text(label, size=tokens.type_caption, weight=ft.FontWeight.W_700, color=on_tint),
            ],
        ),
    )


# --------------------------------------------------------------------------- #
# run_table — the first ft.DataTable consumer (Run History)                     #
# --------------------------------------------------------------------------- #
# The 5 rostering entities always shown, then the 2 myBlueprint+ entities shown
# only when a row has them — mirroring the `home_status`/Home tile rule. Each entry
# is (entity key -> column header). Both the entity ORDER (the `home_status` tuples)
# and the entity→label fact (`home_status.ENTITY_LABELS`) are single-sourced — this
# `run_table` and the pure `mapping_catalog` read ONE definition, so a label rename
# (e.g. "Courses") changes every surface at once (DRY).
_ROW_ROSTERING_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (key, ENTITY_LABELS.get(key, key)) for key in _ROSTERING_ENTITIES
)
_ROW_MYBLUEPRINT_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (key, ENTITY_LABELS.get(key, key)) for key in _MYBLUEPRINT_ENTITIES
)

# The SFTP glyph + word the view paints per delivery axis (a non-colour cue — never colour-only).
_SFTP_GLYPHS: dict[SftpDelivery, str] = {
    SftpDelivery.DELIVERED: "✓ Delivered",
    SftpDelivery.FAILED: "✗ Failed",
    SftpDelivery.NOT_ATTEMPTED: "—",
}

# A subtle, AA-safe verdict row tint (text stays the primary signal; the tint is a secondary cue).
_ROW_TINTS: dict[Verdict, str] = {
    Verdict.FAILED: ft.Colors.with_opacity(0.06, tokens.color_status_failed),
    Verdict.WARNING: ft.Colors.with_opacity(0.06, tokens.color_status_warning),
}


def _cell(text: str, *, weight: ft.FontWeight = ft.FontWeight.W_500, color: str = tokens.color_text) -> ft.DataCell:
    """One ``ft.DataCell`` wrapping a ``ft.Text`` of a uniform string (the coerce-to-string rule)."""
    return ft.DataCell(content=ft.Text(text, size=13, weight=weight, color=color))


def _source_cell(row: RunRow) -> ft.DataCell:
    """The Source cell: the friendly origin label, with the muted different-district note beneath.

    Both strings come pre-derived from the pure ``RunRow`` (bounded vocabulary / district display
    — never a raw record value); this only stacks them when the note is present.
    """
    if not row.district_note:
        return _cell(row.source)
    return ft.DataCell(
        content=ft.Column(
            spacing=2,
            tight=True,
            controls=[
                ft.Text(row.source, size=tokens.type_body, weight=ft.FontWeight.W_500, color=tokens.color_text),
                ft.Text(row.district_note, size=tokens.type_caption, color=tokens.color_muted),
            ],
        )
    )


def run_table(rows: list[RunRow]) -> ft.Control:
    """The DS-1-styled ``ft.DataTable`` of past runs — the first ``ft.DataTable`` consumer.

    Columns mirror the proven Streamlit set MINUS the raw ``Error`` column (dropped for privacy):
    **When · Status · Source · Students · Staff · Family · Classes · Enrollments · [Courses ·
    Student courses] · SFTP · Warnings · Duration**. The 2 myBlueprint+ count columns render ONLY
    when at least one displayed row has a non-zero count for them — decided TABLE-WIDE (one scan of
    all rows), so a SpacesEDU district shows 5 count columns, not 7 with two all-zero columns.

    Every cell is a uniform string (a not-produced entity → "—"). Status is TEXT-first
    (``status_label``), with an optional AA-safe row tint from ``status_verdict`` (never
    colour-only). Source is the bounded origin label ("Nightly" / "Manual" / "Command line" / "—"),
    with the muted different-district note stacked beneath when present. SFTP renders a glyph +
    word. No sort / select / checkbox (YAGNI, read-only).
    """
    # Table-wide decision: show a myBlueprint+ column only if ANY row carries a non-zero count.
    show_mbp = {key: any(row.entity_counts.get(key, 0) > 0 for row in rows) for key, _label in _ROW_MYBLUEPRINT_COLUMNS}
    count_columns: list[tuple[str, str]] = list(_ROW_ROSTERING_COLUMNS) + [
        (key, label) for key, label in _ROW_MYBLUEPRINT_COLUMNS if show_mbp[key]
    ]

    def _head(text: str) -> ft.Text:
        return ft.Text(text, size=12, weight=ft.FontWeight.W_700, color=tokens.color_muted)

    columns: list[ft.DataColumn] = [
        ft.DataColumn(label=_head("When")),
        ft.DataColumn(label=_head("Status")),
        ft.DataColumn(label=_head("Source")),
    ]
    columns += [ft.DataColumn(label=_head(label), numeric=True) for _key, label in count_columns]
    columns.append(ft.DataColumn(label=_head("SFTP")))
    columns.append(ft.DataColumn(label=_head("Warnings"), numeric=True))
    columns.append(ft.DataColumn(label=_head("Duration"), numeric=True))

    data_rows: list[ft.DataRow] = []
    for row in rows:
        cells: list[ft.DataCell] = [
            _cell(row.when),
            _cell(row.status_label, weight=ft.FontWeight.W_700),
            _source_cell(row),
        ]
        for key, _label in count_columns:
            value = row.entity_counts.get(key)
            cells.append(_cell("—" if value is None else str(value)))
        cells.append(_cell(_SFTP_GLYPHS[row.sftp]))
        cells.append(_cell(str(row.warnings) if row.warnings else "0"))
        cells.append(_cell(row.duration))
        data_rows.append(ft.DataRow(cells=cells, color=_ROW_TINTS.get(row.status_verdict)))

    return ft.DataTable(
        columns=columns,
        rows=data_rows,
        show_checkbox_column=False,
        column_spacing=28,
        heading_row_color=tokens.page_bg,
        heading_text_style=ft.TextStyle(size=12, weight=ft.FontWeight.W_700, color=tokens.color_muted),
        data_text_style=ft.TextStyle(size=13, color=tokens.color_text),
        border=_b_all(1, tokens.color_border),
        border_radius=14,
        divider_thickness=1,
        show_bottom_border=True,
    )


# --------------------------------------------------------------------------- #
# FileChip — a compact "icon + filename" chip (first consumer: Convert)         #
# --------------------------------------------------------------------------- #
def FileChip(filename: str, *, present: bool = True) -> ft.Container:  # noqa: N802 - a view-factory named like a component
    """A compact chip naming one GDE file: a file icon + the filename, DS-1 styled.

    ``present=False`` renders the "expected but missing" variant (a muted, dashed
    amber cue) so the missing-file warning reads at a glance. The filename is a
    config-derived source name (never PII), shown verbatim.
    """
    if present:
        icon = ft.Icons.DESCRIPTION_ROUNDED
        icon_color = tokens.color_action_primary
        text_color = tokens.color_text
        bgcolor = tokens.page_bg
        border = _b_all(1, tokens.color_border)
    else:
        icon = ft.Icons.HELP_OUTLINE_ROUNDED
        icon_color = tokens.color_status_warning
        text_color = tokens.color_status_warning
        bgcolor = tokens.color_surface
        border = _b_all(1, tokens.color_status_warning)

    return ft.Container(
        bgcolor=bgcolor,
        border=border,
        border_radius=10,
        padding=_pad_sym(12, 8),
        content=ft.Row(
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(icon, size=18, color=icon_color),
                ft.Text(filename, size=13, weight=ft.FontWeight.W_600, color=text_color),
            ],
        ),
    )


# --------------------------------------------------------------------------- #
# Verdict-first spine                                                            #
# --------------------------------------------------------------------------- #
def HealthVerdictBanner(  # noqa: N802 - a view-factory named like a component
    verdict: Verdict,
    *,
    headline: str | None = None,
    detail: str | None = None,
    trailing: ft.Control | None = None,
) -> ft.Container:
    """The verdict band (Direction B "verdict_band"): toned tint + solid icon disc + deep text.

    The verdict-first spine, restyled to the approved Direction B shape: a calm status
    TINT background with a 1px LINE border (never a saturated fill behind text), a SOLID
    status-colour circle carrying a WHITE ``ft.Icon`` as the non-colour cue, a
    ``type_section`` headline and ``type_body`` detail in the DEEP on-tint colour, and an
    optional ``trailing`` slot (a "View Run History" link / an "Open Setup" primary —
    the screen's single filled action when a fix is needed) pushed to the right.

    Paints ``verdict_visuals(verdict)`` for the icon + disc colour and the default
    headline (overridable). The icon name resolves like ``nav.py``/``shell.py``
    (``getattr(ft.Icons, name, fallback)``). Every painted pair (deep text on tint,
    white icon on disc) is AA-gated in ``tokens.UI_CONTRAST_PAIRS``.
    """
    visual = verdict_visuals(verdict)
    tint, line, on_tint = _VERDICT_TINTS[verdict]
    icon = getattr(ft.Icons, visual.icon, ft.Icons.INFO_ROUNDED)
    head = headline if headline is not None else visual.headline

    lines: list[ft.Control] = [
        ft.Text(head, size=tokens.type_section, weight=ft.FontWeight.W_700, color=on_tint),
    ]
    if detail:
        lines.append(ft.Text(detail, size=tokens.type_body, color=on_tint))

    disc = ft.Container(
        width=34,
        height=34,
        border_radius=17,
        bgcolor=visual.color,
        alignment=ft.Alignment(0, 0),
        content=ft.Icon(icon, color=tokens.color_on_action, size=19),
    )
    left_group = ft.Row(
        spacing=tokens.space_lg,
        tight=True,
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[disc, ft.Column(spacing=2, controls=lines)],
    )
    row_controls: list[ft.Control] = [left_group]
    if trailing is not None:
        row_controls.append(trailing)

    return ft.Container(
        bgcolor=tint,
        border=_b_all(1, line),
        padding=_pad_sym(tokens.space_lg + 4, tokens.space_lg),
        border_radius=tokens.radius_md,
        content=ft.Row(
            spacing=tokens.space_lg,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=row_controls,
        ),
    )


def ErrorCard(  # noqa: N802 - a view-factory named like a component
    headline: str,
    detail: str | None = None,
    *,
    action: ft.Control | None = None,
) -> ft.Container:
    """The reusable never-crash error surface: a red-bordered card with a clear cause.

    The shell's reliability net — a surface that fails renders this instead of a
    raw stack trace. Uses the failed-verdict colour for the icon/headline (red text
    on white clears AA — ``UI_CONTRAST_PAIRS``), keeping body text legible.
    """
    body: list[ft.Control] = [
        ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, color=tokens.color_status_failed, size=26),
                ft.Text(headline, size=16, weight=ft.FontWeight.W_700, color=tokens.color_status_failed),
            ],
        ),
    ]
    if detail:
        body.append(ft.Text(detail, size=14, color=tokens.color_text))
    if action is not None:
        body.append(action)

    return ft.Container(
        bgcolor=tokens.color_surface,
        padding=24,
        border_radius=16,
        border=_b_all(1, tokens.color_status_failed),
        content=ft.Column(spacing=12, controls=body),
    )


# --------------------------------------------------------------------------- #
# Dev-only render demo (NOT a user nav entry) — exercises the front-loaded spine #
# --------------------------------------------------------------------------- #
def build_design_demo() -> ft.Control:
    """A dev-scoped gallery of the 3 verdict banners + an ErrorCard.

    Reachable only behind the ``DISTRICTSYNC_UI_DEMO`` env flag (wired in
    ``shell.build_screens``) or by a test/manual call — never a user-facing route.
    Exercises the verdict-first spine + error surface this slice so they ship
    visually proven, not as dead library code (their live-data consumer is IA-3).
    """
    return ft.Column(
        spacing=18,
        controls=[
            card(
                content=ft.Text(
                    "Design-system demo (dev only)",
                    size=20,
                    weight=ft.FontWeight.W_800,
                    color=tokens.color_on_action,
                ),
                gradient=hero_gradient(),
            ),
            HealthVerdictBanner(Verdict.HEALTHY, detail="Last run completed cleanly."),
            HealthVerdictBanner(Verdict.WARNING, detail="A source file looked smaller than usual."),
            HealthVerdictBanner(Verdict.FAILED, detail="The output was not delivered."),
            ErrorCard(
                "Something went wrong",
                "We hit a problem rendering this screen. Your nightly sync keeps running.",
            ),
        ],
    )
