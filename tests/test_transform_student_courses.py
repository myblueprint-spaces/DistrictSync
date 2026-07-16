"""Integration tests for the StudentCourses entity transformation.

StudentCourses ports the PowerShell GDEprocessingscript.ps1 logic
originally written for an SD62 use case but now generalized as a
myBlueprint+ tier entity. Each test exercises a specific rule from that
script so a future divergence shows up immediately.
"""

import pandas as pd
import pytest

from src.etl.transformer import DataTransformer

# The numeric early-grade exclusion is derived from course_start_grade
# (default 10) by the transformer, not listed as a literal pattern.
MYEDBC_PATTERNS = [r"^.{5}-K", r"^X", r"^ATT"]
MYEDBC_FLAVORS = ["HUB", "HOL", "DL", "---"]

STUDENT_COURSES_FIELD_MAP = {
    "Student ID": {"value": ""},
    "Course Code": {"value": ""},
    "IntegrationId": {"value": ""},
    "Course Name": {"value": ""},
    "Completion Date": {"value": ""},
    "Final Mark": {"value": ""},
    "Credits Earned": {"value": ""},
    "Alternate Course Code": {"value": ""},
    "Potential Credits Earned": {"value": ""},
    "Term Grade": {"value": ""},
}


@pytest.fixture
def sc_mapping():
    return {
        "source_files": {
            "course_info": "CourseInformation.txt",
            "course_history": "StudentCourseHistory.txt",
            "course_selection": "StudentCourseSelection.txt",
        },
        "field_map": STUDENT_COURSES_FIELD_MAP,
    }


@pytest.fixture
def myedbc_global_config():
    return {
        "excluded_course_code_patterns": MYEDBC_PATTERNS,
        "excluded_course_flavors": MYEDBC_FLAVORS,
    }


@pytest.fixture
def course_info_df():
    """Master course catalog used for title + credit lookups."""
    return pd.DataFrame(
        {
            "course code": ["MAT10", "ENG12", "SCI09", "ENG11", "MAT10HUB"],
            "school number": ["6262013", "6262013", "6262013", "6262013", "6262013"],
            "title": ["Math 10", "English 12", "Science 9", "English 11", "Math 10 HUB"],
            "credit value": [4, 4, 4, 4, 4],
        }
    )


def _history(rows):
    """Build a history DataFrame with the columns the transformer expects."""
    return pd.DataFrame(rows)


def _selection(rows):
    return pd.DataFrame(rows)


def _run(transformer, sc_mapping, global_config, history=None, selection=None, info=None):
    raw_data = {
        "CourseInformation.txt": info if info is not None else pd.DataFrame(),
        "StudentCourseHistory.txt": history if history is not None else pd.DataFrame(),
        "StudentCourseSelection.txt": selection if selection is not None else pd.DataFrame(),
    }
    # The pipeline passes a non-empty primary_df, but the transformer ignores
    # it and re-loads via get_source_file — pass any non-empty placeholder.
    primary = info if (info is not None and not info.empty) else pd.DataFrame({"_": [0]})
    return transformer.transform(primary, sc_mapping, "StudentCourses", raw_data, global_config)


@pytest.fixture
def transformer():
    t = DataTransformer()
    t.set_school_year(2025, "08-25", "07-25")
    return t


class TestHistoryPass:
    def test_basic_passed_history_row(self, transformer, sc_mapping, myedbc_global_config, course_info_df):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["Student ID"] == "S001"
        assert row["Course Code"] == "MAT10"
        assert row["Course Name"] == "Math 10"
        assert row["Final Mark"] == "85"
        assert row["Completion Date"] == "2025-01-30"
        assert row["Credits Earned"] == 4
        assert row["Potential Credits Earned"] == 4

    def test_failed_history_row_has_null_credits_but_potential_populated(
        self, transformer, sc_mapping, myedbc_global_config, course_info_df
    ):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "30",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)
        row = result.iloc[0]
        assert row["Credits Earned"] is None
        # Potential is populated regardless of pass/fail
        assert row["Potential Credits Earned"] == 4

    def test_w_mark_skipped(self, transformer, sc_mapping, myedbc_global_config, course_info_df):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "W",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)
        assert result.empty

    def test_pattern_excluded_code_skipped(self, transformer, sc_mapping, myedbc_global_config, course_info_df):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "XGEN12",
                    "full course code": "XGEN12",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                },
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "ATT--AM",
                    "full course code": "ATT--AM",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                },
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)
        assert result.empty

    def test_section_stripped_from_full_course_code(
        self, transformer, sc_mapping, myedbc_global_config, course_info_df
    ):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10-A",
                    "section": "A",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)
        assert result.iloc[0]["Course Code"] == "MAT10"

    def test_flavor_truncation_applied(self, transformer, sc_mapping, myedbc_global_config, course_info_df):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10HUB-X",
                    "section": "X",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)
        # Section "X" stripped -> "MAT10HUB", flavor truncation -> "MAT10HU"
        assert result.iloc[0]["Course Code"] == "MAT10HU"

    def test_fallback_credits_when_no_courseinfo_match_and_passed(
        self, transformer, sc_mapping, myedbc_global_config, course_info_df
    ):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "UNKNOWN99",
                    "full course code": "UNKNOWN99",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)
        row = result.iloc[0]
        assert row["Course Name"] == ""
        assert row["Credits Earned"] == 4  # pass fallback
        assert row["Potential Credits Earned"] == 4

    def test_courseinfo_prefix_match_when_exact_misses(self, transformer, sc_mapping, myedbc_global_config):
        """If exact (cleaned, school) miss but a 7-char prefix matches CourseInfo."""
        info = pd.DataFrame(
            {
                "course code": ["MAT10HU"],  # exactly 7 chars
                "school number": ["6299999"],  # different school than history
                "title": ["Math 10 HUB Prefix"],
                "credit value": [3],
            }
        )
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",  # not in CourseInfo
                    "course code": "MAT10",
                    "full course code": "MAT10HUB",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=info)
        row = result.iloc[0]
        assert row["Course Code"] == "MAT10HU"
        assert row["Course Name"] == "Math 10 HUB Prefix"
        assert row["Credits Earned"] == 3


class TestSelectionPass:
    def test_selection_with_no_history_included(self, transformer, sc_mapping, myedbc_global_config, course_info_df):
        selection = _selection(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "ENG12",
                    "dl start date": "15-Sep-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, selection=selection, info=course_info_df)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["Course Code"] == "ENG12"
        assert row["Final Mark"] == ""
        assert row["Completion Date"] == ""
        assert row["Credits Earned"] == ""
        assert row["Potential Credits Earned"] == 4
        assert row["Course Name"] == "English 12"

    def test_selection_excluded_when_already_passed(
        self, transformer, sc_mapping, myedbc_global_config, course_info_df
    ):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        selection = _selection(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "dl start date": "15-Sep-2025",
                }
            ]
        )
        result = _run(
            transformer, sc_mapping, myedbc_global_config, history=history, selection=selection, info=course_info_df
        )
        # Only history row survives — no selection row for MAT10
        assert len(result) == 1
        assert result.iloc[0]["Final Mark"] == "85"

    def test_selection_excluded_when_in_progress(self, transformer, sc_mapping, myedbc_global_config, course_info_df):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "",  # no final mark
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "",  # null completion → in progress
                }
            ]
        )
        selection = _selection(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "dl start date": "15-Sep-2025",
                }
            ]
        )
        result = _run(
            transformer, sc_mapping, myedbc_global_config, history=history, selection=selection, info=course_info_df
        )
        # History row kept (in progress), selection row dropped
        assert len(result) == 1
        assert result.iloc[0]["Completion Date"] == ""
        assert result.iloc[0]["Final Mark"] == ""

    def test_selection_included_on_retake_with_newer_start(
        self, transformer, sc_mapping, myedbc_global_config, course_info_df
    ):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "30",  # failed
                    "dl start date": "15-Sep-2023",
                    "dl completion date": "30-Jan-2024",
                }
            ]
        )
        selection = _selection(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "dl start date": "15-Sep-2025",  # newer than history start
                }
            ]
        )
        result = _run(
            transformer, sc_mapping, myedbc_global_config, history=history, selection=selection, info=course_info_df
        )
        # Both rows: failed history + retake selection
        assert len(result) == 2
        codes_and_marks = [(r["Course Code"], r["Final Mark"]) for _, r in result.iterrows()]
        assert ("MAT10", "30") in codes_and_marks
        assert ("MAT10", "") in codes_and_marks

    def test_selection_excluded_when_older_or_same_start(
        self, transformer, sc_mapping, myedbc_global_config, course_info_df
    ):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "30",
                    "dl start date": "15-Sep-2025",
                    "dl completion date": "30-Jan-2026",
                }
            ]
        )
        selection = _selection(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "dl start date": "15-Sep-2024",  # older
                }
            ]
        )
        result = _run(
            transformer, sc_mapping, myedbc_global_config, history=history, selection=selection, info=course_info_df
        )
        assert len(result) == 1
        assert result.iloc[0]["Final Mark"] == "30"

    def test_selection_included_when_history_has_null_start_date(
        self, transformer, sc_mapping, myedbc_global_config, course_info_df
    ):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "30",
                    "dl start date": "",  # null
                    "dl completion date": "30-Jan-2024",
                }
            ]
        )
        selection = _selection(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "dl start date": "15-Sep-2025",
                }
            ]
        )
        result = _run(
            transformer, sc_mapping, myedbc_global_config, history=history, selection=selection, info=course_info_df
        )
        # Fallback rule: null start date in history → include selection
        assert len(result) == 2

    def test_selection_included_when_selection_start_is_null(
        self, transformer, sc_mapping, myedbc_global_config, course_info_df
    ):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "30",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        selection = _selection(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "dl start date": "",  # null
                }
            ]
        )
        result = _run(
            transformer, sc_mapping, myedbc_global_config, history=history, selection=selection, info=course_info_df
        )
        assert len(result) == 2

    def test_selection_pattern_excluded(self, transformer, sc_mapping, myedbc_global_config, course_info_df):
        selection = _selection(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "XGEN12",
                    "dl start date": "15-Sep-2025",
                },
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "ATT--AM",
                    "dl start date": "15-Sep-2025",
                },
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, selection=selection, info=course_info_df)
        assert result.empty

    def test_selection_title_uses_raw_code_not_cleaned(self, transformer, sc_mapping, myedbc_global_config):
        """PowerShell selection-pass title lookup uses the raw course code, not the cleaned one."""
        info = pd.DataFrame(
            {
                "course code": ["MAT10HUB"],
                "school number": ["6262013"],
                "title": ["Math 10 HUB (raw lookup)"],
                "credit value": [4],
            }
        )
        selection = _selection(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10HUB",  # raw includes flavor
                    "dl start date": "15-Sep-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, selection=selection, info=info)
        # Code is flavor-truncated, but title comes from the raw-code lookup
        assert result.iloc[0]["Course Code"] == "MAT10HU"
        assert result.iloc[0]["Course Name"] == "Math 10 HUB (raw lookup)"


class TestOutputShape:
    def test_output_columns_exact_order(self, transformer, sc_mapping, myedbc_global_config, course_info_df):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)
        assert list(result.columns) == [
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
        ]

    def test_blank_columns_are_blank(self, transformer, sc_mapping, myedbc_global_config, course_info_df):
        history = _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)
        row = result.iloc[0]
        assert row["IntegrationId"] == ""
        assert row["Alternate Course Code"] == ""
        assert row["Term Grade"] == ""

    def test_sorted_by_student_id_then_completion_date(
        self, transformer, sc_mapping, myedbc_global_config, course_info_df
    ):
        history = _history(
            [
                {
                    "student number": "S002",
                    "school number": "6262013",
                    "course code": "ENG12",
                    "full course code": "ENG12",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                },
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                },
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "ENG11",
                    "full course code": "ENG11",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2023",
                    "dl completion date": "15-Jun-2024",
                },
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)
        # S001 rows first, then S002. Within S001, "15-Jun-2024" < "30-Jan-2025" lexically.
        student_ids = list(result["Student ID"])
        assert student_ids == ["S001", "S001", "S002"]

    def test_empty_inputs_returns_empty_with_correct_columns(self, transformer, sc_mapping, myedbc_global_config):
        # Pass non-empty info to keep the pipeline's primary_df check happy in real use,
        # but here we test the transformer directly with empty history + selection.
        info = pd.DataFrame(
            {"course code": ["MAT10"], "school number": ["6262013"], "title": ["Math 10"], "credit value": [4]}
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, info=info)
        assert result.empty
        assert list(result.columns) == [
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
        ]


class TestColumnNormalization:
    def test_uppercase_source_columns_normalised(self, transformer, sc_mapping, myedbc_global_config):
        info = pd.DataFrame(
            {
                "Course Code": ["MAT10"],
                "School Number": ["6262013"],
                "Title": ["Math 10"],
                "Credit Value": [4],
            }
        )
        history = pd.DataFrame(
            {
                "Student Number": ["S001"],
                "School Number": ["6262013"],
                "Course Code": ["MAT10"],
                "Full Course Code": ["MAT10"],
                "Section": [""],
                "Final Mark": ["85"],
                "DL Start Date": ["15-Sep-2024"],
                "DL Completion Date": ["30-Jan-2025"],
            }
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=info)
        assert len(result) == 1
        assert result.iloc[0]["Course Name"] == "Math 10"


class TestStudentCoursesStartGrade:
    """course_start_grade gates which grade levels reach StudentCourses.

    Grades are read from the 6th-7th chars of the course code ("0X" for
    single-digit grades). Default 10 keeps only 10-12; 8 or 9 admits those
    grades. Applies to both the history and selection passes.
    """

    @staticmethod
    def _history_rows():
        return _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": code,
                    "full course code": code,
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                }
                for code in ("MAT--07", "MAT--08", "MAT--09", "MEN--10")
            ]
        )

    def _run_grade(self, transformer, sc_mapping, start_grade):
        gc = {"excluded_course_code_patterns": MYEDBC_PATTERNS, "excluded_course_flavors": MYEDBC_FLAVORS}
        if start_grade is not None:
            gc["course_start_grade"] = start_grade
        result = _run(transformer, sc_mapping, gc, history=self._history_rows())
        return set(result["Course Code"])

    def test_default_keeps_only_senior(self, transformer, sc_mapping):
        assert self._run_grade(transformer, sc_mapping, None) == {"MEN--10"}

    def test_start_grade_9(self, transformer, sc_mapping):
        assert self._run_grade(transformer, sc_mapping, 9) == {"MAT--09", "MEN--10"}

    def test_start_grade_8(self, transformer, sc_mapping):
        assert self._run_grade(transformer, sc_mapping, 8) == {"MAT--08", "MAT--09", "MEN--10"}

    def test_start_grade_8_applies_to_selection_pass(self, transformer, sc_mapping):
        selection = _selection(
            [
                {"student number": "S002", "school number": "6262013", "course code": code, "dl start date": ""}
                for code in ("MAT--07", "MAT--08", "MEN--10")
            ]
        )
        gc = {
            "excluded_course_code_patterns": MYEDBC_PATTERNS,
            "excluded_course_flavors": MYEDBC_FLAVORS,
            "course_start_grade": 8,
        }
        result = _run(transformer, sc_mapping, gc, selection=selection)
        assert set(result["Course Code"]) == {"MAT--08", "MEN--10"}


class TestStudentCoursesConfigIntegration:
    def test_base_defines_studentcourses_template(self):
        from src.config.loader import load_config

        cfg = load_config("myedbc")
        assert "StudentCourses" in cfg.mappings
        # ...but not enabled by default
        assert "StudentCourses" not in cfg.global_config.enabled_entities

    def test_studentcourses_field_order(self):
        from src.config.loader import load_config

        cfg = load_config("myedbc")
        expected = [
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
        ]
        assert list(cfg.mappings["StudentCourses"].field_map.keys()) == expected

    def test_studentcourses_source_files_present(self):
        from src.config.loader import load_config

        cfg = load_config("myedbc")
        sources = cfg.mappings["StudentCourses"].source_files
        assert sources["course_history"] == "StudentCourseHistory.txt"
        assert sources["course_selection"] == "StudentCourseSelection.txt"
        assert sources["course_info"] == "CourseInformation.txt"

    def test_mbp_all_enables_studentcourses(self):
        from src.config.loader import load_config

        cfg = load_config("mbp_all")
        assert "StudentCourses" in cfg.global_config.enabled_entities

    def test_mbp_core_enables_studentcourses(self):
        from src.config.loader import load_config

        cfg = load_config("mbp_core")
        assert "StudentCourses" in cfg.global_config.enabled_entities

    def test_registry_returns_dedicated_transformer(self):
        """StudentCourses must resolve to its custom transformer, not DefaultTransformer."""
        from src.etl.transformers.registry import get_transformer
        from src.etl.transformers.student_courses import StudentCoursesTransformer

        t = get_transformer("StudentCourses")
        assert isinstance(t, StudentCoursesTransformer)


class TestRegressionScenario:
    """Multi-row scenario combining several rules — the kind of mixed input a real GDE drop produces."""

    def test_mixed_history_and_selection(self, transformer, sc_mapping, myedbc_global_config):
        info = pd.DataFrame(
            {
                "course code": ["MAT10", "ENG12", "SCI09", "ENG11"],
                "school number": ["6262013"] * 4,
                "title": ["Math 10", "English 12", "Science 9", "English 11"],
                "credit value": [4, 4, 4, 4],
            }
        )
        history = _history(
            [
                # S001 passes MAT10
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                },
                # S001 fails ENG11
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "ENG11",
                    "full course code": "ENG11",
                    "section": "",
                    "final mark": "40",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                },
                # S001 withdrew SCI09 (W) — should be skipped
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "SCI09",
                    "full course code": "SCI09",
                    "section": "",
                    "final mark": "W",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "",
                },
                # S002 has ENG12 in-progress
                {
                    "student number": "S002",
                    "school number": "6262013",
                    "course code": "ENG12",
                    "full course code": "ENG12",
                    "section": "",
                    "final mark": "",
                    "dl start date": "15-Sep-2025",
                    "dl completion date": "",
                },
            ]
        )
        selection = _selection(
            [
                # S001 retaking ENG11 (failed → newer start → include)
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "ENG11",
                    "dl start date": "15-Sep-2026",
                },
                # S001 selecting SCI09 (only W in history, no other record → no metadata → include)
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "SCI09",
                    "dl start date": "15-Sep-2026",
                },
                # S001 trying to retake passed MAT10 (excluded)
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "dl start date": "15-Sep-2026",
                },
                # S002 trying to add ENG12 (already in progress → excluded)
                {
                    "student number": "S002",
                    "school number": "6262013",
                    "course code": "ENG12",
                    "dl start date": "15-Sep-2026",
                },
                # S002 fresh ENG11 selection (no history → include)
                {
                    "student number": "S002",
                    "school number": "6262013",
                    "course code": "ENG11",
                    "dl start date": "15-Sep-2026",
                },
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, history=history, selection=selection, info=info)

        # S001 rows: MAT10 (history pass) + ENG11 (history fail) + ENG11 (retake selection) + SCI09 (selection)
        s001 = result[result["Student ID"] == "S001"]
        s001_codes_and_marks = sorted([(r["Course Code"], r["Final Mark"]) for _, r in s001.iterrows()])
        assert s001_codes_and_marks == sorted(
            [
                ("MAT10", "85"),
                ("ENG11", "40"),
                ("ENG11", ""),  # retake
                ("SCI09", ""),  # selection (W was skipped)
            ]
        )

        # S002 rows: ENG12 (in-progress history) + ENG11 (fresh selection)
        s002 = result[result["Student ID"] == "S002"]
        s002_codes_and_marks = sorted([(r["Course Code"], r["Final Mark"]) for _, r in s002.iterrows()])
        assert s002_codes_and_marks == sorted([("ENG12", ""), ("ENG11", "")])


class TestNonNumericMarkDataError:
    """A non-blank, non-"W", non-numeric Final Mark (letter grades, "Pass") is
    scored as not-passing (legacy-PowerShell parity — scoring unchanged) but is
    now RECORDED as a data error so an alpha-marks district sees "Completed
    with N data errors" instead of silently nulled credits.
    """

    def _history_row(self, mark, student="S001", code="MAT10"):
        return {
            "student number": student,
            "school number": "6262013",
            "course code": code,
            "full course code": code,
            "section": "",
            "final mark": mark,
            "dl start date": "15-Sep-2024",
            "dl completion date": "30-Jan-2025",
        }

    def test_numeric_blank_and_w_marks_record_nothing(self, transformer, sc_mapping, myedbc_global_config):
        history = _history(
            [
                self._history_row("85"),
                self._history_row("30", code="ENG12"),
                self._history_row("", code="ENG11"),
                self._history_row("W", code="SCI09"),
            ]
        )
        _run(transformer, sc_mapping, myedbc_global_config, history=history)
        assert transformer.data_errors == []

    def test_letter_and_pass_marks_record_data_errors_with_unchanged_output(
        self, transformer, sc_mapping, myedbc_global_config, course_info_df, caplog
    ):
        history = _history(
            [
                self._history_row("A"),
                self._history_row("Pass", code="ENG12"),
                self._history_row("85", code="ENG11"),
            ]
        )
        with caplog.at_level("ERROR"):
            result = _run(transformer, sc_mapping, myedbc_global_config, history=history, info=course_info_df)

        # Output unchanged: all three rows emitted; alpha marks pass through
        # not-passing (null credits — NaN once pandas coerces the mixed column),
        # the numeric pass keeps its credits.
        assert len(result) == 3
        alpha = result[result["Final Mark"] == "A"].iloc[0]
        assert pd.isna(alpha["Credits Earned"])
        numeric = result[result["Final Mark"] == "85"].iloc[0]
        assert numeric["Credits Earned"] == 4

        errors = transformer.data_errors
        assert len(errors) == 1
        assert errors[0]["entity"] == "StudentCourses"
        assert errors[0]["field"] == "Final Mark"
        assert errors[0]["failed_rows"] == 2
        assert "'A'" in errors[0]["sample"]
        assert any("non-numeric Final Mark" in r.message for r in caplog.records)


class TestStudentCoursesActiveFiltering:
    """Zero-orphan invariant: transcripts only for students on the active
    roster (Students.csv). Fail-safe unchanged when no roster exists (mbponly).
    """

    def _history(self):
        return _history(
            [
                {
                    "student number": "S001",
                    "school number": "6262013",
                    "course code": "MAT10",
                    "full course code": "MAT10",
                    "section": "",
                    "final mark": "85",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                },
                {
                    "student number": "S999",
                    "school number": "6262013",
                    "course code": "ENG12",
                    "full course code": "ENG12",
                    "section": "",
                    "final mark": "70",
                    "dl start date": "15-Sep-2024",
                    "dl completion date": "30-Jan-2025",
                },
            ]
        )

    def test_non_rostered_student_rows_dropped(self, transformer, sc_mapping, myedbc_global_config):
        transformer._context.active_student_ids = {"S001"}
        result = _run(transformer, sc_mapping, myedbc_global_config, history=self._history())
        assert set(result["Student ID"]) == {"S001"}

    def test_selection_rows_also_filtered(self, transformer, sc_mapping, myedbc_global_config):
        transformer._context.active_student_ids = {"S001"}
        selection = _selection(
            [
                {"student number": "S001", "school number": "6262013", "course code": "ENG11", "dl start date": ""},
                {"student number": "S999", "school number": "6262013", "course code": "ENG11", "dl start date": ""},
            ]
        )
        result = _run(transformer, sc_mapping, myedbc_global_config, selection=selection)
        assert set(result["Student ID"]) == {"S001"}

    def test_empty_roster_keeps_all_rows_and_warns(self, transformer, sc_mapping, myedbc_global_config, caplog):
        with caplog.at_level("WARNING"):
            result = _run(transformer, sc_mapping, myedbc_global_config, history=self._history())
        assert set(result["Student ID"]) == {"S001", "S999"}
        assert any("[StudentCourses]" in r.message and "active_student_ids empty" in r.message for r in caplog.records)
