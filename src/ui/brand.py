"""myBlueprint / SpacesEDU brand styles for Streamlit UI.

Inject into any page with:
    from src.ui.brand import inject_brand_css, header
    inject_brand_css()
    header("Page Title", "Optional subtitle")
"""

import base64
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Logo assets — loaded from docs/assets/ at runtime
# Falls back to a text badge if the image file isn't present.
# ---------------------------------------------------------------------------


def _logo_data_uri(filename: str) -> str:
    """Return a data: URI for an image in docs/assets/, or '' if not found."""
    # Try common relative paths (dev layout vs PyInstaller bundle)
    candidates = [
        Path(__file__).parent.parent.parent / "docs" / "assets" / filename,
        Path("docs") / "assets" / filename,
    ]
    for path in candidates:
        if path.exists():
            data = path.read_bytes()
            ext = path.suffix.lstrip(".")
            mime = "image/svg+xml" if ext == "svg" else f"image/{ext}"
            return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    return ""


_SPACES_WORDMARK = _logo_data_uri("spacesedu-wordmark.png")
_MB_LOGO = _logo_data_uri("myblueprint-logo.png")

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
# Derived from myBlueprint corporate identity:
#   Primary blue   — the "blueprint" brand colour
#   Accent teal    — SpacesEDU "growth" accent
#   Dark navy      — headings / strong text
#   Light bg       — page / card backgrounds

MB_PRIMARY = "#1D5BB5"  # myBlueprint blue
MB_DARK = "#0F2D6B"  # deep navy (headings, sidebar)
MB_ACCENT = "#0EA5E9"  # sky-blue accent
MB_GREEN = "#16A34A"  # success / active
MB_LIGHT_BG = "#F0F6FF"  # page background tint
MB_BORDER = "#DBEAFE"  # card / divider border
MB_TEXT = "#0F172A"  # body text
MB_MUTED = "#64748B"  # captions / muted

CSS = f"""
<style>
/* ── Reset & page shell ── */
html, body, [data-testid="stAppViewContainer"] {{
    background-color: {MB_LIGHT_BG};
    color: {MB_TEXT};
    font-family: "Inter", "Segoe UI", Arial, sans-serif;
}}

/* ── Sidebar ── */
[data-testid="stSidebar"] {{
    background: linear-gradient(180deg, {MB_DARK} 0%, {MB_PRIMARY} 100%);
}}
[data-testid="stSidebar"] * {{
    color: #ffffff !important;
}}
[data-testid="stSidebar"] a:hover {{
    color: {MB_ACCENT} !important;
}}
/* Restore dark text + white background inside form widgets in the sidebar —
   without this, the broad white-text rule above makes selectbox values,
   text inputs, and dropdown options white-on-white and invisible. Labels
   stay white. Selector is intentionally broad (every descendant of the
   widget) because BaseWeb does not reliably apply [role="combobox"] to
   the selected value element. */
[data-testid="stSidebar"] [data-baseweb="select"],
[data-testid="stSidebar"] [data-baseweb="select"] *,
[data-testid="stSidebar"] [data-baseweb="input"],
[data-testid="stSidebar"] [data-baseweb="input"] *,
[data-testid="stSidebar"] [data-baseweb="textarea"],
[data-testid="stSidebar"] [data-baseweb="textarea"] * {{
    color: {MB_TEXT} !important;
}}
/* Ensure the widget container has a light background so dark text is
   visible against it (the sidebar gradient would otherwise bleed through
   on any transparent region). */
[data-testid="stSidebar"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] [data-baseweb="input"] > div,
[data-testid="stSidebar"] [data-baseweb="textarea"] > div {{
    background-color: #ffffff !important;
}}
/* Dropdown popup container — rendered via portal at <body> root, so it
   doesn't inherit page-scoped styles. Force a white background and dark
   text on options so dropdowns are readable even when the OS / browser
   defaults to a dark UI theme. */
[data-baseweb="popover"] [data-baseweb="menu"],
[data-baseweb="popover"] [data-baseweb="list"],
[data-baseweb="popover"] ul {{
    background-color: #ffffff !important;
}}
[data-baseweb="popover"] [role="option"] {{
    background-color: #ffffff !important;
    color: {MB_TEXT} !important;
}}
[data-baseweb="popover"] [role="option"] * {{
    color: {MB_TEXT} !important;
}}
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] [role="option"][aria-selected="true"] {{
    background-color: {MB_LIGHT_BG} !important;
}}
/* Inline code in sidebar (e.g. "Config: `1.0` | SIS: ..." metadata line) —
   the sweeping sidebar white-text rule hides the value otherwise. */
[data-testid="stSidebar"] code {{
    color: {MB_DARK} !important;
    background: #EFF6FF !important;
}}

/* ── Top header band ── */
.mb-header {{
    background: linear-gradient(90deg, {MB_DARK} 0%, {MB_PRIMARY} 60%, {MB_ACCENT} 100%);
    padding: 1.2rem 2rem;
    border-radius: 0.75rem;
    margin-bottom: 1.5rem;
    color: #ffffff;
}}
.mb-header h1 {{
    margin: 0;
    font-size: 1.75rem;
    font-weight: 700;
    color: #ffffff !important;
    letter-spacing: -0.02em;
}}
.mb-header p {{
    margin: 0.25rem 0 0;
    font-size: 0.9rem;
    color: rgba(255,255,255,0.82);
}}
.mb-brand {{
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.6);
    margin-bottom: 0.25rem;
}}

/* ── Cards ── */
.mb-card {{
    background: #ffffff;
    border: 1px solid {MB_BORDER};
    border-radius: 0.75rem;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
    box-shadow: 0 1px 4px rgba(15, 45, 107, 0.07);
}}
.mb-card h3 {{
    color: {MB_DARK};
    margin-top: 0;
    font-size: 1rem;
    font-weight: 600;
}}

/* ── Step pill ── */
.mb-step-pill {{
    display: inline-block;
    background: {MB_PRIMARY};
    color: #fff;
    border-radius: 999px;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 0.2em 0.7em;
    margin-right: 0.5rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}}

/* ── Exit DistrictSync button (sidebar) ──
   Sidebar buttons inherit the broad white-text rule above but have no
   background rule (the restore-dark-text rule covers only select/input/
   textarea, and the secondary-button rule is scoped to stMain), so the
   default light button background renders white-on-white. Give the Exit
   button a distinct blue gradient (brighter than the sidebar's navy→blue)
   so its white label — left as-is — reads clearly. Keyed via st.button
   key="exit_districtsync" → the .st-key-exit_districtsync wrapper class. */
[data-testid="stSidebar"] .st-key-exit_districtsync button {{
    background: linear-gradient(90deg, {MB_PRIMARY} 0%, {MB_ACCENT} 100%) !important;
    color: #ffffff !important;
    border: 1px solid {MB_ACCENT} !important;
    font-weight: 600 !important;
}}
[data-testid="stSidebar"] .st-key-exit_districtsync button:hover {{
    background: linear-gradient(90deg, {MB_DARK} 0%, {MB_PRIMARY} 100%) !important;
    border-color: {MB_PRIMARY} !important;
    color: #ffffff !important;
}}

/* ── Primary buttons ── */
[data-testid="stButton"] button[kind="primary"] {{
    background: {MB_PRIMARY} !important;
    border-color: {MB_PRIMARY} !important;
    color: #ffffff !important;
    font-weight: 600;
    border-radius: 0.5rem;
}}
[data-testid="stButton"] button[kind="primary"]:hover {{
    background: {MB_DARK} !important;
    border-color: {MB_DARK} !important;
}}

/* ── Secondary / default buttons ──
   Without this rule, non-primary buttons in the main content area inherit
   Streamlit's dark-mode defaults (dark bg + dark text), making the label
   invisible. Force a light card-style appearance so text is always
   readable. Scoped to the main view so the sidebar's white-text rule
   still wins for sidebar buttons. */
[data-testid="stMain"] [data-testid="stButton"] button:not([kind="primary"]),
[data-testid="stMain"] [data-testid="stBaseButton-secondary"] {{
    background: #ffffff !important;
    color: {MB_TEXT} !important;
    border: 1px solid {MB_BORDER} !important;
    border-radius: 0.5rem;
}}
[data-testid="stMain"] [data-testid="stButton"] button:not([kind="primary"]):hover,
[data-testid="stMain"] [data-testid="stBaseButton-secondary"]:hover {{
    background: {MB_LIGHT_BG} !important;
    color: {MB_DARK} !important;
    border-color: {MB_PRIMARY} !important;
}}

/* ── Success / info / warning / error banners ──
   Streamlit's default text colour for st.warning / st.info / st.error /
   st.success can match the banner background under some themes (e.g.
   white-on-yellow for warnings), making the message unreadable. Force
   the body text dark so the message is always visible. */
[data-testid="stAlert"][data-baseweb="notification"] {{
    border-radius: 0.5rem;
}}
[data-testid="stAlert"] [data-testid="stMarkdownContainer"],
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] * {{
    color: {MB_TEXT} !important;
}}

/* ── Main-content form widgets ──
   Mirrors the sidebar rules but for the main view. Text inputs, number
   inputs, textareas, and selectbox values default to dark text on white
   so they're never invisible regardless of OS theme. The popover rules
   above already handle dropdown option lists. */
[data-testid="stMain"] [data-baseweb="select"],
[data-testid="stMain"] [data-baseweb="select"] *,
[data-testid="stMain"] [data-baseweb="input"],
[data-testid="stMain"] [data-baseweb="input"] *,
[data-testid="stMain"] [data-baseweb="textarea"],
[data-testid="stMain"] [data-baseweb="textarea"] * {{
    color: {MB_TEXT} !important;
}}
[data-testid="stMain"] [data-baseweb="select"] > div,
[data-testid="stMain"] [data-baseweb="input"] > div,
[data-testid="stMain"] [data-baseweb="textarea"] > div {{
    background-color: #ffffff !important;
}}

/* ── Widget labels (the small caption above text inputs / radios /
   selectboxes / file uploaders / etc.) and radio / checkbox option
   labels. Streamlit derives these from the active theme, which can
   land as white-on-white when the OS / browser prefers a dark theme.
   Pin them to the body text colour in the main view so they're
   always readable.

   Note on radio / checkbox: Streamlit renders each option as a
   `<label data-baseweb="radio">` containing the input + text, so the
   selector must target the [data-baseweb="radio"] element itself (and
   its descendants), not nested labels — there aren't any. Same for
   checkbox. The earlier rule targeted "label inside [data-baseweb=
   radio]" and never matched. */
[data-testid="stMain"] [data-testid="stWidgetLabel"],
[data-testid="stMain"] [data-testid="stWidgetLabel"] *,
[data-testid="stMain"] [data-testid="stRadio"],
[data-testid="stMain"] [data-testid="stRadio"] *,
[data-testid="stMain"] [data-testid="stCheckbox"],
[data-testid="stMain"] [data-testid="stCheckbox"] *,
[data-testid="stMain"] [data-baseweb="radio"],
[data-testid="stMain"] [data-baseweb="radio"] *,
[data-testid="stMain"] [data-baseweb="checkbox"],
[data-testid="stMain"] [data-baseweb="checkbox"] *,
[data-testid="stMain"] [data-testid="stFileUploaderDropzone"] *,
[data-testid="stMain"] [data-testid="stText"],
[data-testid="stMain"] [data-testid="stText"] *,
[data-testid="stMain"] pre,
[data-testid="stMain"] pre * {{
    color: {MB_TEXT} !important;
}}

/* ── Tab buttons (st.tabs) ──
   The Help page uses tabs to switch between Installation / FAQ /
   Troubleshooting etc. Without an explicit rule the tab labels follow
   the system theme and become invisible on the light background. */
[data-baseweb="tab-list"] [data-baseweb="tab"],
[data-baseweb="tab-list"] [data-baseweb="tab"] * {{
    color: {MB_TEXT} !important;
}}
[data-baseweb="tab-list"] [data-baseweb="tab"][aria-selected="true"],
[data-baseweb="tab-list"] [data-baseweb="tab"][aria-selected="true"] * {{
    color: {MB_PRIMARY} !important;
    font-weight: 600;
}}

/* ── Metric labels ── */
[data-testid="stMetric"] label {{
    color: {MB_MUTED};
    font-size: 0.78rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}
[data-testid="stMetric"] [data-testid="stMetricValue"] {{
    color: {MB_DARK};
    font-weight: 700;
}}

/* ── Dataframe / table ── */
[data-testid="stDataFrame"] {{
    border-radius: 0.5rem;
    overflow: hidden;
    border: 1px solid {MB_BORDER};
}}

/* ── Dividers ── */
hr {{
    border-color: {MB_BORDER};
    margin: 1.5rem 0;
}}

/* ── Inline code ── */
code {{
    background: #EFF6FF;
    color: {MB_DARK};
    border-radius: 0.3rem;
    padding: 0.15em 0.4em;
    font-size: 0.88em;
}}

/* ── Code blocks (st.code) ──
   st.code() renders a <pre> inside [data-testid="stCode"] that carries a
   dark syntax-theme background. The body-text rule above forces the text
   dark, so without a light block background the content is dark-on-dark and
   unreadable (e.g. the Setup Wizard "Review & Save" summary, the Mapping
   Editor YAML preview). Pin the block background light and keep the text
   dark so st.code output is always legible. */
[data-testid="stMain"] [data-testid="stCode"],
[data-testid="stMain"] [data-testid="stCode"] pre,
[data-testid="stMain"] pre {{
    background: #F1F5F9 !important;
    border: 1px solid {MB_BORDER};
    border-radius: 0.5rem;
}}
[data-testid="stMain"] [data-testid="stCode"] code,
[data-testid="stMain"] [data-testid="stCode"] code * {{
    background: transparent !important;
    padding: 0 !important;
    color: {MB_TEXT} !important;
}}

/* ── Native form controls (radio / checkbox / scrollbars) ──
   Without an explicit color-scheme, a dark-mode OS/browser renders the
   unselected radio as a solid dark filled circle (looks "selected"). Pin
   light rendering and brand the accent so selected = blue, unselected =
   hollow. */
html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
    color-scheme: light;
}}
[data-testid="stMain"] input[type="radio"],
[data-testid="stMain"] input[type="checkbox"] {{
    accent-color: {MB_PRIMARY};
}}

/* ── Expander header + body (st.expander) ──
   The dark theme paints the expander summary with a near-black secondary
   background and the body-text rule forces the label dark — dark-on-dark.
   Pin the header + body light so the label and toggle icon are readable
   (Setup Wizard manage view, Mapping Editor "View Generated YAML"). */
[data-testid="stMain"] [data-testid="stExpander"] details,
[data-testid="stMain"] [data-testid="stExpander"] summary {{
    background-color: #ffffff !important;
}}
[data-testid="stMain"] [data-testid="stExpander"] summary,
[data-testid="stMain"] [data-testid="stExpander"] summary * {{
    color: {MB_TEXT} !important;
}}

/* ── File uploader dropzone ──
   The dropzone defaults to a dark secondary background; the widget-label
   rule forces its helper text ("200MB per file • TXT, CSV") dark, leaving it
   invisible. Pin the dropzone background light. */
[data-testid="stMain"] [data-testid="stFileUploaderDropzone"] {{
    background-color: #F8FAFC !important;
}}

/* ── Page links in the main content (Home page "Navigation" column) ──
   These inherit the theme link/text colour, which lands white-on-light under
   a dark browser theme. Pin to the brand blue so they read as links. */
[data-testid="stMain"] [data-testid="stPageLink"] a,
[data-testid="stMain"] [data-testid="stPageLink-NavLink"],
[data-testid="stMain"] [data-testid="stPageLink-NavLink"] * {{
    color: {MB_PRIMARY} !important;
}}

/* ── Progress bar ── */
.mb-progress-bar {{
    display: flex;
    gap: 0.4rem;
    margin-bottom: 1.5rem;
}}
.mb-progress-step {{
    flex: 1;
    height: 5px;
    border-radius: 999px;
    background: {MB_BORDER};
}}
.mb-progress-step.active {{
    background: {MB_PRIMARY};
}}
.mb-progress-step.done {{
    background: {MB_GREEN};
}}
</style>
"""


def inject_brand_css() -> None:
    """Inject the myBlueprint/SpacesEDU brand stylesheet into the current page."""
    st.markdown(CSS, unsafe_allow_html=True)


def header(title: str, subtitle: str = "", brand: str = "SpacesEDU by myBlueprint") -> None:
    """Render the branded page header band with SpacesEDU logo."""
    sub_html = f"<p>{subtitle}</p>" if subtitle else ""

    if _SPACES_WORDMARK:
        logo_html = (
            f'<img src="{_SPACES_WORDMARK}" alt="SpacesEDU" '
            f'style="height:32px;margin-bottom:0.5rem;filter:brightness(0) invert(1)">'
        )
    else:
        logo_html = f'<div class="mb-brand">{brand}</div>'

    st.markdown(
        f'<div class="mb-header">{logo_html}<h1>{title}</h1>{sub_html}</div>',
        unsafe_allow_html=True,
    )


def card(content_fn, title: str = "") -> None:
    """Render content inside a styled card. Pass a callable that calls st.* inside it."""
    title_html = f"<h3>{title}</h3>" if title else ""
    st.markdown(f'<div class="mb-card">{title_html}', unsafe_allow_html=True)
    content_fn()
    st.markdown("</div>", unsafe_allow_html=True)


def sidebar_exit_control() -> None:
    """Render the **Exit DistrictSync** control in the sidebar.

    Single source for the exit button so its label/behaviour stay consistent
    across pages. Clicking it calls :func:`src.ui.lifecycle.request_exit`, which
    waits (bounded) for any in-flight conversion write to finish, shows a goodbye
    line, then terminates the server process. (`src/ui` has no shared per-page
    sidebar renderer — Streamlit's native multipage nav owns the sidebar — so each
    page that wants the control calls this helper.)
    """
    from src.ui.lifecycle import request_exit

    st.sidebar.divider()
    if st.sidebar.button(
        "⏻ Exit DistrictSync",
        key="exit_districtsync",
        help="Stop the DistrictSync server and close the app.",
    ):
        request_exit()


def step_progress(current: int, total: int = 5) -> None:
    """Render a coloured step progress bar (1-indexed)."""
    bars = ""
    for i in range(1, total + 1):
        if i < current:
            cls = "done"
        elif i == current:
            cls = "active"
        else:
            cls = ""
        bars += f'<div class="mb-progress-step {cls}"></div>'
    st.markdown(
        f'<div class="mb-progress-bar">{bars}</div>',
        unsafe_allow_html=True,
    )
