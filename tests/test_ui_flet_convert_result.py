"""Unit tests for the COUNTED ConvertResult / summarize mapping.

Covers every ``ConvertStatus`` (incl. the exit-3 ``BUILT_NOT_DELIVERED`` booleans,
``NEEDS_ANOMALY_ACK``, ``NO_INPUT``/``NO_OUTPUT``, data-errors) + the privacy
invariant: a ``ConvertResult`` carrying a fake path / ``sis_type`` / column name in
its raw fields must never leak those into the ``summarize`` headline/detail (faults
are named by CATEGORY only — mirrors ``home_status``'s privacy test).
"""

from __future__ import annotations

import inspect

import pytest

from src.etl.errors import EtlError, RunErrorCategory
from src.etl.outcomes import EntityOutcome, OutcomeReason
from src.ui_flet.convert_result import (
    ConvertResult,
    ConvertStatus,
    deliver_error_copy,
    status_for_integrity_fault,
    summarize,
)
from src.ui_flet.failure_copy import (
    FAILED_CATEGORY_COPY,
    NOTHING_SAVED_TAIL,
    NOTHING_SENT_TAIL,
    error_card_copy,
    failed_copy,
    partial_copy,
)
from src.ui_flet.verdict import Verdict


class TestSummarizeDelivered:
    def test_delivered_with_sftp_is_healthy_and_mentions_spacesedu(self) -> None:
        result = ConvertResult(
            delivery_requested=True,
            entity_outcomes=None,
            status=ConvertStatus.DELIVERED,
            entity_counts={"Students": 100},
            sftp_attempted=True,
            sftp_ok=True,
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.HEALTHY
        assert "delivered" in headline.lower()
        assert "SpacesEDU" in headline
        assert detail

    def test_delivered_without_sftp_is_healthy_converted(self) -> None:
        result = ConvertResult(
            delivery_requested=False, entity_outcomes=None, status=ConvertStatus.DELIVERED, sftp_attempted=False
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.HEALTHY
        assert "converted" in headline.lower()
        # No SFTP requested — the headline must not claim a delivery happened.
        assert "SpacesEDU" not in headline


class TestSummarizeDeliveredFromDisk:
    def test_delivered_from_disk_is_healthy_and_never_claims_a_build(self) -> None:
        """Deliver-from-disk (0034 Slice 2): the files shipped, but NOTHING was converted."""
        result = ConvertResult(
            delivery_requested=True,
            entity_outcomes=None,
            status=ConvertStatus.DELIVERED_FROM_DISK,
            sftp_attempted=True,
            sftp_ok=True,
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.HEALTHY
        assert headline == "Files delivered to SpacesEDU"
        assert detail == "The files in your output folder were sent to SpacesEDU successfully."
        # Honesty: a delivery-of-saved-files must not read as a fresh conversion/build.
        for word in ("converted", "built"):
            assert word not in headline.lower()
            assert word not in detail.lower()


class TestSummarizeBuiltNotDelivered:
    def test_exit3_booleans_map_to_failed_built_but_not_delivered(self) -> None:
        """sftp_attempted=True + sftp_ok=False → FAILED 'built but didn't reach SpacesEDU'."""
        result = ConvertResult(
            delivery_requested=True,
            entity_outcomes=None,
            status=ConvertStatus.BUILT_NOT_DELIVERED,
            entity_counts={"Students": 100},
            sftp_attempted=True,
            sftp_ok=False,
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.FAILED
        assert "didn't reach SpacesEDU" in headline
        assert detail  # honest: built + saved, upload failed, files are safe


class TestSummarizeDataErrors:
    def test_data_errors_are_a_warning_with_the_count(self) -> None:
        result = ConvertResult(
            delivery_requested=False,
            entity_outcomes=None,
            status=ConvertStatus.BUILT_WITH_DATA_ERRORS,
            data_errors_total=3,
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.WARNING
        assert "3 data warnings" in headline
        assert detail

    def test_single_data_error_uses_singular(self) -> None:
        result = ConvertResult(
            delivery_requested=False,
            entity_outcomes=None,
            status=ConvertStatus.BUILT_WITH_DATA_ERRORS,
            data_errors_total=1,
        )
        _verdict, headline, _detail = summarize(result)
        assert "1 data warning" in headline
        assert "warnings" not in headline

    def test_delivered_with_data_errors_stays_a_warning_and_acknowledges_delivery(self) -> None:
        # Fail-loud: a successful delivery must NOT silently erase the data-error warning
        # (mirrors home_status's delivered-with-warnings verdict); it stays a WARNING.
        result = ConvertResult(
            delivery_requested=True,
            entity_outcomes=None,
            status=ConvertStatus.DELIVERED_WITH_DATA_ERRORS,
            data_errors_total=2,
            sftp_attempted=True,
            sftp_ok=True,
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.WARNING
        assert "2 data warnings" in headline
        assert "SpacesEDU" in headline  # the delivery is still acknowledged, not hidden
        assert detail


class TestSummarizeAnomalyAck:
    def test_anomaly_ack_is_a_warning_naming_smaller_files(self) -> None:
        result = ConvertResult(
            delivery_requested=False,
            entity_outcomes=None,
            status=ConvertStatus.NEEDS_ANOMALY_ACK,
            anomalies=("Students dropped from 100 to 40 rows (60% decrease)",),
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.WARNING
        assert "smaller than usual" in headline.lower()
        assert detail

    def test_multiple_anomalies_pluralize(self) -> None:
        result = ConvertResult(
            delivery_requested=False,
            entity_outcomes=None,
            status=ConvertStatus.NEEDS_ANOMALY_ACK,
            anomalies=(
                "Students dropped from 100 to 40 rows (60% decrease)",
                "Staff dropped from 20 to 5 rows (75% decrease)",
            ),
        )
        _verdict, _headline, detail = summarize(result)
        assert "2 roster files" in detail


class TestSummarizeNoInputNoOutput:
    def test_no_input_is_failed_plain(self) -> None:
        verdict, headline, detail = summarize(
            ConvertResult(delivery_requested=False, entity_outcomes=None, status=ConvertStatus.NO_INPUT)
        )
        assert verdict is Verdict.FAILED
        assert "No files could be read" in headline
        assert detail

    def test_no_input_uses_plain_language_not_gde(self) -> None:
        # Vocabulary map (0035 W3b): GDE → "MyEd BC extract files" — no jargon in copy.
        _verdict, headline, detail = summarize(
            ConvertResult(delivery_requested=False, entity_outcomes=None, status=ConvertStatus.NO_INPUT)
        )
        assert "GDE" not in headline and "GDE" not in detail
        assert "MyEd BC extract files" in detail

    def test_no_output_is_failed_plain(self) -> None:
        verdict, headline, detail = summarize(
            ConvertResult(delivery_requested=False, entity_outcomes=None, status=ConvertStatus.NO_OUTPUT)
        )
        assert verdict is Verdict.FAILED
        assert "No output" in headline
        assert detail


class TestSummarizeIncompleteRoster:
    """FIX-2 — the delivery gate's refusal, in the product's calm category-only voice."""

    def test_incomplete_roster_is_failed_and_names_the_missing_students(self) -> None:
        result = ConvertResult(
            delivery_requested=False,
            entity_outcomes=None,
            status=ConvertStatus.INCOMPLETE_ROSTER,
            entity_counts={"Classes": 40, "Enrollments": 300, "Family": 80},
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.FAILED
        assert headline == "Your student list came through empty"
        # Plan 0053 S3: the save/send fact is the category copy's shared tail (no delivery was
        # attempted on this result, so it is the output-folder tail) — no longer an absolute
        # "untouched" promise over a best-effort rollback.
        assert detail.endswith("Nothing new was saved to your output folder.")
        assert "student export" in detail  # the concrete next step

    def test_it_is_distinct_from_no_output(self) -> None:
        # Files WERE built here — telling the admin "no output was produced" would send
        # them looking for the wrong fault.
        _v, incomplete, _d = summarize(
            ConvertResult(delivery_requested=False, entity_outcomes=None, status=ConvertStatus.INCOMPLETE_ROSTER)
        )
        _v2, no_output, _d2 = summarize(
            ConvertResult(delivery_requested=False, entity_outcomes=None, status=ConvertStatus.NO_OUTPUT)
        )
        assert incomplete != no_output

    def test_the_copy_is_plain_language_and_carries_no_identifiers(self) -> None:
        _verdict, headline, detail = summarize(
            ConvertResult(delivery_requested=False, entity_outcomes=None, status=ConvertStatus.INCOMPLETE_ROSTER)
        )
        for jargon in ("Students.csv", "roster anchor", "entity", "SFTP", "GDE", "exception", "archive_"):
            assert jargon not in headline and jargon not in detail
        assert "{" not in headline + detail  # fixed copy — no interpolation slot at all


class TestStatusForIntegrityFault:
    """The gate's bounded category → the status that words it (single-sourced, total, loud)."""

    def test_every_category_the_gate_can_return_is_mapped(self) -> None:
        # Derived from the GATE itself, not a hand-written list: a new fault category added
        # to ``check_delivery_integrity`` without Convert copy turns this red.
        import pandas as pd

        from src.etl import pipeline

        frame = pd.DataFrame({"User ID": ["S001"]})
        rostering = ("Students", "Staff", "Family", "Classes", "Enrollments")
        faults = [
            pipeline.check_delivery_integrity({}, rostering),
            pipeline.check_delivery_integrity({"Classes": frame, "Enrollments": frame}, rostering),
        ]
        for fault in faults:
            assert fault is not None
            assert isinstance(status_for_integrity_fault(fault.category), ConvertStatus)

    def test_no_output_and_incomplete_roster_map_to_their_own_statuses(self) -> None:
        assert status_for_integrity_fault(RunErrorCategory.NO_OUTPUT.value) is ConvertStatus.NO_OUTPUT
        assert status_for_integrity_fault(RunErrorCategory.INCOMPLETE_ROSTER.value) is ConvertStatus.INCOMPLETE_ROSTER

    def test_an_unmapped_category_fails_loud(self) -> None:
        # Never default: silently presenting a new fault as an existing one would lie to
        # the admin about why delivery was refused.
        with pytest.raises(ValueError, match="No Convert status is mapped"):
            status_for_integrity_fault("a_future_fault")


class TestOutputFolderUnusableCopy:
    """Plan 0050: all four known output-folder failures land on ONE honest verdict.

    The bug: an unreachable drive, an over-long path, an unwritable folder and a CSV
    held open in Excel all rendered ``convert_error_copy`` - "Check that your input
    folder holds this district's MyEd BC extract files" - which is wrong in every one of
    them. These assertions pin the fix at the copy level, where it is decided.
    """

    def _copy(self) -> tuple[str, str]:
        _verdict, headline, detail = summarize(
            ConvertResult(delivery_requested=False, entity_outcomes=None, status=ConvertStatus.OUTPUT_FOLDER_UNUSABLE)
        )
        return headline, detail

    def test_it_is_a_failed_verdict(self) -> None:
        verdict, _h, _d = summarize(
            ConvertResult(delivery_requested=False, entity_outcomes=None, status=ConvertStatus.OUTPUT_FOLDER_UNUSABLE)
        )
        assert verdict is Verdict.FAILED

    def test_it_names_the_output_folder_and_never_the_input_one(self) -> None:
        headline, detail = self._copy()
        assert "output folder" in headline.lower()
        assert "output folder" in detail.lower()
        assert "input folder" not in detail.lower(), "this is the misattribution the plan exists to remove"
        assert "extract files" not in detail.lower()

    def test_it_is_not_the_generic_on_error_copy(self) -> None:
        # Acceptance criterion 2: none of the four causes may render the generic card (the
        # retired ``convert_error_copy`` became ``error_card_copy`` of an unclassified raise).
        headline, detail = self._copy()
        generic_headline, generic_detail = error_card_copy(RuntimeError("x"), delivery_requested=False)
        assert headline != generic_headline
        assert detail != generic_detail

    def test_it_is_the_output_categorys_shared_copy(self) -> None:
        # Plan 0053 S3: ONE copy source — this status words exactly what Home and Run History
        # say about a run whose record carries the ``output`` category.
        assert self._copy() == failed_copy(RunErrorCategory.OUTPUT, delivery_requested=False)

    def test_it_says_nothing_NEW_was_saved_not_nothing_was_converted(self) -> None:
        # ONE string serves TWO paths. On the write-time path the conversion DID run and
        # only the save failed, so "nothing was converted" would be false there and would
        # contradict the headline ("We couldn't SAVE to your output folder").
        _headline, detail = self._copy()
        assert "nothing new was saved" in detail.lower()
        assert "nothing was converted" not in detail.lower()

    def test_it_never_promises_the_existing_files_are_untouched(self) -> None:
        # The rollback this copy would be leaning on is BEST-EFFORT: `_commit_staged`
        # restores per file inside `try/except OSError` and logs a failed restore at
        # ERROR rather than raising, while `save_all`'s `finally` then discards the
        # backup dir unconditionally. On a drive that drops mid-commit — one of the four
        # causes this status exists for — the restore fails on the same dead path. So the
        # absolute must not be made here; "nothing new was saved" is true regardless.
        _headline, detail = self._copy()
        assert "were not changed" not in detail
        assert "untouched" not in detail.lower()

    def test_it_ends_with_a_concrete_next_step(self) -> None:
        _headline, detail = self._copy()
        assert "Settings" in detail
        assert "Help page" in detail

    def test_the_copy_is_zero_arg_so_nothing_can_be_interpolated(self) -> None:
        # A result carrying a path/district/column in every field must produce the SAME
        # two strings as an empty one - the structural reason nothing can leak.
        loaded = ConvertResult(
            delivery_requested=False,
            entity_outcomes=None,
            status=ConvertStatus.OUTPUT_FOLDER_UNUSABLE,
            entity_counts={"Students": 4},
            data_errors_total=7,
            anomalies=(r"C:\Users\admin\out sd48myedbc",),
            quality_text=r"C:\Users\admin\out",
        )
        assert summarize(loaded)[1:] == self._copy()


class TestSummarizeTotality:
    def test_every_status_has_a_mapping(self) -> None:
        """summarize is TOTAL over ConvertStatus — every member returns a valid triple."""
        for status in ConvertStatus:
            result = ConvertResult(
                delivery_requested=False, entity_outcomes=None, status=status, data_errors_total=1, anomalies=("x",)
            )
            verdict, headline, detail = summarize(result)
            assert isinstance(verdict, Verdict)
            assert isinstance(headline, str) and headline
            assert isinstance(detail, str) and detail


class TestSummarizePrivacy:
    """Faults are named by CATEGORY — a raw path / sis_type / column never leaks."""

    _FAKE_PATH = r"C:\Users\admin\secret\district_extract"
    _FAKE_SIS = "sd48myedbc"
    _FAKE_COLUMN = "Legal Surname"

    def _assert_clean(self, headline: str, detail: str) -> None:
        for leak in (self._FAKE_PATH, self._FAKE_SIS, self._FAKE_COLUMN):
            assert leak not in headline
            assert leak not in detail

    def test_anomaly_strings_carrying_identifiers_never_leak(self) -> None:
        result = ConvertResult(
            delivery_requested=False,
            entity_outcomes=None,
            status=ConvertStatus.NEEDS_ANOMALY_ACK,
            anomalies=(f"{self._FAKE_COLUMN} in {self._FAKE_PATH} for {self._FAKE_SIS} dropped from 100 to 1 rows",),
        )
        _verdict, headline, detail = summarize(result)
        self._assert_clean(headline, detail)

    def test_no_status_interpolates_the_raw_fields(self) -> None:
        for status in ConvertStatus:
            result = ConvertResult(
                delivery_requested=False,
                entity_outcomes=None,
                status=status,
                entity_counts={self._FAKE_COLUMN: 5},
                data_errors_total=2,
                anomalies=(f"{self._FAKE_PATH} {self._FAKE_SIS}",),
                quality_text=f"{self._FAKE_PATH} {self._FAKE_COLUMN}",
            )
            _verdict, headline, detail = summarize(result)
            self._assert_clean(headline, detail)


class TestOnErrorCardCopy:
    """0035 W3b (T1 #2): the ``on_error`` cards are fixed, bounded, and never a dead end.

    The BUILD card is ``failure_copy.error_card_copy(exc)`` since plan 0053 S3 (its own
    tests live in ``tests/test_ui_flet_failure_copy.py``); the deliver pre-flight card stays
    the zero-arg ``deliver_error_copy`` — nothing can be interpolated, so nothing can leak.
    """

    def test_the_input_folder_card_is_retired(self) -> None:
        # Plan 0053 S3: ONE copy source. A second, zero-arg build card would be a second place
        # to word a failure — and the one it replaced pointed every crash at the input folder.
        from src.ui_flet import convert_result

        assert not hasattr(convert_result, "convert_error_copy")
        assert hasattr(convert_result, "deliver_error_copy")  # the twin: the lookup does find a card

    def test_deliver_error_copy_ends_with_a_concrete_next_step(self) -> None:
        headline, detail = deliver_error_copy()
        assert headline == "The delivery couldn't start"
        assert "Your files were not changed." in detail
        assert "Check your output folder in Settings" in detail  # the fix lives in Settings
        assert "Help page" in detail and "support" in detail

    def test_error_copy_is_plain_language(self) -> None:
        cards = (deliver_error_copy(), error_card_copy(RuntimeError("x"), delivery_requested=False))
        for headline, detail in cards:
            for jargon in ("SFTP", "GDE", "exception", "traceback", "SSH"):
                assert jargon not in headline
                assert jargon not in detail

    def test_error_copy_has_no_interpolation_slots(self) -> None:
        # Belt-and-suspenders: fixed copy means no format placeholders a future edit
        # could accidentally feed a raw exception into.
        for headline, detail in (deliver_error_copy(), error_card_copy(ValueError("x"), delivery_requested=True)):
            assert "{" not in headline + detail and "}" not in headline + detail


def _outcomes(*failed: str, reason: OutcomeReason = OutcomeReason.MISSING_SOURCE_COLUMN) -> tuple[EntityOutcome, ...]:
    """A rostering run's outcomes with ``failed`` entities FAILED and the rest BUILT."""
    return tuple(
        EntityOutcome.failed(name, reason) if name in failed else EntityOutcome.built(name, 10)
        for name in ("Students", "Staff", "Family", "Classes", "Enrollments")
    )


_SUCCESS_SHAPED = [
    ConvertStatus.DELIVERED,
    ConvertStatus.DELIVERED_WITH_DATA_ERRORS,
    ConvertStatus.BUILT_WITH_DATA_ERRORS,
]


class TestSummarizePartial:
    """Plan 0053 S3: a success-shaped result whose outcomes show a FAILED entity is PARTIAL."""

    @pytest.mark.parametrize("status", _SUCCESS_SHAPED)
    def test_a_failed_entity_turns_a_success_shaped_status_into_a_warning(self, status: ConvertStatus) -> None:
        result = ConvertResult(
            delivery_requested=True,
            status=status,
            sftp_attempted=True,
            sftp_ok=True,
            entity_outcomes=_outcomes("Family"),
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.WARNING
        expected_headline, expected_detail = partial_copy(_outcomes("Family")[2:3], delivered=True)
        assert headline == expected_headline == "Your roster synced without family contacts"
        assert detail.startswith(expected_detail)

    @pytest.mark.parametrize("status", _SUCCESS_SHAPED)
    def test_twin_the_same_status_with_every_entity_built_is_unchanged(self, status: ConvertStatus) -> None:
        # The positive twin: outcomes are READ, but only a FAILED one changes the verdict.
        built = ConvertResult(
            delivery_requested=True, status=status, sftp_attempted=True, sftp_ok=True, entity_outcomes=_outcomes()
        )
        legacy = ConvertResult(
            delivery_requested=True, status=status, sftp_attempted=True, sftp_ok=True, entity_outcomes=None
        )
        assert summarize(built) == summarize(legacy)
        assert "without" not in summarize(built)[1]

    def test_not_delivered_says_completed(self) -> None:
        result = ConvertResult(
            delivery_requested=False, status=ConvertStatus.DELIVERED, entity_outcomes=_outcomes("Family")
        )
        _verdict, headline, detail = summarize(result)
        assert headline == "Your sync completed without family contacts"
        assert "Everything else completed." in detail
        assert "delivered" not in detail.lower()

    def test_the_data_warning_count_rides_along_as_a_second_sentence(self) -> None:
        result = ConvertResult(
            delivery_requested=False,
            status=ConvertStatus.BUILT_WITH_DATA_ERRORS,
            data_errors_total=3,
            entity_outcomes=_outcomes("Family"),
        )
        _verdict, _headline, detail = summarize(result)
        assert detail.endswith("There were also 3 data warnings: some records had field problems and were left blank.")

    @pytest.mark.parametrize(
        "status",
        [
            ConvertStatus.BUILT_NOT_DELIVERED,
            ConvertStatus.NO_OUTPUT,
            ConvertStatus.INCOMPLETE_ROSTER,
            ConvertStatus.OUTPUT_FOLDER_UNUSABLE,
        ],
    )
    def test_failed_statuses_keep_their_precedence(self, status: ConvertStatus) -> None:
        with_failure = ConvertResult(
            delivery_requested=False, status=status, anomalies=("x",), entity_outcomes=_outcomes("Family")
        )
        without = ConvertResult(delivery_requested=False, status=status, anomalies=("x",), entity_outcomes=None)
        assert summarize(with_failure) == summarize(without)

    def test_the_anomaly_gate_keeps_its_precedence_and_names_why_the_file_is_missing(self) -> None:
        """Plan 0053 S4 (was: identical with and without the outcomes). The gate still WINS —
        same verdict, same headline, its own detail first — and its prompt now carries the
        not-built sentence, so "convert anyway" is consent to a KNOWN cause."""
        with_failure = ConvertResult(
            delivery_requested=False,
            status=ConvertStatus.NEEDS_ANOMALY_ACK,
            anomalies=("x",),
            entity_outcomes=_outcomes("Family"),
        )
        without = ConvertResult(
            delivery_requested=False, status=ConvertStatus.NEEDS_ANOMALY_ACK, anomalies=("x",), entity_outcomes=None
        )
        gated_verdict, gated_headline, gated_detail = summarize(with_failure)
        plain_verdict, plain_headline, plain_detail = summarize(without)
        assert (gated_verdict, gated_headline) == (plain_verdict, plain_headline)
        assert gated_headline == "Some files look much smaller than usual"
        not_built = partial_copy(_outcomes("Family")[2:3], delivered=False)[1]
        assert gated_detail == f"{plain_detail} {not_built}"
        assert "missing a column this district's mapping needs" in gated_detail
        # The twin: with every entity built the prompt is exactly today's.
        built = ConvertResult(
            delivery_requested=False,
            status=ConvertStatus.NEEDS_ANOMALY_ACK,
            anomalies=("x",),
            entity_outcomes=_outcomes(),
        )
        assert summarize(built) == summarize(without)


class TestFailedStatusesReadTheSharedTable:
    """Plan 0053 S3: ONE copy source — every category-FAILED status words its category."""

    @pytest.mark.parametrize(
        ("status", "category"),
        [
            (ConvertStatus.NO_INPUT, RunErrorCategory.NO_INPUT),
            (ConvertStatus.NO_OUTPUT, RunErrorCategory.NO_OUTPUT),
            (ConvertStatus.INCOMPLETE_ROSTER, RunErrorCategory.INCOMPLETE_ROSTER),
            (ConvertStatus.OUTPUT_FOLDER_UNUSABLE, RunErrorCategory.OUTPUT),
        ],
    )
    def test_the_status_renders_its_categorys_copy(self, status: ConvertStatus, category: RunErrorCategory) -> None:
        verdict, headline, detail = summarize(
            ConvertResult(delivery_requested=False, status=status, entity_outcomes=None)
        )
        assert verdict is Verdict.FAILED
        assert (headline, detail) == failed_copy(category, delivery_requested=False)
        assert headline == FAILED_CATEGORY_COPY[category][0]

    @pytest.mark.parametrize(
        ("status", "category"),
        [
            (ConvertStatus.NO_INPUT, RunErrorCategory.NO_INPUT),
            (ConvertStatus.NO_OUTPUT, RunErrorCategory.NO_OUTPUT),
            (ConvertStatus.INCOMPLETE_ROSTER, RunErrorCategory.INCOMPLETE_ROSTER),
            (ConvertStatus.OUTPUT_FOLDER_UNUSABLE, RunErrorCategory.OUTPUT),
        ],
    )
    def test_a_refusal_with_delivery_requested_says_nothing_was_sent_like_the_crash_card(
        self, status: ConvertStatus, category: RunErrorCategory
    ) -> None:
        # One Convert run with delivery ticked gets ONE tail whether it failed as a status or as a
        # raise: every FAILED status stops before the upload, so "requested" means "not sent".
        _verdict, headline, detail = summarize(
            ConvertResult(delivery_requested=True, status=status, entity_outcomes=None)
        )
        assert detail.endswith(NOTHING_SENT_TAIL)
        assert (headline, detail) == error_card_copy(EtlError("x", category=category), delivery_requested=True)

    def test_twin_without_delivery_requested_says_nothing_new_was_saved(self) -> None:
        _v, _h, detail = summarize(
            ConvertResult(delivery_requested=False, status=ConvertStatus.INCOMPLETE_ROSTER, entity_outcomes=None)
        )
        assert detail.endswith(NOTHING_SAVED_TAIL)
        assert NOTHING_SENT_TAIL not in detail


class TestDeliveryRequestedContract:
    """``ConvertResult.delivery_requested`` picks a claim about what did NOT happen — no default."""

    def test_it_is_required_keyword_only_with_no_default(self) -> None:
        param = inspect.signature(ConvertResult).parameters["delivery_requested"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty

    def test_omitting_it_is_a_type_error(self) -> None:
        with pytest.raises(TypeError, match="delivery_requested"):
            ConvertResult(status=ConvertStatus.NO_INPUT, entity_outcomes=None)  # type: ignore[call-arg]

    def test_an_attempt_nobody_requested_is_refused(self) -> None:
        with pytest.raises(ValueError, match="nobody requested"):
            ConvertResult(
                status=ConvertStatus.DELIVERED, sftp_attempted=True, entity_outcomes=None, delivery_requested=False
            )

    def test_twin_an_attempt_that_was_requested_constructs(self) -> None:
        result = ConvertResult(
            status=ConvertStatus.DELIVERED, sftp_attempted=True, entity_outcomes=None, delivery_requested=True
        )
        assert result.delivery_requested is True


class TestUnsetOutputFolderCard:
    """``convert_job``'s unset-output-folder gate error words the OUTPUT category, never DATA."""

    def test_it_is_still_a_value_error_for_every_existing_caller(self) -> None:
        from src.etl.errors import OutputFolderUnsetError

        assert issubclass(OutputFolderUnsetError, ValueError)

    def test_the_card_points_at_the_output_folder(self) -> None:
        from src.etl.errors import OutputFolderUnsetError

        for requested in (True, False):
            card = error_card_copy(OutputFolderUnsetError("x"), delivery_requested=requested)
            assert card == failed_copy(RunErrorCategory.OUTPUT, delivery_requested=requested)
            assert "output folder" in card[1].lower()

    def test_twin_an_untyped_value_error_is_the_data_card(self) -> None:
        # Why the type exists: a bare ValueError classifies to DATA ("something in this district's data").
        card = error_card_copy(ValueError("x"), delivery_requested=False)
        assert card == failed_copy(RunErrorCategory.DATA, delivery_requested=False)
