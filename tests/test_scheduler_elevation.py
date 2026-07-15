"""Tests for per-operation Windows elevation (Plan 0029, D5).

Two layers:
  1. ``src/scheduler/elevation.py`` — the generic elevation IPC primitive: real DPAPI
     round-trip (Windows-only, UAC-free), the request/result handshake protocol, the
     ShellExecuteEx outcome mapping (via mocked ctypes seams — cross-platform), and the
     orphan sweep.
  2. ``src/scheduler/windows.py`` — the self-elevated register/delete flow with elevation
     mocked: outcome→message mapping, read-back confirmation, the fixed child-bootstrap
     script text (fail-closed different-account branch, entropy constant, never echoes the
     password), and the load-bearing security proof that the password NEVER appears on
     argv/env of any subprocess call or in the encoded bootstrap.

No test triggers a real UAC prompt or registers/deletes a real task.
"""

from __future__ import annotations

import base64
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
    def test_system_powershell_path_is_absolute_system32(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
        path = elevation._system_powershell_path()
        assert "System32" in path
        assert "WindowsPowerShell" in path
        assert path.endswith("powershell.exe")
        assert path != "powershell"

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
        assert captured["argv"][0] == "icacls"  # type: ignore[index]
        assert captured["kwargs"]["creationflags"] == subprocess_no_window_flags()  # type: ignore[index]


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
        out = elevation.run_elevated_powershell("QUJD", timeout_s=1)
        assert out.result is ElevationResult.DECLINED

    def test_other_shellexec_error_is_launch_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._win(monkeypatch)
        monkeypatch.setattr(elevation, "_shell_execute_runas", lambda f, p: (0, 5))
        out = elevation.run_elevated_powershell("QUJD", timeout_s=1)
        assert out.result is ElevationResult.LAUNCH_FAILED

    def test_timeout_terminates_and_is_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._win(monkeypatch)
        monkeypatch.setattr(elevation, "_shell_execute_runas", lambda f, p: (1234, 0))
        monkeypatch.setattr(elevation, "_wait_for_process", lambda h, t: elevation._WAIT_TIMEOUT)
        terminated: list[int] = []
        closed: list[int] = []
        monkeypatch.setattr(elevation, "_terminate_process", lambda h: terminated.append(h))
        monkeypatch.setattr(elevation, "_close_handle", lambda h: closed.append(h))
        out = elevation.run_elevated_powershell("QUJD", timeout_s=1)
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
        out = elevation.run_elevated_powershell("QUJD", timeout_s=1)
        assert out.result is ElevationResult.COMPLETED
        assert out.exit_code == 7
        assert closed == [1234]

    def test_non_windows_is_launch_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(elevation.sys, "platform", "linux")
        out = elevation.run_elevated_powershell("QUJD", timeout_s=1)
        assert out.result is ElevationResult.LAUNCH_FAILED

    def test_pins_absolute_system32_powershell_and_encoded_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._win(monkeypatch)
        captured: dict[str, str] = {}

        def _fake(file: str, params: str) -> tuple[int, int]:
            captured["file"] = file
            captured["params"] = params
            return (0, 1223)

        monkeypatch.setattr(elevation, "_shell_execute_runas", _fake)
        elevation.run_elevated_powershell("ENCODEDBLOB", timeout_s=1)
        # Absolute System32 WindowsPowerShell path (never a bare "powershell" — PATH-hijack).
        assert "System32" in captured["file"]
        assert "WindowsPowerShell" in captured["file"]
        assert "powershell.exe" in captured["file"]
        assert captured["file"] != "powershell"
        assert "-EncodedCommand ENCODEDBLOB" in captured["params"]
        assert "-NoProfile" in captured["params"]


# ---------------------------------------------------------------------------
# windows.register_task — the self-elevated path (elevation mocked)
# ---------------------------------------------------------------------------


_SECRET = "P@ssw0rd-do-not-leak-42"


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

        def _run(enc: str, *, timeout_s: float) -> ElevationOutcome:
            captured["encoded"] = enc
            return ElevationOutcome(ElevationResult.COMPLETED, exit_code=0)

        monkeypatch.setattr("src.scheduler.elevation.write_request", _write_request)
        monkeypatch.setattr("src.scheduler.elevation.run_elevated_powershell", _run)
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: {"ok": True, "message": "Registered."})
        confirm = MagicMock(return_value=ScheduleReadback(found=True))
        monkeypatch.setattr("src.scheduler.windows.read_schedule", confirm)

        ok, _msg = self._register()

        assert ok is True
        confirm.assert_called_once()  # success is CONFIRMED via read-back, not assumed
        # The password rode the DPAPI payload (the sanctioned secure channel) ...
        assert captured["payload"]["DSYNC_TASK_PW"] == _SECRET  # type: ignore[index]
        # ... but NEVER the encoded bootstrap command the elevated child receives.
        decoded = base64.b64decode(str(captured["encoded"])).decode("utf-16-le")
        assert _SECRET not in decoded
        assert "DSYNC_DIFFERENT_ACCOUNT" in decoded  # fail-closed branch present
        # entropy is base64-embedded (uniform injection-proofing) — the raw string is NOT present.
        assert base64.b64encode(elevation.DPAPI_ENTROPY_UTF8.encode()).decode() in decoded
        assert elevation.DPAPI_ENTROPY_UTF8 not in decoded
        assert "CurrentUser" in decoded and "LocalMachine" not in decoded
        assert not req.exists()  # handshake file cleaned up in finally

    def test_no_result_confirms_via_readback_then_no_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "dsync_elev_abc.req"
        req.write_bytes(b"blob")
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        monkeypatch.setattr(
            "src.scheduler.elevation.run_elevated_powershell",
            lambda enc, *, timeout_s: ElevationOutcome(ElevationResult.COMPLETED, exit_code=0),
        )
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: None)  # child wrote nothing
        # No result → the flow STILL tries a read-back (the child may have registered); unknown → no-result.
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
        monkeypatch.setattr(
            "src.scheduler.elevation.run_elevated_powershell",
            lambda enc, *, timeout_s: ElevationOutcome(ElevationResult.COMPLETED, exit_code=0),
        )
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: None)
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=True))
        ok, _msg = self._register()
        assert ok is True  # the child crashed before writing a result, but the task IS registered

    def test_declined_maps_to_uac_declined(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)
        monkeypatch.setattr(
            "src.scheduler.elevation.run_elevated_powershell",
            lambda enc, *, timeout_s: ElevationOutcome(ElevationResult.DECLINED),
        )
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
        monkeypatch.setattr(
            "src.scheduler.elevation.run_elevated_powershell",
            lambda enc, *, timeout_s: ElevationOutcome(ElevationResult.TIMEOUT),
        )
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
        monkeypatch.setattr(
            "src.scheduler.elevation.run_elevated_powershell",
            lambda enc, *, timeout_s: ElevationOutcome(ElevationResult.TIMEOUT),
        )
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
        monkeypatch.setattr(
            "src.scheduler.elevation.run_elevated_powershell",
            lambda enc, *, timeout_s: ElevationOutcome(ElevationResult.COMPLETED, exit_code=0),
        )
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
        monkeypatch.setattr(
            "src.scheduler.elevation.run_elevated_powershell",
            lambda enc, *, timeout_s: ElevationOutcome(ElevationResult.COMPLETED, exit_code=1),
        )
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
        monkeypatch.setattr(
            "src.scheduler.elevation.run_elevated_powershell",
            lambda enc, *, timeout_s: ElevationOutcome(ElevationResult.COMPLETED, exit_code=1),
        )
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
        monkeypatch.setattr(
            "src.scheduler.elevation.run_elevated_powershell",
            lambda enc, *, timeout_s: ElevationOutcome(ElevationResult.COMPLETED, exit_code=0),
        )
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: {"ok": True})
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=None))
        ok, msg = self._register()
        assert ok is False  # honest: child said ok, but read-back couldn't confirm
        assert msg == windows._MSG_ELEVATION_NO_RESULT

    def test_password_never_in_any_subprocess_or_encoded_command(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_win_nonelevated(monkeypatch)
        req = tmp_path / "r.req"
        req.write_bytes(b"blob")
        captured: dict[str, str] = {}
        monkeypatch.setattr("src.scheduler.elevation.write_request", lambda payload: req)

        def _run(enc: str, *, timeout_s: float) -> ElevationOutcome:
            captured["encoded"] = enc
            return ElevationOutcome(ElevationResult.COMPLETED, exit_code=0)

        monkeypatch.setattr("src.scheduler.elevation.run_elevated_powershell", _run)
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: {"ok": True})

        # Let the REAL read_schedule run, but mock ITS subprocess so we can inspect every call.
        calls: list[tuple[list[str], dict]] = []

        def _fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return MagicMock(returncode=0, stdout="DSYNC_ABSENT", stderr="")

        monkeypatch.setattr("src.scheduler.windows.subprocess.run", _fake_run)

        self._register()

        # No subprocess call carried the secret (argv OR env), and DSYNC_TASK_PW is never
        # in any child env of a subprocess call.
        for argv, kwargs in calls:
            assert _SECRET not in " ".join(str(a) for a in argv)
            env = kwargs.get("env") or {}
            assert _SECRET not in "".join(str(v) for v in env.values())
            assert "DSYNC_TASK_PW" not in env
        # And not in the encoded bootstrap the elevated child receives.
        assert _SECRET not in base64.b64decode(captured["encoded"]).decode("utf-16-le")


# ---------------------------------------------------------------------------
# The fixed child-bootstrap script text (static assertions)
# ---------------------------------------------------------------------------


class TestElevatedRegisterScriptText:
    def _script(self) -> str:
        return windows._build_elevated_register_script(
            has_password=True,
            req_path=Path("C:/data/dsync_elev_aaa.req"),
            res_path=Path("C:/data/dsync_elev_aaa.res"),
        )

    def test_has_failclosed_currentuser_and_entropy(self) -> None:
        script = self._script()
        # Entropy is base64-embedded (uniform injection-proofing) — raw string absent, b64 present.
        assert base64.b64encode(elevation.DPAPI_ENTROPY_UTF8.encode()).decode() in script
        assert elevation.DPAPI_ENTROPY_UTF8 not in script
        assert "DSYNC_DIFFERENT_ACCOUNT" in script  # fail-closed cross-SID branch
        assert "CurrentUser" in script
        assert "LocalMachine" not in script  # NEVER widened to machine scope

    def test_never_echoes_password_in_result(self) -> None:
        script = self._script()
        # The password is referenced by Register-ScheduledTask -Password ...
        assert "$env:DSYNC_TASK_PW" in script
        # ... but never written to the result file.
        for line in script.splitlines():
            if "Write-DsyncResult" in line:
                assert "DSYNC_TASK_PW" not in line

    def test_paths_embedded_as_base64_not_interpolated(self) -> None:
        script = self._script()
        assert "dsync_elev_aaa.req" not in script  # base64-embedded, not raw-interpolated
        assert "FromBase64String" in script

    def test_result_write_is_atomic(self) -> None:
        script = self._script()
        assert "Move-Item" in script  # temp + rename atomic publish

    def test_register_body_is_single_sourced(self) -> None:
        # The elevated bootstrap runs the SAME register body as the direct script — no fork.
        assert windows._register_body(True) in self._script()

    def test_direct_script_unchanged_by_refactor(self) -> None:
        # The direct (already-elevated) script is still the fixed program the existing tests pin.
        direct = windows._build_register_script(has_password=True)
        assert direct.startswith("$ErrorActionPreference")
        assert "-LogonType Password" in direct
        assert direct.endswith("  exit 1\n}\n")
        assert windows._register_body(True) in direct


# ---------------------------------------------------------------------------
# windows.delete_task_elevated
# ---------------------------------------------------------------------------


class TestDeleteTaskElevated:
    def _mk(self, monkeypatch: pytest.MonkeyPatch, res: Path, outcome: ElevationOutcome, result: object) -> None:
        monkeypatch.setattr("src.scheduler.elevation.new_result_path", lambda: res)
        monkeypatch.setattr("src.scheduler.elevation.run_elevated_powershell", lambda enc, *, timeout_s: outcome)
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: result)

    def test_ok_confirmed_removed_via_readback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # The child's self-reported ok is NOT trusted alone — removal is confirmed by a
        # read-back that finds the task gone (found=False).
        self._mk(
            monkeypatch, tmp_path / "d.res", ElevationOutcome(ElevationResult.COMPLETED, exit_code=0), {"ok": True}
        )
        confirm = MagicMock(return_value=ScheduleReadback(found=False))
        monkeypatch.setattr("src.scheduler.windows.read_schedule", confirm)
        ok, _msg = windows.delete_task_elevated("DistrictSync_Daily")
        assert ok is True
        confirm.assert_called_once()

    def test_ok_but_still_present_is_unconfirmed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._mk(
            monkeypatch, tmp_path / "d.res", ElevationOutcome(ElevationResult.COMPLETED, exit_code=0), {"ok": True}
        )
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=True))
        ok, msg = windows.delete_task_elevated("DistrictSync_Daily")
        assert ok is False  # child said ok but the task is still there → don't assert removal
        assert msg == windows._MSG_ELEVATION_REMOVE_UNCONFIRMED

    def test_timeout_confirmed_removed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._mk(monkeypatch, tmp_path / "d.res", ElevationOutcome(ElevationResult.TIMEOUT), None)
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=False))
        ok, _msg = windows.delete_task_elevated("DistrictSync_Daily")
        assert ok is True  # timed out but the read-back confirms it's gone

    def test_no_result_unconfirmed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._mk(monkeypatch, tmp_path / "d.res", ElevationOutcome(ElevationResult.COMPLETED, exit_code=0), None)
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=None))
        ok, msg = windows.delete_task_elevated("DistrictSync_Daily")
        assert ok is False
        assert msg == windows._MSG_ELEVATION_REMOVE_UNCONFIRMED

    def test_declined(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._mk(monkeypatch, tmp_path / "d.res", ElevationOutcome(ElevationResult.DECLINED), None)
        confirm = MagicMock()
        monkeypatch.setattr("src.scheduler.windows.read_schedule", confirm)
        ok, msg = windows.delete_task_elevated("DistrictSync_Daily")
        assert ok is False
        assert msg == windows._MSG_UAC_DECLINED
        confirm.assert_not_called()  # a declined prompt is pre-consent — no read-back

    def test_delete_script_is_injection_free(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        res = tmp_path / "d.res"
        captured: dict[str, str] = {}
        monkeypatch.setattr("src.scheduler.elevation.new_result_path", lambda: res)

        def _run(enc: str, *, timeout_s: float) -> ElevationOutcome:
            captured["enc"] = enc
            return ElevationOutcome(ElevationResult.COMPLETED, exit_code=0)

        monkeypatch.setattr("src.scheduler.elevation.run_elevated_powershell", _run)
        monkeypatch.setattr("src.scheduler.elevation.read_result", lambda p: {"ok": True})
        monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=False))
        windows.delete_task_elevated("DistrictSync_Daily")
        decoded = base64.b64decode(captured["enc"]).decode("utf-16-le")
        assert "DistrictSync_Daily" not in decoded  # base64-embedded, never interpolated
        assert "Unregister-ScheduledTask" in decoded

    def test_invalid_task_name_rejected_before_elevation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        launched = MagicMock()
        monkeypatch.setattr("src.scheduler.elevation.run_elevated_powershell", launched)
        with pytest.raises(ValueError):
            windows.delete_task_elevated("bad;name|rm -rf")
        launched.assert_not_called()
