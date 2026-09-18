"""The PARENT half of machine-scope provisioning (plan 0049 S-1b-ii.1 / ii.2).

Five concerns, all of which live on the unprivileged side of the UAC boundary:

* :func:`complete_handover` — the post-provision session steps, gated on the parent's OWN
  re-read of the HKLM switch and ordered so nothing destructive can run before the re-pin;
* the ``MOVED.txt`` fence — ``AppConfig.save()`` and ``write_run_record`` refuse in a
  superseded profile, each with the positive twin that proves the mechanism works at all;
* :func:`request_access` — the ``grant_current_user`` round trip, branch by branch;
* :func:`delivery_secret_unreadable` (0049 S-2b.1) — the ONE pre-UAC gate input, read
  through ``select_store()`` and never "the keyring" by name, failing CLOSED;
* :func:`request_provision` (0049 S-2b.3) — the ``provision`` round trip, branch by branch,
  plus the ``"(step: …)"`` parity that keeps a refusal's bounded step recoverable.

NOTHING here launches an elevated child: every test drives the monkeypatched
``windows.run_elevated_child`` seam, exactly as ``tests/test_elevated_apply.py`` does. And
nothing touches a real keyring or a real DPAPI blob — the store is a stand-in behind
``select_store()``, or the suite's in-memory keyring backend.
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


# --------------------------------------------------------------------------- #
# delivery_secret_unreadable — the ONE pre-UAC gate (S-2b.1)                    #
# --------------------------------------------------------------------------- #
class _Store:
    """A stand-in for whatever ``select_store()`` returns, with both reads spied on."""

    def __init__(self, *, secret: str | None = "pw", has: bool | None = None, boom: Exception | None = None) -> None:
        self._secret = secret
        self._has = bool(secret) if has is None else has
        self._boom = boom
        self.has_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, str]] = []

    def has_secret(self, host: str, username: str) -> bool:
        self.has_calls.append((host, username))
        if self._boom is not None:
            raise self._boom
        return self._has

    def get_password(self, host: str, username: str) -> str | None:
        self.get_calls.append((host, username))
        if self._boom is not None:
            raise self._boom
        return self._secret


def _store(monkeypatch, store: _Store | None = None, *, select_boom: Exception | None = None) -> _Store:
    """Point ``select_store()`` at a stand-in. NOTHING here touches a real keyring."""
    from src.sftp import secret_store

    resolved = store if store is not None else _Store()

    def _select():
        if select_boom is not None:
            raise select_boom
        return resolved

    monkeypatch.setattr(secret_store, "select_store", _select)
    return resolved


_DELIVERY = {"enabled": True, "host": "sftp.example.com", "username": "sd74"}


class TestDeliverySecretUnreadable:
    def test_a_readable_secret_opens_the_gate(self, monkeypatch):
        store = _store(monkeypatch, _Store(secret="pw"))
        assert provision_session.delivery_secret_unreadable(**_DELIVERY) is False
        assert store.has_calls == [("sftp.example.com", "sd74")]

    def test_a_missing_secret_closes_it(self, monkeypatch):
        _store(monkeypatch, _Store(secret=None))
        assert provision_session.delivery_secret_unreadable(**_DELIVERY) is True

    @pytest.mark.parametrize(
        "over",
        [
            {"enabled": False},  # delivery is off — nothing to seed, and no problem
            {"host": ""},
            {"host": "   "},
            {"username": ""},
        ],
    )
    def test_delivery_that_is_not_configured_is_not_a_problem(self, monkeypatch, over):
        store = _store(monkeypatch, _Store(secret=None))
        assert provision_session.delivery_secret_unreadable(**{**_DELIVERY, **over}) is False
        assert store.has_calls == [], "the store was consulted for an install with no delivery"

    def test_it_never_materialises_the_password_to_answer_a_boolean(self, monkeypatch):
        """``has_secret`` exists precisely so a predicate need not hold the secret."""
        store = _store(monkeypatch, _Store(secret="pw"))
        provision_session.delivery_secret_unreadable(**_DELIVERY)
        assert store.get_calls == []

    def test_it_fails_closed_when_the_store_cannot_be_selected(self, monkeypatch, caplog):
        """ "We could not find out" is reported as unreadable: a wrong False hands the admin a
        permanently machine-scoped computer whose nightly silently stops delivering, while a
        wrong True shows a note naming a remedy they can carry out in a minute."""
        _store(monkeypatch, select_boom=RuntimeError("the shared profile is refused"))
        with caplog.at_level(logging.WARNING):
            assert provision_session.delivery_secret_unreadable(**_DELIVERY) is True
        assert any("delivery password" in record.getMessage() for record in caplog.records)

    def test_a_raising_has_secret_also_fails_closed(self, monkeypatch):
        _store(monkeypatch, _Store(boom=OSError("the backend is gone")))
        assert provision_session.delivery_secret_unreadable(**_DELIVERY) is True

    def test_it_reads_the_machine_store_on_a_machine_scoped_install(self, machine_scope):
        """The plan's rule: read through ``select_store()``, never "the keyring" by name.

        On a SECOND provisioning the secret already lives in the machine store, so a literal
        keyring read would block a perfectly healthy register — and the inverse matters just
        as much: a keyring entry must not make a machine-scoped install look ready to
        deliver when the shared blob is absent. Proved by seeding the KEYRING and asserting
        the answer ignores it. All three of the trust predicate's raw reads are seeded by the
        fixture; seeding fewer reaches the real Win32 API (green on Windows, red on Linux).
        """
        import keyring

        from src.sftp.secret_store import KEYRING_SERVICE, MachineSecretStore, select_store

        keyring.set_password(KEYRING_SERVICE, "sd74", "keyring-pw")
        paths_module.pin_data_dir()
        assert paths_module.is_machine_scope() is True
        assert isinstance(select_store(), MachineSecretStore)
        assert provision_session.delivery_secret_unreadable(**_DELIVERY) is True

    def test_the_same_keyring_entry_is_readable_per_user(self):
        """The positive twin: the row above must fail because the SCOPE changed the store,
        not because the seeding never worked."""
        import keyring

        from src.sftp.secret_store import KEYRING_SERVICE

        keyring.set_password(KEYRING_SERVICE, "sd74", "keyring-pw")
        assert provision_session.delivery_secret_unreadable(**_DELIVERY) is False

    def test_the_keywords_are_required(self):
        with pytest.raises(TypeError):
            provision_session.delivery_secret_unreadable()  # type: ignore[call-arg]


class TestReadDeliverySecret:
    def test_it_returns_the_stored_password(self, monkeypatch):
        _store(monkeypatch, _Store(secret="pw"))
        assert provision_session._read_delivery_secret(**_DELIVERY) == "pw"

    def test_nothing_configured_reads_nothing(self, monkeypatch):
        store = _store(monkeypatch, _Store(secret="pw"))
        assert provision_session._read_delivery_secret(**{**_DELIVERY, "enabled": False}) == ""
        assert store.get_calls == []

    def test_a_failure_is_empty_never_a_raise(self, monkeypatch, caplog):
        """``_seed_secret`` reads ``""`` as "nothing was sent" and makes no claim — which
        the gate above has already refused, so this is the floor rather than the route."""
        _store(monkeypatch, _Store(boom=OSError("dpapi said no")))
        with caplog.at_level(logging.WARNING):
            assert provision_session._read_delivery_secret(**_DELIVERY) == ""
        assert not any("pw" in record.getMessage() for record in caplog.records)

    def test_a_none_password_is_the_empty_string(self, monkeypatch):
        _store(monkeypatch, _Store(secret=None))
        assert provision_session._read_delivery_secret(**_DELIVERY) == ""


# --------------------------------------------------------------------------- #
# request_provision — the round trip (S-2b.3)                                  #
# --------------------------------------------------------------------------- #
_EXE = Path("C:/Program Files/DistrictSync/DistrictSync.exe")
_PASSWORD = "svc-secret-not-in-any-result"  # nosec B105 - a test fixture, never a real credential


def _provision(**over: object) -> provision_session.ProvisionAttempt:
    """One call shape for the whole file; the next required keyword is one edit here."""
    kwargs: dict[str, object] = {
        "task_name": "DistrictSync_Daily",
        "exe_path": _EXE,
        "sis_type": "sd74myedbc",
        "input_dir": Path("C:/GDE/in"),
        "output_dir": Path("C:/GDE/out"),
        "run_time": "03:00",
        "sftp": True,
        "sftp_host": "sftp.example.com",
        "sftp_username": "sd74",
        "run_as_user": "CORP\\svc_districtsync",
        "run_as_password": _PASSWORD,
    }
    kwargs.update(over)
    return provision_session.request_provision(**kwargs)  # type: ignore[arg-type]


def _refusal(step: provision_session.ProvisionStep, **kwargs: object) -> str:
    """The REAL exception's message — never a re-typed sentence (see the parity test)."""
    from src.scheduler.provisioning import ProvisionRefused

    return ProvisionRefused(step, **kwargs).message  # type: ignore[arg-type]


class TestRequestProvision:
    def test_a_successful_provision(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        _store(monkeypatch, _Store(secret="delivery-pw"))
        assert _provision().outcome is provision_session.ProvisionOutcome.PROVISIONED

    def test_the_payload_carries_the_op_the_action_line_and_both_secrets(self, monkeypatch):
        sent = _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        _store(monkeypatch, _Store(secret="delivery-pw"))
        _provision()

        assert len(sent) == 1
        payload = sent[0]
        assert payload["op"] == "provision"
        assert payload["source_data_dir"] == str(paths_module.per_user_data_dir())
        assert payload["user"] == "CORP\\svc_districtsync"
        assert payload["password"] == _PASSWORD
        assert payload["sftp_password"] == "delivery-pw"
        # The action line comes from windows._build_action_args — the SAME function the
        # ordinary register path uses, so a nightly cannot get different arguments
        # depending on which door created it.
        expected_args, expected_dir = provision_session.windows._build_action_args(
            _EXE, "sd74myedbc", Path("C:/GDE/in"), Path("C:/GDE/out"), True
        )
        assert payload["arguments"] == expected_args
        assert payload["working_dir"] == str(expected_dir)
        assert "--sftp" in str(payload["arguments"])

    def test_delivery_off_sends_no_secret_and_reads_none(self, monkeypatch):
        store = _store(monkeypatch, _Store(secret="delivery-pw"))
        sent = _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        _provision(sftp=False)
        assert sent[0]["sftp_password"] == ""
        assert store.get_calls == []

    def test_a_declined_prompt_is_its_own_outcome(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.DECLINED)
        _store(monkeypatch)
        assert _provision().outcome is provision_session.ProvisionOutcome.DECLINED

    def test_a_launch_failure_is_its_own_outcome(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.LAUNCH_FAILED)
        _store(monkeypatch)
        assert _provision().outcome is provision_session.ProvisionOutcome.LAUNCH_FAILED

    def test_a_timeout_is_unconfirmed(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.TIMEOUT)
        _store(monkeypatch)
        assert _provision().outcome is provision_session.ProvisionOutcome.UNCONFIRMED

    def test_a_killed_childs_own_ok_is_not_taken_as_a_provision(self, monkeypatch):
        """The child is TERMINATED on the bounded wait, so its result is not evidence — and
        ``complete_handover``'s own switch read is what settles which side of the commit we
        are on. Without the TIMEOUT branch this falls through and reports PROVISIONED from a
        process we killed."""
        _child(monkeypatch, result=ElevationResult.TIMEOUT, payload={"ok": True})
        _store(monkeypatch)
        assert _provision().outcome is provision_session.ProvisionOutcome.UNCONFIRMED

    def test_an_absent_result_is_unconfirmed(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.COMPLETED)
        _store(monkeypatch)
        assert _provision().outcome is provision_session.ProvisionOutcome.UNCONFIRMED

    def test_the_cross_sid_sentinel_gets_its_own_outcome(self, monkeypatch):
        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": "DSYNC_DIFFERENT_ACCOUNT"},
        )
        _store(monkeypatch)
        attempt = _provision()
        assert attempt.outcome is provision_session.ProvisionOutcome.DIFFERENT_ACCOUNT
        # Detected, never echoed: the sentinel carries the DSYNC_ prefix no admin-facing
        # string may republish, and the copy for this branch is written from the TYPE.
        assert "DSYNC_" not in repr(attempt)

    def test_a_step_refusal_carries_the_step_and_not_the_message(self, monkeypatch):
        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": _refusal(provision_session.ProvisionStep.PRE_EXISTING)},
        )
        _store(monkeypatch)
        attempt = _provision()
        assert attempt.outcome is provision_session.ProvisionOutcome.REFUSED
        assert attempt.step is provision_session.ProvisionStep.PRE_EXISTING
        # The interpolated message can carry an icacls exit code and a rollback clause — text
        # no copy review ever saw. The step is the whole vocabulary.
        assert attempt.message == ""

    def test_a_refusal_keeps_its_icacls_exit_code(self, monkeypatch):
        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": _refusal(provision_session.ProvisionStep.PRINCIPAL, icacls_exit=1332)},
        )
        _store(monkeypatch)
        attempt = _provision()
        assert (attempt.step, attempt.icacls_exit) == (provision_session.ProvisionStep.PRINCIPAL, 1332)

    def test_a_rollback_failure_reports_the_primary_step(self, monkeypatch):
        """A rollback failure APPENDS a second ``(step: rollback)`` marker. The primary step
        is the cause, so the FIRST match wins — otherwise every failed rollback would read
        as "a leftover folder" and hide what actually went wrong."""
        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": _refusal(provision_session.ProvisionStep.MIGRATE, rollback_failed=True)},
        )
        _store(monkeypatch)
        assert _provision().step is provision_session.ProvisionStep.MIGRATE

    def test_a_step_id_this_build_does_not_know_is_still_a_refusal(self, monkeypatch):
        """An older/newer child. It is still TRUE that the child refused, and inventing a
        step would be worse than naming none."""
        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": "DistrictSync could not set up the shared settings folder (step: zzz)."},
        )
        _store(monkeypatch)
        attempt = _provision()
        assert attempt.outcome is provision_session.ProvisionOutcome.REFUSED
        assert attempt.step is None

    def test_a_registration_failure_arrives_as_a_classifiable_canonical(self, monkeypatch):
        """The child registers the nightly LAST, after the HKLM commit — so this message is
        an ordinary schedule failure and must reach ``classify_schedule_error`` intact."""
        from src.scheduler import task_com
        from src.ui_flet.setup_errors import _unclassified_copy, classify_schedule_error

        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": task_com.MSG_LOGON_FAILURE},
        )
        _store(monkeypatch)
        attempt = _provision()
        assert attempt.outcome is provision_session.ProvisionOutcome.FAILED
        assert attempt.message == task_com.MSG_LOGON_FAILURE
        assert classify_schedule_error(attempt.message, True, account_is_current=False) != _unclassified_copy(
            attempt.message
        )

    def test_an_access_denied_from_the_child_is_relabelled(self, monkeypatch):
        """The prompt WAS approved, so the admin must not be told to answer it again (the
        loop SD60 ran on 2026-09-14). Same rule ``windows._register_elevated`` applies."""
        from src.scheduler import task_com, windows

        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": task_com.MSG_ACCESS_DENIED},
        )
        _store(monkeypatch)
        assert _provision().message == windows._MSG_ELEVATED_ACCESS_DENIED

    def test_a_leaking_child_message_collapses_before_it_can_surface(self, monkeypatch):
        from src.scheduler import windows

        _child(
            monkeypatch,
            result=ElevationResult.COMPLETED,
            payload={"ok": False, "message": "boom DSYNC_TASK_PW=hunter2"},
        )
        _store(monkeypatch)
        attempt = _provision()
        assert attempt.message == windows._MSG_CHILD_DETAIL_UNAVAILABLE
        assert "hunter2" not in repr(attempt)

    def test_an_unusable_account_name_is_refused_before_any_prompt(self, monkeypatch):
        """Exactly what the child's ``_principal_account`` would refuse with, decided here so
        the admin reads the account-name copy instead of a generic "couldn't start"."""
        launched: list[object] = []

        def _never(*args: object) -> ElevationOutcome:
            launched.append(args)
            return ElevationOutcome(ElevationResult.COMPLETED)

        monkeypatch.setattr(provision_session.windows, "run_elevated_child", _never)
        attempt = _provision(run_as_user="CORP\\svc account")
        assert attempt.outcome is provision_session.ProvisionOutcome.REFUSED
        assert attempt.step is provision_session.ProvisionStep.PRINCIPAL
        assert launched == [], "a UAC prompt was raised for an account we already refused"

    def test_the_data_dir_override_is_refused_with_its_own_step(self, monkeypatch):
        """``build_provision_payload`` refuses under ``DISTRICTSYNC_DATA_DIR`` in BOTH halves
        — and ``ProvisionRefused`` IS a ``RuntimeError``, so the generic rung would otherwise
        swallow the one refusal that arrives with a step already in hand."""
        _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        _store(monkeypatch)
        monkeypatch.setattr(paths_module, "_override_data_dir", lambda: Path("C:/somewhere/else"))
        attempt = _provision()
        assert attempt.outcome is provision_session.ProvisionOutcome.REFUSED
        assert attempt.step is provision_session.ProvisionStep.OVERRIDE

    def test_a_pre_consent_failure_is_unavailable_never_provisioned(self, monkeypatch, caplog):
        _store(monkeypatch)
        monkeypatch.setattr(
            provision_session.elevation,
            "write_request",
            lambda payload: (_ for _ in ()).throw(OSError("the profile is locked")),
        )
        with caplog.at_level(logging.ERROR):
            assert _provision().outcome is provision_session.ProvisionOutcome.UNAVAILABLE
        assert any("shared-settings change" in record.getMessage() for record in caplog.records)

    @pytest.mark.parametrize("bad", [{"run_time": "25:99"}, {"task_name": "bad;name"}, {"sis_type": "bad type"}])
    def test_a_malformed_task_field_never_reaches_a_prompt(self, monkeypatch, bad):
        """Validated in BOTH halves: the child re-validates every field, and the parent is
        the half with a log sink."""
        launched: list[object] = []

        def _never(*args: object) -> ElevationOutcome:
            launched.append(args)
            return ElevationOutcome(ElevationResult.COMPLETED)

        monkeypatch.setattr(provision_session.windows, "run_elevated_child", _never)
        _store(monkeypatch)
        assert _provision(**bad).outcome is provision_session.ProvisionOutcome.UNAVAILABLE
        assert launched == []

    def test_the_handshake_files_are_cleaned_up(self, monkeypatch):
        _child(monkeypatch, result=ElevationResult.COMPLETED, payload={"ok": True})
        _store(monkeypatch)
        _provision()
        assert not list(paths_module.handshake_dir().glob("dsync_elev_*"))

    @pytest.mark.parametrize(
        "payload",
        [
            {"ok": True},
            {"ok": False, "message": "DistrictSync could not set up the shared settings folder (step: secret)."},
            {"ok": False, "message": "something unmapped"},
            None,
        ],
    )
    def test_no_password_ever_reaches_the_returned_attempt(self, monkeypatch, payload):
        """Two secrets can ride the payload; neither may ride the result."""
        _child(monkeypatch, result=ElevationResult.COMPLETED, payload=payload)
        _store(monkeypatch, _Store(secret="delivery-pw"))
        rendered = repr(_provision())
        assert _PASSWORD not in rendered
        assert "delivery-pw" not in rendered

    def test_no_outcome_is_spelled_twice(self):
        values = [member.value for member in provision_session.ProvisionOutcome]
        assert len(values) == len(set(values))

    def test_it_mirrors_register_tasks_argument_shape(self):
        """The view keeps passing the fields it already holds, and the action line is
        composed in ONE place. A second spelling of that command line is how a nightly ends
        up running with different arguments depending on which door created it."""
        mine = list(inspect.signature(provision_session.request_provision).parameters)
        theirs = list(inspect.signature(provision_session.windows.register_task).parameters)
        assert mine[:7] == theirs[:7]

    def test_the_credential_keywords_are_required_and_undefaulted(self):
        params = inspect.signature(provision_session.request_provision).parameters
        for name in ("run_as_user", "run_as_password", "sftp_host", "sftp_username"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
            assert params[name].default is inspect.Parameter.empty, name


class TestStepMarkerParity:
    """The literal ``"(step: …)"`` shape is spelled in ``provisioning`` and matched here.

    Any literal copied out of ``src/`` needs a parity test tying it back (CLAUDE.md). This
    one runs the REAL exception through the REAL parser for every member and both optional
    clauses, so a re-worded ``ProvisionRefused`` message is red here rather than silently
    turning every refusal into a generic schedule failure.
    """

    @pytest.mark.parametrize("step", list(provision_session.ProvisionStep))
    def test_every_step_round_trips(self, step):
        attempt = provision_session._classify_child_refusal(_refusal(step))
        assert attempt.outcome is provision_session.ProvisionOutcome.REFUSED
        assert attempt.step is step

    @pytest.mark.parametrize("step", list(provision_session.ProvisionStep))
    def test_every_step_round_trips_with_both_optional_clauses(self, step):
        attempt = provision_session._classify_child_refusal(_refusal(step, icacls_exit=5, rollback_failed=True))
        assert attempt.step is step
        assert attempt.icacls_exit == 5

    def test_a_message_with_no_step_marker_is_not_a_refusal(self):
        """The positive twin for the sweep above: the parser is selective, not a catch-all
        that would swallow the post-commit registration failure this flow must surface."""
        attempt = provision_session._classify_child_refusal("Windows rejected the user name or password.")
        assert attempt.outcome is provision_session.ProvisionOutcome.FAILED
        assert attempt.step is None
