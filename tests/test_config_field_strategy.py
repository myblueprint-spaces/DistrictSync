"""Typed-config field Strategy (T1.1) — per-variant ``.apply`` behavior.

Each structured field-mapping variant in ``src/config/models.py`` is a
Strategy: ``.apply(...)`` produces exactly the value the generic field-map
loop assigns for that variant, and ``BaseTransformer.apply_field_map`` is a
thin typed dispatch over them (no dict sniffing). These tests mirror the
legacy raw-dict behavior branch by branch, pin typed-vs-raw parity through
``apply_field_map``, and pin the fail-fast ALLOWED_TRANSFORMS gate at
config load.
"""

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from pydantic import ValidationError

from src.config.models import (
    ALLOWED_TRANSFORMS,
    ConfiguredField,
    EntityConfig,
    FieldAcademicYear,
    FieldAppendYear,
    FieldEmailFormat,
    FieldEnrollStatus,
    FieldFixedValue,
    FieldIdRolePair,
    FieldNameConfig,
    FieldTransform,
    MappingConfig,
    ensure_field_mapping,
)
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext


class _HostTransformer(BaseTransformer):
    """Concrete BaseTransformer (the ABC requires ``transform``) used as the
    Strategy host for direct ``.apply`` and ``apply_field_map`` calls."""

    def transform(self, df, mapping, context):  # pragma: no cover — unused
        return df


def _ctx() -> TransformContext:
    ctx = TransformContext()
    ctx.set_school_year(2025, "08-25", "07-25")
    return ctx


def _apply(field_map: dict, working: pd.DataFrame, ctx: TransformContext | None = None) -> pd.DataFrame:
    ctx = ctx or _ctx()
    host = _HostTransformer()
    return host.apply_field_map(working, pd.DataFrame(index=working.index), field_map, "Students", ctx)


class TestFieldTransformApply:
    def test_plain_column_read(self):
        working = pd.DataFrame({"grade": ["1", "2"]})
        out = _apply({"Grade": FieldTransform(column="Grade")}, working)
        assert list(out["Grade"]) == ["1", "2"]

    def test_transform_applied_per_row(self):
        working = pd.DataFrame({"grade": ["1", "K"]})
        out = _apply({"Grade": FieldTransform(column="Grade", transform="grade_to_ceds")}, working)
        assert list(out["Grade"]) == ["01", "KG"]

    def test_absent_column_is_intended_blank_not_recorded(self):
        ctx = _ctx()
        working = pd.DataFrame({"present": ["A"]})
        out = _apply({"Out": FieldTransform(column="missing", transform="grade_to_ceds")}, working, ctx)
        assert out["Out"].isna().all()
        assert ctx.data_errors == []

    def test_unknown_transform_raises_with_allowed_set(self):
        spec = FieldTransform(column="grade", transform="no_such")
        with pytest.raises(ValueError, match="Unknown transform 'no_such'.*Allowed"):
            spec.apply(pd.DataFrame({"grade": ["1"]}), _HostTransformer(), "Grade", "Students", _ctx())

    def test_unknown_transform_via_apply_field_map_blanks_and_records(self):
        # The loop-level contract (mirrors the raw-dict pinned test in
        # test_transform_base) holds for TYPED input too: column-level error,
        # never raised out of apply_field_map.
        ctx = _ctx()
        working = pd.DataFrame({"grade": ["1", "2"]})
        out = _apply({"Out": FieldTransform(column="grade", transform="no_such")}, working, ctx)
        assert out["Out"].isna().all()
        assert len(ctx.data_errors) == 1
        assert "no_such" in ctx.data_errors[0]["sample"]

    def test_subclass_extended_allowlist_is_honored(self):
        # The runtime check reads the HOST's allowlist (subclass-overridable),
        # not the canonical config-load set.
        class _Extended(_HostTransformer):
            ALLOWED_TRANSFORMS = frozenset(BaseTransformer.ALLOWED_TRANSFORMS | {"double"})

            @staticmethod
            def double(value):
                return f"{value}{value}"

        spec = FieldTransform(column="x", transform="double")
        out = spec.apply(pd.DataFrame({"x": ["a"]}), _Extended(), "Out", "Students", _ctx())
        assert out == ["aa"]

    def test_row_resilience_preserved_for_typed_input(self):
        class _Raising(_HostTransformer):
            ALLOWED_TRANSFORMS = frozenset(BaseTransformer.ALLOWED_TRANSFORMS | {"explode_on_bad"})

            @staticmethod
            def explode_on_bad(value):
                if str(value) == "BAD":
                    raise ValueError("boom")
                return f"ok:{value}"

        ctx = _ctx()
        working = pd.DataFrame({"src": ["A", "BAD", "C"]})
        out = _Raising().apply_field_map(
            working,
            pd.DataFrame(index=working.index),
            {"Out": FieldTransform(column="src", transform="explode_on_bad")},
            "Students",
            ctx,
        )
        assert out["Out"].iloc[0] == "ok:A"
        assert pd.isna(out["Out"].iloc[1])
        assert out["Out"].iloc[2] == "ok:C"
        assert len(ctx.data_errors) == 1
        assert ctx.data_errors[0]["failed_rows"] == 1


class TestFieldFixedValueApply:
    def test_fills_every_row_with_the_literal(self):
        working = pd.DataFrame({"x": ["a", "b"]})
        out = _apply({"Role": FieldFixedValue(value="student")}, working)
        assert list(out["Role"]) == ["student", "student"]


class TestFieldAcademicYearApply:
    def test_start_date_uses_academic_start(self):
        out = _apply({"Start Date": FieldAcademicYear(use_academic_year=True)}, pd.DataFrame({"x": [1]}))
        assert list(out["Start Date"]) == ["2024-08-25"]

    def test_other_field_uses_academic_end(self):
        out = _apply({"End Date": FieldAcademicYear(use_academic_year=True)}, pd.DataFrame({"x": [1]}))
        assert list(out["End Date"]) == ["2025-07-25"]

    def test_explicit_value_wins(self):
        # Mirrors the legacy loop where a raw dict carrying 'value' hit the
        # fixed-value branch first, regardless of use_academic_year.
        spec = FieldAcademicYear(use_academic_year=False, value="2024-09-03")
        out = _apply({"Start Date": spec}, pd.DataFrame({"x": [1]}))
        assert list(out["Start Date"]) == ["2024-09-03"]


class TestFieldAppendYearApply:
    def test_appends_school_year_to_id(self):
        working = pd.DataFrame({"mtid": ["MT1", "MT2"]})
        out = _apply({"Class ID": FieldAppendYear(column="MTID")}, working)
        assert list(out["Class ID"]) == ["MT1_2025", "MT2_2025"]

    def test_blank_id_passes_through_unappended(self):
        working = pd.DataFrame({"mtid": ["MT1", ""]})
        out = _apply({"Class ID": FieldAppendYear(column="mtid")}, working)
        assert list(out["Class ID"]) == ["MT1_2025", ""]

    def test_append_disabled_reads_column_directly(self):
        working = pd.DataFrame({"mtid": ["MT1"]})
        out = _apply({"Class ID": FieldAppendYear(column="mtid", append_year_to_id=False)}, working)
        assert list(out["Class ID"]) == ["MT1"]

    def test_append_disabled_absent_column_is_blank(self):
        out = _apply(
            {"Class ID": FieldAppendYear(column="missing", append_year_to_id=False)},
            pd.DataFrame({"x": [1]}),
        )
        assert out["Class ID"].isna().all()


class TestConfigCarrierApply:
    """Variants that configure dedicated machinery elsewhere yield an intended
    blank in the generic loop (what the legacy dict sniffing produced), and
    never record a data error."""

    @pytest.mark.parametrize(
        "spec",
        [
            FieldEmailFormat(format="{student number}@x.ca"),
            FieldNameConfig(),
            FieldIdRolePair(student_id_col="Student ID", staff_id_col="Teacher ID"),
            FieldEnrollStatus(),
        ],
        ids=["email-format", "name-config", "id-role-pair", "enroll-status"],
    )
    def test_yields_intended_blank_and_no_error(self, spec):
        ctx = _ctx()
        out = _apply({"Out": spec}, pd.DataFrame({"x": [1, 2]}), ctx)
        assert out["Out"].isna().all()
        assert ctx.data_errors == []

    def test_prefilled_column_is_skipped(self):
        # The entity transformer fills the real column BEFORE apply_field_map;
        # the loop's already-present check must leave it alone.
        working = pd.DataFrame({"x": [1]})
        result = pd.DataFrame(index=working.index)
        result["Email Address"] = ["kept@x.ca"]
        out = _HostTransformer().apply_field_map(
            working, result, {"Email Address": FieldEmailFormat(format="{x}@x.ca")}, "Students", _ctx()
        )
        assert list(out["Email Address"]) == ["kept@x.ca"]


class TestTypedRawParity:
    """apply_field_map accepts raw YAML-shaped values and already-typed
    variants at the same boundary — both must produce identical frames."""

    RAW = {
        "Direct": "ColA",
        "Fixed": {"value": "V"},
        "Grade": {"column": "grade", "transform": "grade_to_ceds"},
        "Start Date": {"use_academic_year": True},
        "Class ID": {"column": "mtid", "append_year_to_id": True},
        "Missing": "not_a_column",
        "Auto": None,
    }

    def test_typed_equals_raw(self):
        working = pd.DataFrame({"cola": ["a", "b"], "grade": ["1", "K"], "mtid": ["M1", "M2"]})
        typed = {k: ensure_field_mapping(v) for k, v in self.RAW.items()}

        out_raw = _apply(dict(self.RAW), working.copy())
        out_typed = _apply(typed, working.copy())
        assert_frame_equal(out_raw, out_typed)


class TestEnsureFieldMapping:
    def test_typed_value_passes_through_by_identity(self):
        spec = FieldTransform(column="grade", transform="grade_to_ceds")
        assert ensure_field_mapping(spec) is spec

    def test_none_and_str_pass_through(self):
        assert ensure_field_mapping(None) is None
        assert ensure_field_mapping("Grade") == "Grade"

    def test_raw_dict_is_classified(self):
        spec = ensure_field_mapping({"column": "grade", "transform": "grade_to_ceds"})
        assert isinstance(spec, FieldTransform)
        assert spec.transform == "grade_to_ceds"

    def test_unrecognized_dict_passes_through_unwrapped(self):
        raw = {"unknown_key": "x"}
        assert ensure_field_mapping(raw) is raw

    def test_idempotent(self):
        once = ensure_field_mapping({"value": "V"})
        assert ensure_field_mapping(once) is once


class TestConfiguredFieldFailLoud:
    def test_base_apply_raises_not_implemented(self):
        class _Future(ConfiguredField):
            pass

        with pytest.raises(NotImplementedError, match="_Future"):
            _Future().apply(pd.DataFrame(), _HostTransformer(), "Out", "Students", _ctx())

    def test_runtime_allowlist_is_the_config_canonical_set(self):
        # Single source of truth: the class-level default must BE the
        # config-layer set (subclasses may extend their own copy).
        assert BaseTransformer.ALLOWED_TRANSFORMS is ALLOWED_TRANSFORMS


class TestLoadTimeTransformGate:
    """ALLOWED_TRANSFORMS is enforced fail-fast at CONFIG LOAD: an unknown
    ``transform:`` name never reaches the transform loop via a validated
    config."""

    def test_entity_config_rejects_unknown_transform(self):
        with pytest.raises(ValidationError) as exc_info:
            EntityConfig(
                source_files={"student_demographic": "students.csv"},
                field_map={"Grade": {"column": "grade", "transform": "grade_to_seds"}},
            )
        msg = str(exc_info.value)
        assert "Unknown transform 'grade_to_seds'" in msg
        assert "'Grade'" in msg
        assert "grade_to_ceds" in msg  # the allowed set is listed (actionable)

    def test_mapping_config_error_names_the_entity(self):
        with pytest.raises(ValidationError) as exc_info:
            MappingConfig(
                version="1.0",
                sis="myedbc",
                mappings={
                    "Students": {
                        "source_files": {"student_demographic": "students.csv"},
                        "field_map": {"Grade": {"column": "grade", "transform": "nope"}},
                    }
                },
            )
        msg = str(exc_info.value)
        assert "Students" in msg
        assert "Unknown transform 'nope'" in msg

    def test_known_transforms_accepted(self):
        cfg = EntityConfig(
            source_files={"student_demographic": "students.csv"},
            field_map={
                "Grade": {"column": "grade", "transform": "grade_to_ceds"},
                "Plain": {"column": "x"},  # empty transform is fine
            },
        )
        assert isinstance(cfg.field_map["Grade"], FieldTransform)

    def test_every_allowed_transform_is_a_base_transformer_method(self):
        # The allowlist names methods resolved via getattr(host, name) — a
        # drifted entry would fail at apply time; pin it at the source.
        for name in ALLOWED_TRANSFORMS:
            assert callable(getattr(BaseTransformer, name))
