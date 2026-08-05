"""Tests for per-operation Windows elevation (Plan 0029, D5).

Two layers:
  1. ``src/scheduler/elevation.py`` — the generic elevation IPC primitive: real DPAPI
     round-trip (Windows-only, UAC-free), the request/result handshake protocol, the
     ShellExecuteEx outcome mapping (via mocked ctypes seams — cross-platform), and the
     orphan sweep.
  2. ``src/scheduler/windows.py`` — the self-elevated register/delete flow with elevation
     mocked (plan 0041 S1b: the elevated child is DistrictSync itself in --elevated-apply
     mode): outcome-to-message mapping, read-back confirmation, the structural
     single-source pin (both UAC sides call the same task_com function), and the
     load-bearing proof that the password rides ONLY the DPAPI payload — never the
     child's argv.

No test triggers a real UAC prompt or registers/deletes a real task.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.scheduler import elevation, windows
from src.scheduler.elevation import ElevationOutcome, ElevationResult
from src.scheduler.windows import ScheduleReadback

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is a Windows-only API")


# ---------------------------------------------------------------------------
# DPAPI round-trip (real API — Windows-only, no UAC needed)
# ---------------------------------------------------------------------------


class TestDpapiRoundTrip:
    @WINDOWS_ONLY
    def test_protect_then_unprotect_same_user(self) -> None:
        secret = b'{"DSYNC_TASK_PW":"s3cr3t"}'
        blob = elevation.protect_blob(secret)
        assert blob != secret
        assert elevation.unprotect_blob(blob) == secret

    @WINDOWS_ONLY
    def test_blob_is_opaque_not_plaintext(self) -> None:
        blob = elevation.protect_blob(b"hunter2-plaintext-secret")
        assert b"hunter2-plaintext-secret" not in blob

    @WINDOWS_ONLY
    def test_entropy_mismatch_fails_closed(self) -> None:
        # Sealed with a DIFFERENT entropy → the constant-entropy unprotect must FAIL
        # (this is the tamper/namespacing binding; the SID binding is the real boundary).
        blob = elevation._dpapi("CryptProtectData", b"payload", b"a-different-entropy")
        with pytest.raises(OSError):
            elevation.unprotect_blob(blob)

    @WINDOWS_ONLY
    def test_tampered_blob_fails_closed(self) -> None:
        blob = bytearray(elevation.protect_blob(b"payload-bytes"))
        blob[len(blob) // 2] ^= 0xFF  # flip a bit in the ciphertext
        with pytest.raises(OSError):
            elevation.unprotect_blob(bytes(blob))


# ---------------------------------------------------------------------------
# Request / result handshake protocol
# ---------------------------------------------------------------------------


class TestRequestResultProtocol:
    @WINDOWS_ONLY
    def test_write_request_creates_dpapi_opaque_file(self) -> None:
        payload: dict[str, object] = {"DSYNC_TASK_PW": "hunter2", "DSYNC_TASKNAME": "DistrictSync_Daily"}
        path = elevation.write_request(payload)
        try:
            assert path.exists()
            assert path.name.startswith("dsync_elev_") and path.suffix == ".req"
            data = path.read_bytes()
            # DPAPI-opaque on disk — neither the password nor the JSON keys are readable.
            assert b"hunter2" not in data
            assert b"DSYNC_TASKNAME" not in data
            # ... but it round-trips back to the exact payload for the same user.
            assert elevation.unprotect_blob(data) == json.dumps(payload).encode("utf-8")
        finally:
            path.unlink(missing_ok=True)

    def test_read_result_missing_is_none(self, tmp_path: Path) -> None:
        assert elevation.read_result(tmp_path / "nope.res") is None

    def test_read_result_partial_is_none(self, tmp_path: Path) -> None:
        partial = tmp_path / "x.res"
        partial.write_text('{"ok": tr', encoding="utf-8")  # a torn write
        assert elevation.read_result(partial) is None

    def test_read_result_non_dict_is_none(self, tmp_path: Path) -> None:
        arr = tmp_path / "x.res"
        arr.write_text("[1, 2, 3]", encoding="utf-8")
        assert elevation.read_result(arr) is None

    def test_read_result_valid_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "x.res"
        p.write_text('{"ok": true, "message": "Registered."}', encoding="utf-8")
        assert elevation.read_result(p) == {"ok": True, "message": "Registered."}

    def test_read_result_tolerates_bom(self, tmp_path: Path) -> None:
        # The PowerShell child may prepend a UTF-8 BOM; read_result must not choke.
        p = tmp_path / "x.res"
        p.write_bytes(b'\xef\xbb\xbf{"ok": true}')  # UTF-8 BOM + JSON
        assert elevation.read_result(p) == {"ok": True}

    def test_read_result_caps_oversized_file(self, tmp_path: Path) -> None:
        # A corrupt/runaway result file is never slurped whole — over the cap → None.
        p = tmp_path / "big.res"
        p.write_text('{"ok": true, "pad": "' + "x" * (65 * 1024) + '"}', encoding="utf-8")
        assert elevation.read_result(p) is None

    def test_new_result_path_is_reserved_not_created(self) -> None:
        p = elevation.new_result_path()
        assert p.name.startswith("dsync_elev_") and p.suffix == ".res"
        assert not p.exists()


# ---------------------------------------------------------------------------
# Orphan sweep
# ---------------------------------------------------------------------------


class TestElevationHelpers:
    def test_icacls_resolves_through_the_shared_system_binary_seam(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Elevation's remaining subprocess (icacls DACL) resolves through the ONE shared
        helper — a second local copy is how a call site drifts back to a hijackable bare
        name. (powershell.exe left this module entirely at plan 0041 S1b: the elevated
        child is the caller's own exe, passed in by windows._run_elevated_child.)"""
        monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
        from src.utils.helpers import system_binary

        assert system_binary("icacls.exe") == r"C:\Windows\System32\icacls.exe"
        with pytest.raises(ValueError):
            system_binary("powershell.exe")  # retired from the allowlist with its last caller

    def test_current_user_prefers_domain_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USERDOMAIN", "CORP")
        monkeypatch.setenv("USERNAME", "jane")
        assert elevation._current_user() == "CORP\\jane"

    def test_current_user_falls_back_to_bare_username(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("USERDOMAIN", raising=False)
        monkeypatch.setenv("USERNAME", "jane")
        assert elevation._current_user() == "jane"

    def test_owner_only_dacl_icacls_passes_no_window_flag(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The windowed exe must not flash a console when icacls locks the DPAPI request file's DACL.
        from src.utils.helpers import subprocess_no_window_flags

        monkeypatch.setattr(elevation.sys, "platform", "win32")
        monkeypatch.setattr(elevation, "_current_user", lambda: "CORP\\jane")
        captured: dict[str, object] = {}

        def _fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return MagicMock(returncode=0)

        monkeypatch.setattr(elevation.subprocess, "run", _fake_run)
        elevation._set_owner_only_dacl(tmp_path / "dsync_elev_x.req")
        assert captured["argv"][0].endswith("icacls.exe")  # type: ignore[union-attr]
        assert captured["kwargs"]["creationflags"] == subprocess_no_window_flags()  # type: ignore[index]

    def test_owner_only_dacl_invokes_absolute_system32_icacls(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """argv[0] is the ABSOLUTE System32 icacls.exe — never a bare ``icacls``.

        This call locks the DACL on the DPAPI-sealed request file that carries the
        district account password; a bare name would let a binary planted in the calling
        exe's directory or the CWD (both probed before System32 absent
        ``SafeProcessSearchMode``) run *with that file's path as an argument*.
        """
        monkeypatch.setattr(elevation.sys, "platform", "win32")
        monkeypatch.setattr(elevation, "_current_user", lambda: "CORP\\jane")
        monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
        captured: dict[str, object] = {}

        def _fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
            captured["argv"] = argv
            return MagicMock(returncode=0)

        monkeypatch.setattr(elevation.subprocess, "run", _fake_run)
        elevation._set_owner_only_dacl(tmp_path / "dsync_elev_x.req")

        argv = captured["argv"]
        assert argv[0] == r"C:\Windows\System32\icacls.exe"  # type: ignore[index]
        assert argv[0] != "icacls"  # type: ignore[index]
        # The remaining owner-only DACL arguments are unchanged.
        assert argv[2:] == ["/inheritance:r", "/grant:r", "CORP\\jane:F"]  # type: ignore[index]


class TestSweepOrphans:
    def test_deletes_old_keeps_fresh(self) -> None:
        from src.utils import paths

        directory = paths.user_data_dir()
        old = directory / "dsync_elev_old.req"
        old.write_text("x", encoding="utf-8")
        fresh = directory / "dsync_elev_fresh.req"
        fresh.write_text("y", encoding="utf-8")
        two_hours_ago = time.time() - 7200
        os.utime(old, (two_hours_ago, two_hours_ago))

        removed = elevation.sweep_orphans()

        assert removed >= 1
        assert not old.exists()
        assert fresh.exists()  # an in-flight handshake is left alone

    def test_sweep_ignores_unrelated_files(self) -> None:
        from src.utils import paths

        directory = paths.user_data_dir()
        other = directory / "config.json"
        other.write_text("{}", encoding="utf-8")
        old = time.time() - 7200
        os.utime(other, (old, old))

        elevation.sweep_orphans()

        assert other.exists()  # not a dsync_elev_* handshake file → untouched


# ---------------------------------------------------------------------------
# run_elevated_powershell outcome mapping (ctypes seams mocked — cross-platform)
# ---------------------------------------------------------------------------


class TestRunElevatedOutcomeMapping:
    def _win(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(elevation.sys, "platform", "win32")

    def test_declined_1223_is_declined(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._win(monkeypatch)
        monkeypatch.setattr(elevation, "_shell_execute_runas", lambda f, p: (0, 1223))
        out = elevation.run_elevated("C:/app.exe", "--elevated-apply a b", timeout_s=1)
        assert out.result is ElevationResult.DECLINED

    def test_other_shellexec_error_is_launch_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._win(monkeypatch)
        monkeypatch.setattr(elevation, "_shell_execute_runas", lambda f, p: (0, 5))
        out = elevation.run_elevated("C:/app.exe", "x", timeout_s=1)
        assert out.result is ElevationResult.LAUNCH_FAILED

    def test_timeout_terminates_and_is_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._win(monkeypatch)
        monkeypatch.setattr(elevation, "_shell_execute_runas", lambda f, p: (1234, 0))
        monkeypatch.setattr(elevation, "_wait_for_process", lambda h, t: elevation._WAIT_TIMEOUT)
        terminated: list[int] = []
        closed: list[int] = []
        monkeypatch.setattr(elevation, "_terminate_process", lambda h: terminated.append(h))
        monkeypatch.setattr(elevation, "_close_handle", lambda h: closed.append(h))
        out = elevation.run_elevated("C:/app.exe", "x", timeout_s=1)
        assert out.result is ElevationResult.TIMEOUT
        assert terminated == [1234]  # the hung child was terminated
        assert closed == [1234]  # the handle is always closed

    def test_completed_returns_exit_code_and_closes_handle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._win(monkeypatch)
        closed: list[int] = []
        monkeypatch.setattr(elevation, "_shell_execute_runas", lambda f, p: (1234, 0))
        monkeypatch.setattr(elevation, "_wait_for_process", lambda h, t: 0)
        monkeypatch.setattr(elevation, "_get_exit_code", lambda h: 7)
        monkeypatch.setattr(elevation, "_close_handle", lambda h: closed.append(h))
        out = elevation.run_elevated("C:/app.exe", "x", timeout_s=1)
        assert out.result is ElevationResult.COMPLETED
        assert out.exit_code == 7
        assert closed == [1234]

    def test_non_windows_is_launch_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(elevation.sys, "platform", "linux")
        out = elevation.run_elevated("C:/app.exe", "x", timeout_s=1)
        assert out.result is ElevationResult.LAUNCH_FAILED

    def test_passes_file_and_params_through_verbatim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """run_elevated launches exactly what the caller resolved — the caller owns the
        target-binary decision (plan 0041 row 15), this primitive only launches it."""
        self._win(monkeypatch)
        captured: dict[str, str] = {}

        def _fake(file: str, params: str) -> tuple[int, int]:
            captured["file"] = file
            captured["params"] = params
            return (0, 1223)

        monkeypatch.setattr(elevation, "_shell_execute_runas", _fake)
        elevation.run_elevated("C:/DistrictSync/DistrictSync.exe", '--elevated-apply "r.req" "r.res"', timeout_s=1)
        assert captured["file"] == "C:/DistrictSync/DistrictSync.exe"
        assert captured["params"] == '--elevated-apply "r.req" "r.res"'


# ---------------------------------------------------------------------------
# windows.register_task — the self-elevated path (elevation mocked)
# ---------------------------------------------------------------------------


_SECRET = "P@ssw0rd-do-not-leak-42"


def _patch_run_elevated(monkeypatch: pytest.MonkeyPatch, captured: dict, outcome: ElevationOutcome) -> None:
    def _run(file: str, params: str, *, timeout_s: float) -> ElevationOutcome:
        captured["file"] = file
        captured["params"] = params
        return outcome

    monkeypatch.setattr("src.scheduler.elevation.run_elevated", _run)


class TestRegisterElevatedFlow:
    def _patch_win_nonelevated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.scheduler.windows.sys.platform", "win32")
        monkeypatch.setattr("src.scheduler.windows.is_elevated", lambda: False)

    def _register(self) -> tuple[bool, str]:
        return windows.register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("C:/DistrictSync/DistrictSync.exe"),
            sis_type="myedbc",
            input_dir=Path("C:/input"),
            output_dir=Path("C:/output"),
            run_time="03:00",
            run_as_user="CORP\\jane",
            run_as_password=_SECRET,
        )

    def test_ok_confirmed_via_readback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "dsync_elev_abc.req"
        req.write_bytes(b"blob")
        captured: dict[str, object] = {}

        def _write_request(payload: dict[str, object]) -> Path:
            captured["payload"] = payload
            return req

        monkeypatch.setattr("src.scheduler.elevation.write_request", _write_request)
        _patch_run_elevated(monkeypatch, captured, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0))
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: {"ok": True, "message": ""})
        confirm = MagicMock(return_value=ScheduleReadback(found=True))
        monkeypatch.setattr("src.scheduler.windows.read_schedule", confirm)

        ok, _msg = self._register()

        assert ok is True
        confirm.assert_called_once()  # success is CONFIRMED via read-back, not assumed
        # The password rode the DPAPI payload (the sanctioned secure channel) ...
        payload = captured["payload"]
        assert payload["password"] == _SECRET  # type: ignore[index]
        assert payload["op"] == "register"  # type: ignore[index]
        # ... but NEVER the child argv (visible to every process on the box).
        assert _SECRET not in str(captured["params"])
        assert "--elevated-apply" in str(captured["params"])
        assert str(req) in str(captured["params"])  # the request path rides argv (no secret)
        assert not req.exists()  # handshake file cleaned up in finally

    def test_child_is_our_own_exe_never_powershell(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """S1b: the elevated child is sys.executable in --elevated-apply mode — the
        encoded-PowerShell persistence signature is gone from the elevation path too."""
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        captured: dict[str, object] = {}
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, captured, ElevationOutcome(ElevationResult.DECLINED))

        self._register()

        assert str(captured["file"]) == sys.executable
        assert "powershell" not in str(captured["file"]).lower()
        assert "-EncodedCommand" not in str(captured["params"])

    def test_no_result_confirms_via_readback_then_no_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "dsync_elev_abc.req"
        req.write_bytes(b"blob")
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, {}, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0))
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: None)  # child wrote nothing
        confirm = MagicMock(return_value=ScheduleReadback(found=None))
        monkeypatch.setattr("src.scheduler.windows.read_schedule", confirm)

        ok, msg = self._register()

        assert ok is False
        assert msg == windows._MSG_ELEVATION_NO_RESULT
        confirm.assert_called_once()  # a missing result is resolved by read-back, not assumed-failed

    def test_no_result_but_readback_found_is_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "dsync_elev_abc.req"
        req.write_bytes(b"blob")
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, {}, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0))
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: None)
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=True))
        ok, _msg = self._register()
        assert ok is True  # the child crashed before writing a result, but the task IS registered

    def test_declined_maps_to_uac_declined(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, {}, ElevationOutcome(ElevationResult.DECLINED))
        read_result = MagicMock()
        monkeypatch.setattr("src.scheduler.elevation.read_result", read_result)

        ok, msg = self._register()

        assert ok is False
        assert msg == windows._MSG_UAC_DECLINED
        read_result.assert_not_called()  # a declined prompt produces no result file

    def test_timeout_confirmed_via_readback_is_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # A timeout is POST-consent; the terminated child may already have registered — a
        # read-back that finds the task turns the long-running attempt into a confirmed success.
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        read_result = MagicMock()
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, {}, ElevationOutcome(ElevationResult.TIMEOUT))
        monkeypatch.setattr("src.scheduler.elevation.read_result", read_result)
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=True))
        ok, _msg = self._register()
        assert ok is True
        read_result.assert_not_called()  # a timeout has no result file to read — read-back decides

    def test_timeout_unconfirmed_is_hedged_timeout(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, {}, ElevationOutcome(ElevationResult.TIMEOUT))
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=None))
        ok, msg = self._register()
        assert ok is False
        assert msg == windows._MSG_ELEVATION_TIMEOUT  # hedged: may-or-may-not-have-registered

    def test_different_account_sentinel_maps_to_different_account(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, {}, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0))
        monkeypatch.setattr(
            "src.scheduler.elevation.read_result",
            lambda p: {"ok": False, "message": "DSYNC_DIFFERENT_ACCOUNT"},
        )
        ok, msg = self._register()
        assert ok is False
        assert msg == windows._MSG_DIFFERENT_ACCOUNT

    def test_child_registration_failure_is_sanitized_passthrough(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, {}, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0))
        monkeypatch.setattr(
            "src.scheduler.elevation.read_result",
            lambda p: {"ok": False, "message": "The user name or password is incorrect."},
        )
        ok, msg = self._register()
        assert ok is False
        assert "password is incorrect" in msg  # real cause surfaced (sanitized)

    def test_child_message_carrying_dsync_token_is_scrubbed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, {}, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0))
        monkeypatch.setattr(
            "src.scheduler.elevation.read_result",
            lambda p: {"ok": False, "message": "boom DSYNC_TASK_PW=leak"},
        )
        ok, msg = self._register()
        assert ok is False
        assert "DSYNC_" not in msg and "leak" not in msg  # defense-in-depth scrub

    def test_ok_but_readback_unknown_is_no_result(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, {}, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0))
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: {"ok": True})
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=None))
        ok, msg = self._register()
        assert ok is False  # honest: child said ok, but read-back couldn't confirm
        assert msg == windows._MSG_ELEVATION_NO_RESULT

    def test_password_never_on_the_launch_argv(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """The ONE process this flow launches is the elevated child; its argv must carry
        the handshake paths and nothing secret. (There is no subprocess.run anywhere in
        the scheduler any more — that absence is pinned in test_schedulers.py.)"""
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        captured: dict[str, object] = {}
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        _patch_run_elevated(monkeypatch, captured, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0))
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: {"ok": True})
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=True))

        self._register()

        assert _SECRET not in str(captured["file"])
        assert _SECRET not in str(captured["params"])


# ---------------------------------------------------------------------------
# The structural single-source pin (replaces the retired script-text pin)
# ---------------------------------------------------------------------------


class TestRegistrationSingleSource:
    def test_direct_and_elevated_paths_call_the_same_function(self) -> None:
        """The retired PS transport shared `_register_body` TEXT between the direct script
        and the elevated bootstrap; the COM transport shares the FUNCTION. Pin: the
        elevated child's register op resolves to the very same callable the direct path
        calls — a fork would show up as two distinct functions here."""
        import src.scheduler.elevated_apply as child
        import src.scheduler.task_com as tc

        # Both sides name task_com.register_task_definition — not a copy.
        assert child._do_register.__module__ == "src.scheduler.elevated_apply"
        src_text = Path(child.__file__).read_text(encoding="utf-8")
        assert "task_com.register_task_definition(" in src_text
        assert callable(tc.register_task_definition)

    def test_elevated_child_command_shape(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Dev mode prefixes `-m src.main`; frozen mode is the bare exe. Both carry the
        quoted handshake paths after --elevated-apply."""
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "src.scheduler.elevation.run_elevated",
            lambda file, params, *, timeout_s: (
                captured.update(file=file, params=params) or ElevationOutcome(ElevationResult.DECLINED)
            ),
        )
        req, res = tmp_path / "a.req", tmp_path / "a.res"
        windows._run_elevated_child(req, res)
        params = str(captured["params"])
        if Path(sys.executable).name.lower().startswith("python"):
            assert params.startswith("-m src.main --elevated-apply ")
        else:  # pragma: no cover - frozen-exe shape
            assert params.startswith("--elevated-apply ")
        assert f'"{req}"' in params and f'"{res}"' in params


# ---------------------------------------------------------------------------
# windows.delete_task_elevated
# ---------------------------------------------------------------------------


class TestDeleteTaskElevated:
    def _mk(
        self,
        monkeypatch: pytest.MonkeyPatch,
        req: Path,
        outcome: ElevationOutcome,
        result: object,
        captured: dict | None = None,
    ) -> None:
        def _write_request(payload: dict[str, object]) -> Path:
            if captured is not None:
                captured["payload"] = payload
            return req

        monkeypatch.setattr("src.scheduler.elevation.write_request", _write_request)
        _patch_run_elevated(monkeypatch, captured if captured is not None else {}, outcome)
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: result)

    def test_ok_confirmed_removed_via_readback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # The child's self-reported ok is NOT trusted alone — removal is confirmed by a
        # read-back that finds the task gone (found=False).
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        captured: dict[str, object] = {}
        self._mk(monkeypatch, req, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0), {"ok": True}, captured)
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=False))
        ok, msg = windows.delete_task_elevated("DistrictSync_Daily")
        assert ok is True
        assert "removed" in msg.lower()
        assert captured["payload"] == {"op": "delete", "task_name": "DistrictSync_Daily"}  # type: ignore[index]

    def test_ok_but_still_present_is_unconfirmed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        self._mk(monkeypatch, req, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0), {"ok": True})
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=True))
        ok, msg = windows.delete_task_elevated("DistrictSync_Daily")
        assert ok is False
        assert msg == windows._MSG_ELEVATION_REMOVE_UNCONFIRMED

    def test_timeout_confirmed_removed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        self._mk(monkeypatch, req, ElevationOutcome(ElevationResult.TIMEOUT), None)
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=False))
        ok, _msg = windows.delete_task_elevated("DistrictSync_Daily")
        assert ok is True

    def test_no_result_unconfirmed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        self._mk(monkeypatch, req, ElevationOutcome(ElevationResult.COMPLETED, exit_code=0), None)
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=None))
        ok, msg = windows.delete_task_elevated("DistrictSync_Daily")
        assert ok is False
        assert msg == windows._MSG_ELEVATION_REMOVE_UNCONFIRMED

    def test_declined(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        self._mk(monkeypatch, req, ElevationOutcome(ElevationResult.DECLINED), None)
        ok, msg = windows.delete_task_elevated("DistrictSync_Daily")
        assert ok is False
        assert msg == windows._MSG_UAC_DECLINED

    def test_handshake_cleaned_up_in_finally(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        self._mk(monkeypatch, req, ElevationOutcome(ElevationResult.DECLINED), None)
        windows.delete_task_elevated("DistrictSync_Daily")
        assert not req.exists()

    def test_invalid_task_name_rejected_before_elevation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        launched = MagicMock()
        monkeypatch.setattr("src.scheduler.elevation.run_elevated", launched)
        with pytest.raises(ValueError):
            windows.delete_task_elevated("bad;name|rm")
        launched.assert_not_called()
