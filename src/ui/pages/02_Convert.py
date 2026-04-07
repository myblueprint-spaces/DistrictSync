"""Ad-hoc conversion page — upload GDE files, convert, download CSVs.

This is the original single-page Streamlit UI, now housed as page 2 of
the multi-page app.  Partners can use this for one-off conversions
without running the full CLI pipeline.
"""

import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

_root = Path(__file__).parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.config.loader import load_config  # noqa: E402
from src.etl.transformer import DataTransformer  # noqa: E402
from src.main import extract_required_files  # noqa: E402
from src.quality.report import DataQualityReport  # noqa: E402
from src.ui.brand import header, inject_brand_css  # noqa: E402

MAPPING_DIR = Path("config/mappings")


def get_available_configs() -> list[str]:
    configs: list[str] = []
    if MAPPING_DIR.exists():
        for path in sorted(MAPPING_DIR.glob("*_mapping.yaml")):
            name = path.stem.replace("_mapping", "")
            configs.append(name)
    return configs


def run_conversion(
    uploaded_files: dict[str, io.BytesIO],
    config_name: str,
) -> dict[str, pd.DataFrame]:
    """Run the ETL conversion using validated config with _base inheritance."""
    config = load_config(config_name)
    raw = config.to_raw_dict()
    mappings = raw.get("mappings", {})
    global_config = raw.get("global_config", {})

    raw_data: dict[str, pd.DataFrame] = {}
    for filename, file_buf in uploaded_files.items():
        try:
            file_buf.seek(0)
            content = file_buf.read()
            for encoding in ("utf-8", "latin1", "cp1252"):
                try:
                    text = content.decode(encoding)
                    break
                except (UnicodeDecodeError, AttributeError):
                    continue
            else:
                text = content.decode("utf-8", errors="replace")

            first_line = text.split("\n")[0]
            delimiter = "\t" if "\t" in first_line else ","
            df = pd.read_csv(io.StringIO(text), sep=delimiter)
            df.columns = [col.strip().lower() for col in df.columns]
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

# Sidebar: district selection
selected = st.sidebar.selectbox(
    "District Configuration",
    options=configs,
    index=configs.index("myedbc") if "myedbc" in configs else 0,
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
    "Upload your .txt GDE files",
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

st.divider()
st.caption("SpacesEDU by myBlueprint · GDE2Acsv · support@myBlueprint.ca")
