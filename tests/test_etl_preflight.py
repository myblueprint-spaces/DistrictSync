"""Tests for the pure pre-flight expected/missing source-column derivation.

WHY the derivation matters (and why these tests are not paranoia): an absent
mapped column is an *intended blank* — ``apply_field_map`` does not record it in
``context.data_errors`` and the run reports success — so this module is the ONLY
signal that a district's renamed header is silently shipping a column of blanks.
Two failure directions matter and are twinned throughout: reporting NOTHING when
a column really is missing (the admin finds out in October), and reporting a WALL
of columns from an empty or partial observation (the report becomes noise and
gets ignored).
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

from src.config.loader import load_config
from src.config.models import EntityConfig, GlobalConfig, MappingConfig
from src.etl.column_names import normalize_column_name
from src.etl.preflight import (
    OBSERVATION_SCOPE,
    ROW_FILTER_FIELD,
    ExpectationOrigin,
    ExpectedColumn,
    MissingColumn,
    ObservationScope,
    PreflightReport,
    expected_columns,
    missing_columns,
    missing_columns_by_entity,
    observation_scope,
    preflight_report,
)
from tests.test_contract import _DISTRICT_SETUP

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "etl" / "preflight.py"
SNAPSHOT_INPUT = Path(__file__).resolve().parent / "snapshots" / "input"


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


class TestPurity:
    def test_the_module_imports_no_flet_no_pandas_and_no_io(self):
        """The docstring claims a pure, frame-free derivation. ``pandas`` is banned
        because that is what says this module never handles a FRAME (it is handed the
        already-observed headers), ``pathlib``/``flet`` because it does no I/O and must
        stay testable headless."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        banned = ("flet", "pandas", "pathlib")
        assert not [name for name in imported if name.split(".")[0] in banned]
        # Positive twin — the sweep really does see this module's imports.
        assert "src.config.models" in imported
        assert "src.etl.column_names" in imported


# ---------------------------------------------------------------------------
# expected_columns over the REAL bundled configs
# ---------------------------------------------------------------------------


class TestExpectedColumnsOverRealConfigs:
    def test_myedbc_names_the_columns_its_entities_actually_read(self):
        expected = expected_columns(load_config("myedbc"))
        assert expected

        by_entity: dict[str, set[str]] = {}
        for item in expected:
            by_entity.setdefault(item.entity, set()).add(normalize_column_name(item.source_column))

        assert {"legal first name", "legal surname", "date of birth"} <= by_entity["Students"]
        assert "email address" in by_entity["Staff"]

    def test_a_fixed_value_field_names_no_column(self):
        """``mbp_core``'s StudentCourses field_map is ALL ``{value: ""}`` (the transformer
        fills those columns itself), so an ACTIVE entity contributes ZERO expected
        columns — a literal names no header. Twin below: the same config's Students and
        CourseInfo entities DO contribute."""
        config = load_config("mbp_core")
        assert "StudentCourses" in config.active_entities()

        expected = expected_columns(config)
        assert [item for item in expected if item.entity == "StudentCourses"] == []
        assert {item.entity for item in expected} == {"Students", "CourseInfo"}

    def test_an_academic_year_field_names_no_column(self):
        """``Start Date``/``End Date`` resolve from the computed academic year."""
        expected = expected_columns(load_config("myedbc"))
        assert [item for item in expected if item.output_field in ("Start Date", "End Date")] == []

    def test_the_auto_detected_enroll_status_sentinel_names_no_column(self):
        """myedbc leaves ``EnrollStatus: null`` — the status column is resolved from a SET
        of aliases at transform time, so naming any one of them would be false."""
        config = load_config("myedbc")
        assert config.mappings["Students"].field_map["EnrollStatus"] is None
        assert [item for item in expected_columns(config) if item.output_field == "EnrollStatus"] == []

    def test_a_disabled_entity_contributes_nothing(self):
        """``mbponly`` DEFINES the rostering entities (via ``_base``) but emits neither —
        their columns must not be reported as missing from files the run never loads.
        Twin: the entities it DOES emit are present."""
        config = load_config("mbponly")
        assert {"Staff", "Family", "Classes"} <= set(config.mappings)

        entities = {item.entity for item in expected_columns(config)}
        assert not entities & {"Staff", "Family", "Classes", "Enrollments"}
        assert entities <= config.active_entities()
        assert "CourseInfo" in entities

    def test_a_row_filter_column_is_expected(self):
        """SD60's Family keeps only true guardians — the filter column must exist or the
        run fails loudly, so it belongs in the expectation set."""
        expected = expected_columns(load_config("sd60myedbc"))
        rows = [item for item in expected if item.output_field == ROW_FILTER_FIELD]
        assert [normalize_column_name(item.source_column) for item in rows] == ["parent auth / guardian"]
        assert rows[0].entity == "Family"

    def test_an_email_template_names_its_placeholders_but_not_its_pseudo_fields(self):
        """SD60 generates ``{legal first}{legal surname}{admission yy}@learn60.ca``:
        the two name placeholders and the derived date's SOURCE column are real headers;
        the ``admission yy`` pseudo field is injected by the transformer and is NOT."""
        config = load_config("sd60myedbc")
        email = config.mappings["Students"].field_map["Email Address"]
        assert set(email.derived_dates) == {"admission yy"}

        names = [
            normalize_column_name(item.source_column)
            for item in expected_columns(config)
            if item.output_field == "Email Address"
        ]
        assert "admission yy" not in names
        assert normalize_column_name(email.derived_dates["admission yy"].column) in names
        assert len(names) == 3


# ---------------------------------------------------------------------------
# expected_columns over a SYNTHETIC config — every variant, in one place
# ---------------------------------------------------------------------------


def _synthetic_config() -> MappingConfig:
    """One config exercising EVERY field-map variant, plus row_filters/source_columns.

    ``Family`` is defined but NOT enabled (the disabled-entity twin). No entity is
    called ``Classes``, so the academic-date validator stays out of the way.
    """
    return MappingConfig(
        version="1.0",
        sis="SyntheticSIS",
        district_name="Synthetic",
        global_config={"enabled_entities": ["Students", "Staff"]},
        mappings={
            "Students": {
                "source_files": {"student_demographic": "Demo.txt"},
                "field_map": {
                    "User ID": "Student Number",  # bare string
                    "EnrollStatus": None,  # auto-detect sentinel → nothing
                    "Grade": {"column": "Grade Level", "transform": "grade_to_ceds"},
                    "Class ID": {"column": "Master Timetable ID", "append_year_to_id": True},
                    "School ID": {"value": "100"},  # fixed literal → nothing
                    "Start Date": {"use_academic_year": True},  # → nothing
                    "Email Address": {
                        "format": "{legal first name}.{admission yy}@example.org",
                        "derived_dates": {"admission yy": {"column": "Admission Date", "date_format": "yy"}},
                    },
                    "Name": {
                        "primary teacher flag": "Primary Teacher",
                        "teacher last name": "Teacher Name",
                        "course title": "Title",
                        "section letter": "",  # blank → skipped
                    },
                    "Role": {"student_id_col": "Student ID", "staff_id_col": "Teacher ID"},
                    "Status": {
                        "status_column": "Enrolment Status",
                        "withdraw_date_column": "Withdraw Date",
                        "active_values": ["Active", "PreReg"],  # VALUES, not a column
                    },
                    "Typo": {"unrecognised": "shape"},  # warn-passthrough → nothing
                },
                "row_filters": [{"column": "Parent Auth / Guardian", "include": ["Y"]}],
                "source_columns": {"full_course_code": "Course Code Full", "unset_role": ""},
            },
            "Staff": {
                "source_files": {"staff_info": "Staff.txt"},
                # The SAME header, read by a second entity — the grouping case.
                "field_map": {"User ID": "Teacher ID", "Last Name": "  Legal Surname  "},
            },
            "Family": {
                "source_files": {"emergency_contacts": "Parents.txt"},
                "field_map": {"Email": "Contact Email"},
            },
        },
    )


class TestExpectedColumnsOverEveryVariant:
    def test_every_shape_that_names_a_column_contributes_exactly_once(self):
        expected = expected_columns(_synthetic_config())
        students = {(item.output_field, item.source_column) for item in expected if item.entity == "Students"}
        assert students == {
            ("User ID", "Student Number"),
            ("Grade", "Grade Level"),
            ("Class ID", "Master Timetable ID"),
            ("Email Address", "legal first name"),
            ("Email Address", "Admission Date"),
            ("Name", "Primary Teacher"),
            ("Name", "Teacher Name"),
            ("Name", "Title"),
            ("Role", "Student ID"),
            ("Role", "Teacher ID"),
            ("Status", "Enrolment Status"),
            ("Status", "Withdraw Date"),
            (ROW_FILTER_FIELD, "Parent Auth / Guardian"),
            ("full_course_code", "Course Code Full"),
        }

    def test_the_shapes_that_name_nothing_name_nothing(self):
        """Twinned against the assertion above: the same entity DOES contribute for the
        transform/format/name-config shapes, so an empty result here is not vacuous."""
        expected = expected_columns(_synthetic_config())
        silent = {"EnrollStatus", "School ID", "Start Date", "Typo"}
        assert [item for item in expected if item.output_field in silent] == []

    def test_a_blank_configured_name_is_not_an_expectation(self):
        """The name config's blank ``section letter`` and the blank ``source_columns``
        role are absent — a blank names no column, and reporting ``""`` as missing would
        be a permanent false finding on every district."""
        expected = expected_columns(_synthetic_config())
        assert all(item.source_column.strip() for item in expected)
        assert "unset_role" not in {item.output_field for item in expected}

    def test_the_disabled_entity_is_absent(self):
        expected = expected_columns(_synthetic_config())
        assert {item.entity for item in expected} == {"Students", "Staff"}
        assert "Contact Email" not in {item.source_column for item in expected}

    def test_the_reported_spelling_is_the_config_s_own_trimmed(self):
        expected = expected_columns(_synthetic_config())
        staff = {item.source_column for item in expected if item.entity == "Staff"}
        assert staff == {"Teacher ID", "Legal Surname"}  # trimmed, case preserved


# ---------------------------------------------------------------------------
# Normalisation parity — one rule, both sides
# ---------------------------------------------------------------------------


class TestNormalisationParity:
    def test_a_padded_mixed_case_config_entry_matches_a_real_extractor_header(self, tmp_path: Path):
        """The two sides of the comparison are the CONFIG's spelling and a header the
        EXTRACTOR observed. Asserted against ``normalize_column_name`` itself — the one
        rule both sides now share — so a change to either cannot drift silently."""
        from src.etl.extractor import DataExtractor

        (tmp_path / "Demo.txt").write_text("  Legal Surname ,Student Number\nSmith,S1\n", encoding="utf-8")
        frame = DataExtractor(str(tmp_path)).load_data(["Demo.txt"])["Demo.txt"]
        observed = tuple(str(column) for column in frame.columns)

        assert "legal surname" in observed  # the extractor already normalised it
        assert normalize_column_name("  Legal Surname ") in observed

        expected = (ExpectedColumn(entity="Staff", output_field="Last Name", source_column="  Legal Surname "),)
        assert missing_columns(expected, {"Demo.txt": observed}) == ()

    def test_the_frame_level_normaliser_delegates_to_the_promoted_one(self):
        """Acceptance criterion 6: ONE public rule, used by both sides."""
        import pandas as pd

        from src.utils.helpers import normalize_columns

        frame = pd.DataFrame(columns=["  Legal Surname ", "GRADE"])
        assert list(normalize_columns(frame).columns) == [
            normalize_column_name("  Legal Surname "),
            normalize_column_name("GRADE"),
        ]


# ---------------------------------------------------------------------------
# missing_columns
# ---------------------------------------------------------------------------


_EXPECTED_PAIR = (
    ExpectedColumn(entity="Students", output_field="Last Name", source_column="Legal Surname"),
    ExpectedColumn(entity="Staff", output_field="Last Name", source_column="Legal Surname"),
    ExpectedColumn(entity="Students", output_field="User ID", source_column="Student Number"),
)


class TestMissingColumns:
    def test_a_column_present_in_ANY_file_is_not_reported(self):
        """The claim is file-AGNOSTIC: a ``field_map`` entry names no file, so a header
        found in any loaded file satisfies it."""
        observed = {
            "Demo.txt": ("student number",),
            "Staff.txt": ("legal surname",),
        }
        assert missing_columns(_EXPECTED_PAIR, observed) == ()

    def test_a_column_absent_from_EVERY_file_is_reported_once_and_grouped(self):
        observed = {"Demo.txt": ("student number",), "Staff.txt": ("first name",)}

        result = missing_columns(_EXPECTED_PAIR, observed)

        assert result == (
            MissingColumn(
                source_column="Legal Surname",
                entities=("Students", "Staff"),
                output_fields=("Last Name",),
            ),
        )

    def test_the_first_seen_spelling_is_kept(self):
        expected = (
            ExpectedColumn(entity="Students", output_field="Last Name", source_column="Legal Surname"),
            ExpectedColumn(entity="Staff", output_field="Last Name", source_column="  legal SURNAME  "),
        )
        result = missing_columns(expected, {"Demo.txt": ("student number",)})
        assert [item.source_column for item in result] == ["Legal Surname"]
        assert result[0].entities == ("Students", "Staff")

    def test_an_empty_observation_makes_NO_claim(self):
        """The premise of "not in any of your files" is that we read them. A
        default-constructed ``PipelineResult`` (or a run whose every file was absent)
        must report nothing rather than indict the whole config."""
        assert missing_columns(_EXPECTED_PAIR, {}) == ()
        assert missing_columns(_EXPECTED_PAIR, {"Demo.txt": (), "Staff.txt": ()}) == ()

    def test_ONE_observed_header_is_enough_to_make_a_claim(self):
        """The twin of the rule above — the guard is "nothing observed", not "not
        everything observed", so a partial observation still reports."""
        result = missing_columns(_EXPECTED_PAIR, {"Demo.txt": ("student number",), "Staff.txt": ()})
        assert [item.source_column for item in result] == ["Legal Surname"]

    def test_an_expectation_with_no_columns_reports_nothing(self):
        assert missing_columns((), {"Demo.txt": ("student number",)}) == ()

    def test_a_malformed_observation_value_never_raises(self):
        """A hand-built mapping (a test, a future caller) may carry a shape this layer
        never produces; it must degrade, not raise. ``None`` contributes no headers, so
        with nothing else observed there is no claim; the twin shows the readable file
        beside it still counts."""
        assert missing_columns(_EXPECTED_PAIR, {"Demo.txt": None}) == ()  # type: ignore[dict-item]
        result = missing_columns(
            _EXPECTED_PAIR,
            {"Demo.txt": None, "Staff.txt": ("student number",)},  # type: ignore[dict-item]
        )
        assert [item.source_column for item in result] == ["Legal Surname"]


# ---------------------------------------------------------------------------
# preflight_report — the measured denominators + totality
# ---------------------------------------------------------------------------


class TestPreflightReport:
    def test_the_denominators_measure_what_was_actually_read(self):
        config = _synthetic_config()
        report = preflight_report(
            config,
            {"Demo.txt": ("student number", "grade level"), "Staff.txt": (), "Parents.txt": ()},
        )

        assert isinstance(report, PreflightReport)
        # Only the file that contributed a header counts as read.
        assert report.checked_files == 1
        # A green report can never be an empty derivation: the denominator is > 0 and
        # equals the distinct expected columns.
        assert report.checked_columns == len(
            {normalize_column_name(item.source_column) for item in expected_columns(config)}
        )
        assert report.checked_columns > 0
        assert "Legal Surname" in {item.source_column for item in report.missing}

    def test_a_junk_field_map_value_survives_validation_as_a_string_and_is_not_reported(self):
        """The REAL path a hand-edited YAML takes — and why the shape filter exists.

        ``MappingConfig(**raw)`` runs ``classify_field``, whose non-dict fallback is
        ``str(raw)``: ``field_map: {"Weird": [1, 2]}`` is stored as the bare string
        ``'[1, 2]'`` and ``{"WeirdNum": 5}`` as ``'5'``. Both reach preflight
        indistinguishable by TYPE from a real column name, so the ``_field_map_columns``
        junk-shape guard (which fires only for ``model_construct``-style callers) never
        sees them — without ``_looks_like_header`` the report would tell an admin to look
        for a column named ``[1, 2]`` in their export."""
        raw = {
            "version": "1.0",
            "sis": "Junk",
            "district_name": "Junk District",
            "global_config": {},
            "mappings": {
                "Students": {
                    "source_files": {"student_demographic": "Demo.txt"},
                    "field_map": {"Weird": [1, 2], "WeirdNum": 5, "Fine": "Legal Surname"},
                }
            },
        }
        config = MappingConfig(**raw)
        # The positive twin for the premise: validation really did stringify them.
        assert config.mappings["Students"].field_map["Weird"] == "[1, 2]"

        assert [normalize_column_name(item.source_column) for item in expected_columns(config)] == ["legal surname"]

        report = preflight_report(config, {"Demo.txt": ("student number",)})
        assert [item.source_column for item in report.missing] == ["Legal Surname"]
        assert report.checked_columns == 1

    def test_a_real_config_has_a_non_zero_denominator(self):
        report = preflight_report(load_config("myedbc"), {"StudentDemographicInformation.txt": ("student number",)})
        assert report.checked_columns > 10
        assert report.checked_files == 1

    def test_nothing_observed_is_reported_as_nothing_read_and_nothing_claimed(self):
        report = preflight_report(load_config("myedbc"), {"StudentDemographicInformation.txt": ()})
        assert report.missing == ()
        assert report.checked_files == 0
        assert report.checked_columns > 0  # we DID derive expectations — we just read nothing

    def test_a_malformed_field_map_degrades_instead_of_raising(self):
        """Totality (acceptance criterion 2) for a RAW / UNVALIDATED caller.

        ``model_construct`` bypasses Pydantic entirely, so this pins the in-module guard
        for a caller that hands over shapes ``classify_field`` never saw: a list, a
        nested dict, a non-string column, a junk row filter and a blank auxiliary
        column. It is deliberately NOT a hand-edited-YAML scenario — a hand-edited YAML
        goes through ``MappingConfig`` and arrives already stringified (see
        ``test_a_junk_field_map_value_survives_validation_as_a_string_and_is_not_reported``).
        Only the ONE readable entry survives, which is the positive twin proving the walk
        still ran."""
        config = MappingConfig.model_construct(
            version="1.0",
            sis="Malformed",
            district_name="",
            district_domains=[],
            global_config=GlobalConfig(),
            mappings={
                "Students": EntityConfig.model_construct(
                    source_files={"student_demographic": "Demo.txt"},
                    field_map={
                        "A": ["Legal Surname", "Student Number"],
                        "B": {"column": {"nested": 1}},
                        "C": {"column": 5},
                        "D": " Usual Surname ",
                    },
                    headers={},
                    row_filters=["not a row filter"],
                    source_columns={"role": None},
                )
            },
        )

        report = preflight_report(config, {"Demo.txt": ("student number",)})

        assert [item.source_column for item in report.missing] == ["Usual Surname"]
        assert report.checked_columns == 1


# ---------------------------------------------------------------------------
# End to end — a REAL run's observation, a REAL config
# ---------------------------------------------------------------------------


@pytest.mark.regression
class TestAgainstARealRun:
    """The headline case, honestly constructed: rename ONE header in a copy of the SD74
    snapshot extract and prove the report names exactly that header — the situation the
    ETL itself reports as a clean, successful, zero-data-error run.
    """

    @staticmethod
    def _run(input_dir: Path, tmp_path: Path) -> dict[str, tuple[str, ...]]:
        from src.etl.pipeline import run_pipeline

        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        result = run_pipeline("sd74myedbc", str(input_dir), str(out), dry_run=True)
        assert result is not None
        return result.input_columns

    def test_a_renamed_header_is_named_by_the_report_after_a_SUCCESSFUL_run(self, tmp_path: Path):
        source = tmp_path / "input"
        shutil.copytree(SNAPSHOT_INPUT, source)
        demographic = source / "StudentDemographicInformation.txt"
        text = demographic.read_text(encoding="utf-8")
        assert "Legal surname," in text
        demographic.write_text(text.replace("Legal surname,", "Family name,", 1), encoding="utf-8")

        config = load_config("sd74myedbc")
        report = preflight_report(config, self._run(source, tmp_path))

        renamed = [item for item in report.missing if normalize_column_name(item.source_column) == "legal surname"]
        assert len(renamed) == 1
        assert renamed[0].entities == ("Students",)
        assert report.checked_files == 6  # every configured file contributed a header

    def test_the_unmodified_extract_does_not_report_that_header(self, tmp_path: Path):
        """The twin. (The live SD74 config legitimately names a column the FROZEN
        snapshot extract predates, so the baseline is not empty — this asserts the
        header the test above renames is absent from it, which is the claim that
        matters.)"""
        source = tmp_path / "input"
        shutil.copytree(SNAPSHOT_INPUT, source)

        report = preflight_report(load_config("sd74myedbc"), self._run(source, tmp_path))

        assert "legal surname" not in {normalize_column_name(item.source_column) for item in report.missing}
        assert report.checked_files == 6


# ---------------------------------------------------------------------------
# Totality — every derivation degrades rather than raising
# ---------------------------------------------------------------------------


class _Raises:
    """A value whose every reading raises — the shape a hand-edited config can smuggle in."""

    @property
    def column(self):  # noqa: D102 - a hostile property, by design
        raise RuntimeError("unreadable column")

    def __str__(self) -> str:
        raise RuntimeError("unreadable value")


class _HostileEntity:
    """An entity object whose three column-bearing attributes all raise on read."""

    @property
    def field_map(self):  # noqa: D102
        raise RuntimeError("unreadable field_map")

    @property
    def row_filters(self):  # noqa: D102
        raise RuntimeError("unreadable row_filters")

    @property
    def source_columns(self):  # noqa: D102
        raise RuntimeError("unreadable source_columns")


class _HostileConfig:
    """A config-shaped object whose entity mappings cannot be read at all."""

    mappings: dict = {}

    def active_entities(self):  # noqa: D102
        raise RuntimeError("unreadable enabled_entities")


class _ReadableEntity:
    """A minimal readable entity: one bare-string field_map entry, nothing else."""

    def __init__(self, field_map: dict | None = None) -> None:
        self.field_map = {"Last Name": "Legal Surname"} if field_map is None else field_map
        self.row_filters: list = []
        self.source_columns: dict = {}


class _PartlyReadableEntity:
    """One readable row filter beside a raising one; one readable auxiliary value
    beside a raising one — the mixed case that proves the skip is per-item."""

    def __init__(self) -> None:
        self.field_map: dict = {}
        self.row_filters = [_Raises(), _RowFilter("School Number")]
        self.source_columns = {"bad_role": _Raises(), "good_role": "Course Code"}


class _RowFilter:
    def __init__(self, column: str) -> None:
        self.column = column


class TestTotality:
    """Every function here is TOTAL: an unreadable shape yields a SHORTER derivation,
    never an exception in front of an admin mid-setup. Each case is paired with a
    readable neighbour so an empty result is never merely the absence of a walk.
    """

    def test_an_unreadable_config_yields_no_expectations(self):
        assert expected_columns(_HostileConfig()) == ()  # type: ignore[arg-type]

    def test_an_unreadable_entity_is_skipped_and_its_readable_neighbour_is_not(self):
        config = _HostileConfig()
        config.mappings = {"Broken": _HostileEntity(), "Students": _ReadableEntity()}
        config.active_entities = lambda: {"Broken", "Students"}  # type: ignore[method-assign]

        expected = expected_columns(config)  # type: ignore[arg-type]

        assert [(item.entity, item.source_column) for item in expected] == [("Students", "Legal Surname")]

    def test_an_unreadable_row_filter_and_auxiliary_value_are_skipped(self):
        config = _HostileConfig()
        config.mappings = {"Students": _PartlyReadableEntity()}
        config.active_entities = lambda: {"Students"}  # type: ignore[method-assign]

        expected = expected_columns(config)  # type: ignore[arg-type]

        # The readable row filter survives; the raising one and the raising
        # auxiliary value contribute nothing.
        assert [(item.output_field, item.source_column) for item in expected] == [
            (ROW_FILTER_FIELD, "School Number"),
            ("good_role", "Course Code"),
        ]

    def test_an_unparseable_email_template_and_derived_dates_contribute_nothing(self):
        from src.config.models import FieldEmailFormat

        spec = FieldEmailFormat.model_construct(format="{unclosed", sanitize=False, derived_dates=_Raises())
        config = _HostileConfig()
        config.mappings = {"Students": _ReadableEntity(field_map={"Email Address": spec})}
        config.active_entities = lambda: {"Students"}  # type: ignore[method-assign]

        assert expected_columns(config) == ()  # type: ignore[arg-type]

    def test_a_positional_placeholder_names_no_column(self):
        from src.config.models import FieldEmailFormat

        spec = FieldEmailFormat(format="{0}{}{legal surname}@example.org")
        config = _HostileConfig()
        config.mappings = {"Students": _ReadableEntity(field_map={"Email Address": spec})}
        config.active_entities = lambda: {"Students"}  # type: ignore[method-assign]

        assert [item.source_column for item in expected_columns(config)] == ["legal surname"]

    def test_an_unreadable_observation_makes_no_claim_and_no_crash(self):
        class _HostileMapping(dict):
            def values(self):  # noqa: D102
                raise RuntimeError("unreadable observation")

        expected = (ExpectedColumn(entity="Students", output_field="Last Name", source_column="Legal Surname"),)
        assert missing_columns(expected, _HostileMapping()) == ()

        config = _HostileConfig()
        config.mappings = {"Students": _ReadableEntity()}
        config.active_entities = lambda: {"Students"}  # type: ignore[method-assign]
        report = preflight_report(config, _HostileMapping())  # type: ignore[arg-type]
        assert report.missing == () and report.checked_files == 0
        assert report.checked_columns == 1  # the expectations WERE derived

    def test_a_column_list_that_raises_mid_iteration_degrades_to_what_it_yielded(self):
        def _explodes():
            yield "student number"
            raise RuntimeError("truncated header row")

        expected = (
            ExpectedColumn(entity="Students", output_field="User ID", source_column="Student Number"),
            ExpectedColumn(entity="Students", output_field="Last Name", source_column="Legal Surname"),
        )
        result = missing_columns(expected, {"Demo.txt": _explodes()})  # type: ignore[dict-item]
        assert [item.source_column for item in result] == ["Legal Surname"]

    def test_a_value_whose_string_conversion_raises_names_no_column(self):
        config = _HostileConfig()
        config.mappings = {"Students": _ReadableEntity(field_map={"Last Name": _Raises()})}
        config.active_entities = lambda: {"Students"}  # type: ignore[method-assign]

        assert expected_columns(config) == ()  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The run-time source observation — own files, per entity (plan 0053 S6)
# ---------------------------------------------------------------------------

# The myedbc file set with every column its active entities map — the all-present baseline the
# per-entity twins below each take ONE column away from. Headers as the extractor observes them
# (normalised); the keys are the config's own spelling of each file.
_MYEDBC_OBSERVED: dict[str, tuple[str, ...]] = {
    "StudentDemographicInformation.txt": (
        "student number",
        "legal first name",
        "legal surname",
        "date of birth",
        "grade",
        "school number",
        "homeroom",
        "next school code",
        "usual first name",
        "usual surname",
        "student email address",
    ),
    "StaffInformationEnhanced.txt": (
        "teacher id",
        "first name",
        "last name",
        "email address",
        "teaching staff",
        "school number",
    ),
    "EmergencyContactInformation.txt": ("student number", "first name", "last name", "email address"),
    "StudentSchedule.txt": (
        "student id",
        "teacher id",
        "master timetable id",
        "school number",
        "grade",
        "primary teacher",
        "teacher name",
        "course title",
        "section letter",
    ),
    "CourseInformation.txt": ("school number", "course code", "title"),
    "ClassInformationEnh.txt": ("school number", "teacher id", "master timetable id"),
}


def _observed_without(filename: str, *columns: str) -> dict[str, tuple[str, ...]]:
    observed = dict(_MYEDBC_OBSERVED)
    observed[filename] = tuple(c for c in observed[filename] if c not in columns)
    return observed


class TestMissingColumnsByEntity:
    """The OWN-FILE claim (P11): an entity's mapped column absent from the file(s) IT reads.
    Every negative ("no claim") is paired with a positive that proves the same input DOES
    produce a claim once the one condition under test is removed."""

    def test_the_all_present_baseline_makes_no_claim(self):
        assert missing_columns_by_entity(load_config("myedbc"), _MYEDBC_OBSERVED) == {}

    def test_the_sd67_shape_names_family_s_missing_email_column_in_config_spelling(self):
        """SD67 2026-09-22 / SD51 2026-09-18: Family maps ``Email Address``; its contacts
        file lacks it — every contact is then excluded for a blank email."""
        config = load_config("sd67myedbc")
        assert "Family" not in missing_columns_by_entity(config, _renamed_demographic(_MYEDBC_OBSERVED))

        observed = _renamed_demographic(_observed_without("EmergencyContactInformation.txt", "email address"))
        assert missing_columns_by_entity(config, observed)["Family"] == ("Email Address",)

    def test_a_column_in_another_entity_s_file_does_not_hide_a_miss_in_this_one(self):
        """The masking bug the file-agnostic union has: Staff's file carries
        ``email address``, so ``missing_columns`` reports NOTHING for Family's miss."""
        observed = _observed_without("EmergencyContactInformation.txt", "email address")
        assert "email address" in observed["StaffInformationEnhanced.txt"]
        config = load_config("myedbc")

        assert missing_columns_by_entity(config, observed) == {"Family": ("Email Address",)}
        # The file-agnostic twin cannot see it — the reason S6 compares OWN files.
        assert "Email Address" not in {m.source_column for m in missing_columns(expected_columns(config), observed)}

    def test_no_claim_for_an_entity_one_of_whose_files_was_not_observed(self):
        """Soundness: Classes reads five files; with ClassInformation absent (an empty
        tuple — what the extractor yields for a file not on disk) nothing is claimed for
        Classes, even though a column it maps is missing. The twin, all five observed,
        claims it."""
        missing_title = _observed_without("StudentSchedule.txt", "course title")
        config = load_config("myedbc")
        assert missing_columns_by_entity(config, missing_title) == {"Classes": ("Course Title",)}

        unobserved = dict(missing_title)
        unobserved["ClassInformationEnh.txt"] = ()
        assert "Classes" not in missing_columns_by_entity(config, unobserved)

    def test_a_file_left_out_of_the_observation_entirely_is_not_observed(self):
        missing_title = _observed_without("StudentSchedule.txt", "course title")
        del missing_title["ClassInformationEnh.txt"]
        assert "Classes" not in missing_columns_by_entity(load_config("myedbc"), missing_title)

    def test_nothing_observed_makes_no_claim_at_all(self):
        config = load_config("myedbc")
        assert missing_columns_by_entity(config, {}) == {}
        assert missing_columns_by_entity(config, dict.fromkeys(_MYEDBC_OBSERVED, ())) == {}

    def test_a_row_filter_column_is_observed(self):
        """SD60's guardian ``row_filters`` column is a mapped read of Family's OWN file."""
        config = load_config("sd60myedbc")
        family_files = tuple(config.mappings["Family"].source_files.values())
        observed = {name: ("student number", "first name", "last name", "email address") for name in family_files}
        findings = missing_columns_by_entity(config, observed)
        assert "Parent Auth / Guardian" in findings["Family"]
        observed = {name: (*cols, "parent auth / guardian") for name, cols in observed.items()}
        assert "Parent Auth / Guardian" not in missing_columns_by_entity(config, observed).get("Family", ())

    def test_source_columns_are_excluded_but_the_same_name_in_the_field_map_is_not(self):
        """``source_columns`` are documented CROSS-file auxiliary reads: never an own-file
        claim. The twin maps the SAME header through the field_map and is claimed."""
        aux_only = _entity_config(
            field_map={"User ID": "Student Number"}, source_columns={"full_course_code": "Course Code Full"}
        )
        observed = {"Demo.txt": ("student number",)}
        assert missing_columns_by_entity(aux_only, observed) == {}

        mapped = _entity_config(field_map={"User ID": "Student Number", "Code": "Course Code Full"})
        assert missing_columns_by_entity(mapped, observed) == {"Students": ("Course Code Full",)}

    def test_every_expectation_carries_its_origin(self):
        origins = {(item.output_field, item.origin) for item in expected_columns(_synthetic_config())}
        assert ("User ID", ExpectationOrigin.FIELD_MAP) in origins
        assert (ROW_FILTER_FIELD, ExpectationOrigin.ROW_FILTER) in origins
        assert ("full_course_code", ExpectationOrigin.SOURCE_COLUMN) in origins
        assert ExpectedColumn("E", "F", "C").origin is ExpectationOrigin.FIELD_MAP  # the reporting default

    def test_enrollments_compares_against_every_file_because_it_also_reads_classinformation(self):
        """``Teacher ID`` present ONLY in ClassInformation (a Classes file, read by the
        co-teacher rows) is not an Enrollments miss; absent everywhere, it is."""
        nowhere = {name: tuple(c for c in cols if c != "teacher id") for name, cols in _MYEDBC_OBSERVED.items()}
        only_in_class_info = {**nowhere, "ClassInformationEnh.txt": _MYEDBC_OBSERVED["ClassInformationEnh.txt"]}
        assert "teacher id" in only_in_class_info["ClassInformationEnh.txt"]
        config = load_config("myedbc")
        assert "Enrollments" not in missing_columns_by_entity(config, only_in_class_info)

        assert missing_columns_by_entity(config, nowhere)["Enrollments"] == ("Teacher ID",)

    def test_an_id_role_pair_column_is_listed_once_in_config_spelling(self):
        """Enrollments maps ``Student ID`` twice (User ID and Role): one finding."""
        observed = _observed_without("StudentSchedule.txt", "student id")
        assert missing_columns_by_entity(load_config("myedbc"), observed)["Enrollments"] == ("Student ID",)

    def test_student_attendance_placeholders_are_never_claimed(self):
        """Its field_map values are placeholders (it never calls ``apply_field_map``).
        Twin: an OWN_FILES entity over the same empty-of-them file IS claimed."""
        config = load_config("sd51attendance")
        files = tuple(config.mappings["StudentAttendance"].source_files.values())
        observed = dict.fromkeys(files, ("unrelated column",))
        assert missing_columns_by_entity(config, observed) == {}

        family_like = _entity_config(field_map={"School Number": "School Number"})
        assert missing_columns_by_entity(family_like, {"Demo.txt": ("unrelated column",)}) == {
            "Students": ("School Number",)
        }

    def test_a_headerless_file_matches_its_declared_headers(self, tmp_path: Path):
        """SD40's schedule is headerless: the extractor injects the DECLARED names, so the
        mapped columns match by construction and nothing is claimed for them."""
        from src.etl.extractor import DataExtractor
        from src.etl.pipeline import extract_required_files, observed_input_columns

        _DISTRICT_SETUP["sd40myedbc"](tmp_path)
        config = load_config("sd40myedbc")
        headers: dict[str, list[str]] = {}
        for entity_cfg in config.to_raw_dict()["mappings"].values():
            headers.update(entity_cfg.get("headers", {}))
        assert headers, "sd40 declares headers for a headerless file"
        data = DataExtractor(str(tmp_path)).load_data(extract_required_files(config), file_headers=headers)
        assert missing_columns_by_entity(config, observed_input_columns(data)) == {}

    def test_an_unlisted_entity_defaults_to_its_own_files(self):
        assert observation_scope("SomethingInvented") is ObservationScope.OWN_FILES
        assert observation_scope("StudentAttendance") is ObservationScope.NO_CLAIM


def _renamed_demographic(observed: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """SD67 reads its ENHANCED demographic export under its own filename."""
    renamed = dict(observed)
    renamed["StudentDemographicEnh.txt"] = renamed.pop("StudentDemographicInformation.txt")
    return renamed


def _entity_config(*, field_map: dict, source_columns: dict | None = None) -> MappingConfig:
    return MappingConfig(
        version="1.0",
        sis="SyntheticSIS",
        district_name="Synthetic",
        global_config={"enabled_entities": ["Students"]},
        mappings={
            "Students": {
                "source_files": {"student_demographic": "Demo.txt"},
                "field_map": field_map,
                "source_columns": source_columns or {},
            }
        },
    )


class TestObservationScopeIsComplete:
    def test_every_registry_entity_declares_where_its_field_map_is_read(self):
        from src.etl.transformers.registry import TRANSFORMER_REGISTRY

        assert set(OBSERVATION_SCOPE) == set(TRANSFORMER_REGISTRY)
        assert TRANSFORMER_REGISTRY, "non-vacuity: the registry is not empty"

    def test_doctored_a_missing_entry_is_red(self):
        from src.etl.transformers.registry import TRANSFORMER_REGISTRY

        doctored = {k: v for k, v in OBSERVATION_SCOPE.items() if k != "Enrollments"}
        assert set(doctored) != set(TRANSFORMER_REGISTRY)


class TestMissingColumnsByEntityIsTotal:
    def test_an_unreadable_config_yields_nothing(self):
        assert missing_columns_by_entity(_HostileConfig(), _MYEDBC_OBSERVED) == {}  # type: ignore[arg-type]

    def test_an_unreadable_observation_yields_nothing(self):
        class _HostileMapping(dict):
            def items(self):  # noqa: D102
                raise RuntimeError("unreadable observation")

        assert missing_columns_by_entity(load_config("myedbc"), _HostileMapping()) == {}

    def test_a_header_row_that_raises_part_way_is_not_observed(self):
        """A partial header row would make every column it did not yield look missing,
        so the file counts as UNOBSERVED. Twin: the same row read in full claims the one
        genuinely missing column."""

        def _explodes():
            yield "student number"
            raise RuntimeError("truncated header row")

        config = load_config("myedbc")
        observed: dict = _observed_without("EmergencyContactInformation.txt", "email address")
        assert missing_columns_by_entity(config, observed)["Family"] == ("Email Address",)
        observed["EmergencyContactInformation.txt"] = _explodes()
        assert "Family" not in missing_columns_by_entity(config, observed)

    @pytest.mark.parametrize("junk", [None, "a string is not a header row", 5])
    def test_a_non_sequence_header_row_is_not_observed(self, junk):
        observed: dict = _observed_without("EmergencyContactInformation.txt", "email address")
        observed["EmergencyContactInformation.txt"] = junk
        assert "Family" not in missing_columns_by_entity(load_config("myedbc"), observed)


# The findings the observation makes over every bundled config's CONTRACT FIXTURE
# (`tests/test_contract.py` builders), recorded rather than hidden. Both are fixture
# artefacts, not district faults: the shared demographic builder writes no `Next school code`
# (the base's PreRegSchoolCode source), and the rostering CourseInformation builder carries
# `Title` where the base's class-name config names `Course Title` (DECISIONS 2026-09-24, S6).
_NEXT_SCHOOL = {"Students": ("Next school code",)}
_NEXT_SCHOOL_AND_TITLE = {"Students": ("Next school code",), "Classes": ("Course Title",)}
_FIXTURE_FINDINGS: dict[str, dict[str, tuple[str, ...]]] = {
    "myedbc": _NEXT_SCHOOL_AND_TITLE,
    "sd40myedbc": {},
    "sd48myedbc": _NEXT_SCHOOL_AND_TITLE,
    "sd51myedbc": _NEXT_SCHOOL_AND_TITLE,
    "sd54myedbc": _NEXT_SCHOOL,
    "sd60myedbc": {},
    "sd74myedbc": _NEXT_SCHOOL,
    "sd51attendance": {},
    "sd83myedbc": _NEXT_SCHOOL_AND_TITLE,
    "sd27myedbc": _NEXT_SCHOOL_AND_TITLE,
    "sd67myedbc": _NEXT_SCHOOL_AND_TITLE,
    "sd69myedbc": _NEXT_SCHOOL_AND_TITLE,
    "sd71myedbc": _NEXT_SCHOOL_AND_TITLE,
    "sd75myedbc": _NEXT_SCHOOL_AND_TITLE,
    "sd10myedbc": _NEXT_SCHOOL_AND_TITLE,
    "sd38myedbc": _NEXT_SCHOOL,
    "unitychristianmyedbc": _NEXT_SCHOOL_AND_TITLE,
    "mbp_all": _NEXT_SCHOOL_AND_TITLE,
    "mbp_core": _NEXT_SCHOOL,
    "mbponly": {},
}


def _fixture_findings(sis: str, input_dir: Path) -> dict[str, tuple[str, ...]]:
    from src.etl.extractor import DataExtractor
    from src.etl.pipeline import extract_required_files, observed_input_columns

    config = load_config(sis)
    headers: dict[str, list[str]] = {}
    for entity_cfg in config.to_raw_dict()["mappings"].values():
        headers.update(entity_cfg.get("headers", {}))
    data = DataExtractor(str(input_dir)).load_data(extract_required_files(config), file_headers=headers)
    return missing_columns_by_entity(config, observed_input_columns(data))


class TestTheSweepOverEveryBundledFixture:
    def test_the_recorded_table_covers_every_swept_config(self):
        assert set(_FIXTURE_FINDINGS) == set(_DISTRICT_SETUP)

    @pytest.mark.parametrize("sis", sorted(_DISTRICT_SETUP))
    def test_each_fixture_makes_exactly_its_recorded_findings(self, sis: str, tmp_path: Path):
        _DISTRICT_SETUP[sis](tmp_path)
        assert _fixture_findings(sis, tmp_path) == _FIXTURE_FINDINGS[sis]

    def test_doctored_dropping_one_mapped_column_adds_exactly_one_finding(self, tmp_path: Path):
        """Non-vacuity: the sweep is not green because it cannot see anything."""
        import pandas as pd

        _DISTRICT_SETUP["myedbc"](tmp_path)
        demographic = tmp_path / "StudentDemographicInformation.txt"
        frame = pd.read_csv(demographic, dtype=str)
        assert "Legal Surname" in frame.columns
        frame.drop(columns=["Legal Surname"]).to_csv(demographic, index=False)

        findings = _fixture_findings("myedbc", tmp_path)
        assert findings["Students"] == ("Legal Surname", "Next school code")
        assert {k: v for k, v in findings.items() if k != "Students"} == {"Classes": ("Course Title",)}
