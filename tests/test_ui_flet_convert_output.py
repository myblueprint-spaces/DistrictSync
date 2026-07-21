"""Unit tests for the COUNTED Convert output-folder + run/deliver-gate logic (Plan 0029, Slice 9).

Covers the path-bearing trust logic that keeps the silent fallbacks out (D9/D10):
- ``can_run_convert`` gating table (no district / empty output dir / invalid input / all set);
- ``output_dir_is_set`` blank/whitespace/None handling;
- ``resolved_output_caption`` derivation (set → names the folder; unset → routed message);
- ``open_folder`` per-OS dispatch (Windows/macOS/Linux) + blank-path and failure handling —
  all mocked, so no real file browser opens under test;
- the deliver-from-disk facts + gate (0034 Slice 2): ``output_csvs_present`` /
  ``newest_output_csv_mtime_iso`` (top-level-only, mirroring the SFTP glob) /
  ``freshness_fact`` / the ``standalone_deliver_state`` table.

The path lives HERE, never in ``ConvertResult`` — ``test_ui_flet_convert_result`` pins the
result model stays path-free; this module is its deliberate counterpart.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.ui_flet import convert_output
from src.ui_flet.convert_output import (
    DeliverReadiness,
    can_run_convert,
    deliverable_manifest,
    district_mismatch_note,
    freshness_fact,
    interaction_state,
    missing_files_copy,
    newest_output_csv_mtime_iso,
    open_folder,
    output_csvs_present,
    output_dir_is_set,
    resolved_output_caption,
    setup_first_copy,
    show_setup_first_card,
    standalone_deliver_state,
)


class TestCanRunConvert:
    """The run-gate is True ONLY when all three input gates are satisfied (no silent fallback)."""

    def test_all_gates_present_runs(self) -> None:
        assert can_run_convert(district_chosen=True, output_dir_set=True, input_valid=True) is True

    def test_no_district_blocks(self) -> None:
        # D9: no explicit district → refuse (no alphabetical configs[0] guess).
        assert can_run_convert(district_chosen=False, output_dir_set=True, input_valid=True) is False

    def test_empty_output_dir_blocks(self) -> None:
        # D10: no output folder → refuse (no silent write into the input folder).
        assert can_run_convert(district_chosen=True, output_dir_set=False, input_valid=True) is False

    def test_invalid_input_blocks(self) -> None:
        assert can_run_convert(district_chosen=True, output_dir_set=True, input_valid=False) is False

    def test_all_gates_missing_blocks(self) -> None:
        assert can_run_convert(district_chosen=False, output_dir_set=False, input_valid=False) is False


class TestOutputDirIsSet:
    @pytest.mark.parametrize("value", ["", "   ", "\t", None])
    def test_blank_is_not_set(self, value: str | None) -> None:
        assert output_dir_is_set(value) is False

    @pytest.mark.parametrize("value", ["/out", r"C:\Users\admin\output", "  /out  "])
    def test_real_path_is_set(self, value: str) -> None:
        assert output_dir_is_set(value) is True


class TestResolvedOutputCaption:
    def test_set_names_the_folder_and_routes_to_settings(self) -> None:
        caption = resolved_output_caption(r"C:\Users\admin\output")
        assert r"C:\Users\admin\output" in caption
        assert "Files will be written to" in caption
        assert "change it in Settings" in caption

    def test_set_trims_surrounding_whitespace(self) -> None:
        assert "Files will be written to /out —" in resolved_output_caption("  /out  ")

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_unset_shows_the_routed_blocked_message(self, value: str | None) -> None:
        caption = resolved_output_caption(value)
        assert "Set your output folder in Settings first" in caption


class TestOpenFolder:
    """Per-OS dispatch, fully mocked — no real file browser opens under test."""

    def test_blank_path_does_not_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list = []
        monkeypatch.setattr(convert_output.subprocess, "run", lambda *a, **k: calls.append(a))
        monkeypatch.setattr(convert_output.os, "startfile", lambda *a, **k: calls.append(a), raising=False)
        assert open_folder("") is False
        assert open_folder("   ") is False
        assert calls == []

    def test_windows_uses_startfile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list = []
        monkeypatch.setattr(convert_output.sys, "platform", "win32")
        monkeypatch.setattr(convert_output.os, "startfile", lambda p: recorded.append(p), raising=False)
        # subprocess must NOT be used on Windows.
        monkeypatch.setattr(convert_output.subprocess, "run", lambda *a, **k: pytest.fail("subprocess used on Windows"))
        assert open_folder(r"C:\out") is True
        assert recorded == [r"C:\out"]

    def test_macos_uses_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list = []
        monkeypatch.setattr(convert_output.sys, "platform", "darwin")
        monkeypatch.setattr(convert_output.subprocess, "run", lambda args, **k: recorded.append(args))
        assert open_folder("/out") is True
        assert recorded == [["open", "/out"]]

    def test_linux_uses_xdg_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list = []
        monkeypatch.setattr(convert_output.sys, "platform", "linux")
        monkeypatch.setattr(convert_output.subprocess, "run", lambda args, **k: recorded.append(args))
        assert open_folder("/out") is True
        assert recorded == [["xdg-open", "/out"]]

    def test_dispatch_failure_returns_false_and_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*_a, **_k):
            raise OSError("no display / no file browser")

        monkeypatch.setattr(convert_output.sys, "platform", "linux")
        monkeypatch.setattr(convert_output.subprocess, "run", _boom)
        assert open_folder("/out") is False  # calm degradation, not a crash


class TestOutputCsvsPresent:
    """The deliver gate's files-on-disk fact — top-level only, mirroring the SFTP glob."""

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank_dir_is_absent(self, value: str | None) -> None:
        assert output_csvs_present(value) is False

    def test_missing_dir_is_absent(self, tmp_path: Path) -> None:
        assert output_csvs_present(str(tmp_path / "nope")) is False

    def test_empty_dir_is_absent(self, tmp_path: Path) -> None:
        assert output_csvs_present(str(tmp_path)) is False

    def test_non_csv_files_do_not_count(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("x")
        assert output_csvs_present(str(tmp_path)) is False

    def test_top_level_csv_counts(self, tmp_path: Path) -> None:
        (tmp_path / "Students.csv").write_text("id\n1\n")
        assert output_csvs_present(str(tmp_path)) is True

    def test_nested_csv_does_not_count(self, tmp_path: Path) -> None:
        # archive_<ts>/ contents never ship (the SFTP glob is top-level) — the fact must agree.
        nested = tmp_path / "archive_20260701"
        nested.mkdir()
        (nested / "Students.csv").write_text("id\n1\n")
        assert output_csvs_present(str(tmp_path)) is False


class TestNewestOutputCsvMtimeIso:
    def test_no_csvs_is_empty(self, tmp_path: Path) -> None:
        assert newest_output_csv_mtime_iso(str(tmp_path)) == ""

    @pytest.mark.parametrize("value", ["", None])
    def test_blank_dir_is_empty(self, value: str | None) -> None:
        assert newest_output_csv_mtime_iso(value) == ""

    def test_newest_of_several_wins(self, tmp_path: Path) -> None:
        old = tmp_path / "Staff.csv"
        new = tmp_path / "Students.csv"
        old.write_text("id\n1\n")
        new.write_text("id\n1\n")
        old_ts = datetime(2026, 7, 1, 3, 0, 0).timestamp()
        new_ts = datetime(2026, 7, 14, 3, 0, 0).timestamp()
        os.utime(old, (old_ts, old_ts))
        os.utime(new, (new_ts, new_ts))
        assert newest_output_csv_mtime_iso(str(tmp_path)) == datetime.fromtimestamp(new_ts).isoformat(
            timespec="seconds"
        )

    def test_nested_csv_is_invisible(self, tmp_path: Path) -> None:
        nested = tmp_path / "archive_20260701"
        nested.mkdir()
        (nested / "Students.csv").write_text("id\n1\n")
        assert newest_output_csv_mtime_iso(str(tmp_path)) == ""


class TestFreshnessFact:
    _NOW = datetime(2026, 7, 15, 8, 0, 0)

    def test_names_the_vintage_plainly(self) -> None:
        two_hours_ago = (self._NOW - timedelta(hours=2)).isoformat(timespec="seconds")
        assert freshness_fact(two_hours_ago, now=self._NOW) == "Files last built 2 hours ago."

    def test_empty_mtime_degrades_to_recently(self) -> None:
        # TOTAL: an unstattable folder ("" upstream) still reads calmly, never raises.
        assert freshness_fact("", now=self._NOW) == "Files last built recently."


class TestStandaloneDeliverState:
    """The deliver-from-disk gate table (0034 Slice 2) — hidden / not-ready / ready."""

    def test_all_facts_present_is_ready(self) -> None:
        state = standalone_deliver_state(sftp_configured=True, credential_present=True, csvs_present=True)
        assert state is DeliverReadiness.READY

    def test_unconfigured_hides(self) -> None:
        state = standalone_deliver_state(sftp_configured=False, credential_present=True, csvs_present=True)
        assert state is DeliverReadiness.HIDDEN

    def test_no_csvs_hides(self) -> None:
        # Nothing to deliver → no affordance at all (never a dead button).
        state = standalone_deliver_state(sftp_configured=True, credential_present=True, csvs_present=False)
        assert state is DeliverReadiness.HIDDEN

    def test_missing_credential_is_the_calm_not_ready_state(self) -> None:
        state = standalone_deliver_state(sftp_configured=True, credential_present=False, csvs_present=True)
        assert state is DeliverReadiness.NEEDS_CREDENTIAL

    def test_missing_credential_with_no_csvs_still_hides(self) -> None:
        # Nothing to deliver dominates — don't nag about a credential for zero files.
        state = standalone_deliver_state(sftp_configured=True, credential_present=False, csvs_present=False)
        assert state is DeliverReadiness.HIDDEN


class TestResolvedOutputCaptionModeAware:
    """0035 W3b: the unset-output prompt is honest about WHICH surface owns the fix.

    Post-setup the fix lives on the graduated Settings scroll; pre-setup there IS no
    Settings scroll yet — the Setup wizard owns the folder, so the caption routes there.
    A SET output folder renders the normal caption in either mode.
    """

    def test_unset_pre_setup_routes_to_the_wizard(self) -> None:
        caption = resolved_output_caption("", setup_completed=False)
        assert caption == "Finish setup first — the Setup wizard will set your output folder."

    def test_unset_post_setup_keeps_the_settings_route(self) -> None:
        caption = resolved_output_caption("", setup_completed=True)
        assert "Set your output folder in Settings first" in caption

    def test_set_ignores_the_mode_axis(self) -> None:
        # A known folder is a known folder — the mode only matters for the unset prompt.
        for completed in (True, False):
            caption = resolved_output_caption("/out", setup_completed=completed)
            assert "Files will be written to /out" in caption

    def test_default_mode_is_post_setup(self) -> None:
        # Back-compat: existing call sites that never pass the kwarg keep the old copy.
        assert resolved_output_caption("") == resolved_output_caption("", setup_completed=True)


class TestShowSetupFirstCard:
    """The pre-setup routed-card gate (0035 W3b) — nag only when Setup genuinely owns the fix."""

    def test_pre_setup_with_nothing_saved_shows(self) -> None:
        assert show_setup_first_card(setup_completed=False, output_dir_set=False, district_saved=False) is True

    def test_pre_setup_missing_output_shows(self) -> None:
        assert show_setup_first_card(setup_completed=False, output_dir_set=False, district_saved=True) is True

    def test_pre_setup_missing_district_shows(self) -> None:
        assert show_setup_first_card(setup_completed=False, output_dir_set=True, district_saved=False) is True

    def test_pre_setup_with_essentials_in_place_stays_quiet(self) -> None:
        # A partially-set-up install whose district + output folder already work keeps the
        # usable form un-nagged — blocking it behind wizard completion would be a regression.
        assert show_setup_first_card(setup_completed=False, output_dir_set=True, district_saved=True) is False

    def test_completed_setup_never_shows(self) -> None:
        for output_set in (True, False):
            for district in (True, False):
                assert (
                    show_setup_first_card(setup_completed=True, output_dir_set=output_set, district_saved=district)
                    is False
                )


class TestSetupFirstCopy:
    def test_copy_is_calm_and_jargon_free(self) -> None:
        title, body = setup_first_copy()
        assert title == "Finish setup first"
        assert "district" in body and "folders" in body
        for jargon in ("SFTP", "GDE", "config", "sis_type"):
            assert jargon not in title
            assert jargon not in body

    def test_body_stands_alone_without_the_routed_button(self) -> None:
        # Defensive mounts render the card without "Open Setup" — the body must not
        # point at a button that may be absent.
        _title, body = setup_first_copy()
        assert "below" not in body.lower()
        assert "button" not in body.lower()


class TestDistrictMismatchNote:
    """The amber saved-vs-picked heads-up — fires ONLY on a real override (0035 W3b)."""

    def test_matching_pick_is_quiet(self, tmp_path: Path) -> None:
        assert district_mismatch_note("myedbc", "myedbc", config_dir=tmp_path) is None

    def test_no_pick_is_quiet(self, tmp_path: Path) -> None:
        assert district_mismatch_note(None, "myedbc", config_dir=tmp_path) is None
        assert district_mismatch_note("", "myedbc", config_dir=tmp_path) is None

    def test_no_saved_district_is_quiet(self, tmp_path: Path) -> None:
        # A fresh install has nothing saved — an explicit pick is just the pick, no nag.
        assert district_mismatch_note("myedbc", None, config_dir=tmp_path) is None
        assert district_mismatch_note("myedbc", "   ", config_dir=tmp_path) is None

    def test_whitespace_variants_of_the_same_district_are_quiet(self, tmp_path: Path) -> None:
        assert district_mismatch_note(" myedbc ", "myedbc", config_dir=tmp_path) is None

    def test_override_names_the_saved_district(self, tmp_path: Path) -> None:
        # config_dir is an empty dir → friendly_district_name falls back to the raw id
        # (TOTAL, hermetic — no dependency on the repo's real mapping files).
        note = district_mismatch_note("sd40myedbc", "sd99custom", config_dir=tmp_path)
        assert note is not None
        assert "differs from your saved district" in note
        assert "sd99custom" in note

    def test_note_names_the_saved_not_the_picked(self, tmp_path: Path) -> None:
        # The note explains what the NIGHTLY sync uses — the pick is already on screen.
        note = district_mismatch_note("sd40myedbc", "sd99custom", config_dir=tmp_path)
        assert note is not None
        assert "sd40myedbc" not in note


class TestMissingFilesCopy:
    def test_heading_softened_and_reassurance_is_honest(self) -> None:
        heading, reassurance = missing_files_copy()
        # The old alarm phrasing is gone; the new heading observes calmly.
        assert "Expected files not found" not in heading
        assert heading == "Not found yet — your district's extracts usually include:"
        # The reassurance states the real consequence: skip-on-empty, never guessed data.
        assert reassurance == "You can still convert — anything a missing file feeds is skipped, not guessed."


class TestInteractionState:
    """The busy/idle disabled table (0035 W3b) — the view paints exactly this."""

    def test_running_disables_everything_even_with_gates_ok(self) -> None:
        state = interaction_state(gates_ok=True, job_running=True)
        assert state.convert_disabled is True  # no double-start — mirrors the JobRunner guard
        assert state.inputs_disabled is True  # no mid-run edits desynchronizing the form

    def test_running_with_gates_unmet_also_disables_everything(self) -> None:
        state = interaction_state(gates_ok=False, job_running=True)
        assert state.convert_disabled is True
        assert state.inputs_disabled is True

    def test_idle_with_gates_ok_enables_everything(self) -> None:
        state = interaction_state(gates_ok=True, job_running=False)
        assert state.convert_disabled is False
        assert state.inputs_disabled is False

    def test_idle_with_gates_unmet_disables_only_the_button(self) -> None:
        # The inputs stay editable — that's HOW the admin satisfies the gates.
        state = interaction_state(gates_ok=False, job_running=False)
        assert state.convert_disabled is True
        assert state.inputs_disabled is False


class TestDeliverableManifest:
    """Deliver-from-disk's authoritative set: the ACTIVE CONFIG's entity CSVs on disk.

    A delivery-only run has no ``outputs`` to vouch for, so the config's enabled entities
    stand in — never the folder's ``*.csv`` glob (that is the bug this closes).
    """

    _ENTITIES = ["Students", "Staff", "Family", "Classes", "Enrollments"]

    def _folder(self, tmp_path: Path, names: list[str]) -> Path:
        for name in names:
            (tmp_path / name).write_text("col\nv\n", encoding="utf-8")
        return tmp_path

    def test_foreign_csvs_are_excluded(self, tmp_path: Path) -> None:
        folder = self._folder(
            tmp_path,
            [f"{e}.csv" for e in self._ENTITIES] + ["old_roster.csv", "students_backup.csv", "notes.csv"],
        )
        manifest = deliverable_manifest(self._ENTITIES, str(folder))
        assert manifest == {f"{e}.csv" for e in self._ENTITIES}

    def test_multi_run_folder_keeps_every_config_owned_csv(self, tmp_path: Path) -> None:
        """Back-compat: CSVs left by earlier runs of the SAME config all still deliver."""
        folder = self._folder(tmp_path, [f"{e}.csv" for e in self._ENTITIES])
        assert deliverable_manifest(self._ENTITIES, str(folder)) == {f"{e}.csv" for e in self._ENTITIES}

    def test_config_owned_entity_with_no_file_is_simply_absent(self, tmp_path: Path) -> None:
        """The folder legitimately holds only what past runs wrote — not a hard failure."""
        folder = self._folder(tmp_path, ["Students.csv", "Staff.csv"])
        assert deliverable_manifest(self._ENTITIES, str(folder)) == {"Students.csv", "Staff.csv"}

    def test_csv_owned_by_another_config_is_not_delivered(self, tmp_path: Path) -> None:
        """A CourseInfo.csv left by an mbp_all run is not this district's to ship."""
        folder = self._folder(tmp_path, ["Students.csv", "CourseInfo.csv", "StudentCourses.csv"])
        assert deliverable_manifest(self._ENTITIES, str(folder)) == {"Students.csv"}

    def test_blank_or_missing_folder_is_empty(self, tmp_path: Path) -> None:
        assert deliverable_manifest(self._ENTITIES, "") == set()
        assert deliverable_manifest(self._ENTITIES, str(tmp_path / "nope")) == set()

    def test_uses_the_loaders_entity_filename_rule(self, tmp_path: Path) -> None:
        """One spelling of entity→filename: the manifest and the write path agree."""
        from src.etl.loader import DataLoader

        folder = self._folder(tmp_path, ["Students.csv"])
        assert deliverable_manifest(["Students"], str(folder)) == {DataLoader.csv_filename("Students")}
