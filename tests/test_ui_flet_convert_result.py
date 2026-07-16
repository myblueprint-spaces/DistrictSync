"""Unit tests for the COUNTED ConvertResult / summarize mapping.

Covers every ``ConvertStatus`` (incl. the exit-3 ``BUILT_NOT_DELIVERED`` booleans,
``NEEDS_ANOMALY_ACK``, ``NO_INPUT``/``NO_OUTPUT``, data-errors) + the privacy
invariant: a ``ConvertResult`` carrying a fake path / ``sis_type`` / column name in
its raw fields must never leak those into the ``summarize`` headline/detail (faults
are named by CATEGORY only — mirrors ``home_status``'s privacy test).
"""

from __future__ import annotations

from src.ui_flet.convert_result import (
    ConvertResult,
    ConvertStatus,
    convert_error_copy,
    deliver_error_copy,
    summarize,
)
from src.ui_flet.verdict import Verdict


class TestSummarizeDelivered:
    def test_delivered_with_sftp_is_healthy_and_mentions_spacesedu(self) -> None:
        result = ConvertResult(
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
        result = ConvertResult(status=ConvertStatus.DELIVERED, sftp_attempted=False)
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.HEALTHY
        assert "converted" in headline.lower()
        # No SFTP requested — the headline must not claim a delivery happened.
        assert "SpacesEDU" not in headline


class TestSummarizeDeliveredFromDisk:
    def test_delivered_from_disk_is_healthy_and_never_claims_a_build(self) -> None:
        """Deliver-from-disk (0034 Slice 2): the files shipped, but NOTHING was converted."""
        result = ConvertResult(
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
            status=ConvertStatus.BUILT_WITH_DATA_ERRORS,
            data_errors_total=3,
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.WARNING
        assert "3 data warnings" in headline
        assert detail

    def test_single_data_error_uses_singular(self) -> None:
        result = ConvertResult(status=ConvertStatus.BUILT_WITH_DATA_ERRORS, data_errors_total=1)
        _verdict, headline, _detail = summarize(result)
        assert "1 data warning" in headline
        assert "warnings" not in headline

    def test_delivered_with_data_errors_stays_a_warning_and_acknowledges_delivery(self) -> None:
        # Fail-loud: a successful delivery must NOT silently erase the data-error warning
        # (mirrors home_status's delivered-with-warnings verdict); it stays a WARNING.
        result = ConvertResult(
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
            status=ConvertStatus.NEEDS_ANOMALY_ACK,
            anomalies=("Students dropped from 100 to 40 rows (60% decrease)",),
        )
        verdict, headline, detail = summarize(result)
        assert verdict is Verdict.WARNING
        assert "smaller than usual" in headline.lower()
        assert detail

    def test_multiple_anomalies_pluralize(self) -> None:
        result = ConvertResult(
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
        verdict, headline, detail = summarize(ConvertResult(status=ConvertStatus.NO_INPUT))
        assert verdict is Verdict.FAILED
        assert "No files could be read" in headline
        assert detail

    def test_no_input_uses_plain_language_not_gde(self) -> None:
        # Vocabulary map (0035 W3b): GDE → "MyEd BC extract files" — no jargon in copy.
        _verdict, headline, detail = summarize(ConvertResult(status=ConvertStatus.NO_INPUT))
        assert "GDE" not in headline and "GDE" not in detail
        assert "MyEd BC extract files" in detail

    def test_no_output_is_failed_plain(self) -> None:
        verdict, headline, detail = summarize(ConvertResult(status=ConvertStatus.NO_OUTPUT))
        assert verdict is Verdict.FAILED
        assert "No output" in headline
        assert detail


class TestSummarizeTotality:
    def test_every_status_has_a_mapping(self) -> None:
        """summarize is TOTAL over ConvertStatus — every member returns a valid triple."""
        for status in ConvertStatus:
            result = ConvertResult(status=status, data_errors_total=1, anomalies=("x",))
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
            status=ConvertStatus.NEEDS_ANOMALY_ACK,
            anomalies=(f"{self._FAKE_COLUMN} in {self._FAKE_PATH} for {self._FAKE_SIS} dropped from 100 to 1 rows",),
        )
        _verdict, headline, detail = summarize(result)
        self._assert_clean(headline, detail)

    def test_no_status_interpolates_the_raw_fields(self) -> None:
        for status in ConvertStatus:
            result = ConvertResult(
                status=status,
                entity_counts={self._FAKE_COLUMN: 5},
                data_errors_total=2,
                anomalies=(f"{self._FAKE_PATH} {self._FAKE_SIS}",),
                quality_text=f"{self._FAKE_PATH} {self._FAKE_COLUMN}",
            )
            _verdict, headline, detail = summarize(result)
            self._assert_clean(headline, detail)


class TestOnErrorCardCopy:
    """0035 W3b (T1 #2): the generic ``on_error`` cards are fixed, bounded, and never a dead end.

    Both providers are ZERO-ARG by contract — no exception, path, or column name can be
    interpolated, so nothing can leak — and each detail ends with a concrete next step
    (what to check → try again → the Help page's support path).
    """

    def test_convert_error_copy_ends_with_a_concrete_next_step(self) -> None:
        headline, detail = convert_error_copy()
        assert headline == "The conversion couldn't finish"
        assert "Your existing files were not changed." in detail
        assert "Check that your input folder" in detail  # the concrete check
        assert "try" in detail and "again" in detail  # the retry
        assert "Help page" in detail and "support" in detail  # the support path

    def test_deliver_error_copy_ends_with_a_concrete_next_step(self) -> None:
        headline, detail = deliver_error_copy()
        assert headline == "The delivery couldn't start"
        assert "Your files were not changed." in detail
        assert "Check your output folder in Settings" in detail  # the fix lives in Settings
        assert "Help page" in detail and "support" in detail

    def test_error_copy_is_plain_language(self) -> None:
        for headline, detail in (convert_error_copy(), deliver_error_copy()):
            for jargon in ("SFTP", "GDE", "exception", "traceback", "SSH"):
                assert jargon not in headline
                assert jargon not in detail

    def test_error_copy_has_no_interpolation_slots(self) -> None:
        # Belt-and-suspenders: fixed copy means no format placeholders a future edit
        # could accidentally feed a raw exception into.
        for headline, detail in (convert_error_copy(), deliver_error_copy()):
            assert "{" not in headline + detail and "}" not in headline + detail
