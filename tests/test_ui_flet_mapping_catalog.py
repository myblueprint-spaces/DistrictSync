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

from src.ui_flet.mapping_catalog import (
    CUSTOM_ORIGIN_LABEL,
    ConfigSummary,
    active_output_entities,
    can_apply,
    catalog,
    disambiguated_labels,
    filtered_catalog,
    list_configs,
    post_apply_presentation,
    summarize_config,
)
from src.ui_flet.schedule_status import ScheduleState
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
        output_entities=(),
        output_labels=(),
        source_file_count=0,
        loaded_ok=False,
        # UNRESOLVABLE, not declared-empty (0038 S5): we could not read the config, so it
        # claims nobody and can never narrow anyone's district list to itself.
        district_domains=None,
        # A single explicit `config_dir` cannot express a tier, so the loader defines it as
        # "bundled"-equivalent and `_origin_of` re-uses that definition — asserted, not
        # assumed, by `TestOrigin.test_an_explicit_config_dir_reports_bundled`.
        origin="bundled",
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
    """One summary per ``available_configs`` id — ``_PINNED_FIRST`` leading, the rest alphabetical.

    The ORDER is presentation (which row a picker shows first); the SET is the safety property
    (a district that vanishes from every picker is unreachable). Both are asserted, separately,
    so a future pin can move a row without anyone being able to drop one.
    """
    from src.config.loader import available_configs

    summaries = list_configs(config_dir=bundle_dir)
    ids = [s.sis_type for s in summaries]
    enumerated = available_configs(bundle_dir)

    # The SET is a permutation of what `available_configs` enumerates — nothing added, nothing lost.
    assert sorted(ids) == sorted(enumerated)
    # The ORDER: the generic MyEducationBC mapping leads (QA, 2026-08-18), then the rest keep
    # `available_configs`' alphabetical order.
    assert ids[0] == "myedbc"
    assert ids[1:] == [sis for sis in enumerated if sis != "myedbc"]
    # Every bundled config is loadable.
    assert all(s.loaded_ok for s in summaries)


def test_a_pin_for_an_absent_config_is_simply_ignored() -> None:
    """``_pinned_order`` is a PERMUTATION — it can never invent a row or drop one.

    The positive twin of the ordering pin above: pinning an id the directory does not contain
    must not put a phantom district into what is structurally an allowlist.
    """
    from src.ui_flet.mapping_catalog import _pinned_order

    assert _pinned_order(["b", "a"]) == ["b", "a"]  # no pinned id present → order untouched
    assert _pinned_order(["a", "myedbc", "b"]) == ["myedbc", "a", "b"]
    assert sorted(_pinned_order(["a", "myedbc", "b"])) == ["a", "b", "myedbc"]


def test_bundled_catalog_renders_no_two_identical_labels(bundle_dir: Path) -> None:
    """G13 — no two BUNDLED catalog rows render the same display text.

    The highest-consequence wrong click in the product is picking the wrong district (a
    wrong mapping ships a wrong roster). A picker that renders two rows reading exactly
    the same makes that click a coin flip the admin cannot see — so identical display
    text is a product defect, not a cosmetic one.

    ``ConfigSummary.district_name`` IS the display text every picker paints
    (``screens/setup``, ``screens/convert``, ``screens/mapping``), so the invariant is
    asserted where it is produced rather than in each view. Scoped to the BUNDLED set —
    the only catalog this repo controls; a user-dropped YAML in
    ``~/.districtsync/mappings/`` is handled by runtime disambiguation (S5), not by this
    pin.

    Written RED-first (plan 0038 S3): ``sd51attendance_mapping.yaml`` inherits
    ``sd51myedbc``'s ``district_name`` via ``_base``, so today both render
    "SD51 - Boundary School District".
    """
    summaries = list_configs(config_dir=bundle_dir)

    by_label: dict[str, list[str]] = {}
    for summary in summaries:
        by_label.setdefault(summary.district_name, []).append(summary.sis_type)
    collisions = {label: ids for label, ids in by_label.items() if len(ids) > 1}

    assert not collisions, (
        "Bundled district configs render identical picker labels — an admin cannot tell "
        f"these rows apart: {collisions}. Give each config a distinct `district_name:`."
    )


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
        output_entities=(),
        output_labels=(),
        source_file_count=0,
        loaded_ok=loaded_ok,
        district_domains=() if loaded_ok else None,
        origin="bundled",  # the gate does not read origin; a shipped row keeps this axis quiet
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


# --------------------------------------------------------------------------- #
# post_apply_presentation — the post-Apply schedule-honesty truth table          #
# (plan 0034 Slice 1: a registered task keeps converting the OLD district)       #
# --------------------------------------------------------------------------- #
_ALL_STATES: tuple[ScheduleState | None, ...] = (
    ScheduleState.LIVE,
    ScheduleState.MISSING,
    ScheduleState.UNKNOWN,
    None,  # probe pending / not run (non-Windows) — same honesty tier as UNKNOWN
)


@pytest.mark.parametrize("hint", [True, False])
def test_live_schedule_warns_assertively_regardless_of_hint(hint: bool) -> None:
    """A LIVE read-back is definitive: the notice asserts the stale district, hint irrelevant."""
    pres = post_apply_presentation("Old District", schedule_state=ScheduleState.LIVE, hint_registered=hint)

    assert pres.notice is not None
    assert pres.notice.headline == "Your nightly schedule still uses Old District"
    assert pres.notice.detail == "Open Settings and Save to update it to the new district."


@pytest.mark.parametrize("state", [ScheduleState.UNKNOWN, None])
def test_unconfirmed_with_hint_hedges_never_asserts(state: ScheduleState | None) -> None:
    """UNKNOWN / probe-pending + the registered hint → the SAME notice, honestly hedged (D4).

    The hint alone must never assert a live schedule — the copy says "may still use", and the
    fix routing is identical (open Settings and Save).
    """
    pres = post_apply_presentation("Old District", schedule_state=state, hint_registered=True)

    assert pres.notice is not None
    assert pres.notice.headline == "Your nightly schedule may still use Old District"
    assert pres.notice.detail == (
        "We couldn't confirm the nightly schedule right now — "
        "open Settings and Save to make sure it uses the new district."
    )
    # Hedged means hedged: the assertive claim never appears in the unconfirmed branch.
    assert "still uses" not in pres.notice.headline


@pytest.mark.parametrize("state", [ScheduleState.UNKNOWN, None])
def test_unconfirmed_without_hint_raises_no_notice(state: ScheduleState | None) -> None:
    """Unconfirmed + no registered hint → no notice (never nag over a schedule nobody set up)."""
    pres = post_apply_presentation("Old District", schedule_state=state, hint_registered=False)

    assert pres.notice is None


@pytest.mark.parametrize("hint", [True, False])
def test_missing_schedule_raises_no_notice(hint: bool) -> None:
    """A definitively-absent task → no stale-district notice, even when the config expected one.

    An expected-but-missing schedule is Home/Setup's attention signal (re-register), not a
    stale-district risk — no task exists to keep converting the old district.
    """
    pres = post_apply_presentation("Old District", schedule_state=ScheduleState.MISSING, hint_registered=hint)

    assert pres.notice is None


@pytest.mark.parametrize("state", _ALL_STATES)
@pytest.mark.parametrize("hint", [True, False])
def test_healthy_detail_claims_folders_only_in_every_branch(state: ScheduleState | None, hint: bool) -> None:
    """The HEALTHY band's detail is honest in EVERY branch: folders only, never the schedule.

    Pins the removal of the old "Your folders and schedule are unchanged." reassurance — an
    unchanged LIVE task still carries the OLD ``--sis``, so "schedule unchanged" was exactly
    the hazard being masked.
    """
    pres = post_apply_presentation("Old District", schedule_state=state, hint_registered=hint)

    assert pres.healthy_detail == "Your folders are unchanged."
    assert "schedule" not in pres.healthy_detail.lower()


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_old_district_name_falls_back(blank: str) -> None:
    """A blank pre-Apply district name (unset district) → "the previous district" (total)."""
    pres = post_apply_presentation(blank, schedule_state=ScheduleState.LIVE, hint_registered=True)

    assert pres.notice is not None
    assert pres.notice.headline == "Your nightly schedule still uses the previous district"


# --------------------------------------------------------------------------- #
# active_output_entities (0038 S7) — Home's roster-size clause needs the TRUTH  #
# --------------------------------------------------------------------------- #
def test_output_entities_and_labels_are_one_ordering(bundle_dir: Path) -> None:
    """``output_labels`` is DERIVED from ``output_entities`` — one produced set, one order.

    Two independently-built tuples would let a picker's label order and Home's "which entity
    leads" rule drift apart while both looked right in isolation.
    """
    from src.ui_flet.home_status import ENTITY_LABELS

    for sis_type in ("sd48myedbc", "mbp_all", "mbp_core", "mbponly", "sd51attendance", "sd51myedbc"):
        summary = summarize_config(sis_type, config_dir=bundle_dir)
        assert summary.output_labels == tuple(ENTITY_LABELS.get(key, key) for key in summary.output_entities), sis_type


def test_a_rostering_config_leads_with_students(bundle_dir: Path) -> None:
    entities = summarize_config("sd48myedbc", config_dir=bundle_dir).output_entities
    assert entities[0] == "Students"
    assert set(entities) == {"Students", "Staff", "Family", "Classes", "Enrollments"}


def test_the_attendance_only_config_produces_ONLY_attendance(bundle_dir: Path) -> None:
    # The config whose record carries Students=0 by SHAPE — the "0 students" hazard.
    assert summarize_config("sd51attendance", config_dir=bundle_dir).output_entities == ("StudentAttendance",)


def test_the_myblueprint_only_config_leads_with_courses(bundle_dir: Path) -> None:
    assert summarize_config("mbponly", config_dir=bundle_dir).output_entities == ("CourseInfo", "StudentCourses")


def test_a_degraded_config_produces_no_entities(tmp_path: Path) -> None:
    """Unknowable → ``()``, which makes Home's size clause vanish rather than guess."""
    assert summarize_config("does_not_exist", config_dir=tmp_path).output_entities == ()


def test_active_output_entities_is_total_over_junk() -> None:
    assert active_output_entities("") == ()
    assert active_output_entities("   ") == ()
    assert active_output_entities("no_such_district_config") == ()


def test_active_output_entities_memoises_one_parse_per_district(monkeypatch, bundle_dir: Path) -> None:
    """The mount-cost claim, asserted: Home resolves ONE config, once per session."""
    from src.ui_flet import mapping_catalog

    mapping_catalog.reset_catalog_cache()
    calls: list[str] = []
    real = mapping_catalog.summarize_config
    monkeypatch.setattr(
        mapping_catalog,
        "summarize_config",
        lambda sis, **kw: (calls.append(sis), real(sis, **kw))[1],
    )

    first = mapping_catalog.active_output_entities("sd48myedbc", config_dir=bundle_dir)
    second = mapping_catalog.active_output_entities("sd48myedbc", config_dir=bundle_dir)

    assert first == second
    assert calls == ["sd48myedbc"], f"expected exactly one parse per district, got {calls}"
    mapping_catalog.reset_catalog_cache()


def test_reset_clears_the_per_config_cache_too(tmp_path: Path) -> None:
    """Both caches drop together — a fresh catalog beside a stale summary is a split brain."""
    from src.ui_flet import mapping_catalog

    _write(
        tmp_path,
        "later",
        """
        version: '1.0'
        sis: later
        district_name: Later
        global_config:
          enabled_entities: [Students]
        mappings:
          Students:
            source_files:
              student_demographic: StudentDemographicInformation.txt
            field_map:
              "User ID": student number
        """,
    )
    mapping_catalog.reset_catalog_cache()
    assert mapping_catalog.active_output_entities("later", config_dir=tmp_path) == ("Students",)

    (tmp_path / "later_mapping.yaml").unlink()
    assert mapping_catalog.active_output_entities("later", config_dir=tmp_path) == ("Students",)  # memoised

    mapping_catalog.reset_catalog_cache()
    assert mapping_catalog.active_output_entities("later", config_dir=tmp_path) == ()


def test_every_bundled_config_can_name_its_own_roster_size(bundle_dir: Path) -> None:
    """The reality-read across all ELEVEN shipped configs — the AC, over real data.

    For each config: the size clause must (a) exist, (b) name an entity that config genuinely
    produces, and (c) never mention students on a config that does not emit Students. Reads the
    real YAMLs rather than a hand-listed table, so a twelfth config joins this sweep for free.
    """
    from src.config.loader import available_configs
    from src.ui_flet.home_status import SIZE_NOUNS, size_clause

    ids = available_configs(bundle_dir)
    assert len(ids) == 20, f"the bundled config count moved ({len(ids)}) — keep this pin in lockstep"

    # A record where EVERY entity key is non-zero and DISTINCT, so the entity the clause chose
    # is identifiable from the number it printed.
    counts = {key: 1000 + index for index, key in enumerate(SIZE_NOUNS)}

    for sis_type in ids:
        entities = summarize_config(sis_type, config_dir=bundle_dir).output_entities
        # The record is this config's OWN — the sweep is about which entity gets named, not
        # about the Stage-7 different-district guard (which has its own rows in
        # ``tests/test_ui_flet_home_status.py``). Stamping the district keeps the two axes
        # separate: without it the sweep would silently exercise the "no district on the
        # record" path instead of the agreeing one.
        clause = size_clause(dict(counts, sis_type=sis_type), entities, expected_sis_type=sis_type)
        assert clause, f"{sis_type} produces {entities} but Home can name none of it"
        leading = next(key for key in SIZE_NOUNS if key in set(entities))
        assert f"{counts[leading]:,}" in clause, f"{sis_type} named the wrong entity: {clause}"
        if "Students" not in entities:
            assert "student" not in clause, f"{sis_type} does not emit Students but the clause says {clause!r}"


# --------------------------------------------------------------------------- #
# ORIGIN — which TIER a config's YAML came out of (plan 0044 S2)                #
#                                                                             #
# Every case here runs against the REAL user-then-bundled pair, via the autouse #
# `isolated_user_profile` redirect + `authoring.write_overlay`. That is not     #
# ceremony: a single-dir fixture is "bundled"-equivalent BY DEFINITION           #
# (`loader._require_search_pair` refuses a one-dir seam for exactly this        #
# reason), so an origin assertion made through `config_dir=` would be vacuous.   #
# Each "is marked" therefore carries a shipped-row TWIN in the same build.       #
# --------------------------------------------------------------------------- #
@pytest.fixture
def custom_overlay(monkeypatch) -> str:
    """Write a REAL ``sd93custom`` overlay into the isolated user mappings dir. Returns its id.

    Goes through ``authoring.write_overlay`` (which load-backs through the real loader), so
    the row these tests read is a config the app could genuinely run — not a hand-planted file
    that only looks like one. Clears any leaked ``sys.frozen`` / ``sys._MEIPASS`` first so
    ``bundle_mappings_dir()`` resolves to the project's ``config/mappings``.
    """
    import sys

    from src.config.authoring import OverlaySpec, write_overlay

    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    write_overlay(
        OverlaySpec(
            sd_number=93,
            district_name="SD93 - Origin Test",
            district_domains=("sd93.bc.ca",),
            base="myedbc",
        ),
        overwrite=False,
    )
    from src.ui_flet import mapping_catalog

    mapping_catalog.reset_catalog_cache()  # the write happened after the fixture's own reset
    return "sd93custom"


class TestOrigin:
    def test_a_user_dir_config_reports_user_and_a_shipped_one_reports_bundled(self, custom_overlay: str) -> None:
        """The pair that makes either half mean anything — same build, same call, two tiers."""
        added = summarize_config(custom_overlay)
        shipped = summarize_config("myedbc")

        assert added.origin == "user"
        assert added.loaded_ok is True, "the overlay must be a config the app can really load"
        assert shipped.origin == "bundled"
        assert shipped.loaded_ok is True

    def test_every_row_of_a_real_catalog_build_carries_an_origin(self, custom_overlay: str) -> None:
        """The acceptance criterion: ``origin`` is populated on EVERY row, ``"user"`` exactly
        for the file in the user dir."""
        rows = {s.sis_type: s.origin for s in list_configs()}

        assert set(rows.values()) <= {"user", "bundled"}
        assert rows[custom_overlay] == "user"
        assert [sis for sis, origin in rows.items() if origin == "user"] == [custom_overlay]
        assert len(rows) == 21, f"the shipped 20 plus the overlay; got {sorted(rows)}"

    def test_an_explicit_config_dir_reports_bundled(self, custom_overlay: str) -> None:
        """The loader's own rule, ASSERTED rather than assumed: one dir cannot express a tier.

        The dir handed in here is literally the USER mappings dir holding the overlay — so a
        path-parent-derived origin would say ``"user"``. It must say ``"bundled"``, because
        that is what ``load_config`` does with a single ``config_dir`` (no user-dir domains
        floor, an invalid domain row keeps its loud raise), and two spellings of one rule that
        could disagree is the defect this pins.

        The TWIN is the same id read through the real PAIR, in the same test: the file is
        identical, so only the seam can explain the two answers. (Through the single dir the
        overlay also DEGRADES — its ``_base: myedbc`` is unreachable when the bundled dir is
        not searched — which is the same "one dir cannot express a tier" fact from the other
        side, and shows origin is resolved before any load is attempted.)
        """
        from src.utils.paths import user_mappings_dir

        user_dir = user_mappings_dir()
        assert (user_dir / f"{custom_overlay}_mapping.yaml").exists(), "the file really is in that dir"

        assert summarize_config(custom_overlay, config_dir=user_dir).origin == "bundled"
        assert summarize_config(custom_overlay).origin == "user", "the twin: the real pair still sees the tier"

    def test_a_MALFORMED_user_overlay_degrades_and_STILL_reports_user(self, monkeypatch) -> None:
        """The row most likely to need the marker is the one that cannot be read.

        Origin is resolved BEFORE the load is attempted, so a broken YAML in the user dir
        still renders as "Added on this computer" — which is precisely the fact that tells an
        admin the file is theirs to fix or remove. A degraded row that claimed ``"bundled"``
        would read as a fault in the shipped product.
        """
        import sys

        from src.utils.paths import user_mappings_dir

        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        (user_mappings_dir() / "sd94custom_mapping.yaml").write_text(
            "mappings: [this is not a valid mappings dict\n", encoding="utf-8"
        )

        summary = summarize_config("sd94custom")

        assert summary.loaded_ok is False
        assert summary.origin == "user"
        assert summary.district_name == "sd94custom"  # the raw-id fallback, unchanged

    def test_origin_is_TOTAL_over_a_raising_resolver(self, monkeypatch) -> None:
        """``user_mappings_dir()`` mkdir-s, so a locked-down profile can raise inside the
        lookup. Nothing may escape: origin is a presentation fact, and losing it must cost a
        marker, never a district row."""
        from src.ui_flet import mapping_catalog

        monkeypatch.setattr(
            mapping_catalog,
            "resolve_config_path",
            lambda *_a, **_kw: (_ for _ in ()).throw(OSError("locked-down profile")),
        )

        summary = summarize_config("myedbc")

        assert summary.origin == "bundled"
        assert summary.loaded_ok is True, "the load itself was untouched — only origin degraded"

    def test_a_config_that_exists_NOWHERE_reports_bundled(self) -> None:
        """A miss is not a user file: the fallback direction is "unmarked", never a false claim
        that we did not ship a mapping we ship."""
        assert summarize_config("no_such_district_config").origin == "bundled"


class TestAConfigAddedOnThisComputerRidesEveryList:
    """Owner finding (2026-09-03): an admin whose address matched a shipped district could not
    see the mapping they had authored themselves — the domain filter narrowed it away and the
    "Show all districts" escape had been retired a month earlier, so their own file was
    unreachable from every one of the four pickers.

    These run against the REAL user-then-bundled pair (``config_dir=`` is "bundled" by
    definition — see the block comment above ``TestOrigin``), so the ``origin`` the rule reads
    is the one the loader really resolves.
    """

    def test_it_survives_a_match_that_excludes_it(self, custom_overlay: str) -> None:
        """The rule, with its negative twin in the same assertion block: an unmatched SHIPPED
        row IS dropped, so the filter demonstrably fired and the kept row is not just a filter
        that never ran (CANDIDATES: no vacuous greens)."""
        visible = {s.sis_type for s in filtered_catalog("sd48.bc.ca", saved_sis="sd48myedbc").summaries}

        assert custom_overlay in visible, "the admin's own mapping was filtered out of their picker"
        assert "sd48myedbc" in visible, "premise: the matched district is present"
        assert "sd51myedbc" not in visible, "the twin: an unmatched shipped row is still narrowed away"

    def test_it_is_kept_by_ORIGIN_and_not_by_its_own_domain(self, custom_overlay: str, monkeypatch) -> None:
        """``custom_overlay`` declares ``sd93.bc.ca``, so a rule that only ever kept CLAIMED
        rows would pass the row above for the wrong reason. Here the overlay claims nothing at
        all — and is still there."""
        import sys

        from src.config.authoring import OverlaySpec, write_overlay
        from src.ui_flet import mapping_catalog

        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        write_overlay(
            OverlaySpec(sd_number=94, district_name="SD94 - Unclaimed", district_domains=(), base="myedbc"),
            overwrite=False,
        )
        mapping_catalog.reset_catalog_cache()

        visible = {s.sis_type for s in filtered_catalog("sd48.bc.ca", saved_sis="sd48myedbc").summaries}

        assert "sd94custom" in visible
        assert "sd51myedbc" not in visible, "the twin: the filter is still narrowing shipped rows"


class TestTheProvenanceMarkerOnALabel:
    def _row(self, sis: str, name: str, origin: str) -> ConfigSummary:
        return ConfigSummary(
            sis_type=sis,
            district_name=name,
            output_entities=(),
            output_labels=(),
            source_file_count=0,
            loaded_ok=True,
            district_domains=(),
            origin=origin,  # type: ignore[arg-type]
        )

    def test_the_added_row_is_marked_and_the_shipped_row_is_NOT(self) -> None:
        labels = disambiguated_labels(
            (self._row("sd93custom", "SD93", "user"), self._row("sd48myedbc", "Sea to Sky", "bundled"))
        )

        assert labels["sd93custom"] == f"SD93 — {CUSTOM_ORIGIN_LABEL}"
        assert labels["sd48myedbc"] == "Sea to Sky"
        assert CUSTOM_ORIGIN_LABEL not in labels["sd48myedbc"]

    def test_a_same_name_collision_carries_BOTH_the_id_suffix_and_the_marker(self) -> None:
        """The shape the ``sd<num>custom``-beside-``sd<num>myedbc`` namespace anticipates: an
        added config named exactly like the shipped one. The ID is what separates the two rows
        (so BOTH keep their suffix, not just the later one); the marker only says which of
        them lives on this computer — it is appended AFTER, and it never joins detection.
        """
        labels = disambiguated_labels(
            (self._row("sd48custom", "Sea to Sky", "user"), self._row("sd48myedbc", "Sea to Sky", "bundled"))
        )

        assert labels["sd48custom"] == f"Sea to Sky (sd48custom) — {CUSTOM_ORIGIN_LABEL}"
        assert labels["sd48myedbc"] == "Sea to Sky (sd48myedbc)"
        assert len(set(labels.values())) == 2

    def test_the_marker_does_not_CREATE_a_collision_between_two_added_configs(self) -> None:
        """Two added configs sharing a district name must still be told apart by their ids —
        the marker is on both, so it cannot be the thing that separates them."""
        labels = disambiguated_labels(
            (self._row("sd93custom", "Same Name", "user"), self._row("sd94custom", "Same Name", "user"))
        )

        assert labels["sd93custom"] == f"Same Name (sd93custom) — {CUSTOM_ORIGIN_LABEL}"
        assert labels["sd94custom"] == f"Same Name (sd94custom) — {CUSTOM_ORIGIN_LABEL}"
        assert len(set(labels.values())) == 2

    def test_the_marker_reaches_a_REAL_catalog_row(self, custom_overlay: str) -> None:
        """The synthetic rows above pin the rule; this proves it fires on a config actually
        written to disk, with a shipped twin in the same label map."""
        labels = disambiguated_labels(list_configs())

        assert labels[custom_overlay].endswith(f" — {CUSTOM_ORIGIN_LABEL}")
        assert CUSTOM_ORIGIN_LABEL not in labels["myedbc"]

    def test_the_label_is_the_SINGLE_source_of_the_marker_words(self) -> None:
        """The words live in one constant — owner-approved, PII-free, and making no claim
        about who authored the file or whether it can be edited."""
        assert CUSTOM_ORIGIN_LABEL == "Added on this computer"
        lowered = CUSTOM_ORIGIN_LABEL.lower()
        for banned in ("edit", "soon", "later", "unsupported", "invalid", "you"):
            assert banned not in lowered, CUSTOM_ORIGIN_LABEL


class TestTheCatalogInvalidationRule:
    def test_a_freshly_written_overlay_is_STALE_until_the_cache_is_dropped(self, monkeypatch) -> None:
        """The rule that keeps ``src/config/`` from importing ``src/ui_flet/``: the UI caller
        invalidates after a successful write.

        Both halves are here on purpose. The STALE half asserts an ABSENCE and would pass for
        the wrong reason if memoisation were ever removed — it pins a documented residual
        (``catalog``'s docstring), not a guarantee. The FRESH half is what makes it mean
        something: ``reset_catalog_cache()`` really is the invalidation, not a comment.
        """
        import sys

        from src.config.authoring import OverlaySpec, write_overlay
        from src.ui_flet import mapping_catalog

        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)

        before = {s.sis_type for s in catalog()}
        assert "sd93custom" not in before
        assert len(before) == 20, "the positive twin: the build really did read the shipped catalog"

        write_overlay(
            OverlaySpec(
                sd_number=93,
                district_name="SD93 - Invalidation Test",
                district_domains=("sd93.bc.ca",),
                base="myedbc",
            ),
            overwrite=False,
        )

        assert "sd93custom" not in {s.sis_type for s in catalog()}, "the memo is stale, as documented"

        mapping_catalog.reset_catalog_cache()

        after = {s.sis_type for s in catalog()}
        assert "sd93custom" in after
        assert len(after) == 21

    def test_a_deleted_overlay_also_needs_the_invalidation(self, custom_overlay: str) -> None:
        """The same rule on the delete side — ``delete_overlay`` does not clear the memo
        either, so an offered district would outlive its file until the UI caller invalidates."""
        from src.config.authoring import delete_overlay
        from src.ui_flet import mapping_catalog

        assert custom_overlay in {s.sis_type for s in catalog()}

        assert delete_overlay(custom_overlay) is True

        assert custom_overlay in {s.sis_type for s in catalog()}, "stale until invalidated"
        mapping_catalog.reset_catalog_cache()
        assert custom_overlay not in {s.sis_type for s in catalog()}
