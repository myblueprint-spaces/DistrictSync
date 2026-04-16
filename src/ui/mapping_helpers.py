"""Helpers for the Mapping Editor wizard.

Column detection, field metadata, override-diff YAML generation,
and reusable Streamlit widgets for mapping configuration.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml

# ---------------------------------------------------------------------------
# Column detection from uploaded sample files
# ---------------------------------------------------------------------------


def detect_columns(file_bytes: bytes, filename: str) -> tuple[list[str], bool]:
    """Read a GDE file and return (column_names, is_headerless).

    Uses the same multi-encoding fallback as DataExtractor.
    Headerless heuristic: first-row values are all numeric or date-like.
    """
    text = None
    for encoding in ("utf-8", "latin1", "cp1252"):
        try:
            text = file_bytes.decode(encoding)
            break
        except (UnicodeDecodeError, AttributeError):
            continue
    if text is None:
        text = file_bytes.decode("utf-8", errors="replace")

    first_line = text.split("\n")[0].strip()
    delimiter = "\t" if "\t" in first_line else ","

    # Parse first row
    df = pd.read_csv(io.StringIO(text), sep=delimiter, nrows=0)
    col_names = [c.strip() for c in df.columns]

    # Heuristic: if all column "names" look like data (numeric, dates, quoted numbers)
    is_headerless = _looks_like_data(col_names)

    if is_headerless:
        # Re-read with no header to count columns
        df_full = pd.read_csv(io.StringIO(text), sep=delimiter, header=None, nrows=1)
        col_names = [f"Column {i + 1}" for i in range(len(df_full.columns))]

    return col_names, is_headerless


def _looks_like_data(values: list[str]) -> bool:
    """Return True if the values look like data rather than column headers."""
    if not values:
        return False
    numeric_or_date = 0
    for v in values:
        v = v.strip().strip('"')
        if re.match(r"^\d+(\.\d+)?$", v) or re.match(r"^\d{2,4}[-/]\w{2,3}[-/]\d{2,4}$", v):
            numeric_or_date += 1
    return numeric_or_date >= len(values) * 0.7


# ---------------------------------------------------------------------------
# Field metadata — drives the wizard UI
# ---------------------------------------------------------------------------

CEDS_GRADES = [
    "IT",
    "PR",
    "PK",
    "TK",
    "KG",
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "11",
    "12",
    "13",
]

# Source file roles with plain-language descriptions
SOURCE_FILE_ROLES = {
    "student_demographic": {
        "label": "Student Information",
        "help": "Student names, grades, school numbers, enrollment status, contact info",
        "entities": ["Students", "Classes", "Enrollments"],
    },
    "staff_info": {
        "label": "Staff Information",
        "help": "Teacher/staff names, email addresses, school assignments",
        "entities": ["Staff", "Classes"],
    },
    "emergency_contacts": {
        "label": "Emergency Contacts",
        "help": "Parent/guardian names, phone numbers, email addresses",
        "entities": ["Family"],
    },
    "student_schedule": {
        "label": "Student Schedule",
        "help": "Which students are enrolled in which courses, teacher assignments",
        "entities": ["Classes", "Enrollments"],
    },
    "course_info": {
        "label": "Course Information",
        "help": "Course codes, titles, descriptions, credit values",
        "entities": ["Classes"],
    },
    "class_info": {
        "label": "Class Information (Enhanced)",
        "help": "Class sections with teacher, period, and schedule details. Used for blended class detection.",
        "entities": ["Classes"],
    },
}


def get_field_metadata() -> dict[str, list[dict[str, Any]]]:
    """Return plain-language field definitions for each entity.

    Each field has: name, label, help, widget, transform (optional), required.
    """
    return {
        "Students": [
            {
                "name": "User ID",
                "label": "Student's unique ID",
                "help": "Typically the Student Number column.",
                "widget": "column_select",
                "required": True,
            },
            {
                "name": "Student Number",
                "label": "Student number",
                "help": "The student's official number in your SIS.",
                "widget": "column_select",
                "required": True,
            },
            {
                "name": "First Name",
                "label": "Student's first name",
                "help": "Legal or preferred first name.",
                "widget": "column_select",
                "required": True,
            },
            {
                "name": "Last Name",
                "label": "Student's last name",
                "help": "Legal or preferred last name.",
                "widget": "column_select",
                "required": True,
            },
            {
                "name": "Date of Birth",
                "label": "Date of birth",
                "help": "Student's date of birth.",
                "widget": "column_select",
                "required": True,
            },
            {
                "name": "Grade",
                "label": "Grade level",
                "help": "Grades are automatically converted to standard CEDS format (K becomes KG, 1 becomes 01, etc.).",
                "widget": "transform",
                "transform": "grade_to_ceds",
                "required": True,
            },
            {
                "name": "EnrollStatus",
                "label": "Enrollment status",
                "help": "Auto-detected from the enrollment status column or withdrawal date. No configuration needed.",
                "widget": "auto",
                "required": False,
            },
            {
                "name": "SchoolCode",
                "label": "School number/code",
                "help": "The school this student attends.",
                "widget": "column_select",
                "required": True,
            },
            {
                "name": "Homeroom",
                "label": "Homeroom",
                "help": "The student's homeroom assignment (used for homeroom class generation).",
                "widget": "column_select",
                "required": False,
            },
            {
                "name": "PreRegSchoolCode",
                "label": "Previous school number",
                "help": "The school the student transferred from (if applicable).",
                "widget": "column_select",
                "required": False,
            },
            {
                "name": "Preferred First Name",
                "label": "Preferred/usual first name",
                "help": "If different from legal name. Leave blank to skip.",
                "widget": "column_select",
                "required": False,
            },
            {
                "name": "Preferred Last Name",
                "label": "Preferred/usual last name",
                "help": "If different from legal name. Leave blank to skip.",
                "widget": "column_select",
                "required": False,
            },
            {
                "name": "Community Hours",
                "label": "Community service hours",
                "help": "Leave blank if not tracked.",
                "widget": "fixed_or_column",
                "required": False,
            },
            {
                "name": "Literacy Test Completed",
                "label": "Literacy test completion",
                "help": "Leave blank if not tracked.",
                "widget": "fixed_or_column",
                "required": False,
            },
            {
                "name": "Email Address",
                "label": "Student email address",
                "help": "Choose how to determine student emails.",
                "widget": "email_choice",
                "required": False,
            },
        ],
        "Staff": [
            {
                "name": "User ID",
                "label": "Staff member's unique ID",
                "help": "Typically the Teacher ID column.",
                "widget": "column_select",
                "required": True,
            },
            {
                "name": "First Name",
                "label": "Staff first name",
                "help": "",
                "widget": "column_select",
                "required": True,
            },
            {"name": "Last Name", "label": "Staff last name", "help": "", "widget": "column_select", "required": True},
            {"name": "Email", "label": "Staff email address", "help": "", "widget": "column_select", "required": True},
            {
                "name": "Role",
                "label": "Teaching staff indicator",
                "help": "Column where 'Y' means teacher, anything else means administrator.",
                "widget": "transform",
                "transform": "map_role",
                "required": True,
            },
            {
                "name": "School ID",
                "label": "Staff member's school number",
                "help": "",
                "widget": "column_select",
                "required": True,
            },
        ],
        "Family": [
            {
                "name": "First Name",
                "label": "Contact's first name",
                "help": "",
                "widget": "column_select",
                "required": True,
            },
            {
                "name": "Last Name",
                "label": "Contact's last name",
                "help": "",
                "widget": "column_select",
                "required": True,
            },
            {
                "name": "Email",
                "label": "Contact's email address",
                "help": "",
                "widget": "column_select",
                "required": False,
            },
            {
                "name": "Student User ID",
                "label": "Student number (links contact to student)",
                "help": "",
                "widget": "column_select",
                "required": True,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Override diff — produce minimal YAML with _base inheritance
# ---------------------------------------------------------------------------


def build_override_dict(base_resolved: dict, user_config: dict) -> dict:
    """Compare user_config against base_resolved, return only differences.

    The result is suitable for saving as a district override YAML with _base.
    """
    override: dict[str, Any] = {}

    # Compare global_config
    base_gc = base_resolved.get("global_config", {})
    user_gc = user_config.get("global_config", {})
    gc_diff = _diff_dict(base_gc, user_gc)
    if gc_diff:
        override["global_config"] = gc_diff

    # Compare mappings
    base_mappings = base_resolved.get("mappings", {})
    user_mappings = user_config.get("mappings", {})
    mappings_diff: dict[str, Any] = {}

    for entity_name in user_mappings:
        base_entity = base_mappings.get(entity_name, {})
        user_entity = user_mappings[entity_name]
        entity_diff = _diff_dict(base_entity, user_entity)
        if entity_diff:
            mappings_diff[entity_name] = entity_diff

    if mappings_diff:
        override["mappings"] = mappings_diff

    return override


def _diff_dict(base: dict, user: dict) -> dict:
    """Recursively diff two dicts, returning only keys that differ in user."""
    diff: dict[str, Any] = {}
    for key, user_val in user.items():
        base_val = base.get(key)
        if isinstance(user_val, dict) and isinstance(base_val, dict):
            sub_diff = _diff_dict(base_val, user_val)
            if sub_diff:
                diff[key] = sub_diff
        elif isinstance(user_val, list) and isinstance(base_val, list):
            if user_val != base_val:
                diff[key] = user_val
        elif user_val != base_val:
            diff[key] = user_val
    # Include keys in user that aren't in base at all
    for key in user:
        if key not in base:
            diff[key] = user[key]
    return diff


# ---------------------------------------------------------------------------
# YAML save
# ---------------------------------------------------------------------------


def save_mapping_yaml(district_id: str, override_dict: dict, base_name: str = "myedbc") -> Path:
    """Write a district mapping YAML file with _base inheritance.

    Returns the path to the saved file.
    """
    output: dict[str, Any] = {}
    output["_base"] = base_name
    output["version"] = override_dict.pop("version", "1.0")
    output["sis"] = override_dict.pop("sis", "MyEducationBC")
    if "district_name" in override_dict:
        output["district_name"] = override_dict.pop("district_name")

    # Add remaining overrides
    for key in ("global_config", "mappings"):
        if key in override_dict:
            output[key] = override_dict[key]

    # User-created mappings live in a persistent per-user directory so
    # they survive exe restarts (the bundled config/mappings dir is a
    # read-only temp extraction in the frozen exe).
    from src.utils.paths import user_mappings_dir

    config_dir = user_mappings_dir()
    file_path = config_dir / f"{district_id}myedbc_mapping.yaml"

    # Build YAML with comment header
    header = f"# {district_id.upper()} - District mapping (auto-generated by Mapping Editor)\n"
    header += f"# Inherits from: {base_name}\n"
    yaml_str = yaml.safe_dump(output, default_flow_style=False, sort_keys=False, allow_unicode=True)

    file_path.write_text(header + yaml_str, encoding="utf-8")
    return file_path


# ---------------------------------------------------------------------------
# Reusable Streamlit widgets
# ---------------------------------------------------------------------------


def column_selectbox(
    label: str,
    detected_cols: list[str],
    default: str,
    help_text: str = "",
    key: str = "",
) -> str:
    """Selectbox populated from detected columns, with text_input fallback."""
    if detected_cols:
        # Add current default if not in detected list
        options = list(detected_cols)
        if default and default not in [c.lower() for c in options]:
            options.insert(0, default)
        # Find default index (case-insensitive)
        default_idx = 0
        for i, opt in enumerate(options):
            if opt.lower() == default.lower():
                default_idx = i
                break
        return st.selectbox(label, options=options, index=default_idx, help=help_text, key=key)
    else:
        return st.text_input(label, value=default, help=help_text, key=key)
