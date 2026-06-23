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
from src.config.loader import available_configs, load_config  # noqa: E402
from src.etl.extractor import DataExtractor  # noqa: E402
from src.etl.loader import DataLoader  # noqa: E402
from src.etl.pipeline import (  # noqa: E402
    TransformOutputs,
    compute_anomalies,
    extract_required_files,
    run_transform,
)
from src.quality.report import DataQualityReport  # noqa: E402
from src.ui.brand import header, inject_brand_css  # noqa: E402
from src.utils.helpers import build_zip_name  # noqa: E402

# ---------------------------------------------------------------------------
# Load app config (for district pre-selection, diff, SFTP)
# ---------------------------------------------------------------------------
_app_cfg = AppConfig.load()


def get_available_configs() -> list[str]:
    """List all districts from user dir + bundled defaults."""
    return available_configs()


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
                # Keep "Previous" a uniform string: it shares this column with the
                # "—"/"?" sentinels above. A mixed int/str object column makes
                # pyarrow (Streamlit's Arrow serializer) infer int64 and fail on
                # the em-dash. Delta is computed from the int before stringifying.
                "Previous": str(len(old_df)),
                "New": len(new_df),
                "Delta": f"{'+' if delta >= 0 else ''}{delta}",
                "Added columns": ", ".join(added) or "—",
                "Removed columns": ", ".join(removed) or "—",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Conversion pipeline
# ---------------------------------------------------------------------------


def run_conversion(
    uploaded_files: dict[str, io.BytesIO],
    config_name: str,
) -> TransformOutputs | None:
    """Run the ETL conversion using validated config with _base inheritance.

    Thin adapter over the shared engine: reads uploads → ``load_from_bytes``
    (same bytes-core as the CLI: encoding detection + malformed-row repair) →
    ``run_transform`` (school-year determination, ``enabled_entities`` filter,
    per-entity loop, field-order collection). Returns the shared
    :class:`TransformOutputs` (frames + per-entity field order) so the page
    writes through ``DataLoader`` byte-for-byte identically to the CLI, or
    ``None`` if nothing could be loaded.
    """
    config = load_config(config_name)
    raw = config.to_raw_dict()
    mappings = raw.get("mappings", {})
    global_config = raw.get("global_config", {})

    # Collect explicit headers for headerless files
    file_headers: dict[str, list[str]] = {}
    for entity_cfg in mappings.values():
        for filename, header_list in entity_cfg.get("headers", {}).items():
            file_headers[filename] = header_list

    # Parse uploads through the SAME bytes-core the CLI uses (encoding detection +
    # malformed-row repair). Read each upload into bytes and hand them to
    # DataExtractor.load_from_bytes — keyed by the original filename so config
    # source_files resolve identically to a disk run.
    sources: dict[str, bytes] = {}
    for filename, file_buf in uploaded_files.items():
        file_buf.seek(0)
        sources[filename] = file_buf.getvalue()

    try:
        raw_data = DataExtractor("").load_from_bytes(sources, file_headers)
    except Exception as e:
        st.error(f"Could not load uploaded files: {e}")
        return None

    if not raw_data:
        st.error("No files could be loaded.")
        return None

    with st.status("Converting...", expanded=True) as status:
        # Shared transform-orchestration — the SAME engine the CLI runs, so the
        # Convert page can never diverge (honors enabled_entities + field order).
        result = run_transform(raw_data, mappings, global_config)
        status.update(label="Conversion complete", state="complete")

    return result


def create_zip(outputs: dict[str, pd.DataFrame], field_orders: dict[str, list[str]]) -> bytes:
    """Zip every output CSV using the SAME per-entity encoding + column selection
    as the disk/SFTP write path (``DataLoader``). ``csv_encoding`` is the single
    source of truth for the BOM rule (no BOM for StudentAttendance, BOM for the
    rest); ``DataLoader.select_ordered`` writes strictly the contract columns and
    raises the same ``ValueError`` (not a raw ``KeyError``) on a missing column,
    so the download path fails loud exactly like the disk/SFTP write."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, df in outputs.items():
            field_order = field_orders.get(name, list(df.columns))
            encoding = DataLoader.csv_encoding(name)
            ordered = DataLoader.select_ordered(df, field_order, name)
            zf.writestr(f"{name}.csv", ordered.to_csv(index=False, encoding=encoding))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Convert — DistrictSync", page_icon="🔄", layout="wide")
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
    st.session_state.pop("convert_field_orders", None)
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
    result = run_conversion(uploaded_map, selected)
    if result and result.outputs:
        st.session_state.convert_outputs = result.outputs
        st.session_state.convert_field_orders = result.field_orders
        # Run quality report
        report = DataQualityReport().analyze(result.outputs)
        st.session_state.convert_quality = report.to_text()
    else:
        st.error("No output produced. Check that the correct district config is selected.")
        st.session_state.pop("convert_outputs", None)
        st.session_state.pop("convert_field_orders", None)
        st.session_state.pop("convert_quality", None)

# Display results from session state (persists across re-renders)
outputs = st.session_state.get("convert_outputs")
field_orders = st.session_state.get("convert_field_orders", {})
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
            st.dataframe(pd.DataFrame(diff_rows), width="stretch", hide_index=True)

    st.subheader("3. Preview")
    for name, df in outputs.items():
        with st.expander(f"{name}.csv — {len(df):,} rows", expanded=False):
            st.dataframe(df.head(50), width="stretch")

    st.subheader("4. Download")
    # The download path writes through the SAME encoding + column-order rules as
    # the CLI/SFTP path (DataLoader.csv_encoding + strict df[field_order]). A
    # missing field-map column surfaces here as a loud, actionable error rather
    # than a silent partial CSV — fail loud, matching the scheduled run.
    try:
        zip_data = create_zip(outputs, field_orders)
        st.download_button(
            label="Download All CSVs (ZIP)",
            data=zip_data,
            file_name=build_zip_name(selected),
            mime="application/zip",
            type="primary",
        )

        cols = st.columns(min(len(outputs), 5))
        for col, (name, df) in zip(cols, outputs.items()):
            field_order = field_orders.get(name, list(df.columns))
            ordered = DataLoader.select_ordered(df, field_order, name)
            with col:
                st.download_button(
                    label=f"{name}.csv",
                    data=ordered.to_csv(index=False, encoding=DataLoader.csv_encoding(name)),
                    file_name=f"{name}.csv",
                    mime="text/csv",
                )
    except ValueError as e:
        st.error(f"Cannot build download — a required output column is missing: {e}")

    # Step 5: SFTP upload (only shown when configured)
    if _app_cfg.sftp_is_configured():
        st.subheader("5. Upload via SFTP")

        # Anomaly warnings — shared compute (src.etl.pipeline.compute_anomalies),
        # single-sourced with the CLI; the page adds only its own presentation.
        if _app_cfg.output_dir and Path(_app_cfg.output_dir).is_dir():
            anomalies = compute_anomalies(outputs, Path(_app_cfg.output_dir))
            if anomalies:
                for msg in anomalies:
                    st.warning(f"Anomaly detected: {msg}")
                st.caption("Row count drops > 20% may indicate a data problem. Review before uploading.")
        else:
            anomalies = []

        st.caption(f"Upload to `{_app_cfg.sftp_host}:{_app_cfg.sftp_port}{_app_cfg.sftp_remote_path}`")
        if st.button("Upload via SFTP", type="secondary"):
            from src.sftp.uploader import SFTPUploader

            with st.spinner("Uploading..."), tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                # Write through the SAME atomic DataLoader path the CLI
                # uses — identical per-entity encoding (no BOM for
                # StudentAttendance) and strict column order. A missing
                # field-map column raises ValueError HERE (staging), before
                # any upload — surface it as a write/validation failure, not
                # a mislabeled "upload failed".
                try:
                    DataLoader(tmpdir).save_all(outputs, field_orders)
                except ValueError as e:
                    st.error(f"Cannot prepare upload — a required output column is missing: {e}")
                else:
                    try:
                        uploader = SFTPUploader(
                            host=_app_cfg.sftp_host,
                            port=_app_cfg.sftp_port,
                            username=_app_cfg.sftp_username,
                            remote_path=_app_cfg.sftp_remote_path,
                        )
                        uploaded_files_list = uploader.upload_csvs(tmp_path, sis_type=selected)
                        st.success(
                            f"Uploaded ZIP with {len(uploaded_files_list)} file(s): {', '.join(uploaded_files_list)}"
                        )
                    except Exception as e:
                        st.error(f"SFTP upload failed: {e}")

st.divider()
st.caption("SpacesEDU by myBlueprint · DistrictSync · support@myBlueprint.ca")
