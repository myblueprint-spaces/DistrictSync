"""DSYNC-37 — wake the computer to run the nightly task (Task Scheduler ``Settings.WakeToRun``).

One concern, end to end: the flag is set EXPLICITLY on the COM definition in both directions,
travels the whole registration chain (direct path, elevated child payload, provisioning
pre-flight, both adapters), is recorded as the fifth facet of the registered-schedule record,
is read back off the live task for display, and is offered on the Setup schedule section with
honest helper copy. Every "not" assertion here has a positive twin beside it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import src.ui_flet.screens.setup as setup_mod
from src.config.app_config import AppConfig, ConfigLoadState
from src.scheduler import elevated_apply, task_com, windows
from src.scheduler.task_com import Principal, PrincipalKind, RegisterParams, apply_definition, task_facts
from src.scheduler.windows import ScheduleReadback, current_run_as_user
from src.ui_flet.schedule_status import (
    WAKE_OFF_LINE,
    WAKE_ON_LINE,
    ScheduleState,
    ScheduleStatus,
    derive_schedule_status,
    diagnostic_details,
    wake_line,
)
from src.ui_flet.setup_flow import (
    SCHEDULE_WAKE_HELPER,
    SCHEDULE_WAKE_TOGGLE_LABEL,
    WAKE_TO_RUN_DEFAULT,
    ScheduleReconcile,
    TaskArgs,
    registered_schedule,
    schedule_reconcile,
    task_args_to_persisted,
    wake_to_run_initial,
)
from tests.test_ui_flet_render_smoke import (
    _benign_probe,
    _button_by_content,
    _checkbox_by_label,
    _has_text,
    _pick_event,
    _settings_dirs,
)
from tests.test_ui_flet_service_account import _capture_register, _drain, _registered_args

_REPO = Path(__file__).resolve().parent.parent


_USER_FOR_KIND = {
    PrincipalKind.PASSWORD: "CORP\\jane",
    PrincipalKind.MANAGED_SERVICE_ACCOUNT: "CORP\\svc$",
}


def _params(*, wake_to_run: bool, kind: PrincipalKind = PrincipalKind.PASSWORD) -> RegisterParams:
    return RegisterParams(
        task_name="DistrictSync_Daily",
        exe=r"C:\DistrictSync\DistrictSync.exe",
        arguments="--sis myedbc --source scheduled",
        working_dir=r"C:\DistrictSync",
        run_time="03:00",
        user=_USER_FOR_KIND.get(kind) or current_run_as_user(),
        password="pw" if kind is PrincipalKind.PASSWORD else None,
        kind=kind,
        run_highest=True,
        wake_to_run=wake_to_run,
    )


def _registered_settings(folder):
    return folder.RegisterTaskDefinition.call_args[0][1].Settings


# --------------------------------------------------------------------------- #
# The COM definition                                                           #
# --------------------------------------------------------------------------- #
class TestTheComSetting:
    @pytest.mark.parametrize("kind", list(PrincipalKind))
    def test_wake_on_sets_WakeToRun_True_for_every_principal_kind(self, kind):
        service, folder = MagicMock(), MagicMock()
        apply_definition(service, folder, _params(wake_to_run=True, kind=kind))
        assert _registered_settings(folder).WakeToRun is True

    def test_wake_off_sets_WakeToRun_False_explicitly(self):
        """The twin: OFF is written, not left to the COM default — a MagicMock attribute that
        was never assigned would be a MagicMock, never ``False``."""
        service, folder = MagicMock(), MagicMock()
        apply_definition(service, folder, _params(wake_to_run=False))
        assert _registered_settings(folder).WakeToRun is False

    def test_the_no_catch_up_guarantee_is_untouched(self):
        """DECISIONS 2026-06-15: waking the computer must never turn on catch-up runs."""
        for wake in (True, False):
            service, folder = MagicMock(), MagicMock()
            apply_definition(service, folder, _params(wake_to_run=wake))
            settings = _registered_settings(folder)
            assert settings.StartWhenAvailable is False
            assert settings.ExecutionTimeLimit == "PT2H"

    def test_wake_to_run_is_a_required_field(self):
        kwargs = dict(_params(wake_to_run=True).__dict__)
        del kwargs["wake_to_run"]
        with pytest.raises(TypeError):
            RegisterParams(**kwargs)  # type: ignore[call-arg]
        assert RegisterParams(**kwargs, wake_to_run=False).wake_to_run is False  # positive twin


# --------------------------------------------------------------------------- #
# The read-back                                                                #
# --------------------------------------------------------------------------- #
def _fake_task(wake: object) -> MagicMock:
    task = MagicMock()
    task.Definition.Actions.Count = 0
    task.Definition.Settings.WakeToRun = wake
    task.NextRunTime = None
    task.LastRunTime = None
    task.LastTaskResult = 0
    return task


class TestTheReadBack:
    @pytest.mark.parametrize("wake", [True, False])
    def test_task_facts_reads_the_live_setting(self, wake):
        assert task_facts(_fake_task(wake)).wake_to_run is wake

    @pytest.mark.parametrize("raw", [1, 0, "true", None])
    def test_a_non_boolean_reading_is_no_answer(self, raw):
        # ``1`` is not ``True`` here: a shape this module does not understand must never be
        # reported as "wakes". The twin above proves real booleans ARE read.
        assert task_facts(_fake_task(raw)).wake_to_run is None

    def test_read_schedule_carries_it(self, monkeypatch):
        monkeypatch.setattr("src.scheduler.windows.sys.platform", "win32")
        facts = task_com.TaskFacts(next_run=None, last_run=None, last_result=0, action_path=None, wake_to_run=False)
        monkeypatch.setattr(windows.task_com, "bounded", lambda fn, **_k: facts)
        assert windows.read_schedule("DistrictSync_Daily").wake_to_run is False

    @pytest.mark.parametrize("wake", [True, False])
    def test_the_live_status_carries_it(self, wake):
        status = derive_schedule_status(
            ScheduleReadback(found=True, last_result=0, wake_to_run=wake),
            hint_registered=True,
            latest_record_ts=None,
            foreign_account="",
            shared_records=False,
        )
        assert status.wake_to_run is wake

    def test_a_missing_task_reports_nothing_about_waking(self):
        status = derive_schedule_status(
            ScheduleReadback(found=False, wake_to_run=True),
            hint_registered=True,
            latest_record_ts=None,
            foreign_account="",
            shared_records=False,
        )
        assert status.wake_to_run is None
        assert wake_line(status) is None


class TestTheReadoutLine:
    def test_on_and_off_lines(self):
        live = dict(state=ScheduleState.LIVE, headline="", detail="")
        assert wake_line(ScheduleStatus(**live, wake_to_run=True)) == WAKE_ON_LINE
        assert wake_line(ScheduleStatus(**live, wake_to_run=False)) == WAKE_OFF_LINE

    def test_unknown_or_not_live_says_nothing(self):
        assert wake_line(ScheduleStatus(state=ScheduleState.LIVE, headline="", detail="")) is None
        assert wake_line(ScheduleStatus(state=ScheduleState.UNKNOWN, headline="", detail="", wake_to_run=True)) is None
        assert wake_line(None) is None

    def test_the_copy_never_promises_a_wake(self):
        """Honesty: Windows wakes only a sleeping computer whose power plan allows it."""
        for line in (WAKE_ON_LINE, WAKE_OFF_LINE, SCHEDULE_WAKE_HELPER):
            assert "always" not in line.lower()
            assert "guarantee" not in line.lower()
        assert "shut down" in SCHEDULE_WAKE_HELPER
        assert "Allow wake timers" in SCHEDULE_WAKE_HELPER
        assert "sleep" in SCHEDULE_WAKE_HELPER

    def test_copy_details_reports_it_only_when_known(self):
        live = dict(state=ScheduleState.LIVE, headline="", detail="")
        assert "Wake computer to run: yes" in diagnostic_details(
            ScheduleStatus(**live, wake_to_run=True), task_name="T"
        )
        assert "Wake computer to run: no" in diagnostic_details(
            ScheduleStatus(**live, wake_to_run=False), task_name="T"
        )
        assert "Wake computer" not in diagnostic_details(ScheduleStatus(**live), task_name="T")


# --------------------------------------------------------------------------- #
# The registration chain                                                       #
# --------------------------------------------------------------------------- #
class TestTheDirectPath:
    @pytest.mark.parametrize("wake", [True, False])
    def test_register_task_hands_it_to_the_com_params(self, monkeypatch, wake):
        seen: dict = {}
        monkeypatch.setattr(windows.task_com, "bounded", lambda fn, **_k: fn())
        monkeypatch.setattr(windows.task_com, "register_task_definition", lambda params: seen.setdefault("p", params))
        ok, _msg = windows.register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("x.exe"),
            sis_type="myedbc",
            input_dir=Path("i"),
            output_dir=Path("o"),
            run_time="03:00",
            principal=Principal(kind=PrincipalKind.INTERACTIVE_TOKEN),
            wake_to_run=wake,
        )
        assert ok is True
        assert seen["p"].wake_to_run is wake


class TestTheElevatedPath:
    @pytest.mark.parametrize("wake", [True, False])
    def test_the_sealed_payload_carries_a_real_boolean(self, monkeypatch, wake):
        monkeypatch.setattr("src.scheduler.windows.sys.platform", "win32")
        monkeypatch.setattr("src.scheduler.windows.is_elevated", lambda: False)
        captured: dict = {}

        def _write_request(payload):
            captured.update(payload)
            raise OSError("stop before any UAC prompt")

        monkeypatch.setattr("src.scheduler.elevation.write_request", _write_request)
        windows.register_task(
            task_name="DistrictSync_Daily",
            exe_path=Path("x.exe"),
            sis_type="myedbc",
            input_dir=Path("i"),
            output_dir=Path("o"),
            run_time="03:00",
            principal=Principal(kind=PrincipalKind.PASSWORD, user="CORP\\jane", password="pw"),
            wake_to_run=wake,
        )
        assert captured["wake_to_run"] is wake


def _register_payload(**over) -> dict:
    payload = {
        "op": "register",
        "task_name": "DistrictSync_Daily",
        "exe": r"C:\DistrictSync\DistrictSync.exe",
        "arguments": "--sis myedbc --source scheduled",
        "working_dir": r"C:\DistrictSync",
        "run_time": "03:00",
        "user": "CORP\\jane",
        "password": "pw",
        "kind": PrincipalKind.PASSWORD.value,
        "run_highest": True,
        "wake_to_run": True,
    }
    payload.update(over)
    return payload


def _run_child(tmp_path: Path, monkeypatch, payload: dict) -> tuple[dict, list]:
    req, res = tmp_path / "r.req", tmp_path / "r.res"
    req.write_bytes(b"sealed")
    monkeypatch.setattr("src.scheduler.elevation.unprotect_blob", lambda _b: json.dumps(payload).encode())
    calls: list = []
    monkeypatch.setattr("src.scheduler.task_com.register_task_definition", calls.append)
    assert elevated_apply.run_elevated_apply([str(req), str(res)]) == 0
    return json.loads(res.read_text(encoding="utf-8")), calls


class TestTheElevatedChild:
    @pytest.mark.parametrize("wake", [True, False])
    def test_the_payload_round_trips(self, tmp_path, monkeypatch, wake):
        result, calls = _run_child(tmp_path, monkeypatch, _register_payload(wake_to_run=wake))
        assert result == {"ok": True, "message": ""}
        assert len(calls) == 1
        assert calls[0].wake_to_run is wake

    @pytest.mark.parametrize("raw", ["false", "true", 0, 1, None])
    def test_a_non_boolean_is_refused_and_nothing_registers(self, tmp_path, monkeypatch, raw):
        # ``bool("false")`` is True — coercing would wake a computer the admin said not to.
        result, calls = _run_child(tmp_path, monkeypatch, _register_payload(wake_to_run=raw))
        assert result["ok"] is False
        assert result["message"] == elevated_apply._MSG_REQUEST_INVALID
        assert calls == []

    def test_a_missing_flag_is_refused(self, tmp_path, monkeypatch):
        payload = _register_payload()
        del payload["wake_to_run"]
        result, calls = _run_child(tmp_path, monkeypatch, payload)
        assert result["message"] == elevated_apply._MSG_REQUEST_INVALID
        assert calls == []

    def test_the_provision_preflight_refuses_a_non_boolean_before_any_step(self, monkeypatch):
        touched: list = []
        monkeypatch.setattr("src.scheduler.provisioning.apply_provision", lambda *a, **k: touched.append(a))
        with pytest.raises(ValueError):
            elevated_apply._do_provision({**_register_payload(wake_to_run="no"), "source_data_dir": "x"})
        assert touched == []
        elevated_apply._do_provision({**_register_payload(), "source_data_dir": "x"})  # positive twin
        assert len(touched) == 1


# --------------------------------------------------------------------------- #
# The record (AppConfig + setup_flow)                                          #
# --------------------------------------------------------------------------- #
def _args() -> TaskArgs:
    return TaskArgs.of(input_dir="i", output_dir="o", sis_type="myedbc", sftp_enabled=False, run_time="03:00")


def _record(raw_wake: object, *, with_args: bool = True):
    return registered_schedule(
        raw_task_args=task_args_to_persisted(_args()) if with_args else None,
        unattended_flag=False,
        raw_run_as_user="",
        raw_run_as_kind="",
        raw_wake_to_run=raw_wake,
    )


class TestTheRecord:
    def test_the_config_key_defaults_to_not_recorded(self):
        assert AppConfig().schedule_wake_to_run is None

    @pytest.mark.parametrize("value", [True, False])
    def test_the_facet_is_a_counted_admin_choice(self, value):
        cfg = AppConfig(load_state=ConfigLoadState.UNREADABLE)
        cfg.schedule_wake_to_run = value
        assert cfg._carries_chosen_settings() is True

    def test_its_default_alone_is_not_a_choice(self):
        assert AppConfig(load_state=ConfigLoadState.UNREADABLE)._carries_chosen_settings() is False

    @pytest.mark.parametrize("wake", [True, False])
    def test_a_recorded_value_is_read_back(self, wake):
        assert _record(wake).wake_to_run is wake

    @pytest.mark.parametrize("raw", [None, "yes", 1])
    def test_an_unrecorded_value_beside_a_record_means_False(self, raw):
        """EVIDENCED: no build before DSYNC-37 set WakeToRun, and the COM default is False."""
        assert _record(raw).wake_to_run is False

    def test_no_record_means_unknown(self):
        assert _record(True, with_args=False).wake_to_run is None

    def test_raw_wake_to_run_is_a_required_keyword(self):
        with pytest.raises(TypeError):
            registered_schedule(  # type: ignore[call-arg]
                raw_task_args=None, unattended_flag=False, raw_run_as_user="", raw_run_as_kind=""
            )


class TestTheOpeningValue:
    def test_a_new_registration_defaults_on(self):
        assert WAKE_TO_RUN_DEFAULT is True
        assert wake_to_run_initial(schedule_registered=False, record=_record(None, with_args=False)) is True

    def test_an_upgraded_task_reads_off(self):
        """An existing task changes only when the admin schedules again — never on upgrade."""
        assert wake_to_run_initial(schedule_registered=True, record=_record(None)) is False

    def test_a_recorded_task_reads_its_record(self):
        assert wake_to_run_initial(schedule_registered=True, record=_record(True)) is True
        assert wake_to_run_initial(schedule_registered=True, record=_record(False)) is False

    def test_an_unproven_registration_reads_off(self):
        assert wake_to_run_initial(schedule_registered=True, record=_record(None, with_args=False)) is False


class TestTheReconcile:
    def _reconcile(self, record, pending_wake):
        return schedule_reconcile(
            schedule_registered=True,
            record=record,
            pending=_args(),
            pending_run_as_user="",
            current_account="PC\\ted",
            pending_wake_to_run=pending_wake,
        )

    def test_a_wake_only_change_reregisters(self):
        assert self._reconcile(_record(None), True) is ScheduleReconcile.REREGISTER
        assert self._reconcile(_record(True), False) is ScheduleReconcile.REREGISTER

    def test_an_untouched_box_on_an_upgraded_install_is_up_to_date(self):
        record = _record(None)
        pending = wake_to_run_initial(schedule_registered=True, record=record)
        assert self._reconcile(record, pending) is ScheduleReconcile.UP_TO_DATE
        assert self._reconcile(_record(True), True) is ScheduleReconcile.UP_TO_DATE


# --------------------------------------------------------------------------- #
# The Setup schedule section                                                   #
# --------------------------------------------------------------------------- #
@pytest.fixture
def stub_page() -> MagicMock:
    return MagicMock()


def _cfg(tmp_path, monkeypatch, *, platform: str = "win32", **over) -> AppConfig:
    monkeypatch.setattr(sys, "platform", platform)
    in_dir, out_dir = _settings_dirs(tmp_path)
    fields: dict = {
        "input_dir": str(in_dir),
        "output_dir": str(out_dir),
        "sis_type": "myedbc",
        "setup_completed": True,
        "schedule_time": "03:00",
    }
    fields.update(over)
    cfg = AppConfig(**fields)
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    _benign_probe(monkeypatch)
    monkeypatch.setattr("src.scheduler.windows.current_run_as_user", lambda: "PC\\ted")
    return cfg


def _box(tree):
    return _checkbox_by_label(tree, SCHEDULE_WAKE_TOGGLE_LABEL)


class TestTheSetupSection:
    def test_a_fresh_install_offers_the_box_ticked_with_honest_helper(self, tmp_path, stub_page, monkeypatch):
        cfg = _cfg(tmp_path, monkeypatch)
        tree, handle = setup_mod._build_schedule_section(stub_page, cfg)
        assert _box(tree) is not None
        assert _box(tree).value is True
        assert _has_text(tree, SCHEDULE_WAKE_HELPER)
        assert handle.wake_to_run_value() is True

    def test_scheduling_registers_and_records_wake_on(self, tmp_path, stub_page, monkeypatch):
        cfg = _cfg(tmp_path, monkeypatch)
        seen = _capture_register(monkeypatch)
        tree, _ = setup_mod._build_schedule_section(stub_page, cfg)
        _button_by_content(tree, "Schedule nightly sync").on_click(None)
        _drain(stub_page)
        assert seen["calls"] == 1
        assert seen["wake_to_run"] is True
        assert cfg.schedule_wake_to_run is True

    def test_unticking_registers_and_records_wake_off(self, tmp_path, stub_page, monkeypatch):
        cfg = _cfg(tmp_path, monkeypatch)
        seen = _capture_register(monkeypatch)
        tree, handle = setup_mod._build_schedule_section(stub_page, cfg)
        box = _box(tree)
        box.value = False
        box.on_change(_pick_event(False))
        assert handle.wake_to_run_value() is False
        _button_by_content(tree, "Schedule nightly sync").on_click(None)
        _drain(stub_page)
        assert seen["wake_to_run"] is False
        assert cfg.schedule_wake_to_run is False

    def test_an_upgraded_install_shows_its_task_does_not_wake(self, tmp_path, stub_page, monkeypatch):
        cfg = _cfg(tmp_path, monkeypatch, schedule_registered=True)
        cfg.schedule_task_args = _registered_args(cfg)
        tree, handle = setup_mod._build_schedule_section(stub_page, cfg)
        assert _box(tree).value is False
        assert handle.wake_to_run_value() is False

    def test_a_recorded_wake_on_task_shows_ticked(self, tmp_path, stub_page, monkeypatch):
        cfg = _cfg(tmp_path, monkeypatch, schedule_registered=True, schedule_wake_to_run=True)
        cfg.schedule_task_args = _registered_args(cfg)
        tree, _ = setup_mod._build_schedule_section(stub_page, cfg)
        assert _box(tree).value is True

    def test_a_confirmed_unregister_clears_the_facet(self, tmp_path, stub_page, monkeypatch):
        cfg = _cfg(tmp_path, monkeypatch, schedule_registered=True, schedule_wake_to_run=True)
        cfg.schedule_task_args = _registered_args(cfg)
        monkeypatch.setattr("src.scheduler.windows.delete_task", lambda name: (True, "Schedule removed."))
        tree, _ = setup_mod._build_schedule_section(stub_page, cfg)
        _button_by_content(tree, "Remove nightly sync").on_click(None)
        _drain(stub_page)
        assert cfg.schedule_registered is False
        assert cfg.schedule_wake_to_run is None

    def test_cron_offers_no_box(self, tmp_path, stub_page, monkeypatch):
        cfg = _cfg(tmp_path, monkeypatch, platform="linux")
        tree, handle = setup_mod._build_schedule_section(stub_page, cfg)
        assert _box(tree) is None
        assert handle.wake_to_run_value() is WAKE_TO_RUN_DEFAULT  # positive twin: the cell still answers


# --------------------------------------------------------------------------- #
# Doc parity — the developer guide quotes the app's own copy                   #
# --------------------------------------------------------------------------- #
class TestDocParity:
    def test_ui_surfaces_quotes_the_label_and_helper_verbatim(self):
        text = (_REPO / "docs/developer/ui-surfaces.md").read_text(encoding="utf-8")
        assert SCHEDULE_WAKE_TOGGLE_LABEL in text
        assert SCHEDULE_WAKE_HELPER in text

    def test_scheduler_guide_names_the_setting(self):
        text = (_REPO / "docs/developer/scheduler.md").read_text(encoding="utf-8")
        assert "WakeToRun" in text
        assert "StartWhenAvailable" in text
