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

from src.scheduler import elevated_apply, task_com, windows
from src.scheduler.messages import ACCESS_DENIED_MARKERS, SECRET_SENTINEL_PREFIX, carries_foreign_marker
from src.scheduler.task_com import (
    _HRESULT_CANONICAL,
    HR_ACCESS_DENIED,
    HR_ACCOUNT_INFO_NOT_SET,
    HR_LOGON_FAILURE,
    HR_NO_SUCH_LOGON_SESSION,
    HR_NOT_FOUND,
    MSG_ACCESS_DENIED,
    MSG_ACCOUNT_INFO_NOT_SET,
    MSG_LOGON_FAILURE,
    MSG_NO_LOGON_SESSION,
    MSG_NOT_FOUND,
    MSG_OPERATION_FAILED,
    BoundedTimeout,
    TaskComError,
    _canonical_message,
    _iso_or_none,
    _unsigned_or_none,
    bounded,
    com_error_scode,
    format_hresult,
    hresult_for,
)
from src.ui_flet.schedule_status import interpret_unregister

# The measured Windows FormatMessage text for the two codes whose OWN description carries a
# marker another consumer keys on — the live hazard plan 0047 closes (A7, measured 2026-09-16).
WINDOWS_TEXT_NO_LOGON_SESSION = "A specified logon session does not exist. It may already have been terminated."
WINDOWS_TEXT_PATH_NOT_FOUND = "The system cannot find the path specified."


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

    @pytest.mark.parametrize(
        ("hr", "expected"),
        [
            (HR_LOGON_FAILURE, MSG_LOGON_FAILURE),
            (HR_ACCOUNT_INFO_NOT_SET, MSG_ACCOUNT_INFO_NOT_SET),
            (HR_NO_SUCH_LOGON_SESSION, MSG_NO_LOGON_SESSION),
        ],
    )
    def test_each_credential_class_has_its_own_canonical(self, hr, expected):
        """DELIBERATE contract change (plan 0047, defect A2).

        This row used to assert that HR_LOGON_FAILURE and HR_ACCOUNT_INFO_NOT_SET both read
        as "The user name or password is incorrect." — one string for two different statuses.
        The classifier keys on these by EXACT equality, so the conflation made an admin whose
        task has NO SAVED ACCOUNT INFORMATION (0x8004130F — a GET-path lookup miss that has no
        password parameter at all) be told to retype a password forever. Splitting them is the
        whole point of the slice; HR_LOGON_FAILURE's text stays byte-identical so nothing that
        matched a real wrong-password failure stops matching.
        """
        assert _canonical_message(hr, _wrapped(0)) == expected

    def test_the_logon_failure_text_is_unchanged(self):
        """The one golden pin: the live-observed wrong-password text (2026-08-05)."""
        assert MSG_LOGON_FAILURE == "The user name or password is incorrect."

    def test_unmapped_hresult_surfaces_the_excepinfo_description_with_its_code(self):
        """Row 10 + plan 0047: readable Windows prose, now carrying the code.

        DELIBERATE change — this assertion used to be ``== "Windows' own description"``. The
        hex suffix is what lets the classifier's details clause (and the district's log line)
        answer "which failure?" for a code we have not mapped yet; N2 names the log line as the
        mechanism by which the next unknown code becomes evidence.
        """
        msg = _canonical_message(0x80041318, _wrapped(0x80041318 - (1 << 32)))
        assert msg == "Windows' own description (0x80041318)"

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


# ---------------------------------------------------------------------------
# Plan 0047 — the marker-guard / injectivity boundary (INVARIANTS)
# ---------------------------------------------------------------------------


class TestTableSweep:
    """Every row of the ONE table, round-tripped through the real consumer.

    The positive twin and the negative sweep in one line: `interpret_unregister` must be
    success-shaped for EXACTLY the not-found row. A marker leaking into any other canonical
    turns a FAILED removal into "No schedule was registered", after which Setup persists
    ``schedule_registered = False`` over a task that is still live (defect A7).
    """

    @pytest.mark.parametrize(("hr", "msg"), sorted(_HRESULT_CANONICAL.items()))
    def test_only_the_not_found_row_is_success_shaped(self, hr, msg):
        assert interpret_unregister(False, msg).success_shaped is (hr == HR_NOT_FOUND)

    @pytest.mark.parametrize(("hr", "msg"), sorted(_HRESULT_CANONICAL.items()))
    def test_only_the_access_denied_row_carries_an_access_denied_marker(self, hr, msg):
        carries = any(marker in msg.lower() for marker in ACCESS_DENIED_MARKERS)
        assert carries is (hr == HR_ACCESS_DENIED)

    @pytest.mark.parametrize(("hr", "msg"), sorted(_HRESULT_CANONICAL.items()))
    def test_no_row_carries_the_secret_sentinel_prefix(self, hr, msg):
        assert SECRET_SENTINEL_PREFIX not in msg


class TestDescriptionGuard:
    """The other escape: Windows' OWN description, and ``str(exc)`` when there is no scode."""

    def test_the_measured_logon_session_description_is_dropped_for_its_code(self):
        """A7, measured 2026-09-16: this exact Windows text carries "does not exist"."""
        exc = _FakeComError(-2147352567, (0, None, WINDOWS_TEXT_NO_LOGON_SESSION, None, 0, 0x7654321 - (1 << 32)))
        msg = _canonical_message(0xF7654321, exc)
        assert not carries_foreign_marker(msg)
        assert "0xF7654321" in msg

    def test_the_measured_path_not_found_description_is_dropped_for_its_code(self):
        exc = _FakeComError(-2147352567, (0, None, WINDOWS_TEXT_PATH_NOT_FOUND, None, 0, 0x7654321 - (1 << 32)))
        msg = _canonical_message(0xF7654321, exc)
        assert not carries_foreign_marker(msg)
        assert "0xF7654321" in msg

    def test_an_access_denied_description_is_dropped_too(self):
        """G7's ONE recorded behaviour change: an UNMAPPED code whose description happens to
        say "access is denied" no longer fires the delete path's elevated retry. The code that
        OWNS the marker (0x80070005) still does — pinned by TestTableSweep above."""
        exc = _FakeComError(-2147352567, (0, None, "Access is denied by policy.", None, 0, 0x7654321 - (1 << 32)))
        msg = _canonical_message(0xF7654321, exc)
        assert not any(marker in msg.lower() for marker in ACCESS_DENIED_MARKERS)

    def test_a_marker_free_description_passes_through_with_its_code(self):
        """The POSITIVE twin: the guard must not swallow every description."""
        exc = _FakeComError(-2147352567, (0, None, "The task XML is malformed.", None, 0, 0x7654321 - (1 << 32)))
        assert _canonical_message(0xF7654321, exc) == "The task XML is malformed. (0xF7654321)"

    def test_str_exc_is_guarded_the_same_way_when_there_is_no_scode(self):
        msg = _canonical_message(None, RuntimeError("the folder does not exist here"))
        assert msg == MSG_OPERATION_FAILED

    def test_a_marker_free_str_exc_still_passes_through(self):
        """The positive twin for the ``scode is None`` escape."""
        assert _canonical_message(None, RuntimeError("the task XML is malformed")) == "the task XML is malformed"

    def test_a_secret_sentinel_bearing_description_is_also_dropped_for_its_code(self):
        """Stage 7 coverage gap (finding 10): the sweep above never fed a
        ``SECRET_SENTINEL_PREFIX``-bearing string through here — only the absent-task and
        access-denied markers were exercised. `messages.carries_foreign_marker` covers all
        three markers already, so this pins the third."""
        exc = _FakeComError(-2147352567, (0, None, "leaked DSYNC_TASK_PW=hunter2", None, 0, 0x7654321 - (1 << 32)))
        msg = _canonical_message(0xF7654321, exc)
        assert not carries_foreign_marker(msg)
        assert SECRET_SENTINEL_PREFIX not in msg
        assert "0xF7654321" in msg

    def test_a_secret_sentinel_bearing_str_exc_is_also_dropped(self):
        """The ``scode is None`` twin of the row above."""
        msg = _canonical_message(None, RuntimeError("leaked DSYNC_TASK_PW=hunter2"))
        assert msg == MSG_OPERATION_FAILED
        assert SECRET_SENTINEL_PREFIX not in msg


class TestMessageInjectivity:
    """Exact-equality classification is only sound on an injective producible set (G3)."""

    @staticmethod
    def _producible() -> dict[str, list[str]]:
        names: dict[str, list[str]] = {}
        for module in (task_com, windows, elevated_apply):
            for name, value in vars(module).items():
                if isinstance(value, str) and (name.startswith("MSG_") or name.startswith("_MSG_")):
                    names.setdefault(value, []).append(f"{module.__name__}.{name}")
        return names

    def test_every_producible_message_has_exactly_one_name(self):
        duplicates = {value: owners for value, owners in self._producible().items() if len(owners) > 1}
        assert duplicates == {}

    def test_the_sweep_sees_the_constants_at_all(self):
        """Not vacuous: the reflection must actually find every producible message (23
        today — 7 engine canonicals, 12 transport categories, 4 child refusals; the 12th is
        _MSG_ELEVATED_ACCESS_DENIED, added with its classifier branch at plan 0047 A2)."""
        assert len(self._producible()) == 23

    def test_every_table_value_is_a_named_constant(self):
        produced = self._producible()
        assert all(value in produced for value in _HRESULT_CANONICAL.values())


class TestFormatHresult:
    @pytest.mark.parametrize(
        ("scode", "expected"),
        [
            (None, "n/a"),
            (0, "0x00000000"),
            (HR_NO_SUCH_LOGON_SESSION, "0x80070520"),
            (-2147024891, "0x80070005"),  # a SIGNED int arrives from excepinfo — the & mask
        ],
    )
    def test_total_over_every_shape(self, scode, expected):
        assert format_hresult(scode) == expected

    def test_a_non_numeric_excepinfo_scode_reaches_the_n_a_path(self):
        """The REACHABLE "n/a" source: com_error_scode cannot coerce excepinfo[5]."""
        exc = _FakeComError(-2147352567, (0, None, "x", None, 0, "not-a-number"))
        assert com_error_scode(exc) is None
        assert format_hresult(com_error_scode(exc)) == "n/a"


class TestHresultFor:
    @pytest.mark.parametrize(("hr", "msg"), sorted(_HRESULT_CANONICAL.items()))
    def test_round_trips_every_row(self, hr, msg):
        assert hresult_for(msg) == hr

    @pytest.mark.parametrize("text", ["", "Windows' own description (0x80041318)", MSG_OPERATION_FAILED])
    def test_anything_else_is_none(self, text):
        assert hresult_for(text) is None

    def test_the_named_constants_are_the_table(self):
        assert hresult_for(MSG_ACCESS_DENIED) == HR_ACCESS_DENIED
        assert hresult_for(MSG_NOT_FOUND) == HR_NOT_FOUND
        assert hresult_for(MSG_NO_LOGON_SESSION) == HR_NO_SUCH_LOGON_SESSION


class TestOneHexSpelling:
    """A re-spelling guard (NOT a red-first pin — it is already true today).

    ``format_hresult`` is the ONE place the ``0x%08X`` shape is spelled. A second formatter
    would let the log line and the message drift on padding or case.
    """

    def test_src_contains_exactly_one_08x_format_spelling(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1] / "src"
        hits = [
            f"{path.name}:{n}"
            for path in sorted(root.rglob("*.py"))
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if ":08X" in line
        ]
        assert len(hits) == 1, hits
        assert hits[0].startswith("task_com.py:"), hits


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
