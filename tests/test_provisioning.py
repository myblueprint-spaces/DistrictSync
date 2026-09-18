"""``src/scheduler/provisioning.py`` — the elevated provisioning engine (plan 0049 S-1b-i).

This is the most privileged code in the app: it runs behind a UAC prompt, creates a
directory every account on the computer can reach, seals a delivery password into it and
writes an HKLM switch that permanently re-points where DistrictSync reads its settings
from. The tests below are the refusal table for that sequence.

Three properties carry the weight, and each has a positive twin so a green cannot come
from a gate that quietly stopped firing:

* **Never adopt.** ``CreateDirectoryW``'s ``ERROR_ALREADY_EXISTS`` is the atomic gate —
  anything already at the path routes to a refusal, never to an adopt-and-fix path.
* **Never commit against a directory the app would refuse.** Step 6 verifies with the
  app's OWN trust predicate + the open-group ACE walk, never with an ``icacls`` exit code.
* **Never strand.** A failure before the commit removes what it created; a removal failure
  gets its own step id. A directory that IS trusted but has no committed switch is the
  RESUME case, not a wedge.

Every Windows API is driven through a named seam (``_create_directory_with_sddl``,
``_account_sid``, ``_run_icacls``, ``_commit_machine_switch``), so the whole decision table
runs on every OS; the seams themselves have real-API tests where the API exists.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from src.scheduler import provisioning
from src.utils import paths as paths_module

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Win32 security APIs are Windows-only")

Step = provisioning.ProvisionStep

_SETUP_SID = "S-1-5-21-1-2-3-1001"
_PRINCIPAL_SID = "S-1-5-21-1-2-3-1002"


# --------------------------------------------------------------------------- #
# Fixtures — every Win32 seam replaced, so the sequence runs on any OS.        #
# --------------------------------------------------------------------------- #


@pytest.fixture
def machine_root(tmp_path, monkeypatch):
    """Redirect ``machine_data_dir()`` into tmp. NEVER the real ``C:\\ProgramData``."""
    target = tmp_path / "ProgramData" / "DistrictSync"
    target.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths_module, "machine_data_dir", lambda: target)
    return target


class _Rig:
    """Records what each seam was asked to do, so a test can assert the sequence."""

    def __init__(self) -> None:
        self.sddl: dict[str, str] = {}
        self.icacls: list[list[str]] = []
        self.icacls_exit = 0
        self.committed: list[tuple[str, str]] = []
        self.registered = 0
        self.verify_raises: BaseException | None = None
        self.create_raises: BaseException | None = None
        self.commit_raises: BaseException | None = None
        self.deleted: list[str] = []
        self.delete_confirms = True


@pytest.fixture
def rig(monkeypatch, machine_root):
    r = _Rig()

    def _create(path: Path, sddl: str) -> None:
        if r.create_raises is not None:
            raise r.create_raises
        if path.exists():
            raise FileExistsError(f"{path} already exists")
        path.mkdir(parents=True)
        r.sddl[path.name] = sddl

    def _icacls(args: list[str]) -> int:
        r.icacls.append(args)
        return r.icacls_exit

    def _sid(name: str) -> str:
        if name.lower() in {"corp\\jane", "jane"}:
            return _SETUP_SID
        if name.lower() in {"corp\\svc", "svc"}:
            return _PRINCIPAL_SID
        # Windows resolves these perfectly well, so the rig must too — otherwise the
        # unprunable-principal fence is never what refuses them and a probe that deletes
        # the fence stays green (found by falsification, 2026-09-17).
        if name.lower() in {"administrators", "builtin\\administrators"}:
            return "S-1-5-32-544"
        if name.lower() in {"system", "nt authority\\system"}:
            return "S-1-5-18"
        raise LookupError(name)

    def _verify(path: Path) -> None:
        if r.verify_raises is not None:
            raise r.verify_raises

    def _commit(provisioned_by: str) -> None:
        if r.commit_raises is not None:
            raise r.commit_raises
        r.committed.append(("MachineScope", provisioned_by))

    def _delete(task_name: str) -> bool:
        r.deleted.append(task_name)
        return r.delete_confirms

    monkeypatch.setattr(provisioning, "_create_directory_with_sddl", _create)
    monkeypatch.setattr(provisioning, "_run_icacls", _icacls)
    monkeypatch.setattr(provisioning, "_account_sid", _sid)
    monkeypatch.setattr(provisioning, "_commit_machine_switch", _commit)
    monkeypatch.setattr(provisioning, "_delete_task_confirmed_missing", _delete)
    monkeypatch.setattr(provisioning, "current_run_as_user", lambda: "CORP\\jane")
    monkeypatch.setattr(paths_module, "assert_machine_dir_trusted", _verify)
    monkeypatch.setattr(paths_module, "assert_no_open_aces", _verify)
    monkeypatch.setattr(paths_module, "_machine_switch_on", lambda: False)
    return r


@pytest.fixture
def source_profile(tmp_path, monkeypatch):
    """A per-user profile with something in it, wired as the resolver's answer."""
    source = tmp_path / "user_profile"
    source.mkdir()
    (source / "config.json").write_text(json.dumps({"sis_type": "sd74myedbc"}), encoding="utf-8")
    monkeypatch.setattr(paths_module, "per_user_data_dir", lambda: source)
    return source


def _payload(source: Path, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "op": "provision",
        "source_data_dir": str(source),
        "task_name": "DistrictSync_Daily",
        "exe": r"C:\DistrictSync\DistrictSync.exe",
        "arguments": "--sis myedbc --source scheduled",
        "working_dir": r"C:\DistrictSync",
        "run_time": "03:00",
        "user": "CORP\\svc",
        "run_highest": True,
    }
    payload.update(overrides)
    return payload


def _run(payload: dict[str, Any], rig: _Rig) -> None:
    def _register() -> None:
        rig.registered += 1

    provisioning.apply_provision(payload, register=_register)


# --------------------------------------------------------------------------- #
# Step 1 — the override, in BOTH halves.                                       #
# --------------------------------------------------------------------------- #


class TestOverrideIsRefusedInBothHalves:
    """``DISTRICTSYNC_DATA_DIR`` is mandated for every local, CI and QA run.

    A UAC-launched child gets an environment rebuilt from the consenting token, so
    without this refusal the owner's own fresh-profile QA walk would permanently switch
    their laptop to machine scope and copy their real ``config.json`` into ProgramData.
    """

    def test_the_child_refuses(self, rig, source_profile, monkeypatch, tmp_path):
        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path / "throwaway"))
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.step is Step.OVERRIDE
        assert rig.committed == []
        assert rig.sddl == {}

    def test_the_parent_refuses(self, monkeypatch, tmp_path, source_profile):
        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path / "throwaway"))
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            provisioning.build_provision_payload(
                task_name="DistrictSync_Daily",
                exe=r"C:\DistrictSync\DistrictSync.exe",
                arguments="--source scheduled",
                working_dir=r"C:\DistrictSync",
                run_time="03:00",
                user="CORP\\svc",
                run_highest=True,
            )
        assert exc.value.step is Step.OVERRIDE

    def test_without_the_override_the_parent_stamps_its_own_source(self, source_profile):
        payload = provisioning.build_provision_payload(
            task_name="DistrictSync_Daily",
            exe=r"C:\DistrictSync\DistrictSync.exe",
            arguments="--source scheduled",
            working_dir=r"C:\DistrictSync",
            run_time="03:00",
            user="CORP\\svc",
            run_highest=True,
        )
        assert payload["op"] == "provision"
        assert payload["source_data_dir"] == str(source_profile)


class TestSourcePathMustAgree:
    def test_a_payload_naming_another_directory_is_refused(self, rig, source_profile, tmp_path):
        payload = _payload(source_profile, source_data_dir=str(tmp_path / "somewhere-else"))
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(payload, rig)
        assert exc.value.step is Step.SOURCE
        assert rig.committed == []

    def test_the_agreeing_payload_proceeds(self, rig, source_profile):
        _run(_payload(source_profile), rig)
        assert rig.committed  # positive twin: the check is not refusing everything


# --------------------------------------------------------------------------- #
# Step 2/3 — creation is the atomic gate; never adopt.                         #
# --------------------------------------------------------------------------- #


class TestNeverAdopt:
    def test_a_pre_existing_untrusted_directory_is_refused(self, rig, source_profile, machine_root):
        machine_root.mkdir(parents=True)
        rig.verify_raises = paths_module.MachineScopeRefused(
            paths_module.MachineScopeRefusedReason.FOREIGN_OWNER, machine_root
        )
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.step is Step.PRE_EXISTING
        assert rig.committed == []
        assert rig.icacls == []  # nothing was "fixed up" on the way to refusing

    def test_error_already_exists_routes_to_the_refusal_not_to_adoption(self, rig, source_profile, machine_root):
        """The TOCTOU plant: the path is absent at step 2 and present at step 3."""
        rig.create_raises = FileExistsError("ERROR_ALREADY_EXISTS")
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.step is Step.PRE_EXISTING
        assert rig.committed == []

    def test_a_refusal_never_removes_a_directory_it_did_not_create(self, rig, source_profile, machine_root):
        machine_root.mkdir(parents=True)
        (machine_root / "someone-elses.txt").write_text("x", encoding="utf-8")
        rig.verify_raises = paths_module.MachineScopeRefused(
            paths_module.MachineScopeRefusedReason.REPARSE, machine_root
        )
        with pytest.raises(provisioning.ProvisionRefused):
            _run(_payload(source_profile), rig)
        assert (machine_root / "someone-elses.txt").exists()


class TestCreateWithSddl:
    """The positive twin for create-with-SDDL: no separate DACL step exists at all."""

    def test_the_created_directory_is_verified_with_no_icacls_call_in_between(self, rig, source_profile):
        _run(_payload(source_profile), rig)
        assert rig.icacls == [], "a fresh provision applies its DACL AT CREATION, never afterwards"
        assert set(rig.sddl) == {"DistrictSync", "runs"}

    def test_the_root_descriptor_names_only_sid_strings(self, rig, source_profile):
        _run(_payload(source_profile), rig)
        sddl = rig.sddl["DistrictSync"]
        assert sddl.startswith("O:BAG:BAD:P(")
        assert _SETUP_SID in sddl and _PRINCIPAL_SID in sddl
        # A localised group name in an SDDL fails on a non-English Windows.
        for localised in ("Administrators", "SYSTEM", "Users", "Everyone"):
            assert localised not in sddl

    def test_the_principal_gets_rx_at_the_root_and_modify_on_runs(self, rig, source_profile):
        _run(_payload(source_profile), rig)
        assert f"(A;OICI;{provisioning.READ_EXECUTE};;;{_PRINCIPAL_SID})" in rig.sddl["DistrictSync"]
        assert f"(A;OICI;{provisioning.MODIFY};;;{_PRINCIPAL_SID})" in rig.sddl["runs"]
        assert f"(A;OICI;{provisioning.READ_EXECUTE};;;{_PRINCIPAL_SID})" not in rig.sddl["runs"]

    def test_no_open_group_reaches_either_descriptor(self, rig, source_profile):
        _run(_payload(source_profile), rig)
        for sddl in rig.sddl.values():
            for open_sid in paths_module.OPEN_GROUP_SIDS:
                assert f";{open_sid})" not in sddl
            assert ";;;WD)" not in sddl and ";;;AU)" not in sddl and ";;;BU)" not in sddl

    def test_an_unresolvable_principal_refuses_before_anything_is_created(self, rig, source_profile):
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile, user="CORP\\nobody"), rig)
        assert exc.value.step is Step.PRINCIPAL
        assert rig.sddl == {}

    def test_a_principal_that_is_the_setup_user_is_not_double_aced(self, rig, source_profile):
        _run(_payload(source_profile, user="CORP\\jane"), rig)
        assert rig.sddl["DistrictSync"].count(_SETUP_SID) == 1


# --------------------------------------------------------------------------- #
# Step 6 — verify with our own predicate, never with an exit code.             #
# --------------------------------------------------------------------------- #


class TestVerifyBeforeCommit:
    def test_a_tampered_dacl_is_rejected_before_the_switch_is_written(
        self, rig, source_profile, machine_root, monkeypatch
    ):
        calls: list[Path] = []
        created: list[Path] = []
        real_walk = paths_module.assert_no_open_aces

        def _walk(path: Path) -> None:
            calls.append(path)
            if created:  # the FRESH directory, i.e. the post-create verify
                raise paths_module.MachineScopeRefused(paths_module.MachineScopeRefusedReason.OPEN_ACE, path)

        def _create(path: Path, sddl: str) -> None:
            path.mkdir(parents=True)
            created.append(path)

        monkeypatch.setattr(provisioning, "_create_directory_with_sddl", _create)
        monkeypatch.setattr(paths_module, "assert_no_open_aces", _walk)
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.step is Step.VERIFY
        assert rig.committed == []
        assert calls, "the walk must actually have been called"
        assert real_walk is not _walk  # the seam exists to be replaced, not invented here

    def test_a_history_db_in_the_wrong_place_fails_the_verify(self, rig, source_profile, machine_root, monkeypatch):
        (source_profile / "history.db").write_bytes(b"")
        monkeypatch.setattr(provisioning, "migrate_profile", lambda source, destination: None)
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.step is Step.VERIFY
        assert rig.committed == []

    def test_the_verify_passes_when_the_run_store_landed_under_runs(self, rig, source_profile, machine_root):
        conn = _seed_run_store(source_profile / "history.db", rows=2)
        try:
            _run(_payload(source_profile), rig)
        finally:
            conn.close()
        assert (machine_root / "runs" / "history.db").exists()
        assert rig.committed


# --------------------------------------------------------------------------- #
# Rollback + resume.                                                           #
# --------------------------------------------------------------------------- #


class TestRollback:
    def test_a_pre_commit_failure_removes_the_directory_it_created(self, rig, source_profile, machine_root):
        rig.commit_raises = None
        rig.verify_raises = paths_module.MachineScopeRefused(
            paths_module.MachineScopeRefusedReason.INHERITED_ACL, machine_root
        )
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.step is Step.VERIFY
        assert not machine_root.exists()

    def test_a_RESUMED_provision_never_removes_the_directory_on_failure(
        self, rig, source_profile, machine_root, monkeypatch
    ):
        """The dangerous half of rollback: on a resume the directory is NOT ours to delete.

        It already holds the admin's migrated settings and, after a kill between the secret
        write and the commit, a sealed delivery password. Removing it would destroy exactly
        the state the resume rule exists to preserve — and the ``if created`` guard is the
        only thing standing between the two cases. (Found by falsification: deleting that
        guard left the suite green, because every rollback test until now refused at step 2,
        before the try block was even entered.)
        """
        machine_root.mkdir(parents=True)
        (machine_root / "runs").mkdir()
        (machine_root / "sftp_secret.bin").write_bytes(b"sealed")
        calls: list[Path] = []

        def _fail_verify(path: Path) -> None:
            calls.append(path)
            raise paths_module.MachineScopeRefused(paths_module.MachineScopeRefusedReason.OPEN_ACE, path)

        # Trusted at step 2 (so we RESUME), then failing at step 6.
        def _trusted_then_broken(path: Path) -> None:
            if calls:
                _fail_verify(path)
            calls.append(path)

        monkeypatch.setattr(paths_module, "assert_machine_dir_trusted", _trusted_then_broken)
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.step is Step.VERIFY
        assert machine_root.exists(), "a resume must never delete a directory it did not create"
        assert (machine_root / "sftp_secret.bin").exists()
        assert rig.committed == []

    def test_a_removal_failure_gets_its_own_step_id(self, rig, source_profile, machine_root, monkeypatch):
        rig.verify_raises = paths_module.MachineScopeRefused(
            paths_module.MachineScopeRefusedReason.INHERITED_ACL, machine_root
        )

        real_rmtree = provisioning.shutil.rmtree

        def _boom(path, *args, **kwargs):
            # ONLY the rollback's removal fails — the migration's own staging cleanup uses
            # the same function, and breaking that too would fail this test one step early.
            if Path(path) == machine_root:
                raise OSError("the directory is in use")
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(provisioning.shutil, "rmtree", _boom)
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.rollback_failed is True
        assert Step.ROLLBACK.value in exc.value.message
        assert Step.VERIFY.value in exc.value.message  # the original step is still named

    def test_a_failure_AFTER_the_commit_leaves_the_directory_and_keeps_the_canonical(
        self, rig, source_profile, machine_root
    ):
        """Step 8 is NOT wrapped in a step id.

        The registration goes through ``elevated_apply._do_register``, whose ``task_com``
        canonical ("The user name or password is incorrect.") is what the admin needs and
        what ``setup_errors`` classifies by EXACT equality. Flattening it to
        ``(step: register)`` would destroy the one message with a real answer in it — and
        the switch is already committed, so there is nothing left to roll back.
        """
        from src.scheduler import task_com

        def _register() -> None:
            raise task_com.TaskComError(task_com.HR_LOGON_FAILURE, task_com.MSG_LOGON_FAILURE)

        with pytest.raises(task_com.TaskComError) as exc:
            provisioning.apply_provision(_payload(source_profile), register=_register)
        assert exc.value.message == task_com.MSG_LOGON_FAILURE
        assert machine_root.exists()  # the switch is committed; the directory is valid
        assert rig.committed


class TestResume:
    """A kill between the secret write and the commit must not strand the install."""

    def test_trusted_and_switch_off_re_runs_and_commits(self, rig, source_profile, machine_root):
        machine_root.mkdir(parents=True)
        (machine_root / "runs").mkdir()
        _run(_payload(source_profile), rig)
        assert rig.sddl == {}, "a resume never re-creates the directory"
        assert rig.committed
        assert rig.registered == 1

    def test_a_resume_applies_the_principal_aces_with_icacls(self, rig, source_profile, machine_root):
        machine_root.mkdir(parents=True)
        (machine_root / "runs").mkdir()
        _run(_payload(source_profile), rig)
        joined = [" ".join(a) for a in rig.icacls]
        assert any("CORP\\svc" in line and str(machine_root) in line for line in joined)
        assert any("CORP\\svc" in line and str(machine_root / "runs") in line for line in joined)

    def test_a_resume_whose_principal_IS_the_setup_user_grants_nothing_extra(self, rig, source_profile, machine_root):
        """The fresh descriptor omits the duplicate ace, so the resume must too — otherwise
        two installs of the same shape read back differently through ``icacls``."""
        machine_root.mkdir(parents=True)
        (machine_root / "runs").mkdir()
        _run(_payload(source_profile, user="CORP\\jane"), rig)
        assert rig.icacls == []
        assert rig.committed  # positive twin: the resume still completed

    def test_a_resume_whose_icacls_fails_is_FATAL(self, rig, source_profile, machine_root):
        machine_root.mkdir(parents=True)
        rig.icacls_exit = 5
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.step is Step.PRINCIPAL
        assert exc.value.icacls_exit == 5
        assert "5" in exc.value.message
        assert rig.committed == []

    def test_a_resume_never_clobbers_what_is_already_there(self, rig, source_profile, machine_root):
        machine_root.mkdir(parents=True)
        (machine_root / "runs").mkdir()
        (machine_root / "config.json").write_text(json.dumps({"sis_type": "sd83myedbc"}), encoding="utf-8")
        _run(_payload(source_profile), rig)
        kept = json.loads((machine_root / "config.json").read_text(encoding="utf-8"))
        assert kept["sis_type"] == "sd83myedbc", "a stale per-user copy must not overwrite the shared one"

    @pytest.mark.parametrize(
        "reason",
        [
            paths_module.MachineScopeRefusedReason.FOREIGN_OWNER,
            paths_module.MachineScopeRefusedReason.INHERITED_ACL,
            paths_module.MachineScopeRefusedReason.REPARSE,
            paths_module.MachineScopeRefusedReason.NOT_A_DIRECTORY,
            paths_module.MachineScopeRefusedReason.INACCESSIBLE,
            paths_module.MachineScopeRefusedReason.OPEN_ACE,
        ],
    )
    def test_every_failing_predicate_case_stays_a_hard_refusal(self, rig, source_profile, machine_root, reason):
        machine_root.mkdir(parents=True)
        rig.verify_raises = paths_module.MachineScopeRefused(reason, machine_root)
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.step is Step.PRE_EXISTING
        assert rig.committed == []


# --------------------------------------------------------------------------- #
# Step 7 — the commit uses the exported 64-bit access mask.                    #
# --------------------------------------------------------------------------- #


class TestCommit:
    def test_the_commit_is_the_last_thing_before_the_registration(self, rig, source_profile, monkeypatch):
        order: list[str] = []
        staged = provisioning._commit_machine_switch

        def _spy(provisioned_by: str) -> None:
            order.append("commit")
            staged(provisioned_by)

        monkeypatch.setattr(provisioning, "_commit_machine_switch", _spy)
        provisioning.apply_provision(_payload(source_profile), register=lambda: order.append("register"))
        assert order == ["commit", "register"]

    def test_a_commit_failure_reports_the_commit_step(self, rig, source_profile, machine_root):
        rig.commit_raises = OSError("HKLM is read-only")
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert exc.value.step is Step.COMMIT
        assert not machine_root.exists()  # a failed commit still rolls back


# --------------------------------------------------------------------------- #
# grant_current_user / prune_principal.                                        #
# --------------------------------------------------------------------------- #


class TestGrantCurrentUser:
    def test_the_grantee_comes_from_the_childs_own_token(self, rig, machine_root):
        machine_root.mkdir(parents=True)
        provisioning.apply_grant_current_user({"op": "grant_current_user"})
        joined = " ".join(" ".join(a) for a in rig.icacls)
        assert "CORP\\jane" in joined

    def test_a_payload_named_account_gets_no_ace(self, rig, machine_root):
        machine_root.mkdir(parents=True)
        provisioning.apply_grant_current_user({"op": "grant_current_user", "user": "CORP\\attacker"})
        joined = " ".join(" ".join(a) for a in rig.icacls)
        assert "attacker" not in joined
        assert "CORP\\jane" in joined  # positive twin: an ACE WAS granted

    def test_a_non_zero_icacls_exit_is_fatal(self, rig, machine_root):
        machine_root.mkdir(parents=True)
        rig.icacls_exit = 1332
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            provisioning.apply_grant_current_user({"op": "grant_current_user"})
        assert exc.value.step is Step.GRANT
        assert exc.value.icacls_exit == 1332

    def test_an_untrusted_directory_is_never_granted_on(self, rig, machine_root):
        machine_root.mkdir(parents=True)
        rig.verify_raises = paths_module.MachineScopeRefused(
            paths_module.MachineScopeRefusedReason.FOREIGN_OWNER, machine_root
        )
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            provisioning.apply_grant_current_user({"op": "grant_current_user"})
        assert exc.value.step is Step.PRE_EXISTING
        assert rig.icacls == []

    def test_the_override_refuses_the_grant_too(self, rig, machine_root, monkeypatch, tmp_path):
        machine_root.mkdir(parents=True)
        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path / "throwaway"))
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            provisioning.apply_grant_current_user({"op": "grant_current_user"})
        assert exc.value.step is Step.OVERRIDE


class TestPrunePrincipal:
    def _payload(self, **overrides):
        payload = {"op": "prune_principal", "task_name": "DistrictSync_Daily", "user": "CORP\\svc"}
        payload.update(overrides)
        return payload

    def test_an_unconfirmed_delete_leaves_the_ace(self, rig, machine_root):
        machine_root.mkdir(parents=True)
        rig.delete_confirms = False
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            provisioning.apply_prune_principal(self._payload())
        assert exc.value.step is Step.DELETE
        assert rig.icacls == []

    def test_a_confirmed_missing_delete_prunes(self, rig, machine_root):
        machine_root.mkdir(parents=True)
        provisioning.apply_prune_principal(self._payload())
        joined = " ".join(" ".join(a) for a in rig.icacls)
        assert "/remove:g" in joined
        assert "CORP\\svc" in joined

    def test_the_prune_leaves_the_delivery_secret(self, rig, machine_root):
        machine_root.mkdir(parents=True)
        secret = machine_root / "sftp_secret.bin"
        secret.write_bytes(b"sealed")
        provisioning.apply_prune_principal(self._payload())
        assert secret.exists(), "Convert's manual delivery still needs the secret"

    @pytest.mark.parametrize(
        "protected",
        [
            pytest.param("Administrators", id="the group that owns the profile"),
            pytest.param("SYSTEM", id="the account the nightly's own service needs"),
            pytest.param("CORP\\jane", id="the child's own token — it would lock itself out"),
            pytest.param("", id="blank, which 0046 reads as the signed-in account"),
        ],
    )
    def test_a_structural_principal_is_never_pruned(self, rig, machine_root, protected):
        """Pruning any of these leaves a directory nothing else in the app can repair.

        These SIDs resolve in the rig exactly as they do on Windows, so the refusal has to
        come from the fence itself — an unresolvable name would refuse for the wrong reason
        and hide a deleted fence (which is what happened before this was falsified).
        """
        machine_root.mkdir(parents=True)
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            provisioning.apply_prune_principal(self._payload(user=protected))
        assert exc.value.step is Step.PRINCIPAL
        assert rig.icacls == []
        assert rig.deleted == [], "a refused principal must not even remove the task"

    def test_a_genuine_service_principal_still_prunes(self, rig, machine_root):
        """The positive twin: the fence must not refuse every account."""
        machine_root.mkdir(parents=True)
        provisioning.apply_prune_principal(self._payload(user="CORP\\svc"))
        assert rig.icacls

    def test_a_non_zero_icacls_exit_is_fatal(self, rig, machine_root):
        machine_root.mkdir(parents=True)
        rig.icacls_exit = 5
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            provisioning.apply_prune_principal(self._payload())
        assert exc.value.step is Step.PRUNE
        assert exc.value.icacls_exit == 5


# --------------------------------------------------------------------------- #
# Migration — non-vacuous by construction.                                     #
# --------------------------------------------------------------------------- #


def _seed_run_store(db_path: Path, *, rows: int) -> sqlite3.Connection:
    """A WAL run store with ``rows`` committed and the ``-wal`` left UNCHECKPOINTED.

    The connection is returned OPEN on purpose: closing it checkpoints, which would make
    a plain byte copy of the main file succeed and turn every assertion below vacuous.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, payload TEXT)")
    conn.commit()
    for i in range(rows):
        conn.execute("INSERT INTO runs (payload) VALUES (?)", (f"row-{i}",))
    conn.commit()
    return conn


class TestMigration:
    def test_the_wal_content_arrives_and_a_byte_copy_would_have_missed_it(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        destination = tmp_path / "dst"
        (destination / "runs").mkdir(parents=True)
        conn = _seed_run_store(source / "history.db", rows=3)
        try:
            wal = source / "history.db-wal"
            assert wal.exists() and wal.stat().st_size > 0, "the -wal must be live for this to prove anything"

            # The control: a main-file-only byte copy, taken at the same instant.
            import shutil

            byte_copy = tmp_path / "byte_copy.db"
            shutil.copy2(source / "history.db", byte_copy)

            provisioning.migrate_profile(source, destination)
        finally:
            conn.close()

        promoted = sqlite3.connect(destination / "runs" / "history.db")
        try:
            assert promoted.execute("SELECT count(*) FROM runs").fetchone()[0] == 3
        finally:
            promoted.close()

        # The control must NOT have the row. In WAL mode the `CREATE TABLE` is itself
        # uncheckpointed, so the main file does not even carry the schema — accept either
        # shape, but never a populated table, which would make this test prove nothing.
        control = sqlite3.connect(byte_copy)
        try:
            control_rows = control.execute("SELECT count(*) FROM runs").fetchone()[0]
        except sqlite3.OperationalError:
            control_rows = 0
        finally:
            control.close()
        assert control_rows == 0, "a byte copy that already had the rows would make this test prove nothing"

    def test_a_torn_config_fails_here_not_at_the_next_load(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        destination = tmp_path / "dst"
        (destination / "runs").mkdir(parents=True)
        (source / "config.json").write_text('{"sis_type": "sd74my', encoding="utf-8")
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            provisioning.migrate_profile(source, destination)
        assert exc.value.step is Step.MIGRATE
        assert not (destination / "config.json").exists()

    def test_a_readable_config_is_parsed_then_written(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        destination = tmp_path / "dst"
        (destination / "runs").mkdir(parents=True)
        (source / "config.json").write_text(json.dumps({"sis_type": "sd74myedbc"}), encoding="utf-8")
        provisioning.migrate_profile(source, destination)
        assert json.loads((destination / "config.json").read_text(encoding="utf-8"))["sis_type"] == "sd74myedbc"

    def test_mappings_and_known_hosts_ride_along(self, tmp_path):
        source = tmp_path / "src"
        (source / "mappings").mkdir(parents=True)
        (source / "mappings" / "sd99custom_mapping.yaml").write_text("_base: myedbc\n", encoding="utf-8")
        (source / "known_hosts").write_text("host ssh-ed25519 AAAA\n", encoding="utf-8")
        destination = tmp_path / "dst"
        (destination / "runs").mkdir(parents=True)
        provisioning.migrate_profile(source, destination)
        assert (destination / "mappings" / "sd99custom_mapping.yaml").exists()
        assert (destination / "known_hosts").exists()

    def test_nothing_to_migrate_is_a_no_op(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        destination = tmp_path / "dst"
        (destination / "runs").mkdir(parents=True)
        provisioning.migrate_profile(source, destination)
        assert list(destination.iterdir()) == [destination / "runs"]

    def test_the_source_is_never_deleted(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "config.json").write_text("{}", encoding="utf-8")
        conn = _seed_run_store(source / "history.db", rows=1)
        destination = tmp_path / "dst"
        (destination / "runs").mkdir(parents=True)
        try:
            provisioning.migrate_profile(source, destination)
        finally:
            conn.close()
        assert (source / "config.json").exists()
        assert (source / "history.db").exists()

    def test_no_staging_directory_survives_a_success(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "config.json").write_text("{}", encoding="utf-8")
        destination = tmp_path / "dst"
        (destination / "runs").mkdir(parents=True)
        provisioning.migrate_profile(source, destination)
        assert [p.name for p in destination.iterdir() if p.name.startswith(".")] == []

    def test_a_short_copy_is_caught_by_the_row_census(self, tmp_path, monkeypatch):
        """The census is the only thing that can catch a backup that silently lost rows.

        ``integrity_check`` passes happily on a well-formed database that is simply
        MISSING records, so without this comparison a partial copy would be promoted and
        the district's run history would quietly shrink. Driven at the ``backup`` seam
        because a genuine short copy cannot be produced on demand. (Found by falsification:
        deleting the comparison left the suite green.)
        """
        source = tmp_path / "src"
        source.mkdir()
        conn = _seed_run_store(source / "history.db", rows=4)
        try:

            def _lossy_backup(live, copy):
                copy.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, payload TEXT)")
                copy.execute("INSERT INTO runs (payload) VALUES ('only-one')")
                copy.commit()

            monkeypatch.setattr(provisioning, "_backup_into", _lossy_backup)
            destination = tmp_path / "dst"
            (destination / "runs").mkdir(parents=True)
            with pytest.raises(provisioning.ProvisionRefused) as exc:
                provisioning.migrate_profile(source, destination)
        finally:
            conn.close()
        assert exc.value.step is Step.MIGRATE
        assert not (destination / "runs" / "history.db").exists(), "a short copy must never be promoted"

    def test_a_file_that_is_not_a_database_fails_loud(self, tmp_path):
        """A torn store must fail HERE, not at the nightly's first write."""
        source = tmp_path / "src"
        source.mkdir()
        (source / "history.db").write_bytes(b"this is not a SQLite database")
        destination = tmp_path / "dst"
        (destination / "runs").mkdir(parents=True)
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            provisioning.migrate_profile(source, destination)
        assert exc.value.step is Step.MIGRATE
        assert not (destination / "runs" / "history.db").exists()

    def test_the_census_covers_every_table_not_just_runs(self, tmp_path):
        """The row check is schema-agnostic, so ``meta`` is proven too."""
        source = tmp_path / "src"
        source.mkdir()
        conn = _seed_run_store(source / "history.db", rows=2)
        try:
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO meta VALUES ('created_at', '2026-09-17')")
            conn.commit()
            census = provisioning._row_census(conn)
            assert census == {"meta": 1, "runs": 2}
            destination = tmp_path / "dst"
            (destination / "runs").mkdir(parents=True)
            provisioning.migrate_profile(source, destination)
        finally:
            conn.close()
        promoted = sqlite3.connect(destination / "runs" / "history.db")
        try:
            assert provisioning._row_census(promoted) == {"meta": 1, "runs": 2}
        finally:
            promoted.close()


# --------------------------------------------------------------------------- #
# The result vocabulary carries no secret, no path, no stderr.                 #
# --------------------------------------------------------------------------- #


class TestResultVocabulary:
    _SFTP_SECRET = "uniq-sftp-pw-QQ42"
    _TASK_SECRET = "uniq-task-pw-ZZ91"

    def test_every_step_message_is_bounded_and_marker_free(self):
        from src.scheduler.messages import carries_foreign_marker

        for step in Step:
            message = provisioning.ProvisionRefused(step).message
            assert step.value in message
            assert not carries_foreign_marker(message)

    def test_neither_secret_reaches_the_message(self, rig, source_profile, machine_root, monkeypatch):
        rig.verify_raises = paths_module.MachineScopeRefused(
            paths_module.MachineScopeRefusedReason.INHERITED_ACL, machine_root
        )
        payload = _payload(
            source_profile,
            password=self._TASK_SECRET,
            sftp_host="sftp.example.org",
            sftp_username="district",
            sftp_password=self._SFTP_SECRET,
        )
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(payload, rig)
        assert self._TASK_SECRET not in exc.value.message
        assert self._SFTP_SECRET not in exc.value.message

    def test_the_message_names_no_filesystem_path(self, rig, source_profile, machine_root):
        rig.verify_raises = paths_module.MachineScopeRefused(
            paths_module.MachineScopeRefusedReason.INHERITED_ACL, machine_root
        )
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(_payload(source_profile), rig)
        assert str(machine_root) not in exc.value.message
        assert str(source_profile) not in exc.value.message

    def test_an_icacls_failure_carries_the_exit_code_and_nothing_else(self):
        refusal = provisioning.ProvisionRefused(Step.GRANT, icacls_exit=1332)
        assert "1332" in refusal.message
        assert "stderr" not in refusal.message.lower()


class TestSecretSeeding:
    def test_the_seed_is_written_through_the_machine_store(self, rig, source_profile, machine_root, monkeypatch):
        written: list[tuple[str, str, str]] = []

        class _Store:
            def store_password(self, host, username, password):
                written.append((host, username, password))

        monkeypatch.setattr(provisioning, "_machine_secret_store", lambda: _Store())
        payload = _payload(source_profile, sftp_host="sftp.example.org", sftp_username="district", sftp_password="pw")
        _run(payload, rig)
        assert written == [("sftp.example.org", "district", "pw")]

    def test_no_seed_means_no_store_write_and_no_claim(self, rig, source_profile, monkeypatch):
        touched: list[str] = []

        monkeypatch.setattr(provisioning, "_machine_secret_store", lambda: touched.append("built"))
        _run(_payload(source_profile), rig)
        assert touched == [], "the store must not be touched when nothing was seeded"
        assert rig.committed  # positive twin: provisioning still completes

    def test_a_failed_seal_refuses_before_the_commit(self, rig, source_profile, machine_root, monkeypatch):
        class _Store:
            def store_password(self, host, username, password):
                raise OSError("DPAPI would not seal")

        monkeypatch.setattr(provisioning, "_machine_secret_store", lambda: _Store())
        payload = _payload(source_profile, sftp_host="sftp.example.org", sftp_username="district", sftp_password="pw")
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _run(payload, rig)
        assert exc.value.step is Step.SECRET
        assert rig.committed == []
        assert not machine_root.exists()


# --------------------------------------------------------------------------- #
# Windows-only, REAL: create-with-SDDL then read the DACL back with icacls.    #
# --------------------------------------------------------------------------- #


@WINDOWS_ONLY
class TestRealCreateWithSddl:
    """No elevation: the descriptor names the CURRENT user where the real one names ``BA``.

    Everything else is the shipped shape — protected DACL, SID strings, ``runs/`` with the
    principal's Modify — so the mechanism itself is proven, not just the string builder.
    """

    def test_the_dacl_is_applied_at_creation_and_reads_back_clean(self, tmp_path):
        import subprocess

        from src.utils.helpers import system_binary

        me = provisioning._account_sid(provisioning.current_run_as_user())
        root = tmp_path / "DistrictSync"
        runs = root / "runs"
        provisioning._create_directory_with_sddl(
            root, provisioning._machine_root_sddl(setup_sid=me, principal_sid="S-1-5-20", owner_sid=me)
        )
        provisioning._create_directory_with_sddl(
            runs, provisioning._runs_sddl(setup_sid=me, principal_sid="S-1-5-20", owner_sid=me)
        )

        owner, control = paths_module._read_dir_security(root)
        assert owner == me
        assert control & paths_module._SE_DACL_PROTECTED

        paths_module.assert_no_open_aces(root)
        paths_module.assert_no_open_aces(runs)

        out = subprocess.run(  # nosec B603 - absolute System32 binary, our own tmp path
            [system_binary("icacls.exe"), str(root)], capture_output=True, text=True, check=False
        ).stdout
        assert "Users:" not in out and "Everyone" not in out
        assert "(RX)" in out  # the principal's read-execute at the root

        runs_out = subprocess.run(  # nosec B603 - absolute System32 binary, our own tmp path
            [system_binary("icacls.exe"), str(runs)], capture_output=True, text=True, check=False
        ).stdout
        assert "(M)" in runs_out

    def test_a_second_create_is_error_already_exists(self, tmp_path):
        me = provisioning._account_sid(provisioning.current_run_as_user())
        root = tmp_path / "DistrictSync"
        sddl = provisioning._machine_root_sddl(setup_sid=me, principal_sid="S-1-5-20", owner_sid=me)
        provisioning._create_directory_with_sddl(root, sddl)
        with pytest.raises(FileExistsError):
            provisioning._create_directory_with_sddl(root, sddl)

    def test_the_created_directory_can_be_listed_and_removed(self, tmp_path):
        """The measurement that killed strip-then-verify: a zero-ACE DACL makes both fail."""
        import shutil

        me = provisioning._account_sid(provisioning.current_run_as_user())
        root = tmp_path / "DistrictSync"
        provisioning._create_directory_with_sddl(
            root, provisioning._machine_root_sddl(setup_sid=me, principal_sid="S-1-5-20", owner_sid=me)
        )
        (root / "probe.txt").write_text("x", encoding="utf-8")
        assert [p.name for p in root.iterdir()] == ["probe.txt"]
        shutil.rmtree(root)
        assert not root.exists()
