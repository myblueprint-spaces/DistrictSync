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
/* Restore dark text inside form widgets in the sidebar — without this,
   the broad white-text rule above makes selectbox values, text inputs,
   and dropdown options white-on-white and invisible. Labels stay white. */
[data-testid="stSidebar"] [data-baseweb="select"] [role="combobox"],
[data-testid="stSidebar"] [data-baseweb="select"] [role="combobox"] *,
[data-testid="stSidebar"] [data-baseweb="input"] input,
[data-testid="stSidebar"] [data-baseweb="textarea"] textarea,
[data-testid="stSidebar"] [data-baseweb="select"] input {{
    color: {MB_TEXT} !important;
}}
/* Dropdown popup options (rendered via portal but may inherit sidebar scope) */
[data-baseweb="popover"] [role="option"],
[data-baseweb="popover"] [role="option"] * {{
    color: {MB_TEXT} !important;
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

/* ── Success / info banners ── */
[data-testid="stAlert"][data-baseweb="notification"] {{
    border-radius: 0.5rem;
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

/* ── Code blocks ── */
code {{
    background: #EFF6FF;
    color: {MB_DARK};
    border-radius: 0.3rem;
    padding: 0.15em 0.4em;
    font-size: 0.88em;
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
