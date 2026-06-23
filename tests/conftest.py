"""
Shared fixtures for DistrictSync tests.

All data is synthetic — no real student PII.
Fixtures are designed to exercise every code path in the transformer.
"""

import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.etl.transformer import DataTransformer

# ---------------------------------------------------------------------------
# Streamlit server fixture (used by Playwright UI smoke tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def streamlit_server():
    """Start a headless Streamlit server for UI smoke tests.

    Starts once per test session on port 8502 and tears down afterward.
    Tests that need the server should request the `streamlit_server` fixture
    directly — this avoids starting Streamlit for non-UI test runs.
    """
    try:
        import requests as _requests
    except ImportError:
        pytest.skip("requests library not installed — skipping UI server startup")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "src/ui/Home.py",
            "--server.headless=true",
            "--server.port=8502",
            "--browser.gatherUsageStats=false",
            "--logger.level=error",
            "--global.developmentMode=false",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    base_url = "http://localhost:8502"
    started = False
    for _ in range(60):
        # Bail early if the process already crashed
        if proc.poll() is not None:
            stderr_out = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            pytest.fail(f"Streamlit process exited early (rc={proc.returncode}):\n{stderr_out[:500]}")
        try:
            r = _requests.get(base_url, timeout=2)
            if r.status_code == 200:
                started = True
                break
        except Exception:
            time.sleep(1)

    if not started:
        proc.terminate()
        pytest.fail("Streamlit server did not start within 60 seconds on port 8502")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Transformer instance
# ---------------------------------------------------------------------------


@pytest.fixture
def transformer():
    """Fresh DataTransformer with school year pre-set."""
    t = DataTransformer()
    t.set_school_year(2025, "08-25", "07-25")
    return t


@pytest.fixture
def transformer_bare():
    """DataTransformer with no school year set (for testing determine_school_year)."""
    return DataTransformer()


# ---------------------------------------------------------------------------
# Mapping / config fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_mapping():
    """Load the real myedbc mapping file."""
    mapping_path = Path("config/mappings/myedbc_mapping.yaml")
    with open(mapping_path) as f:
        return yaml.safe_load(f)


@pytest.fixture
def global_config(base_mapping):
    """Global config section from the real mapping."""
    return {
        **base_mapping.get("global_config", {}),
        "mappings": base_mapping.get("mappings", {}),
    }


@pytest.fixture
def students_mapping(base_mapping):
    return base_mapping["mappings"]["Students"]


@pytest.fixture
def staff_mapping(base_mapping):
    return base_mapping["mappings"]["Staff"]


@pytest.fixture
def family_mapping(base_mapping):
    return base_mapping["mappings"]["Family"]


@pytest.fixture
def classes_mapping(base_mapping):
    return base_mapping["mappings"]["Classes"]


@pytest.fixture
def enrollments_mapping(base_mapping):
    return base_mapping["mappings"]["Enrollments"]


@pytest.fixture
def student_attendance_mapping(base_mapping):
    """SD51-shaped StudentAttendance mapping: BOTH bands declared.

    The base now declares NO `source_files` for StudentAttendance — each
    district selects the band(s) it runs by which roles it declares. This
    fixture starts from the base entity (headers + 28-col field_map) and adds
    both `source_files` roles, exactly as SD51 does, so the transformer resolves
    both bands by role. Single-band variants override `source_files` below.
    """
    mapping = dict(base_mapping["mappings"]["StudentAttendance"])
    mapping["source_files"] = {
        "daily_absences": "StudentDailyAbsences.txt",
        "period_absences": "StudentPeriodAbsences.txt",
    }
    return mapping


@pytest.fixture
def student_attendance_daily_only_mapping(student_attendance_mapping):
    """StudentAttendance mapping declaring ONLY the K-7 daily band."""
    mapping = dict(student_attendance_mapping)
    mapping["source_files"] = {"daily_absences": "StudentDailyAbsences.txt"}
    return mapping


@pytest.fixture
def student_attendance_period_only_mapping(student_attendance_mapping):
    """StudentAttendance mapping declaring ONLY the 8-12 period band."""
    mapping = dict(student_attendance_mapping)
    mapping["source_files"] = {"period_absences": "StudentPeriodAbsences.txt"}
    return mapping


@pytest.fixture
def attendance_global_config(base_mapping):
    """global_config carrying the real `attendance.daily` derivation block.

    Mirrors how the pipeline passes global_config to the transformer; the
    StudentAttendance transformer reads `global_config.attendance.daily`.
    """
    return dict(base_mapping.get("global_config", {}))


@pytest.fixture
def student_daily_absences_df():
    """Synthetic Student Daily Absences (K-7) — headers already injected/normalized.

    Columns use the normalized (lowercase) names the extractor produces after
    injecting the headerless file's `headers:` list. NO real PII.
    """
    return pd.DataFrame(
        {
            "school number": ["100", "100", "100", "100", "100"],
            "student number": ["S1", "S2", "S3", "S4", "S5"],
            "absence date": ["18-Sep-2024", "18-Sep-2024", "19-Sep-2024", "20-Sep-2024", "20-Sep-2024"],
            "absent code am": ["A", "A", "T", "A", ""],  # last row blank -> dropped
            "authorized am": ["N", "Y", "N", "N", ""],
            "portion absent": [1.0, 0.5, 1.0, 0.25, 0.0],
        }
    )


@pytest.fixture
def student_period_absences_df():
    """Synthetic Student Period Absences (8-12) — headers injected/normalized.

    Per-period PASS-THROUGH band: one output row per row here, category passed
    through as-is. Columns use the normalized (lowercase) names the extractor
    produces after injecting the headerless `StudentPeriodAbsences.txt` headers.
    Includes a non-accepted category (`OffSite`) that must survive, a blank
    category and a blank student-number row (both dropped), and two identical
    rows (no dedup -> two output rows). NO real PII.
    """
    return pd.DataFrame(
        {
            "school number": ["100", "100", "100", "100", "100", "100", "100"],
            "student number": ["P1", "P1", "P2", "P3", "P4", "", "P5"],
            "absence date": [
                "2024-09-18",  # already ISO -> stays 2024-09-18
                "2024-09-18",  # identical to row 0 -> NOT deduped
                "19-Sep-2024",
                "19-Sep-2024",
                "20-Sep-2024",
                "20-Sep-2024",  # blank student number -> dropped
                "20-Sep-2024",  # blank category -> dropped
            ],
            "absence category": ["A", "A", "L", "OffSite", "AD", "A", ""],
        }
    )


# ---------------------------------------------------------------------------
# Synthetic GDE DataFrames
# ---------------------------------------------------------------------------


@pytest.fixture
def student_demographic_df():
    """Synthetic StudentDemographicInformation with various enrollment states."""
    return pd.DataFrame(
        {
            "student number": ["S001", "S002", "S003", "S004", "S005", "S006", "S007"],
            "legal first name": ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace"],
            "legal surname": ["Smith", "Jones", "Brown", "White", "Green", "Black", "Taylor"],
            "date of birth": [
                "2010-01-15",
                "2009-06-20",
                "2011-03-10",
                "2010-11-05",
                "2008-09-22",
                "2012-04-18",
                "2010-07-30",
            ],
            "grade": ["K", "3", "7", "10", "12", "1", "5"],
            "school number": ["100", "100", "100", "200", "200", "100", "100"],
            "homeroom": ["A1", "A1", "B2", "C3", "C4", "A1", "B2"],
            "previous school number": ["", "99", "", "150", "", "", ""],
            "usual first name": ["Ali", "", "Chuck", "", "Evie", "", "Gracie"],
            "usual surname": ["", "", "", "", "", "", ""],
            "student email address": [
                "alice@test.ca",
                "bob@test.ca",
                "",
                "diana@test.ca",
                "eve@test.ca",
                "frank@test.ca",
                "grace@test.ca",
            ],
            "enrolment status": ["Active", "Active", "Active", "Active", "Active", "Active", "PreReg"],
            "teacher name": ["Ms. Harper", "Ms. Harper", "Mr. Reed", "Mrs. Liu", "Mr. Singh", "Ms. Harper", "Mr. Reed"],
            "teacher id": ["T001", "T001", "T002", "T003", "T004", "T001", "T002"],
        }
    )


@pytest.fixture
def student_demographic_with_withdraw_df():
    """Students with withdraw dates instead of enrollment status."""
    return pd.DataFrame(
        {
            "student number": ["S001", "S002", "S003", "S004", "S005"],
            "legal first name": ["Alice", "Bob", "Charlie", "Diana", "Eve"],
            "legal surname": ["Smith", "Jones", "Brown", "White", "Green"],
            "date of birth": ["2010-01-15", "2009-06-20", "2011-03-10", "2010-11-05", "2008-09-22"],
            "grade": ["5", "8", "10", "12", "3"],
            "school number": ["100", "100", "200", "200", "100"],
            "homeroom": ["A1", "B2", "C3", "C4", "A1"],
            "previous school number": ["", "", "", "", ""],
            "usual first name": ["", "", "", "", ""],
            "usual surname": ["", "", "", "", ""],
            "student email address": ["", "", "", "", ""],
            "withdraw date": [
                "",  # No date → Active
                "15-Jan-2020",  # Past → Inactive (%d-%b-%Y)
                "2099-12-31",  # Future → Active (%Y-%m-%d)
                "01/01/2020",  # Past → Inactive (%m/%d/%Y)
                "BADDATE",  # Unparseable → Inactive
            ],
            "teacher name": ["Ms. Harper", "Mr. Reed", "Mrs. Liu", "Mr. Singh", "Ms. Harper"],
            "teacher id": ["T001", "T002", "T003", "T004", "T001"],
        }
    )


@pytest.fixture
def student_schedule_df():
    """Synthetic StudentSchedule with mix of homeroom and non-homeroom grades."""
    return pd.DataFrame(
        {
            "student number": ["S001", "S002", "S003", "S004", "S005", "S006", "S007", "S003", "S004"],
            "student id": ["S001", "S002", "S003", "S004", "S005", "S006", "S007", "S003", "S004"],
            "school number": ["100", "100", "100", "200", "200", "100", "100", "100", "200"],
            "school year": [
                "2025/2026",
                "2025/2026",
                "2025/2026",
                "2025/2026",
                "2025/2026",
                "2025/2026",
                "2025/2026",
                "2025/2026",
                "2025/2026",
            ],
            "grade": ["K", "3", "7", "10", "12", "1", "5", "7", "10"],
            "master timetable id": ["MT001", "MT002", "MT003", "MT004", "MT005", "MT006", "MT007", "MT008", "MT009"],
            "teacher id": ["T001", "T001", "T002", "T003", "T004", "T001", "T002", "T002", "T003"],
            "section letter": ["A", "A", "B", "A", "A", "A", "B", "A", "B"],
            "district course code": ["HR-K", "HR-3", "SCI07", "MAT10", "ENG12", "HR-1", "HR-5", "ENG07", "SCI10"],
            "primary teacher": ["Y", "Y", "Y", "Y", "Y", "Y", "Y", "Y", "Y"],
            "teacher name": ["Harper", "Harper", "Reed", "Liu", "Singh", "Harper", "Reed", "Reed", "Liu"],
        }
    )


@pytest.fixture
def staff_info_df():
    """Synthetic StaffInformationEnhanced."""
    return pd.DataFrame(
        {
            "teacher id": ["T001", "T002", "T003", "T004", "T005"],
            "first name": ["Jane", "Mark", "Linda", "Raj", "Sara"],
            "last name": ["Harper", "Reed", "Liu", "Singh", "Chen"],
            "email address": [
                "harper@school.ca",
                "reed@school.ca",
                "liu@school.ca",
                "singh@school.ca",
                "chen@school.ca",
            ],
            "teaching staff": ["Y", "Y", "Y", "Y", "N"],
            "school number": ["100", "100", "200", "200", "100"],
        }
    )


@pytest.fixture
def course_info_df():
    """Synthetic CourseInformation."""
    return pd.DataFrame(
        {
            "school number": ["100", "100", "200", "200", "100", "100"],
            "course code": ["SCI07", "ENG07", "MAT10", "ENG12", "SCI10", "HR-K"],
            "title": ["Science 7", "English 7", "Math 10", "English 12", "Science 10", "Homeroom K"],
        }
    )


@pytest.fixture
def emergency_contact_df():
    """Synthetic EmergencyContactInformation."""
    return pd.DataFrame(
        {
            "student number": ["S001", "S001", "S002", "S003", "S004"],
            "first name": ["John", "Mary", "Robert", "Susan", "James"],
            "last name": ["Smith", "Smith", "Jones", "Brown", "White"],
            "email address": ["john@mail.com", "mary@mail.com", "robert@mail.com", "susan@mail.com", "james@mail.com"],
        }
    )


@pytest.fixture
def class_info_enh_df():
    """Synthetic ClassInformationEnh for blended class detection.

    Creates a scenario where teacher T010 teaches two different grade levels
    at the same time slot (term=1, semester=1, day=1, period=1) — this should
    be detected as a blended class.
    """
    return pd.DataFrame(
        {
            "school number": ["300", "300", "300", "300"],
            "teacher id": ["T010", "T010", "T010", "T020"],
            "master timetable id": ["MT100", "MT101", "MT102", "MT103"],
            "course code": ["ENG01", "ENG02", "SCI03", "MAT10"],
            "term": ["1", "1", "1", "1"],
            "semester": ["1", "1", "1", "1"],
            "day": ["1", "1", "1", "2"],
            "period": ["1", "1", "1", "1"],
        }
    )


@pytest.fixture
def blended_schedule_df():
    """Student schedule that matches the blended class_info_enh scenario."""
    return pd.DataFrame(
        {
            "student number": ["S100", "S101", "S102", "S103"],
            "student id": ["S100", "S101", "S102", "S103"],
            "school number": ["300", "300", "300", "300"],
            "school year": ["2025/2026", "2025/2026", "2025/2026", "2025/2026"],
            "grade": ["1", "2", "3", "10"],
            "master timetable id": ["MT100", "MT101", "MT102", "MT103"],
            "teacher id": ["T010", "T010", "T010", "T020"],
            "section letter": ["A", "A", "A", "B"],
            "district course code": ["ENG01", "ENG02", "SCI03", "MAT10"],
            "primary teacher": ["Y", "Y", "Y", "Y"],
            "teacher name": ["Adams", "Adams", "Adams", "Baker"],
        }
    )


@pytest.fixture
def blended_course_info_df():
    """Course info for the blended class scenario."""
    return pd.DataFrame(
        {
            "school number": ["300", "300", "300", "300"],
            "course code": ["ENG01", "ENG02", "SCI03", "MAT10"],
            "title": ["English 1", "English 2", "Science 3", "Math 10"],
        }
    )


@pytest.fixture
def blended_staff_df():
    """Staff info for the blended class scenario."""
    return pd.DataFrame(
        {
            "teacher id": ["T010", "T020"],
            "first name": ["Adam", "Betty"],
            "last name": ["Adams", "Baker"],
            "email address": ["adams@school.ca", "baker@school.ca"],
            "teaching staff": ["Y", "Y"],
            "school number": ["300", "300"],
        }
    )


# ---------------------------------------------------------------------------
# Assembled raw_data dicts (what the extractor would produce)
# ---------------------------------------------------------------------------


@pytest.fixture
def raw_data(student_demographic_df, student_schedule_df, staff_info_df, course_info_df, emergency_contact_df):
    """Standard raw_data dict with all source files loaded."""
    return {
        "StudentDemographicInformation.txt": student_demographic_df,
        "StudentSchedule.txt": student_schedule_df,
        "StaffInformationEnhanced.txt": staff_info_df,
        "CourseInformation.txt": course_info_df,
        "EmergencyContactInformation.txt": emergency_contact_df,
        "ClassInformationEnh.txt": pd.DataFrame(),  # No class info by default
    }


@pytest.fixture
def raw_data_with_blended(
    student_demographic_df,
    blended_schedule_df,
    blended_staff_df,
    blended_course_info_df,
    emergency_contact_df,
    class_info_enh_df,
):
    """Raw data dict set up for blended class detection."""
    return {
        "StudentDemographicInformation.txt": student_demographic_df,
        "StudentSchedule.txt": blended_schedule_df,
        "StaffInformationEnhanced.txt": blended_staff_df,
        "CourseInformation.txt": blended_course_info_df,
        "EmergencyContactInformation.txt": emergency_contact_df,
        "ClassInformationEnh.txt": class_info_enh_df,
    }
