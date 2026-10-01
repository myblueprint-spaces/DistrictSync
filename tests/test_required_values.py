"""Required values: a row missing a value SpacesEDU requires is left out, counted and amber (plan 0053 S13d).

Owner ruling 2026-09-28 ("leave out + count + amber"), restated 2026-09-30 as "this generalises
Family's no-email rule"; ``docs/developer/failure-policy.md`` §5 (c), catalogue #42/#43. Pinned
here:

* **the table** — ONE pandas-free declaration (:mod:`src.etl.required_fields`), stdlib-only by
  AST with a doctored twin; every bundled config's active rostering entities PRODUCE every
  required column (a doctored config that drops one is caught). ``output-contract.md``'s
  Required column is tied to it both ways in ``tests/test_output_contract_doc.py``;
* **the rule** (:func:`~src.etl.transformers.required_values.leave_out_rows_missing_required_values`)
  — the ONE blank test, the count == rows removed, ONE aggregated log line carrying contract
  column names and counts only (sentinel-swept), an absent column is a config fault that never
  empties the entity, the left-out ids exclude any id with a kept row;
* **the wiring** — each rostering transformer runs it LAST, after its scope filters (an unroled
  staff member is never counted), and Students publishes its roster from the kept rows;
* **the cascade** — Enrollments leaves out the teacher rows of a left-out staff member and every
  row of a left-out class, narrowly (a teacher absent from Staff.csv for another reason stays);
  ``DEPENDS_ON`` holds that and every bundled config runs an upstream before its dependent;
* **end to end** — a synthetic drop planting a left-out student, staff member, contact and class:
  no blank required value in a delivered rostering CSV, no delivered row pointing at anything
  left out, the notes on the record, Home amber naming the outputs; and the all-blank case: a
  CRITICAL entity stops the night with TRUE copy (card == record), Family is left out amber.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.config.loader import available_configs, load_config
from src.etl.errors import EmptyRequiredOutputError
from src.etl.outcomes import (
    DEPENDS_ON,
    EntityOutcome,
    OutcomeKind,
    OutcomeNote,
    OutcomeReason,
    outcomes_from_record,
)
from src.etl.pipeline import configured_entity_order, run_pipeline
from src.etl.required_fields import (
    EMAIL_OUTPUT_FIELDS,
    REQUIRED_OUTPUT_FIELDS,
    REQUIRED_VALUE_NOTE,
    REQUIRED_VALUE_NOTES,
    is_required_field,
    left_out_for_required_values,
    required_fields,
)
from src.etl.transformers.context import ClassArtifacts, TransformContext
from src.etl.transformers.enrollments import EnrollmentTransformer
from src.etl.transformers.ids import is_blank_series
from src.etl.transformers.required_values import leave_out_rows_missing_required_values
from src.etl.transformers.staff import StaffTransformer
from src.history.store import read_run_records
from src.ui_flet.failure_copy import (
    error_card_copy,
    failed_copy_for,
    partial_copy,
    partial_label,
    stopped_for_required_values,
    warning_outcomes,
)
from src.ui_flet.home_status import LatestReason, classify_latest_reason
from src.utils.paths import bundle_mappings_dir
from tests._pins import BUNDLED_CONFIG_COUNT
from tests.test_contract import _create_mbp_all_inputs, _create_myedbc_inputs

_REPO = Path(__file__).resolve().parents[1]
_REQUIRED_FIELDS_SOURCE = _REPO / "src" / "etl" / "required_fields.py"

#: Values that must never reach a log line (§8) — planted in the rows the rule leaves out.
_SENTINELS = ("Zelda-Sentinel", "zelda.sentinel@example.org", "SENT-9001")


def _ctx() -> TransformContext:
    return TransformContext()


def _staff(**overrides: list) -> pd.DataFrame:
    base = {
        "User ID": ["T1", "T2", "T3"],
        "First Name": ["Ann", "Zelda-Sentinel", "Cy"],
        "Last Name": ["A", "B", "C"],
        "Email": ["a@example.org", "", "c@example.org"],
        "Role": ["teacher", "teacher", "administrator"],
        "School ID": ["100", "100", "200"],
    }
    return pd.DataFrame({**base, **overrides})


# --------------------------------------------------------------------------- #
# The table                                                                    #
# --------------------------------------------------------------------------- #
def _non_stdlib_imports(source: str) -> list[str]:
    """Every import in ``source`` that is neither the stdlib nor ``src.etl.outcomes``."""
    allowed_roots = {"__future__", "collections", "types", "typing"}
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        found += [n for n in names if n.split(".", 1)[0] not in allowed_roots and n != "src.etl.outcomes"]
    return found


class TestTheTable:
    def test_the_table_module_is_pandas_free(self):
        assert _non_stdlib_imports(_REQUIRED_FIELDS_SOURCE.read_text(encoding="utf-8")) == []

    def test_doctored_a_pandas_import_is_caught(self):
        assert _non_stdlib_imports("import pandas as pd\nfrom typing import Final\n") == ["pandas"]

    def test_the_table_is_the_five_rostering_files_of_the_spec(self):
        assert set(REQUIRED_OUTPUT_FIELDS) == {"Students", "Staff", "Family", "Classes", "Enrollments"}
        assert REQUIRED_OUTPUT_FIELDS["Family"] == ("First Name", "Last Name", "Email", "Student User ID")
        assert all(fields and len(set(fields)) == len(fields) for fields in REQUIRED_OUTPUT_FIELDS.values())

    @pytest.mark.parametrize("entity", ["CourseInfo", "StudentCourses", "StudentAttendance", "Invented"])
    def test_a_feed_outside_the_spec_raises_never_an_empty_answer(self, entity):
        with pytest.raises(ValueError, match="not a SpacesEDU rostering output"):
            required_fields(entity)
        assert not is_required_field(entity, "User ID")

    def test_is_required_field_is_total(self):
        assert is_required_field("Staff", "Email")
        assert not is_required_field("Staff", "Phone")
        assert not is_required_field(None, "Email") and not is_required_field(["Staff"], "Email")

    def test_one_note_per_output_and_the_email_exclusion_is_the_same_rule(self):
        assert set(REQUIRED_VALUE_NOTE) == set(REQUIRED_OUTPUT_FIELDS)
        assert len(set(REQUIRED_VALUE_NOTE.values())) == len(REQUIRED_VALUE_NOTE)
        assert {*REQUIRED_VALUE_NOTE.values(), OutcomeNote.CONTACTS_EXCLUDED_NO_EMAIL} == REQUIRED_VALUE_NOTES
        assert all(email in REQUIRED_OUTPUT_FIELDS[entity] for entity, email in EMAIL_OUTPUT_FIELDS.items())

    def test_the_predicate_reads_the_notes(self):
        assert left_out_for_required_values(((OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE, 3),))
        assert left_out_for_required_values(((OutcomeNote.CONTACTS_EXCLUDED_NO_EMAIL, 1),))
        assert not left_out_for_required_values(((OutcomeNote.STAFF_STATUS_COLUMN_ABSENT, 3),))
        assert not left_out_for_required_values(())


def _unproduced_required(configs: dict[str, dict]) -> list[str]:
    """Every ``config:entity:column`` a config's ACTIVE rostering entity does not produce.

    The output columns ARE the field_map keys (the field-map engine writes one per key and the
    loader orders by them; Classes/Enrollments build exactly those keys), so a required column
    missing from them is one the mapping never produces.
    """
    problems: list[str] = []
    for sis, raw in sorted(configs.items()):
        mappings, global_config = raw["mappings"], raw["global_config"]
        for entity in configured_entity_order(mappings, global_config):
            if entity not in REQUIRED_OUTPUT_FIELDS:
                continue
            keys = set((mappings.get(entity) or {}).get("field_map", {}))
            problems += [f"{sis}:{entity}:{column}" for column in REQUIRED_OUTPUT_FIELDS[entity] if column not in keys]
    return problems


def _bundled_raw_configs() -> dict[str, dict]:
    return {
        sis: load_config(sis, bundle_mappings_dir()).to_raw_dict() for sis in available_configs(bundle_mappings_dir())
    }


class TestEveryBundledConfigProducesEveryRequiredColumn:
    def test_no_bundled_config_misses_a_required_column(self):
        configs = _bundled_raw_configs()
        assert len(configs) == BUNDLED_CONFIG_COUNT
        assert _unproduced_required(configs) == []

    def test_non_vacuity_most_configs_carry_rostering_entities(self):
        configs = _bundled_raw_configs()
        with_staff = [
            s for s, r in configs.items() if "Staff" in configured_entity_order(r["mappings"], r["global_config"])
        ]
        assert len(with_staff) >= 10

    def test_doctored_a_config_without_staff_email_is_caught(self):
        raw = load_config("myedbc", bundle_mappings_dir()).to_raw_dict()
        del raw["mappings"]["Staff"]["field_map"]["Email"]
        assert _unproduced_required({"doctored": raw}) == ["doctored:Staff:Email"]


def _order_violations(configs: dict[str, dict]) -> list[str]:
    """Every configured dependent that runs BEFORE a configured upstream it reads (``DEPENDS_ON``)."""
    problems: list[str] = []
    for sis, raw in sorted(configs.items()):
        order = configured_entity_order(raw["mappings"], raw["global_config"])
        for entity, upstream in DEPENDS_ON.items():
            if entity not in order:
                continue
            problems += [
                f"{sis}: {entity} runs before {dep}"
                for dep in sorted(upstream)
                if dep in order and order.index(dep) > order.index(entity)
            ]
    return problems


class TestStaffRunsBeforeEnrollments:
    """Enrollments reads Staff's ``left_out_staff_ids`` (DEPENDS_ON since S13d): a config that ran
    Enrollments first would miss the cascade. Pinned for every upstream, every bundled config."""

    def test_every_bundled_config_runs_each_upstream_first(self):
        assert _order_violations(_bundled_raw_configs()) == []

    def test_depends_on_names_staff_for_enrollments(self):
        assert "Staff" in DEPENDS_ON["Enrollments"]

    def test_doctored_an_entity_order_with_enrollments_first_is_caught(self):
        raw = load_config("myedbc", bundle_mappings_dir()).to_raw_dict()
        raw["global_config"]["entity_order"] = ["Students", "Enrollments", "Classes", "Staff", "Family"]
        assert _order_violations({"doctored": raw}) == [
            "doctored: Enrollments runs before Classes",
            "doctored: Enrollments runs before Staff",
        ]


# --------------------------------------------------------------------------- #
# The rule                                                                     #
# --------------------------------------------------------------------------- #
class TestTheRule:
    @pytest.mark.parametrize("blank", ["", "   ", float("nan"), None, "nan", pd.NA], ids=repr)
    def test_every_spelling_of_blank_leaves_the_row_out(self, blank):
        ctx = _ctx()
        frame = _staff(Email=["a@example.org", blank, "c@example.org"])
        left = leave_out_rows_missing_required_values(frame, "Staff", ctx, id_column="User ID")
        assert list(left.kept["User ID"]) == ["T1", "T3"]
        assert left.ids == frozenset({"T2"})
        assert ctx.outcome_notes_for("Staff") == ((OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE, 1),)

    def test_the_count_is_rows_left_out_and_the_log_line_is_counts_only(self, caplog):
        ctx = _ctx()
        frame = _staff(
            **{
                "User ID": ["T1", "SENT-9001", "T3"],
                "Email": ["a@example.org", "", "zelda.sentinel@example.org"],
                "School ID": ["100", "", ""],
            }
        )
        with caplog.at_level(logging.INFO):
            left = leave_out_rows_missing_required_values(frame, "Staff", ctx, id_column="User ID")
        assert list(left.kept["User ID"]) == ["T1"]
        assert ctx.outcome_notes_for("Staff") == ((OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE, 2),)
        lines = [r for r in caplog.records if "REQUIRED VALUES MISSING" in r.getMessage()]
        assert len(lines) == 1 and lines[0].levelno == logging.WARNING
        message = lines[0].getMessage()
        assert "left out 2 of 3 row(s)" in message and "{'Email': 1, 'School ID': 2}" in message
        for record in caplog.records:
            for sentinel in _SENTINELS:
                assert sentinel not in record.getMessage(), f"a value reached the log: {sentinel!r}"

    def test_twin_a_complete_frame_is_returned_and_records_nothing(self, caplog):
        ctx = _ctx()
        frame = _staff(Email=["a@example.org", "b@example.org", "c@example.org"])
        with caplog.at_level(logging.INFO):
            left = leave_out_rows_missing_required_values(frame, "Staff", ctx, id_column="User ID")
        assert left.kept is frame and left.ids == frozenset()
        assert ctx.outcome_notes == [] and not caplog.records

    def test_an_id_with_a_kept_row_is_not_left_out_and_a_blank_id_never_is(self):
        """A pupil at two schools with one row missing its school still ships (plan 0053 S13d)."""
        ctx = _ctx()
        frame = pd.DataFrame(
            {
                "Class ID": ["C1", "C1", "C2", ""],
                "Name": ["Math", "Math", "Art", "Nameless"],
                "School ID": ["100", "", "", "100"],
            }
        )
        left = leave_out_rows_missing_required_values(frame, "Classes", ctx, id_column="Class ID")
        assert list(left.kept["Class ID"]) == ["C1"]
        assert left.ids == frozenset({"C2"})

    def test_without_an_id_column_no_ids_are_published(self):
        ctx = _ctx()
        left = leave_out_rows_missing_required_values(_staff(), "Staff", ctx, id_column=None)
        assert len(left.kept) == 2 and left.ids == frozenset()

    def test_an_absent_required_column_is_a_config_fault_never_an_empty_entity(self, caplog):
        ctx = _ctx()
        frame = _staff().drop(columns=["Email", "School ID"])
        with caplog.at_level(logging.WARNING):
            left = leave_out_rows_missing_required_values(frame, "Staff", ctx, id_column="User ID")
        assert left.kept is frame and left.ids == frozenset()
        assert ctx.outcome_notes_for("Staff") == (
            (OutcomeNote.EMAIL_OUTPUT_NOT_MAPPED, 3),
            (OutcomeNote.REQUIRED_OUTPUT_NOT_MAPPED, 3),
        )
        assert sum("No output column for ['School ID']" in r.getMessage() for r in caplog.records) == 1
        for record in caplog.records:
            for value in ("T1", "T2", "Zelda-Sentinel", "a@example.org", "c@example.org"):
                assert value not in record.getMessage(), f"a value reached the log: {value!r}"

    def test_an_absent_column_and_a_blank_value_are_told_apart(self):
        """The unmapped note counts the rows that SHIP without the column — never the row the
        rule left out for a blank value in a column that IS produced (review B2)."""
        ctx = _ctx()
        frame = _staff().drop(columns=["School ID"])
        left = leave_out_rows_missing_required_values(frame, "Staff", ctx, id_column=None)
        assert len(left.kept) == 2
        assert dict(ctx.outcome_notes_for("Staff")) == {
            OutcomeNote.REQUIRED_OUTPUT_NOT_MAPPED: len(left.kept),
            OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE: 1,
        }

    def test_the_email_unmapped_note_counts_shipped_rows_too(self, caplog):
        ctx = _ctx()
        frame = _staff(**{"First Name": ["Ann", "", "Cy"]}).drop(columns=["Email"])
        with caplog.at_level(logging.WARNING):
            left = leave_out_rows_missing_required_values(frame, "Staff", ctx, id_column="User ID")
        assert list(left.kept["User ID"]) == ["T1", "T3"] and left.ids == frozenset({"T2"})
        assert ctx.outcome_notes_for("Staff") == (
            (OutcomeNote.EMAIL_OUTPUT_NOT_MAPPED, 2),
            (OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE, 1),
        )

    def test_every_row_left_out_beside_an_absent_column_logs_the_gap_and_records_no_zero(self, caplog):
        """No row ships, so no unmapped note (a note counts at least one row); the gap is still
        logged once, and the rule's own note still counts every row."""
        ctx = _ctx()
        frame = _staff(Email=["", "", ""]).drop(columns=["School ID"])
        with caplog.at_level(logging.WARNING):
            left = leave_out_rows_missing_required_values(frame, "Staff", ctx, id_column="User ID")
        assert left.kept.empty and left.ids == frozenset({"T1", "T2", "T3"})
        assert ctx.outcome_notes_for("Staff") == ((OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE, 3),)
        assert sum("No output column for ['School ID']" in r.getMessage() for r in caplog.records) == 1

    def test_rows_with_none_of_the_required_columns_all_ship_and_are_surfaced_once(self):
        ctx = _ctx()
        frame = pd.DataFrame({"x": [1, 2]})
        left = leave_out_rows_missing_required_values(frame, "Staff", ctx, id_column="User ID")
        assert left.kept is frame and left.ids == frozenset()
        assert ctx.outcome_notes_for("Staff") == (
            (OutcomeNote.EMAIL_OUTPUT_NOT_MAPPED, 2),
            (OutcomeNote.REQUIRED_OUTPUT_NOT_MAPPED, 2),
        )

    def test_an_absent_id_column_publishes_no_ids_but_still_leaves_the_row_out(self):
        """The id column is itself required, so its absence is already the surfaced config fault
        (``REQUIRED_OUTPUT_NOT_MAPPED``) — and a Staff.csv without ids has nothing to orphan. It
        must not raise: that would turn a mapping gap into a stopped night."""
        ctx = _ctx()
        frame = _staff().drop(columns=["User ID"])
        left = leave_out_rows_missing_required_values(frame, "Staff", ctx, id_column="User ID")
        assert len(left.kept) == 2 and left.ids == frozenset()
        assert ctx.outcome_notes_for("Staff") == (
            (OutcomeNote.REQUIRED_OUTPUT_NOT_MAPPED, 2),
            (OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE, 1),
        )

    def test_an_empty_frame_records_nothing(self):
        ctx = _ctx()
        empty = _staff().iloc[0:0]
        assert leave_out_rows_missing_required_values(empty, "Staff", ctx, id_column="User ID").kept is empty
        assert ctx.outcome_notes == []

    def test_a_feed_outside_the_spec_is_a_caller_bug(self):
        with pytest.raises(ValueError):
            leave_out_rows_missing_required_values(pd.DataFrame({"x": [1]}), "CourseInfo", _ctx(), id_column=None)

    def test_uses_the_one_blank_test(self):
        """The rule's blank is ``ids.is_blank_series`` — the same answer, value by value."""
        values = pd.Series(["x", "", " ", "nan", "NaN", None, float("nan"), "0"])
        ctx = _ctx()
        frame = pd.DataFrame({column: ["v"] * len(values) for column in REQUIRED_OUTPUT_FIELDS["Classes"]})
        frame["Name"] = values
        kept = leave_out_rows_missing_required_values(frame, "Classes", ctx, id_column=None).kept
        assert list(kept.index) == list(values[~is_blank_series(values)].index)


# --------------------------------------------------------------------------- #
# The wiring                                                                   #
# --------------------------------------------------------------------------- #
class TestStaffWiring:
    _MAPPING = {
        "source_files": {"staff_info": "Staff.txt"},
        "field_map": {
            "User ID": "Teacher Id",
            "First Name": "First Name",
            "Last Name": "Last Name",
            "Email": "Email Address",
            "Role": {"column": "Teaching Staff", "transform": "map_role"},
            "School ID": "School Number",
        },
    }

    def _run(self, frame: pd.DataFrame) -> tuple[pd.DataFrame, TransformContext]:
        ctx = _ctx()
        ctx.raw_data = {"Staff.txt": frame}
        return StaffTransformer().transform(frame, self._MAPPING, ctx), ctx

    def test_a_staff_member_without_an_email_is_left_out_and_published(self):
        frame = pd.DataFrame(
            {
                "teacher id": ["T1", "T2"],
                "first name": ["Ann", "Bo"],
                "last name": ["A", "B"],
                "email address": ["a@example.org", ""],
                "teaching staff": ["Y", "Y"],
                "school number": ["100", "100"],
            }
        )
        out, ctx = self._run(frame)
        assert list(out["User ID"]) == ["T1"]
        assert ctx.left_out_staff_ids == frozenset({"T2"})
        assert (OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE, 1) in ctx.outcome_notes_for("Staff")

    def test_an_unroled_staff_member_is_out_of_scope_never_counted(self):
        """A custodian with no role is dropped by the role rule (§5 #22), never "missing a Role"."""
        frame = pd.DataFrame(
            {
                "teacher id": ["T1", "T9"],
                "first name": ["Ann", "Sam"],
                "last name": ["A", "Z"],
                "email address": ["a@example.org", "s@example.org"],
                "teaching staff": ["Y", "N"],
                "school number": ["100", "100"],
            }
        )
        out, ctx = self._run(frame)
        assert list(out["User ID"]) == ["T1"]
        assert ctx.left_out_staff_ids == frozenset()
        assert not left_out_for_required_values(ctx.outcome_notes_for("Staff"))


# --------------------------------------------------------------------------- #
# The cascade                                                                  #
# --------------------------------------------------------------------------- #
def _enrollments(**overrides: list) -> pd.DataFrame:
    base = {
        "Class ID": ["HR_1", "HR_1", "MT2_2026", "MT2_2026", "BL_1", "CO_1", "MT3_2026", "MT3_2026"],
        "User ID": ["S1", "T1", "S2", "T1", "T1", "T1", "S3", "T7"],
        "Role": ["student", "teacher", "student", "teacher", "teacher", "teacher", "student", "teacher"],
        "School ID": ["100"] * 8,
    }
    return pd.DataFrame({**base, **overrides})


class TestTheCascade:
    def test_every_teacher_row_of_a_left_out_staff_member_goes_and_its_student_twin_stays(self, caplog):
        """One pass over the concatenated frame covers every builder (homeroom, subject, blended,
        co-teacher rows all carry Role=teacher). A STUDENT whose id collides with the staff id
        is not a teacher row and stays."""
        ctx = _ctx()
        ctx.left_out_staff_ids = frozenset({"T1"})
        frame = _enrollments(**{"User ID": ["T1", "T1", "S2", "T1", "T1", "T1", "S3", "T7"]})
        with caplog.at_level(logging.INFO):
            out = EnrollmentTransformer._leave_out_orphans_of_left_out_rows(frame, ctx)
        assert list(out["User ID"]) == ["T1", "S2", "S3", "T7"]
        assert list(out["Role"]) == ["student", "student", "student", "teacher"]
        (line,) = [r.getMessage() for r in caplog.records if "pointing at rows left out upstream" in r.getMessage()]
        assert "4 teacher row(s) of left-out staff" in line and "0 row(s) of left-out classes" in line

    def test_every_row_of_a_left_out_class_goes(self):
        ctx = _ctx()
        ctx.left_out_class_ids = frozenset({"MT2_2026"})
        out = EnrollmentTransformer._leave_out_orphans_of_left_out_rows(_enrollments(), ctx)
        assert "MT2_2026" not in set(out["Class ID"]) and len(out) == 6

    def test_twin_nothing_left_out_upstream_touches_nothing(self, caplog):
        ctx = _ctx()
        frame = _enrollments()
        with caplog.at_level(logging.INFO):
            assert EnrollmentTransformer._leave_out_orphans_of_left_out_rows(frame, ctx) is frame
        assert not caplog.records

    def test_twin_left_out_upstream_but_unreferenced_touches_nothing(self, caplog):
        """The common measured shape (SD51/SD60/SD74): staff left out, no enrollment points at
        them. ``S1`` names only a STUDENT row, so it is no teacher orphan; ``NOPE`` is no class."""
        ctx = _ctx()
        ctx.left_out_staff_ids = frozenset({"S1"})
        ctx.left_out_class_ids = frozenset({"NOPE"})
        frame = _enrollments()
        with caplog.at_level(logging.INFO):
            assert EnrollmentTransformer._leave_out_orphans_of_left_out_rows(frame, ctx) is frame
        assert not [r for r in caplog.records if "pointing at rows left out upstream" in r.getMessage()]

    def test_narrow_a_teacher_absent_from_staff_for_another_reason_stays(self):
        """T7 is not in Staff.csv (not exported, departed …) but was not LEFT OUT for a required
        value: widening the teacher zero-orphan rule is a ROADMAP item, not this slice."""
        ctx = _ctx()
        ctx.left_out_staff_ids = frozenset({"T1"})
        out = EnrollmentTransformer._leave_out_orphans_of_left_out_rows(_enrollments(), ctx)
        assert "T7" in set(out["User ID"])

    def test_the_blended_builder_drops_a_section_with_no_teacher(self):
        """Scope, not a missing value: a blended section without a teacher id makes no teacher
        row, like the other three builders (so the rule never counts it)."""
        artifacts = ClassArtifacts(
            blended_teacher_map={"BL_1": ["T1", "nan", ""]},
            blended_class_metadata={"BL_1": {"School ID": "100"}},
        )
        out = EnrollmentTransformer._blended_teacher_enrollments(artifacts)
        assert out is not None and list(out["User ID"]) == ["T1"]
        assert (
            EnrollmentTransformer._blended_teacher_enrollments(
                ClassArtifacts(blended_teacher_map={"BL_1": ["nan"]}, blended_class_metadata={})
            )
            is None
        )


# --------------------------------------------------------------------------- #
# End to end over a synthetic drop                                             #
# --------------------------------------------------------------------------- #
def _read(out: Path, entity: str) -> pd.DataFrame:
    return pd.read_csv(out / f"{entity}.csv", dtype=str, keep_default_na=False, encoding="utf-8-sig")


def _blank_required(out: Path) -> dict[str, dict[str, int]]:
    found: dict[str, dict[str, int]] = {}
    for entity, required in REQUIRED_OUTPUT_FIELDS.items():
        path = out / f"{entity}.csv"
        if not path.exists():
            continue
        frame = _read(out, entity)
        counts = {column: int(is_blank_series(frame[column]).sum()) for column in required if column in frame}
        if any(counts.values()):
            found[entity] = counts
    return found


def _edit(path: Path, **columns: dict[int, str]) -> None:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    for column, cells in columns.items():
        for row, value in cells.items():
            frame.loc[row, column] = value
    frame.to_csv(path, index=False)


def _append(path: Path, row: dict[str, str]) -> None:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    pd.concat([frame, pd.DataFrame([row])], ignore_index=True).fillna("").to_csv(path, index=False)


def _plant_one_of_each(d: Path) -> None:
    """Over the base MyEd BC fixture: a student (S004), two teachers (T001 — the homeroom teacher of
    A1 — and T003 — MAT10's teacher), a family contact and a class (MT009) each missing a value
    SpacesEDU requires, each with rows elsewhere that would point at it."""
    demo = pd.read_csv(d / "StudentDemographicInformation.txt", dtype=str, keep_default_na=False)
    new = demo.iloc[[1]].copy()
    new["Student Number"] = "S004"
    new["Legal First Name"] = "Zelda-Sentinel"
    new["Student email address"] = ""
    pd.concat([demo, new], ignore_index=True).to_csv(d / "StudentDemographicInformation.txt", index=False)
    schedule = pd.read_csv(d / "StudentSchedule.txt", dtype=str, keep_default_na=False)
    s004 = schedule.iloc[[1]].copy()
    s004["Student Number"] = s004["Student ID"] = "S004"
    orphan_class = schedule.iloc[[2]].copy()
    orphan_class["Master Timetable ID"] = "MT009"
    orphan_class["School Number"] = ""
    pd.concat([schedule, s004, orphan_class], ignore_index=True).to_csv(d / "StudentSchedule.txt", index=False)
    _edit(d / "StaffInformationEnhanced.txt", **{"Email Address": {0: "", 1: ""}})
    _append(
        d / "EmergencyContactInformation.txt",
        {
            "Student Number": "S002",
            "First Name": "",
            "Last Name": "Sentinel",
            "Email Address": "zelda.sentinel@example.org",
        },
    )
    _append(
        d / "EmergencyContactInformation.txt",
        {"Student Number": "S004", "First Name": "Kin-Sentinel", "Last Name": "Of", "Email Address": "kin@example.org"},
    )


#: The ids ``_plant_one_of_each`` leaves out (a student, two staff, a class) — swept from the log.
_LEFT_OUT_IDS = ("S004", "T001", "T003", "MT009")


@pytest.mark.integration
class TestEndToEnd:
    def test_one_of_each_left_out_counted_amber_and_orphan_free(self, tmp_path: Path, caplog) -> None:
        inp, out = tmp_path / "in", tmp_path / "out"
        inp.mkdir()
        out.mkdir()
        _create_myedbc_inputs(inp)
        _plant_one_of_each(inp)
        with caplog.at_level(logging.INFO):
            result = run_pipeline("myedbc", str(inp), str(out))

        # No blank required value reaches a delivered rostering CSV.
        assert _blank_required(out) == {}
        students, staff, family, classes, enrollments = (
            _read(out, e) for e in ("Students", "Staff", "Family", "Classes", "Enrollments")
        )
        assert "S004" not in set(students["User ID"])
        assert not {"T001", "T003"} & set(staff["User ID"])
        assert "MT009_2026" not in set(classes["Class ID"])
        # Nothing delivered points at anything left out.
        assert "S004" not in set(enrollments["User ID"]) and "S004" not in set(family["Student User ID"])
        teachers = enrollments[enrollments["Role"] == "teacher"]
        assert not {"T001", "T003"} & set(teachers["User ID"])
        assert "MT009_2026" not in set(enrollments["Class ID"])
        assert "Sentinel" not in set(family["Last Name"])
        # Positive twins: what was NOT left out is still delivered, linked.
        assert {"S001", "S002", "S003"} <= set(students["User ID"]) and "T004" in set(teachers["User ID"])
        assert set(enrollments["Class ID"]) <= set(classes["Class ID"])

        # The notes: count == rows removed, on the record.
        outcomes = {o.entity: o for o in result.entity_outcomes}
        assert dict(outcomes["Students"].notes)[OutcomeNote.STUDENTS_EXCLUDED_REQUIRED_VALUE] == 1
        assert dict(outcomes["Staff"].notes)[OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE] == 2
        assert dict(outcomes["Family"].notes)[OutcomeNote.CONTACTS_EXCLUDED_REQUIRED_VALUE] == 1
        assert dict(outcomes["Classes"].notes)[OutcomeNote.CLASSES_EXCLUDED_REQUIRED_VALUE] == 1
        (record,) = read_run_records() or []
        assert record["status"] == "success"
        assert record["entity_outcomes"]["Staff"]["notes"]["staff_excluded_required_value"] == 2

        # Amber on Home, naming the outputs.
        assert classify_latest_reason(record, prior_build=None) is LatestReason.PARTIAL
        stored = outcomes_from_record(record)
        headline, detail = partial_copy(warning_outcomes(stored), delivered=False)
        assert headline.startswith("Your sync completed without some students, some staff")
        assert "SpacesEDU requires" in detail
        assert "staff missing required values left out" in partial_label(warning_outcomes(stored))

        # The log: the cascade line and no planted value — nor any left-out id — anywhere.
        assert any("teacher row(s) of left-out staff" in r.getMessage() for r in caplog.records)
        for rec in caplog.records:
            for sentinel in (*_SENTINELS, *_LEFT_OUT_IDS, "Kin-Sentinel", "kin@example.org"):
                assert sentinel not in rec.getMessage(), sentinel

    def test_a_left_out_student_leaves_no_transcript_row(self, tmp_path: Path) -> None:
        inp, out = tmp_path / "in", tmp_path / "out"
        inp.mkdir()
        out.mkdir()
        _create_mbp_all_inputs(inp)
        _edit(inp / "StudentDemographicInformation.txt", **{"Student email address": {2: ""}})  # S003
        run_pipeline("mbp_all", str(inp), str(out))
        assert "S003" not in set(_read(out, "Students")["User ID"])
        courses = _read(out, "StudentCourses")
        assert not courses.empty and "S003" not in set(courses["Student ID"])
        assert "S002" in set(courses["Student ID"]), "the twin: a kept student's transcript still ships"
        assert "S003" not in set(_read(out, "Enrollments")["User ID"])

    def test_every_staff_email_blank_stops_the_night_with_true_copy(self, tmp_path: Path) -> None:
        inp, out = tmp_path / "in", tmp_path / "out"
        inp.mkdir()
        out.mkdir()
        _create_myedbc_inputs(inp)
        _edit(inp / "StaffInformationEnhanced.txt", **{"Email Address": dict.fromkeys(range(5), "")})
        with pytest.raises(EmptyRequiredOutputError) as raised:
            run_pipeline("myedbc", str(inp), str(out))
        assert raised.value.entity == "Staff" and raised.value.required_values_left_out is True
        assert list(out.iterdir()) == [], "nothing is written"
        (record,) = read_run_records() or []
        assert (record["status"], record["error_category"]) == ("failed", "empty_required_output")
        entry = record["entity_outcomes"]["Staff"]
        assert (entry["kind"], entry["reason"]) == ("empty", "required_values_missing")
        outcomes = outcomes_from_record(record)
        assert stopped_for_required_values(raised.value.category, outcomes)
        card = error_card_copy(raised.value, delivery_requested=False)
        home = failed_copy_for(record["error_category"], delivery_requested=False, outcomes=outcomes)
        assert card == home
        assert card[0] == "No staff records could be sent"
        assert "came out of your export" not in card[0] and "missing a value SpacesEDU requires" in card[1]

    def test_twin_a_stop_for_another_reason_keeps_the_category_wording(self, tmp_path: Path) -> None:
        """Every staff member unroled (out of scope, not missing a value) → the S13c wording."""
        inp, out = tmp_path / "in", tmp_path / "out"
        inp.mkdir()
        out.mkdir()
        _create_myedbc_inputs(inp)
        _edit(inp / "StaffInformationEnhanced.txt", **{"Teaching Staff": dict.fromkeys(range(5), "N")})
        (inp / "StudentSchedule.txt").write_text(
            (inp / "StudentSchedule.txt").read_text(encoding="utf-8").replace("T00", "X00"), encoding="utf-8"
        )
        _edit(inp / "StudentDemographicInformation.txt", **{"Teacher ID": {0: "X1", 1: "X3", 2: "X4"}})
        _edit(inp / "ClassInformationEnh.txt", **{"Teacher ID": {0: "X1", 1: "X3", 2: "X4"}})
        with pytest.raises(EmptyRequiredOutputError) as raised:
            run_pipeline("myedbc", str(inp), str(out))
        assert raised.value.entity == "Staff" and raised.value.required_values_left_out is False
        (record,) = read_run_records() or []
        assert record["entity_outcomes"]["Staff"]["reason"] == "no_rows_after_transform"
        card = error_card_copy(raised.value, delivery_requested=False)
        assert card[0] == "No staff records came out of your export"
        assert card == failed_copy_for(
            record["error_category"], delivery_requested=False, outcomes=outcomes_from_record(record)
        )

    def test_every_contact_missing_a_name_leaves_family_out_amber(self, tmp_path: Path) -> None:
        inp, out = tmp_path / "in", tmp_path / "out"
        inp.mkdir()
        out.mkdir()
        _create_myedbc_inputs(inp)
        _edit(inp / "EmergencyContactInformation.txt", **{"First Name": {0: "", 1: ""}})
        result = run_pipeline("myedbc", str(inp), str(out))
        family = next(o for o in result.entity_outcomes if o.entity == "Family")
        assert (family.kind, family.reason) == (OutcomeKind.EMPTY, OutcomeReason.REQUIRED_VALUES_MISSING)
        assert not (out / "Family.csv").exists() and (out / "Staff.csv").exists()
        (record,) = read_run_records() or []
        assert classify_latest_reason(record, prior_build=None) is LatestReason.PARTIAL
        headline, detail = partial_copy(warning_outcomes(outcomes_from_record(record)), delivered=False)
        assert headline == "Your sync completed without family contacts"
        assert "missing a value SpacesEDU requires" in detail


def test_a_stopped_record_without_a_required_value_note_is_not_worded_as_one():
    """The copy selection is by the recorded NOTE, never the reason alone (a refined
    ``missing_source_column`` still carries it; a plain empty output carries none)."""
    from src.etl.errors import RunErrorCategory

    noted = EntityOutcome.empty(
        "Staff", OutcomeReason.MISSING_SOURCE_COLUMN, notes=((OutcomeNote.STAFF_EXCLUDED_REQUIRED_VALUE, 4),)
    )
    plain = EntityOutcome.empty("Staff", OutcomeReason.NO_ROWS_AFTER_TRANSFORM)
    family = EntityOutcome.empty(
        "Family", OutcomeReason.REQUIRED_VALUES_MISSING, notes=((OutcomeNote.CONTACTS_EXCLUDED_NO_EMAIL, 2),)
    )
    category = RunErrorCategory.EMPTY_REQUIRED_OUTPUT
    assert stopped_for_required_values(category, [noted])
    assert not stopped_for_required_values(category, [plain])
    assert not stopped_for_required_values(category, [family]), "Family is never the stop"
    assert not stopped_for_required_values(RunErrorCategory.DATA, [noted])


# --------------------------------------------------------------------------- #
# Every surface: amber naming the output; the creator gate shows the sentence #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entity", sorted(REQUIRED_VALUE_NOTE))
def test_every_surface_words_a_left_out_output_amber_and_by_name(entity: str) -> None:
    """Home (``partial_copy`` via ``warning_outcomes``), Run History (``partial_label``) and Convert
    (``convert_result.summarize``) all turn a BUILT output carrying its required-value note amber,
    naming what was left out; the creator's gate PASSES it (show notes, don't block) and carries
    the note's sentence — the count is never in the copy."""
    from src.etl.pipeline import PipelineResult
    from src.ui_flet.config_editor import GateState, gate_outcome_for
    from src.ui_flet.convert_result import ConvertResult, ConvertStatus, summarize
    from src.ui_flet.failure_copy import _NOTE_COPY, note_sentence
    from src.ui_flet.verdict import Verdict

    note = REQUIRED_VALUE_NOTE[entity]
    noted = EntityOutcome.built(entity, 7, notes=((note, 123),))
    outcomes = (EntityOutcome.built("Students", 5), noted) if entity != "Students" else (noted,)
    assert warning_outcomes(outcomes) == (noted,)
    headline, detail = partial_copy([noted], delivered=True)
    assert headline == f"Your roster synced without {_NOTE_COPY[note]['phrase']}"
    assert "SpacesEDU requires" in detail and "123" not in headline + detail
    assert partial_label([noted]) == _NOTE_COPY[note]["label"]
    verdict, convert_headline, _ = summarize(
        ConvertResult(
            status=ConvertStatus.DELIVERED,
            entity_counts={entity: 7},
            entity_outcomes=outcomes,
            sftp_attempted=False,
            sftp_ok=False,
            delivery_requested=False,
        )
    )
    assert verdict is Verdict.WARNING and _NOTE_COPY[note]["phrase"] in convert_headline
    gate = gate_outcome_for(
        result=PipelineResult(entity_outcomes=outcomes, entity_counts={"Students": 5}),  # type: ignore[arg-type]
        error=None,
        output_dir_valid=True,
        expected_files=["Students.txt"],
        present_files=["Students.txt"],
    )
    assert gate.state is GateState.PASSED and gate.warning_notes == (note_sentence(note),)


def test_twin_a_clean_run_is_not_amber() -> None:
    clean = (EntityOutcome.built("Students", 5), EntityOutcome.built("Staff", 3))
    assert warning_outcomes(clean) == ()
