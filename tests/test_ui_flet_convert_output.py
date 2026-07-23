"""Unit tests for the COUNTED Convert output-folder + run/deliver-gate logic (Plan 0029, Slice 9).

Covers the path-bearing trust logic that keeps the silent fallbacks out (D9/D10):
- ``can_run_convert`` gating table (no district / empty output dir / invalid input / all set);
- ``output_dir_is_set`` blank/whitespace/None handling;
- ``resolved_output_caption`` derivation (set → names the folder; unset → routed message);
- ``open_folder`` per-OS dispatch (Windows/macOS/Linux) + blank-path and failure handling —
  all mocked, so no real file browser opens under test;
- the deliver-from-disk facts + gate (0034 Slice 2, narrowed by FIX-4): ``deliverable_files``
  (the ONE district-narrowed derivation the readiness gate, the freshness line and the
  delivery payload all read) / ``freshness_fact`` / the ``standalone_deliver_state`` table;
- the run-identity binding for the anomaly acknowledgement (FIX-2): ``run_identity`` /
  ``ack_authorizes`` (fail-closed, OS-appropriate folder comparison) + the
  ``interaction_state(awaiting_ack=...)`` freeze that keeps the reviewed run reviewable.

The path lives HERE, never in ``ConvertResult`` — ``test_ui_flet_convert_result`` pins the
result model stays path-free; this module is its deliberate counterpart.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.ui_flet import convert_output
from src.ui_flet.convert_output import (
    DeliverReadiness,
    ResultDeliverReadiness,
    RunIdentity,
    ack_authorizes,
    can_run_convert,
    configured_output_entities,
    deliverable_files,
    deliverable_manifest,
    district_mismatch_note,
    freshness_fact,
    interaction_state,
    missing_files_copy,
    nothing_to_deliver_copy,
    open_folder,
    output_dir_is_set,
    resolved_output_caption,
    result_deliver_state,
    result_district_changed_copy,
    run_identity,
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


# The bundled district configs — passed explicitly so these tests can never be perturbed
# by a developer's `~/.districtsync/mappings/` override (load_config's first search dir).
_MAPPINGS_DIR = Path(__file__).resolve().parents[1] / "config" / "mappings"


def _seed(folder: Path, *names: str) -> Path:
    for name in names:
        (folder / name).write_text("id\n1\n", encoding="utf-8")
    return folder


class TestConfiguredOutputEntities:
    """The district → entity-name step — the ONE derivation the gate and the payload share."""

    def test_rostering_district_names_the_five_rostering_entities(self) -> None:
        entities = configured_output_entities("sd74myedbc", config_dir=_MAPPINGS_DIR)
        assert set(entities) == {"Students", "Staff", "Family", "Classes", "Enrollments"}

    def test_myblueprint_tier_names_only_its_two_entities(self) -> None:
        # `_base: myedbc` leaves the rostering entities in `mappings`, so a raw `mappings.keys()`
        # read would over-claim here — `enabled_entities` is the inclusion rule (CLAUDE.md).
        assert set(configured_output_entities("mbponly", config_dir=_MAPPINGS_DIR)) == {
            "CourseInfo",
            "StudentCourses",
        }

    def test_unloadable_district_raises_for_the_payload_path(self) -> None:
        # STRICT by design: `deliver_job` must fail LOUD on a config fault rather than
        # mislabel it "we couldn't send your files". Only the view gate degrades.
        with pytest.raises(Exception):  # noqa: B017 - FileNotFoundError today; any load fault must surface
            configured_output_entities("not_a_real_district", config_dir=_MAPPINGS_DIR)


class TestDeliverableFiles:
    """FIX-4: readiness + freshness derive from the SAME narrowed set the payload ships.

    The pre-FIX-4 gate was a bare ``glob("*.csv")`` with zero config awareness, so an output
    folder holding another config's CSVs rendered a READY card whose click could only ever
    fail (``upload_csvs`` refuses an empty manifest) — an unsatisfiable loop that also
    persisted a FAILED delivery record for a delivery that was never possible.
    """

    def _files(self, sis_type: str, folder: Path):
        return deliverable_files(sis_type, str(folder), config_dir=_MAPPINGS_DIR)

    # -- the reproduction ---------------------------------------------------- #
    def test_another_configs_csvs_are_not_deliverable(self, tmp_path: Path) -> None:
        # An earlier mbponly run's output, district since switched to sd74myedbc.
        _seed(tmp_path, "CourseInfo.csv", "StudentCourses.csv")
        files = self._files("sd74myedbc", tmp_path)
        assert files.filenames == frozenset()
        assert files.present is False

    def test_rostering_csvs_are_not_deliverable_under_a_myblueprint_tier(self, tmp_path: Path) -> None:
        _seed(tmp_path, "Students.csv", "Classes.csv")
        assert self._files("mbponly", tmp_path).present is False

    def test_a_lone_foreign_csv_is_not_deliverable(self, tmp_path: Path) -> None:
        _seed(tmp_path, "report.csv")
        assert self._files("sd74myedbc", tmp_path).present is False

    # -- the regression guard ------------------------------------------------ #
    def test_the_active_configs_csvs_are_deliverable(self, tmp_path: Path) -> None:
        _seed(tmp_path, "Students.csv", "Classes.csv")
        files = self._files("sd74myedbc", tmp_path)
        assert files.filenames == frozenset({"Students.csv", "Classes.csv"})
        assert files.present is True

    def test_only_the_config_owned_subset_of_a_mixed_folder_is_deliverable(self, tmp_path: Path) -> None:
        _seed(tmp_path, "Students.csv", "CourseInfo.csv", "report.csv")
        assert self._files("sd74myedbc", tmp_path).filenames == frozenset({"Students.csv"})

    # -- totality (a broken partner config must not crash the render path) ---- #
    def test_unloadable_district_degrades_to_empty(self, tmp_path: Path) -> None:
        _seed(tmp_path, "Students.csv")
        files = self._files("not_a_real_district", tmp_path)
        assert files.filenames == frozenset()
        assert files.newest_mtime_iso == ""

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank_district_degrades_to_empty(self, value: str | None) -> None:
        assert deliverable_files(value, "/out", config_dir=_MAPPINGS_DIR).present is False

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_blank_output_dir_is_empty(self, value: str | None) -> None:
        assert deliverable_files("sd74myedbc", value, config_dir=_MAPPINGS_DIR).present is False

    def test_missing_output_dir_is_empty(self, tmp_path: Path) -> None:
        assert self._files("sd74myedbc", tmp_path / "nope").present is False

    def test_empty_output_dir_is_empty(self, tmp_path: Path) -> None:
        assert self._files("sd74myedbc", tmp_path).present is False

    def test_non_csv_files_do_not_count(self, tmp_path: Path) -> None:
        (tmp_path / "Students.txt").write_text("x")
        assert self._files("sd74myedbc", tmp_path).present is False

    def test_nested_csv_does_not_count(self, tmp_path: Path) -> None:
        # archive_<ts>/ contents never ship (the SFTP glob is top-level) — the fact must agree.
        nested = tmp_path / "archive_20260701"
        nested.mkdir()
        _seed(nested, "Students.csv")
        assert self._files("sd74myedbc", tmp_path).present is False

    # -- the freshness line -------------------------------------------------- #
    def test_freshness_is_the_newest_deliverable_csv(self, tmp_path: Path) -> None:
        _seed(tmp_path, "Staff.csv", "Students.csv")
        old_ts = datetime(2026, 7, 1, 3, 0, 0).timestamp()
        new_ts = datetime(2026, 7, 14, 3, 0, 0).timestamp()
        os.utime(tmp_path / "Staff.csv", (old_ts, old_ts))
        os.utime(tmp_path / "Students.csv", (new_ts, new_ts))
        assert self._files("sd74myedbc", tmp_path).newest_mtime_iso == datetime.fromtimestamp(new_ts).isoformat(
            timespec="seconds"
        )

    def test_freshness_never_quotes_a_file_outside_the_manifest(self, tmp_path: Path) -> None:
        # The honesty bar: a foreign CSV dropped in yesterday must not be reported as the
        # vintage of the files that would actually ship.
        _seed(tmp_path, "Students.csv", "report.csv", "CourseInfo.csv")
        deliverable_ts = datetime(2026, 7, 1, 3, 0, 0).timestamp()
        foreign_ts = datetime(2026, 7, 20, 3, 0, 0).timestamp()
        os.utime(tmp_path / "Students.csv", (deliverable_ts, deliverable_ts))
        os.utime(tmp_path / "report.csv", (foreign_ts, foreign_ts))
        os.utime(tmp_path / "CourseInfo.csv", (foreign_ts, foreign_ts))
        assert self._files("sd74myedbc", tmp_path).newest_mtime_iso == datetime.fromtimestamp(deliverable_ts).isoformat(
            timespec="seconds"
        )

    def test_no_deliverable_csvs_has_no_vintage(self, tmp_path: Path) -> None:
        _seed(tmp_path, "report.csv")
        assert self._files("sd74myedbc", tmp_path).newest_mtime_iso == ""

    def test_unreadable_folder_degrades_to_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # TOTAL: an OS-level folder read failure hides the action, never crashes the render.
        def _boom(self: Path, _pattern: str):
            raise OSError("permission denied")

        monkeypatch.setattr(convert_output.Path, "glob", _boom)
        assert self._files("sd74myedbc", tmp_path).present is False

    def test_unstattable_deliverable_file_still_has_no_vintage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A file that vanishes between the glob and the stat (a run committing underneath the
        # read) degrades the vintage to "recently" downstream — the file itself still counts
        # as deliverable, so the action is not withdrawn over a transient stat failure.
        class _Unstattable:
            name = "Students.csv"

            def stat(self):
                raise OSError("vanished mid-read")

        monkeypatch.setattr(convert_output, "_top_level_csvs", lambda _dir: [_Unstattable()])
        files = self._files("sd74myedbc", tmp_path)
        assert files.filenames == frozenset({"Students.csv"})
        assert files.newest_mtime_iso == ""
        assert freshness_fact(files.newest_mtime_iso) == "Files last built recently."


class TestReadinessFromTheNarrowedSet:
    """The acceptance shape: the gate the screen paints, fed by the narrowed set.

    The screen composes exactly these two pure calls, so the dead-end can only return if
    someone re-widens the fact — which these pin.
    """

    def _readiness(self, sis_type: str, folder: Path) -> DeliverReadiness:
        files = deliverable_files(sis_type, str(folder), config_dir=_MAPPINGS_DIR)
        return standalone_deliver_state(
            sftp_configured=True,
            credential_present=True,
            csvs_present=files.present,
        )

    def test_rostering_csvs_under_a_myblueprint_tier_are_hidden(self, tmp_path: Path) -> None:
        _seed(tmp_path, "Students.csv", "Classes.csv")
        assert self._readiness("mbponly", tmp_path) is DeliverReadiness.HIDDEN

    def test_a_lone_foreign_csv_is_hidden(self, tmp_path: Path) -> None:
        _seed(tmp_path, "report.csv")
        assert self._readiness("mbponly", tmp_path) is DeliverReadiness.HIDDEN

    def test_the_active_configs_csvs_are_ready(self, tmp_path: Path) -> None:
        _seed(tmp_path, "CourseInfo.csv", "StudentCourses.csv")
        assert self._readiness("mbponly", tmp_path) is DeliverReadiness.READY


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

    def test_awaiting_ack_freezes_the_inputs(self) -> None:
        # FIX-2: while "some files look much smaller than usual" is on screen the card is
        # asking about ONE run — the district dropdown and the input picker must not be
        # editable underneath it (that is how an approval landed on an unreviewed run).
        state = interaction_state(gates_ok=True, job_running=False, awaiting_ack=True)
        assert state.inputs_disabled is True

    def test_awaiting_ack_leaves_convert_available_as_the_escape_hatch(self) -> None:
        # Starting over is legitimate — ``_start_convert`` clears the card and supersedes
        # the pending question, so the button must NOT be a dead end next to it.
        state = interaction_state(gates_ok=True, job_running=False, awaiting_ack=True)
        assert state.convert_disabled is False

    def test_awaiting_ack_defaults_to_false(self) -> None:
        # Every pre-existing caller keeps its exact behaviour (the axis is opt-in).
        assert interaction_state(gates_ok=True, job_running=False) == interaction_state(
            gates_ok=True, job_running=False, awaiting_ack=False
        )


class TestRunIdentityAndAckAuthorization:
    """FIX-2 — the anomaly acknowledgement is a capability scoped to ONE run.

    The defect this pins: the ack card snapshotted ``(district, input_dir)`` and never used
    them, while ``_set_running(False)`` re-enabled both controls underneath the card. An
    admin could review district A / folder A, repoint either input, click "I've reviewed
    this — convert anyway", and the write-gate opened for a run nobody had looked at.
    """

    _A = RunIdentity(district="myedbc", input_dir="/gde/nightly")

    def test_run_identity_strips_and_totalizes(self) -> None:
        assert run_identity("  myedbc ", "  /gde/nightly  ") == self._A
        assert run_identity(None, None) == RunIdentity(district="", input_dir="")

    def test_the_reviewed_run_is_authorized(self) -> None:
        assert ack_authorizes(self._A, run_identity("myedbc", "/gde/nightly")) is True

    def test_a_different_input_folder_is_refused(self) -> None:
        assert ack_authorizes(self._A, run_identity("myedbc", "/gde/other")) is False

    def test_a_different_district_is_refused(self) -> None:
        assert ack_authorizes(self._A, run_identity("sd48myedbc", "/gde/nightly")) is False

    def test_no_ack_authorizes_nothing(self) -> None:
        assert ack_authorizes(None, run_identity("myedbc", "/gde/nightly")) is False

    @pytest.mark.parametrize(
        "ack",
        [
            RunIdentity(district="", input_dir="/gde/nightly"),
            RunIdentity(district="myedbc", input_dir=""),
            RunIdentity(district="", input_dir=""),
        ],
    )
    def test_an_unidentifiable_ack_is_fail_closed(self, ack: RunIdentity) -> None:
        # A half-built token is never consent — and two blank identities must not
        # "match" each other into an authorization.
        assert ack_authorizes(ack, run_identity(ack.district, ack.input_dir)) is False

    def test_cosmetic_path_differences_still_authorize_the_same_folder(self) -> None:
        # A trailing separator / a redundant '.' segment is the SAME folder on every OS —
        # refusing it would nag the admin for a difference they cannot see.
        assert ack_authorizes(self._A, run_identity("myedbc", "/gde/nightly/")) is True
        assert ack_authorizes(self._A, run_identity("myedbc", "/gde/./nightly")) is True

    @pytest.mark.skipif(not sys.platform.startswith("win"), reason="case-insensitive paths are a Windows rule")
    def test_windows_treats_case_and_separators_as_the_same_folder(self) -> None:
        ack = RunIdentity(district="myedbc", input_dir=r"C:\GDE\Nightly")
        assert ack_authorizes(ack, run_identity("myedbc", "c:/gde/nightly")) is True

    @pytest.mark.skipif(sys.platform.startswith("win"), reason="case-sensitive paths are a POSIX rule")
    def test_posix_treats_a_case_difference_as_a_different_folder(self) -> None:
        ack = RunIdentity(district="myedbc", input_dir="/GDE/Nightly")
        assert ack_authorizes(ack, run_identity("myedbc", "/gde/nightly")) is False

    def test_the_district_id_is_never_case_folded(self) -> None:
        # Config ids are exact — a near-miss district must not be treated as reviewed.
        assert ack_authorizes(self._A, run_identity("MYEDBC", "/gde/nightly")) is False


class TestResultDeliverState:
    """The POST-RUN deliver gate (FIX-5) — the result card's counterpart to the standalone one.

    Every case is keyed on the RUN's district: the card describes one specific conversion,
    and the files it built are the only ones it may send, under the only name they may carry.
    """

    _READY = {
        "offerable": True,
        "sftp_configured": True,
        "files_present": True,
        "district_matches_pick": True,
        "credential_present": True,
    }

    def _state(self, **over) -> ResultDeliverReadiness:
        return result_deliver_state(**{**self._READY, **over})

    def test_everything_satisfied_offers_the_action(self) -> None:
        assert self._state() is ResultDeliverReadiness.READY

    def test_a_result_that_cannot_be_delivered_hides(self) -> None:
        # An already-delivered run (or one that wrote nothing) has no delivery to offer.
        assert self._state(offerable=False) is ResultDeliverReadiness.HIDDEN

    def test_no_delivery_setup_hides(self) -> None:
        assert self._state(sftp_configured=False) is ResultDeliverReadiness.HIDDEN

    def test_nothing_on_disk_hides(self) -> None:
        # `upload_csvs` refuses an empty manifest, so offering the click would guarantee a
        # failed-delivery record for a delivery that was never possible.
        assert self._state(files_present=False) is ResultDeliverReadiness.HIDDEN

    def test_a_moved_pick_withdraws_the_action_and_explains(self) -> None:
        # THE security case: sd74myedbc and sd40myedbc write identical filenames, so a card
        # left live across a pick change delivered successfully — under the wrong identity.
        assert self._state(district_matches_pick=False) is ResultDeliverReadiness.DISTRICT_CHANGED

    def test_a_moved_pick_outranks_a_missing_credential(self) -> None:
        # The district drift is the fact the admin needs; routing them to Setup for a
        # credential would answer a question they didn't ask.
        assert (
            self._state(district_matches_pick=False, credential_present=False)
            is ResultDeliverReadiness.DISTRICT_CHANGED
        )

    def test_nothing_to_deliver_outranks_a_moved_pick(self) -> None:
        # With nothing to send there is no delivery to pause — hide rather than explain a
        # restriction on an action that doesn't exist.
        assert self._state(files_present=False, district_matches_pick=False) is ResultDeliverReadiness.HIDDEN

    def test_a_missing_credential_routes_to_setup(self) -> None:
        assert self._state(credential_present=False) is ResultDeliverReadiness.NEEDS_CREDENTIAL


class TestResultDistrictChangedCopy:
    """The copy that REPLACES the withdrawn deliver action — never a dead end, never a lie."""

    def test_it_names_the_run_s_district_and_both_ways_forward(self, tmp_path: Path) -> None:
        # config_dir is an empty dir → friendly_district_name falls back to the raw id
        # (TOTAL, hermetic — no dependency on the repo's real mapping files).
        title, body = result_district_changed_copy("sd74myedbc", config_dir=tmp_path)
        assert title == "Delivery paused — you changed district"
        assert "sd74myedbc" in body
        assert "Switch the district back" in body  # way back
        assert "convert again" in body  # way onward

    def test_it_never_promises_a_cross_district_delivery(self, tmp_path: Path) -> None:
        _title, body = result_district_changed_copy("sd74myedbc", config_dir=tmp_path)
        assert "won't send them under another district's name" in body

    def test_a_blank_district_still_reads_as_a_sentence(self, tmp_path: Path) -> None:
        # TOTAL: the degenerate case must not render a hole in the copy.
        _title, body = result_district_changed_copy("", config_dir=tmp_path)
        assert "this district" in body
        assert "  " not in body


class TestNothingToDeliverCopy:
    """The choke point's refusal — states the honest reason and the concrete next step."""

    def test_it_names_the_district_and_the_next_step(self, tmp_path: Path) -> None:
        title, body = nothing_to_deliver_copy("mbponly", config_dir=tmp_path)
        assert title == "Nothing to deliver yet"
        assert "mbponly" in body
        assert "Convert for mbponly first" in body

    def test_it_never_asserts_a_vintage(self, tmp_path: Path) -> None:
        # The whole point: no "Files last built …" claim about files that don't exist.
        _title, body = nothing_to_deliver_copy("mbponly", config_dir=tmp_path)
        assert "last built" not in body

    def test_a_blank_district_still_reads_as_a_sentence(self, tmp_path: Path) -> None:
        _title, body = nothing_to_deliver_copy("", config_dir=tmp_path)
        assert "this district" in body
        assert "  " not in body
