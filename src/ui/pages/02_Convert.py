"""Ad-hoc conversion page — upload GDE files, convert, download CSVs.

Full feature parity with CLI: headerless file support, diff vs previous
output, data quality report, anomaly detection, and SFTP upload.
"""

import io
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

_root = Path(__file__).parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.config.app_config import AppConfig  # noqa: E402
from src.config.loader import load_config  # noqa: E402
from src.etl.transformer import DataTransformer  # noqa: E402
from src.main import extract_required_files  # noqa: E402
from src.quality.report import DataQualityReport  # noqa: E402
from src.ui.brand import header, inject_brand_css  # noqa: E402
from src.utils.helpers import normalize_columns  # noqa: E402

MAPPING_DIR = Path("config/mappings")
ANOMALY_THRESHOLD = 0.20

# ---------------------------------------------------------------------------
# Load app config (for district pre-selection, diff, SFTP)
# ---------------------------------------------------------------------------
_app_cfg = AppConfig.load()


def get_available_configs() -> list[str]:
    configs: list[str] = []
    if MAPPING_DIR.exists():
        for path in sorted(MAPPING_DIR.glob("*_mapping.yaml")):
            name = path.stem.replace("_mapping", "")
            configs.append(name)
    return configs


# ---------------------------------------------------------------------------
# Robust file loader (matches DataExtractor encoding/delimiter grid)
# ---------------------------------------------------------------------------


def _load_uploaded_file(
    buf: io.BytesIO,
    filename: str,
    file_headers: dict[str, list[str]],
) -> pd.DataFrame:
    """Load an uploaded file with multi-encoding + delimiter detection.

    Handles headerless files when *filename* is in *file_headers*.
    """
    buf.seek(0)
    content = buf.read()
    explicit_names = file_headers.get(filename)

    encodings = ["utf-8", "latin1", "cp1252"]
    delimiters = [",", "\t", None]  # None = Python engine auto-detect

    for enc in encodings:
        for sep in delimiters:
            try:
                kwargs: dict = {
                    "sep": sep,
                    "encoding": enc,
                    "on_bad_lines": "warn",
                    "low_memory": False,
                }
                if sep is None:
                    kwargs["engine"] = "python"
                if explicit_names:
                    kwargs["header"] = None
                    kwargs["names"] = explicit_names

                df = pd.read_csv(io.BytesIO(content), **kwargs)
                if len(df.columns) > 1 and not df.empty:
                    return normalize_columns(df)
            except Exception:  # nosec B110 - try next encoding/delimiter combo
                continue

    # Last resort: force UTF-8 with error replacement
    df = pd.read_csv(
        io.BytesIO(content),
        encoding="utf-8",
        encoding_errors="replace",
        on_bad_lines="warn",
    )
    return normalize_columns(df)


# ---------------------------------------------------------------------------
# Diff helper (adapted from _print_diff in src/main.py)
# ---------------------------------------------------------------------------


def _compute_diff(
    outputs: dict[str, pd.DataFrame],
    output_dir: Path,
) -> list[dict]:
    """Compare new outputs against existing CSVs. Returns table rows."""
    rows = []
    for name, new_df in outputs.items():
        existing = output_dir / f"{name}.csv"
        if not existing.exists():
            rows.append({"Entity": name, "Previous": "—", "New": len(new_df), "Delta": "NEW"})
            continue
        try:
            old_df = pd.read_csv(existing)
        except Exception:
            rows.append({"Entity": name, "Previous": "?", "New": len(new_df), "Delta": "unreadable"})
            continue
        delta = len(new_df) - len(old_df)
        added = sorted(set(new_df.columns) - set(old_df.columns))
        removed = sorted(set(old_df.columns) - set(new_df.columns))
        rows.append(
            {
                "Entity": name,
                "Previous": len(old_df),
                "New": len(new_df),
                "Delta": f"{'+' if delta >= 0 else ''}{delta}",
                "Added columns": ", ".join(added) or "—",
                "Removed columns": ", ".join(removed) or "—",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Anomaly detection (adapted from _check_anomalies in src/main.py)
# ---------------------------------------------------------------------------


def _check_anomalies_ui(
    outputs: dict[str, pd.DataFrame],
    output_dir: Path,
) -> list[str]:
    """Compare row counts vs previous output; return warning strings."""
    warnings: list[str] = []
    for entity, df in outputs.items():
        prev_path = output_dir / f"{entity}.csv"
        if not prev_path.exists():
            continue
        try:
            with open(prev_path, encoding="utf-8") as f:
                prev_count = sum(1 for _ in f) - 1
        except Exception:  # nosec B110 - skip unreadable previous files
            continue
        if prev_count > 0 and len(df) < prev_count * (1 - ANOMALY_THRESHOLD):
            pct = ((prev_count - len(df)) / prev_count) * 100
            warnings.append(f"{entity} dropped from {prev_count:,} to {len(df):,} rows ({pct:.0f}% decrease)")
    return warnings


# ---------------------------------------------------------------------------
# Conversion pipeline
# ---------------------------------------------------------------------------


def run_conversion(
    uploaded_files: dict[str, io.BytesIO],
    config_name: str,
) -> dict[str, pd.DataFrame]:
    """Run the ETL conversion using validated config with _base inheritance."""
    config = load_config(config_name)
    raw = config.to_raw_dict()
    mappings = raw.get("mappings", {})
    global_config = raw.get("global_config", {})

    # Collect explicit headers for headerless files
    file_headers: dict[str, list[str]] = {}
    for entity_cfg in mappings.values():
        for filename, header_list in entity_cfg.get("headers", {}).items():
            file_headers[filename] = header_list

    raw_data: dict[str, pd.DataFrame] = {}
    for filename, file_buf in uploaded_files.items():
        try:
            df = _load_uploaded_file(file_buf, filename, file_headers)
            raw_data[filename] = df
        except Exception as e:
            st.warning(f"Could not load `{filename}`: {e}")

    if not raw_data:
        st.error("No files could be loaded.")
        return {}

    transformer = DataTransformer()
    sy_sources = global_config.get("school_year_sources", {})
    sy = transformer.determine_school_year(raw_data, sy_sources)
    transformer.set_school_year(sy)

    outputs: dict[str, pd.DataFrame] = {}
    entity_order = global_config.get("entity_order") or list(mappings.keys())

    with st.status("Converting...", expanded=True) as status:
        for entity_name in entity_order:
            st.write(f"Processing {entity_name}...")
            entity_cfg = mappings.get(entity_name, {})
            source_config = entity_cfg.get("source_files", {})
            if not source_config:
                continue
            source_files = list(source_config.values()) if isinstance(source_config, dict) else source_config
            if not source_files:
                continue
            primary_df = raw_data.get(source_files[0], pd.DataFrame())
            if primary_df.empty:
                continue
            transformed = transformer.transform(primary_df, entity_cfg, entity_name, raw_data, global_config)
            if not transformed.empty:
                field_order = list(entity_cfg.get("field_map", {}).keys())
                ordered = [c for c in field_order if c in transformed.columns]
                extra = [c for c in transformed.columns if c not in field_order]
                outputs[entity_name] = transformed[ordered + extra]
        status.update(label="Conversion complete", state="complete")

    return outputs


def create_zip(outputs: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, df in outputs.items():
            zf.writestr(f"{name}.csv", df.to_csv(index=False, encoding="utf-8-sig"))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Convert — GDE2Acsv", page_icon="🔄", layout="wide")
inject_brand_css()
header("Ad-hoc Conversion", "Upload GDE files and download the converted CSVs directly in the browser")

configs = get_available_configs()
if not configs:
    st.error("No mapping configs found in config/mappings/")
    st.stop()

# Sidebar: district selection (pre-select from AppConfig)
default_district = (
    _app_cfg.sis_type if _app_cfg.sis_type in configs else ("myedbc" if "myedbc" in configs else configs[0])
)
selected = st.sidebar.selectbox(
    "District Configuration",
    options=configs,
    index=configs.index(default_district),
    key="convert_district",
)

# Show config info in sidebar
try:
    loaded_cfg = load_config(selected)
    st.sidebar.divider()
    st.sidebar.markdown(f"**Config:** `{loaded_cfg.version}` | **SIS:** {loaded_cfg.sis}")
    hg = loaded_cfg.global_config.homeroom_grades
    if hg:
        st.sidebar.markdown(f"**Homeroom grades:** {', '.join(hg)}")
except Exception as e:
    st.sidebar.error(f"Config error: {e}")

# Clear cached outputs when district changes
if "convert_last_district" not in st.session_state:
    st.session_state.convert_last_district = selected
if st.session_state.convert_last_district != selected:
    st.session_state.convert_last_district = selected
    st.session_state.pop("convert_outputs", None)
    st.session_state.pop("convert_quality", None)

# Step 1: Upload
st.subheader("1. Upload GDE Files")
try:
    config_obj = load_config(selected)
    expected = extract_required_files(config_obj)
except Exception:
    expected = []

if expected:
    st.info(f"Expected files for `{selected}`: {', '.join(expected)}")

uploaded = st.file_uploader(
    "Upload your GDE files",
    type=["txt", "csv"],
    accept_multiple_files=True,
)

if not uploaded:
    st.stop()

MAX_FILE_SIZE = 100 * 1024 * 1024
uploaded_map: dict[str, io.BytesIO] = {}
for f in uploaded:
    if f.size > MAX_FILE_SIZE:
        st.error(f"`{f.name}` exceeds 100MB limit ({f.size / 1024 / 1024:.1f}MB)")
        continue
    uploaded_map[f.name] = f

st.success(f"Uploaded {len(uploaded_map)} file(s): {', '.join(uploaded_map.keys())}")

# Show missing files warning
if expected:
    missing = [f for f in expected if f not in uploaded_map]
    if missing:
        st.warning(f"Missing expected files: {', '.join(missing)}")

# Step 2: Convert
st.subheader("2. Convert")
if st.button("Run Conversion", type="primary"):
    outputs = run_conversion(uploaded_map, selected)
    if outputs:
        st.session_state.convert_outputs = outputs
        # Run quality report
        report = DataQualityReport().analyze(outputs)
        st.session_state.convert_quality = report.to_text()
    else:
        st.error("No output produced. Check that the correct district config is selected.")
        st.session_state.pop("convert_outputs", None)
        st.session_state.pop("convert_quality", None)

# Display results from session state (persists across re-renders)
outputs = st.session_state.get("convert_outputs")
if outputs:
    st.success(f"Generated {len(outputs)} output file(s)")

    # Quality report
    quality_text = st.session_state.get("convert_quality", "")
    if quality_text:
        with st.expander("Data Quality Report", expanded=False):
            st.code(quality_text, language="text")

    # Diff vs previous output
    if _app_cfg.output_dir and Path(_app_cfg.output_dir).is_dir():
        with st.expander("Diff vs Previous Output", expanded=False):
            diff_rows = _compute_diff(outputs, Path(_app_cfg.output_dir))
            st.dataframe(pd.DataFrame(diff_rows), use_container_width=True, hide_index=True)

    st.subheader("3. Preview")
    for name, df in outputs.items():
        with st.expander(f"{name}.csv — {len(df):,} rows", expanded=False):
            st.dataframe(df.head(50), use_container_width=True)

    st.subheader("4. Download")
    zip_data = create_zip(outputs)
    st.download_button(
        label="Download All CSVs (ZIP)",
        data=zip_data,
        file_name="gde2acsv_output.zip",
        mime="application/zip",
        type="primary",
    )

    cols = st.columns(min(len(outputs), 5))
    for col, (name, df) in zip(cols, outputs.items()):
        with col:
            st.download_button(
                label=f"{name}.csv",
                data=df.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"{name}.csv",
                mime="text/csv",
            )

    # Step 5: SFTP upload (only shown when configured)
    if _app_cfg.sftp_is_configured():
        st.subheader("5. Upload via SFTP")

        # Anomaly warnings
        if _app_cfg.output_dir and Path(_app_cfg.output_dir).is_dir():
            anomalies = _check_anomalies_ui(outputs, Path(_app_cfg.output_dir))
            if anomalies:
                for msg in anomalies:
                    st.warning(f"Anomaly detected: {msg}")
                st.caption("Row count drops > 20% may indicate a data problem. Review before uploading.")
        else:
            anomalies = []

        try:
            import paramiko  # noqa: F401

            _sftp_available = True
        except ImportError:
            _sftp_available = False

        if not _sftp_available:
            st.info("SFTP upload requires the `paramiko` package. Install with: `pip install paramiko`")
        else:
            st.caption(f"Upload to `{_app_cfg.sftp_host}:{_app_cfg.sftp_port}{_app_cfg.sftp_remote_path}`")
            if st.button("Upload via SFTP", type="secondary"):
                from src.sftp.uploader import SFTPUploader

                with st.spinner("Uploading..."):
                    try:
                        with tempfile.TemporaryDirectory() as tmpdir:
                            tmp_path = Path(tmpdir)
                            for name, df in outputs.items():
                                df.to_csv(tmp_path / f"{name}.csv", index=False, encoding="utf-8-sig")
                            uploader = SFTPUploader(
                                host=_app_cfg.sftp_host,
                                port=_app_cfg.sftp_port,
                                username=_app_cfg.sftp_username,
                                remote_path=_app_cfg.sftp_remote_path,
                            )
                            uploaded_files_list = uploader.upload_csvs(tmp_path)
                        st.success(f"Uploaded {len(uploaded_files_list)} file(s): {', '.join(uploaded_files_list)}")
                    except Exception as e:
                        st.error(f"SFTP upload failed: {e}")

st.divider()
st.caption("SpacesEDU by myBlueprint · GDE2Acsv · support@myBlueprint.ca")
