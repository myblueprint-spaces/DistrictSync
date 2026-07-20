"""Tests for the logging bootstrap fallback (``src/utils/logger.py``).

``logger.py`` itself is coverage-omitted (view-adjacent boot glue), but the
fallback path's ROTATION contract is cheap to pin: when ``logging.conf`` is
missing, ``get_logger`` must install a ``RotatingFileHandler`` with the SAME
limits as the normal fileConfig path (5 MB x 3 backups) — never a plain
``FileHandler`` that grows unbounded on a district server.
"""

import logging
import logging.handlers

from src.utils import logger as logger_mod


def test_fallback_installs_rotating_file_handler_matching_the_normal_limits(tmp_path, monkeypatch):
    # Point the bundled-config lookup at a dir with NO logging.conf → the fallback branch.
    monkeypatch.setattr(logger_mod, "bundle_config_dir", lambda: tmp_path / "no_such_config_dir")
    monkeypatch.setattr(logger_mod, "user_log_file", lambda: tmp_path / "etl_tool.log")

    # basicConfig only configures a handler-less root — isolate root, restore after.
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    root.handlers = []
    try:
        logger_mod.get_logger("fallback-rotation-test")

        rotating = [h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert rotating, "the fallback must install a RotatingFileHandler (not a plain FileHandler)"
        handler = rotating[0]
        # Same limits as config/logging.conf's handler_fileHandler args (5 MB x 3).
        assert handler.maxBytes == 5_242_880
        assert handler.backupCount == 3
        # No unrotated plain FileHandler may sneak in alongside it.
        assert not any(
            isinstance(h, logging.FileHandler) and not isinstance(h, logging.handlers.RotatingFileHandler)
            for h in root.handlers
        )
    finally:
        for h in root.handlers:
            if h not in saved_handlers:
                h.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)
