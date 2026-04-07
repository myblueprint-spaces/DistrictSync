"""GDE2Acsv — Streamlit multi-page application.

This is the main entry point. Streamlit automatically discovers pages
from the ``pages/`` subdirectory.

Run with:
    streamlit run src/ui/app.py
"""

import sys
from pathlib import Path

import streamlit as st

_root = Path(__file__).parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.ui.brand import header, inject_brand_css  # noqa: E402

st.set_page_config(
    page_title="GDE2Acsv — SpacesEDU",
    page_icon="🏫",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_brand_css()
header(
    "GDE2Acsv",
    "MyEducation BC General Data Extracts → SpacesEDU Advanced CSV",
)

# ---------------------------------------------------------------------------
# Setup status banner
# ---------------------------------------------------------------------------
try:
    from src.config.app_config import AppConfig  # noqa: E402
    cfg = AppConfig.load()
    if cfg.is_complete():
        st.success(
            f"**Configured** — District: `{cfg.sis_type}` | "
            f"Daily run: `{cfg.schedule_time}` | "
            f"SFTP: {'✓ enabled' if cfg.sftp_is_configured() else '✗ disabled'}"
        )
    else:
        st.info("**Not yet configured.** Use the Setup Wizard in the sidebar to get started.")
except Exception:
    st.info("**Not yet configured.** Use the Setup Wizard in the sidebar to get started.")

st.divider()

# ---------------------------------------------------------------------------
# What it does
# ---------------------------------------------------------------------------
col1, col2 = st.columns([3, 2])

with col1:
    st.subheader("What it does")
    st.markdown(
        "GDE2Acsv reads the five standard GDE export files from MyEducation BC and "
        "produces the five CSV files required by SpacesEDU's Advanced CSV import format. "
        "It runs automatically every night and uploads results via SFTP."
    )

    st.markdown("""
| Input (MyEdBC GDE) | Output (SpacesEDU) |
|---|---|
| `StudentDemographicInformation.txt` | `Students.csv` |
| `StaffInformationEnhanced.txt` | `Staff.csv` |
| `EmergencyContactInformation.txt` | `Family.csv` |
| `StudentSchedule.txt` + `CourseInformation.txt` | `Classes.csv` |
| `StudentSchedule.txt` + demographics | `Enrollments.csv` |
""")

with col2:
    st.subheader("Navigation")
    st.markdown("""
<div style="display:flex;flex-direction:column;gap:0.75rem;margin-top:0.5rem">
  <div style="background:#fff;border:1px solid #DBEAFE;border-radius:0.6rem;padding:0.9rem 1.1rem">
    <strong style="color:#0F2D6B">⚙️ Setup Wizard</strong><br>
    <span style="color:#64748B;font-size:0.85rem">Configure paths, schedule, and SFTP upload (first-time setup)</span>
  </div>
  <div style="background:#fff;border:1px solid #DBEAFE;border-radius:0.6rem;padding:0.9rem 1.1rem">
    <strong style="color:#0F2D6B">🔄 Convert</strong><br>
    <span style="color:#64748B;font-size:0.85rem">Upload GDE files and download CSVs on demand</span>
  </div>
  <div style="background:#fff;border:1px solid #DBEAFE;border-radius:0.6rem;padding:0.9rem 1.1rem">
    <strong style="color:#0F2D6B">📋 Run History</strong><br>
    <span style="color:#64748B;font-size:0.85rem">View the log of automated daily runs</span>
  </div>
</div>
""", unsafe_allow_html=True)

st.divider()
st.caption("SpacesEDU by myBlueprint · GDE2Acsv · support@myBlueprint.ca")
