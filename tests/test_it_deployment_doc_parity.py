"""Plan 0055 R1 (DSYNC-103): the IT deployment guide quotes the app; every quote and command is pinned to the source.

A guide that names a button, a choice or a command the app does not have sends an administrator looking for nothing,
so each literal the guide quotes is read from the module that produces it, and each command-line flag it shows must be
one ``main``'s parser accepts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "partner" / "it-deployment.md"


def _text() -> str:
    # One space for any run of whitespace and no blockquote markers, so a quote wrapped across lines still matches.
    raw = GUIDE.read_text(encoding="utf-8").replace("\n> ", " ")
    return re.sub(r"\s+", " ", raw)


def _quotes() -> dict[str, str]:
    from src.scheduler import machine_setup
    from src.ui_flet import machine_setup_window
    from src.update import setting
    from src.update.package import PACK_ID

    quotes = {
        "window title": machine_setup_window.WINDOW_TITLE,
        "primary button": machine_setup_window.PRIMARY_LABEL,
        "settings section": setting.SECTION_TITLE,
        "not set up (CLI)": machine_setup.NOT_SET_UP_CLI,
        "install folder": f"C:\\Program Files\\{PACK_ID}\\",
    }
    for mode, label in setting.MODE_LABELS.items():
        quotes[f"mode {mode}"] = label
    adml = (ROOT / "deploy" / "group-policy" / "en-US" / "DistrictSync.adml").read_text(encoding="utf-8")
    policy_name = re.search(r'<string id="UpdateMode">([^<]+)</string>', adml)
    assert policy_name
    quotes["policy name"] = policy_name.group(1)
    return quotes


@pytest.mark.parametrize("name", sorted(_quotes()))
def test_every_quote_is_the_apps_own_text(name):
    assert re.sub(r"\s+", " ", _quotes()[name]) in _text(), name


def test_every_flag_the_guide_shows_is_one_the_app_accepts():
    import src.main as main_mod

    shown = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", GUIDE.read_text(encoding="utf-8")))
    source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
    accepted = set(re.findall(r'"(--[a-z][a-z-]+)"', source))
    assert shown, "the scan found no flags: it is watching the wrong file"
    assert shown <= accepted, sorted(shown - accepted)
    assert main_mod.TASK_PASSWORD_ENV_VAR in GUIDE.read_text(encoding="utf-8")


def test_the_guide_states_what_is_not_live_yet():
    """The installer is not attached to a release yet, the update service is not live and U2's apply is not built: the
    guide says each, and never describes automatic installing in the present tense."""
    text = _text()
    assert "for the release that first ships it" in text
    assert "update checks are not live yet" in text
    assert "until then **On** behaves like **Notify**" in text
    assert "As designed, before installing anything DistrictSync will check" in text
    assert "new signed versions are installed automatically" not in text
