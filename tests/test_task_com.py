"""``src/scheduler/task_com.py`` — the COM engine's pure layer (plan 0041 Slice 1a).

Everything here runs on EVERY OS: the module under test imports pywin32 lazily, and these
tests exercise the pure helpers (HRESULT unwrapping, the canonical-message table, datetime
sentinel rules, the bounded worker) with synthetic objects — never a real COM apartment.
The live-apartment behaviour was proven against the real Task Scheduler during the slice
(register → read → delete → re-read lifecycle) and is re-proven by the QA walk; what THESE
tests pin is the classification logic those runs flowed through, including the two facts
the live probe caught that no documentation states:

* the real HRESULT of a COM failure hides in ``excepinfo[5]`` behind a generic
  ``DISP_E_EXCEPTION`` wrapper;
* the raw COM task object's never-run sentinel is the **1999-11-30** null date — not the
  1899-12-30 epoch the PowerShell cmdlets showed, which the retired ``Year -gt 1900``
  guard was calibrated to and would have MISSED.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pytest

from src.scheduler import task_com
from src.scheduler.task_com import (
    HR_ACCESS_DENIED,
    HR_ACCOUNT_INFO_NOT_SET,
    HR_LOGON_FAILURE,
    HR_NOT_FOUND,
    BoundedTimeout,
    TaskComError,
    _canonical_message,
    _iso_or_none,
    _unsigned_or_none,
    bounded,
    com_error_scode,
)


class _FakeComError(Exception):
    """The shape of ``pythoncom.com_error`` without needing pywin32 (runs on Linux CI)."""

    def __init__(self, hresult: int, excepinfo: tuple | None) -> None:
        super().__init__(hresult, "fake", excepinfo, None)
        self.hresult = hresult
        self.excepinfo = excepinfo


def _wrapped(scode_signed: int) -> _FakeComError:
    """A DISP_E_EXCEPTION-wrapped failure — the shape the live probe observed."""
    return _FakeComError(-2147352567, (0, None, "Windows' own description", None, 0, scode_signed))


class TestComErrorScode:
    def test_unwraps_the_real_hresult_from_excepinfo(self):
        """THE live-probed trap: hresult is the generic wrapper; excepinfo[5] is real.

        Keying on the outer value would make found=False unreachable — every missing
        task would read as UNKNOWN forever, silently.
        """
        assert com_error_scode(_wrapped(-2147024894)) == HR_NOT_FOUND

    def test_falls_back_to_hresult_when_no_excepinfo(self):
        assert com_error_scode(_FakeComError(-2147024891, None)) == HR_ACCESS_DENIED

    def test_result_is_unsigned(self):
        scode = com_error_scode(_wrapped(-2147024894))
        assert scode is not None and scode > 0

    def test_non_com_error_is_none(self):
        assert com_error_scode(RuntimeError("boom")) is None

    def test_short_excepinfo_falls_back_to_hresult(self):
        assert com_error_scode(_FakeComError(-2147024891, (0, None, "x"))) == HR_ACCESS_DENIED


class TestCanonicalMessage:
    """Rows 9/13 of the 0041 contract: consumers key on EXACT substrings of these."""

    def test_access_denied_keeps_the_adapter_retry_substring(self):
        msg = _canonical_message(HR_ACCESS_DENIED, _wrapped(-2147024891))
        assert msg == "Access is denied."

    def test_not_found_keeps_the_absent_delete_marker(self):
        """`interpret_unregister`'s idempotency keys on "cannot find"."""
        msg = _canonical_message(HR_NOT_FOUND, _wrapped(-2147024894))
        assert "cannot find" in msg.lower()

    @pytest.mark.parametrize("hr", [HR_LOGON_FAILURE, HR_ACCOUNT_INFO_NOT_SET])
    def test_credential_failures_read_as_the_password_message(self, hr):
        assert _canonical_message(hr, _wrapped(0)) == "The user name or password is incorrect."

    def test_unmapped_hresult_surfaces_the_excepinfo_description(self):
        """Row 10: readable Windows prose, never a com_error tuple repr."""
        msg = _canonical_message(0x80041318, _wrapped(0x80041318 - (1 << 32)))
        assert msg == "Windows' own description"

    def test_unmapped_hresult_without_description_names_the_hex_status(self):
        exc = _FakeComError(-2147352567, (0, None, "", None, 0, 0x80041318 - (1 << 32)))
        msg = _canonical_message(0x80041318, exc)
        assert "0x80041318" in msg

    def test_never_a_raw_tuple_repr(self):
        """The whole point of the boundary: an admin-facing message, not `(-2147…, …)`."""
        exc = _wrapped(-2147024891)
        msg = _canonical_message(com_error_scode(exc), exc)
        assert not msg.startswith("(")
        assert "-214" not in msg


class TestIsoOrNone:
    def test_a_real_datetime_becomes_naive_local_iso(self):
        """pywin32 stamps local wall-clock values +00:00 (probed live: a 03:00 local
        trigger read back as 03:00+00:00) — the lying tzinfo is STRIPPED so comparisons
        against naive-local run-record timestamps stay well-defined."""
        value = datetime(2026, 8, 6, 3, 0, 0, tzinfo=timezone.utc)
        assert _iso_or_none(value) == "2026-08-06T03:00:00"

    def test_the_com_null_date_is_none(self):
        """The 1999-11-30 sentinel the live probe caught — NOT the documented 1899 epoch."""
        assert _iso_or_none(datetime(1999, 11, 30, 0, 0, 0, tzinfo=timezone.utc)) is None

    def test_the_cim_epoch_is_also_none(self):
        """The 1899-12-30 epoch the PS cmdlets showed stays covered (year < 2000)."""
        assert _iso_or_none(datetime(1899, 12, 30)) is None

    def test_none_is_none(self):
        assert _iso_or_none(None) is None

    def test_a_dateless_object_is_none(self):
        assert _iso_or_none(object()) is None


class TestUnsignedOrNone:
    def test_has_not_run_passes_through(self):
        assert _unsigned_or_none(267011) == task_com.RESULT_HAS_NOT_RUN

    def test_signed_hresults_normalise_to_unsigned(self):
        """PS/schtasks reported LastTaskResult unsigned; pywin32 may hand it back signed."""
        assert _unsigned_or_none(-2147024894) == 0x80070002

    def test_zero_is_zero_not_none(self):
        assert _unsigned_or_none(0) == 0

    def test_non_numeric_is_none(self):
        assert _unsigned_or_none("267011") is None
        assert _unsigned_or_none(None) is None


class TestBounded:
    def test_returns_the_result(self):
        assert bounded(lambda: 42, timeout_s=5.0, label="t") == 42

    def test_transports_the_raise(self):
        def _boom():
            raise TaskComError(HR_NOT_FOUND, "The system cannot find the file specified.")

        with pytest.raises(TaskComError) as exc_info:
            bounded(_boom, timeout_s=5.0, label="t")
        assert exc_info.value.scode == HR_NOT_FOUND

    def test_timeout_raises_bounded_timeout_and_warns(self, caplog):
        """The row-8 trade: bounded caller, one WARN, a leaked daemon worker — never a
        wedged UI and never a false verdict (the caller maps this to UNKNOWN)."""

        def _hang():
            time.sleep(30)

        with caplog.at_level(logging.WARNING), pytest.raises(BoundedTimeout):
            bounded(_hang, timeout_s=0.2, label="probe")
        assert "did not answer" in caplog.text

    def test_the_worker_is_a_daemon(self):
        """A leaked worker must never block interpreter exit (the packed exe's close)."""
        seen: dict[str, bool] = {}

        def _capture():
            import threading

            seen["daemon"] = threading.current_thread().daemon

        bounded(_capture, timeout_s=5.0, label="t")
        assert seen["daemon"] is True


class TestTaskComErrorShape:
    def test_it_is_plain_data(self):
        """The boundary contract: scode + message, str()-able, no COM baggage."""
        err = TaskComError(HR_ACCESS_DENIED, "Access is denied.")
        assert err.scode == HR_ACCESS_DENIED
        assert str(err) == "Access is denied."

    def test_context_free_raise_shape(self):
        """The raise sites attach NO __context__ (raised outside the except handler) —
        a chained com_error's traceback pins COM objects past CoUninitialize, which is
        the live-observed "releasing IUnknown" teardown failure."""
        try:
            raise TaskComError(None, "x")
        except TaskComError as caught:
            assert caught.__context__ is None
