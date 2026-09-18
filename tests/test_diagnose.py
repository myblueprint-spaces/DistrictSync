"""``DistrictSync --diagnose`` — the read-only support report (plan 0049 S-1b-ii.3).

Four properties this command lives or dies by, each with a positive twin so no assertion is
vacuous: it is REACHABLE in the one state it exists for (a refused machine scope, where the
CLI preamble returns before argparse is built), it NAMES the bounded reason, it prints no
password in ANY state including secret-present, and every profile-resolving line is guarded
on its own — one raising must not cost the report the other twenty.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from src.config.app_config import AppConfig
from src.scheduler.windows import ScheduleReadback
from src.utils import diagnostics
from src.utils import paths as paths_module


@pytest.fixture(autouse=True)
def no_real_scheduler(monkeypatch):
    """Never query the developer's / CI runner's real Task Scheduler from a unit test."""
    monkeypatch.setattr("src.scheduler.windows.read_schedule", lambda name: ScheduleReadback(found=False))


def _report() -> str:
    out = io.StringIO()
    assert diagnostics.run_diagnose(out) == 0
    return out.getvalue()


class TestReachability:
    def test_the_pre_check_returns_before_the_preamble_pins_anything(self, monkeypatch, capsys):
        """The whole point of recognising it beside ``--elevated-apply``.

        ``_cli`` pins the data dir and RETURNS a machine-scope refusal before argparse
        exists, so a normally-parsed flag would be unreachable on exactly the install this
        command is for.
        """
        from src import main as main_module

        pinned: list[str] = []
        monkeypatch.setattr(main_module, "pin_data_dir", lambda: pinned.append("pin"))
        monkeypatch.setattr(main_module, "migrate_legacy_data_dir", lambda: pinned.append("migrate"))

        assert main_module.cli(["--diagnose"]) == 0
        assert pinned == [], "the preamble ran — the flag is being parsed, not pre-checked"
        assert diagnostics.REPORT_ANCHOR in capsys.readouterr().out

    def test_it_runs_when_the_profile_itself_refuses(self, monkeypatch, capsys):
        from src import main as main_module

        def _refuse() -> object:
            raise paths_module.MachineScopeRefused(
                paths_module.MachineScopeRefusedReason.INACCESSIBLE, "C:\\ProgramData\\DistrictSync"
            )

        monkeypatch.setattr(paths_module, "_pinned", _refuse)

        assert main_module.cli(["--diagnose"]) == 0
        assert "inaccessible" in capsys.readouterr().out

    def test_help_documents_the_flag_it_dispatches(self, capsys):
        """One spelling in the pre-check, one in the epilog — pinned together."""
        from src import main as main_module

        assert main_module.cli(["--help"]) == 0
        assert "--diagnose" in capsys.readouterr().out

    def test_the_exit_code_is_zero_in_every_state(self, monkeypatch):
        assert diagnostics.run_diagnose(io.StringIO()) == 0

        monkeypatch.setattr(paths_module, "_pinned", lambda: (_ for _ in ()).throw(OSError("everything is broken")))
        assert diagnostics.run_diagnose(io.StringIO()) == 0


class TestRefusalReason:
    def test_the_bounded_reason_is_named_not_a_stringified_exception(self, monkeypatch):
        def _refuse() -> object:
            raise paths_module.MachineScopeRefused(
                paths_module.MachineScopeRefusedReason.OPEN_ACE, "C:\\ProgramData\\DistrictSync"
            )

        monkeypatch.setattr(paths_module, "_pinned", _refuse)
        report = _report()

        assert "open_ace" in report
        assert "shared profile refused" in report

    def test_the_trust_verdict_is_the_app_s_own_predicate(self, monkeypatch, tmp_path):
        root = tmp_path / "ProgramData" / "DistrictSync"
        root.mkdir(parents=True)
        monkeypatch.setattr(paths_module, "machine_data_dir", lambda: root)

        def _refuse(path):
            raise paths_module.MachineScopeRefused(paths_module.MachineScopeRefusedReason.FOREIGN_OWNER, path)

        monkeypatch.setattr(paths_module, "_assert_machine_dir_trusted", _refuse)
        report = _report()

        assert "trusted:" in report
        assert "foreign_owner" in report

    def test_a_per_user_install_says_so_plainly(self):
        report = _report()
        assert "this account only" in report


class TestNoSecrets:
    def test_no_password_is_printed_when_a_secret_IS_present(self, monkeypatch, isolated_user_profile):
        """The absence assertion's positive twin: the secret really is there and readable."""
        from src.sftp.secret_store import UserSecretStore

        secret = "correct-horse-battery-staple"  # nosec B105 - a test fixture value, not a credential
        UserSecretStore().store_password("sftp.example.org", "svc_sd74", secret)
        monkeypatch.setattr(
            AppConfig,
            "load",
            classmethod(lambda cls: cls(sftp_enabled=True, sftp_host="sftp.example.org", sftp_username="svc_sd74")),
        )

        report = _report()

        assert "identity match" in report
        assert "yes" in report.split("identity match")[1].splitlines()[0]
        assert secret not in report

    def test_no_password_is_printed_when_the_store_raises(self, monkeypatch):
        class _Boom:
            def has_secret(self, host: str, username: str) -> bool:
                raise OSError("hunter2 must never reach the report")

        monkeypatch.setattr("src.sftp.secret_store.select_store", lambda: _Boom())
        report = _report()

        assert "hunter2" not in report
        assert "identity match" in report

    def test_the_header_does_not_claim_to_be_pii_free(self):
        note = diagnostics.PRIVACY_NOTE.lower()
        assert "no passwords" in note
        assert "windows accounts and folders" in note
        for overclaim in ("no personal", "pii-free", "anonymous", "no identifying"):
            assert overclaim not in note


class TestPerLineGuards:
    def test_one_unresolvable_line_does_not_cost_the_report_the_others(self, monkeypatch):
        monkeypatch.setattr(paths_module, "user_log_file", lambda: (_ for _ in ()).throw(OSError("denied")))
        report = _report()

        assert "log file" in report
        assert "unavailable (OSError)" in report
        # Every other section still printed.
        for heading in (
            "Profile:",
            "Shared-settings switch",
            "Shared folder:",
            "Delivery (SFTP):",
            "Nightly schedule:",
        ):
            assert heading in report

    def test_the_run_store_line_survives_a_refused_profile(self, monkeypatch):
        monkeypatch.setattr(
            "src.history.store.read_run_records",
            lambda limit=None: (_ for _ in ()).throw(
                paths_module.MachineScopeRefused(paths_module.MachineScopeRefusedReason.MISSING, "C:\\x")
            ),
        )
        report = _report()

        assert "Last run recorded:" in report
        assert "unavailable" in report.split("Last run recorded:")[1]

    def test_an_empty_ledger_is_reported_as_such_not_as_a_failure(self, isolated_user_profile):
        report = _report()
        assert "no runs recorded yet" in report

    def test_a_recorded_run_is_shown_without_its_error_detail(self, isolated_user_profile):
        from src.history.store import write_run_record

        assert write_run_record(
            {
                "status": "failed",
                "sis_type": "sd74myedbc",
                "run_as": "CORP\\svc_districtsync",
                "error_category": "config",
                "error": "the free-text detail that belongs in the log only",
            },
            source="scheduled",
        )
        report = _report()

        assert "sd74myedbc" in report
        assert "CORP\\svc_districtsync" in report
        assert "config" in report
        assert "free-text detail" not in report

    def test_every_line_is_aligned_the_way_sftp_show_aligns_its_block(self):
        rows = [line for line in diagnostics.diagnose_lines() if line.startswith("  ")]
        assert rows
        for row in rows:
            assert row[2] != " ", f"a value ran into the label column: {row!r}"
            label, sep, value = row.partition(":")
            assert sep, f"no label on {row!r}"
            # A label longer than the column must still be separated from its value — the
            # ``recorded run-as:CORP\\svc`` shape an unguarded ljust produces.
            assert value.startswith(" "), f"the value butts against the label: {row!r}"
            assert value.strip(), f"an empty value on {row!r}"

    def test_an_unconfigured_delivery_is_not_asked_the_identity_question(self, isolated_user_profile):
        """A blank host+username has no meaningful identity match — and "yes" would be worse."""
        report = _report()
        assert "n/a (delivery is not configured)" in report


class TestProvisionedMachine:
    def test_a_trusted_shared_folder_says_so(self, monkeypatch, tmp_path):
        """The report's most load-bearing line on a provisioned computer — the POSITIVE arm.

        Seeds ALL THREE of the trust predicate's raw reads: seeding fewer calls the real
        Win32 API, which passes on Windows and raises ``OSError`` → ``INACCESSIBLE`` on
        CI's Linux leg (PR #132).
        """
        root = tmp_path / "ProgramData" / "DistrictSync"
        (root / paths_module.MACHINE_RUNS_SUBDIR).mkdir(parents=True)
        monkeypatch.setattr(paths_module, "machine_data_dir", lambda: root)
        monkeypatch.setattr(
            paths_module, "_read_dir_security", lambda path: ("S-1-5-32-544", paths_module._SE_DACL_PROTECTED)
        )
        monkeypatch.setattr(paths_module, "_read_dacl_aces", lambda path: ((0, "S-1-5-18"), (0, "S-1-5-32-544")))

        report = _report()

        assert "trusted:" in report
        assert "yes (owner, inheritance and permissions all check out)" in report
        assert "owner SID" in report
        assert "S-1-5-32-544" in report

    def test_a_redirected_machine_root_is_named_not_silently_resolved(self, monkeypatch):
        """``WIN_PD_OVERRIDE_*`` redirects the machine root — the report must say so."""

        def _refuse() -> object:
            raise paths_module.MachineScopeRefused(
                paths_module.MachineScopeRefusedReason.REDIRECTED, "WIN_PD_OVERRIDE_X"
            )

        monkeypatch.setattr(paths_module, "machine_data_dir", _refuse)
        report = _report()

        assert "unavailable — redirected" in report

    def test_an_unreadable_ledger_is_told_apart_from_an_empty_one(self, monkeypatch):
        monkeypatch.setattr("src.history.store.read_run_records", lambda limit=None: None)
        report = _report()

        assert "the run store could not be read" in report
        assert "no runs recorded yet" not in report

    """The sections that only have content on a provisioned computer.

    Without these the most important half of the report — what a SHARED install looks like —
    is exercised nowhere: the registry read raises ``FileNotFoundError`` on every developer
    box and immediately on CI's Linux leg, so only the "key absent" arm would ever run.
    """

    def test_the_hklm_values_are_printed_when_the_switch_is_committed(self, monkeypatch):
        monkeypatch.setattr(
            diagnostics,
            "read_hklm_values",
            lambda: {"MachineScope": 1, "ProvisionedAt": "2026-09-17T10:00:00-06:00", "ProvisionedBy": "CORP\\admin"},
        )
        report = _report()

        assert "MachineScope:" in report
        assert "ProvisionedAt:" in report
        assert "2026-09-17T10:00:00-06:00" in report
        assert "CORP\\admin" in report

    def test_a_value_the_provisioner_never_wrote_reads_as_not_set(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "read_hklm_values", lambda: {"MachineScope": 1})
        report = _report()
        assert "ProvisionedBy:" in report
        assert "not set" in report

    def test_an_unreadable_key_is_reported_not_silently_absent(self, monkeypatch):
        monkeypatch.setattr(diagnostics, "read_hklm_values", lambda: (_ for _ in ()).throw(PermissionError("denied")))
        report = _report()
        assert "unreadable (PermissionError)" in report

    def test_the_shared_blob_is_reported_by_presence_never_by_content(self, monkeypatch, tmp_path):
        from src.sftp.secret_store import MachineSecretStore

        root = tmp_path / "ProgramData" / "DistrictSync"
        root.mkdir(parents=True)
        monkeypatch.setattr(paths_module, "machine_data_dir", lambda: root)
        monkeypatch.setattr("src.sftp.secret_store.select_store", lambda: MachineSecretStore())
        (root / "sftp_secret.bin").write_bytes(b"\x01\x02sealed-bytes-that-must-never-print\x03")

        report = _report()

        assert "shared file (sftp_secret.bin)" in report
        assert "secret file" in report
        assert "sealed-bytes" not in report


class TestHklmParity:
    """The reader's value names must be the WRITER's, or the report says "not set" forever."""

    def test_every_display_value_name_is_one_the_provisioner_writes(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "scheduler" / "provisioning.py").read_text(
            encoding="utf-8"
        )
        for name in diagnostics._HKLM_DISPLAY_VALUES:
            assert f'"{name}"' in source, f"--diagnose reads {name!r}, which nothing writes"

    def test_the_switch_name_is_imported_not_respelled(self):
        assert diagnostics.paths.MACHINE_SCOPE_VALUE_NAME not in diagnostics._HKLM_DISPLAY_VALUES

    def test_the_planted_twin_proves_the_scan_has_teeth(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "scheduler" / "provisioning.py").read_text(
            encoding="utf-8"
        )
        assert '"ProvisionedNowhere"' not in source


class TestScheduleSection:
    def test_the_recorded_principal_is_labelled_as_recorded_never_as_read_back(self, monkeypatch):
        """``ScheduleReadback`` does not carry the task's own UserId yet (that is S-3)."""
        monkeypatch.setattr(
            AppConfig, "load", classmethod(lambda cls: cls(schedule_run_as_user="CORP\\svc_districtsync"))
        )
        report = _report()

        assert "recorded run-as" in report
        assert "CORP\\svc_districtsync" in report

    def test_a_tri_state_read_back_is_rendered_in_plain_language(self, monkeypatch):
        for found, expected in ((True, "live"), (False, "not scheduled"), (None, "could not be read")):
            monkeypatch.setattr(
                "src.scheduler.windows.read_schedule", lambda name, found=found: ScheduleReadback(found=found)
            )
            assert expected in _report()
