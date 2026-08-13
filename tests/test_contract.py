"""Output schema contract tests — the mechanical enforcement of the published
output contract (``docs/developer/output-contract.md``).

Verifies that the pipeline output for **every** bundled config produces exactly
the entity CSVs that config's run is expected to emit, each with exactly the
contract's columns **in exactly the contract's order**, encoded per the
per-entity BOM rule. A failure here is a partner-visible change that MAY be
rejected by the importer or silently mis-read.

The contract's *data* — :data:`OUTPUT_SCHEMA`, :data:`EXPECTED_ENTITIES`,
:data:`NO_BOM_ENTITIES` — lives in the neutral ``tests/contract_schema.py`` so
the unit and e2e modules can share it without importing this one (which pulls in
the conftest-registered ``ci_flet_pack_smoke`` and an 11-config pipeline sweep).
``tests/test_output_contract_doc.py`` gates that data against the published doc.

Three things are pinned here, and each is a *partner-visible* contract:

1. **Column ORDER equality** (not membership) — ``list(df.columns) == expected``
   per emitted entity. Emitted columns are treated as POSITIONAL:
   order-sensitivity is CONFIRMED for ``StudentAttendance`` (see
   ``src/etl/transformers/student_attendance.py`` — "exact case-sensitive order")
   and is NOT yet confirmed with the partner for the rostering / course feeds, so
   we pin the emitted order and require importer re-confirmation before changing
   it. The published doc is the authority for both the column set and its order;
   ``OUTPUT_SCHEMA`` is its in-test mirror.
2. **The expected-entity table** (:data:`EXPECTED_ENTITIES`) — the single source
   for "which CSVs should this config's contract run have produced". It is NOT
   derivable from config alone: it is ``active_entities ∩ entities whose sources
   this fixture supplies``. ``sd51myedbc`` actively enables StudentAttendance yet
   its fixture deliberately supplies no absence files (the skip-on-empty pin), so
   its expected set is the 5 rostering entities. Its VALUES are guarded against
   erosion in both directions by
   ``test_expected_entities_track_active_entities`` + :data:`DELIBERATELY_UNCOVERED`.
3. **The on-disk encoding** — the rostering/course CSVs carry the Excel BOM;
   ``StudentAttendance`` must not (``DataLoader.csv_encoding`` is the code SSOT;
   :data:`NO_BOM_ENTITIES` is the contract's own statement of the same rule, and
   a policy test pins the two together).

Parametrized over ALL 12 bundled configs:

* the 7 SpacesEDU rostering configs — myedbc (base), sd40myedbc (CSV files +
  headerless schedule + ATT--* exclusions), sd48myedbc, sd51myedbc (plain
  inheritance + generated emails), sd54myedbc (renamed source files,
  withdraw-date-only active detection, surname.firstname emails), sd60myedbc
  (Family row_filters, cross-enrollment collapse, sanitized learn60 emails with
  derived admission-year, Home-school rostering), sd74myedbc;
* sd51attendance — StudentAttendance ONLY, from the two HEADERLESS absence GDEs;
* sd83myedbc — standard MyEd BC file naming (same shape as myedbc/mbp_all), all
  7 entities enabled; REUSES ``_create_mbp_all_inputs`` since the input shape is
  identical (its overrides — extended homeroom grades, a lower course-grade
  floor, blanked Date of Birth — are business-logic differences, not fixture
  ones);
* the 3 myBlueprint+ tiers — mbp_all (all 7), mbp_core (Students + the two course
  CSVs), mbponly (the two course CSVs only, reusing the committed
  ``tests/snapshots/mbp_input/`` fixtures its own e2e test owns).

Fixture provenance differs by builder, and the difference matters:

* the **seven district builders** mirror that district's REAL GDE header shape —
  column names verified against the district extracts — with fully synthetic
  rows, so the per-district mapping quirks are exercised end-to-end;
* the **two sd51attendance builders** are authored from the YAML ``headers:``
  blocks in ``myedbc_mapping.yaml`` (the injected file-order column lists), NOT
  from a district extract;
* the **two course-GDE builders** are a READ-SUBSET authored from the
  StudentCourses field_map / ``source_columns`` roles — an 8-column subset whose
  order deliberately differs from the 17/16-column committed
  ``tests/snapshots/mbp_input/`` fixtures (source column ORDER is irrelevant: the
  extractor normalizes to a name-keyed frame);
* ``mbponly`` reuses those committed fixtures unchanged.

``TestDistrictQuirks`` pins the quirk behaviours per district via indirect
parametrization (reusing the module-scoped pipeline run for that district).
"""

import shutil
from pathlib import Path

import ci_flet_pack_smoke as smoke  # loaded + registered by tests/conftest.py
import pandas as pd
import pytest

from src.etl.loader import DataLoader
from src.main import main

# The contract's DATA lives in the neutral shared module (see its docstring for
# why the dependency runs this way and not the reverse). Re-bound here as
# module-level names so `test_contract.OUTPUT_SCHEMA` / `.EXPECTED_ENTITIES`
# stay the documented handles — the same objects, one definition.
from tests.contract_schema import (
    EXPECTED_ENTITIES,
    NO_BOM_ENTITIES,
    ORDER_AUTHORITY,
    OUTPUT_SCHEMA,
)
from tests.test_pipeline_e2e_mbponly import FIXTURE_DIR as MBP_GDE_DIR

UTF8_BOM = b"\xef\xbb\xbf"

#: The ONLY entities a config actively enables that this module's fixtures
#: deliberately do NOT supply sources for — i.e. the only sanctioned gap between
#: ``active_entities()`` and :data:`EXPECTED_ENTITIES`, across all 11 bundled
#: configs. sd51myedbc's absence GDEs are withheld ON PURPOSE so the run pins
#: skip-on-empty (a missing attendance drop must never block rostering).
#:
#: Every gap must be declared HERE, not silently absorbed into a frozenset:
#: shrinking an ``EXPECTED_ENTITIES`` value to make a red test green now fails
#: ``test_expected_entities_track_active_entities``, and a future config that
#: enables an entity whose sources no fixture supplies fails it too.
DELIBERATELY_UNCOVERED: dict[str, frozenset[str]] = {
    "sd51myedbc": frozenset({"StudentAttendance"}),
}

VALID_STAFF_ROLES = {"teacher", "administrator"}
VALID_ENROLLMENT_ROLES = {"student", "teacher"}

# ---------------------------------------------------------------------------
# GDE data builders (shared across all district fixtures)
# ---------------------------------------------------------------------------


def _write_student_demographic(path: Path, filename: str) -> None:
    pd.DataFrame(
        {
            "Student Number": ["S001", "S002", "S003"],
            "Legal First Name": ["Alice", "Bob", "Charlie"],
            "Legal Surname": ["Smith", "Jones", "Brown"],
            "Date of birth": ["2010-01-15", "2009-06-20", "2011-03-10"],
            "Grade": ["3", "10", "12"],
            "School Number": ["100", "200", "200"],
            "Homeroom": ["A1", "C3", "C4"],
            "Previous school number": ["", "", ""],
            "Usual First Name": ["", "", ""],
            "Usual surname": ["", "", ""],
            "Student email address": ["alice@test.ca", "bob@test.ca", "charlie@test.ca"],
            "Enrolment Status": ["Active", "Active", "Active"],
            "Teacher Name": ["Ms. Harper", "Mrs. Liu", "Mr. Singh"],
            "Teacher ID": ["T001", "T003", "T004"],
        }
    ).to_csv(path / filename, index=False)


def _write_staff(path: Path, filename: str) -> None:
    pd.DataFrame(
        {
            "Teacher ID": ["T001", "T003", "T004"],
            "First Name": ["Jane", "Linda", "Raj"],
            "Last Name": ["Harper", "Liu", "Singh"],
            "Email Address": ["harper@school.ca", "liu@school.ca", "singh@school.ca"],
            "Teaching Staff": ["Y", "Y", "Y"],
            "School Number": ["100", "200", "200"],
        }
    ).to_csv(path / filename, index=False)


#: The one synthetic course catalog every fixture shares. The rostering districts
#: only need (School Number, Course Code, Title) for the Classes title join; the
#: myBlueprint+ tiers additionally read Grade Level + Credit Value (the two
#: columns CourseInfo's field_map sources), so the same rows carry them and the
#: two shapes can never drift apart.
_COURSE_CATALOG: list[dict[str, str]] = [
    {"School Number": "100", "Course Code": "HR-3", "Title": "Homeroom 3", "Grade Level": "03", "Credit Value": "0"},
    {"School Number": "200", "Course Code": "MAT10", "Title": "Math 10", "Grade Level": "10", "Credit Value": "4"},
    {"School Number": "200", "Course Code": "ENG12", "Title": "English 12", "Grade Level": "12", "Credit Value": "4"},
]
_ROSTERING_COURSE_COLUMNS = ["School Number", "Course Code", "Title"]


def _write_course_info(path: Path, filename: str = "CourseInformation.txt", *, catalog: bool = False) -> None:
    """Write CourseInformation.txt.

    ``catalog=True`` adds the two columns the CourseInfo entity's field_map
    sources (Grade Level -> "Grade", Credit Value -> "Credit Value"); only the
    myBlueprint+ tiers enable that entity, so the rostering fixtures stay on the
    3-column rostering shape.
    """
    df = pd.DataFrame(_COURSE_CATALOG)
    if not catalog:
        df = df[_ROSTERING_COURSE_COLUMNS]
    df.to_csv(path / filename, index=False)


def _write_base_schedule(path: Path, filename: str, section_col: str = "Section Letter") -> None:
    """The canonical MyEd BC schedule shape shared by the base-like districts."""
    pd.DataFrame(
        {
            "Student Number": ["S001", "S002", "S003"],
            "Student ID": ["S001", "S002", "S003"],
            "School Number": ["100", "200", "200"],
            "School Year": ["2025/2026", "2025/2026", "2025/2026"],
            "Grade": ["3", "10", "12"],
            "Master Timetable ID": ["MT001", "MT002", "MT003"],
            "Teacher ID": ["T001", "T003", "T004"],
            section_col: ["A", "A", "A"],
            "District Course Code": ["HR-3", "MAT10", "ENG12"],
            "Primary Teacher": ["Y", "Y", "Y"],
            "Teacher Name": ["Harper", "Liu", "Singh"],
        }
    ).to_csv(path / filename, index=False)


def _write_family(path: Path, filename: str, last_name_col: str = "Last Name") -> None:
    pd.DataFrame(
        {
            "Student Number": ["S001"],
            "First Name": ["John"],
            last_name_col: ["Smith"],
            "Email Address": ["john@mail.com"],
        }
    ).to_csv(path / filename, index=False)


def _write_class_info_empty(path: Path, filename: str) -> None:
    pd.DataFrame(
        columns=["School Number", "Teacher ID", "Master Timetable ID", "Term", "Semester", "Day", "Period"]
    ).to_csv(path / filename, index=False)


# ---------------------------------------------------------------------------
# myBlueprint+ course GDEs (CourseInfo + StudentCourses)
#
# Shaped like tests/snapshots/mbp_input/ (the committed mbponly fixtures) but
# keyed to THIS module's synthetic population (S001-S003 at schools 100/200), so
# the roster and the transcripts describe one coherent district. That matters:
# StudentCourses applies the zero-orphan filter against `Students.csv`, so
# borrowing the mbponly fixtures' 4000001/4000002 students would filter the frame
# to empty and mbp_all/mbp_core would emit no StudentCourses.csv at all.
# ---------------------------------------------------------------------------


def _write_course_history(path: Path, filename: str = "StudentCourseHistory.txt") -> None:
    """Two completed history rows: one pass (75) and one fail (45).

    ``Full Course Code`` ends with ``-<Section>`` so the section-stripping layer
    is exercised end-to-end; the fail row leaves the retake door open for the
    matching selection row below. This module asserts only that the entity is
    emitted with the contract's columns (row-level behaviour pinned in
    tests/test_transform_student_courses.py).
    """
    pd.DataFrame(
        {
            "School Number": ["200", "200"],
            "Student Number": ["S002", "S003"],
            "Course Code": ["MAT10", "ENG12"],
            "Full Course Code": ["MAT10-S1 - A", "ENG12-S1 - A"],
            "Section": ["S1 - A", "S1 - A"],
            "Final Mark": ["75", "45"],
            "DL Start Date": ["05-Sep-2024", "05-Sep-2024"],
            "DL Completion Date": ["30-Jan-2025", "30-Jan-2025"],
        }
    ).to_csv(path / filename, index=False)


def _write_course_selection(path: Path, filename: str = "StudentCourseSelection.txt") -> None:
    """Two selection rows exercising BOTH selection-pass branches.

    S002/MAT10 was already passed -> excluded. S003/ENG12 was failed and this
    selection starts LATER than the history row -> a retake, included. This
    module asserts only that the entity is emitted with the contract's columns
    (row-level behaviour pinned in tests/test_transform_student_courses.py).
    """
    pd.DataFrame(
        {
            "School Year": ["2025/2026", "2025/2026"],
            "School Number": ["200", "200"],
            "Student Number": ["S002", "S003"],
            "Course Code": ["MAT10", "ENG12"],
            "Section": ["S1", "S1"],
            "Master Timetable ID": ["MT002", "MT003"],
            "Teacher ID": ["T003", "T004"],
            "DL Start Date": ["05-Sep-2025", "05-Sep-2025"],
        }
    ).to_csv(path / filename, index=False)


# ---------------------------------------------------------------------------
# StudentAttendance GDEs — BOTH files are HEADERLESS
#
# Column names are injected at extract time from the `headers:` blocks in
# myedbc_mapping.yaml, so these fixtures prove that injection end-to-end (the
# same shape the SD40 headerless schedule fixture uses: dict keys document the
# file-order positions, `header=False` omits them on disk). The full MyEd
# Data-Elements column lists are written even though only a handful are read —
# a short row would silently shift every injected name.
# ---------------------------------------------------------------------------


def _write_daily_absences(path: Path, filename: str = "StudentDailyAbsences.txt") -> None:
    """K-7 Student Daily Absences (18 columns) — DERIVED category + row multiplicity.

    S001: (A, N) at a full-day portion -> category "A", TWO half-day rows.
    S002: (T, Y) at a half-day portion -> category "L-E", ONE row. This module
    asserts only that the entity is emitted with the contract's columns and
    encoding (row-level behaviour pinned in
    tests/test_transform_student_attendance.py).
    """
    pd.DataFrame(
        {
            "School Number": ["100", "100"],
            "Student Number": ["S001", "S002"],
            "Student Legal Last Name": ["Smith", "Jones"],
            "Student Legal First Name": ["Alice", "Bob"],
            "Grade": ["03", "05"],
            "Homeroom": ["A1", "A2"],
            "Teacher Name": ["Harper", "Harper"],
            "Absence Date": ["18-Sep-2024", "19-Sep-2024"],
            "Reason Code AM": ["", ""],
            "Sub Allocation Code AM": ["", ""],
            "Authorized AM": ["N", "Y"],
            "Reason Code PM": ["", ""],
            "Sub Allocation Code PM": ["", ""],
            "Authorized PM": ["", ""],
            "Absent Code AM": ["A", "T"],
            "Absent Code PM": ["", ""],
            "Teacher ID": ["T001", "T001"],
            "Portion Absent": [1.0, 0.5],
        }
    ).to_csv(path / filename, index=False, header=False)


def _write_period_absences(path: Path, filename: str = "StudentPeriodAbsences.txt") -> None:
    """8-12 Student Period Absences (17 columns) — PER-PERIOD PASS-THROUGH.

    One output row per input row, category passed through as-is (including the
    non-accepted "OffSite", which SpacesEDU ignores rather than rejects).
    """
    pd.DataFrame(
        {
            "School Number": ["200", "200"],
            "Student Number": ["S003", "S003"],
            "Student Legal Last Name": ["Brown", "Brown"],
            "Student Legal First Name": ["Charlie", "Charlie"],
            "Grade": ["12", "12"],
            "Homeroom": ["C4", "C4"],
            "Teacher Name": ["Singh", "Singh"],
            "Absence Date": ["2024-09-20", "20-Sep-2024"],
            "Course Code": ["ENG12", "ENG12"],
            "Absence Category": ["A", "OffSite"],
            "Absence Sub Allocation Code": ["", ""],
            "Authorized Absence Code": ["", ""],
            "Master Timetable ID": ["MT003", "MT003"],
            "Section Letter": ["A", "A"],
            "Teacher ID": ["T004", "T004"],
            "School Course Code": ["ENG12", "ENG12"],
            "Flavour": ["", ""],
        }
    ).to_csv(path / filename, index=False, header=False)


# ---------------------------------------------------------------------------
# Per-district input file creation
# ---------------------------------------------------------------------------


def _create_myedbc_inputs(d: Path, *, course_catalog: bool = False) -> None:
    """The standard MyEd BC file set. ``course_catalog`` widens CourseInformation.txt
    to its catalog shape for the myBlueprint+ tiers (see :func:`_write_course_info`)."""
    _write_student_demographic(d, "StudentDemographicInformation.txt")
    _write_staff(d, "StaffInformationEnhanced.txt")
    _write_base_schedule(d, "StudentSchedule.txt")
    _write_course_info(d, catalog=course_catalog)
    _write_family(d, "EmergencyContactInformation.txt")
    _write_class_info_empty(d, "ClassInformationEnh.txt")


def _create_sd48_inputs(d: Path) -> None:
    _write_student_demographic(d, "StudentDemographicEnhanced.txt")
    _write_staff(d, "StaffInformation.txt")
    _write_base_schedule(d, "StudentSchedule.txt")
    _write_course_info(d)
    _write_family(d, "EmergencyContactInformation.txt")
    _write_class_info_empty(d, "ClassInformationEnh.txt")


def _create_sd74_inputs(d: Path) -> None:
    _write_student_demographic(d, "StudentDemographicInformation.txt")
    _write_staff(d, "StaffInformation.txt")
    _write_base_schedule(d, "studentcourseselection.txt", section_col="Section")
    _write_course_info(d)
    _write_family(d, "ParentInformation.txt", last_name_col="Surname")
    _write_class_info_empty(d, "ClassInfoEnhanced.txt")


def _create_sd40_inputs(d: Path) -> None:
    """SD40 (New Westminster): CSV extracts, HEADERLESS schedule, ATT--* exclusions.

    The schedule CSV is written WITHOUT a header row — column names are injected
    at extract time from the ``headers:`` block in sd40myedbc_mapping.yaml, so
    this fixture proves that injection end-to-end. An ATT--AM bookkeeping row
    (MT900) exercises ``excluded_course_codes``.
    """
    # Real SD-40_StudentDemographic.csv header subset (two-L "Enrollment status").
    pd.DataFrame(
        {
            "School number": ["100", "200", "200"],
            "Student number": ["S001", "S002", "S003"],
            "Homeroom": ["A1", "C3", "C4"],
            "Teacher name": ["Ms. Harper", "Mrs. Liu", "Mr. Singh"],
            "Legal surname": ["Smith", "Jones", "Brown"],
            "Legal first name": ["Alice", "Bob", "Charlie"],
            "Usual surname": ["", "", ""],
            "Usual first name": ["", "", ""],
            "Date of birth": ["2010-01-15", "2009-06-20", "2011-03-10"],
            "Grade": ["3", "10", "12"],
            "Enrollment status": ["Active", "Active", "Active"],
            "Next school code": ["", "", ""],
            "Student email address": ["", "", ""],
            "Teacher ID": ["T001", "T003", "T004"],
        }
    ).to_csv(d / "SD-40_StudentDemographic.csv", index=False)
    _write_staff(d, "SD-40_StaffInformation.csv")
    # HEADERLESS schedule — the 20 columns of the YAML `headers` block, in file
    # order (dict keys document the positions; header=False omits them on disk).
    pd.DataFrame(
        {
            "School Year": ["2025/2026"] * 4,
            "School Number": ["100", "200", "200", "200"],
            "Student Number": ["S001", "S002", "S003", "S002"],
            "PEN": ["P001", "P002", "P003", "P002"],
            "Grade": ["3", "10", "12", "10"],
            "Homeroom": ["A1", "C3", "C4", "C3"],
            "Course School Number": ["100", "200", "200", "200"],
            "Course Code": ["HR-3", "MAT10", "ENG12", "ATT--AM"],
            "District Course Code": ["HR-3", "MAT10", "ENG12", "ATT--AM"],
            "Course Title": ["Homeroom 3", "Math 10", "English 12", "AM Attendance"],
            "Short Name": ["HR3", "MA10", "EN12", "ATTAM"],
            "Period": ["1", "2", "3", "4"],
            "Day": ["1", "1", "1", "1"],
            "Semester": ["S1", "S1", "S1", "S1"],
            "Section Letter": ["A", "A", "A", "A"],
            "Master Timetable ID": ["MT001", "MT002", "MT003", "MT900"],
            "Teacher ID": ["T001", "T003", "T004", "T003"],
            "Teacher Name": ["Harper", "Liu", "Singh", "Liu"],
            "Primary Teacher": ["Y", "Y", "Y", "Y"],
            "Enrolment Status": ["Active", "Active", "Active", "Active"],
        }
    ).to_csv(d / "SD-40_StudentSchedule.csv", index=False, header=False)
    _write_course_info(d, "SD-40_CourseInformation.csv")
    _write_family(d, "SD-40_StudentEmergencyContact.csv")
    # Real SD-40_ClassInformation.csv has NO Master Timetable ID column, so
    # blended detection must fall back to the deduplicated schedule.
    pd.DataFrame(
        {
            "School Number": ["200"],
            "Course Code": ["MAT10"],
            "Teacher Id": ["T003"],
            "Primary Teacher": ["Y"],
            "Section Letter": ["A"],
            "Semester": ["S1"],
            "Term": ["T1"],
            "Day": ["1"],
            "Period": ["2"],
        }
    ).to_csv(d / "SD-40_ClassInformation.csv", index=False)


def _create_sd51_inputs(d: Path) -> None:
    """SD51 (Boundary): plain base inheritance + generated {student number} emails.

    StudentDailyAbsences.txt / StudentPeriodAbsences.txt are intentionally
    absent: the enabled StudentAttendance entity skips on all-empty sources
    (attendance has its own dedicated test module) while the 5 rostering CSVs
    still emit — this pins that a missing attendance drop never blocks rostering.
    """
    _write_student_demographic(d, "StudentDemographicEnhanced.txt")
    _write_staff(d, "StaffInformation.txt")
    _write_base_schedule(d, "StudentSchedule.txt")
    _write_course_info(d)
    _write_family(d, "EmergencyContactInformation.txt")
    _write_class_info_empty(d, "ClassInformationEnh.txt")


def _create_sd54_inputs(d: Path) -> None:
    """SD54 (Bulkley Valley): renamed lowercase files, no enrollment-status column.

    The real SD54 demographic has NO "Enrollment status" column, so active
    detection falls back to the withdraw date (S004 has a past date → dropped).
    Emails are generated as {legal surname}.{usual first name}@sd54.bc.ca. An
    ATT--AM row (MT900) exercises ``excluded_course_codes`` via the schedule's
    "District Course Code" column (SD54's schedule has no plain "Course Code").
    """
    pd.DataFrame(
        {
            "School number": ["100", "200", "200", "200"],
            "Student number": ["S001", "S002", "S003", "S004"],
            "Homeroom": ["A1", "C3", "C4", "C5"],
            "Teacher name": ["Ms. Harper", "Mrs. Liu", "Mr. Singh", "Mr. Singh"],
            "Legal surname": ["Smith", "Jones", "Brown", "White"],
            "Legal first name": ["Alice", "Bob", "Charlie", "Wendy"],
            "Usual surname": ["", "", "", ""],
            "Usual first name": ["Ali", "Rob", "Chuck", "Wen"],
            "Date of birth": ["2010-01-15", "2009-06-20", "2011-03-10", "2009-11-30"],
            "Grade": ["3", "10", "12", "11"],
            "Withdraw date": ["", "", "", "2024-09-15"],
            "Teacher ID": ["T001", "T003", "T004", "T004"],
        }
    ).to_csv(d / "StudentDemographicInformation.txt", index=False)
    _write_staff(d, "staffinformation.txt")
    # Real studentschedule.txt shape: "Student ID", District Course Code only,
    # "Semester/Term" — plus the ATT--AM bookkeeping row.
    pd.DataFrame(
        {
            "School Year": ["2025/2026"] * 4,
            "School Number": ["100", "200", "200", "200"],
            "Student ID": ["S001", "S002", "S003", "S002"],
            "Grade": ["3", "10", "12", "10"],
            "Homeroom": ["A1", "C3", "C4", "C3"],
            "District Course Code": ["HR-3", "MAT10", "ENG12", "ATT--AM"],
            "Course Title": ["Homeroom 3", "Math 10", "English 12", "AM Attendance"],
            "Period": ["1", "2", "3", "4"],
            "Day": ["1", "1", "1", "1"],
            "Semester/Term": ["S1", "S1", "S1", "S1"],
            "Section Letter": ["A", "A", "A", "A"],
            "Master Timetable ID": ["MT001", "MT002", "MT003", "MT900"],
            "Teacher ID": ["T001", "T003", "T004", "T003"],
            "Teacher Name": ["Harper", "Liu", "Singh", "Liu"],
            "Primary Teacher": ["Y", "Y", "Y", "Y"],
        }
    ).to_csv(d / "studentschedule.txt", index=False)
    _write_course_info(d, "courseinformation.txt")
    # S004's contact must be dropped with the student (active-roster filter).
    pd.DataFrame(
        {
            "Student Number": ["S001", "S004"],
            "First Name": ["John", "Wanda"],
            "Last Name": ["Smith", "White"],
            "Email Address": ["john@mail.com", "wanda@mail.com"],
        }
    ).to_csv(d / "EmergencyContactInformationEnhanced.txt", index=False)
    _write_class_info_empty(d, "classinformationenhanced.txt")


def _create_sd60_inputs(d: Path) -> None:
    """SD60 (Peace River North): the most-overridden district config.

    Exercises: Family ``row_filters`` (Parent Auth / Guardian = Y), generated
    learn60 emails (sanitize + derived admission-year yy), rostering under
    "Home school number", cross-enrollment collapse (S002 Active at schools
    200 AND 300 → one Students row, enrollments preserved at both), the base
    active_values dropping "Active No Primary" (S005), and ATT--AM exclusion.
    """
    pd.DataFrame(
        {
            "School number": ["200", "300", "100", "210", "200"],
            "Student number": ["S002", "S002", "S001", "S003", "S005"],
            "Homeroom": ["C3", "C3", "A1", "C4", "C5"],
            "Teacher name": ["Mrs. Liu", "Mrs. Liu", "Ms. Harper", "Mr. Singh", "Mr. Singh"],
            "Legal surname": ["Jones", "Jones", "Smith", "O'Brien", "Turner"],
            "Legal first name": ["Bob", "Bob", "Alice", "Mary-Jane", "Eve"],
            "Usual surname": ["", "", "", "", ""],
            "Usual first name": ["", "", "", "", ""],
            "Date of birth": ["2009-06-20", "2009-06-20", "2010-01-15", "2011-03-10", "2010-08-01"],
            "Grade": ["10", "10", "3", "12", "11"],
            "Enrollment status": ["Active", "Active", "Active", "Active", "Active No Primary"],
            "Admission date": ["2014-05-01", "2014-05-01", "2015-09-08", "2016-01-15", "2017-09-05"],
            "Home school number": ["200", "200", "100", "200", "200"],
            "Next school code": ["", "", "", "", ""],
            "Student email address": ["", "", "", "", ""],
            "Teacher ID": ["T003", "T003", "T001", "T004", "T004"],
        }
    ).to_csv(d / "Student_demo_enh.txt", index=False)
    _write_staff(d, "StaffInformation.txt")
    # Real StudentCourseSelection.txt shape: "Course Code" + "Section" (no
    # Section Letter, no Primary Teacher flag). S002 has classes at BOTH its
    # schools; the ATT--AM row (MT900) must be excluded.
    pd.DataFrame(
        {
            "School Year": ["2025/2026"] * 5,
            "School Number": ["100", "200", "300", "200", "200"],
            "Student Number": ["S001", "S002", "S002", "S003", "S002"],
            "Grade": ["3", "10", "10", "12", "10"],
            "Teacher Name": ["Harper", "Liu", "Singh", "Singh", "Liu"],
            "Semester": ["S1", "S1", "S1", "S1", "S1"],
            "Course Code": ["HR-3", "MAT10", "SCI10", "ENG12", "ATT--AM"],
            "Section": ["A", "A", "A", "A", "A"],
            "Master Timetable ID": ["MT001", "MT002", "MT202", "MT003", "MT900"],
            "Teacher ID": ["T001", "T003", "T004", "T004", "T003"],
        }
    ).to_csv(d / "StudentCourseSelection.txt", index=False)
    pd.DataFrame(
        {
            "School Number": ["100", "200", "300", "200"],
            "Course Code": ["HR-3", "MAT10", "SCI10", "ENG12"],
            "Title": ["Homeroom 3", "Math 10", "Science 10", "English 12"],
        }
    ).to_csv(d / "CourseInformation.txt", index=False)
    # row_filters keep only Parent Auth / Guardian = Y (the N contact drops).
    pd.DataFrame(
        {
            "Student Number": ["S001", "S001"],
            "First Name": ["John", "Nana"],
            "Last Name": ["Smith", "Elder"],
            "Email Address": ["john@mail.com", "nana@mail.com"],
            "Parent Auth / Guardian": ["Y", "N"],
        }
    ).to_csv(d / "EmergencyEnhanced.txt", index=False)
    _write_class_info_empty(d, "ClassInformation.txt")


def _create_sd51attendance_inputs(d: Path) -> None:
    """SD51 attendance tier: ONLY the two HEADERLESS absence GDEs.

    This config narrows ``enabled_entities`` to StudentAttendance alone, so no
    rostering GDE is required — and none is written, which also pins that a
    config with no roster anchor is a legitimate delivery (see
    ``pipeline._delivery_integrity_fault``).
    """
    _write_daily_absences(d)
    _write_period_absences(d)


def _create_mbp_all_inputs(d: Path) -> None:
    """mbp_all: the 5 rostering entities PLUS CourseInfo + StudentCourses."""
    _create_myedbc_inputs(d, course_catalog=True)
    _write_course_history(d)
    _write_course_selection(d)


def _create_mbp_core_inputs(d: Path) -> None:
    """mbp_core: Students + the two course CSVs — no schedule, staff or contacts."""
    _write_student_demographic(d, "StudentDemographicInformation.txt")
    _write_course_info(d, catalog=True)
    _write_course_history(d)
    _write_course_selection(d)


def _create_mbponly_inputs(d: Path) -> None:
    """mbponly: REUSE the committed fixtures its own e2e test owns.

    ``tests/snapshots/mbp_input/`` is the single source of the myBlueprint+ GDE
    fixture data (``tests/test_pipeline_e2e_mbponly.py`` owns the path constant);
    copying it in — rather than authoring a second synthetic copy, or pointing the
    run at the shared dir — keeps every config in this sweep on the same
    build-an-isolated-input-dir shape while leaving the committed fixtures
    read-only.
    """
    for name in ("CourseInformation.txt", "StudentCourseHistory.txt", "StudentCourseSelection.txt"):
        shutil.copy2(MBP_GDE_DIR / name, d / name)


_DISTRICT_SETUP = {
    "myedbc": _create_myedbc_inputs,
    "sd40myedbc": _create_sd40_inputs,
    "sd48myedbc": _create_sd48_inputs,
    "sd51myedbc": _create_sd51_inputs,
    "sd54myedbc": _create_sd54_inputs,
    "sd60myedbc": _create_sd60_inputs,
    "sd74myedbc": _create_sd74_inputs,
    "sd51attendance": _create_sd51attendance_inputs,
    # Same standard MyEd BC file shape + all 7 entities as mbp_all — sd83myedbc's
    # overrides (homeroom grades, course-grade floor, blanked DOB) are
    # business-logic differences the shared fixture already exercises correctly.
    "sd83myedbc": _create_mbp_all_inputs,
    "mbp_all": _create_mbp_all_inputs,
    "mbp_core": _create_mbp_core_inputs,
    "mbponly": _create_mbponly_inputs,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(
    params=list(_DISTRICT_SETUP.keys()),
    ids=list(_DISTRICT_SETUP.keys()),
    scope="module",
)
def district_output(request, tmp_path_factory):
    """Run the pipeline for one district and return (sis_type, output_dir).

    This is a MODULE-scoped fixture, so it runs during setup BEFORE the function-scoped
    ``isolated_user_profile`` autouse fixture is active. ``run_pipeline`` now writes a
    run record to the store via ``paths.user_data_dir()``, so redirect that seam into
    this fixture's own tmp dir here too — otherwise a module-scoped run would write the
    REAL ``history.db`` (the isolation canary would catch it). The CSV output goes to the
    explicit ``output_dir``, so the SpacesEDU schema / SD74 snapshot is unaffected.
    """
    sis = request.param
    d = tmp_path_factory.mktemp(f"contract_{sis}")
    input_dir = d / "input"
    output_dir = d / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    _DISTRICT_SETUP[sis](input_dir)
    mp = pytest.MonkeyPatch()
    mp.setattr("src.utils.paths.user_data_dir", lambda: d / ".districtsync")
    try:
        main(sis, str(input_dir), str(output_dir))
    finally:
        mp.undo()
    return sis, output_dir


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


def _skip_unless_emitted(sis: str, *entities: str) -> None:
    """Skip when this config's expected output does not include ``entities``.

    An entity-shaped assertion (Staff roles, enrollment orphans, …) is simply
    inapplicable to a config that does not emit that entity — e.g. mbponly emits
    no roster at all. Keyed off :data:`EXPECTED_ENTITIES` so the skip can never
    disagree with what the run is asserted to have produced.
    """
    absent = [e for e in entities if e not in EXPECTED_ENTITIES[sis]]
    if absent:
        pytest.skip(f"[{sis}] does not emit {', '.join(absent)}")


def _order_failure_message(sis: str, entity: str, actual: list[str]) -> str:
    expected = OUTPUT_SCHEMA[entity]
    missing = [c for c in expected if c not in actual]
    unexpected = [c for c in actual if c not in expected]
    return (
        f"[{sis}] {entity}.csv does not match the published output contract.\n"
        f"  expected (order matters): {expected}\n"
        f"  actual:                   {actual}\n"
        f"  missing: {missing or 'none'} | unexpected: {unexpected or 'none'}\n"
        f"  The authority for this column set AND its order is {ORDER_AUTHORITY}\n"
        f"  Emitted columns are treated as POSITIONAL: order-sensitivity is ESTABLISHED IN CODE\n"
        f"  for StudentAttendance (see src/etl/transformers/student_attendance.py) and NOT\n"
        f"  confirmed with the partner for the rostering / course feeds, so we pin the\n"
        f"  emitted order and require importer re-confirmation before changing it — this is\n"
        f"  a partner-visible change that MAY be rejected or silently mis-read.\n"
        f"  A red order test is a partner-visible contract change requiring importer "
        f"re-confirmation, not a test edit."
    )


@pytest.mark.integration
class TestOutputSchemaContract:
    def test_exactly_the_expected_csvs_are_emitted(self, district_output):
        """The run leaves EXACTLY the expected entity CSVs on disk — no more, no less.

        Both directions matter: a missing file is a broken delivery, and an
        unexpected one means a config emitted an entity nobody asked for (the
        `_base`-inheritance hazard behind the stale-output archival rule).
        """
        sis, out = district_output
        produced = {p.stem for p in out.glob("*.csv")}
        expected = set(EXPECTED_ENTITIES[sis])
        assert produced == expected, (
            f"[{sis}] emitted {sorted(produced)}, expected exactly {sorted(expected)} "
            f"(missing {sorted(expected - produced) or 'none'}; "
            f"unexpected {sorted(produced - expected) or 'none'})"
        )

    def test_column_order_matches_contract(self, district_output):
        """EXACT column order per emitted entity — set equality is not enough.

        Emitted columns are treated as POSITIONAL: order-sensitivity is CONFIRMED
        for StudentAttendance (``src/etl/transformers/student_attendance.py``) and
        NOT yet confirmed with the partner for the rostering / course feeds, so we
        pin the emitted order and require importer re-confirmation before changing
        it. A membership-only assertion would pass straight through a reorder.

        EVERY mismatching entity is reported, not just the first — a config whose
        output drifted in three entities should say so in one run.
        """
        sis, out = district_output
        failures = []
        for entity in sorted(EXPECTED_ENTITIES[sis]):
            df = pd.read_csv(out / DataLoader.csv_filename(entity), encoding="utf-8-sig")
            actual = list(df.columns)
            if actual != OUTPUT_SCHEMA[entity]:
                failures.append(_order_failure_message(sis, entity, actual))
        assert not failures, "\n\n".join(failures)

    def test_staff_role_values(self, district_output):
        sis, out = district_output
        _skip_unless_emitted(sis, "Staff")
        df = pd.read_csv(out / "Staff.csv", encoding="utf-8-sig")
        bad = set(df["Role"].dropna().unique()) - VALID_STAFF_ROLES
        assert not bad, f"[{sis}] Staff.csv has invalid Role values: {bad}"

    def test_enrollment_role_values(self, district_output):
        sis, out = district_output
        _skip_unless_emitted(sis, "Enrollments")
        df = pd.read_csv(out / "Enrollments.csv", encoding="utf-8-sig")
        bad = set(df["Role"].dropna().unique()) - VALID_ENROLLMENT_ROLES
        assert not bad, f"[{sis}] Enrollments.csv has invalid Role values: {bad}"

    def test_class_ids_contain_school_year(self, district_output):
        sis, out = district_output
        _skip_unless_emitted(sis, "Classes")
        classes = pd.read_csv(out / "Classes.csv", encoding="utf-8-sig")
        ids = classes["Class ID"].dropna().astype(str)
        assert any("_20" in cid for cid in ids), f"[{sis}] No Class ID contains a school year suffix"

    def test_students_grade_is_ceds_format(self, district_output):
        sis, out = district_output
        _skip_unless_emitted(sis, "Students")
        students = pd.read_csv(out / "Students.csv", encoding="utf-8-sig", dtype=str)
        grades = students["Grade"].dropna()
        # CEDS grades are 2-char strings (e.g. "03", "KG") or special values
        invalid = [g for g in grades if len(str(g)) > 5]
        assert not invalid, f"[{sis}] Students.csv has unexpectedly long Grade values: {invalid}"

    def test_every_enrollment_class_exists_in_classes(self, district_output):
        """Every Class ID referenced in Enrollments.csv must exist in Classes.csv.

        Regression guard for the blended-class orphan bug: detected blended
        classes must always be written to Classes.csv before Enrollments
        references them.
        """
        sis, out = district_output
        _skip_unless_emitted(sis, "Classes", "Enrollments")
        classes = pd.read_csv(out / "Classes.csv", encoding="utf-8-sig", dtype=str)
        enrollments = pd.read_csv(out / "Enrollments.csv", encoding="utf-8-sig", dtype=str)
        class_ids = set(classes["Class ID"].dropna().astype(str))
        enrolled_ids = set(enrollments["Class ID"].dropna().astype(str))
        orphans = enrolled_ids - class_ids
        assert not orphans, (
            f"[{sis}] {len(orphans)} Class IDs in Enrollments.csv are not defined in Classes.csv: {sorted(orphans)[:5]}"
        )

    def test_no_empty_user_ids_in_enrollments(self, district_output):
        """Every Enrollments row must have a non-empty User ID.

        Regression guard for SD40 FY2026: blended detection was grouping
        teacherless sections into fake blends and emitting teacher rows
        with empty User ID, which the partner's pre-upload validator
        rejects with 'Missing required Field:userId'.
        """
        sis, out = district_output
        _skip_unless_emitted(sis, "Enrollments")
        enrollments = pd.read_csv(out / "Enrollments.csv", encoding="utf-8-sig", dtype=str)
        user_ids = enrollments["User ID"].fillna("").astype(str).str.strip().str.lower()
        blank = enrollments[(user_ids == "") | (user_ids == "nan")]
        assert blank.empty, (
            f"[{sis}] {len(blank)} Enrollments rows have empty/nan User ID. Sample: {blank.head(3).to_dict('records')}"
        )

    def test_no_empty_class_ids_in_enrollments(self, district_output):
        """Every Enrollments row must have a non-empty Class ID."""
        sis, out = district_output
        _skip_unless_emitted(sis, "Enrollments")
        enrollments = pd.read_csv(out / "Enrollments.csv", encoding="utf-8-sig", dtype=str)
        class_ids = enrollments["Class ID"].fillna("").astype(str).str.strip().str.lower()
        blank = enrollments[(class_ids == "") | (class_ids == "nan")]
        assert blank.empty, f"[{sis}] {len(blank)} Enrollments rows have empty/nan Class ID"


# ---------------------------------------------------------------------------
# On-disk encoding contract (the per-entity BOM rule)
#
# Right-sized to ONE end-to-end byte assertion per encoding class rather than
# one per config × entity: no single config emits both classes (sd51myedbc's
# fixture deliberately supplies no absence GDEs, and that pin must survive), so
# the two SD51 tiers cover them between them — sd51myedbc for the BOM class,
# sd51attendance for the no-BOM class. The unit-level pins on
# `DataLoader.csv_encoding` live in tests/test_loader.py; the policy test below
# ties that SSOT to this module's contract statement.
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestOnDiskEncodingContract:
    @pytest.mark.parametrize("district_output", ["sd51myedbc"], indirect=True)
    def test_rostering_csv_starts_with_the_excel_bom(self, district_output):
        """Rostering CSVs are utf-8-SIG so districts can open them in Excel unmangled."""
        sis, out = district_output
        head = (out / "Students.csv").read_bytes()[: len(UTF8_BOM)]
        assert head == UTF8_BOM, f"[{sis}] Students.csv must start with the UTF-8 BOM, got {head!r}"

    @pytest.mark.parametrize("district_output", ["sd51attendance"], indirect=True)
    def test_student_attendance_csv_has_no_bom(self, district_output):
        """StudentAttendance is plain utf-8: SpacesEDU's attendance importer treats a
        BOM as part of the case-sensitive first header and rejects the file
        ("Unexpected file" + cascading "Invalid date format")."""
        sis, out = district_output
        head = (out / "StudentAttendance.csv").read_bytes()[: len(b"School Number")]
        assert not head.startswith(UTF8_BOM), f"[{sis}] StudentAttendance.csv must NOT carry a BOM"
        assert head == b"School Number", (
            f'[{sis}] StudentAttendance.csv must begin with the bare first header b"School Number", got {head!r}'
        )


def test_loader_encoding_policy_matches_the_contract():
    """`DataLoader.csv_encoding` (the code SSOT) agrees with the contract's BOM rule
    for every entity in the schema — so the byte assertions above and the writer
    can never drift apart."""
    for entity in OUTPUT_SCHEMA:
        expected = "utf-8" if entity in NO_BOM_ENTITIES else "utf-8-sig"
        assert DataLoader.csv_encoding(entity) == expected, (
            f"{entity}: loader writes {DataLoader.csv_encoding(entity)!r}, contract says {expected!r} "
            f"(see {ORDER_AUTHORITY} -> BOM matrix)"
        )


def test_pack_smoke_rostering_entities_match_the_expected_entity_table():
    """The release-gate exe smoke must not drift from the contract table.

    `scripts/ci_flet_pack_smoke.py` runs the packed exe against the SD74 fixture
    and asserts a hardcoded rostering-entity list. `EXPECTED_ENTITIES` is the
    single expectation source, so the smoke's list is pinned to it here — adding
    an entity to SD74's output without updating the smoke would otherwise leave
    the release gate silently checking the old set.

    The anchor is deliberately `EXPECTED_ENTITIES["sd74myedbc"]`, not
    `contract_schema.ROSTERING_ENTITIES` (they are the same object today, by
    identity). The latter names the 5-CSV rostering SET; the smoke asserts what
    the RELEASE artifact must produce for SD74. Re-anchoring to it would mean a
    future shrink of this module's SD74 fixture silently demanded the release
    gate shrink with it.
    """
    assert frozenset(smoke._ROSTERING_ENTITIES) == EXPECTED_ENTITIES["sd74myedbc"], (
        f"ci_flet_pack_smoke._ROSTERING_ENTITIES={sorted(smoke._ROSTERING_ENTITIES)} disagrees with "
        f"EXPECTED_ENTITIES['sd74myedbc']={sorted(EXPECTED_ENTITIES['sd74myedbc'])}"
    )


def test_the_sweep_covers_every_bundled_config():
    """EVERY bundled config is swept, and the two module tables agree on the set.

    ``available_configs`` is the discovery SSOT the CI validate step derives from,
    so a newly bundled mapping lands in this contract sweep by turning this test
    red — never by being silently uncovered. Scoped to the BUNDLED dir so a
    developer's user-dir override cannot make the assertion drift.
    """
    from src.config.loader import available_configs
    from src.utils.paths import bundle_mappings_dir

    bundled = set(available_configs(bundle_mappings_dir()))
    assert set(_DISTRICT_SETUP) == bundled, (
        f"contract sweep covers {sorted(_DISTRICT_SETUP)}, bundled configs are {sorted(bundled)}"
    )
    assert set(EXPECTED_ENTITIES) == bundled, (
        f"EXPECTED_ENTITIES covers {sorted(EXPECTED_ENTITIES)}, bundled configs are {sorted(bundled)}"
    )


def test_expected_entities_track_active_entities():
    """EXPECTED_ENTITIES' VALUES are pinned to the real configs — both erosion
    directions closed.

    ``test_the_sweep_covers_every_bundled_config`` pins the table's KEYS; this
    pins its VALUES, so the frozensets cannot quietly drift from what the configs
    actually enable:

    * (i) expected ⊆ active — the table can never claim a CSV the config does not
      even enable;
    * (ii) active − expected must equal the config's DECLARED gap — deleting an
      entity from a frozenset to make a red test green now fails HERE, and a new
      config that enables an entity whose sources no fixture supplies fails here
      too (declare it in :data:`DELIBERATELY_UNCOVERED`, with the reason, or give
      the fixture its sources);
    * (iii) expected ⊆ OUTPUT_SCHEMA — an entity in the table with no schema
      would otherwise surface as a bare ``KeyError`` inside the order sweep.

    This PRESERVES the sd51 skip-on-empty pin rather than eroding it: the gap
    stays, but it is now a declared, reviewed line instead of an unexplained
    absence.
    """
    from src.config.loader import load_config

    for sis, expected in sorted(EXPECTED_ENTITIES.items()):
        active = load_config(sis).active_entities()
        declared_gap = DELIBERATELY_UNCOVERED.get(sis, frozenset())

        assert expected <= active, (
            f"[{sis}] EXPECTED_ENTITIES lists {sorted(expected - active)}, which the config "
            f"does not enable (active: {sorted(active)})"
        )
        assert (active - expected) == declared_gap, (
            f"[{sis}] the gap between active_entities and EXPECTED_ENTITIES is "
            f"{sorted(active - expected)}, but DELIBERATELY_UNCOVERED declares "
            f"{sorted(declared_gap)}. Either supply that entity's source files in this "
            f"module's fixture builder, or declare the gap (with its reason) in "
            f"DELIBERATELY_UNCOVERED — never by shrinking an EXPECTED_ENTITIES value."
        )
        assert expected <= set(OUTPUT_SCHEMA), (
            f"[{sis}] EXPECTED_ENTITIES lists {sorted(expected - set(OUTPUT_SCHEMA))}, which has "
            f"no OUTPUT_SCHEMA entry — add its contract columns (in emitted order) first"
        )


# ---------------------------------------------------------------------------
# Per-district quirk pins
#
# Each test indirect-parametrizes `district_output` to ONE district, so pytest
# reuses that district's module-scoped run WITHIN a param block — but it re-enters
# the default param between indirect blocks, so the module is NOT 11 runs: it is
# ~6 extra ones (measured with `--setup-show`: 17 fixture setups for 11 params,
# ~1-2s). Cheap, and still far below the per-test cost without the module scope.
# ---------------------------------------------------------------------------


def _read_output(out: Path, entity: str) -> pd.DataFrame:
    return pd.read_csv(out / f"{entity}.csv", encoding="utf-8-sig", dtype=str)


def _assert_mt_excluded(out: Path, mt_id: str) -> None:
    """No Classes/Enrollments row may reference the excluded section's MT ID."""
    for entity in ("Classes", "Enrollments"):
        ids = _read_output(out, entity)["Class ID"].dropna().astype(str)
        offenders = [cid for cid in ids if cid.startswith(f"{mt_id}_")]
        assert not offenders, f"{entity}.csv contains excluded ATT section {mt_id}: {offenders}"


@pytest.mark.integration
class TestDistrictQuirks:
    # ---- SD40: headerless schedule + ATT exclusions + generated emails ----

    @pytest.mark.parametrize("district_output", ["sd40myedbc"], indirect=True)
    def test_sd40_headerless_schedule_columns_injected(self, district_output):
        """The header-free schedule CSV must load via the YAML `headers` block —
        proven by subject classes keyed on its Master Timetable ID column."""
        _, out = district_output
        ids = set(_read_output(out, "Classes")["Class ID"].dropna())
        assert any(cid.startswith("MT002_") for cid in ids), f"Expected MT002_<year> class, got {sorted(ids)}"
        assert any(cid.startswith("MT003_") for cid in ids)

    @pytest.mark.parametrize("district_output", ["sd40myedbc"], indirect=True)
    def test_sd40_att_bookkeeping_sections_excluded(self, district_output):
        _, out = district_output
        _assert_mt_excluded(out, "MT900")

    @pytest.mark.parametrize("district_output", ["sd40myedbc"], indirect=True)
    def test_sd40_generated_newwestschools_emails(self, district_output):
        _, out = district_output
        students = _read_output(out, "Students")
        assert set(students["Email Address"]) == {
            "s001@newwestschools.ca",
            "s002@newwestschools.ca",
            "s003@newwestschools.ca",
        }

    # ---- SD51: plain inheritance + generated emails ----

    @pytest.mark.parametrize("district_output", ["sd51myedbc"], indirect=True)
    def test_sd51_generated_sd51_emails(self, district_output):
        _, out = district_output
        students = _read_output(out, "Students")
        assert set(students["Email Address"]) == {
            "s001@sd51.bc.ca",
            "s002@sd51.bc.ca",
            "s003@sd51.bc.ca",
        }

    # ---- SD54: withdraw-date-only active detection + surname.firstname emails ----

    @pytest.mark.parametrize("district_output", ["sd54myedbc"], indirect=True)
    def test_sd54_generated_surname_dot_usual_first_emails(self, district_output):
        _, out = district_output
        students = _read_output(out, "Students")
        assert set(students["Email Address"]) == {
            "smith.ali@sd54.bc.ca",
            "jones.rob@sd54.bc.ca",
            "brown.chuck@sd54.bc.ca",
        }

    @pytest.mark.parametrize("district_output", ["sd54myedbc"], indirect=True)
    def test_sd54_withdraw_date_fallback_drops_student(self, district_output):
        """SD54's demographic has no status column — a past withdraw date must
        drop the student via the date-only fallback."""
        _, out = district_output
        user_ids = set(_read_output(out, "Students")["User ID"])
        assert "S004" not in user_ids
        assert {"S001", "S002", "S003"} == user_ids

    @pytest.mark.parametrize("district_output", ["sd54myedbc"], indirect=True)
    def test_sd54_family_filtered_to_active_roster(self, district_output):
        """The withdrawn student's contact must not ship (zero-orphan invariant)."""
        _, out = district_output
        family = _read_output(out, "Family")
        assert set(family["Student User ID"]) == {"S001"}

    @pytest.mark.parametrize("district_output", ["sd54myedbc"], indirect=True)
    def test_sd54_att_bookkeeping_sections_excluded(self, district_output):
        """SD54's schedule has no plain 'Course Code' column — exclusion must
        work via 'District Course Code'."""
        _, out = district_output
        _assert_mt_excluded(out, "MT900")

    # ---- SD60: row_filters + learn60 emails + home-school rostering + collapse ----

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_family_row_filter_keeps_guardians_only(self, district_output):
        _, out = district_output
        family = _read_output(out, "Family")
        assert set(family["Email"]) == {"john@mail.com"}, "non-guardian (N) contact must be dropped"
        assert len(family) == 1

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_generated_learn60_emails_sanitized_with_admission_yy(self, district_output):
        """firstlast + 2-digit admission year @learn60.ca; sanitize strips the
        apostrophe/hyphen from Mary-Jane O'Brien."""
        _, out = district_output
        students = _read_output(out, "Students")
        emails = dict(zip(students["User ID"], students["Email Address"]))
        assert emails == {
            "S001": "alicesmith15@learn60.ca",
            "S002": "bobjones14@learn60.ca",
            "S003": "maryjaneobrien16@learn60.ca",
        }

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_cross_enrollment_collapses_to_one_row_keeping_both_schools(self, district_output):
        """S002 is Active at schools 200 AND 300: ONE Students row (home school),
        but enrollments preserved at BOTH schools."""
        _, out = district_output
        students = _read_output(out, "Students")
        s002 = students[students["User ID"] == "S002"]
        assert len(s002) == 1
        assert s002["SchoolCode"].iloc[0] == "200"
        enrollments = _read_output(out, "Enrollments")
        s002_classes = set(enrollments[enrollments["User ID"] == "S002"]["Class ID"])
        assert any(cid.startswith("MT002_") for cid in s002_classes), "home-school class lost"
        assert any(cid.startswith("MT202_") for cid in s002_classes), "cross-school class lost"

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_rosters_under_home_school_number(self, district_output):
        """S003 attends school 210 but the Home school number is 200 —
        SchoolCode must be the home school."""
        _, out = district_output
        students = _read_output(out, "Students")
        assert students[students["User ID"] == "S003"]["SchoolCode"].iloc[0] == "200"

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_active_no_primary_dropped(self, district_output):
        _, out = district_output
        assert "S005" not in set(_read_output(out, "Students")["User ID"])

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_att_bookkeeping_sections_excluded(self, district_output):
        _, out = district_output
        _assert_mt_excluded(out, "MT900")

    @pytest.mark.parametrize("district_output", ["sd60myedbc"], indirect=True)
    def test_sd60_class_name_without_primary_teacher_flag(self, district_output):
        """SD60's schedule has no Primary-Teacher flag; class names still carry
        the teacher name, the course-info Title, AND the section letter.

        The section letter comes from SD60's configured "section letter":
        "Section" column (the schedule has no "Section Letter") — pinned
        exactly since the spaced-key Name config drives naming.
        """
        _, out = district_output
        classes = _read_output(out, "Classes")
        name = classes[classes["Class ID"].astype(str).str.startswith("MT002_")]["Name"].iloc[0]
        assert name == "Liu Math 10 (A) 2026", name
