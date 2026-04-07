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

st.set_page_config(page_title="Run History — GDE2Acsv", page_icon="📋", layout="wide")
inject_brand_css()
header("Run History", "Automated daily ETL run log")

# ---------------------------------------------------------------------------
# Locate the log file
# ---------------------------------------------------------------------------

LOG_PATHS = [
    Path("etl_tool.log"),
    Path.home() / ".gde2acsv" / "etl_tool.log",
]

log_file = next((p for p in LOG_PATHS if p.exists()), None)

if log_file is None:
    st.info(
        "No run history yet.  Once the schedule is activated (via Setup Wizard), "
        "run logs will appear here."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Parse structured log lines (JSON-tagged)
# ---------------------------------------------------------------------------

STRUCTURED_TAG = "__GDE2ACSV_RUN__"
runs: list[dict] = []

with open(log_file, encoding="utf-8", errors="replace") as f:
    for line in f:
        if STRUCTURED_TAG in line:
            try:
                # Format: ... __GDE2ACSV_RUN__ {...json...}
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

    rows = []
    for r in runs[-50:][::-1]:  # newest first
        rows.append({
            "Date / Time": r.get("timestamp", "—"),
            "Status": "✅ Success" if r.get("status") == "success" else "❌ Failed",
            "Duration (s)": r.get("duration_s", "—"),
            "Students": r.get("Students", "—"),
            "Staff": r.get("Staff", "—"),
            "Family": r.get("Family", "—"),
            "Classes": r.get("Classes", "—"),
            "Enrollments": r.get("Enrollments", "—"),
            "SFTP": "✅" if r.get("sftp_ok") else ("❌" if r.get("sftp_attempted") else "—"),
            "Error": r.get("error", ""),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

else:
    st.info(
        "No structured run history found.  "
        "Run history will be recorded starting from the next scheduled run."
    )

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
st.caption("SpacesEDU by myBlueprint · GDE2Acsv · support@myBlueprint.ca")
