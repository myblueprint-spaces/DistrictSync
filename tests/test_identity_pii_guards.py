"""The identity-PII containment guards (plan 0038 S3).

The admin's work email is personal data. It belongs on exactly two surfaces (Settings and
Help, both landing in S4a) and in the settings file. It must never reach a log line, a run
record, the durable run store, the ETL, or a CLI output — because those are the artifacts a
district pastes into a support ticket, a shared drive, or an audit export.

**Be precise about "the settings file" — it is not one file.** `AppConfig.save()` preserves
an unreadable predecessor byte-for-byte as `config.corrupt-<ts>.json` beside `config.json`
(see `_preserve_unreadable_predecessor`), and NOTHING prunes those copies. So a stored
identity survives in every quarantine snapshot taken after it was written, in the same
directory, indefinitely. That is deliberate for its own purpose — the copies exist so an
admin can recover settings by eye — but it means the honest containment model is
"`config.json` **and any `config.corrupt-*.json` sibling**", not one file. The consequence
lands on S4a: the Settings "blank clears" path must ALSO unlink those predecessors, or
clearing the address leaves it readable on disk (carried into the plan's S4a criteria).

These are BOUNDED REGRESSION GUARDS, and this docstring says so rather than letting the
green tick imply more. Each pins one specific escape route that is cheap to open by
accident. Together they do not prove containment; they make the four most likely
accidents loud:

* **the layer ban** (static) — the identity field NAMES may not appear in the ETL,
  run-store, SFTP, scheduler or quality layers at all. Cheapest possible guard, and it
  fires on the first line of the mistake rather than on its consequence.
* **the sink test** (runtime) — a poisoned stored identity, driven through a REAL pipeline
  run, reaches neither the `__DISTRICTSYNC_RUN__` log line nor the SQLite store.
* **the run-record key-set pin** — the record's keys are FROZEN, so adding a field to it
  is a deliberate act with a test to update, never a drive-by.
* **the CLI pins** — `--sftp-show` grows no identity line, and `src.utils.identity` never
  enters the CLI's imported-module graph (G1: identity does not touch the nightly sync).

Every absence-assertion here carries a POSITIVE twin, because "the canary was not found"
is equally satisfied by a guard that works and by a mechanism that never ran.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from src.config.app_config import AppConfig
from src.etl.pipeline import build_run_record, run_pipeline
from src.history.store import read_run_records

REPO_ROOT = Path(__file__).resolve().parents[1]

# The poisoned value. Uses an IANA-reserved TLD so it can never be a real address, and a
# distinctive local part so a PARTIAL leak (the person, without the domain) is caught too.
CANARY_EMAIL = "CANARY.ADMIN@leak-probe.invalid"
CANARY_LOCAL = "CANARY.ADMIN"
CANARY_SD = "CANARYSD"

IDENTITY_FIELD_NAMES = ("identity_email", "identity_prompt_dismissed", "identity_sd_number")

# Layers that must never learn the admin's identity exists. Chosen because each one
# either WRITES A DURABLE ARTIFACT or LEAVES THE MACHINE: the ETL emits CSVs a district
# uploads, the store is a queryable ledger, SFTP transmits, the scheduler bakes argv into
# an OS task, and quality builds reports people paste into tickets.
BANNED_LAYERS = ("src/etl", "src/history", "src/sftp", "src/scheduler", "src/quality", "src/main.py")


# --------------------------------------------------------------------------- #
# 1. The static layer ban                                                      #
# --------------------------------------------------------------------------- #
def _python_files(*relative_paths: str) -> list[Path]:
    """Every ``.py`` under each entry — an entry may be a DIRECTORY or a single FILE.

    ``src/main.py`` is in the list as a file: it is the CLI entry point, and the runtime
    import-graph pin below only observes a *module-scope* import. A function-local
    ``import src.utils.identity`` inside the ETL branch — the exact shape that would sneak
    identity onto the nightly-sync path — is loaded only when that branch RUNS, so the
    graph snapshot after `--sftp-show` would never see it. The static walk closes that
    blind spot; the two guards are complementary, not redundant.
    """
    files: list[Path] = []
    for entry in relative_paths:
        target = REPO_ROOT / entry
        files.extend([target] if target.is_file() else target.rglob("*.py"))
    return sorted(files)


def test_the_layer_ban_has_files_to_check() -> None:
    """Positive twin for the ban below: the walk really does find the layers."""
    files = _python_files(*BANNED_LAYERS)
    assert len(files) > 10, f"only {len(files)} files found — is the layer list stale?"


@pytest.mark.parametrize("layer", BANNED_LAYERS)
def test_identity_field_names_never_appear_in_a_durable_or_egress_layer(layer: str) -> None:
    """A grep-strength tripwire on the field NAMES, not their values.

    Deliberately blunt: any mention at all is a finding, including a comment. A layer that
    has no business knowing identity exists has no business naming it — and this fires on
    the first line of the mistake instead of on the leaked artifact it eventually produces.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}: {name}"
        for path in _python_files(layer)
        for name in IDENTITY_FIELD_NAMES
        if name in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"identity field names reached {layer}: {offenders}"


@pytest.mark.parametrize("layer", BANNED_LAYERS)
def test_the_identity_module_is_never_imported_by_those_layers(layer: str) -> None:
    """The stronger structural half: no import edge either."""
    offenders = []
    for path in _python_files(layer):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src.utils.identity")) or (
                isinstance(node, ast.Import) and any(a.name.startswith("src.utils.identity") for a in node.names)
            ):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert not offenders, f"src.utils.identity imported by {layer}: {offenders}"


def test_the_ban_would_actually_catch_a_violation(tmp_path: Path) -> None:
    """Falsification twin — prove the substring check is not vacuous."""
    planted = tmp_path / "leaky.py"
    planted.write_text("record['who'] = cfg.identity_email\n", encoding="utf-8")

    assert any(name in planted.read_text(encoding="utf-8") for name in IDENTITY_FIELD_NAMES)


# --------------------------------------------------------------------------- #
# 2. The run-record key-set pin                                                #
# --------------------------------------------------------------------------- #
# The exact keys of the ONE dict written to BOTH sinks. Frozen so a new field is a
# deliberate act with a test to update — the cheapest defence of the privacy split
# between the diagnostic log (rich) and the durable store (bounded).
FROZEN_RUN_RECORD_KEYS = frozenset(
    {
        "timestamp",
        "status",
        "source",
        "sis_type",
        "error_category",
        "duration_s",
        "sftp_attempted",
        "sftp_ok",
        "anomalies",
        "data_errors",
        "Students",
        "Staff",
        "Family",
        "Classes",
        "Enrollments",
        "CourseInfo",
        "StudentCourses",
        "StudentAttendance",
    }
)

# The store's own columns. Deliberately NARROWER than the record (the full record rides in
# the `record` JSON blob); an identity column appearing here would make the address
# queryable, indexable and exportable.
FROZEN_STORE_COLUMNS = frozenset(
    {"id", "timestamp", "sis_type", "source", "status", "error_category", "schema_version", "record"}
)


def test_run_record_key_set_is_frozen() -> None:
    record = build_run_record(
        status="success",
        elapsed=1.0,
        entity_counts={"Students": 3},
        source="cli",
        sis_type="myedbc",
        error_category="none",
    )
    assert set(record) == FROZEN_RUN_RECORD_KEYS


def test_no_run_record_key_is_identity_shaped() -> None:
    """A separate, weaker-but-broader assertion: nothing identity-ish, under any name."""
    assert not [k for k in FROZEN_RUN_RECORD_KEYS if "identity" in k.lower() or "email" in k.lower()]


def test_store_column_set_is_frozen(isolated_user_profile: Path) -> None:
    """Read from the REAL created schema, not from a copy of the DDL string."""
    import sqlite3

    from src.history.store import write_run_record
    from src.utils.paths import user_history_db

    record = build_run_record(
        status="success",
        elapsed=1.0,
        entity_counts={"Students": 1},
        source="cli",
        sis_type="myedbc",
        error_category="none",
    )
    assert write_run_record(record, source="cli") is True  # positive twin: the DB exists

    with sqlite3.connect(user_history_db()) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}

    assert columns == FROZEN_STORE_COLUMNS
    assert not [c for c in columns if "identity" in c.lower() or "email" in c.lower()]


# --------------------------------------------------------------------------- #
# 3. The end-to-end sink test                                                  #
# --------------------------------------------------------------------------- #
def _write_minimal_input(d: Path) -> None:
    """Minimal-but-complete myedbc rostering input (mirrors test_pipeline_run_store)."""
    pd.DataFrame(
        {
            "Student Number": ["S001", "S002"],
            "Legal First Name": ["Alice", "Bob"],
            "Legal Surname": ["Smith", "Jones"],
            "Date of birth": ["2010-01-15", "2009-06-20"],
            "Grade": ["10", "12"],
            "School Number": ["100", "100"],
            "Homeroom": ["A1", "A1"],
            "Previous school number": ["", ""],
            "Usual First Name": ["", ""],
            "Usual surname": ["", ""],
            "Student email address": ["alice@test.ca", "bob@test.ca"],
            "Enrolment Status": ["Active", "Active"],
            "Teacher Name": ["Ms. Harper", "Ms. Harper"],
            "Teacher ID": ["T001", "T001"],
        }
    ).to_csv(d / "StudentDemographicInformation.txt", index=False)
    pd.DataFrame(
        {
            "Student Number": ["S001", "S002"],
            "Student ID": ["S001", "S002"],
            "School Number": ["100", "100"],
            "School Year": ["2025/2026", "2025/2026"],
            "Grade": ["10", "12"],
            "Master Timetable ID": ["MT001", "MT002"],
            "Teacher ID": ["T001", "T001"],
            "Section Letter": ["A", "A"],
            "District Course Code": ["MAT10", "ENG12"],
            "Primary Teacher": ["Y", "Y"],
            "Teacher Name": ["Harper", "Harper"],
        }
    ).to_csv(d / "StudentSchedule.txt", index=False)
    pd.DataFrame(
        {
            "Teacher ID": ["T001"],
            "First Name": ["Jane"],
            "Last Name": ["Harper"],
            "Email Address": ["harper@school.ca"],
            "Teaching Staff": ["Y"],
            "School Number": ["100"],
        }
    ).to_csv(d / "StaffInformationEnhanced.txt", index=False)
    pd.DataFrame(
        {
            "School Number": ["100", "100"],
            "Course Code": ["MAT10", "ENG12"],
            "Title": ["Math 10", "English 12"],
        }
    ).to_csv(d / "CourseInformation.txt", index=False)
    pd.DataFrame(
        {
            "Student Number": ["S001"],
            "First Name": ["John"],
            "Last Name": ["Smith"],
            "Email Address": ["john@mail.com"],
        }
    ).to_csv(d / "EmergencyContactInformation.txt", index=False)
    pd.DataFrame(
        columns=["School Number", "Teacher ID", "Master Timetable ID", "Term", "Semester", "Day", "Period"]
    ).to_csv(d / "ClassInformationEnh.txt", index=False)


def test_a_poisoned_stored_identity_reaches_neither_sink(isolated_user_profile: Path, tmp_path, caplog) -> None:
    """A REAL pipeline run, with a REAL poisoned settings file, over both sinks at once.

    Drives the actual code path a nightly sync takes rather than calling the emitters
    directly, so a leak introduced anywhere between `AppConfig.load()` and either sink is
    caught — including one added by a future slice that decides the run record "should
    know who owns this install".
    """
    saved = AppConfig(
        input_dir=str(tmp_path / "in"),
        output_dir=str(tmp_path / "out"),
        sis_type="myedbc",
        identity_email=CANARY_EMAIL,
        identity_sd_number=CANARY_SD,
    )
    saved.save()
    assert CANARY_EMAIL in (isolated_user_profile / "config.json").read_text(encoding="utf-8")

    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    _write_minimal_input(input_dir)

    with caplog.at_level("DEBUG"):
        run_pipeline("myedbc", str(input_dir), str(output_dir), source="cli")

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    run_lines = [line for line in log_text.splitlines() if "__DISTRICTSYNC_RUN__" in line]
    stored = read_run_records()

    # Positive twins FIRST — prove both sinks actually ran, for a SUCCESSFUL run. A failed
    # run would emit a record and write no CSVs, so every absence below would pass for the
    # wrong reason (the "reached an output CSV" check especially).
    assert run_lines, "no __DISTRICTSYNC_RUN__ line was emitted; the log assertion would be vacuous"
    assert stored, "no run record was stored; the store assertion would be vacuous"
    logged = json.loads(run_lines[0].split("__DISTRICTSYNC_RUN__ ", 1)[1])
    assert (logged["sis_type"], logged["status"]) == ("myedbc", "success")
    assert stored[0]["status"] == "success"
    assert logged["Students"] > 0, "the run produced no students; the sinks carry nothing to leak"

    # ...then the absence assertions.
    for probe in (CANARY_EMAIL, CANARY_LOCAL, CANARY_SD, "leak-probe.invalid"):
        assert probe not in log_text, f"{probe!r} reached the diagnostic log"
        assert probe not in json.dumps(stored), f"{probe!r} reached the run store"

    written = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in output_dir.glob("*.csv"))
    assert written, "no CSVs were written; the output assertion would be vacuous"
    for probe in (CANARY_EMAIL, CANARY_LOCAL, CANARY_SD):
        assert probe not in written, f"{probe!r} reached an output CSV"


# --------------------------------------------------------------------------- #
# 4. The CLI pins (G1)                                                         #
# --------------------------------------------------------------------------- #
def test_sftp_show_grows_no_identity_line(isolated_user_profile: Path, monkeypatch, capsys) -> None:
    """`--sftp-show` prints saved settings to a terminal a partner routinely screenshots."""
    from src.main import _sftp_show

    AppConfig(
        sftp_enabled=True,
        sftp_host="sftp.ca.spacesedu.com",
        sftp_username="district_x",
        sftp_remote_path="/files",
        identity_email=CANARY_EMAIL,
        identity_sd_number=CANARY_SD,
    ).save()

    assert _sftp_show(object()) == 0  # noqa: PLW1508 - args is unused by this handler
    out = capsys.readouterr().out

    assert "sftp.ca.spacesedu.com" in out  # positive twin: it really printed the config
    for probe in (CANARY_EMAIL, CANARY_LOCAL, CANARY_SD, "identity"):
        assert probe not in out


def _module_graph(code: str, tmp_profile: Path) -> set[str]:
    """Run ``code`` in a FRESH interpreter and return the ``src.*`` modules it imported.

    A runtime graph, not a static approximation: it observes exactly what the interpreter
    actually loaded, including lazy imports a static walk would miss or over-count.
    """
    env = {**os.environ, "DISTRICTSYNC_DATA_DIR": str(tmp_profile), "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            code + "\nimport sys, json; print(json.dumps(sorted(m for m in sys.modules if m.startswith('src.'))))",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return set(json.loads(proc.stdout.strip().splitlines()[-1]))


def test_importing_the_cli_never_pulls_in_the_identity_module(tmp_path: Path) -> None:
    """G1 — identity does not touch the nightly sync.

    Honest about its strength: ``src.utils.identity`` has no importer in ``src/`` today, so
    this is green by construction right now. It exists to BITE LATER, when S4a/S5 wire
    identity into the UI and someone reaches for a primitive from a shared module the CLI
    also imports. The positive controls below keep it from being a vacuous check.
    """
    graph = _module_graph("import src.main", tmp_path)

    assert "src.utils.identity" not in graph
    assert "src.ui_flet.identity_gate" not in graph
    # Positive controls: the graph is real and populated.
    assert {"src.main", "src.config.app_config", "src.utils.validators"} <= graph


def test_running_a_real_cli_shape_never_pulls_in_the_identity_module(tmp_path: Path) -> None:
    """Stronger than import-time: the most identity-adjacent CLI shape actually RUNS.

    ``--sftp-show`` is the one non-ETL CLI path that loads ``AppConfig`` — i.e. the path
    that holds the identity fields in memory — so if any consumer were reachable from
    there, it would be loaded by the time this snapshot is taken.
    """
    graph = _module_graph(
        "import src.main; src.main._cli(['--sftp-show'])",
        tmp_path,
    )

    assert "src.utils.identity" not in graph
    assert {"src.main", "src.config.app_config"} <= graph


def test_the_graph_probe_can_detect_the_module(tmp_path: Path) -> None:
    """Falsification twin — the probe reports the module when it IS imported."""
    graph = _module_graph("import src.main; import src.utils.identity", tmp_path)

    assert "src.utils.identity" in graph
