"""The Class Information export is optional where blended detection is off (plan 0053 S13e).

Owner ruling 2026-09-30, verbatim: *"Optional if blended off: ClassInformation becomes optional
for any config with blended detection off; when missing, co-teachers are left out and Home is
amber (SD45 every night)."* Since 2026-09-28 every file a CRITICAL entity lists is required
(``pipeline.check_required_inputs``); this slice adds ONE exception, DERIVED from the config's own
declarations — never a per-district list — through ONE predicate every consumer reads:

* ``outcomes.blended_detection_off`` is the ONE reading of ``global_config.blended_classes``
  (Classes skips detection on it; the predicate below lets the file go on it) — pinned below by
  a source scan, so a second spelling of the opt-out cannot drift from the exception;
* ``outcomes.source_file_may_be_absent(entity, role, *, global_config)`` is the ONE optional-input
  predicate: Family's files (owner 2026-09-28) and the ``class_info`` role where detection is off.
  The input gate applies it per listing; ``pipeline.optional_source_files`` (Convert's missing-file
  line) reads it too — both pinned against a statement of the owner's rule over EVERY bundled
  config, with a doctored twin;
* when the file is missing or row-less, Enrollments' co-teacher path records
  ``OutcomeNote.COTEACHER_SOURCE_UNUSABLE`` (count 1 — the file) with ONE warning naming the file
  in the mapping's spelling — it used to return in silence — so Home and Run History are amber
  every night it persists (SD45, which sends no Class Information, every night).

Blended-ON configs are unchanged: a missing or row-less Class Information still stops the night
(``incomplete_input``), pinned end to end on the SD74 fixture. All data is synthetic (the
``tests/test_contract.py`` builders).
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pandas as pd
import pytest

from src.config.app_config import AppConfig
from src.config.loader import available_configs, load_config
from src.config.models import EntityConfig, GlobalConfig, MappingConfig
from src.etl import outcomes
from src.etl.errors import IncompleteInputError
from src.etl.outcomes import (
    CLASS_INFORMATION_ROLE,
    ENTITY_CRITICALITY,
    EntityCriticality,
    OutcomeKind,
    OutcomeLedger,
    OutcomeNote,
    OutcomeReason,
    blended_detection_off,
    source_file_may_be_absent,
)
from src.etl.pipeline import (
    check_required_inputs,
    configured_entity_order,
    extract_required_files,
    optional_source_files,
    run_pipeline,
)
from src.etl.transformers.context import ClassArtifacts, TransformContext
from src.etl.transformers.enrollments import COTEACHERS_LEFT_OUT_LOG_ANCHOR, EnrollmentTransformer
from src.history.store import read_run_records
from src.ui_flet.convert_result import ConvertStatus
from src.ui_flet.failure_copy import note_sentence
from src.ui_flet.screens.convert import convert_job
from tests.test_contract import (
    _SUPPORT_STAFF_ID,
    _create_sd45_inputs,
    _create_sd51_inputs,
    _create_sd74_inputs,
    _write_class_info,
)
from tests.test_coteacher_standing_warning import _assert_partial_with_the_note, _latest

_REPO = Path(__file__).resolve().parents[1]
_NOTE = OutcomeNote.COTEACHER_SOURCE_UNUSABLE
_LEFT_OUT_LINE = COTEACHERS_LEFT_OUT_LOG_ANCHOR


# --------------------------------------------------------------------------- #
# The ONE reading of the opt-out and the ONE optional-input predicate            #
# --------------------------------------------------------------------------- #
class TestThePredicate:
    @pytest.mark.parametrize(
        ("global_config", "off"),
        [
            ({}, False),  # absent key = ON (every district but the opt-outs)
            ({"blended_classes": True}, False),
            ({"blended_classes": False}, True),
            ({"blended_classes": None}, False),  # never truthiness
            ({"blended_classes": "false"}, False),  # only the resolved bool False is off
            ({"blended_classes": 0}, False),
        ],
    )
    def test_blended_detection_off_is_the_resolved_bool(self, global_config: dict, off: bool) -> None:
        assert blended_detection_off(global_config) is off

    def test_class_information_is_optional_exactly_where_detection_is_off(self) -> None:
        off, on = {"blended_classes": False}, {}
        assert source_file_may_be_absent("Classes", CLASS_INFORMATION_ROLE, global_config=off) is True
        # Twins: detection on keeps it required; another role is never excused by the opt-out.
        assert source_file_may_be_absent("Classes", CLASS_INFORMATION_ROLE, global_config=on) is False
        assert source_file_may_be_absent("Classes", "student_schedule", global_config=off) is False
        assert source_file_may_be_absent("Enrollments", "student_schedule", global_config=off) is False

    def test_every_file_of_the_isolatable_entity_stays_optional_whatever_the_opt_out(self) -> None:
        isolatable = [e for e, c in ENTITY_CRITICALITY.items() if c is EntityCriticality.ISOLATABLE]
        assert isolatable == ["Family"]
        for global_config in ({}, {"blended_classes": False}):
            assert source_file_may_be_absent("Family", "emergency_contacts", global_config=global_config) is True

    def test_the_global_config_keyword_is_required(self) -> None:
        import inspect

        parameter = inspect.signature(source_file_may_be_absent).parameters["global_config"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


#: The ONE place `global_config.blended_classes` is READ (outcomes) and the ONE place the resolved
#: raw dict is WRITTEN (models.to_raw_dict). Any other module spelling the key reads the opt-out a
#: second way, which could drift from the input gate's exception.
_BLENDED_KEY = "blended_classes"
_BLENDED_KEY_HOMES = frozenset({"src/config/models.py", "src/etl/outcomes.py"})


def _modules_spelling_the_blended_key(sources: dict[str, str]) -> set[str]:
    """Every module (repo-relative path) whose code holds the string constant ``blended_classes``."""
    found: set[str] = set()
    for relative, source in sources.items():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and node.value == _BLENDED_KEY:
                found.add(relative)
    return found


def _src_sources() -> dict[str, str]:
    return {
        path.relative_to(_REPO).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((_REPO / "src").rglob("*.py"))
    }


class TestTheOptOutIsReadInOnePlace:
    def test_only_the_reader_and_the_writer_spell_the_key(self) -> None:
        assert _modules_spelling_the_blended_key(_src_sources()) == _BLENDED_KEY_HOMES

    def test_doctored_a_second_reader_is_red(self) -> None:
        """The twin: a transformer re-reading the opt-out on its own is found."""
        sources = _src_sources()
        sources["src/etl/transformers/doctored.py"] = (
            'def detect(context):\n    return context.global_config.get("blended_classes", True) is False\n'
        )
        assert _modules_spelling_the_blended_key(sources) - _BLENDED_KEY_HOMES == {"src/etl/transformers/doctored.py"}


# --------------------------------------------------------------------------- #
# The input gate — pure                                                          #
# --------------------------------------------------------------------------- #
_ROWS = pd.DataFrame({"x": ["1"]})
_MAPPINGS = {
    "Students": {"source_files": {"student_demographic": "demo.txt"}},
    "Classes": {"source_files": {"student_schedule": "schedule.txt", "class_info": "classinfo.txt"}},
    "Enrollments": {"source_files": {"student_schedule": "schedule.txt"}},
}
_OFF = {"blended_classes": False}


def _gate(mappings: dict, raw: dict, *, absent: set, global_config: dict) -> OutcomeLedger:
    ledger = OutcomeLedger(list(mappings))
    check_required_inputs(mappings, raw, absent=absent, ledger=ledger, global_config=global_config)
    return ledger


class TestTheInputGate:
    @pytest.mark.parametrize("damage", ["missing", "row_less"])
    def test_detection_off_lets_the_night_go_without_class_information(self, damage: str) -> None:
        raw = {"demo.txt": _ROWS, "schedule.txt": _ROWS, "classinfo.txt": pd.DataFrame()}
        absent = {"classinfo.txt"} if damage == "missing" else set()
        ledger = _gate(_MAPPINGS, raw, absent=absent, global_config=_OFF)
        assert ledger.outcomes == (), "a passing gate records nothing"

    @pytest.mark.parametrize(
        ("damage", "missing", "empty"), [("missing", ("classinfo.txt",), ()), ("row_less", (), ("classinfo.txt",))]
    )
    def test_twin_detection_on_still_stops_the_night(self, damage, missing, empty) -> None:
        """With detection on (the key absent — every district but the opt-outs) the file is the
        blended working frame: missing or row-less, the night stops, Classes recorded FAILED. (The
        file is NAMED end to end — the label vocabulary comes from the source observation — in
        ``test_twin_a_blended_on_config_without_class_information_still_stops`` below.)"""
        raw = {"demo.txt": _ROWS, "schedule.txt": _ROWS, "classinfo.txt": pd.DataFrame()}
        absent = {"classinfo.txt"} if damage == "missing" else set()
        ledger = OutcomeLedger(list(_MAPPINGS))
        with pytest.raises(IncompleteInputError) as raised:
            check_required_inputs(_MAPPINGS, raw, absent=absent, ledger=ledger, global_config={})
        assert (raised.value.missing, raised.value.empty) == (missing, empty)
        assert [(o.entity, o.kind, o.reason) for o in ledger.outcomes] == [
            ("Classes", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_FILE)
        ]

    def test_a_class_information_file_another_listing_requires_stays_required(self) -> None:
        """Judged per LISTING: the same file named under a role the opt-out does not excuse is
        required through it, exactly as a file Family shares with a CRITICAL entity is."""
        shared = {
            **_MAPPINGS,
            "Enrollments": {"source_files": {"student_schedule": "schedule.txt", "extra": "classinfo.txt"}},
        }
        raw = {"demo.txt": _ROWS, "schedule.txt": _ROWS, "classinfo.txt": pd.DataFrame()}
        ledger = OutcomeLedger(list(shared))
        with pytest.raises(IncompleteInputError) as raised:
            check_required_inputs(shared, raw, absent={"classinfo.txt"}, ledger=ledger, global_config=_OFF)
        assert raised.value.missing == ("classinfo.txt",)
        assert [o.entity for o in ledger.outcomes] == ["Enrollments"], "required through Enrollments only"

    def test_detection_off_excuses_nothing_else(self) -> None:
        raw = {"demo.txt": _ROWS, "schedule.txt": pd.DataFrame(), "classinfo.txt": pd.DataFrame()}
        with pytest.raises(IncompleteInputError) as raised:
            _gate(_MAPPINGS, raw, absent={"classinfo.txt", "schedule.txt"}, global_config=_OFF)
        assert raised.value.missing == ("schedule.txt",)


# --------------------------------------------------------------------------- #
# Every consumer of the predicate agrees with the owner's rule, on every config  #
# --------------------------------------------------------------------------- #
def _the_owners_rule(config) -> dict[str, frozenset[tuple[str, str]]]:
    """The rule restated from the rulings, independently of the code: over the ENABLED entities, a
    file is optional when every listing of it is Family's (2026-09-28) or the Class Information
    role on a config whose `blended_classes` is False (2026-09-30)."""
    active = config.active_entities()
    blended_off = config.global_config.blended_classes is False
    optional: dict[str, set[tuple[str, str]]] = {}
    required: set[str] = set()
    for entity, entity_cfg in config.mappings.items():
        if entity not in active:
            continue
        for role, filename in entity_cfg.source_files.items():
            if entity == "Family" or (role == "class_info" and blended_off):
                optional.setdefault(filename, set()).add((entity, role))
            else:
                required.add(filename)
    return {name: frozenset(listings) for name, listings in optional.items() if name not in required}


def _consumer_disagreements(config_name: str) -> list[str]:
    """Where the input gate or ``optional_source_files`` departs from the owner's rule."""
    config = load_config(config_name)
    rule = _the_owners_rule(config)
    problems: list[str] = []
    if optional_source_files(config) != rule:
        problems.append(f"{config_name}: optional_source_files reads {optional_source_files(config)}, the rule {rule}")
    raw_config = config.to_raw_dict()
    mappings, global_config = raw_config["mappings"], raw_config["global_config"]
    required = extract_required_files(config)
    ledger = OutcomeLedger(configured_entity_order(mappings, global_config))
    stopped: set[str] = set()
    try:
        check_required_inputs(mappings, {}, absent=set(required), ledger=ledger, global_config=global_config)
    except IncompleteInputError as exc:
        stopped = set(exc.missing)
    if stopped != set(required) - set(rule):
        problems.append(
            f"{config_name}: the gate stops for {sorted(stopped)}, the rule for {sorted(set(required) - set(rule))}"
        )
    return problems


def _two_entity_config(*, blended_classes: bool, enrollments_extra: dict[str, str]) -> MappingConfig:
    return MappingConfig(
        version="1.9",
        sis="test",
        global_config=GlobalConfig(
            blended_classes=blended_classes, academic_start_month_day="08-25", academic_end_month_day="07-25"
        ),
        mappings={
            "Classes": EntityConfig(
                source_files={"student_schedule": "Sched.txt", "class_info": "CI.txt"},
                field_map={"Class ID": "Master Timetable ID"},
            ),
            "Enrollments": EntityConfig(
                source_files={"student_schedule": "Sched.txt", **enrollments_extra},
                field_map={"Class ID": "Master Timetable ID"},
            ),
            "Family": EntityConfig(
                source_files={"emergency_contacts": "Contacts.txt"}, field_map={"Email": "Email Address"}
            ),
        },
    )


class TestOptionalSourceFiles:
    def test_detection_off_names_the_class_information_file_and_family_s(self) -> None:
        config = _two_entity_config(blended_classes=False, enrollments_extra={})
        assert optional_source_files(config) == {
            "CI.txt": frozenset({("Classes", CLASS_INFORMATION_ROLE)}),
            "Contacts.txt": frozenset({("Family", "emergency_contacts")}),
        }

    def test_twin_detection_on_names_only_family_s(self) -> None:
        config = _two_entity_config(blended_classes=True, enrollments_extra={})
        assert optional_source_files(config) == {"Contacts.txt": frozenset({("Family", "emergency_contacts")})}

    def test_twin_a_file_another_listing_requires_is_not_optional(self) -> None:
        config = _two_entity_config(blended_classes=False, enrollments_extra={"extra": "CI.txt"})
        assert "CI.txt" not in optional_source_files(config)


class TestEveryConsumerAgreesWithTheRule:
    @pytest.mark.parametrize("config_name", available_configs())
    def test_the_gate_and_the_screen_follow_the_rule(self, config_name: str) -> None:
        assert _consumer_disagreements(config_name) == []

    def test_non_vacuity_the_rule_excuses_class_information_somewhere_and_not_everywhere(self) -> None:
        excused = {
            name
            for name in available_configs()
            if any(
                role == CLASS_INFORMATION_ROLE
                for listings in optional_source_files(load_config(name)).values()
                for _entity, role in listings
            )
        }
        assert {"sd45myedbc", "sd51myedbc"} <= excused
        assert "sd74myedbc" not in excused and "myedbc" not in excused

    def test_doctored_an_opt_out_the_predicate_ignores_is_red(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The twin: were the predicate to stop reading the opt-out, SD45's night would stop again
        for its Class Information — and the pin names it."""
        monkeypatch.setattr(outcomes, "blended_detection_off", lambda _global_config: False)
        problems = _consumer_disagreements("sd45myedbc")
        assert len(problems) == 2
        assert all("ClassInformationEnh.txt" in problem for problem in problems)


# --------------------------------------------------------------------------- #
# The co-teacher path records the missing FILE (it used to return in silence)    #
# --------------------------------------------------------------------------- #
def _context(raw: dict, *, classes_sources: dict | None) -> TransformContext:
    mappings = {} if classes_sources is None else {"Classes": {"source_files": classes_sources}}
    ctx = TransformContext(school_year=2026, raw_data=raw, entity_mappings=mappings)
    ctx.class_artifacts = ClassArtifacts(
        homeroom_classes_df=pd.DataFrame(),
        class_info_df=pd.DataFrame(),
        blended_class_map={},
        blended_class_metadata={},
        blended_teacher_map={},
    )
    return ctx


def _coteachers(ctx: TransformContext):
    return EnrollmentTransformer()._classinfo_coteacher_enrollments(
        "teacher id", "Teacher ID", {}, ctx.class_artifacts, ctx
    )


def _left_out_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if _LEFT_OUT_LINE in r.getMessage()]


class TestTheCoteacherPathRecordsTheMissingFile:
    @pytest.mark.parametrize("raw", [{}, {"ClassInfoEnh.txt": pd.DataFrame()}], ids=["missing", "row_less"])
    def test_a_missing_or_row_less_file_leaves_the_coteachers_out_with_the_note(self, raw, caplog) -> None:
        ctx = _context(raw, classes_sources={"student_schedule": "s.txt", "class_info": "ClassInfoEnh.txt"})
        with caplog.at_level(logging.WARNING):
            assert _coteachers(ctx) is None
        assert ctx.outcome_notes_for("Enrollments") == ((_NOTE, 1),), "count 1: the one file"
        (line,) = _left_out_lines(caplog)
        assert "'ClassInfoEnh.txt'" in line, "the file is named in the mapping's spelling"
        assert "missing from the input folder or has no rows" in line

    def test_twin_a_file_whose_rows_were_all_excluded_course_codes_records_nothing(self, caplog) -> None:
        """The raw export HAD rows (every one an excluded code): usable, with nothing for co-teachers."""
        ctx = _context({"ClassInfoEnh.txt": _ROWS}, classes_sources={"class_info": "ClassInfoEnh.txt"})
        with caplog.at_level(logging.WARNING):
            assert _coteachers(ctx) is None
        assert ctx.outcome_notes_for("Enrollments") == ()
        assert _left_out_lines(caplog) == []

    @pytest.mark.parametrize("classes_sources", [None, {"student_schedule": "s.txt"}], ids=["no_mappings", "no_role"])
    def test_twin_a_mapping_that_lists_no_class_information_records_nothing(self, classes_sources, caplog) -> None:
        ctx = _context({}, classes_sources=classes_sources)
        with caplog.at_level(logging.WARNING):
            assert _coteachers(ctx) is None
        assert ctx.outcome_notes_for("Enrollments") == ()
        assert _left_out_lines(caplog) == []

    def test_the_note_copy_is_true_for_a_missing_file_and_a_missing_column(self) -> None:
        sentence = note_sentence(_NOTE)
        assert "is missing, has no rows, or lacks a column" in sentence


# --------------------------------------------------------------------------- #
# End to end                                                                     #
# --------------------------------------------------------------------------- #
def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir(parents=True)
    out.mkdir(parents=True)
    return inp, out


def _csvs(out: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(out.glob("*.csv"))}


@pytest.mark.integration
@pytest.mark.parametrize("damage", ["missing", "header_only"])
def test_sd45_without_class_information_is_partial_two_nights_running(tmp_path: Path, damage: str) -> None:
    """SD45 sends no Class Information (its mapping inherits the base's listing): every night ends
    success, Enrollments BUILT with the co-teacher note, Home and Run History amber."""
    inp, out = _dirs(tmp_path)
    _create_sd45_inputs(inp)
    if damage == "header_only":
        _write_class_info(inp, "ClassInformationEnh.txt", sections=())
    for _night in (1, 2):
        result = run_pipeline("sd45myedbc", str(inp), str(out))
        record, prior, records = _latest()
        assert record["status"] == "success" and record["error_category"] == "none"
        assert record["entity_outcomes"]["Enrollments"]["notes"] == {_NOTE.value: 1}
        assert {o.entity: o.kind for o in result.entity_outcomes} == dict.fromkeys(
            ("Students", "Staff", "Family", "Classes", "Enrollments"), OutcomeKind.BUILT
        )
        _assert_partial_with_the_note(record, prior, records, "sd45myedbc")
    assert set(_csvs(out)) == {"Students.csv", "Staff.csv", "Family.csv", "Classes.csv", "Enrollments.csv"}


@pytest.mark.integration
def test_twin_sd45_with_a_complete_class_information_is_clean_and_delivers_the_same_files(tmp_path: Path) -> None:
    """With the file, nothing is noted — and since detection is off it feeds only co-teachers, none of
    which this fixture's sections link, so every delivered file is byte-identical to the night
    without it."""
    without_in, without_out = _dirs(tmp_path / "without")
    _create_sd45_inputs(without_in)
    run_pipeline("sd45myedbc", str(without_in), str(without_out))

    with_in, with_out = _dirs(tmp_path / "with")
    _create_sd45_inputs(with_in)
    _write_class_info(with_in, "ClassInformationEnh.txt")
    run_pipeline("sd45myedbc", str(with_in), str(with_out))
    record, _prior, _records = _latest()
    assert record["status"] == "success"
    assert all("notes" not in entry for entry in record["entity_outcomes"].values())
    assert _csvs(with_out) == _csvs(without_out)


@pytest.mark.integration
def test_sd51_without_its_class_information_runs_with_the_note(tmp_path: Path) -> None:
    """SD51, the other blended-off config, DOES send the file — and may now go without it."""
    inp, out = _dirs(tmp_path)
    _create_sd51_inputs(inp)
    (inp / "ClassInformationEnh.txt").unlink()
    run_pipeline("sd51myedbc", str(inp), str(out))
    record, prior, records = _latest()
    assert record["status"] == "success"
    assert record["entity_outcomes"]["Enrollments"]["notes"] == {_NOTE.value: 1}
    _assert_partial_with_the_note(record, prior, records, "sd51myedbc")


def _add_unroled_coteacher(inp: Path) -> None:
    """Append ONE co-teacher row (``Primary Teacher`` = N) for the fixture's support-staff member,
    whose staff record states no teaching role and who holds no timetable section — so a Class
    Information row is their ONLY teaching evidence (plan 0052's teacher-of-record rescue)."""
    path = inp / "ClassInformationEnh.txt"
    frame = pd.read_csv(path, dtype=str)
    row = frame.iloc[[1]].copy()
    row["Teacher ID"] = _SUPPORT_STAFF_ID
    row["Primary Teacher"] = "N"
    pd.concat([frame, row], ignore_index=True).to_csv(path, index=False)


def _shipped_staff(out: Path) -> set[str]:
    return set(pd.read_csv(out / "Staff.csv", encoding="utf-8-sig", dtype=str)["User ID"].dropna())


@pytest.mark.integration
def test_without_the_file_a_coteacher_only_it_evidences_leaves_staff_too_and_the_copy_says_so(
    tmp_path: Path,
) -> None:
    """S13e review B1: on a blended-off night without Class Information, an un-roled co-teacher whose
    only teaching evidence is that export is not rescued, so they leave ``Staff.csv`` as well as the
    enrollments. That is part of "co-teachers are left out" (owner ruling 2026-09-30) and the note's
    copy must say so — the twin with the file proves the rescue does reach them."""
    with_in, with_out = _dirs(tmp_path / "with")
    _create_sd51_inputs(with_in)
    _add_unroled_coteacher(with_in)
    run_pipeline("sd51myedbc", str(with_in), str(with_out))
    assert _SUPPORT_STAFF_ID in _shipped_staff(with_out), "the rescue must reach the co-teacher when the file is there"

    without_in, without_out = _dirs(tmp_path / "without")
    _create_sd51_inputs(without_in)
    (without_in / "ClassInformationEnh.txt").unlink()
    run_pipeline("sd51myedbc", str(without_in), str(without_out))
    record, _prior, _records = _latest()
    assert record["status"] == "success"
    assert _SUPPORT_STAFF_ID not in _shipped_staff(without_out)
    assert record["entity_outcomes"]["Enrollments"]["notes"] == {_NOTE.value: 1}
    assert "notes" not in record["entity_outcomes"]["Staff"]
    assert "left out of the staff file too" in note_sentence(_NOTE)


@pytest.mark.integration
def test_twin_a_blended_on_config_without_class_information_still_stops(tmp_path: Path) -> None:
    """SD74 detects blended classes on its Class Information: missing, the night stops as before."""
    inp, out = _dirs(tmp_path)
    _create_sd74_inputs(inp)
    (inp / "ClassInfoEnhanced.txt").unlink()
    with pytest.raises(IncompleteInputError) as raised:
        run_pipeline("sd74myedbc", str(inp), str(out))
    assert raised.value.missing == ("ClassInfoEnhanced.txt",)
    record = read_run_records()[0]
    assert record["error_category"] == "incomplete_input"
    classes = record["entity_outcomes"]["Classes"]
    assert (classes["kind"], classes["reason"], classes["file_label"]) == (
        "failed",
        "missing_source_file",
        "ClassInfoEnhanced.txt",
    )
    assert _csvs(out) == {}


@pytest.mark.integration
def test_cli_and_convert_agree_on_a_night_without_class_information(tmp_path: Path) -> None:
    """The gate is shared: Convert over the same SD45 drop delivers the same bytes and outcomes."""
    inp, cli_out = _dirs(tmp_path)
    ui_out = tmp_path / "ui_out"
    ui_out.mkdir()
    _create_sd45_inputs(inp)
    cli_result = run_pipeline("sd45myedbc", str(inp), str(cli_out))
    AppConfig(input_dir=str(inp), output_dir=str(ui_out), sis_type="sd45myedbc").save()
    ui_result = convert_job("sd45myedbc", str(inp))
    assert ui_result.status is ConvertStatus.DELIVERED
    assert ui_result.entity_outcomes == cli_result.entity_outcomes
    assert _csvs(ui_out) == _csvs(cli_out)
    by_source = {r["source"]: r["entity_outcomes"] for r in reversed(read_run_records() or [])}
    assert by_source["manual"] == by_source["cli"]
    assert by_source["manual"]["Enrollments"]["notes"] == {_NOTE.value: 1}
