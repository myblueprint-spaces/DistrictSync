"""Pins the report-only dependency CVE scan job in ci.yml (Jira DSYNC-8).

Read from the parsed YAML, never grepped from raw text. The job must exist, must stay
report-only (``continue-on-error``) until blocking mode is a deliberate decision, must
audit ``requirements.txt``, and must pin its actions by full commit SHA.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
_SHA_USE = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _job() -> dict:
    jobs = yaml.safe_load(_CI.read_text(encoding="utf-8"))["jobs"]
    assert "dependency-audit" in jobs
    return jobs["dependency-audit"]


def test_dependency_audit_job_is_report_only():
    assert _job()["continue-on-error"] is True


def test_dependency_audit_scans_runtime_requirements_with_pinned_tool():
    steps = "\n".join(s.get("run", "") for s in _job()["steps"])
    assert "pip-audit -r requirements.txt" in steps
    assert re.search(r"pip-audit==\d+\.\d+\.\d+", steps)


def test_dependency_audit_actions_pinned_by_sha():
    uses = [s["uses"] for s in _job()["steps"] if "uses" in s]
    assert uses
    assert all(_SHA_USE.match(u) for u in uses), uses
