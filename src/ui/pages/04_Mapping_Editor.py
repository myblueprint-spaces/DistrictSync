"""Mapping Editor — visual wizard for creating and editing district mapping configs.

Guides non-technical users through configuring source file names, field
mappings, academic calendar, and other district-specific settings.
Saves the result as a minimal override YAML with _base inheritance.
"""

import sys
from pathlib import Path
from typing import Any

import streamlit as st
import yaml

_root = Path(__file__).parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.config.loader import available_configs, load_config  # noqa: E402
from src.ui.brand import header, inject_brand_css, step_progress  # noqa: E402
from src.ui.mapping_helpers import (  # noqa: E402
    CEDS_GRADES,
    SOURCE_FILE_ROLES,
    build_override_dict,
    column_selectbox,
    detect_columns,
    get_field_metadata,
    save_mapping_yaml,
)

st.set_page_config(page_title="Mapping Editor — DistrictSync", page_icon="🗺️", layout="wide")
inject_brand_css()
header("Mapping Editor", "Create or customize your district's data configuration")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "me_step" not in st.session_state:
    st.session_state.me_step = 1
if "me_config" not in st.session_state:
    st.session_state.me_config = {}
if "me_detected_columns" not in st.session_state:
    st.session_state.me_detected_columns = {}
if "me_headerless_files" not in st.session_state:
    st.session_state.me_headerless_files = {}


def _go(step: int) -> None:
    st.session_state.me_step = step


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_available_configs() -> list[str]:
    """List all district identifiers from user dir + bundled defaults."""
    return available_configs()


def _load_resolved_config(name: str) -> dict:
    """Load and resolve a config (with _base inheritance) to a raw dict."""
    cfg = load_config(name)
    return cfg.to_raw_dict()


def _get_entity_source_file(config: dict, entity: str, role: str) -> str:
    """Get the source file name for a role within an entity."""
    return config.get("mappings", {}).get(entity, {}).get("source_files", {}).get(role, "")


def _get_all_source_files(config: dict) -> dict[str, str]:
    """Collect all unique source files across entities. Returns {role: filename}."""
    files: dict[str, str] = {}
    for entity_cfg in config.get("mappings", {}).values():
        for role, filename in entity_cfg.get("source_files", {}).items():
            if role not in files:
                files[role] = filename
    sy_sources = config.get("global_config", {}).get("school_year_sources", {})
    for role, filename in sy_sources.items():
        if role not in files:
            files[role] = filename
    return files


def _get_field_value(config: dict, entity: str, field: str, default: str = "") -> Any:
    """Get a field mapping value from the resolved config."""
    fm = config.get("mappings", {}).get(entity, {}).get("field_map", {})
    val = fm.get(field, default)
    if isinstance(val, dict) and "column" in val:
        return val["column"]
    if isinstance(val, str):
        return val
    return default


def _get_detected_cols_for_entity(entity: str, config: dict) -> list[str]:
    """Get auto-detected columns for the primary source file of an entity."""
    sf = config.get("mappings", {}).get(entity, {}).get("source_files", {})
    if not sf:
        return []
    primary_file = list(sf.values())[0]
    return st.session_state.me_detected_columns.get(primary_file, [])


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------
STEPS = ["Start", "Files", "Calendar", "Students", "Staff & Family", "Classes and Courses", "Save"]
step_progress(st.session_state.me_step, total=len(STEPS))
step_cols = st.columns(len(STEPS))
for i, (col, label) in enumerate(zip(step_cols, STEPS), start=1):
    current = st.session_state.me_step
    if i < current:
        col.markdown(f"<span style='color:#16A34A;font-size:0.8rem'>✓ {i}. {label}</span>", unsafe_allow_html=True)
    elif i == current:
        col.markdown(
            f"<span style='color:#1D5BB5;font-size:0.8rem;font-weight:700'>● {i}. {label}</span>",
            unsafe_allow_html=True,
        )
    else:
        col.markdown(f"<span style='color:#94A3B8;font-size:0.8rem'>{i}. {label}</span>", unsafe_allow_html=True)
st.divider()

# ===================================================================
# STEP 1 — Getting Started
# ===================================================================
if st.session_state.me_step == 1:
    st.subheader("Step 1 — Getting Started")
    st.markdown(
        "Create a new district configuration or edit an existing one. "
        "Most settings inherit from the default MyEdBC template — "
        "you only need to change what's different for your district."
    )

    # All entities the pipeline knows how to produce. The 5 "rostering"
    # entities are the SpacesEDU standard; CourseInfo + StudentCourses
    # are the myBlueprint+ additions.
    ALL_ENTITY_OPTIONS = ["Students", "Staff", "Family", "Classes", "Enrollments", "CourseInfo", "StudentCourses"]
    DEFAULT_ROSTERING_ENTITIES = ["Students", "Staff", "Family", "Classes", "Enrollments"]

    mode = st.radio(
        "What would you like to do?",
        ["Create a new district configuration", "Edit an existing configuration"],
        key="me_mode_radio",
    )

    if "Create" in mode:
        district_id = st.text_input(
            "District identifier",
            placeholder="sd40",
            help="Used as the config filename. Use your district number, e.g. sd40, sd99.",
            key="me_new_district_id",
        )
        default_entities = DEFAULT_ROSTERING_ENTITIES
        selected_existing = None
    else:
        configs = _get_available_configs()
        if not configs:
            st.warning("No mapping configs found.")
            district_id = None
            selected_existing = None
            default_entities = DEFAULT_ROSTERING_ENTITIES
        else:
            selected_existing = st.selectbox("Select configuration to edit", configs, key="me_edit_select")
            district_id = None
            # Pull the existing config's enabled_entities so the multiselect
            # reflects whatever was already saved (e.g. mbp_core's 3-entity
            # set, mbp_all's full 7). Falls back to the rostering default
            # when the field is missing or empty.
            try:
                existing = _load_resolved_config(selected_existing)
                existing_enabled = existing.get("global_config", {}).get("enabled_entities") or []
                default_entities = [
                    e for e in existing_enabled if e in ALL_ENTITY_OPTIONS
                ] or DEFAULT_ROSTERING_ENTITIES
            except Exception:
                default_entities = DEFAULT_ROSTERING_ENTITIES

    # Shared output-CSV selector — shown for both create and edit so the
    # user controls which entities the pipeline runs. Disabling Family
    # (for example) doesn't remove the Family field_map from the YAML
    # but does skip the entity at run time.
    st.markdown("---")
    st.markdown("#### Output CSVs")
    enabled_entities = st.multiselect(
        "Which CSV files should this configuration generate?",
        options=ALL_ENTITY_OPTIONS,
        default=default_entities,
        help=(
            "The 5 SpacesEDU rostering files are enabled by default. "
            "Add CourseInfo and StudentCourses to also generate the myBlueprint+ course CSVs."
        ),
        key="me_enabled_entities",
    )

    if "Create" in mode:
        if st.button("Continue →", type="primary"):
            if not district_id:
                st.error("Please enter a district identifier.")
            elif not district_id.replace("_", "").replace("-", "").isalnum():
                st.error("District ID must be letters, numbers, underscores, or hyphens only.")
            elif not enabled_entities:
                st.error("Select at least one output CSV.")
            else:
                existing = _get_available_configs()
                full_id = f"{district_id}myedbc"
                if full_id in existing:
                    st.warning(f"Configuration `{full_id}` already exists. Use 'Edit existing' to modify it.")
                else:
                    cfg_dict = _load_resolved_config("myedbc")
                    cfg_dict.setdefault("global_config", {})["enabled_entities"] = enabled_entities
                    st.session_state.me_config = cfg_dict
                    st.session_state.me_district_id = district_id
                    st.session_state.me_mode = "create"
                    _go(2)
                    st.rerun()
    elif selected_existing is not None and st.button("Continue →", type="primary"):
        if not enabled_entities:
            st.error("Select at least one output CSV.")
        else:
            cfg_dict = _load_resolved_config(selected_existing)
            cfg_dict.setdefault("global_config", {})["enabled_entities"] = enabled_entities
            st.session_state.me_config = cfg_dict
            st.session_state.me_district_id = selected_existing.replace("myedbc", "")
            st.session_state.me_mode = "edit"
            _go(2)
            st.rerun()

# ===================================================================
# STEP 2 — Source Files
# ===================================================================
elif st.session_state.me_step == 2:
    st.subheader("Step 2 — Your GDE Files")
    st.markdown(
        "Tell DistrictSync what your data files are called. "
        "Most districts use the standard names, but some have different prefixes or extensions."
    )

    config = st.session_state.me_config
    current_files = _get_all_source_files(config)

    st.markdown("#### File Names")
    new_files: dict[str, str] = {}
    for role, meta in SOURCE_FILE_ROLES.items():
        default = current_files.get(role, "")
        new_files[role] = st.text_input(
            meta["label"],
            value=default,
            help=meta["help"],
            key=f"me_file_{role}",
        )

    # Optional: upload sample files for column detection
    st.markdown("---")
    st.markdown("#### Upload Sample Files (Optional)")
    st.caption(
        "Upload one or more of your GDE files so the wizard can auto-detect column names. "
        "This makes the next steps easier — you'll pick from a dropdown instead of typing."
    )

    uploaded = st.file_uploader(
        "Upload sample GDE files",
        type=["txt", "csv"],
        accept_multiple_files=True,
        key="me_sample_upload",
    )

    if uploaded:
        for f in uploaded:
            content = f.read()
            cols, is_headerless = detect_columns(content, f.name)
            st.session_state.me_detected_columns[f.name] = cols
            if is_headerless:
                st.session_state.me_headerless_files[f.name] = content
                st.warning(f"`{f.name}` appears to have no column headers.")
            else:
                st.success(f"`{f.name}`: {len(cols)} columns detected")

    # Handle headerless files
    if st.session_state.me_headerless_files:
        st.markdown("---")
        st.markdown("#### Headerless Files")
        st.caption("These files appear to have no column headers. Please provide the column names.")

        for filename, content in st.session_state.me_headerless_files.items():
            st.markdown(f"**{filename}**")
            # Count columns from data
            first_line = content.decode("utf-8", errors="replace").split("\n")[0]
            delim = "\t" if "\t" in first_line else ","
            col_count = len(first_line.split(delim))

            # Standard Student Schedule headers
            default_headers = [
                "School Year",
                "School Number",
                "Student Number",
                "PEN",
                "Grade",
                "Homeroom",
                "Course School Number",
                "Course Code",
                "District Course Code",
                "Course Title",
                "Short Name",
                "Period",
                "Day",
                "Semester",
                "Section Letter",
                "Master Timetable ID",
                "Teacher ID",
                "Teacher Name",
                "Primary Teacher",
                "Enrolment Status",
            ]

            header_text = st.text_area(
                f"Column names for {filename} ({col_count} columns, one per line)",
                value="\n".join(default_headers[:col_count]),
                height=200,
                key=f"me_headers_{filename}",
            )
            parsed_headers = [h.strip() for h in header_text.strip().split("\n") if h.strip()]
            if len(parsed_headers) != col_count:
                st.error(f"Expected {col_count} column names but got {len(parsed_headers)}.")
            else:
                st.session_state.me_detected_columns[filename] = parsed_headers
                st.success(f"{len(parsed_headers)} headers set for `{filename}`")

    # Navigation
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("← Back"):
            _go(1)
            st.rerun()
    with col2:
        if st.button("Continue →", type="primary"):
            # Apply file name changes to config
            config = st.session_state.me_config
            for entity_cfg in config.get("mappings", {}).values():
                sf = entity_cfg.get("source_files", {})
                for role in list(sf.keys()):
                    if role in new_files and new_files[role]:
                        sf[role] = new_files[role]
            # Update school_year_sources
            gc = config.get("global_config", {})
            sy_sources = gc.get("school_year_sources", {})
            for role in list(sy_sources.keys()):
                if role in new_files and new_files[role]:
                    sy_sources[role] = new_files[role]

            # Store headerless file headers in entity configs
            for filename, cols in st.session_state.me_detected_columns.items():
                if filename in st.session_state.me_headerless_files:
                    for entity_cfg in config.get("mappings", {}).values():
                        if filename in entity_cfg.get("source_files", {}).values():
                            if "headers" not in entity_cfg:
                                entity_cfg["headers"] = {}
                            entity_cfg["headers"][filename] = cols

            _go(3)
            st.rerun()

# ===================================================================
# STEP 3 — School Year & Calendar
# ===================================================================
elif st.session_state.me_step == 3:
    st.subheader("Step 3 — School Year & Calendar")

    config = st.session_state.me_config
    gc = config.get("global_config", {})

    # School year source
    all_files = _get_all_source_files(config)
    file_options = list(set(all_files.values()))
    current_sy_file = list(gc.get("school_year_sources", {}).values())[0] if gc.get("school_year_sources") else ""

    sy_file = st.selectbox(
        "Which file contains the School Year?",
        file_options,
        index=file_options.index(current_sy_file) if current_sy_file in file_options else 0,
        help="DistrictSync reads the school year from this file to generate IDs and dates.",
        key="me_sy_file",
    )

    # Academic dates
    st.markdown("---")
    st.markdown("#### Academic Calendar Dates")

    date_mode = st.radio(
        "How should start and end dates be determined?",
        ["Calculate automatically from school year", "Set fixed dates"],
        index=0,
        help="Automatic mode uses the school year to compute dates. Fixed mode lets you set specific dates.",
        key="me_date_mode",
    )

    if "Calculate" in date_mode:
        c1, c2 = st.columns(2)
        with c1:
            start_md = st.text_input(
                "Academic start (month-day)",
                value=gc.get("academic_start_month_day", "08-25"),
                help="First day of the school year, e.g. 08-25 for August 25",
                key="me_start_md",
            )
        with c2:
            end_md = st.text_input(
                "Academic end (month-day)",
                value=gc.get("academic_end_month_day", "07-25"),
                help="Last day of the school year, e.g. 07-25 for July 25",
                key="me_end_md",
            )
        use_academic_year = True
        fixed_start = ""
        fixed_end = ""
    else:
        c1, c2 = st.columns(2)
        with c1:
            fixed_start = st.text_input("Start date (YYYY-MM-DD)", value="", key="me_fixed_start")
        with c2:
            fixed_end = st.text_input("End date (YYYY-MM-DD)", value="", key="me_fixed_end")
        use_academic_year = False
        start_md = gc.get("academic_start_month_day", "08-25")
        end_md = gc.get("academic_end_month_day", "07-25")

    # Homeroom grades
    st.markdown("---")
    st.markdown("#### Homeroom Grades")
    st.caption(
        "Elementary students typically stay in one classroom with one teacher (homeroom). "
        "Select which grade levels should have homeroom classes auto-generated."
    )

    current_hr = gc.get("homeroom_grades", [])
    homeroom_grades = st.multiselect(
        "Grades with homeroom classes",
        options=CEDS_GRADES,
        default=[g for g in current_hr if g in CEDS_GRADES],
        key="me_homeroom_grades",
    )

    # Navigation
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("← Back"):
            _go(2)
            st.rerun()
    with col2:
        if st.button("Continue →", type="primary"):
            # Save calendar settings
            gc["school_year_sources"] = {"student_schedule": sy_file}
            gc["homeroom_grades"] = homeroom_grades
            gc["academic_start_month_day"] = start_md
            gc["academic_end_month_day"] = end_md

            # Store date mode for use in Classes step
            st.session_state.me_use_academic_year = use_academic_year
            st.session_state.me_fixed_start = fixed_start
            st.session_state.me_fixed_end = fixed_end

            _go(4)
            st.rerun()

# ===================================================================
# STEP 4 — Student Data Mapping
# ===================================================================
elif st.session_state.me_step == 4:
    st.subheader("Step 4 — Student Data")
    st.markdown(
        "Tell DistrictSync which columns in your Student Information file contain each piece of data needed by SpacesEDU."
    )

    config = st.session_state.me_config
    detected = _get_detected_cols_for_entity("Students", config)
    fields = get_field_metadata()["Students"]
    student_fm = config.get("mappings", {}).get("Students", {}).get("field_map", {})

    new_fm: dict[str, Any] = {}
    for field in fields:
        name = field["name"]
        current = _get_field_value(config, "Students", name)

        if field["widget"] == "auto":
            st.info(f"**{field['label']}** — {field['help']}")
            # Don't add to field_map — it's auto-detected
            continue

        elif field["widget"] == "transform":
            val = column_selectbox(
                field["label"],
                detected,
                current,
                field["help"],
                key=f"me_s_{name}",
            )
            st.caption(f"ℹ️ {field['help']}")
            new_fm[name] = {"column": val, "transform": field["transform"]}

        elif field["widget"] == "email_choice":
            st.markdown(f"**{field['label']}**")
            # Determine current mode
            email_raw = student_fm.get(name, "")
            if isinstance(email_raw, dict) and "format" in email_raw:
                default_mode = "Generate from a pattern"
                default_pattern = email_raw["format"]
                default_col = ""
            elif isinstance(email_raw, str) and email_raw:
                default_mode = "Read from a column in the file"
                default_pattern = ""
                default_col = email_raw
            else:
                default_mode = "Leave blank"
                default_pattern = ""
                default_col = ""

            email_modes = ["Read from a column in the file", "Generate from a pattern", "Leave blank"]
            email_mode = st.radio(
                "How should student emails be set?",
                email_modes,
                index=email_modes.index(default_mode),
                key=f"me_s_{name}_mode",
            )
            if email_mode == "Read from a column in the file":
                new_fm[name] = column_selectbox(
                    "Email column",
                    detected,
                    default_col or "Student email address",
                    "",
                    key=f"me_s_{name}_col",
                )
            elif "pattern" in email_mode:
                pattern = st.text_input(
                    "Email pattern",
                    value=default_pattern or "{student number}@yourdistrict.bc.ca",
                    help="Use column names in braces, e.g. {student number}@sd40.bc.ca",
                    key=f"me_s_{name}_pattern",
                )
                new_fm[name] = {"format": pattern}
            else:
                # Leave blank — omit from field_map (or set empty)
                pass

        elif field["widget"] == "fixed_or_column":
            st.markdown(f"**{field['label']}**")
            fix_mode = st.radio(
                f"{field['label']} source",
                ["Leave blank", "Map to a column"],
                index=0,
                key=f"me_s_{name}_fix",
                label_visibility="collapsed",
            )
            if fix_mode == "Leave blank":
                new_fm[name] = {"value": ""}
            else:
                new_fm[name] = column_selectbox(
                    f"{field['label']} column",
                    detected,
                    "",
                    field["help"],
                    key=f"me_s_{name}_col",
                )

        else:  # column_select
            val = column_selectbox(
                field["label"],
                detected,
                current,
                field["help"],
                key=f"me_s_{name}",
            )
            if val:
                new_fm[name] = val

    # Navigation
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("← Back"):
            _go(3)
            st.rerun()
    with col2:
        if st.button("Continue →", type="primary"):
            config.get("mappings", {}).get("Students", {})["field_map"] = new_fm
            _go(5)
            st.rerun()

# ===================================================================
# STEP 5 — Staff & Family
# ===================================================================
elif st.session_state.me_step == 5:
    st.subheader("Step 5 — Staff & Family Data")

    config = st.session_state.me_config
    metadata = get_field_metadata()

    tab_staff, tab_family = st.tabs(["Staff", "Family / Emergency Contacts"])

    with tab_staff:
        st.markdown("Map columns from your Staff Information file.")
        detected_staff = _get_detected_cols_for_entity("Staff", config)
        staff_fm: dict[str, Any] = {}

        for field in metadata["Staff"]:
            name = field["name"]
            current = _get_field_value(config, "Staff", name)

            if field["widget"] == "transform":
                val = column_selectbox(
                    field["label"],
                    detected_staff,
                    current,
                    field["help"],
                    key=f"me_st_{name}",
                )
                st.caption(f"ℹ️ {field['help']}")
                staff_fm[name] = {"column": val, "transform": field["transform"]}
            else:
                val = column_selectbox(
                    field["label"],
                    detected_staff,
                    current,
                    field["help"],
                    key=f"me_st_{name}",
                )
                if val:
                    staff_fm[name] = val

    with tab_family:
        st.markdown("Map columns from your Emergency Contact / Parent Information file.")
        detected_family = _get_detected_cols_for_entity("Family", config)
        family_fm: dict[str, Any] = {}

        for field in metadata["Family"]:
            name = field["name"]
            current = _get_field_value(config, "Family", name)
            val = column_selectbox(
                field["label"],
                detected_family,
                current,
                field["help"],
                key=f"me_f_{name}",
            )
            if val:
                family_fm[name] = val

    # Navigation
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("← Back"):
            _go(4)
            st.rerun()
    with col2:
        if st.button("Continue →", type="primary"):
            config.get("mappings", {}).get("Staff", {})["field_map"] = staff_fm
            config.get("mappings", {}).get("Family", {})["field_map"] = family_fm
            _go(6)
            st.rerun()

# ===================================================================
# STEP 6 — Classes and Courses
# ===================================================================
elif st.session_state.me_step == 6:
    st.subheader("Step 6 — Classes and Courses")

    config = st.session_state.me_config
    gc = config.get("global_config", {})
    schedule_file = ""
    for entity_cfg in config.get("mappings", {}).values():
        sf = entity_cfg.get("source_files", {})
        if "student_schedule" in sf:
            schedule_file = sf["student_schedule"]
            break
    detected_sched = st.session_state.me_detected_columns.get(schedule_file, [])

    classes_fm = config.get("mappings", {}).get("Classes", {}).get("field_map", {})
    enroll_fm = config.get("mappings", {}).get("Enrollments", {}).get("field_map", {})

    # --- Class ID ---
    st.markdown("#### Class Identification")
    class_id_raw = classes_fm.get("Class ID", {})
    default_class_id_col = (
        class_id_raw.get("column", "Master Timetable ID") if isinstance(class_id_raw, dict) else "Master Timetable ID"
    )

    class_id_col = column_selectbox(
        "Unique class identifier column",
        detected_sched,
        default_class_id_col,
        "The Master Timetable ID or similar unique class section identifier.",
        key="me_class_id_col",
    )
    append_year = st.checkbox(
        "Append school year to make IDs unique across years",
        value=True,
        key="me_append_year",
    )

    # --- Class Name ---
    st.markdown("---")
    st.markdown("#### Class Name Construction")
    st.caption("Class names are built from multiple columns: e.g. 'Smith - Math 10 (A) 2025'")

    name_config = classes_fm.get("Name", {})
    c1, c2 = st.columns(2)
    with c1:
        teacher_name_col = column_selectbox(
            "Teacher name column",
            detected_sched,
            name_config.get("teacher last name", "Teacher Name"),
            "",
            key="me_class_teacher",
        )
        course_title_col = column_selectbox(
            "Course title column",
            detected_sched,
            name_config.get("course title", "Course Title"),
            "",
            key="me_class_title",
        )
    with c2:
        section_col = column_selectbox(
            "Section letter column",
            detected_sched,
            name_config.get("section letter", "Section Letter"),
            "",
            key="me_class_section",
        )
        primary_teacher_col = column_selectbox(
            "Primary teacher flag column (optional)",
            detected_sched,
            name_config.get("primary teacher flag", "Primary Teacher"),
            "Column where 'Y' marks the primary teacher. Leave blank if not available.",
            key="me_class_primary",
        )

    # --- Class Dates ---
    st.markdown("---")
    st.markdown("#### Class Dates")
    use_academic = getattr(st.session_state, "me_use_academic_year", True)
    fixed_start = getattr(st.session_state, "me_fixed_start", "")
    fixed_end = getattr(st.session_state, "me_fixed_end", "")

    if use_academic:
        st.info("Using academic calendar dates from Step 3.")
    else:
        st.info(f"Using fixed dates: {fixed_start} to {fixed_end}")

    # --- Enrollment IDs ---
    st.markdown("---")
    st.markdown("#### Enrollment Columns")
    st.caption(
        "Enrollments link students and teachers to classes. "
        "Specify which columns identify the student and teacher in the schedule file."
    )

    enroll_user_id = enroll_fm.get("User ID", {})
    default_student_id = (
        enroll_user_id.get("student_id_col", "Student ID") if isinstance(enroll_user_id, dict) else "Student ID"
    )
    default_teacher_id = (
        enroll_user_id.get("staff_id_col", "Teacher ID") if isinstance(enroll_user_id, dict) else "Teacher ID"
    )

    student_id_col = column_selectbox(
        "Student ID column",
        detected_sched,
        default_student_id,
        "Which column identifies the student in the schedule file.",
        key="me_enroll_student",
    )
    teacher_id_col = column_selectbox(
        "Teacher/Staff ID column",
        detected_sched,
        default_teacher_id,
        "Which column identifies the teacher in the schedule file.",
        key="me_enroll_teacher",
    )

    # --- High school course grade (CourseInfo / StudentCourses only) ---
    show_course_grade = bool({"CourseInfo", "StudentCourses"} & set(gc.get("enabled_entities", [])))
    course_start_grade = gc.get("course_start_grade", 10)
    if show_course_grade:
        st.markdown("---")
        st.markdown("#### High School Course Grade")
        st.caption(
            "The CourseInfo and StudentCourses files include senior courses only "
            "(grades 10-12) by default. Lower the start grade to also include grade 8 "
            "or 9 courses. Courses below the selected grade are never included."
        )
        grade_options = [10, 9, 8]
        course_start_grade = st.selectbox(
            "Lowest grade to include in course files",
            options=grade_options,
            index=grade_options.index(course_start_grade) if course_start_grade in grade_options else 0,
            format_func=lambda g: f"Grade {g} and up",
            key="me_course_start_grade",
        )

    # Navigation
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("← Back"):
            _go(5)
            st.rerun()
    with col2:
        if st.button("Continue →", type="primary"):
            # Build Classes field_map
            new_classes_fm: dict[str, Any] = {
                "Class ID": {"column": class_id_col, "append_year_to_id": True} if append_year else class_id_col,
                "Name": {
                    "primary teacher flag": primary_teacher_col,
                    "teacher last name": teacher_name_col,
                    "course title": course_title_col,
                    "section letter": section_col,
                },
                "Grade": "Grade",
                "School ID": "School Number",
            }

            if use_academic:
                new_classes_fm["Start Date"] = {"use_academic_year": True}
                new_classes_fm["End Date"] = {"use_academic_year": True}
            else:
                new_classes_fm["Start Date"] = {"value": fixed_start, "use_academic_year": False}
                new_classes_fm["End Date"] = {"value": fixed_end, "use_academic_year": False}

            config["mappings"]["Classes"]["field_map"] = new_classes_fm

            # Build Enrollments field_map
            new_enroll_fm: dict[str, Any] = {
                "Class ID": {"column": class_id_col, "append_year_to_id": True} if append_year else class_id_col,
                "User ID": {"student_id_col": student_id_col, "staff_id_col": teacher_id_col},
                "Role": {"student_id_col": student_id_col, "staff_id_col": teacher_id_col},
                "School ID": "School Number",
            }
            config["mappings"]["Enrollments"]["field_map"] = new_enroll_fm

            # Persist the high school course grade floor (drives the derived
            # early-grade exclusion for CourseInfo / StudentCourses).
            gc["course_start_grade"] = course_start_grade

            _go(7)
            st.rerun()

# ===================================================================
# STEP 7 — Review & Save
# ===================================================================
elif st.session_state.me_step == 7:
    st.subheader("Step 7 — Review & Save")

    config = st.session_state.me_config
    district_id = st.session_state.get("me_district_id", "new")
    mode = st.session_state.get("me_mode", "create")

    # Summary
    gc = config.get("global_config", {})
    st.markdown(f"**District:** `{district_id}` | **Mode:** {mode}")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Output CSVs**")
        enabled_entities_review = gc.get("enabled_entities") or [
            "Students",
            "Staff",
            "Family",
            "Classes",
            "Enrollments",
        ]
        st.markdown(", ".join(f"`{e}`" for e in enabled_entities_review))

        st.markdown("**Source Files**")
        all_files = _get_all_source_files(config)
        for role, filename in all_files.items():
            label = SOURCE_FILE_ROLES.get(role, {}).get("label", role)
            st.markdown(f"- {label}: `{filename}`")

        st.markdown("**Academic Calendar**")
        st.markdown(f"- Homeroom grades: `{', '.join(gc.get('homeroom_grades', []))}`")
        st.markdown(f"- Start: `{gc.get('academic_start_month_day', '08-25')}`")
        st.markdown(f"- End: `{gc.get('academic_end_month_day', '07-25')}`")
        if {"CourseInfo", "StudentCourses"} & set(enabled_entities_review):
            st.markdown(f"- Course files lowest grade: `Grade {gc.get('course_start_grade', 10)} and up`")

    with col2:
        for entity in enabled_entities_review:
            with st.expander(f"{entity} Field Mapping"):
                fm = config.get("mappings", {}).get(entity, {}).get("field_map", {})
                if not fm:
                    st.markdown("_No field mapping defined._")
                    continue
                for field_name, field_val in fm.items():
                    st.markdown(f"- **{field_name}**: `{field_val}`")

    st.divider()

    # Navigation and save
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("← Back"):
            _go(6)
            st.rerun()
    with col2:
        if st.button("Save Configuration", type="primary"):
            try:
                # Build override dict (only differences from base)
                if mode == "create":
                    base_resolved = _load_resolved_config("myedbc")
                    override = build_override_dict(base_resolved, config)
                else:
                    base_name = district_id + "myedbc" if "myedbc" not in district_id else district_id
                    # For edit mode, diff against the base of the base
                    base_resolved = _load_resolved_config("myedbc")
                    override = build_override_dict(base_resolved, config)

                file_path = save_mapping_yaml(district_id, override)
                st.success(f"Configuration saved to `{file_path}`")

                # Validate by loading it back
                try:
                    config_name = f"{district_id}myedbc"
                    loaded = load_config(config_name)
                    st.success(
                        f"Validation passed — `{config_name}` loads correctly with {len(loaded.mappings)} entities."
                    )
                except Exception as e:
                    st.warning(f"Configuration saved but validation warning: {e}")

            except Exception as e:
                st.error(f"Failed to save: {e}")

    # Preview YAML
    with st.expander("View Generated YAML"):
        if mode == "create":
            base_resolved = _load_resolved_config("myedbc")
            override = build_override_dict(base_resolved, config)
        else:
            base_resolved = _load_resolved_config("myedbc")
            override = build_override_dict(base_resolved, config)

        preview: dict[str, Any] = {"_base": "myedbc", "version": "1.0", "sis": "MyEducationBC"}
        for k in ("global_config", "mappings"):
            if k in override:
                preview[k] = override[k]

        yaml_str = yaml.safe_dump(preview, default_flow_style=False, sort_keys=False, allow_unicode=True)
        st.code(yaml_str, language="yaml")
