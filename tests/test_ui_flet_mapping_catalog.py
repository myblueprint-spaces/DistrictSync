"""Unit tests for the pure Mapping config-catalog (IA-8a, COUNTED — the trust-relevant core).

Covers:
- the load-bearing empty-``enabled_entities``-means-all resolution vs an explicit strict SUBSET
  (against the REAL bundled configs via the ``config_dir`` seam) — the single most important
  assertion in the slice: the summary tells an admin the TRUE output-CSV set (picking ``mbp_core``
  DROPS the 5 rostering CSVs);
- the total-over-a-failing-config degradation (both a malformed-YAML raise and a missing id) —
  ``loaded_ok=False``, ``output_labels=()``, ``district_name`` = the raw id, NEVER a raise;
- the PRIVACY guarantee — a planted sentinel (fake path / column name) in a config's validation
  error appears in NO field of the degraded ``ConfigSummary`` (structure only, never a raw error);
- ``list_configs`` enumeration order + the de-duped ``source_file_count``;
- the ``district_name`` raw-id fallback for a config with an empty ``district_name``.

Pure derivation → fixture mapping YAMLs via the ``config_dir`` seam + the real bundled configs.
No flet control instantiation (the ``build_mapping`` view is coverage-omitted, manually smoked).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.ui_flet.mapping_catalog import ConfigSummary, can_apply, list_configs, summarize_config
from src.utils.paths import bundle_mappings_dir


@pytest.fixture()
def bundle_dir() -> Path:
    """The real bundled ``config/mappings/`` dir — the source of truth for the subset cases."""
    return bundle_mappings_dir()


def _write(directory: Path, sis_type: str, body: str) -> None:
    (directory / f"{sis_type}_mapping.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Output-label derivation — empty-means-all vs explicit strict subset [gate #2] #
# --------------------------------------------------------------------------- #
def test_empty_enabled_entities_means_all(tmp_path: Path) -> None:
    """A config with NO ``enabled_entities`` → ``output_labels`` covers ALL ``mappings.keys()``."""
    _write(
        tmp_path,
        "allents",
        """
        version: '1.0'
        sis: allents
        district_name: All Entities Test
        global_config: {}
        mappings:
          Students:
            source_files:
              student_demographic: StudentDemographicInformation.txt
            field_map:
              "User ID": student number
          CourseInfo:
            source_files:
              course_info: CourseInformation.txt
            field_map:
              "Course Code": course code
          StudentCourses:
            source_files:
              course_info: CourseInformation.txt
            field_map:
              "Course Code": course code
        """,
    )
    summary = summarize_config("allents", config_dir=tmp_path)

    assert summary.loaded_ok is True
    # Empty enabled_entities → all mappings, in the canonical rostering-then-myBlueprint order.
    assert summary.output_labels == ("Students", "Courses", "Student courses")


def test_explicit_subset_is_exactly_that_subset(bundle_dir: Path) -> None:
    """``mbp_core`` (explicit ``enabled_entities`` = Students + the 2 course CSVs) → EXACTLY those.

    The load-bearing product-ux truth: picking ``mbp_core`` DROPS the 5 rostering CSVs — no
    Staff / Family / Classes / Enrollments. Asserts the exact label strings so a drift in the
    single-source ``ENTITY_LABELS`` map fails this test.
    """
    summary = summarize_config("mbp_core", config_dir=bundle_dir)

    assert summary.loaded_ok is True
    assert summary.output_labels == ("Students", "Courses", "Student courses")
    # The consequence the summary makes visible: none of the rostering-only CSVs are produced.
    for dropped in ("Staff", "Family", "Classes", "Enrollments"):
        assert dropped not in summary.output_labels


def test_bundled_myedbc_produces_the_five_rostering_csvs(bundle_dir: Path) -> None:
    """The bundled ``myedbc`` (rostering ``enabled_entities``) → exactly the 5 rostering CSVs."""
    summary = summarize_config("myedbc", config_dir=bundle_dir)

    assert summary.loaded_ok is True
    assert summary.output_labels == ("Students", "Staff", "Family", "Classes", "Enrollments")


def test_mbponly_produces_only_the_two_course_csvs(bundle_dir: Path) -> None:
    """``mbponly`` (``enabled_entities`` = the 2 course entities) → only the course CSVs, in order."""
    summary = summarize_config("mbponly", config_dir=bundle_dir)

    assert summary.loaded_ok is True
    assert summary.output_labels == ("Courses", "Student courses")


def test_non_standard_entity_key_falls_back_to_the_raw_key(tmp_path: Path) -> None:
    """An enabled entity NOT in the canonical spine surfaces via its raw key (total, appended)."""
    _write(
        tmp_path,
        "extra",
        """
        version: '1.0'
        sis: extra
        district_name: Extra Entity Test
        global_config: {}
        mappings:
          Students:
            source_files:
              student_demographic: StudentDemographicInformation.txt
            field_map:
              "User ID": student number
          CustomThing:
            source_files:
              custom: CustomFile.txt
            field_map:
              "X": x
        """,
    )
    summary = summarize_config("extra", config_dir=tmp_path)

    assert summary.loaded_ok is True
    # Canonical key first (Students), then the non-standard key appended by its raw name.
    assert summary.output_labels == ("Students", "CustomThing")


# --------------------------------------------------------------------------- #
# Source-file de-dupe count                                                      #
# --------------------------------------------------------------------------- #
def test_source_file_count_dedupes_shared_files(tmp_path: Path) -> None:
    """The same GDE file feeding several entities counts ONCE (distinct filenames, not occurrences)."""
    _write(
        tmp_path,
        "shared",
        """
        version: '1.0'
        sis: shared
        district_name: Shared Files Test
        global_config: {}
        mappings:
          Students:
            source_files:
              a: Same.txt
            field_map:
              "User ID": id
          CourseInfo:
            source_files:
              b: Same.txt
              c: Other.txt
            field_map:
              "Course Code": code
        """,
    )
    summary = summarize_config("shared", config_dir=tmp_path)

    assert summary.loaded_ok is True
    # {Same.txt, Other.txt} — Same.txt shared across both entities counts once.
    assert summary.source_file_count == 2


def test_enabled_entity_absent_from_mappings_is_skipped(tmp_path: Path) -> None:
    """An ``enabled_entities`` key with no matching ``mappings`` entry is skipped, never crashes.

    Defensive: an entity enabled but undefined contributes no label / no source file (its key is
    not in ``mappings.keys()``, so it never reaches ``output_labels``; the file-count loop skips
    the missing entity). The config still summarizes cleanly.
    """
    _write(
        tmp_path,
        "ghost",
        """
        version: '1.0'
        sis: ghost
        district_name: Ghost Entity Test
        global_config:
          enabled_entities: [Students, CourseInfo]
        mappings:
          Students:
            source_files:
              a: A.txt
            field_map:
              "User ID": id
        """,
    )
    summary = summarize_config("ghost", config_dir=tmp_path)

    assert summary.loaded_ok is True
    # Only Students is defined; the enabled-but-undefined CourseInfo contributes nothing.
    assert summary.output_labels == ("Students",)
    assert summary.source_file_count == 1


def test_source_file_count_zero_when_none(tmp_path: Path) -> None:
    """A config whose enabled entities declare no source files → ``source_file_count == 0``."""
    _write(
        tmp_path,
        "nofiles",
        """
        version: '1.0'
        sis: nofiles
        district_name: No Files Test
        global_config:
          enabled_entities: [StudentAttendance]
        mappings:
          StudentAttendance:
            source_files: {}
            field_map:
              "Date": date
        """,
    )
    summary = summarize_config("nofiles", config_dir=tmp_path)

    assert summary.loaded_ok is True
    assert summary.output_labels == ("Attendance",)
    assert summary.source_file_count == 0


# --------------------------------------------------------------------------- #
# Total over a failing config + no-raw-error privacy [gate #3]                   #
# --------------------------------------------------------------------------- #
def test_missing_config_id_degrades_never_raises(tmp_path: Path) -> None:
    """A missing config id → a safe degraded summary (``loaded_ok=False``), never a raise."""
    summary = summarize_config("does_not_exist", config_dir=tmp_path)

    assert summary == ConfigSummary(
        sis_type="does_not_exist",
        district_name="does_not_exist",  # friendly fallback to the raw id
        output_labels=(),
        source_file_count=0,
        loaded_ok=False,
    )


def test_malformed_yaml_degrades_never_raises(tmp_path: Path) -> None:
    """A malformed-YAML config (a ``load_config`` raise path) → a safe degraded summary, no raise."""
    _write(
        tmp_path,
        "broken",
        """
        version: '1.0'
        sis: broken
        mappings: [this is not a valid mappings dict
        """,
    )
    summary = summarize_config("broken", config_dir=tmp_path)

    assert summary.loaded_ok is False
    assert summary.output_labels == ()
    assert summary.source_file_count == 0
    assert summary.district_name == "broken"  # raw-id fallback
    assert summary.sis_type == "broken"


def test_pydantic_validation_error_degrades_with_no_raw_error_text(tmp_path: Path) -> None:
    """A Pydantic-``ValueError`` config → degraded; NO field echoes the raw error text (privacy).

    Plants a recognizable sentinel (a fake column name) that WILL appear in the Pydantic
    validation error, then asserts it appears in NO field of the returned ``ConfigSummary`` — the
    degraded summary carries STRUCTURE only, never a raw exception / path / Pydantic message.
    """
    sentinel = "SENTINEL_FAKE_COLUMN_xyz"
    # `course_start_grade` must be 8/9/10 — a bad value raises a Pydantic ValueError whose message
    # would carry the offending value; the sentinel rides in as an invalid extra global key too.
    _write(
        tmp_path,
        "invalid",
        f"""
        version: '1.0'
        sis: invalid
        district_name: Invalid Config
        global_config:
          course_start_grade: 999
          school_year_sources:
            {sentinel}: {sentinel}
        mappings:
          Students:
            source_files:
              a: A.txt
            field_map:
              "User ID": id
        """,
    )
    summary = summarize_config("invalid", config_dir=tmp_path)

    assert summary.loaded_ok is False
    assert summary.output_labels == ()
    assert summary.source_file_count == 0
    # Privacy: the raw error text (incl. the sentinel) must NOT surface in any admin-facing field.
    for field_value in (summary.sis_type, summary.district_name):
        assert sentinel not in field_value
    # And no output label carries it either.
    assert all(sentinel not in label for label in summary.output_labels)


# --------------------------------------------------------------------------- #
# district_name fallback                                                         #
# --------------------------------------------------------------------------- #
def test_empty_district_name_falls_back_to_raw_id(tmp_path: Path) -> None:
    """A valid config with an empty ``district_name`` → the summary's name falls back to the raw id."""
    _write(
        tmp_path,
        "noname",
        """
        version: '1.0'
        sis: noname
        global_config: {}
        mappings:
          Students:
            source_files:
              a: A.txt
            field_map:
              "User ID": id
        """,
    )
    summary = summarize_config("noname", config_dir=tmp_path)

    assert summary.loaded_ok is True
    assert summary.district_name == "noname"


# --------------------------------------------------------------------------- #
# list_configs — enumeration order + degraded inclusion                          #
# --------------------------------------------------------------------------- #
def test_list_configs_enumerates_all_ids_in_order(bundle_dir: Path) -> None:
    """``list_configs`` returns one summary per ``available_configs`` id, in ``available_configs`` order."""
    from src.config.loader import available_configs

    summaries = list_configs(config_dir=bundle_dir)
    assert [s.sis_type for s in summaries] == available_configs(bundle_dir)
    # Every bundled config is loadable.
    assert all(s.loaded_ok for s in summaries)


def test_list_configs_includes_degraded_config_never_omits(tmp_path: Path) -> None:
    """A dir with a broken config still yields a summary for it (degraded), never omitted or crashed."""
    _write(
        tmp_path,
        "good",
        """
        version: '1.0'
        sis: good
        district_name: Good Config
        global_config: {}
        mappings:
          Students:
            source_files:
              a: A.txt
            field_map:
              "User ID": id
        """,
    )
    _write(
        tmp_path,
        "bad",
        """
        version: '1.0'
        sis: bad
        mappings: [broken
        """,
    )
    summaries = list_configs(config_dir=tmp_path)

    by_id = {s.sis_type: s for s in summaries}
    assert set(by_id) == {"good", "bad"}
    assert by_id["good"].loaded_ok is True
    assert by_id["bad"].loaded_ok is False  # listed, not omitted


# --------------------------------------------------------------------------- #
# can_apply — the pure Mapping Apply-gate truth table (D1)                       #
# --------------------------------------------------------------------------- #
def _summary(sis: str, *, loaded_ok: bool) -> ConfigSummary:
    """A minimal ConfigSummary for the gate truth-table (only sis_type + loaded_ok matter)."""
    return ConfigSummary(
        sis_type=sis,
        district_name=sis,
        output_labels=(),
        source_file_count=0,
        loaded_ok=loaded_ok,
    )


def test_can_apply_loadable_and_different_is_true() -> None:
    """A loadable config that differs from the persisted current is applyable."""
    assert can_apply(_summary("sd40myedbc", loaded_ok=True), "myedbc") is True


def test_can_apply_same_as_persisted_is_false() -> None:
    """A no-op switch (pending IS the persisted current) is not applyable."""
    assert can_apply(_summary("myedbc", loaded_ok=True), "myedbc") is False


def test_can_apply_broken_config_is_false() -> None:
    """A config that failed to load is never applyable — the next run would fail."""
    assert can_apply(_summary("sd40myedbc", loaded_ok=False), "myedbc") is False


def test_can_apply_none_pending_is_false() -> None:
    """No selection (``None``) is not applyable."""
    assert can_apply(None, "myedbc") is False


def test_can_apply_revert_after_apply_is_possible() -> None:
    """The load-bearing D1 fix: after switching A→B, reverting B→A is applyable again.

    The gate compares against the PERSISTED current (not a frozen mount instance), so once B is
    persisted, re-selecting A (loadable, != B) is applyable — a switch can always be undone
    without a restart (the pre-fix bug compared against the stale mount value, so revert failed).
    """
    a = _summary("myedbc", loaded_ok=True)
    b = _summary("mbp_core", loaded_ok=True)
    # Before apply: persisted=A, pending=B → applyable.
    assert can_apply(b, "myedbc") is True
    # Just after applying B: persisted=B, pending=B → no-op, disabled.
    assert can_apply(b, "mbp_core") is False
    # Revert: persisted=B, pending=A → applyable again (the previously-impossible case).
    assert can_apply(a, "mbp_core") is True
