"""Tests for helper functions in src/main.py.

Covers _check_anomalies, _emit_run_log, extract_required_files,
_sftp_upload, and _print_diff.
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pandas as pd

from src.etl.pipeline import (
    TransformOutputs,
    compute_anomalies,
    configured_entity_order,
    run_transform,
)
from src.main import (
    _check_anomalies,
    _emit_run_log,
    _print_diff,
    _sftp_upload,
    extract_required_files,
)

# -----------------------------------------------------------------------
# extract_required_files
# -----------------------------------------------------------------------


class TestExtractRequiredFiles:
    def test_collects_all_source_files_when_no_enabled_filter(self):
        config = MagicMock()
        entity1 = MagicMock()
        entity1.source_files = {"primary": "StudentDemo.txt", "schedule": "StudentSchedule.txt"}
        entity2 = MagicMock()
        entity2.source_files = {"primary": "StaffInfo.txt"}
        config.mappings = {"Students": entity1, "Staff": entity2}
        config.active_entities.return_value = {"Students", "Staff"}
        config.global_config.school_year_sources = {"primary": "StudentSchedule.txt"}

        files = extract_required_files(config)
        assert set(files) == {"StudentDemo.txt", "StudentSchedule.txt", "StaffInfo.txt"}

    def test_deduplicates_files(self):
        config = MagicMock()
        entity1 = MagicMock()
        entity1.source_files = {"primary": "Same.txt"}
        entity2 = MagicMock()
        entity2.source_files = {"primary": "Same.txt"}
        config.mappings = {"A": entity1, "B": entity2}
        config.active_entities.return_value = {"A", "B"}
        config.global_config.school_year_sources = {"primary": "Same.txt"}

        files = extract_required_files(config)
        assert len(files) == 1

    def test_filters_by_active_entities(self):
        """Inactive entities' source files must not appear — and a
        school_year_source not used by any active entity is dropped
        (determine_school_year falls back to the calendar-date heuristic).
        The enabled-selection rule itself is covered on
        ``MappingConfig.active_entities`` (test_config.py)."""
        config = MagicMock()
        students = MagicMock()
        students.source_files = {"primary": "StudentDemo.txt"}
        staff = MagicMock()
        staff.source_files = {"primary": "StaffInfo.txt"}
        classes = MagicMock()
        classes.source_files = {"primary": "StudentSchedule.txt", "info": "ClassInfo.txt"}
        config.mappings = {"Students": students, "Staff": staff, "Classes": classes}
        config.active_entities.return_value = {"Students"}
        config.global_config.school_year_sources = {"primary": "StudentSchedule.txt"}

        files = extract_required_files(config)
        assert set(files) == {"StudentDemo.txt"}

    def test_real_mbp_core_config_only_requires_three_files(self):
        """End-to-end check against the actual mbp_core_mapping.yaml."""
        from src.config.loader import load_config

        cfg = load_config("mbp_core")
        files = set(extract_required_files(cfg))
        # mbp_core enables Students + CourseInfo + StudentCourses
        assert files == {
            "StudentDemographicInformation.txt",
            "CourseInformation.txt",
            "StudentCourseHistory.txt",
            "StudentCourseSelection.txt",
        }

    def test_real_mbponly_config_requires_only_course_files(self):
        """mbponly emits only the two course CSVs, so no demographic GDE is needed."""
        from src.config.loader import load_config

        cfg = load_config("mbponly")
        files = set(extract_required_files(cfg))
        # CourseInfo + StudentCourses only — no StudentDemographicInformation.txt
        assert files == {
            "CourseInformation.txt",
            "StudentCourseHistory.txt",
            "StudentCourseSelection.txt",
        }


# -----------------------------------------------------------------------
# _check_anomalies
# -----------------------------------------------------------------------


class TestCheckAnomalies:
    def test_no_anomaly_when_no_previous_file(self, tmp_path):
        outputs = {"Students": pd.DataFrame({"id": range(100)})}
        warnings = _check_anomalies(outputs, tmp_path)
        assert warnings == []

    def test_no_anomaly_within_threshold(self, tmp_path):
        # Previous: 100 rows. New: 85 rows (15% drop, below 20% threshold).
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n" + "\n".join(str(i) for i in range(100)) + "\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(85)})}
        warnings = _check_anomalies(outputs, tmp_path)
        assert warnings == []

    def test_anomaly_when_large_drop(self, tmp_path):
        # Previous: 100 rows. New: 50 rows (50% drop, above 20% threshold).
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n" + "\n".join(str(i) for i in range(100)) + "\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(50)})}
        warnings = _check_anomalies(outputs, tmp_path)
        assert len(warnings) == 1
        assert "ANOMALY" in warnings[0]
        assert "Students" in warnings[0]

    def test_no_anomaly_when_previous_empty(self, tmp_path):
        # Previous: 0 rows (header only). Should not trigger.
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(10)})}
        warnings = _check_anomalies(outputs, tmp_path)
        assert warnings == []

    def test_unreadable_previous_warns_as_degradation(self, tmp_path):
        # A directory where a CSV is expected raises on open — surfaced LOUDLY as
        # an anomaly-check degradation (never a silent skip, never a hard failure).
        (tmp_path / "Students.csv").mkdir()

        outputs = {"Students": pd.DataFrame({"id": range(10)})}
        warnings = _check_anomalies(outputs, tmp_path)
        assert len(warnings) == 1
        assert warnings[0].startswith("ANOMALY: Students:")
        assert "could not be read" in warnings[0]

    def test_cli_wrapper_adds_anomaly_prefix_and_logs(self, tmp_path, caplog):
        """_check_anomalies is a thin CLI renderer over compute_anomalies: it
        prefixes 'ANOMALY:' and logs each shared base message at WARNING."""
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n" + "\n".join(str(i) for i in range(100)) + "\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(50)})}
        with caplog.at_level(logging.WARNING, logger="src.etl.pipeline"):
            warnings = _check_anomalies(outputs, tmp_path)

        # The shared base message (no prefix) plus the CLI's 'ANOMALY:' prefix.
        base = compute_anomalies(outputs, tmp_path)
        assert base == ["Students dropped from 100 to 50 rows (50% decrease)"]
        assert warnings == [f"ANOMALY: {base[0]}"]
        assert any("ANOMALY: Students dropped from 100 to 50" in r.message for r in caplog.records)


# -----------------------------------------------------------------------
# compute_anomalies (shared compute — single source consumed by CLI + UI)
# -----------------------------------------------------------------------


class TestComputeAnomalies:
    """Unit tests for the pure, surface-agnostic anomaly compute.

    compute_anomalies returns the SAME plain warning-string list that both the
    CLI (_check_anomalies wrapper) and the Convert page consume — no logging,
    no printing, no 'ANOMALY:' prefix. Each surface adds its own presentation.
    """

    def test_detects_large_drop(self, tmp_path):
        # Previous: 100 rows. New: 50 rows (50% drop, above the 20% threshold).
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n" + "\n".join(str(i) for i in range(100)) + "\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(50)})}
        warnings = compute_anomalies(outputs, tmp_path)

        # Plain base message — NO 'ANOMALY:' prefix, NO logging side-effects.
        assert warnings == ["Students dropped from 100 to 50 rows (50% decrease)"]
        assert not warnings[0].startswith("ANOMALY:")

    def test_no_previous_output_returns_empty(self, tmp_path):
        outputs = {"Students": pd.DataFrame({"id": range(100)})}
        assert compute_anomalies(outputs, tmp_path) == []

    def test_no_false_positive_under_threshold(self, tmp_path):
        # Previous: 100 rows. New: 85 rows (15% drop, below the 20% threshold).
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n" + "\n".join(str(i) for i in range(100)) + "\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(85)})}
        assert compute_anomalies(outputs, tmp_path) == []

    def test_unreadable_previous_file_warns_never_silently_skips(self, tmp_path):
        # A directory where a CSV is expected raises on open → an anomaly-check
        # degradation warning for that entity (loud), never a raise, never silence.
        (tmp_path / "Students.csv").mkdir()

        outputs = {"Students": pd.DataFrame({"id": range(10)})}
        assert compute_anomalies(outputs, tmp_path) == [
            "Students: the previous output file could not be read, so the drop check was skipped for this entity"
        ]

    def test_zero_row_output_frame_is_a_full_drop(self, tmp_path):
        # N→0 with the entity still IN outputs is caught by the drop leg itself.
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n1\n2\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": []})}
        assert compute_anomalies(outputs, tmp_path) == ["Students dropped from 2 to 0 rows (100% decrease)"]

    def test_empty_previous_file_is_not_an_anomaly(self, tmp_path):
        # Header-only previous file (0 data rows) is a missing baseline, not a drop.
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(10)})}
        assert compute_anomalies(outputs, tmp_path) == []

    def test_result_is_the_shared_list_both_surfaces_consume(self, tmp_path):
        """Both surfaces derive their warnings from this exact list: the CLI
        wrapper prefixes each base message; the Convert page renders each base
        message verbatim as 'Anomaly detected: {msg}'."""
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n" + "\n".join(str(i) for i in range(100)) + "\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(40)})}
        base = compute_anomalies(outputs, tmp_path)

        assert base == ["Students dropped from 100 to 40 rows (60% decrease)"]
        # CLI surface: thin wrapper prefixes 'ANOMALY:' over the shared base list.
        assert _check_anomalies(outputs, tmp_path) == [f"ANOMALY: {base[0]}"]
        # UI surface renders the SAME base message (st.warning(f"Anomaly detected: {msg}")).
        assert [f"Anomaly detected: {msg}" for msg in base] == [
            "Anomaly detected: Students dropped from 100 to 40 rows (60% decrease)"
        ]


# -----------------------------------------------------------------------
# compute_anomalies — vanished-entity leg (present→absent / N→0)
# -----------------------------------------------------------------------


class TestComputeAnomaliesVanish:
    """The vanished-entity leg: an entity the run was CONFIGURED to produce
    (``expected_entities`` — the enabled-entities-derived set from
    ``configured_entity_order``) that produced nothing while a non-empty
    previous CSV sits on disk. ``run_transform`` drops a zero-row entity from
    ``outputs`` entirely, so this leg is the only way a vanishing roster file
    surfaces to the anomaly check.
    """

    def test_present_to_absent_fires(self, tmp_path):
        prev = tmp_path / "Family.csv"
        prev.write_text("id\n" + "\n".join(str(i) for i in range(10)) + "\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(100)})}
        warnings = compute_anomalies(outputs, tmp_path, ["Students", "Family"])
        assert warnings == ["Family produced no output this run (previous run had 10 rows)"]

    def test_n_to_zero_fires_via_the_vanish_leg(self, tmp_path):
        # A zero-row transform never enters outputs (run_transform skips it), so
        # N→0 surfaces through the SAME present→absent leg.
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n1\n2\n3\n", encoding="utf-8")

        warnings = compute_anomalies({}, tmp_path, ["Students"])
        assert warnings == ["Students produced no output this run (previous run had 3 rows)"]

    def test_first_run_is_silent(self, tmp_path):
        # No previous CSVs on disk → nothing to vanish against (not an anomaly).
        outputs = {"Students": pd.DataFrame({"id": range(5)})}
        assert compute_anomalies(outputs, tmp_path, ["Students", "Family", "Classes"]) == []

    def test_empty_previous_file_is_silent(self, tmp_path):
        # Header-only previous file (0 data rows) is a missing baseline, not a vanish.
        (tmp_path / "Family.csv").write_text("id\n", encoding="utf-8")
        assert compute_anomalies({}, tmp_path, ["Family"]) == []

    def test_unreadable_previous_for_expected_entity_warns(self, tmp_path):
        # The degradation warning fires for a vanished entity's corrupt baseline too.
        (tmp_path / "Family.csv").mkdir()
        assert compute_anomalies({}, tmp_path, ["Family"]) == [
            "Family: the previous output file could not be read, so the drop check was skipped for this entity"
        ]

    def test_produced_entities_never_hit_the_vanish_leg(self, tmp_path):
        # An entity in outputs is judged by the drop leg only (within threshold → silent).
        prev = tmp_path / "Students.csv"
        prev.write_text("id\n" + "\n".join(str(i) for i in range(100)) + "\n", encoding="utf-8")

        outputs = {"Students": pd.DataFrame({"id": range(95)})}
        assert compute_anomalies(outputs, tmp_path, ["Students"]) == []

    def test_default_no_expected_entities_keeps_drop_only_behavior(self, tmp_path):
        # Callers that pass no expected set (back-compat) get the drop leg alone.
        prev = tmp_path / "Family.csv"
        prev.write_text("id\n1\n2\n", encoding="utf-8")
        assert compute_anomalies({}, tmp_path) == []


# -----------------------------------------------------------------------
# configured_entity_order (the enabled-entities-derived expected set)
# -----------------------------------------------------------------------


class TestConfiguredEntityOrder:
    """The single source of "what this run is configured to produce" — shared by
    ``run_transform`` (iteration) and the anomaly vanish leg (expectation).
    NEVER raw ``mappings.keys()`` when an ``enabled_entities`` filter is set."""

    def test_enabled_entities_controls_inclusion(self):
        mappings = {"Students": {}, "Staff": {}, "CourseInfo": {}}
        gc = {"entity_order": [], "enabled_entities": ["Students", "Staff"]}
        assert configured_entity_order(mappings, gc) == ["Students", "Staff"]

    def test_empty_enabled_means_all_mappings(self):
        # Back-compat: empty/absent enabled_entities → every mapping runs.
        mappings = {"Students": {}, "Staff": {}}
        gc = {"entity_order": [], "enabled_entities": []}
        assert configured_entity_order(mappings, gc) == ["Students", "Staff"]

    def test_entity_order_controls_ordering(self):
        mappings = {"Students": {}, "Staff": {}}
        gc = {"entity_order": ["Staff", "Students"], "enabled_entities": []}
        assert configured_entity_order(mappings, gc) == ["Staff", "Students"]

    def test_inherited_but_disabled_entity_is_not_expected(self):
        # The _base-inheritance foot-gun: CourseInfo lives in mappings but is NOT
        # enabled → it must not be "expected" (a different config's CSV sharing
        # the output dir would otherwise fire a false vanish anomaly).
        mappings = {"Students": {}, "CourseInfo": {}}
        gc = {"entity_order": [], "enabled_entities": ["Students"]}
        assert "CourseInfo" not in configured_entity_order(mappings, gc)


# -----------------------------------------------------------------------
# _emit_run_log
# -----------------------------------------------------------------------


class TestEmitRunLog:
    def test_emits_structured_log(self, caplog):
        outputs = {
            "Students": pd.DataFrame({"id": range(10)}),
            "Staff": pd.DataFrame({"id": range(5)}),
        }
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            _emit_run_log("success", 1.5, outputs)

        log_lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        assert len(log_lines) == 1
        payload = json.loads(log_lines[0].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["status"] == "success"
        assert payload["duration_s"] == 1.5
        assert payload["Students"] == 10
        assert payload["Staff"] == 5
        assert payload["sftp_attempted"] is False

    def test_emits_error_info(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            _emit_run_log("failed", 0.3, {}, error="boom")

        log_lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        payload = json.loads(log_lines[0].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["status"] == "failed"
        assert payload["error"] == "boom"

    def test_emits_sftp_status(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            _emit_run_log("success", 2.0, {}, sftp_attempted=True, sftp_ok=True)

        log_lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        payload = json.loads(log_lines[0].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["sftp_attempted"] is True
        assert payload["sftp_ok"] is True

    def test_emits_anomalies(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            _emit_run_log("success", 1.0, {}, anomalies=["Students dropped 50%"])

        log_lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        payload = json.loads(log_lines[0].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["anomalies"] == ["Students dropped 50%"]

    def test_clean_run_has_empty_data_errors(self, caplog):
        """A run with no field-transform errors carries an empty data_errors map."""
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            _emit_run_log("success", 1.0, {})

        log_lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        payload = json.loads(log_lines[0].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["data_errors"] == {}

    def test_emits_data_errors_summary_status_stays_success(self, caplog):
        """When field-transform errors occurred the summary is carried, but the
        ETL status stays `success` — data_errors is a separate axis."""
        summary = {"total": 3, "by_field": {"Students.Grade": 3}}
        with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
            _emit_run_log("success", 1.0, {}, data_errors=summary)

        log_lines = [r.message for r in caplog.records if "__DISTRICTSYNC_RUN__" in r.message]
        payload = json.loads(log_lines[0].split("__DISTRICTSYNC_RUN__ ")[1])
        assert payload["status"] == "success"
        assert payload["data_errors"] == summary


# -----------------------------------------------------------------------
# _sftp_upload
# -----------------------------------------------------------------------


class TestSftpUpload:
    def test_sftp_not_configured(self, tmp_path):
        """When AppConfig says SFTP is not configured, _sftp_upload returns False."""
        mock_cfg = MagicMock()
        mock_cfg.sftp_is_configured.return_value = False
        mock_app_config_cls = MagicMock()
        mock_app_config_cls.load.return_value = mock_cfg

        with patch("src.config.app_config.AppConfig.load", return_value=mock_cfg):
            result = _sftp_upload(str(tmp_path), manifest={"Students.csv"})
            assert result is False

    def test_sftp_missing_dependency(self):
        # If paramiko/keyring not installed, should return False gracefully
        with patch.dict("sys.modules", {"src.sftp.uploader": None, "src.sftp": None}):
            result = _sftp_upload("/output", manifest={"Students.csv"})
            assert result is False


# -----------------------------------------------------------------------
# _print_diff
# -----------------------------------------------------------------------


class TestPrintDiff:
    def test_diff_new_file(self, tmp_path, capsys):
        outputs = {"Students": pd.DataFrame({"id": [1, 2, 3]})}
        _print_diff(outputs, str(tmp_path))
        captured = capsys.readouterr()
        assert "NEW" in captured.out
        assert "Students" in captured.out

    def test_diff_existing_file(self, tmp_path, capsys):
        # Write existing file
        pd.DataFrame({"id": [1, 2]}).to_csv(tmp_path / "Students.csv", index=False)

        outputs = {"Students": pd.DataFrame({"id": [1, 2, 3, 4]})}
        _print_diff(outputs, str(tmp_path))
        captured = capsys.readouterr()
        assert "Students" in captured.out
        assert "2 -> 4" in captured.out
        assert "+2" in captured.out

    def test_diff_with_column_changes(self, tmp_path, capsys):
        pd.DataFrame({"id": [1], "old_col": ["x"]}).to_csv(tmp_path / "Staff.csv", index=False)

        outputs = {"Staff": pd.DataFrame({"id": [1], "new_col": ["y"]})}
        _print_diff(outputs, str(tmp_path))
        captured = capsys.readouterr()
        assert "Staff" in captured.out


# -----------------------------------------------------------------------
# run_transform
# -----------------------------------------------------------------------


class TestRunTransform:
    """Unit tests for the shared transform-orchestration extracted from run_pipeline.

    These use unregistered entity names so the generic DefaultTransformer (plain
    field_map application) runs — keeping the test focused on run_transform's
    orchestration (school-year set, enabled_entities, entity_order, empty-primary
    skip, field_orders collection) rather than any specific entity's logic.
    """

    @staticmethod
    def _global_config(**overrides):
        cfg = {
            "academic_start_month_day": "09-01",
            "academic_end_month_day": "06-30",
        }
        cfg.update(overrides)
        return cfg

    @staticmethod
    def _entity(source_file, field_map):
        return {
            "source_files": {"primary": source_file},
            "field_map": field_map,
        }

    def test_returns_transformoutputs_namedtuple(self):
        mappings = {"Widgets": self._entity("widgets.txt", {"Out": "in_col"})}
        raw_data = {"widgets.txt": pd.DataFrame({"in_col": ["a", "b"]})}

        result = run_transform(raw_data, mappings, self._global_config())

        assert isinstance(result, TransformOutputs)
        # Unpacks cleanly into (outputs, field_orders, data_errors)
        outputs, field_orders, data_errors = result
        assert "Widgets" in outputs
        assert list(outputs["Widgets"].columns) == ["Out"]
        # A clean run records no field-transform errors.
        assert data_errors == []

    def test_honors_enabled_entities(self):
        """A disabled entity is absent from outputs even though its source file
        is present in raw_data."""
        mappings = {
            "Widgets": self._entity("widgets.txt", {"Out": "in_col"}),
            "Gadgets": self._entity("gadgets.txt", {"Out": "in_col"}),
        }
        raw_data = {
            "widgets.txt": pd.DataFrame({"in_col": ["a"]}),
            "gadgets.txt": pd.DataFrame({"in_col": ["b"]}),
        }
        gc = self._global_config(enabled_entities=["Widgets"])

        outputs = run_transform(raw_data, mappings, gc).outputs

        assert set(outputs.keys()) == {"Widgets"}
        assert "Gadgets" not in outputs

    def test_respects_entity_order(self):
        mappings = {
            "Widgets": self._entity("widgets.txt", {"Out": "in_col"}),
            "Gadgets": self._entity("gadgets.txt", {"Out": "in_col"}),
        }
        raw_data = {
            "widgets.txt": pd.DataFrame({"in_col": ["a"]}),
            "gadgets.txt": pd.DataFrame({"in_col": ["b"]}),
        }
        gc = self._global_config(entity_order=["Gadgets", "Widgets"])

        outputs = run_transform(raw_data, mappings, gc).outputs

        assert list(outputs.keys()) == ["Gadgets", "Widgets"]

    def test_skips_entity_with_empty_primary_source(self):
        """An entity whose primary source frame is empty is skipped, not emitted."""
        mappings = {
            "Widgets": self._entity("widgets.txt", {"Out": "in_col"}),
            "Gadgets": self._entity("gadgets.txt", {"Out": "in_col"}),
        }
        raw_data = {
            "widgets.txt": pd.DataFrame({"in_col": ["a"]}),
            "gadgets.txt": pd.DataFrame({"in_col": []}),  # empty primary
        }

        outputs, field_orders, _ = run_transform(raw_data, mappings, self._global_config())

        assert "Widgets" in outputs
        assert "Gadgets" not in outputs
        assert "Gadgets" not in field_orders

    def test_skips_entity_with_missing_primary_source(self):
        """A referenced-but-absent primary source defaults to an empty frame and
        is skipped (no back-filling)."""
        mappings = {"Widgets": self._entity("not_uploaded.txt", {"Out": "in_col"})}
        raw_data: dict[str, pd.DataFrame] = {}

        outputs = run_transform(raw_data, mappings, self._global_config()).outputs

        assert outputs == {}

    def test_field_orders_derived_from_field_map_keys(self):
        """field_orders for each emitted entity is exactly its field_map key order."""
        mappings = {
            "Widgets": self._entity(
                "widgets.txt",
                {"First": "a", "Second": "b", "Third": "c"},
            )
        }
        raw_data = {"widgets.txt": pd.DataFrame({"a": ["1"], "b": ["2"], "c": ["3"]})}

        field_orders = run_transform(raw_data, mappings, self._global_config()).field_orders

        assert field_orders["Widgets"] == ["First", "Second", "Third"]
