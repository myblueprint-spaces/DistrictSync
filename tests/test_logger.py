"""Tests for the logging bootstrap fallback (``src/utils/logger.py``).

``logger.py`` itself is coverage-omitted (view-adjacent boot glue), but the
fallback path's ROTATION contract is cheap to pin: when ``logging.conf`` is
missing, ``get_logger`` must install a ``RotatingFileHandler`` with the SAME
limits as the normal fileConfig path (5 MB x 3 backups) — never a plain
``FileHandler`` that grows unbounded on a district server.
"""

import logging
import logging.handlers
import os

import pytest

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


# ---------------------------------------------------------------------------
# DSYNC-10: the NORMAL path (logging.conf present) must survive hostile paths.
# fileConfig eval()s handler ``args`` after interpolating the log path, so an
# apostrophe (C:\Users\O'Brien) crashed startup and a crafted name was code
# execution. The file handler is now built in code, never from a path-bearing
# eval'd string.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_loggers():
    root = logging.getLogger()
    etl = logging.getLogger("etl")
    before = (root.handlers[:], root.level, etl.handlers[:], etl.level, etl.propagate)
    try:
        yield
    finally:
        for lg in (root, etl):
            for h in lg.handlers:
                if h not in before[0] + before[2]:
                    h.close()
        root.handlers, root.level = before[0], before[1]
        etl.handlers, etl.level, etl.propagate = before[2], before[3], before[4]


def _flush_all():
    for lg in (logging.getLogger(), logging.getLogger("etl")):
        for h in lg.handlers:
            h.flush()


@pytest.mark.parametrize("dirname", ["O'Brien", "100%_done", "a'b", "it's %(x)s"])
def test_hostile_log_directory_names_do_not_crash(tmp_path, isolated_loggers, dirname):
    d = tmp_path / dirname
    d.mkdir()
    log = d / "etl_tool.log"
    logger_mod.get_logger("dsync10", log_file=log)

    logging.getLogger("etl").info("hello-dsync10")
    _flush_all()
    assert "hello-dsync10" in log.read_text(encoding="utf-8")

    rotating = [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(rotating) == 1
    assert rotating[0].maxBytes == 5_242_880
    assert rotating[0].backupCount == 3
    assert rotating[0].level == logging.INFO


def test_code_payload_in_path_is_inert_not_evaluated(tmp_path, isolated_loggers, monkeypatch):
    monkeypatch.delenv("DSYNC10_PWN", raising=False)
    # If this ever reached eval() it would set the env var as a side effect.
    d = tmp_path / "x'),__import__('os').environ.setdefault('DSYNC10_PWN','1'),('"
    try:
        d.mkdir()
    except OSError:
        pytest.skip("filesystem rejects the payload directory name")
    log = d / "etl_tool.log"
    logger_mod.get_logger("dsync10-payload", log_file=log)
    logging.getLogger("etl").info("payload-ok")
    _flush_all()
    assert "payload-ok" in log.read_text(encoding="utf-8")
    assert "DSYNC10_PWN" not in os.environ, "the path was evaluated as code"


def test_normal_path_keeps_console_and_etl_logger_wiring(tmp_path, isolated_loggers):
    logger_mod.get_logger("dsync10-wiring", log_file=tmp_path / "a.log")
    root, etl = logging.getLogger(), logging.getLogger("etl")
    assert root.level == logging.INFO
    assert etl.level == logging.INFO and not etl.propagate
    assert any(type(h) is logging.StreamHandler and h.level == logging.WARNING for h in root.handlers)
    assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in etl.handlers)
