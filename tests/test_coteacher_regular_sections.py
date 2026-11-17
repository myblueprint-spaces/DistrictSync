"""Co-teacher Path 3: a REGULAR subject section ClassInformation lists with several primary teachers.

MyEd BC's schedule carries one teacher per student-section row, so a co-taught section that is
ONE MyEd section with two ``Primary Teacher = Y`` rows in ClassInformation used to ship only the
schedule's teacher; the second was dropped silently. Path 3 of
``EnrollmentTransformer._classinfo_coteacher_enrollments`` adds a teacher row for every listed
primary teacher on that section's EXISTING Class ID.

The safety properties pinned here (every other district must be untouched):

* additive only — student rows, Class IDs and ``Classes.csv`` are byte-identical with or without
  Path 3; a single-teacher section yields byte-identical Enrollments;
* never a class of its own — an MTID no subject row carries adds nothing;
* blended sections are unchanged (Path 2 owns them);
* primary = Y only, as on Paths 1/2;
* a missing Master Timetable ID column is the same optional-column posture as Path 2 (one
  warning + ``OutcomeNote.COTEACHER_SOURCE_UNUSABLE``), never a failure.

"Before" is the legacy behaviour, reproduced by handing the co-teacher builder an EMPTY regular
class map (exactly what it received before Path 3 existed).
"""

from unittest.mock import patch

import pandas as pd
import pytest

from src.etl.outcomes import OutcomeNote
from src.etl.transformers.enrollments import EnrollmentTransformer

_ORIGINAL_SUBJECT = EnrollmentTransformer._subject_enrollments


def _legacy_subject(self, *args, **kwargs):
    frame, _regular = _ORIGINAL_SUBJECT(self, *args, **kwargs)
    return frame, {}


def _class_info(rows: list[dict]) -> pd.DataFrame:
    columns = [
        "school number",
        "course code",
        "teacher id",
        "primary teacher",
        "section letter",
        "semester",
        "term",
        "day",
        "period",
        "master timetable id",
    ]
    return pd.DataFrame([{c: r.get(c, "") for c in columns} for r in rows], columns=columns)


def _row(mtid: str, teacher: str, primary: str = "Y", school: str = "200", course: str = "MAT10") -> dict:
    return {
        "school number": school,
        "course code": course,
        "teacher id": teacher,
        "primary teacher": primary,
        "section letter": "A",
        "semester": "1",
        "term": "1",
        "day": "1",
        "period": "1",
        "master timetable id": mtid,
    }


def _canon(df: pd.DataFrame) -> pd.DataFrame:
    return df.reset_index(drop=True).astype(str)


class TestRegularSectionCoTeachers:
    @pytest.fixture(autouse=True)
    def _setup(
        self,
        published_transformer,
        student_schedule_df,
        student_demographic_df,
        staff_info_df,
        course_info_df,
        emergency_contact_df,
        classes_mapping,
        enrollments_mapping,
        global_config,
    ):
        self.transformer = published_transformer
        self.transformer.set_school_year(2025, "08-25", "07-25")
        self.schedule = student_schedule_df
        self.base_raw = {
            "StudentDemographicInformation.txt": student_demographic_df,
            "StudentSchedule.txt": student_schedule_df,
            "StaffInformationEnhanced.txt": staff_info_df,
            "CourseInformation.txt": course_info_df,
            "EmergencyContactInformation.txt": emergency_contact_df,
        }
        self.classes_mapping = classes_mapping
        self.enrollments_mapping = enrollments_mapping
        self.global_config = global_config

    def _run(self, class_info: pd.DataFrame, *, legacy: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
        raw = {**self.base_raw, "ClassInformationEnh.txt": class_info}
        classes = self.transformer.transform(self.schedule, self.classes_mapping, "Classes", raw, self.global_config)
        if legacy:
            with patch.object(EnrollmentTransformer, "_subject_enrollments", _legacy_subject):
                enroll = self.transformer.transform(
                    self.schedule, self.enrollments_mapping, "Enrollments", raw, self.global_config
                )
        else:
            enroll = self.transformer.transform(
                self.schedule, self.enrollments_mapping, "Enrollments", raw, self.global_config
            )
        return classes, enroll

    def _subject_class_id(self, enroll: pd.DataFrame, teacher: str, mtid: str) -> str:
        """The Class ID the SCHEDULE path gave this teacher's section ``mtid`` (never recomputed here)."""
        rows = enroll[(enroll["Role"] == "teacher") & (enroll["User ID"] == teacher)]
        rows = rows[rows["Class ID"].str.startswith(f"{mtid}_")]
        assert len(rows) == 1, rows
        return str(rows["Class ID"].iloc[0])

    def test_two_primary_teachers_on_a_regular_section_both_get_a_teacher_row(self):
        # MT004 (school 200, grade 10) is driven by T003 in the schedule; ClassInformation
        # lists T003 AND T005 as primary on it. T005 appears nowhere in the schedule.
        info = _class_info([_row("MT004", "T003"), _row("MT004", "T005")])
        classes_before, before = self._run(info, legacy=True)
        classes_after, after = self._run(info)

        class_id = self._subject_class_id(before, "T003", "MT004")
        teachers_after = set(after[(after["Role"] == "teacher") & (after["Class ID"] == class_id)]["User ID"])
        assert teachers_after == {"T003", "T005"}
        teachers_before = set(before[(before["Role"] == "teacher") & (before["Class ID"] == class_id)]["User ID"])
        assert teachers_before == {"T003"}, "legacy dropped the second teacher — the defect"

        # Additive only: exactly one new row, a teacher row, on an existing class.
        added = pd.concat([after, before]).drop_duplicates(keep=False)
        assert len(added) == 1
        assert added.iloc[0]["Role"] == "teacher" and added.iloc[0]["User ID"] == "T005"
        assert added.iloc[0]["Class ID"] == class_id
        assert added.iloc[0]["School ID"] == "200"
        # Students and Class IDs untouched; Classes.csv byte-identical.
        pd.testing.assert_frame_equal(
            _canon(after[after["Role"] == "student"]), _canon(before[before["Role"] == "student"])
        )
        assert set(after["Class ID"]) == set(before["Class ID"])
        assert class_id in set(classes_after["Class ID"])
        pd.testing.assert_frame_equal(_canon(classes_after), _canon(classes_before))
        # The legacy rows keep their order; the new row is appended after them.
        pd.testing.assert_frame_equal(_canon(after.iloc[: len(before)]), _canon(before))

    def test_single_teacher_section_is_byte_identical(self):
        info = _class_info([_row("MT004", "T003"), _row("MT005", "T004", course="ENG12")])
        _c, before = self._run(info, legacy=True)
        _c, after = self._run(info)
        pd.testing.assert_frame_equal(_canon(after), _canon(before))

    @pytest.mark.parametrize("flag", ["N", ""])
    def test_non_primary_rows_are_not_added(self, flag):
        info = _class_info([_row("MT004", "T003"), _row("MT004", "T005", primary=flag)])
        _c, before = self._run(info, legacy=True)
        _c, after = self._run(info)
        pd.testing.assert_frame_equal(_canon(after), _canon(before))
        assert "T005" not in set(after["User ID"])

    def test_an_mtid_no_subject_row_carries_creates_nothing(self):
        # MT999 is in ClassInformation only: Path 3 must never make (or enrol into) a class.
        info = _class_info([_row("MT999", "T005"), _row("MT999", "T001")])
        classes_before, before = self._run(info, legacy=True)
        classes_after, after = self._run(info)
        pd.testing.assert_frame_equal(_canon(after), _canon(before))
        pd.testing.assert_frame_equal(_canon(classes_after), _canon(classes_before))

    def test_homeroom_grade_section_is_not_a_regular_class(self):
        # MT001 is a kindergarten (homeroom-grade) schedule row: no subject class exists for it.
        info = _class_info([_row("MT001", "T005", school="100", course="HR-K")])
        _c, before = self._run(info, legacy=True)
        _c, after = self._run(info)
        pd.testing.assert_frame_equal(_canon(after), _canon(before))

    def test_missing_mtid_column_is_the_optional_column_posture(self, caplog):
        # Reachable only with blended detection OFF (SD45/SD51): with it on, blended detection
        # requires the column and stops the night first (§5 #39).
        self.global_config = {**self.global_config, "blended_classes": False}
        info = _class_info([_row("MT004", "T003"), _row("MT004", "T005")]).drop(columns=["master timetable id"])
        with caplog.at_level("WARNING"):
            _c, after = self._run(info)
        assert "T005" not in set(after["User ID"])
        notes = dict(self.transformer._context.outcome_notes_for("Enrollments"))
        assert notes.get(OutcomeNote.COTEACHER_SOURCE_UNUSABLE) == 2
        assert any("CO-TEACHERS LEFT OUT" in r.getMessage() for r in caplog.records)

    def test_present_mtid_column_records_no_note(self):
        info = _class_info([_row("MT004", "T003"), _row("MT004", "T005")])
        self._run(info)
        notes = dict(self.transformer._context.outcome_notes_for("Enrollments"))
        assert OutcomeNote.COTEACHER_SOURCE_UNUSABLE not in notes


class TestBlendedSectionsUnchanged:
    @pytest.fixture(autouse=True)
    def _published_transformer(self, published_transformer):
        self.transformer = published_transformer
        self.transformer.set_school_year(2025, "08-25", "07-25")

    def test_blended_rows_identical_and_regular_coteacher_added(
        self, blended_schedule_df, classes_mapping, enrollments_mapping, global_config, raw_data_with_blended
    ):
        gc = {**global_config, "homeroom_grades": []}
        info = raw_data_with_blended["ClassInformationEnh.txt"].copy()
        info["primary teacher"] = "Y"
        info["section letter"] = "A"
        # A second primary teacher on the blended MT100 (Path 2's) and on the regular MT103 (Path 3's).
        extra = info[info["master timetable id"].isin(["MT100", "MT103"])].copy()
        extra["teacher id"] = "T030"
        raw = {**raw_data_with_blended, "ClassInformationEnh.txt": pd.concat([info, extra], ignore_index=True)}

        def run(legacy: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
            classes = self.transformer.transform(blended_schedule_df, classes_mapping, "Classes", raw, gc)
            if legacy:
                with patch.object(EnrollmentTransformer, "_subject_enrollments", _legacy_subject):
                    return classes, self.transformer.transform(
                        blended_schedule_df, enrollments_mapping, "Enrollments", raw, gc
                    )
            return classes, self.transformer.transform(blended_schedule_df, enrollments_mapping, "Enrollments", raw, gc)

        classes_before, before = run(legacy=True)
        blended_ids = set(self.transformer.blended_teacher_map)
        assert blended_ids, "fixture must produce a blended class"
        classes_after, after = run(legacy=False)

        pd.testing.assert_frame_equal(_canon(classes_after), _canon(classes_before))
        in_blended = after["Class ID"].isin(blended_ids)
        pd.testing.assert_frame_equal(_canon(after[in_blended]), _canon(before[before["Class ID"].isin(blended_ids)]))
        added = pd.concat([after, before]).drop_duplicates(keep=False)
        assert len(added) == 1
        assert added.iloc[0]["User ID"] == "T030" and added.iloc[0]["Role"] == "teacher"
        assert added.iloc[0]["Class ID"] not in blended_ids
        assert added.iloc[0]["Class ID"] in set(before["Class ID"])
