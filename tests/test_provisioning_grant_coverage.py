"""DSYNC-117: a second administrator's grant reaches EVERY protected folder of the shared profile.

Reproduced in the lab (MSI 0.57.1, Server 2022, workgroup): the grant window gave the second
administrator ``(OI)(CI)M`` on ``C:\\ProgramData\\DistrictSync`` ONLY. ``runs\\`` is created with a
PROTECTED descriptor (inheritance off), so nothing granted on the root ever reaches it, and the
relaunched app could not create its own log there (``[Errno 13] Permission denied``).

The fix is one table, :data:`provisioning.PROTECTED_SUBTREES`: every folder provisioning creates with
a protected descriptor is a row, with what the setup administrator and the nightly's principal get on
it. The descriptors, the creation step and the grant all read it, so a new protected folder cannot be
created without the grant covering it.

Every Windows API is a seam (``_create_directory_with_sddl``, ``_run_icacls``, ``_account_sid``), and
``machine_data_dir()`` is redirected into tmp, so this never touches the real ``C:\\ProgramData``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.scheduler import provisioning
from src.utils import paths as paths_module

_SETUP_SID = "S-1-5-21-1-2-3-1001"
_PRINCIPAL_SID = "S-1-5-21-1-2-3-1002"
_GRANTEE = "PC01\\second"


@pytest.fixture
def machine_root(tmp_path, monkeypatch):
    """Redirect ``machine_data_dir()`` into tmp. NEVER the real ``C:\\ProgramData``."""
    target = tmp_path / "ProgramData" / "DistrictSync"
    target.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths_module, "machine_data_dir", lambda: target)
    return target


class _Acl:
    """A model of the explicit aces ``icacls /grant`` leaves behind: ``{folder: {account: rights}}``.

    ``icacls <path> /grant <acct>:(OI)(CI)<r>`` MERGES into the account's existing explicit ace —
    granting the same right twice leaves one ace, not two. The model applies that rule, so a re-grant
    that is idempotent on Windows is idempotent here and a grant that never reached a folder is visible
    as a missing entry.
    """

    def __init__(self) -> None:
        self.aces: dict[Path, dict[str, set[str]]] = {}
        self.calls: list[list[str]] = []
        self.created: dict[Path, str] = {}

    def icacls(self, args: list[str]) -> int:
        self.calls.append(list(args))
        target, verb, spec = Path(args[0]), args[1], args[2]
        assert verb == "/grant", verb
        account, _, rights = spec.partition(":")
        assert rights.startswith("(OI)(CI)"), "an inheritable grant, like every other one provisioning makes"
        self.aces.setdefault(target, {}).setdefault(account, set()).add(rights.removeprefix("(OI)(CI)"))
        return 0

    def rights(self, folder: Path, account: str) -> set[str]:
        return self.aces.get(folder, {}).get(account, set())


@pytest.fixture
def acl(monkeypatch, machine_root):
    model = _Acl()

    def _create(path: Path, sddl: str) -> None:
        if path.exists():
            raise FileExistsError(path)
        path.mkdir(parents=True)
        model.created[path] = sddl

    def _sid(name: str) -> str:
        return {"corp\\jane": _SETUP_SID, "corp\\svc": _PRINCIPAL_SID}[name.lower()]

    monkeypatch.setattr(provisioning, "_create_directory_with_sddl", _create)
    monkeypatch.setattr(provisioning, "_run_icacls", model.icacls)
    monkeypatch.setattr(provisioning, "_account_sid", _sid)
    monkeypatch.setattr(provisioning, "current_run_as_user", lambda: _GRANTEE)
    monkeypatch.setattr(paths_module, "assert_machine_dir_trusted", lambda path: None)
    monkeypatch.setattr(paths_module, "assert_no_open_aces", lambda path: None)
    return model


def _provisioned(root: Path) -> list[Path]:
    """A profile exactly as provisioning leaves it: every protected folder, created with its descriptor."""
    provisioning._create_profile(root, setup_sid=_SETUP_SID, principal_sid=_PRINCIPAL_SID)
    return [root / subtree.relative for subtree in provisioning.PROTECTED_SUBTREES]


def _grant() -> None:
    provisioning.apply_grant_current_user({"op": "grant_current_user"})


# --------------------------------------------------------------------------- #
# The table is the single source: what is created protected is what is granted.
# --------------------------------------------------------------------------- #


class TestTheTable:
    def test_runs_and_the_root_are_protected_folders(self):
        relatives = [subtree.relative for subtree in provisioning.PROTECTED_SUBTREES]
        assert "" in relatives, "the profile root is protected"
        assert paths_module.MACHINE_RUNS_SUBDIR in relatives, "runs is protected — the lab's missed folder"

    def test_every_folder_provisioning_creates_is_a_row_and_is_created_protected(self, acl, machine_root):
        """A protected folder added to the creation step without a table row turns this red."""
        _provisioned(machine_root)
        expected = {machine_root / subtree.relative for subtree in provisioning.PROTECTED_SUBTREES}
        assert set(acl.created) == expected
        for sddl in acl.created.values():
            assert "D:P" in sddl, "created PROTECTED — which is why a root grant never reaches it"

    @pytest.mark.parametrize("subtree", provisioning.PROTECTED_SUBTREES, ids=lambda s: s.relative or "root")
    def test_the_descriptor_gives_the_setup_admin_and_the_principal_what_the_row_says(self, acl, machine_root, subtree):
        _provisioned(machine_root)
        sddl = acl.created[machine_root / subtree.relative]
        mask = {"M": provisioning.MODIFY, "RX": provisioning.READ_EXECUTE}
        assert f"(A;OICI;{mask[subtree.setup_rights]};;;{_SETUP_SID})" in sddl
        assert f"(A;OICI;{mask[subtree.principal_rights]};;;{_PRINCIPAL_SID})" in sddl

    def test_the_nightly_principal_still_gets_read_on_the_root_and_modify_on_runs(self):
        """Unchanged by this fix: the principal reads the profile and writes only run artefacts."""
        rows = {subtree.relative: subtree for subtree in provisioning.PROTECTED_SUBTREES}
        assert rows[""].principal_rights == "RX"
        assert rows[paths_module.MACHINE_RUNS_SUBDIR].principal_rights == "M"
        assert rows[""].setup_rights == rows[paths_module.MACHINE_RUNS_SUBDIR].setup_rights == "M"


# --------------------------------------------------------------------------- #
# The grant.                                                                   #
# --------------------------------------------------------------------------- #


class TestTheGrantCoversEveryProtectedFolder:
    def test_runs_is_granted_the_setup_admins_rights(self, acl, machine_root):
        """The lab bug: the grant stopped at the root and ``runs`` stayed closed to the new account."""
        _provisioned(machine_root)
        _grant()
        assert acl.rights(machine_root / paths_module.MACHINE_RUNS_SUBDIR, _GRANTEE) == {"M"}

    def test_positive_twin_the_root_is_still_granted(self, acl, machine_root):
        _provisioned(machine_root)
        _grant()
        assert acl.rights(machine_root, _GRANTEE) == {"M"}

    @pytest.mark.parametrize("subtree", provisioning.PROTECTED_SUBTREES, ids=lambda s: s.relative or "root")
    def test_every_row_gets_exactly_the_setup_admins_right(self, acl, machine_root, subtree):
        _provisioned(machine_root)
        _grant()
        assert acl.rights(machine_root / subtree.relative, _GRANTEE) == {subtree.setup_rights}

    def test_the_grantee_is_still_the_childs_own_token_on_every_folder(self, acl, machine_root):
        _provisioned(machine_root)
        provisioning.apply_grant_current_user({"op": "grant_current_user", "user": "CORP\\attacker"})
        assert all(_GRANTEE in call[2] and "attacker" not in call[2] for call in acl.calls)
        assert len(acl.calls) == len(provisioning.PROTECTED_SUBTREES)

    def test_a_failure_on_runs_is_fatal_and_names_the_grant_step(self, acl, machine_root, monkeypatch):
        _provisioned(machine_root)
        runs = str(machine_root / paths_module.MACHINE_RUNS_SUBDIR)
        monkeypatch.setattr(provisioning, "_run_icacls", lambda args: 5 if args[0] == runs else acl.icacls(args))
        with pytest.raises(provisioning.ProvisionRefused) as exc:
            _grant()
        assert exc.value.step is provisioning.ProvisionStep.GRANT
        assert exc.value.icacls_exit == 5


class TestIdempotentReGrant:
    def test_granting_twice_leaves_the_same_aces_and_never_refuses(self, acl, machine_root):
        _provisioned(machine_root)
        _grant()
        once = {folder: {acct: set(r) for acct, r in by.items()} for folder, by in acl.aces.items()}
        first_calls = list(acl.calls)
        _grant()
        assert acl.aces == once
        assert acl.calls[len(first_calls) :] == first_calls, "the same additive command, nothing else"

    def test_the_grant_never_uses_a_replacing_or_removing_verb(self, acl, machine_root):
        """``/grant`` merges; ``/grant:r`` or ``/remove`` would strip the setup admin's or the nightly's ace."""
        _provisioned(machine_root)
        _grant()
        assert {call[1] for call in acl.calls} == {"/grant"}


class TestRepairOfAPreFixPartialGrant:
    def test_a_root_only_grant_is_completed_on_the_next_attempt(self, acl, machine_root):
        """An install granted by the old build has the root ace and nothing on ``runs``."""
        _provisioned(machine_root)
        acl.aces[machine_root] = {_GRANTEE: {"M"}}  # what the pre-fix grant left
        runs = machine_root / paths_module.MACHINE_RUNS_SUBDIR
        assert acl.rights(runs, _GRANTEE) == set()

        _grant()

        assert acl.rights(runs, _GRANTEE) == {"M"}, "the missing folder is repaired"
        assert acl.rights(machine_root, _GRANTEE) == {"M"}, "and the root ace is unchanged"

    def test_a_missing_protected_folder_is_skipped_not_created(self, acl, machine_root):
        """The grant op never creates a profile folder: it cannot know the setup admin's SID. The
        resume path of ``provision`` re-creates ``runs`` with its descriptor."""
        machine_root.mkdir(parents=True)
        _grant()
        assert acl.rights(machine_root, _GRANTEE) == {"M"}
        assert not (machine_root / paths_module.MACHINE_RUNS_SUBDIR).exists()
        assert acl.created == {}


def test_the_grant_command_names_no_folder_outside_the_shared_profile(acl, machine_root):
    _provisioned(machine_root)
    _grant()
    for call in acl.calls:
        assert Path(call[0]) == machine_root or Path(call[0]).parent == machine_root
        assert re.fullmatch(r".+:\(OI\)\(CI\)(M|RX)", call[2])
