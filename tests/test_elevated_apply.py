"""``src/scheduler/elevated_apply.py`` — the elevated child's fail-closed ladder (0041 S1b).

The child is the PRIVILEGED half of the schedule handshake, so its refusal table is the
security surface: every malformed/hostile input must produce a written refusal result
(never a traceback, never a partial write, never an action), and only a valid same-SID
DPAPI request may reach a ``task_com`` call. DPAPI itself is mocked at the
``elevation.unprotect_blob`` seam so the table runs on every OS.

The DISPATCH-FIRST pin is here too: ``--elevated-apply`` must be recognised above
``main._cli``'s preamble, because that preamble performs best-effort filesystem work
(profile migration, log-sink creation, orphan sweeps) that an ELEVATED process must not
touch — the Round-1 security blocker of plan 0041.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.scheduler import elevated_apply, task_com


def _read(res: Path) -> dict:
    return json.loads(res.read_text(encoding="utf-8"))


def _valid_register_payload(**overrides) -> dict:
    payload = {
        "op": "register",
        "task_name": "DistrictSync_Daily",
        "exe": r"C:\DistrictSync\DistrictSync.exe",
        "arguments": "--sis myedbc --source scheduled",
        "working_dir": r"C:\DistrictSync",
        "run_time": "03:00",
        "user": "CORP\\jane",
        "password": "pw",
        "run_highest": True,
    }
    payload.update(overrides)
    return payload


def _sealed(tmp_path: Path, payload: object) -> tuple[Path, Path]:
    """Write a fake 'sealed' request; the unprotect seam is mocked to return it."""
    req = tmp_path / "dsync_elev_x.req"
    req.write_bytes(b"sealed-bytes")
    res = tmp_path / "dsync_elev_x.res"
    return req, res


class TestRefusalLadder:
    def test_wrong_argc_exits_2_and_writes_nothing(self, tmp_path: Path) -> None:
        assert elevated_apply.run_elevated_apply([]) == 2
        assert elevated_apply.run_elevated_apply(["only-one"]) == 2
        assert elevated_apply.run_elevated_apply(["a", "b", "c"]) == 2
        assert list(tmp_path.iterdir()) == []

    def test_missing_request_writes_refusal(self, tmp_path: Path) -> None:
        res = tmp_path / "r.res"
        code = elevated_apply.run_elevated_apply([str(tmp_path / "absent.req"), str(res)])
        assert code == 0
        out = _read(res)
        assert out["ok"] is False
        assert "missing" in out["message"].lower()

    def test_oversized_request_is_refused_unread(self, tmp_path: Path) -> None:
        req = tmp_path / "big.req"
        req.write_bytes(b"x" * (elevated_apply._MAX_REQUEST_BYTES + 1))
        res = tmp_path / "r.res"
        with patch("src.scheduler.elevation.unprotect_blob") as unseal:
            code = elevated_apply.run_elevated_apply([str(req), str(res)])
            unseal.assert_not_called()
        assert code == 0
        assert _read(res)["ok"] is False

    def test_dpapi_failure_writes_the_different_account_sentinel(self, tmp_path: Path) -> None:
        """A cross-SID consent (another admin clicked Yes) must FAIL CLOSED with the
        sentinel the parent maps to the canonical different-account message."""
        req, res = _sealed(tmp_path, None)
        with patch("src.scheduler.elevation.unprotect_blob", side_effect=OSError("SID mismatch")):
            code = elevated_apply.run_elevated_apply([str(req), str(res)])
        assert code == 0
        out = _read(res)
        assert out["ok"] is False
        assert out["message"] == elevated_apply.DIFFERENT_ACCOUNT_SENTINEL

    @pytest.mark.parametrize("raw", [b"not json", b'"a string"', b"[1,2]"])
    def test_malformed_payload_is_refused(self, tmp_path: Path, raw: bytes) -> None:
        req, res = _sealed(tmp_path, None)
        with patch("src.scheduler.elevation.unprotect_blob", return_value=raw):
            elevated_apply.run_elevated_apply([str(req), str(res)])
        assert _read(res)["ok"] is False

    def test_unknown_op_is_refused(self, tmp_path: Path) -> None:
        req, res = _sealed(tmp_path, None)
        raw = json.dumps({"op": "format-c"}).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.task_com.register_task_definition") as reg,
            patch("src.scheduler.task_com.delete_task_by_name") as dele,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
            reg.assert_not_called()
            dele.assert_not_called()
        assert _read(res)["ok"] is False

    def test_missing_register_fields_are_refused_before_any_com_call(self, tmp_path: Path) -> None:
        req, res = _sealed(tmp_path, None)
        raw = json.dumps({"op": "register", "task_name": "DistrictSync_Daily"}).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.task_com.register_task_definition") as reg,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
            reg.assert_not_called()
        out = _read(res)
        assert out["ok"] is False
        assert "not valid" in out["message"]

    def test_hostile_task_name_is_re_validated_in_the_child(self, tmp_path: Path) -> None:
        """The child re-validates EVERYTHING: a request file is attacker-influencable in
        ways the parent's argv is not, and this is the privileged half."""
        req, res = _sealed(tmp_path, None)
        raw = json.dumps(_valid_register_payload(task_name="evil;calc|name")).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.task_com.register_task_definition") as reg,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
            reg.assert_not_called()
        assert _read(res)["ok"] is False

    def test_the_floor_writes_a_refusal_instead_of_a_traceback(self, tmp_path: Path) -> None:
        req, res = _sealed(tmp_path, None)
        with patch("src.scheduler.elevation.unprotect_blob", side_effect=RuntimeError("boom")):
            code = elevated_apply.run_elevated_apply([str(req), str(res)])
        assert code == 0
        out = _read(res)
        assert out["ok"] is False
        assert "boom" not in out["message"]  # no raw internals in an admin-facing message


class TestSuccessPaths:
    def test_register_dispatches_to_the_shared_task_com_function(self, tmp_path: Path) -> None:
        req, res = _sealed(tmp_path, None)
        raw = json.dumps(_valid_register_payload()).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.task_com.register_task_definition") as reg,
        ):
            code = elevated_apply.run_elevated_apply([str(req), str(res)])
        assert code == 0
        assert _read(res) == {"ok": True, "message": ""}
        params = reg.call_args[0][0]
        assert params.task_name == "DistrictSync_Daily"
        assert params.password == "pw"
        assert params.run_highest is True

    def test_delete_dispatches_to_the_shared_task_com_function(self, tmp_path: Path) -> None:
        req, res = _sealed(tmp_path, None)
        raw = json.dumps({"op": "delete", "task_name": "DistrictSync_Daily"}).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.task_com.delete_task_by_name") as dele,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
            dele.assert_called_once_with("DistrictSync_Daily")
        assert _read(res)["ok"] is True

    def test_a_task_com_failure_surfaces_its_canonical_message(self, tmp_path: Path) -> None:
        req, res = _sealed(tmp_path, None)
        raw = json.dumps(_valid_register_payload()).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch(
                "src.scheduler.task_com.register_task_definition",
                side_effect=task_com.TaskComError(task_com.HR_LOGON_FAILURE, "The user name or password is incorrect."),
            ),
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
        out = _read(res)
        assert out["ok"] is False
        assert out["message"] == "The user name or password is incorrect."

    def test_result_write_is_atomic_no_tmp_survives(self, tmp_path: Path) -> None:
        req, res = _sealed(tmp_path, None)
        raw = json.dumps({"op": "delete", "task_name": "DistrictSync_Daily"}).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.task_com.delete_task_by_name"),
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
        assert res.exists()
        assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())

    def test_the_result_never_carries_the_password(self, tmp_path: Path) -> None:
        secret = "uniq-child-pw-XYZZY"
        req, res = _sealed(tmp_path, None)
        raw = json.dumps(_valid_register_payload(password=secret)).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.task_com.register_task_definition"),
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
        assert secret not in res.read_text(encoding="utf-8")


class TestDispatchFirst:
    """The Round-1 security blocker: the child must run NONE of the CLI preamble."""

    def test_elevated_apply_dispatches_above_the_preamble(self, monkeypatch, tmp_path: Path) -> None:
        import src.main as main_mod

        migrate = MagicMock()
        attach = MagicMock()
        sweep = MagicMock()
        monkeypatch.setattr(main_mod, "migrate_legacy_data_dir", migrate)
        monkeypatch.setattr(main_mod, "_attach_parent_console", attach)
        monkeypatch.setattr("src.scheduler.elevation.sweep_orphans", sweep)

        res = tmp_path / "r.res"
        code = main_mod.cli(["--elevated-apply", str(tmp_path / "absent.req"), str(res)])

        assert code == 0
        assert res.exists()  # the child ran (refusal result for the absent request)
        migrate.assert_not_called()  # NO legacy-profile migration under an elevated token
        attach.assert_not_called()  # NO console attach
        sweep.assert_not_called()  # NO orphan sweep

    def test_the_mode_is_absent_from_help(self, capsys) -> None:
        """An IPC mode with no human caller must not advertise itself.

        ``cli`` converts argparse's ``SystemExit`` to a return code, so assert on that.
        """
        import src.main as main_mod

        code = main_mod.cli(["--help"])
        out = capsys.readouterr().out
        assert code == 0
        assert "usage" in out.lower()  # help really printed (positive twin)
        assert "--elevated-apply" not in out
