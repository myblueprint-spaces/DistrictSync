"""The output contract, mirrored as data — the ONE in-test statement of what
DistrictSync emits.

This module is **neutral** on purpose: it holds constants and imports nothing
from the test suite, so a unit module (``test_transform_student_attendance.py``)
and an integration module (``test_contract.py``) can both consume it without one
dragging the other's heavyweight fixtures in. It exists because the same column
orders were previously spelled out in three places
(``test_contract.OUTPUT_SCHEMA``, ``test_pipeline_e2e_mbponly``'s two column
lists, ``test_transform_student_attendance.EXPECTED_COLUMNS``) with nothing
keeping them in step.

**The prose authority is** :data:`ORDER_AUTHORITY` — ``docs/developer/output-contract.md``.
That document is the maintained mirror of the live SpacesEDU importer; the
constants here are its *mechanical* mirror, and ``tests/test_output_contract_doc.py``
asserts the two cannot silently diverge. A red assertion sourced from this module
is a **partner-visible contract change requiring importer re-confirmation, not a
test edit**.

Dependency direction (deliberate, per the ROADMAP fold guidance): unit and e2e
modules import THIS module. Nothing imports ``test_contract`` — that module
imports the conftest-registered ``ci_flet_pack_smoke`` and drives a 12-config
pipeline sweep, so importing it from a unit test would invert the dependency and
drag both into every unit run.

What deliberately does NOT live here: ``test_contract.DELIBERATELY_UNCOVERED``.
That constant is a statement about *that module's fixtures* (which sources they
withhold), not about the contract, so it stays beside the fixtures it describes.
"""

from __future__ import annotations

#: The document that owns the emitted column set AND its order. Named in every
#: order-failure message so a red test routes to a re-confirmation with the
#: partner, never to an edit of the expectation.
ORDER_AUTHORITY = "docs/developer/output-contract.md"

#: Per-entity columns in EXACT emitted order — the base ``myedbc`` ``field_map``
#: key order, confirmed against the SD74 golden headers. Rostering columns are
#: the SpacesEDU Advanced CSV; CourseInfo/StudentCourses are the myBlueprint+
#: feeds (internal spec — see the doc); StudentAttendance is the 4-column
#: SpacesEDU half-day attendance feed (the contract permits dropping every
#: optional field after Student Number).
#:
#: Emitted columns are treated as POSITIONAL. Order-sensitivity is CONFIRMED for
#: ``StudentAttendance`` (``src/etl/transformers/student_attendance.py`` — "exact
#: case-sensitive order") and is NOT yet confirmed with the partner for the
#: rostering / course feeds, so the emitted order is pinned and a change requires
#: importer re-confirmation first.
OUTPUT_SCHEMA: dict[str, list[str]] = {
    "Students": [
        "User ID",
        "Student Number",
        "First Name",
        "Last Name",
        "Date of Birth",
        "Grade",
        "EnrollStatus",
        "SchoolCode",
        "Homeroom",
        "PreRegSchoolCode",
        "Preferred First Name",
        "Preferred Last Name",
        "Community Hours",
        "Literacy Test Completed",
        "Email Address",
    ],
    "Staff": ["User ID", "First Name", "Last Name", "Email", "Role", "School ID"],
    "Family": ["First Name", "Last Name", "Email", "Student User ID"],
    "Classes": ["Class ID", "Name", "Grade", "School ID", "Start Date", "End Date"],
    "Enrollments": ["Class ID", "User ID", "Role", "School ID"],
    "CourseInfo": [
        "Course Code",
        "Alternate Course Code",
        "School ID",
        "Course Name",
        "Course Description",
        "Discipline",
        "Department",
        "Type",
        "Grade",
        "MaxGrade",
        "Credit Value",
        "IntegrationId",
        "Year Offered",
    ],
    "StudentCourses": [
        "Student ID",
        "Course Code",
        "IntegrationId",
        "Course Name",
        "Completion Date",
        "Final Mark",
        "Credits Earned",
        "Alternate Course Code",
        "Potential Credits Earned",
        "Term Grade",
    ],
    "StudentAttendance": [
        "School Number",
        "Absence Date",
        "Absence Category",
        "Student Number",
    ],
}

#: Entities written as plain UTF-8 (NO BOM) because a strict downstream parser
#: treats the BOM as part of the case-sensitive first header. The contract's own
#: statement of the rule; ``DataLoader._NO_BOM_ENTITIES`` / ``csv_encoding`` is
#: the code SSOT and ``test_contract.test_loader_encoding_policy_matches_the_contract``
#: pins the two together.
NO_BOM_ENTITIES: frozenset[str] = frozenset({"StudentAttendance"})

#: The 5 SpacesEDU rostering CSVs — the set every district config emits.
ROSTERING_ENTITIES: frozenset[str] = frozenset({"Students", "Staff", "Family", "Classes", "Enrollments"})

#: Per config, the entity CSVs the contract run is expected to leave on disk —
#: the single source the fixtures, the assertions AND the published doc's
#: expected-outputs table all read.
#:
#: This is deliberately NOT derived from the config: it is
#: ``config.active_entities() ∩ entities whose source files the fixture supplies``.
#: ``sd51myedbc`` actively enables StudentAttendance, but the contract fixture
#: supplies no absence GDEs on purpose (the skip-on-empty pin — a missing
#: attendance drop must never block rostering), so StudentAttendance is absent
#: from its expected set. Deriving this from ``active_entities()`` would erase
#: exactly that pin.
#:
#: Its VALUES are not free-floating: ``test_contract.test_expected_entities_track_active_entities``
#: pins every entry against the real config plus ``test_contract.DELIBERATELY_UNCOVERED``,
#: so neither erosion direction can pass silently.
EXPECTED_ENTITIES: dict[str, frozenset[str]] = {
    "myedbc": ROSTERING_ENTITIES,
    "sd40myedbc": ROSTERING_ENTITIES,
    "sd48myedbc": ROSTERING_ENTITIES,
    "sd51myedbc": ROSTERING_ENTITIES,  # StudentAttendance enabled, absence GDEs deliberately absent
    "sd54myedbc": ROSTERING_ENTITIES,
    "sd60myedbc": ROSTERING_ENTITIES,
    "sd74myedbc": ROSTERING_ENTITIES,
    "sd51attendance": frozenset({"StudentAttendance"}),
    "sd83myedbc": ROSTERING_ENTITIES | {"CourseInfo", "StudentCourses"},
    # Phase-2 migration districts (2026-08-31): six full-tier configs on the
    # standard MyEd BC file shape (grade-scope overrides are business logic the
    # shared fixture exercises), plus SD10 on the mbp_core shape.
    "sd27myedbc": ROSTERING_ENTITIES | {"CourseInfo", "StudentCourses"},
    "sd38myedbc": ROSTERING_ENTITIES | {"CourseInfo", "StudentCourses"},
    "sd67myedbc": ROSTERING_ENTITIES | {"CourseInfo", "StudentCourses"},
    "sd69myedbc": ROSTERING_ENTITIES | {"CourseInfo", "StudentCourses"},
    "sd71myedbc": ROSTERING_ENTITIES | {"CourseInfo", "StudentCourses"},
    "sd75myedbc": ROSTERING_ENTITIES | {"CourseInfo", "StudentCourses"},
    "sd10myedbc": frozenset({"Students", "CourseInfo", "StudentCourses"}),
    # Unity Christian School (2026-09-01): the rostering tier MINUS Family. Family is
    # DISABLED in the config (its contact GDE carries no email column at all), so this
    # is an `enabled_entities` fact, not a withheld fixture — hence no
    # DELIBERATELY_UNCOVERED entry. Rides the standard MyEd BC file shape.
    "unitychristianmyedbc": ROSTERING_ENTITIES - {"Family"},
    "mbp_all": ROSTERING_ENTITIES | {"CourseInfo", "StudentCourses"},
    "mbp_core": frozenset({"Students", "CourseInfo", "StudentCourses"}),
    "mbponly": frozenset({"CourseInfo", "StudentCourses"}),
}
