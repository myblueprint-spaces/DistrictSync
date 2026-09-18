"""The PARENT half of machine-scope provisioning (plan 0049 S-1b-ii.1 / ii.2).

Three concerns, all of which live on the unprivileged side of the UAC boundary:

* :func:`complete_handover` — the post-provision session steps, gated on the parent's OWN
  re-read of the HKLM switch and ordered so nothing destructive can run before the re-pin;
* the ``MOVED.txt`` fence — ``AppConfig.save()`` and ``write_run_record`` refuse in a
  superseded profile, each with the positive twin that proves the mechanism works at all;
* :func:`request_access` — the ``grant_current_user`` round trip, branch by branch.

NOTHING here launches an elevated child: every test drives the monkeypatched
``windows.run_elevated_child`` seam, exactly as ``tests/test_elevated_apply.py`` does.
"""

from __future__ import annotations

import inspect
import logging
import sqlite3
from pathlib import Path

import pytest

from src.config.app_config import AppConfig, ConfigLoadState, SettingsOverwriteRefused, config_file_path
from src.history import store
from src.scheduler import provision_session
from src.scheduler.elevation import ElevationOutcome, ElevationResult
from src.utils import paths as paths_module

# The DACL a provisioned profile actually gets, as ``(ace type, SID)`` pairs — the shape
# ``_read_dacl_aces`` returns. Copied from tests/test_paths.py deliberately: seeding fewer
# than ALL THREE of the trust predicate's raw reads leaves one hitting the real Win32 API,
# which passes on Windows and raises OSError -> INACCESSIBLE on CI's Linux leg (PR #132).
_TRUSTED_ACES = (
    (0, "S-1-5-18"),
    (0, "S-1-5-32-544"),
    (0, "S-1-5-21-1-2-3-1001"),
)


@pytest.fixture
def machine_scope(tmp_path, monkeypatch):
    """Force a TRUSTED machine profile on, seeding all three raw reads + the switch.

    Returns the (created) machine root. The switch is driven through a mutable holder so a
    test can flip it to OFF without re-seeding the other two seams.
    """
    root = tmp_path / "ProgramData" / "DistrictSync"
    (root / paths_module.MACHINE_RUNS_SUBDIR).mkdir(parents=True)
    state = {"on": True}

    monkeypatch.setattr(paths_module, "machine_data_dir", lambda: root)
    monkeypatch.setattr(paths_module, "_machine_switch_on", lambda: state["on"])
    monkeypatch.setattr(
        paths_module, "_read_dir_security", lambda path: ("S-1-5-32-544", paths_module._SE_DACL_PROTECTED)
    )
    monkeypatch.setattr(paths_module, "_read_dacl_aces", lambda path: _TRUSTED_ACES)
    paths_module.reset_data_dir_pin()
    return root


@pytest.fixture
def user_profile(isolated_user_profile) -> Path:
    """The isolated per-user profile, MATERIALISED (the autouse fixture creates it lazily)."""
    isolated_user_profile.mkdir(parents=True, exist_ok=True)
    return isolated_user_profile


@pytest.fixture
def switch_off(monkeypatch):
    """The parent's own switch read says OFF (the per-user path)."""
    monkeypatch.setattr(paths_module, "_machine_switch_on", lambda: False)


@pytest.fixture
def quiet_sink(monkeypatch):
    """Record every log-sink re-point WITHOUT reconfiguring the process's logging.

    Returns the list of profile paths ``user_log_file()`` resolved to at each call — which
    is what proves the sink was re-pointed AFTER the re-pin rather than before it.
    """
    seen: list[Path] = []

    def _spy(name: str = "", **kwargs: object) -> logging.Logger:
        seen.append(paths_module.user_log_file())
        return logging.getLogger("tests.provision_session")

    monkeypatch.setattr(provision_session, "get_logger", _spy)
    return seen


def _calls() -> tuple[list[str], object, object]:
    """A recorder for the two injected callbacks, in ONE ordered list."""
    order: list[str] = []
    return order, lambda: order.append("persist"), lambda: order.append("reenter")


# --------------------------------------------------------------------------- #
# complete_handover — the gate                                                 #
# --------------------------------------------------------------------------- #
class TestHandoverGate:
    def test_the_child_s_claim_is_not_an_input(self):
        """No elevation outcome / result parameter exists to gate on.

        The child is KILLED on the bounded wait, so its result file is absent on exactly
        the failures where it may nonetheless have committed. Structural, not a promise:
        there is nowhere to pass a claim.
        """
        params = set(inspect.signature(provision_session.complete_handover).parameters)
        assert params == {"persist", "reenter"}
        for banned in ("outcome", "result", "ok", "committed", "registered"):
            assert banned not in params

    def test_fires_when_the_parent_s_own_switch_read_says_on(self, machine_scope, quiet_sink):
        order, persist, reenter = _calls()
        outcome = provision_session.complete_handover(persist=persist, reenter=reenter)

        assert outcome.handed_over is True
        assert order == ["persist", "reenter"]

    def test_does_not_fire_when_the_switch_reads_off(self, switch_off, quiet_sink, user_profile):
        order, persist, reenter = _calls()
        (user_profile / "config.json").write_text("{}", encoding="utf-8")

        outcome = provision_session.complete_handover(persist=persist, reenter=reenter)

        assert outcome.handed_over is False
        # The facets are still persisted (the registration may well have succeeded) but
        # nothing is renamed and no breadcrumb is dropped in a profile that still lives.
        assert order == ["persist"]
        assert outcome.renamed == ()
        assert outcome.breadcrumb is False
        assert (user_profile / "config.json").read_text(encoding="utf-8") == "{}"

    def test_an_unreadable_switch_is_not_a_committed_one(self, monkeypatch, quiet_sink, user_profile):
        """Cannot prove it is ON -> do not rename a live profile."""

        def _boom() -> bool:
            raise paths_module.MachineScopeRefused(
                paths_module.MachineScopeRefusedReason.SWITCH_UNREADABLE, "HKLM\\SOFTWARE\\DistrictSync"
            )

        monkeypatch.setattr(paths_module, "_machine_switch_on", _boom)
        (user_profile / "config.json").write_text("{}", encoding="utf-8")
        order, persist, reenter = _calls()

        outcome = provision_session.complete_handover(persist=persist, reenter=reenter)

        assert outcome.handed_over is False
        assert (user_profile / "config.json").exists()
        assert order == ["persist"]


# --------------------------------------------------------------------------- #
# complete_handover — the order                                                #
# --------------------------------------------------------------------------- #
class TestHandoverOrder:
    def test_machine_scope_is_asserted_before_anything_destructive(
        self, machine_scope, monkeypatch, quiet_sink, user_profile
    ):
        """The switch reads ON but the pin lands per-user -> nothing is renamed.

        Reachable through ``DISTRICTSYNC_DATA_DIR`` (the override wins outright and is
        NEVER machine scope), which is precisely the state the assert exists for.
        """
        (user_profile / "config.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(paths_module, "_pinned", lambda: (user_profile, False))
        order, persist, reenter = _calls()

        outcome = provision_session.complete_handover(persist=persist, reenter=reenter)

        assert outcome.handed_over is False
        assert outcome.renamed == ()
        assert (user_profile / "config.json").exists()
        assert not (user_profile / "MOVED.txt").exists()
        assert order == ["persist"]

    def test_a_refused_repin_renames_nothing(self, machine_scope, monkeypatch, quiet_sink, user_profile):
        """The child committed HKLM against a directory the app's predicate rejects."""
        (user_profile / "config.json").write_text("{}", encoding="utf-8")

        def _refuse(path):
            raise paths_module.MachineScopeRefused(paths_module.MachineScopeRefusedReason.OPEN_ACE, path)

        monkeypatch.setattr(paths_module, "_assert_machine_dir_trusted", _refuse)
        order, persist, reenter = _calls()

        outcome = provision_session.complete_handover(persist=persist, reenter=reenter)

        assert outcome.handed_over is False
        assert outcome.refused is paths_module.MachineScopeRefusedReason.OPEN_ACE
        assert (user_profile / "config.json").exists()
        assert order == []  # there is nowhere to persist to — user_data_dir() refuses

    def test_the_sink_is_repointed_after_the_repin(self, machine_scope, quiet_sink):
        _, persist, reenter = _calls()
        provision_session.complete_handover(persist=persist, reenter=reenter)

        assert quiet_sink, "the log sink was never re-pointed"
        assert quiet_sink[-1].is_relative_to(machine_scope)

    def test_a_sink_that_will_not_reopen_does_not_abort_a_committed_handover(
        self, machine_scope, monkeypatch, user_profile, caplog
    ):
        """The switch is already committed — losing the log is no reason to abandon it."""

        def _boom(name: str = "", **kwargs: object) -> logging.Logger:
            raise OSError("the shared runs folder is not writable by this admin")

        monkeypatch.setattr(provision_session, "get_logger", _boom)
        (user_profile / "config.json").write_text("{}", encoding="utf-8")
        order, persist, reenter = _calls()

        with caplog.at_level(logging.WARNING):
            outcome = provision_session.complete_handover(persist=persist, reenter=reenter)

        assert outcome.handed_over is True
        assert outcome.renamed == ("config.json",)
        assert order == ["persist", "reenter"]
        assert any("log file" in record.getMessage() for record in caplog.records)

    def test_the_facet_save_lands_in_the_machine_config_not_the_renamed_one(
        self, machine_scope, quiet_sink, user_profile
    ):
        """The spec's ordering rule, asserted by where the bytes actually land."""
        (user_profile / "config.json").write_text('{"sis_type": "sd74myedbc"}', encoding="utf-8")
        written: list[Path] = []

        def _persist() -> None:
            # Exactly what S-2's three-facet save does: resolve at call time and write.
            target = config_file_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{"schedule_registered": true}', encoding="utf-8")
            written.append(target)

        provision_session.complete_handover(persist=_persist, reenter=lambda: None)

        assert written == [machine_scope / "config.json"]
        assert (machine_scope / "config.json").read_text(encoding="utf-8") == '{"schedule_registered": true}'
        assert not (user_profile / "config.json").exists()

    def test_persist_precedes_reenter(self, machine_scope, quiet_sink):
        """A re-entry that ran first would rebuild every screen from a config without the facets."""
        order, persist, reenter = _calls()
        provision_session.complete_handover(persist=persist, reenter=reenter)
        assert order == ["persist", "reenter"]


# --------------------------------------------------------------------------- #
# complete_handover — setting the per-user artefacts aside                     #
# --------------------------------------------------------------------------- #
class TestSetAside:
    def _seed(self, profile: Path) -> None:
        (profile / "config.json").write_text("{}", encoding="utf-8")
        (profile / "history.db").write_bytes(b"sqlite")
        (profile / "history.db-wal").write_bytes(b"wal")
        (profile / "history.db-shm").write_bytes(b"shm")

    def test_renames_the_config_the_store_and_both_sidecars(self, machine_scope, quiet_sink, user_profile):
        self._seed(user_profile)

        outcome = provision_session.complete_handover(persist=lambda: None, reenter=lambda: None)

        assert set(outcome.renamed) == {"config.json", "history.db", "history.db-wal", "history.db-shm"}
        assert outcome.unrenamed == ()
        for name in ("config.json", "history.db", "history.db-wal", "history.db-shm"):
            assert not (user_profile / name).exists()
            assert list(user_profile.glob(f"{name}.pre-machine-*"))

    def test_an_absent_artefact_is_not_reported_as_renamed(self, machine_scope, quiet_sink, user_profile):
        (user_profile / "config.json").write_text("{}", encoding="utf-8")

        outcome = provision_session.complete_handover(persist=lambda: None, reenter=lambda: None)

        assert outcome.renamed == ("config.json",)

    def test_a_rename_failure_is_reported_never_swallowed(self, machine_scope, monkeypatch, quiet_sink, user_profile):
        self._seed(user_profile)
        monkeypatch.setattr(
            provision_session.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("locked by another process"))
        )

        outcome = provision_session.complete_handover(persist=lambda: None, reenter=lambda: None)

        assert outcome.handed_over is True  # the switch is committed; the rename is belt-and-braces
        assert outcome.renamed == ()
        assert set(outcome.unrenamed) == {"config.json", "history.db", "history.db-wal", "history.db-shm"}
        # The BREADCRUMB is what actually fences, so it must still be written.
        assert (user_profile / "MOVED.txt").is_file()

    def test_an_existing_target_is_never_overwritten(
        self, machine_scope, quiet_sink, user_profile, monkeypatch, caplog
    ):
        """The EXPLICIT check, not ``os.rename``'s accident.

        On Windows ``os.rename`` refuses an existing target, so the outcome alone cannot
        tell the guard from the platform — and on POSIX the same call REPLACES, destroying
        the evidence. Asserting the guard's own warning is what makes this test mean the
        same thing on both legs of CI (found by a falsification probe that stayed green).
        """
        (user_profile / "config.json").write_text("new", encoding="utf-8")
        monkeypatch.setattr(provision_session, "_stamp", lambda: "fixed")
        (user_profile / "config.json.pre-machine-fixed").write_text("older evidence", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            outcome = provision_session.complete_handover(persist=lambda: None, reenter=lambda: None)

        assert outcome.unrenamed == ("config.json",)
        assert (user_profile / "config.json.pre-machine-fixed").read_text(encoding="utf-8") == "older evidence"
        assert any("is already there" in record.getMessage() for record in caplog.records)

    def test_the_breadcrumb_names_the_shared_profile(self, machine_scope, quiet_sink, user_profile):
        outcome = provision_session.complete_handover(persist=lambda: None, reenter=lambda: None)

        assert outcome.breadcrumb is True
        text = (user_profile / "MOVED.txt").read_text(encoding="utf-8")
        assert str(machine_scope) in text

    def test_a_live_profile_is_never_its_own_supersession(self, machine_scope, monkeypatch, quiet_sink):
        """If the two resolutions ever collided, the handover would fence the LIVE profile."""
        monkeypatch.setattr(paths_module, "per_user_data_dir", lambda: paths_module.user_data_dir())
        order, persist, reenter = _calls()

        outcome = provision_session.complete_handover(persist=persist, reenter=reenter)

        assert outcome.renamed == ()
        assert outcome.breadcrumb is False
        assert not (machine_scope / "MOVED.txt").exists()
        # Only the SET-ASIDE is skipped: the scope really did change, so a session that
        # saved no facets and kept pre-handover screens would be a worse outcome than the
        # collision itself.
        assert order == ["persist", "reenter"]


# --------------------------------------------------------------------------- #
# The MOVED.txt fence (amendment 2)                                            #
# --------------------------------------------------------------------------- #
class TestMovedFence:
    def test_app_config_save_refuses_in_a_superseded_profile(self, user_profile):
        (user_profile / "MOVED.txt").write_text("superseded", encoding="utf-8")
        cfg = AppConfig(sis_type="sd74myedbc")

        with pytest.raises(SettingsOverwriteRefused):
            cfg.save()

        assert not config_file_path().exists()

    def test_app_config_save_succeeds_without_the_breadcrumb(self, user_profile):
        """The positive twin: the same save, the same profile, no MOVED.txt."""
        cfg = AppConfig(sis_type="sd74myedbc")
        cfg.save()
        assert config_file_path().is_file()
        assert AppConfig.load().sis_type == "sd74myedbc"

    def test_the_fence_leaves_a_config_that_is_already_there_untouched(self, user_profile):
        AppConfig(sis_type="sd74myedbc").save()
        before = config_file_path().read_bytes()
        (user_profile / "MOVED.txt").write_text("superseded", encoding="utf-8")

        with pytest.raises(SettingsOverwriteRefused):
            AppConfig(sis_type="sd40myedbc").save()

        assert config_file_path().read_bytes() == before

    def test_the_fence_warns(self, user_profile, caplog):
        (user_profile / "MOVED.txt").write_text("superseded", encoding="utf-8")
        with caplog.at_level(logging.WARNING), pytest.raises(SettingsOverwriteRefused):
            AppConfig(sis_type="sd74myedbc").save()
        assert any("MOVED.txt" in record.getMessage() for record in caplog.records)

    def test_an_unreadable_load_state_still_refuses_on_the_fence(self, user_profile):
        """The fence runs FIRST — before the UNREADABLE guard, which would otherwise pass."""
        (user_profile / "MOVED.txt").write_text("superseded", encoding="utf-8")
        cfg = AppConfig(sis_type="sd74myedbc", load_state=ConfigLoadState.UNREADABLE)
        with pytest.raises(SettingsOverwriteRefused):
            cfg.save()

    def test_write_run_record_refuses_in_a_superseded_profile(self, user_profile, caplog):
        (user_profile / "MOVED.txt").write_text("superseded", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            written = store.write_run_record({"status": "success"}, source="scheduled")

        assert written is False
        assert not paths_module.user_history_db().exists()
        assert any("MOVED.txt" in record.getMessage() for record in caplog.records)

    def test_write_run_record_succeeds_without_the_breadcrumb(self, user_profile):
        """The positive twin: the store creates itself and takes the row."""
        assert store.write_run_record({"status": "success"}, source="scheduled") is True
        assert paths_module.user_history_db().is_file()
        records = store.read_run_records()
        assert records is not None and len(records) == 1

    def test_the_fence_never_raises_out_of_the_run_store(self, user_profile, monkeypatch):
        """``write_run_record``'s non-fatal contract is what keeps a run's exit code its own."""
        (user_profile / "MOVED.txt").write_text("superseded", encoding="utf-8")

        def _fail_hard(*a, **k):
            raise sqlite3.OperationalError("the fence must return before any of this")

        monkeypatch.setattr(store, "_write", _fail_hard)
        assert store.write_run_record({"status": "success"}, source="cli") is False

    def test_the_fence_checks_the_profile_root_not_the_store_s_parent(self, machine_scope, user_profile):
        """On a machine-scoped install the store lives under ``runs/``, one level down."""
        paths_module.pin_data_dir()
        (machine_scope / "MOVED.txt").write_text("superseded", encoding="utf-8")

        assert store.write_run_record({"status": "success"}, source="scheduled") is False
        assert not paths_module.user_history_db().exists()


# --------------------------------------------------------------------------- #
# request_access — the grant round trip                                        #
# --------------------------------------------------------------------------- #
def _child(monkeypatch, *, result: ElevationResult, payload: dict | None = None) -> list[dict]:
    """Drive the elevated child seam. NOTHING here shows a UAC prompt."""
    sent: list[dict] = []
    real_write = provision_session.elevation.write_request

    def _write(request: dict) -> Path:
        sent.append(dict(request))
        return real_write(request)

    def _run(req_path: Path, res_path: Path) -> ElevationOutcome:
        if payload is not None:
            res_path.write_text(__import__("json").dumps(payload), encoding="utf-8")
        return ElevationOutcome(result)

    # The real ``write_request`` still runs (so the file lifecycle stays under test), but its
    # two Windows-only side effects are seams here: DPAPI reaches ``ctypes.WinDLL`` and the
    # owner-only DACL shells out to ``icacls``. Both work on Windows and raise on CI's Linux
    # leg — the same blind spot that reddened PR #129 and #132, in its third form. Stubbed
    # exactly as ``tests/test_scheduler_elevation.py`` stubs them.
    monkeypatch.setattr(provision_session.elevation, "protect_blob", lambda raw: b"sealed:" + raw)
    monkeypatch.setattr(provision_session.elevation, "_set_owner_only_dacl", lambda path: None)
    monkeypatch.setattr(provision_session.elevation, "write_request", _write)
    monkeypatch.setattr(provision_session.windows, "run_elevated_child", _run)
    return sent


class TestRequestAccess:
    def test_a_successful_grant(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        assert provision_session.request_access() is provision_session.GrantOutcome.GRANTED

    def test_the_payload_asks_for_exactly_one_op_and_carries_nothing_else(self, monkeypatch):
        sent = _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        provision_session.request_access()
        assert sent == [{"op": "grant_current_user"}]

    def test_a_declined_prompt_is_its_own_outcome(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.DECLINED)
        assert provision_session.request_access() is provision_session.GrantOutcome.DECLINED

    def test_a_launch_failure_is_its_own_outcome(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.LAUNCH_FAILED)
        assert provision_session.request_access() is provision_session.GrantOutcome.LAUNCH_FAILED

    def test_a_timeout_is_unconfirmed_never_granted(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.TIMEOUT)
        assert provision_session.request_access() is provision_session.GrantOutcome.UNCONFIRMED

    def test_a_killed_child_s_own_ok_is_not_taken_as_a_grant(self, monkeypatch):
        """The child is TERMINATED on the bounded wait, so its result is not evidence.

        Without the TIMEOUT branch this falls through to ``read_result`` and reports a
        success from a process we killed (found by a falsification probe that stayed green
        against a timeout that wrote nothing).
        """
        _child(monkeypatch, result=ElevationResult.TIMEOUT, payload={"ok": True})
        assert provision_session.request_access() is provision_session.GrantOutcome.UNCONFIRMED

    def test_an_absent_result_is_unconfirmed(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.COMPLETED)
        assert provision_session.request_access() is provision_session.GrantOutcome.UNCONFIRMED

    def test_the_cross_sid_sentinel_gets_its_own_outcome(self, monkeypatch):
        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": "DSYNC_DIFFERENT_ACCOUNT"},
        )
        assert provision_session.request_access() is provision_session.GrantOutcome.DIFFERENT_ACCOUNT

    def test_a_child_refusal_is_refused_not_unconfirmed(self, monkeypatch):
        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": "DistrictSync could not set up the shared settings folder (step: grant)."},
        )
        assert provision_session.request_access() is provision_session.GrantOutcome.REFUSED

    def test_the_handshake_files_are_cleaned_up(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        provision_session.request_access()
        assert not list(paths_module.handshake_dir().glob("dsync_elev_*"))

    def test_a_pre_consent_failure_is_unavailable_never_granted(self, monkeypatch):
        monkeypatch.setattr(
            provision_session.elevation,
            "write_request",
            lambda payload: (_ for _ in ()).throw(OSError("the profile is locked")),
        )
        assert provision_session.request_access() is provision_session.GrantOutcome.UNAVAILABLE

    def test_no_outcome_is_spelled_twice(self):
        values = [member.value for member in provision_session.GrantOutcome]
        assert len(values) == len(set(values))
