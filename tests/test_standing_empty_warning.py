"""The standing warning while an enabled entity produces nothing (plan 0053 S8, owner decision D5).

``docs/developer/failure-policy.md`` §7. What is pinned here, each with the twin that proves the
mechanism fires:

* **The tier rule, exhaustively** — ``failure_copy.OUTCOME_TIER(entity, kind, reason)`` over every
  registry entity, an unknown key and every valid pair, against an expected table written out
  here (the doc's copy of it is tied to the code in ``tests/test_failure_policy_parity.py``). An
  invalid pair raises.
* **``MAY_BE_EMPTY`` completeness** — every member is a registry entity, the roster anchor is not
  one, and the set is exactly what DECISIONS approved; a doctored member is caught.
* **One PARTIAL predicate** — ``warning_outcomes`` selects a warning EMPTY outcome, leaves a
  ``MAY_BE_EMPTY`` member's "nothing to send" out, and Home, the Run History banner/row and
  Convert agree for every EMPTY reason on a member and a non-member.
* **Copy** — a single EMPTY outcome's PARTIAL detail is its sentence + the "everything else"
  sentence + a per-reason next step (TOTAL over the EMPTY reasons); a FAILED one is unchanged.
* **Composition** — the warning outranks the vanished-entity anomaly and survives a
  delivery-only record's walk-back.
* **Level-triggered, end to end** — an SD51-shaped drop whose contact export lacks the mapped
  ``Email Address`` column is PARTIAL on night 1 AND night 2 (the anomaly has decayed by night 2);
  twins: the same drop WITH the column is CLEAN (its absent attendance files, a ``MAY_BE_EMPTY``
  entity, stay neutral), and a present-but-blank column is PARTIAL for the other warning reason.
  These run through a TEST-ONLY overlay (:data:`_SD51_SHAPE`) that re-enables Family on the
  bundled ``sd51myedbc``: the shipped config has had Family OFF since 2026-09-25 (owner
  decision — SD51's real contact export has no email column), so the bundled config on the
  very same drop is CLEAN, which is pinned too.

All data is synthetic (the ``tests/test_contract.py`` builders).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.config.app_config import AppConfig
from src.etl.errors import RunErrorCategory
from src.etl.outcomes import (
    ENTITY_CRITICALITY,
    MAY_BE_EMPTY,
    ROSTER_ANCHOR_ENTITY,
    VALID_REASONS,
    EntityOutcome,
    OutcomeKind,
    OutcomeReason,
    failed_entities,
)
from src.etl.pipeline import build_run_record, run_pipeline
from src.etl.transformers.registry import TRANSFORMER_REGISTRY
from src.history.store import read_run_records
from src.ui_flet import failure_copy
from src.ui_flet.convert_result import ConvertResult, ConvertStatus, summarize
from src.ui_flet.failure_copy import OUTCOME_TIER, outcome_sentence, partial_copy, warning_outcomes
from src.ui_flet.home_status import (
    LatestReason,
    build_record_for,
    classify_latest_reason,
    derive_home_status,
)
from src.ui_flet.run_history import derive_history_banner, to_run_row
from src.ui_flet.verdict import Verdict
from src.utils.paths import user_mappings_dir
from tests.test_contract import _create_sd51_inputs

_H, _W, _F = Verdict.HEALTHY, Verdict.WARNING, Verdict.FAILED

#: The D5 table, written out: (kind, reason) -> (tier for a MAY_BE_EMPTY member, tier for any other).
_EXPECTED_TIER: dict[tuple[OutcomeKind, OutcomeReason], tuple[Verdict, Verdict]] = {
    (OutcomeKind.BUILT, OutcomeReason.NONE): (_H, _H),
    (OutcomeKind.EMPTY, OutcomeReason.NO_SOURCE_FILES_DECLARED): (_H, _W),
    (OutcomeKind.EMPTY, OutcomeReason.SOURCE_FILES_EMPTY): (_H, _W),
    (OutcomeKind.EMPTY, OutcomeReason.NO_ROWS_AFTER_TRANSFORM): (_W, _W),
    (OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN): (_W, _W),
    (OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN): (_W, _W),
    (OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR): (_W, _W),
    (OutcomeKind.NOT_RUN, OutcomeReason.RUN_ABORTED): (_F, _F),
}
_VALID_PAIRS = [(kind, reason) for kind, reasons in VALID_REASONS.items() for reason in sorted(reasons)]
_EMPTY_REASONS = sorted(VALID_REASONS[OutcomeKind.EMPTY])
_NOTHING_TO_SEND = [OutcomeReason.SOURCE_FILES_EMPTY, OutcomeReason.NO_SOURCE_FILES_DECLARED]
_ROSTERING = ("Students", "Staff", "Family", "Classes", "Enrollments")
_NOW = datetime(2026, 9, 24, 8, 0, 0)
# The synthetic records below are stamped with the BASE config, which enables Family — never with
# ``sd51myedbc``, whose shipped config no longer builds a Family outcome at all (2026-09-25).
_CFG = AppConfig(sis_type="myedbc", setup_completed=True)


def _outcome(entity: str, kind: OutcomeKind, reason: OutcomeReason) -> EntityOutcome:
    return EntityOutcome(entity, kind, reason, 5 if kind is OutcomeKind.BUILT else 0)


# --------------------------------------------------------------------------- #
# The tier rule                                                                #
# --------------------------------------------------------------------------- #
class TestOutcomeTierTable:
    def test_the_expected_table_covers_every_valid_pair(self) -> None:
        assert set(_EXPECTED_TIER) == set(_VALID_PAIRS)

    @pytest.mark.parametrize("entity", [*sorted(TRANSFORMER_REGISTRY), "InventedByAHandDroppedYaml"])
    def test_every_entity_every_pair(self, entity: str) -> None:
        member = entity in MAY_BE_EMPTY
        for pair, (if_member, otherwise) in _EXPECTED_TIER.items():
            assert OUTCOME_TIER(entity, *pair) is (if_member if member else otherwise), (entity, pair)

    def test_the_member_column_is_really_exercised(self) -> None:
        # Non-vacuity: at least one registry entity sits on each side of the split.
        assert set(TRANSFORMER_REGISTRY) & MAY_BE_EMPTY
        assert set(TRANSFORMER_REGISTRY) - MAY_BE_EMPTY

    def test_a_non_string_entity_is_never_a_member(self) -> None:
        # Total over junk: an unhashable or non-str key warns rather than raising or going quiet.
        assert OUTCOME_TIER(None, OutcomeKind.EMPTY, OutcomeReason.SOURCE_FILES_EMPTY) is _W
        assert OUTCOME_TIER(["StudentAttendance"], OutcomeKind.EMPTY, OutcomeReason.SOURCE_FILES_EMPTY) is _W

    @pytest.mark.parametrize(
        ("kind", "reason"),
        [
            (OutcomeKind.BUILT, OutcomeReason.SOURCE_FILES_EMPTY),
            (OutcomeKind.EMPTY, OutcomeReason.NONE),
            (OutcomeKind.FAILED, OutcomeReason.NO_ROWS_AFTER_TRANSFORM),
            (OutcomeKind.NOT_RUN, OutcomeReason.TRANSFORM_ERROR),
        ],
    )
    def test_an_invalid_pair_raises(self, kind: OutcomeKind, reason: OutcomeReason) -> None:
        with pytest.raises(ValueError, match="not a valid reason"):
            OUTCOME_TIER("Family", kind, reason)

    def test_a_built_entity_with_a_missing_mapped_column_stays_healthy(self) -> None:
        # SD74 / Unity measured: Students BUILT with a mapped column absent — never amber.
        built = EntityOutcome("Students", OutcomeKind.BUILT, OutcomeReason.NONE, 10, ("Next school code",))
        assert warning_outcomes([built]) == ()


def _unknown_members(members: frozenset[str]) -> set[str]:
    """The members naming no registry entity — the ONE check the pin and its twin share."""
    return set(members) - set(TRANSFORMER_REGISTRY)


class TestMayBeEmpty:
    def test_every_member_is_a_registry_entity(self) -> None:
        assert set(TRANSFORMER_REGISTRY) == set(ENTITY_CRITICALITY)
        assert _unknown_members(MAY_BE_EMPTY) == set()

    def test_the_roster_anchor_is_never_a_member(self) -> None:
        assert ROSTER_ANCHOR_ENTITY not in MAY_BE_EMPTY

    def test_exactly_the_approved_set(self) -> None:
        # Widening this is an owner decision recorded in DECISIONS (D5, 2026-09-24) — never a
        # way to quiet a district; a new member must change this line on purpose.
        assert frozenset({"StudentAttendance"}) == MAY_BE_EMPTY

    def test_doctored_an_unknown_member_is_caught(self) -> None:
        assert _unknown_members(MAY_BE_EMPTY | {"Attendence"}) == {"Attendence"}

    def test_it_is_immutable(self) -> None:
        assert isinstance(MAY_BE_EMPTY, frozenset)


# --------------------------------------------------------------------------- #
# The one PARTIAL predicate                                                    #
# --------------------------------------------------------------------------- #
class TestWarningOutcomes:
    def test_it_selects_a_warning_empty_outcome_in_configured_order(self) -> None:
        outcomes = [
            _outcome("Students", OutcomeKind.BUILT, OutcomeReason.NONE),
            _outcome("Family", OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN),
            _outcome("CourseInfo", OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR),
            _outcome("StudentAttendance", OutcomeKind.EMPTY, OutcomeReason.SOURCE_FILES_EMPTY),
        ]
        assert [o.entity for o in warning_outcomes(outcomes)] == ["Family", "CourseInfo"]
        assert [o.entity for o in failed_entities(outcomes)] == ["CourseInfo"]  # S3's narrower set

    @pytest.mark.parametrize("reason", _NOTHING_TO_SEND)
    def test_a_member_with_nothing_to_send_is_not_selected(self, reason: OutcomeReason) -> None:
        assert warning_outcomes([_outcome("StudentAttendance", OutcomeKind.EMPTY, reason)]) == ()

    @pytest.mark.parametrize("reason", [OutcomeReason.NO_ROWS_AFTER_TRANSFORM, OutcomeReason.MISSING_SOURCE_COLUMN])
    def test_twin_a_member_whose_rows_were_all_lost_is_selected(self, reason: OutcomeReason) -> None:
        member = _outcome("StudentAttendance", OutcomeKind.EMPTY, reason)
        assert warning_outcomes([member]) == (member,)

    def test_it_is_the_function_every_surface_imports(self) -> None:
        # Home/Run History (via home_status) and Convert import the ONE predicate.
        from src.ui_flet import convert_result, home_status

        assert home_status.warning_outcomes is failure_copy.warning_outcomes
        assert convert_result.warning_outcomes is failure_copy.warning_outcomes


# --------------------------------------------------------------------------- #
# Copy                                                                         #
# --------------------------------------------------------------------------- #
class TestEmptyPartialCopy:
    def test_the_next_step_table_is_total_over_the_empty_reasons(self) -> None:
        assert set(failure_copy._EMPTY_NEXT_STEP) == set(VALID_REASONS[OutcomeKind.EMPTY])
        for step in failure_copy._EMPTY_NEXT_STEP.values():
            assert step.strip() and "{" not in step and "input folder" not in step

    @pytest.mark.parametrize("reason", _EMPTY_REASONS)
    @pytest.mark.parametrize("delivered", [True, False])
    def test_a_single_empty_outcome_reads_sentence_then_everything_else_then_the_step(
        self, reason: OutcomeReason, delivered: bool
    ) -> None:
        outcome = _outcome("Family", OutcomeKind.EMPTY, reason)
        headline, detail = partial_copy([outcome], delivered=delivered)
        assert headline == (
            "Your roster synced without family contacts" if delivered else "Your sync completed without family contacts"
        )
        everything_else = "Everything else was delivered." if delivered else "Everything else completed."
        assert detail == (
            f"{outcome_sentence(outcome, delivered=delivered)} {everything_else} {failure_copy._EMPTY_NEXT_STEP[reason]}"
        )

    def test_the_sd51_shaped_detail_names_the_file_and_column(self) -> None:
        outcome = EntityOutcome(
            "Family",
            OutcomeKind.EMPTY,
            OutcomeReason.MISSING_SOURCE_COLUMN,
            0,
            ("Email Address",),
            ("Email Address",),
            "EmergencyContactInformation.txt",
        )
        _headline, detail = partial_copy([outcome], delivered=False)
        assert detail == (
            "Family contacts were not built: none of their rows could be used, and their export file, "
            "EmergencyContactInformation.txt, is missing the column “Email Address”, which this district's "
            "mapping reads. Everything else completed. Re-export that file with the column, or — if your "
            "export names it differently — the Help page has our support contact."
        )

    def test_twin_a_single_failed_outcome_is_its_sentence_alone(self) -> None:
        failed = _outcome("Family", OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR)
        assert partial_copy([failed], delivered=True)[1] == outcome_sentence(failed, delivered=True)


# --------------------------------------------------------------------------- #
# Home ≡ Run History ≡ Convert, for every EMPTY reason, member and non-member   #
# --------------------------------------------------------------------------- #
def _record(outcomes: list[EntityOutcome], *, anomalies: list[str] | None = None, hours_ago: int = 2) -> dict:
    record = build_run_record(
        status="success",
        elapsed=1.0,
        entity_counts={"Students": 10},
        sftp_attempted=False,
        sftp_ok=False,
        source="scheduled",
        sis_type="myedbc",
        error_category=RunErrorCategory.NONE,
        entity_outcomes=outcomes,
        anomalies=anomalies or [],
        timestamp=(_NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds"),
    )
    return record


def _run(entity: str, kind: OutcomeKind, reason: OutcomeReason) -> list[EntityOutcome]:
    rows = [EntityOutcome.built(name, 10) for name in _ROSTERING if name != entity]
    return [*rows, _outcome(entity, kind, reason)]


_PARITY_CASES = [(entity, reason) for entity in ("Family", "StudentAttendance") for reason in _EMPTY_REASONS]


class TestSurfaceParity:
    @pytest.mark.parametrize(("entity", "reason"), _PARITY_CASES, ids=[f"{e}-{r.value}" for e, r in _PARITY_CASES])
    def test_home_run_history_and_convert_agree(self, entity: str, reason: OutcomeReason) -> None:
        outcomes = _run(entity, OutcomeKind.EMPTY, reason)
        record = _record(outcomes)
        warns = OUTCOME_TIER(entity, OutcomeKind.EMPTY, reason) is Verdict.WARNING

        home = derive_home_status([record], _CFG, now=_NOW)
        banner = derive_history_banner([record], _CFG, now=_NOW)
        row = to_run_row(record, prior_build=None, now=_NOW)
        convert = summarize(
            ConvertResult(
                delivery_requested=False,
                status=ConvertStatus.DELIVERED,
                sftp_attempted=False,
                sftp_ok=False,
                entity_outcomes=tuple(outcomes),
            )
        )

        reason_seen = classify_latest_reason(record, prior_build=None)
        assert reason_seen is (LatestReason.PARTIAL if warns else LatestReason.CLEAN)
        if warns:
            expected = partial_copy([_outcome(entity, OutcomeKind.EMPTY, reason)], delivered=False)
            assert (home.verdict, home.headline, home.detail) == (Verdict.WARNING, *expected)
            assert (banner.verdict, banner.headline, banner.detail) == (Verdict.WARNING, *expected)
            assert convert == (Verdict.WARNING, *expected)
            assert (row.status_label, row.status_verdict) == ("Completed · 1 file skipped", Verdict.WARNING)
        else:
            assert home.verdict is banner.verdict is convert[0] is Verdict.HEALTHY
            assert (row.status_label, row.status_verdict) == ("Completed", Verdict.HEALTHY)

    def test_non_vacuity_both_branches_are_reached(self) -> None:
        tiers = {OUTCOME_TIER(e, OutcomeKind.EMPTY, r) for e, r in _PARITY_CASES}
        assert tiers == {Verdict.WARNING, Verdict.HEALTHY}


class TestComposition:
    def test_a_warning_empty_outcome_outranks_the_anomaly(self) -> None:
        record = _record(
            _run("Family", OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN),
            anomalies=["ANOMALY: Family produced no output this run"],
        )
        assert classify_latest_reason(record, prior_build=None) is LatestReason.PARTIAL

    def test_twin_a_neutral_empty_outcome_leaves_the_anomaly_in_charge(self) -> None:
        record = _record(
            _run("StudentAttendance", OutcomeKind.EMPTY, OutcomeReason.SOURCE_FILES_EMPTY),
            anomalies=["ANOMALY: StudentAttendance produced no output this run"],
        )
        assert classify_latest_reason(record, prior_build=None) is LatestReason.ANOMALY

    def test_a_delivery_of_the_saved_files_inherits_the_warning(self) -> None:
        build = _record(_run("Family", OutcomeKind.EMPTY, OutcomeReason.NO_ROWS_AFTER_TRANSFORM), hours_ago=3)
        delivery = {**_record([], hours_ago=1), "delivery_only": True, "entity_outcomes": None}
        records = [delivery, build]
        assert classify_latest_reason(delivery, prior_build=build_record_for(records, 0)) is LatestReason.PARTIAL
        row = to_run_row(delivery, prior_build=build_record_for(records, 0), now=_NOW)
        assert row.status_label == "Delivered saved files · 1 file skipped"


# --------------------------------------------------------------------------- #
# End to end: an SD51-shaped drop, two nights                                  #
# --------------------------------------------------------------------------- #
#: A TEST-ONLY overlay: the bundled ``sd51myedbc`` with Family switched back on. The shipped
#: config has had Family OFF since 2026-09-25 (owner decision: SD51's real contact export has no
#: email column, so Family could only ever warn). That drop is still the natural synthetic amber
#: example for the RULE, so these tests keep its shape through the overlay — without claiming the
#: shipped config goes amber (``test_the_bundled_sd51_config_on_the_same_drop_is_clean``).
_SD51_SHAPE = "sd51familyshape"


def _write_sd51_shape_overlay() -> None:
    """Drop the overlay into the (autouse-isolated) user mappings dir, and prove what it is."""
    (user_mappings_dir() / f"{_SD51_SHAPE}_mapping.yaml").write_text(
        "_base: sd51myedbc\n"
        "global_config:\n"
        "  enabled_entities: [Students, Staff, Family, Classes, Enrollments, StudentAttendance]\n",
        encoding="utf-8",
    )
    # Non-vacuity: the overlay is the bundled SD51 config plus Family and NOTHING else.
    from src.config.loader import load_config

    bundled = load_config("sd51myedbc").active_entities()
    assert "Family" not in bundled
    assert load_config(_SD51_SHAPE).active_entities() == bundled | {"Family"}


def _sd51_contacts(d: Path, *, email: str) -> None:
    """Rewrite the SD51 fixture's contact export: ``email`` = "present" | "absent" | "blank"."""
    frame = {
        "Student Number": ["S001", "S002"],
        "First Name": ["John", "Mei"],
        "Last Name": ["Smith", "Wong"],
    }
    if email == "present":
        frame["Email Address"] = ["john@mail.com", "mei@mail.com"]
    elif email == "blank":
        frame["Email Address"] = ["", ""]
    pd.DataFrame(frame).to_csv(d / "EmergencyContactInformation.txt", index=False)


def _latest() -> tuple[dict, dict | None, list[dict]]:
    records = read_run_records()
    assert records
    return records[0], build_record_for(records, 0), records


def _family(record: dict) -> tuple[str, str]:
    entry = record["entity_outcomes"]["Family"]
    return entry["kind"], entry["reason"]


@pytest.mark.integration
def test_an_sd51_shaped_drop_without_the_email_column_is_partial_two_nights_running(tmp_path: Path) -> None:
    _write_sd51_shape_overlay()
    sis = _SD51_SHAPE
    good, bad, out = tmp_path / "good", tmp_path / "bad", tmp_path / "out"
    for d in (good, bad, out):
        d.mkdir()
    _create_sd51_inputs(good)
    _sd51_contacts(good, email="present")
    _create_sd51_inputs(bad)
    _sd51_contacts(bad, email="absent")

    run_pipeline(sis, str(good), str(out))  # night 0: Family built
    record, prior, _ = _latest()
    assert _family(record) == ("built", "none")
    assert classify_latest_reason(record, prior_build=prior) is LatestReason.CLEAN

    night1 = run_pipeline(sis, str(bad), str(out))
    record, prior, records = _latest()
    assert _family(record) == ("empty", "missing_source_column")
    assert record["entity_outcomes"]["Family"]["labels"] == ["Email Address"]
    assert any("Family" in a for a in night1.anomalies), "night 1: the vanished Family.csv also fires the anomaly"
    assert classify_latest_reason(record, prior_build=prior) is LatestReason.PARTIAL
    cfg = AppConfig(sis_type=sis, setup_completed=True)
    home = derive_home_status(records, cfg, now=_NOW + timedelta(days=30), store_created_at=record["timestamp"])
    assert home.verdict is Verdict.WARNING
    assert home.headline == "Your sync completed without family contacts"

    night2 = run_pipeline(sis, str(bad), str(out))
    record, prior, records = _latest()
    assert night2.anomalies == [], "the anomaly decays after one night"
    assert _family(record) == ("empty", "missing_source_column")
    assert classify_latest_reason(record, prior_build=prior) is LatestReason.PARTIAL
    assert to_run_row(record, prior_build=prior).status_label == "Completed · 1 file skipped"
    # The attendance entity (a MAY_BE_EMPTY member) has no source files on every night — never the cause.
    assert record["entity_outcomes"]["StudentAttendance"]["kind"] == "empty"
    assert [o.entity for o in warning_outcomes(_outcomes_of(record))] == ["Family"]


@pytest.mark.integration
def test_twin_the_same_drop_with_the_email_column_is_clean(tmp_path: Path) -> None:
    _write_sd51_shape_overlay()
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    _create_sd51_inputs(inp)
    _sd51_contacts(inp, email="present")
    run_pipeline(_SD51_SHAPE, str(inp), str(out))
    record, prior, _ = _latest()
    assert _family(record) == ("built", "none")
    # The absent attendance files are EMPTY/source_files_empty — neutral for a MAY_BE_EMPTY member.
    assert (
        record["entity_outcomes"]["StudentAttendance"]["kind"],
        record["entity_outcomes"]["StudentAttendance"]["reason"],
    ) == (
        "empty",
        "source_files_empty",
    )
    assert classify_latest_reason(record, prior_build=prior) is LatestReason.CLEAN


@pytest.mark.integration
def test_a_present_but_blank_email_column_is_partial_for_the_other_reason(tmp_path: Path) -> None:
    _write_sd51_shape_overlay()
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    _create_sd51_inputs(inp)
    _sd51_contacts(inp, email="blank")
    run_pipeline(_SD51_SHAPE, str(inp), str(out))
    record, prior, _ = _latest()
    assert _family(record) == ("empty", "no_rows_after_transform")
    assert classify_latest_reason(record, prior_build=prior) is LatestReason.PARTIAL


@pytest.mark.integration
def test_the_bundled_sd51_config_on_the_same_drop_is_clean(tmp_path: Path) -> None:
    """The SHIPPED ``sd51myedbc`` on the no-email drop: no Family outcome at all, Home HEALTHY.

    Its twin is the overlay run above — the identical drop with Family enabled goes PARTIAL — so
    this green is the config's doing (Family off, owner decision 2026-09-25), not the drop's.
    """
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    _create_sd51_inputs(inp)
    _sd51_contacts(inp, email="absent")
    run_pipeline("sd51myedbc", str(inp), str(out))
    record, prior, records = _latest()
    kinds = {name: (entry["kind"], entry["reason"]) for name, entry in record["entity_outcomes"].items()}
    assert kinds == {
        "Students": ("built", "none"),
        "Staff": ("built", "none"),
        "Classes": ("built", "none"),
        "Enrollments": ("built", "none"),
        "StudentAttendance": ("empty", "source_files_empty"),
    }
    assert not (out / "Family.csv").exists()
    assert warning_outcomes(_outcomes_of(record)) == ()
    assert classify_latest_reason(record, prior_build=prior) is LatestReason.CLEAN
    now = datetime.fromisoformat(record["timestamp"]) + timedelta(hours=1)
    home = derive_home_status(
        records,
        AppConfig(sis_type="sd51myedbc", setup_completed=True),
        now=now,
        store_created_at=record["timestamp"],
    )
    assert home.verdict is Verdict.HEALTHY


def _outcomes_of(record: dict) -> tuple[EntityOutcome, ...]:
    from src.etl.outcomes import outcomes_from_record

    return outcomes_from_record(record)
