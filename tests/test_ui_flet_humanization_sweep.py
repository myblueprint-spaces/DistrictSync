"""Cross-surface humanization privacy sweep (IA-9, COUNTED — the invariant lock).

The trust invariant the whole Flet rebuild serves: NO admin-facing string a pure
derivation module emits may carry a raw filesystem path, a raw ``sis_type``, a raw ISO
timestamp, a raw ``ANOMALY:``-prefixed string, or a stack trace. Each prior slice tested
this LOCALLY; this sweep locks it ACROSS every pure derivation entry point at once so a
future edit can't regress one surface.

The approach is adversarial, not a smoke test: it plants recognizable SENTINELS
(``/secret/roster.csv``, ``SECRET_SIS``, a raw ISO ``2099-01-02T03:04:05``, an
``ANOMALY:``-prefixed string, and a ``Traceback``-shaped string) into the records / config
each pure module consumes, drives EVERY reachable status/branch (not just the happy path),
and asserts NONE of those substrings appears in ANY emitted string field. Modeled on
``convert_result.TestSummarizePrivacy`` + ``mapping_catalog``'s degraded-config sentinel
test — the proven shape, generalized to all surfaces.

Pure derivation → synthetic records + fixture mapping YAMLs via the ``config_dir`` seam.
No flet control instantiation (the views are coverage-omitted; the pure modules ARE the
leak surface that feeds them).
"""

from __future__ import annotations

import textwrap
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from src.config.app_config import AppConfig
from src.scheduler.windows import ScheduleReadback
from src.ui_flet.convert_result import ConvertResult, ConvertStatus, summarize
from src.ui_flet.home_status import derive_home_status
from src.ui_flet.mapping_catalog import list_configs, summarize_config
from src.ui_flet.run_history import derive_history_banner, to_run_row, to_run_rows
from src.ui_flet.schedule_status import ScheduleStatus, derive_schedule_status

# --------------------------------------------------------------------------- #
# The planted sentinels — any of these appearing in emitted copy is a LEAK.    #
# --------------------------------------------------------------------------- #
_SECRET_PATH = "/secret/roster.csv"
_SECRET_SIS = "SECRET_SIS"
_RAW_ISO = "2099-01-02T03:04:05"
_RAW_ANOMALY = "ANOMALY: Students /secret/roster.csv dropped from 200 to 1 rows"
_TRACEBACK = "Traceback (most recent call last):\n  File secret.py"

_SENTINELS: tuple[str, ...] = (_SECRET_PATH, _SECRET_SIS, _RAW_ISO, "ANOMALY:", "Traceback")


def _assert_no_sentinel(text: str, *, where: str) -> None:
    for sentinel in _SENTINELS:
        assert sentinel not in text, f"{where!r} leaked sentinel {sentinel!r}: {text!r}"


def _sweep_dataclass(obj: object, *, where: str) -> None:
    """Assert every string / tuple-of-string field of a frozen result dataclass is clean."""
    assert is_dataclass(obj)
    for f in fields(obj):
        value = getattr(obj, f.name)
        if isinstance(value, str):
            _assert_no_sentinel(value, where=f"{where}.{f.name}")
        elif isinstance(value, tuple):
            for item in value:
                if isinstance(item, str):
                    _assert_no_sentinel(item, where=f"{where}.{f.name}[]")


def _sweep_triple(triple: tuple[object, str, str], *, where: str) -> None:
    """Assert the ``(Verdict, headline, detail)`` triple's two strings are clean."""
    _verdict, headline, detail = triple
    _assert_no_sentinel(headline, where=f"{where}.headline")
    _assert_no_sentinel(detail, where=f"{where}.detail")


# A configured install so the derivations run their real rules (not an onboarding gate).
# `sis_type` is deliberately a valid id — the sentinel rides in via the RECORD's free-text
# fields (`error`/`anomalies`/`timestamp`), which is exactly where a leak would originate.
_CFG = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", schedule_registered=True)


def _poisoned_record(**overrides: object) -> dict:
    """A run record carrying EVERY sentinel in its free-text fields, plus per-test overrides.

    ``error`` (the emitter's ``str(e)`` — the exact path/sis/column risk), a raw ISO
    ``timestamp``, an ``ANOMALY:``-prefixed anomaly, and a ``sis_type`` all ride in. A clean
    derivation surfaces NONE of them.
    """
    base: dict = {
        "timestamp": _RAW_ISO,
        "status": "success",
        "error": f"FileNotFoundError: {_SECRET_PATH} sis={_SECRET_SIS}\n{_TRACEBACK}",
        "sis_type": _SECRET_SIS,
        "anomalies": [_RAW_ANOMALY],
        "duration_s": 3.2,
        "Students": 100,
        "Staff": 12,
        "Family": 80,
        "Classes": 40,
        "Enrollments": 300,
        "data_errors": {"total": 3, "by_field": {f"Students in {_SECRET_PATH}": 3}},
    }
    base.update(overrides)
    return base


# Each override drives a DIFFERENT reachable branch of the classify → verdict rules, so the
# sweep exercises every rule (failed-etl / failed-delivery / anomaly / data-warnings / clean /
# stale), not just the happy path. `_STALE_ISO` is a real (parseable) but old timestamp so the
# staleness rule fires on a clean record.
_STALE_ISO = "2000-01-01T00:00:00"

_BRANCH_OVERRIDES: tuple[dict, ...] = (
    {"status": "failed"},  # FAILED_ETL
    {"status": "success", "sftp_attempted": True, "sftp_ok": False, "anomalies": []},  # FAILED_DELIVERY
    {"status": "success", "anomalies": [_RAW_ANOMALY, _RAW_ANOMALY]},  # ANOMALY (plural detail)
    {"status": "success", "anomalies": [], "data_errors": {"total": 2}},  # DATA_WARNINGS
    {"status": "success", "anomalies": [], "data_errors": {"total": 0}},  # CLEAN (recent → healthy)
    {"status": "success", "anomalies": [], "data_errors": {"total": 0}, "timestamp": _STALE_ISO},  # CLEAN + stale
)


# The four schedule read-back states Home derives against (W3-B) — the attention flavors compose
# their copy into a FAILED detail, so the sweep must cover them too. Derived from the real
# ``derive_schedule_status`` (never hand-built) so the swept copy is the copy that ships.
_SCHEDULE_STATES: dict[str, ScheduleStatus | None] = {
    "unprobed": None,
    "live": derive_schedule_status(
        ScheduleReadback(found=True, next_run="2099-01-05T03:00:00"), hint_registered=True, latest_record_ts=None
    ),
    "expected-missing": derive_schedule_status(
        ScheduleReadback(found=False), hint_registered=True, latest_record_ts=None
    ),
    "contradiction": derive_schedule_status(
        ScheduleReadback(found=True, last_run="2099-01-03T03:04:05"),
        hint_registered=True,
        latest_record_ts=_RAW_ISO,
    ),
}


class TestHomeStatusSweep:
    def test_none_records_degradation_is_clean(self) -> None:
        _sweep_dataclass(derive_home_status(None, _CFG), where="derive_home_status(None)")

    def test_empty_records_is_clean(self) -> None:
        _sweep_dataclass(derive_home_status([], _CFG), where="derive_home_status([])")

    @pytest.mark.parametrize("override", _BRANCH_OVERRIDES)
    def test_every_branch_is_clean(self, override: dict) -> None:
        record = _poisoned_record(**override)
        status = derive_home_status([record], _CFG)
        _sweep_dataclass(status, where=f"derive_home_status({override})")

    @pytest.mark.parametrize("override", _BRANCH_OVERRIDES)
    @pytest.mark.parametrize("schedule", list(_SCHEDULE_STATES), ids=[k for k in _SCHEDULE_STATES])
    def test_every_branch_under_every_schedule_state_is_clean(self, override: dict, schedule: str) -> None:
        # W3-B: a FAILED latest under a confirmed-gone schedule now emits a COMBINED detail (the
        # failure sentence + the secondary schedule clause). Sweep every record branch × every
        # schedule read-back state so the composed copy can never become a leak vector.
        record = _poisoned_record(**override)
        status = derive_home_status([record], _CFG, schedule_status=_SCHEDULE_STATES[schedule])
        _sweep_dataclass(status, where=f"derive_home_status({override}, schedule={schedule})")


class TestHistoryBannerSweep:
    def test_none_records_degradation_is_clean(self) -> None:
        _sweep_dataclass(derive_history_banner(None, _CFG), where="derive_history_banner(None)")

    def test_empty_records_is_clean(self) -> None:
        _sweep_dataclass(derive_history_banner([], _CFG), where="derive_history_banner([])")

    @pytest.mark.parametrize("override", _BRANCH_OVERRIDES)
    def test_every_branch_is_clean(self, override: dict) -> None:
        record = _poisoned_record(**override)
        banner = derive_history_banner([record], _CFG)
        _sweep_dataclass(banner, where=f"derive_history_banner({override})")


class TestRunRowSweep:
    @pytest.mark.parametrize("override", _BRANCH_OVERRIDES)
    def test_to_run_row_every_branch_is_clean(self, override: dict) -> None:
        row = to_run_row(_poisoned_record(**override))
        _sweep_dataclass(row, where=f"to_run_row({override})")

    def test_to_run_rows_over_all_branches_is_clean(self) -> None:
        rows = to_run_rows([_poisoned_record(**o) for o in _BRANCH_OVERRIDES])
        for i, row in enumerate(rows):
            _sweep_dataclass(row, where=f"to_run_rows[{i}]")

    def test_run_row_has_no_error_field(self) -> None:
        # The strongest privacy shape — a raw error CAN'T be rendered because the field
        # simply does not exist on the row.
        row = to_run_row(_poisoned_record())
        assert not hasattr(row, "error")


class TestConvertSummarizeSweep:
    @pytest.mark.parametrize("status", list(ConvertStatus))
    def test_every_status_is_clean(self, status: ConvertStatus) -> None:
        # The anomalies tuple carries the sentinel-bearing raw strings + the quality_text
        # carries a path — summarize must surface NEITHER.
        result = ConvertResult(
            status=status,
            data_errors_total=3,
            anomalies=(_RAW_ANOMALY, f"Staff in {_SECRET_PATH} dropped"),
            quality_text=f"quality report referencing {_SECRET_PATH} sis={_SECRET_SIS}",
        )
        _sweep_triple(summarize(result), where=f"summarize({status})")


# --------------------------------------------------------------------------- #
# Mapping catalog — a broken config whose validation error carries a sentinel   #
# --------------------------------------------------------------------------- #
def _write_config(directory: Path, sis_type: str, body: str) -> None:
    (directory / f"{sis_type}_mapping.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


class TestMappingCatalogSweep:
    def test_degraded_config_never_echoes_the_sentinel(self, tmp_path: Path) -> None:
        # A Pydantic-invalid config whose error text WOULD carry the sentinel → a degraded
        # ConfigSummary that names the failure by category (loaded_ok=False), never the raw error.
        _write_config(
            tmp_path,
            _SECRET_SIS.lower(),
            f"""
            version: "1.0"
            sis: MyEducationBC
            district_name: Broken District
            global_config:
              course_start_grade: 999
              school_year_sources:
                "{_SECRET_PATH}": "{_SECRET_PATH}"
            mappings:
              Students:
                source_files:
                  a: A.txt
                field_map: {{}}
            """,
        )
        summary = summarize_config(_SECRET_SIS.lower(), config_dir=tmp_path)
        assert summary.loaded_ok is False
        _sweep_dataclass(summary, where="summarize_config(broken)")

    def test_valid_bundled_config_summary_is_clean(self, tmp_path: Path) -> None:
        # A valid config whose district_name / entity vocabulary is structural — no sentinel
        # can appear because none is present, but this pins the loaded_ok=True path is swept too.
        _write_config(
            tmp_path,
            "clean",
            """
            version: "1.0"
            sis: MyEducationBC
            district_name: Clean District
            mappings:
              Students:
                source_files:
                  student: StudentDemographicInformation.txt
                field_map: {}
            """,
        )
        summary = summarize_config("clean", config_dir=tmp_path)
        assert summary.loaded_ok is True
        _sweep_dataclass(summary, where="summarize_config(clean)")

    def test_list_configs_all_summaries_are_clean(self, tmp_path: Path) -> None:
        # A dir mixing a valid + a broken config — list_configs returns one summary per id,
        # some degraded; every field of every summary is swept.
        _write_config(
            tmp_path,
            "good",
            """
            version: "1.0"
            sis: MyEducationBC
            district_name: Good District
            mappings:
              Students:
                source_files:
                  s: S.txt
                field_map: {}
            """,
        )
        _write_config(
            tmp_path,
            _SECRET_SIS.lower(),
            f"""
            version: "1.0"
            sis: MyEducationBC
            district_name: Broken District
            global_config:
              course_start_grade: 999
              school_year_sources:
                "{_SECRET_PATH}": "{_SECRET_PATH}"
            mappings:
              Students:
                source_files:
                  a: A.txt
                field_map: {{}}
            """,
        )
        summaries = list_configs(config_dir=tmp_path)
        assert summaries  # both configs enumerated
        for summary in summaries:
            _sweep_dataclass(summary, where=f"list_configs → {summary.sis_type}")
