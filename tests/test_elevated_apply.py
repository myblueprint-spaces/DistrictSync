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
        assert out["message"] == elevated_apply._MSG_REQUEST_MISSING

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

    def test_a_refused_read_is_the_cross_account_signature_not_a_missing_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defect A8, reported by SD51 2026-09-16 (pre-fix).

        ``elevation.write_request`` seals the request under the SIGNED-IN user's profile with
        an owner-only DACL. When the UAC prompt is answered with a DIFFERENT administrator
        account, the elevated child's ``read_bytes()`` raises ``PermissionError`` — which the
        generic ``except OSError`` reported as "The elevated request was missing.", three rungs
        before the DPAPI cross-SID rung that was designed to say *different account*. The admin
        was then told to "try again in a moment", forever. ``PermissionError`` is an ``OSError``
        subclass, so the rung ORDER is the whole fix.
        """
        req, res = _sealed(tmp_path, None)
        real_read_bytes = Path.read_bytes

        def _refuse(self: Path, *args, **kwargs):
            if self == req:
                raise PermissionError(13, "Access is denied")
            return real_read_bytes(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", _refuse)
        code = elevated_apply.run_elevated_apply([str(req), str(res)])
        assert code == 0
        out = _read(res)
        assert out["ok"] is False
        assert out["message"] == elevated_apply.DIFFERENT_ACCOUNT_SENTINEL

    def test_a_genuinely_absent_request_still_reports_missing(self, tmp_path: Path) -> None:
        """The NEGATIVE twin of the rung above: a swept/never-written request is still
        'missing', so the new rung narrowed the generic arm rather than replacing it."""
        res = tmp_path / "gone.res"
        elevated_apply.run_elevated_apply([str(tmp_path / "never-written.req"), str(res)])
        assert _read(res)["message"] == elevated_apply._MSG_REQUEST_MISSING

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


class TestChildRefusalVocabulary:
    """``CHILD_REFUSALS`` is the child's OWN failure vocabulary — derived, never hand-listed.

    The classifier's produced-vs-classified sweep (A2) consumes this tuple, so a new refusal
    literal added without registering it must be red HERE, in the module that authored it.

    BOUND (Verify 2026-09-16, R7): the walk recognises only the shape
    ``_write_result(res_path, False, _MSG_NAME)`` — positional arguments with a bare
    ``_MSG_``-prefixed ``Name`` as the third. A refusal added as a keyword argument, an
    f-string, or via a local variable holding the constant is INVISIBLE to it. Every call site
    uses the bare-literal shape today, so the guard holds in practice; widening it past that
    blind spot is ROADMAP, not a pinned property.
    """

    @staticmethod
    def _refusal_names() -> set[str]:
        """Every ``_MSG_*`` name this module passes to ``_write_result(..., False, X)``.

        Deliberately excludes the three non-literal messages the child can write: the
        cross-SID ``DIFFERENT_ACCOUNT_SENTINEL`` (a sentinel the parent maps, not copy),
        ``task_com.MSG_COM_UNAVAILABLE`` (the ENGINE's canonical, named there) and
        ``exc.message`` (a ``task_com`` canonical passed straight through).
        """
        import ast
        import pathlib

        source = pathlib.Path(elevated_apply.__file__).read_text(encoding="utf-8")
        found: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_write_result"):
                continue
            args = node.args
            if len(args) < 3 or not (isinstance(args[1], ast.Constant) and args[1].value is False):
                continue
            if isinstance(args[2], ast.Name) and args[2].id.startswith("_MSG_"):
                found.add(args[2].id)
        return found

    def test_the_scan_finds_write_result_calls_at_all(self) -> None:
        """Not vacuous: an AST walk that matched nothing would make every row below pass."""
        assert len(self._refusal_names()) >= 3

    def test_child_refusals_is_exactly_the_written_literal_set(self) -> None:
        written = {getattr(elevated_apply, name) for name in self._refusal_names()}
        assert set(elevated_apply.CHILD_REFUSALS) == written

    def test_no_refusal_literal_carries_a_foreign_marker(self) -> None:
        """The child's copy reaches the same consumers the engine's canonicals do."""
        from src.scheduler.messages import carries_foreign_marker

        assert [m for m in elevated_apply.CHILD_REFUSALS if carries_foreign_marker(m)] == []


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


class TestPrincipalReValidation:
    """Plan 0046 A1 — the privileged half re-validates the field that names the PRINCIPAL.

    ``user`` used to be validated only ``if password is not None``, i.e. everywhere except
    the one case worth refusing. The parent can no longer send an unvalidated account, so
    this is a fail-closed floor rather than a second opinion — but a floor with a hole in
    it is not a floor, and this module's contract is that EVERY input is re-checked here.
    """

    def _run(self, tmp_path: Path, payload: dict):
        req, res = _sealed(tmp_path, None)
        raw = json.dumps(payload).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.task_com.register_task_definition") as reg,
        ):
            code = elevated_apply.run_elevated_apply([str(req), str(res)])
        return code, _read(res), reg

    def test_a_hostile_account_is_refused_without_a_password(self, tmp_path: Path) -> None:
        payload = _valid_register_payload(user="svc && calc", password=None)
        _code, out, reg = self._run(tmp_path, payload)
        assert out["ok"] is False
        assert "not valid" in out["message"]
        reg.assert_not_called()

    def test_a_hostile_account_is_refused_with_a_password(self, tmp_path: Path) -> None:
        """The positive twin of the branch above: both password states refuse."""
        payload = _valid_register_payload(user="svc && calc", password="pw")
        _code, out, reg = self._run(tmp_path, payload)
        assert out["ok"] is False
        reg.assert_not_called()

    def test_a_valid_account_still_registers_without_a_password(self, tmp_path: Path) -> None:
        """Not vacuous: the unconditional validation must not close the whole branch."""
        payload = _valid_register_payload(password=None)
        _code, out, reg = self._run(tmp_path, payload)
        assert out["ok"] is True
        assert reg.call_args[0][0].user == "CORP\\jane"

    def test_a_blank_password_never_becomes_an_unattended_registration(self, tmp_path: Path) -> None:
        """R2 in the privileged half: ``""`` is normalised to ``None`` here too, so the
        child cannot register TASK_LOGON_PASSWORD with a blank credential even if a
        request file says so."""
        payload = _valid_register_payload(password="")
        _code, out, reg = self._run(tmp_path, payload)
        assert out["ok"] is True
        params = reg.call_args[0][0]
        assert params.password is None
        service, folder = MagicMock(), MagicMock()
        task_com.apply_definition(service, folder, params)
        assert folder.RegisterTaskDefinition.call_args[0][5] == task_com.TASK_LOGON_INTERACTIVE_TOKEN


# The two secrets the non-leak table below chases through every outcome. Module-level so a
# RuntimeError message can interpolate them without a `self` in scope.
_SFTP_LEAK = "uniq-sftp-pw-QQ42"  # nosec B105 - a test fixture value, not a credential
_TASK_LEAK = "uniq-task-pw-ZZ91"  # nosec B105 - a test fixture value, not a credential


class TestMachineScopeOps:
    """The three S-1b-i ops (plan 0049). NOTHING in the app reaches them yet — S-2 wires
    Schedule-time dispatch — so these tests ARE the only caller until then.

    Every elevated path is driven through the seams, exactly as the register/delete rows
    above are: no UAC prompt, no registry write, no ``C:\\ProgramData`` touched.
    """

    _SFTP_SECRET = "uniq-sftp-pw-QQ42"  # nosec B105 - a test fixture value, not a credential
    _TASK_SECRET = "uniq-task-pw-ZZ91"  # nosec B105 - a test fixture value, not a credential

    def _provision_payload(self, **overrides):
        payload = _valid_register_payload(op="provision")
        payload["source_data_dir"] = r"C:\Users\jane\AppData\Local\DistrictSync"
        payload.update(overrides)
        return payload

    def test_provision_dispatches_to_the_engine_with_the_payload(self, tmp_path: Path) -> None:
        req, res = _sealed(tmp_path, None)
        raw = json.dumps(self._provision_payload()).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.provisioning.apply_provision") as engine,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
        assert _read(res) == {"ok": True, "message": ""}
        assert engine.call_args[0][0]["op"] == "provision"
        assert callable(engine.call_args[1]["register"])

    def test_the_injected_register_is_the_shared_do_register(self, tmp_path: Path) -> None:
        """Step 8 must go through the SAME re-validating path the plain op uses, so a bad
        password still surfaces as its ``task_com`` canonical rather than a step id."""
        req, res = _sealed(tmp_path, None)
        raw = json.dumps(self._provision_payload()).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.provisioning.apply_provision") as engine,
            patch("src.scheduler.task_com.register_task_definition") as reg,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
            engine.call_args[1]["register"]()
        params = reg.call_args[0][0]
        assert params.task_name == "DistrictSync_Daily"
        assert params.user == "CORP\\jane"

    def test_the_injected_register_re_validates_a_hostile_account(self, tmp_path: Path) -> None:
        """The positive twin of the delegation above: the floor is inherited, not bypassed."""
        req, res = _sealed(tmp_path, None)
        raw = json.dumps(self._provision_payload(user="svc && calc")).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.provisioning.apply_provision") as engine,
            patch("src.scheduler.task_com.register_task_definition") as reg,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
            with pytest.raises(ValueError):
                engine.call_args[1]["register"]()
            reg.assert_not_called()

    def test_missing_provision_fields_are_refused_before_the_engine(self, tmp_path: Path) -> None:
        req, res = _sealed(tmp_path, None)
        raw = json.dumps({"op": "provision", "task_name": "DistrictSync_Daily"}).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.provisioning.apply_provision") as engine,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
            engine.assert_not_called()
        out = _read(res)
        assert out["ok"] is False
        assert "not valid" in out["message"]

    @pytest.mark.parametrize(
        ("field", "value"),
        [("task_name", "evil;calc|name"), ("run_time", "25:99")],
    )
    def test_task_fields_are_validated_BEFORE_the_sequence_starts(self, tmp_path: Path, field: str, value: str) -> None:
        """Registration is step 8, i.e. AFTER the HKLM commit.

        Validating these only inside the injected callable would let a malformed payload
        permanently switch the install to machine scope and only then refuse — a
        provisioned computer with no nightly on it. The whole op must be a no-op instead.
        """
        req, res = _sealed(tmp_path, None)
        raw = json.dumps(self._provision_payload(**{field: value})).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.provisioning.apply_provision") as engine,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
            engine.assert_not_called()
        out = _read(res)
        assert out["ok"] is False
        assert "not valid" in out["message"]

    def test_a_refusal_surfaces_its_bounded_step_message(self, tmp_path: Path) -> None:
        from src.scheduler import provisioning

        req, res = _sealed(tmp_path, None)
        raw = json.dumps(self._provision_payload()).encode()
        refusal = provisioning.ProvisionRefused(provisioning.ProvisionStep.VERIFY)
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.provisioning.apply_provision", side_effect=refusal),
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
        out = _read(res)
        assert out["ok"] is False
        assert out["message"] == refusal.message
        assert "verify" in out["message"]

    @pytest.mark.parametrize(
        ("op", "target"),
        [
            ("grant_current_user", "apply_grant_current_user"),
            ("prune_principal", "apply_prune_principal"),
        ],
    )
    def test_grant_and_prune_dispatch(self, tmp_path: Path, op: str, target: str) -> None:
        req, res = _sealed(tmp_path, None)
        raw = json.dumps({"op": op, "task_name": "DistrictSync_Daily", "user": "CORP\\svc"}).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch(f"src.scheduler.provisioning.{target}") as engine,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
            engine.assert_called_once()
        assert _read(res)["ok"] is True

    def test_prune_re_validates_the_task_name_in_the_privileged_half(self, tmp_path: Path) -> None:
        req, res = _sealed(tmp_path, None)
        raw = json.dumps({"op": "prune_principal", "task_name": "evil;calc|name", "user": "CORP\\svc"}).encode()
        with (
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch("src.scheduler.provisioning.apply_prune_principal") as engine,
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
            engine.assert_not_called()
        assert _read(res)["ok"] is False

    def test_neither_secret_reaches_the_result_on_any_outcome(self, tmp_path: Path) -> None:
        """The non-leak table, extended to BOTH secrets: the delivery password the
        provision payload seeds AND the task password it registers with."""
        from src.scheduler import provisioning

        payload = self._provision_payload(
            password=self._TASK_SECRET,
            sftp_host="sftp.example.org",
            sftp_username="district",
            sftp_password=self._SFTP_SECRET,
        )
        raw = json.dumps(payload).encode()
        outcomes = (
            None,
            provisioning.ProvisionRefused(provisioning.ProvisionStep.SECRET),
            provisioning.ProvisionRefused(provisioning.ProvisionStep.GRANT, icacls_exit=1332),
            task_com.TaskComError(task_com.HR_LOGON_FAILURE, task_com.MSG_LOGON_FAILURE),
            RuntimeError(f"boom {_TASK_LEAK} {_SFTP_LEAK}"),
        )
        for outcome in outcomes:
            req, res = _sealed(tmp_path, None)
            with (
                patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
                patch("src.scheduler.provisioning.apply_provision", side_effect=outcome),
            ):
                elevated_apply.run_elevated_apply([str(req), str(res)])
            written = res.read_text(encoding="utf-8")
            assert self._TASK_SECRET not in written, f"task password leaked via {outcome!r}"
            assert self._SFTP_SECRET not in written, f"delivery password leaked via {outcome!r}"

    def test_neither_secret_reaches_a_log_record(self, tmp_path: Path, caplog) -> None:
        """This module has no logger by design; assert that rather than assume it."""
        import logging

        from src.scheduler import provisioning

        payload = self._provision_payload(
            password=self._TASK_SECRET,
            sftp_host="sftp.example.org",
            sftp_username="district",
            sftp_password=self._SFTP_SECRET,
        )
        raw = json.dumps(payload).encode()
        req, res = _sealed(tmp_path, None)
        with (
            caplog.at_level(logging.DEBUG),
            patch("src.scheduler.elevation.unprotect_blob", return_value=raw),
            patch(
                "src.scheduler.provisioning.apply_provision",
                side_effect=provisioning.ProvisionRefused(provisioning.ProvisionStep.SECRET),
            ),
        ):
            elevated_apply.run_elevated_apply([str(req), str(res)])
        emitted = "\n".join(record.getMessage() for record in caplog.records)
        assert self._TASK_SECRET not in emitted
        assert self._SFTP_SECRET not in emitted

    def test_the_new_ops_are_absent_from_help(self, capsys) -> None:
        """An IPC mode with no human caller must not advertise itself."""
        import src.main as main_mod

        main_mod.cli(["--help"])
        out = capsys.readouterr().out
        assert "usage" in out.lower()  # help really printed (positive twin)
        for op in ("--provision", "grant_current_user", "prune_principal"):
            assert op not in out
