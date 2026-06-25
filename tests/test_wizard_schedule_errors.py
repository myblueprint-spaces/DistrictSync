"""Tests for the Setup Wizard schedule-error classifier.

``_classify_schedule_error`` is a pure function in ``01_Setup_Wizard.py`` that
maps a (de-CLIXML'd) ``register_task`` failure message + the process elevation
state into a calm, actionable wizard message. It is keyed off the canonical
substrings ``register_task`` publishes (``"PowerShell not found"`` /
``"ScheduledTasks module not available"`` / Windows' ``"Access is denied"``).

The page module runs Streamlit page code at import; under pytest that executes
in "bare mode" (no ScriptRunContext) where ``st.*`` calls are no-ops, so we can
load it by file path and pull out the pure classifier. We do NOT exercise any
Streamlit rendering — only the pure function.
"""

import importlib.util
import logging
from pathlib import Path

import pytest

# Silence Streamlit's "missing ScriptRunContext" bare-mode warnings emitted by
# the page's top-level st.* calls during import.
logging.getLogger("streamlit").setLevel(logging.ERROR)

_WIZARD_PATH = Path(__file__).resolve().parents[1] / "src" / "ui" / "pages" / "01_Setup_Wizard.py"


@pytest.fixture(scope="module")
def classify():
    """Load _classify_schedule_error from the wizard page module by file path."""
    spec = importlib.util.spec_from_file_location("wizard_under_test", _WIZARD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._classify_schedule_error


class TestClassifyScheduleError:
    def test_not_elevated_access_denied_says_run_as_admin(self, classify):
        msg = classify("Access is denied.", elevated=False)
        assert "Run as administrator" in msg
        assert "administrator rights" in msg

    def test_elevated_access_denied_points_at_credentials(self, classify):
        msg = classify("Access is denied.", elevated=True)
        # Does NOT send an already-elevated admin in circles.
        assert "Run as administrator" not in msg
        assert "Log on as a batch job" in msg
        assert "Windows account password" in msg
        assert "PIN" in msg

    def test_powershell_not_found(self, classify):
        # Elevation is irrelevant here — the PS message wins regardless.
        for elevated in (True, False):
            msg = classify("PowerShell not found", elevated=elevated)
            assert "PowerShell wasn't found" in msg
            assert "Run as administrator" not in msg

    def test_scheduledtasks_module_missing(self, classify):
        for elevated in (True, False):
            msg = classify("ScheduledTasks module not available", elevated=elevated)
            assert "too old to schedule tasks" in msg
            assert "Run as administrator" not in msg

    def test_unknown_message_passes_through_clean(self, classify):
        msg = classify("The user name or password is incorrect.", elevated=True)
        assert msg == "Failed to register schedule: The user name or password is incorrect."

    def test_lowercase_access_denied_classified(self, classify):
        # Defensive: a lowercase "access denied" phrasing still classifies.
        msg = classify("access denied while registering", elevated=False)
        assert "Run as administrator" in msg
