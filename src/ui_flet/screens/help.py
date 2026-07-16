"""The Help surface — the calm, one-click "get me un-stuck" view (IA model IA-7).

VIEW glue (coverage-omitted): there is NO trust-critical derivation to place in a COUNTED
pure module — the surface reads only two module constants (the org Help Centre URL + the
support email) plus ``AppConfig`` for the friendly greeting, and calls ``page.launch_url``
twice. Manufacturing a "help-topic registry" here would be YAGNI (there is one topic: open
the KB), so this is honestly nearly-all-view; the single testable surface (the constants +
the shell swap-ordering invariant) is covered by ``tests/test_ui_flet_help.py`` WITHOUT
instantiating a flet control.

**Link-out, not a bundled-docs browser** (per the 0013 scope-lock, which supersedes the
stale IA-7 "render ``docs/`` markdown" row): the canonical, always-current docs home is the
org knowledge base (the SpacesEDU Help Centre), so this surface links there + gives a human
support path, rather than rendering the bundled ``docs/`` markdown (a curated in-app offline
render is a scope-locked ROADMAP follow-on gated on the deferred docs-strategy decision).

**Offline-resilient:** an admin on an air-gapped / browserless district server would get a
dead click from ``launch_url`` alone, so the URL + email are ALSO rendered as **selectable
plain text** — the button is the one-click path; the visible text is the calm fallback they
can read/copy off a locked-down server.

Assembled ENTIRELY from ``components.py`` (card/buttons/ErrorCard) + ``tokens`` +
``humanize.friendly_district_name`` — never hand-rolled controls (the ``FilledButton(text=)``
trap; see ``docs/FLET_1.0_CONVENTIONS.md``). ``page.launch_url`` is **introspected against the
installed ``flet==0.85.3``** (it is NOT documented in the conventions doc). Owns no lifecycle.

**Never-crash floor:** the whole body is wrapped in ``try/except`` → ``components.ErrorCard``
so even a view-layer bug shows a calm surface, never a stack trace (defense-in-depth — the
surface reads only constants + a TOTAL ``friendly_district_name``, so a crash is nearly
impossible, but the wrapper matches ``home.py`` / ``run_history.py``).
"""

from __future__ import annotations

import flet as ft

from src.config.app_config import AppConfig
from src.ui_flet import about, components, tokens
from src.ui_flet.humanize import friendly_district_name
from src.utils.version import app_version

# The single canonical support article — the "org knowledge-article base" the scope-lock
# points IA-7 at. Grepped canonical value (byte-identical across release.yml / README.md /
# src/ui/Home.py / src/ui/pages/05_Help.py). A hard-coded module constant (never user input)
# → no injection surface; the drift-guard test pins the exact-case value.
HELP_CENTRE_URL = "https://help.spacesedu.com/en-ca/article/mx56qo"
# The canonical support contact — the footer of every Streamlit page + main.py's CLI failure
# message. Exact mixed-case `myBlueprint` (the drift-guard test pins the case).
SUPPORT_EMAIL = "support@myBlueprint.ca"


def _greeting_header(app_config: AppConfig) -> ft.Control:
    """The Direction B page header titling the surface "Help" (never a raw config id).

    The gradient hero demotes to a slim ``page_header`` (0033 Slice 2); the district-voiced
    subtitle is preserved as the header sub.
    """
    friendly = friendly_district_name(app_config.sis_type)
    subtitle = (
        f"Getting {friendly} un-stuck — the answers, and a human to email."
        if friendly
        else "Getting you un-stuck — the answers, and a human to email."
    )
    return components.page_header("Help", subtitle)


def _copyable_line(page: ft.Page, value: str, *, tooltip: str) -> ft.Control:
    """A selectable plain-text value with a copy button beside it (offline-resilient).

    The selectable text is the calm fallback an admin can read off a locked-down server;
    the ``components.copy_button`` is the one-click path (0032 T1 #9).
    """
    return ft.Row(
        spacing=tokens.space_xs,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            ft.Text(value, size=tokens.type_body, selectable=True, color=tokens.color_muted),
            components.copy_button(page, value, tooltip=tooltip),
        ],
    )


def _get_help_card(page: ft.Page, app_config: AppConfig) -> ft.Control:
    """The "Get help" card — the one prominent action + the human path + the offline fallback.

    The primary "Open the Help Centre" button opens the system browser at the canonical
    article (``page.launch_url`` — introspected against ``flet==0.85.3``); the support
    affordance opens the default mail client with a PREFILLED, PII-free subject (version +
    district display name only — ``about.support_mailto``) so support can triage without a
    back-and-forth. BOTH destinations are ALSO shown as selectable plain text with copy
    buttons so an admin on an air-gapped / browserless district server can read and copy
    the address rather than get a dead click (offline-resilient).
    """
    mailto = about.support_mailto(SUPPORT_EMAIL, app_version(), friendly_district_name(app_config.sis_type))
    return components.card(
        content=ft.Column(
            spacing=16,
            controls=[
                ft.Text(
                    "Get help",
                    size=20,
                    weight=ft.FontWeight.W_800,
                    color=tokens.color_text,
                ),
                ft.Text(
                    "The Help Centre has step-by-step answers, always up to date.",
                    size=14,
                    color=tokens.color_muted,
                ),
                components.primary_button(
                    "Open the Help Centre",
                    lambda _e: page.launch_url(HELP_CENTRE_URL),
                    icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                ),
                # Offline fallback: the address, readable + copyable if no browser opens.
                _copyable_line(page, HELP_CENTRE_URL, tooltip="Copy link"),
                ft.Container(height=4),
                ft.Text(
                    "Prefer a person? Email our support team and we'll help you out.",
                    size=14,
                    color=tokens.color_muted,
                ),
                components.secondary_button(
                    f"Email {SUPPORT_EMAIL}",
                    lambda _e: page.launch_url(mailto),
                    icon=ft.Icons.MAIL_OUTLINE_ROUNDED,
                ),
                # Offline fallback: the email address, readable + copyable.
                _copyable_line(page, SUPPORT_EMAIL, tooltip="Copy email address"),
            ],
        ),
    )


def _reassurance_card(app_config: AppConfig) -> ft.Control:
    """The "What DistrictSync does" reassurance card — the "what even is this?" gap-closer.

    Plain sentences in the verdict-first cockpit voice (no jargon, no raw ids), naming what
    the tool does, WHERE the real sync runs, and the recurring decouple-the-sync promise at a
    leave point: closing this window does not stop the nightly scheduled sync.
    """
    friendly = friendly_district_name(app_config.sis_type)
    intro = (
        f"DistrictSync turns {friendly}'s roster export into the files SpacesEDU and "
        "myBlueprint+ need — no spreadsheets, no manual steps."
        if friendly
        else "DistrictSync turns your district's roster export into the files SpacesEDU and "
        "myBlueprint+ need — no spreadsheets, no manual steps."
    )
    return components.card(
        content=ft.Column(
            spacing=10,
            controls=[
                ft.Text(
                    "What DistrictSync does",
                    size=20,
                    weight=ft.FontWeight.W_800,
                    color=tokens.color_text,
                ),
                ft.Text(intro, size=14, color=tokens.color_text),
                ft.Text(
                    "The real sync runs on its own overnight — a scheduled task on your "
                    "server keeps SpacesEDU up to date every night.",
                    size=14,
                    color=tokens.color_text,
                ),
                ft.Text(
                    "Closing this window doesn't stop the nightly sync — it runs on its own "
                    "schedule. Opening Help is always safe.",
                    size=14,
                    weight=ft.FontWeight.W_600,
                    color=tokens.color_text,
                ),
            ],
        ),
    )


def _about_card(page: ft.Page) -> ft.Control:
    """The About block (0032 T1 #9) — the exact facts support asks for first.

    The version line (pure ``about.version_display`` over the single-source
    ``utils.version.app_version``) with a "Copy version" affordance, and the public
    release-notes link — both values also selectable text with copy buttons (the same
    offline-resilient pattern the Get-help card set). No lifecycle, no config reads.
    """
    version = app_version()
    return components.card(
        content=ft.Column(
            spacing=tokens.space_md,
            controls=[
                ft.Text(
                    "About DistrictSync",
                    size=20,
                    weight=ft.FontWeight.W_800,
                    color=tokens.color_text,
                ),
                ft.Row(
                    spacing=tokens.space_xs,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text(
                            about.version_display(version),
                            size=tokens.type_emphasis,
                            weight=ft.FontWeight.W_600,
                            color=tokens.color_text,
                        ),
                        components.copy_button(page, version, tooltip="Copy version"),
                    ],
                ),
                ft.Text(
                    "Include the version when you contact support — it tells us exactly what you're running.",
                    size=tokens.type_body,
                    color=tokens.color_muted,
                ),
                components.secondary_button(
                    "See what's new",
                    lambda _e: page.launch_url(about.RELEASE_NOTES_URL),
                    icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                ),
                _copyable_line(page, about.RELEASE_NOTES_URL, tooltip="Copy link"),
            ],
        ),
    )


def build_help(page: ft.Page, *, app_config: AppConfig) -> ft.Control:
    """Build the Help surface (read-only, link-out). ``page`` opens external destinations.

    A branded hero + a "Get help" card (the one-click Help Centre + prefilled support-email
    paths, with the addresses also as offline-readable selectable text + copy buttons) + a
    plain "what DistrictSync does / the nightly sync is independent" reassurance card + the
    About block (version, "Copy version", release notes). Wrapped in a never-crash
    ``ErrorCard`` fallback so even a view-layer bug shows a calm surface, never a stack
    trace. Owns no lifecycle — it navigates the admin OUT to the browser / mail client,
    not to another screen.
    """
    try:
        return ft.Column(
            spacing=22,
            controls=[
                _greeting_header(app_config),
                _get_help_card(page, app_config),
                _reassurance_card(app_config),
                _about_card(page),
            ],
        )
    except Exception:  # noqa: BLE001 - the reliability floor: a view bug shows a calm surface, never a trace
        return components.ErrorCard(
            "We couldn't open Help",
            "Your nightly sync keeps running in the background.",
        )
