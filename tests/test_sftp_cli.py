"""Tests for the SFTP CLI subcommands in src/main.py.

Covers --sftp-configure (headless env var + stdin), --sftp-test,
--sftp-show, host allowlist validation, mutual exclusion between
subcommand flags, and precedence of password sources.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from src.main import (
    SFTP_PASSWORD_ENV_VAR,
    _read_sftp_password,
    _sftp_configure,
    _sftp_show,
    _sftp_test,
)


@pytest.fixture
def tmp_app_config(isolated_user_profile, monkeypatch):
    """Surface the isolated app-data path + capture keyring writes for assertions.

    AppConfig persistence is isolated suite-wide by the conftest autouse fixture
    (``paths.user_data_dir`` → tmp). This local ``store`` mock captures the exact
    keyring calls so tests can assert *which* password was stored under *which* key
    (layered on top of the suite-wide in-memory backend).
    """
    cfg_dir = isolated_user_profile
    cfg_file = cfg_dir / "config.json"

    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        "src.sftp.uploader.keyring.set_password",
        lambda service, user, pw: store.__setitem__((service, user), pw),
    )
    monkeypatch.setattr(
        "src.sftp.uploader.keyring.get_password",
        lambda service, user: store.get((service, user)),
    )
    return cfg_dir, cfg_file, store


def _args(**kwargs) -> argparse.Namespace:
    """Build a Namespace with the SFTP CLI defaults."""
    base = {
        "sftp_configure": False,
        "sftp_test": False,
        "sftp_show": False,
        "sftp_host": None,
        "sftp_port": 22,
        "sftp_user": None,
        "sftp_remote": None,
        "sftp_password_stdin": False,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


class TestReadSftpPassword:
    def test_env_var_takes_precedence(self, monkeypatch):
        monkeypatch.setenv(SFTP_PASSWORD_ENV_VAR, "from-env")
        assert _read_sftp_password(_args(sftp_password_stdin=True)) == "from-env"

    def test_stdin_used_when_no_env(self, monkeypatch, capsys):
        monkeypatch.delenv(SFTP_PASSWORD_ENV_VAR, raising=False)
        monkeypatch.setattr("sys.stdin.read", lambda: "from-stdin\n")
        assert _read_sftp_password(_args(sftp_password_stdin=True)) == "from-stdin"

    def test_empty_stdin_exits(self, monkeypatch):
        monkeypatch.delenv(SFTP_PASSWORD_ENV_VAR, raising=False)
        monkeypatch.setattr("sys.stdin.read", lambda: "")
        with pytest.raises(SystemExit) as exc:
            _read_sftp_password(_args(sftp_password_stdin=True))
        assert exc.value.code == 2


class TestSftpShow:
    def test_shows_empty_message_when_not_configured(self, tmp_app_config, capsys):
        assert _sftp_show(_args()) == 0
        assert "not configured" in capsys.readouterr().out.lower()

    def test_shows_saved_settings_without_password(self, tmp_app_config, monkeypatch, capsys):
        monkeypatch.setenv(SFTP_PASSWORD_ENV_VAR, "secret")
        _sftp_configure(
            _args(
                sftp_configure=True,
                sftp_host="sftp.ca.spacesedu.com",
                sftp_user="partner",
                sftp_remote="/files",
            )
        )
        capsys.readouterr()  # drain configure output

        assert _sftp_show(_args()) == 0
        out = capsys.readouterr().out
        assert "sftp.ca.spacesedu.com" in out
        assert "partner" in out
        assert "secret" not in out  # password never printed


class TestSftpConfigureHeadless:
    def test_saves_settings_and_password_from_env(self, tmp_app_config, monkeypatch):
        _, cfg_file, store = tmp_app_config
        monkeypatch.setenv(SFTP_PASSWORD_ENV_VAR, "hunter2")

        rc = _sftp_configure(
            _args(
                sftp_configure=True,
                sftp_host="sftp.ca.spacesedu.com",
                sftp_user="partner",
                sftp_remote="/files",
                sftp_port=2222,
            )
        )
        assert rc == 0
        assert cfg_file.exists()
        assert store == {("DistrictSync_SFTP", "partner"): "hunter2"}

    def test_rejects_disallowed_host(self, tmp_app_config, monkeypatch, capsys):
        monkeypatch.setenv(SFTP_PASSWORD_ENV_VAR, "x")
        rc = _sftp_configure(
            _args(
                sftp_configure=True,
                sftp_host="evil.example.com",
                sftp_user="partner",
                sftp_remote="/files",
            )
        )
        assert rc == 1
        assert "not allowed" in capsys.readouterr().out.lower()

    def test_stdin_password_mode(self, tmp_app_config, monkeypatch):
        _, _, store = tmp_app_config
        monkeypatch.delenv(SFTP_PASSWORD_ENV_VAR, raising=False)
        monkeypatch.setattr("sys.stdin.read", lambda: "piped-pw\n")

        rc = _sftp_configure(
            _args(
                sftp_configure=True,
                sftp_host="sftp.ca.spacesedu.com",
                sftp_user="partner",
                sftp_remote="/files",
                sftp_password_stdin=True,
            )
        )
        assert rc == 0
        assert store[("DistrictSync_SFTP", "partner")] == "piped-pw"


class TestSftpTest:
    def test_errors_when_not_configured(self, tmp_app_config, capsys):
        assert _sftp_test(_args()) == 1
        assert "not configured" in capsys.readouterr().out.lower()

    def test_propagates_uploader_result(self, tmp_app_config, monkeypatch, capsys):
        monkeypatch.setenv(SFTP_PASSWORD_ENV_VAR, "pw")
        _sftp_configure(
            _args(
                sftp_configure=True,
                sftp_host="sftp.ca.spacesedu.com",
                sftp_user="partner",
                sftp_remote="/files",
            )
        )
        capsys.readouterr()

        with patch("src.main.SFTPUploader.test_connection", return_value=(True, "ok")):
            assert _sftp_test(_args()) == 0
        assert "ok" in capsys.readouterr().out

        with patch("src.main.SFTPUploader.test_connection", return_value=(False, "boom")):
            assert _sftp_test(_args()) == 1
