"""Entry-path logging tests (D3) — every entry path configures the shared file sink.

``get_logger()`` moved out of import time: importing ``src.main`` must no longer
attach a handler to the real user log, and each entry path (CLI dispatch + Flet
launcher) must explicitly configure the sink so no session runs silently and every
run lands in ``etl_tool.log``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from src.utils import paths


def _root_file_handlers() -> list[logging.FileHandler]:
    return [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]


def _points_at(handlers: list[logging.FileHandler], expected: Path) -> bool:
    target = expected.resolve()
    return any(Path(h.baseFilename).resolve() == target for h in handlers)


def test_cli_entry_configures_isolated_file_sink(isolated_user_profile: Path) -> None:
    from src.main import _configure_cli_logging

    _configure_cli_logging()

    handlers = _root_file_handlers()
    assert handlers, "the CLI entry path must attach a file handler"
    expected = paths.user_log_file()
    assert _points_at(handlers, expected), "CLI logging must resolve through the paths seam"
    # ...and that resolved sink is the isolated tmp log, never the real profile.
    assert str(isolated_user_profile) in str(expected)


def test_launcher_entry_configures_isolated_file_sink(isolated_user_profile: Path) -> None:
    from src.ui_flet.launcher import boot_logging

    boot_logging()

    handlers = _root_file_handlers()
    assert handlers, "the Flet launcher entry path must attach a file handler"
    expected = paths.user_log_file()
    assert _points_at(handlers, expected), "launcher logging must resolve through the paths seam"
    assert str(isolated_user_profile) in str(expected)


def test_importing_main_does_not_configure_file_logging(tmp_path: Path) -> None:
    """Fresh interpreter: merely importing the CLI module must not open the log sink.

    This is the import-time-pollution regression guard (365 test records once leaked
    into the real log because ``src/main.py`` called ``get_logger`` at import). HOME is
    redirected so even a regression can't touch the real profile while we prove it.
    """
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["PYTHONPATH"] = str(repo_root)
    code = (
        "import logging, src.main;"
        "hs=[type(h).__name__ for h in logging.getLogger().handlers];"
        "print('HASFILE' if any('File' in h for h in hs) else 'CLEAN')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"import failed: {result.stderr}"
    assert "CLEAN" in result.stdout, (
        f"importing src.main configured a file log sink at import time: {result.stdout} {result.stderr}"
    )
