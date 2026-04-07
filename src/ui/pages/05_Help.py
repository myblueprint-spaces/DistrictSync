"""Help & Documentation — renders docs/ markdown files in the Streamlit UI.

Single source of truth: content lives in docs/ (used by MkDocs for the
static site and GitHub Pages). This page reads and renders those same
files so in-app help is always in sync.
"""

import sys
from pathlib import Path

import streamlit as st

_root = Path(__file__).parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.ui.brand import header, inject_brand_css  # noqa: E402

st.set_page_config(page_title="Help — GDE2Acsv", page_icon="❓", layout="wide")
inject_brand_css()
header("Help & Documentation", "How GDE2Acsv works and what to expect")

DOCS_DIR = Path("docs")

# Fallback paths relative to the script (for PyInstaller bundles)
if not DOCS_DIR.exists():
    DOCS_DIR = _root / "docs"


def _read_doc(relative_path: str) -> str:
    """Read a markdown file from the docs directory, stripping MkDocs frontmatter."""
    path = DOCS_DIR / relative_path
    if not path.exists():
        return f"*Documentation file not found: `{relative_path}`*"
    text = path.read_text(encoding="utf-8")
    # Strip MkDocs frontmatter (--- ... ---)
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3 :].strip()
    return text


# ---------------------------------------------------------------------------
tab_install, tab_howit, tab_faq, tab_trouble, tab_dev = st.tabs(
    [
        "Installation",
        "How It Works",
        "FAQ",
        "Troubleshooting",
        "For Developers",
    ]
)

with tab_install:
    st.markdown(_read_doc("partner/installation.md"))

with tab_howit:
    # Combine architecture overview with the partner-facing sections
    arch = _read_doc("developer/architecture.md")
    st.markdown(arch)

with tab_faq:
    st.markdown(_read_doc("partner/faq.md"))

with tab_trouble:
    st.markdown(_read_doc("partner/troubleshooting.md"))

with tab_dev:
    sub_tab1, sub_tab2, sub_tab3, sub_tab4 = st.tabs(["Setup", "Testing", "Adding a District", "Releases"])
    with sub_tab1:
        st.markdown(_read_doc("developer/setup.md"))
    with sub_tab2:
        st.markdown(_read_doc("developer/testing.md"))
    with sub_tab3:
        st.markdown(_read_doc("developer/adding-district.md"))
    with sub_tab4:
        st.markdown(_read_doc("developer/release.md"))

st.divider()
st.caption("SpacesEDU by myBlueprint · GDE2Acsv · support@myBlueprint.ca")
