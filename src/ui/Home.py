"""DistrictSync — Streamlit multi-page application.

This is the main entry point. Streamlit automatically discovers pages
from the ``pages/`` subdirectory.

Run with:
    streamlit run src/ui/Home.py
"""

import sys
from pathlib import Path

import streamlit as st

_root = Path(__file__).parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.ui.brand import header, inject_brand_css, sidebar_exit_control  # noqa: E402
from src.ui.lifecycle import start_idle_watchdog  # noqa: E402

st.set_page_config(
    page_title="DistrictSync — SpacesEDU",
    page_icon="🏫",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def _ensure_idle_watchdog() -> bool:
    """Start the idle-shutdown watchdog exactly once per server process.

    ``@st.cache_resource`` is the once-per-process hook: the thread is started on
    the first script run and the cached result is reused on every rerun / page,
    so closing the last browser tab reaps the server (and leaked console) after
    the grace period. The watchdog degrades to a no-op + warning on any Streamlit
    internal-API change, so this can never break the UI.
    """
    start_idle_watchdog()
    return True


_ensure_idle_watchdog()

inject_brand_css()
header(
    "DistrictSync",
    "MyEducation BC General Data Extracts → SpacesEDU Advanced CSV",
)
sidebar_exit_control()

# ---------------------------------------------------------------------------
# Setup status banner
# ---------------------------------------------------------------------------
try:
    from src.config.app_config import AppConfig  # noqa: E402

    cfg = AppConfig.load()
    if cfg.schedule_registered:
        # Fully configured with active schedule
        status_parts = [
            f"**Configured** — District: `{cfg.sis_type}`",
            f"Daily run: `{cfg.schedule_time}`",
            f"SFTP: {'enabled' if cfg.sftp_is_configured() else 'disabled'}",
        ]

        # Show last run status on Windows
        if sys.platform == "win32":
            try:
                from src.scheduler.windows import query_task

                task_info = query_task(cfg.schedule_task_name)
                if task_info.get("exists"):
                    next_run = task_info.get("next_run_time", "—")
                    last_result = task_info.get("last_result", "—")
                    status_parts.append(f"Next run: `{next_run}`")
                    if last_result == "0":
                        status_parts.append("Last run: success")
                    elif last_result != "—":
                        status_parts.append(f"Last run result: `{last_result}`")
            # Optional status display — ignore failures, widget is informational only.
            except Exception:  # nosec B110
                pass

        st.success(" | ".join(status_parts))
    else:
        st.info("**No active schedule.** Set one up in the Setup Wizard, or use Convert for ad-hoc runs.")
        if st.button("Start Setup Wizard", type="primary"):
            st.switch_page("pages/01_Setup_Wizard.py")
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
        "DistrictSync reads the standard General Data Extract (GDE) files from MyEducation BC and "
        "produces the CSV files required by the SpacesEDU / myBlueprint+ Advanced CSV format. "
        "It runs automatically every night and uploads results via SFTP."
    )

    st.markdown("""
| Input (MyEdBC GDE) | Output (SpacesEDU / myBlueprint+) |
|---|---|
| Student Demographic | `Students.csv` |
| Staff Information – Enhanced | `Staff.csv` |
| Emergency Contact Information | `Family.csv` |
| Student Schedule + Course Information | `Classes.csv` |
| Student Schedule + Class Information – Enhanced | `Enrollments.csv` |
| Course Information | `CourseInfo.csv` *(myBlueprint+)* |
| Student Course History + Selection + Course Information | `StudentCourses.csv` *(myBlueprint+)* |
""")

with col2:
    st.subheader("Navigation")
    st.page_link("pages/01_Setup_Wizard.py", label="Setup Wizard", icon="⚙️")
    st.caption("Configure paths, schedule, and SFTP upload")
    st.page_link("pages/02_Convert.py", label="Convert", icon="🔄")
    st.caption("Upload GDE files and download CSVs on demand")
    st.page_link("pages/03_Run_History.py", label="Run History", icon="📋")
    st.caption("View the log of automated daily runs")
    st.page_link("pages/04_Mapping_Editor.py", label="Mapping Editor", icon="🗺️")
    st.caption("Create or customize district data configurations")
    st.page_link("pages/05_Help.py", label="Help & Docs", icon="❓")
    st.caption("Full documentation: output format, how it works, troubleshooting")

st.divider()
st.markdown(
    "📖 **Documentation** is built into this app on the **Help & Docs** page (sidebar). "
    "For setup basics, see the [SpacesEDU Help Centre article](https://help.spacesedu.com/en-ca/article/mx56qo)."
)
st.caption("SpacesEDU by myBlueprint · DistrictSync · support@myBlueprint.ca")
