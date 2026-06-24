"""Run History page — shows the history of automated ETL runs.

Parses structured JSON log lines emitted by the pipeline and displays
them in a table.  Falls back to showing the raw log tail if no
structured lines are found.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_root = Path(__file__).parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.ui.brand import header, inject_brand_css  # noqa: E402
from src.utils.paths import user_log_file  # noqa: E402

st.set_page_config(page_title="Run History — DistrictSync", page_icon="📋", layout="wide")
inject_brand_css()
header("Run History", "Automated daily ETL run log")

# ---------------------------------------------------------------------------
# Locate the log file
#
# All code paths (manual CLI, wizard, scheduled task) now log to the
# canonical user-data path. Fall back to a legacy relative location
# so logs from older dev runs still show up during a transition.
# ---------------------------------------------------------------------------

LOG_PATHS = [
    user_log_file(),
    Path("etl_tool.log"),  # legacy fallback for very old local runs
]

log_file = next((p for p in LOG_PATHS if p.exists()), None)

if log_file is None:
    st.info("No run history yet.  Once the schedule is activated (via Setup Wizard), run logs will appear here.")
    st.stop()

# ---------------------------------------------------------------------------
# Parse structured log lines (JSON-tagged)
# ---------------------------------------------------------------------------

STRUCTURED_TAG = "__DISTRICTSYNC_RUN__"
runs: list[dict] = []

with open(log_file, encoding="utf-8", errors="replace") as f:
    for line in f:
        if STRUCTURED_TAG in line:
            try:
                # Format: ... __DISTRICTSYNC_RUN__ {...json...}
                json_part = line.split(STRUCTURED_TAG, 1)[1].strip()
                entry = json.loads(json_part)
                runs.append(entry)
            except (json.JSONDecodeError, IndexError):
                pass

# ---------------------------------------------------------------------------
# Display structured runs if available
# ---------------------------------------------------------------------------

if runs:
    st.subheader(f"Last {min(len(runs), 50)} Runs")

    def _fmt(value: object) -> str:
        """Render a count/duration as a uniform string so Streamlit's Arrow
        serializer never sees a mixed number/str column. A column that mixes
        ints (runs that produced the entity) with the "—" sentinel (runs that
        didn't) makes pyarrow infer int64 and fail on the em-dash."""
        return "—" if value is None else str(value)

    def _status_cell(r: dict) -> str:
        """At-a-glance Status that never contradicts the exit code (display-only).

        The run-log `status` stays `success`/`failed` for the ETL run itself;
        SFTP delivery and data-error counts are separate axes already in the
        record. Surface them in the amber Status cell so a run that ETL-succeeded
        but failed to deliver, or completed with field-transform errors, is not
        shown as a plain green ✅ (the dedicated SFTP column already flags the
        delivery boolean; this keeps the headline Status honest)."""
        if r.get("status") != "success":
            return "❌ Failed"
        sftp_failed = bool(r.get("sftp_attempted")) and not r.get("sftp_ok")
        total_data_errors = (r.get("data_errors") or {}).get("total", 0)
        if sftp_failed and total_data_errors:
            return f"⚠️ ETL OK · SFTP FAILED · {total_data_errors} data errors"
        if sftp_failed:
            return "⚠️ ETL OK · SFTP FAILED"
        if total_data_errors:
            return f"⚠️ Completed with {total_data_errors} data errors"
        return "✅ Success"

    rows = []
    for r in runs[-50:][::-1]:  # newest first
        rows.append(
            {
                "Date / Time": r.get("timestamp", "—"),
                "Status": _status_cell(r),
                "Duration (s)": _fmt(r.get("duration_s")),
                "Students": _fmt(r.get("Students")),
                "Staff": _fmt(r.get("Staff")),
                "Family": _fmt(r.get("Family")),
                "Classes": _fmt(r.get("Classes")),
                "Enrollments": _fmt(r.get("Enrollments")),
                "CourseInfo": _fmt(r.get("CourseInfo")),
                "StudentCourses": _fmt(r.get("StudentCourses")),
                "SFTP": "✅" if r.get("sftp_ok") else ("❌" if r.get("sftp_attempted") else "—"),
                "Error": r.get("error", ""),
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)

else:
    st.info("No structured run history found.  Run history will be recorded starting from the next scheduled run.")

# ---------------------------------------------------------------------------
# Always show the recent raw log (last 200 lines)
# ---------------------------------------------------------------------------

st.divider()
with st.expander("Raw Log (last 200 lines)", expanded=not runs):
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-200:])
        st.code(tail, language="text")
    except Exception as e:
        st.error(f"Could not read log file: {e}")

st.caption(f"Log file: `{log_file.resolve()}`")

col1, col2 = st.columns([1, 9])
with col1:
    if st.button("↺ Refresh", type="primary"):
        st.rerun()

st.divider()
st.caption("SpacesEDU by myBlueprint · DistrictSync · support@myBlueprint.ca")
