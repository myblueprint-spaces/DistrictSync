"""DSYNC-64 (plan 0054 S5): the e2e test flavour exists for ONE script and can never reach a release.

The updater's end-to-end proof (``scripts/updater_e2e.py``) installs a build that trusts a loopback feed and a test key.
That build is the ``e2e`` flavour. These tests pin what it is, and that the release build cannot be pointed at loopback or
a test key: no environment variable, setting or stamp in any release path leads there.
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

from src.update import check, feed, health
from src.utils import build_flavor as bf
from tests.test_installed_flavour import _stamp, _unstamped

_ROOT = Path(__file__).resolve().parent.parent


def _stamp_target(monkeypatch, host: str = "127.0.0.1:1", keys: object = None) -> None:
    mod = types.ModuleType("src._e2e_target")
    mod.HOST = host  # type: ignore[attr-defined]
    mod.KEYS = {"k": "04" + "01" * 64} if keys is None else keys  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src._e2e_target", mod)


class TestTheFlavour:
    def test_e2e_is_a_known_flavour_that_counts_as_installed(self, monkeypatch):
        _stamp(monkeypatch, "e2e")
        assert bf.build_flavor() == "e2e" and bf.is_e2e_build() and bf.is_installed_build()

    def test_the_installed_flavour_is_not_e2e(self, monkeypatch):
        _stamp(monkeypatch, "installed")
        assert not bf.is_e2e_build() and bf.is_installed_build()

    def test_portable_is_neither(self, monkeypatch):
        _unstamped(monkeypatch)
        assert not bf.is_e2e_build() and not bf.is_installed_build()


class TestTheTarget:
    def test_the_e2e_build_reads_the_stamped_loopback_feed_and_test_key(self, monkeypatch):
        _stamp(monkeypatch, "e2e")
        _stamp_target(monkeypatch, "127.0.0.1:4321")
        target = feed.production_target()
        assert target is not None and target.host == "127.0.0.1:4321" and target.allow_loopback_http
        assert set(target.trusted_keys) == {"k"}

    def test_an_e2e_build_without_a_usable_stamp_checks_nothing(self, monkeypatch):
        _stamp(monkeypatch, "e2e")
        monkeypatch.setitem(sys.modules, "src._e2e_target", None)
        assert feed.production_target() is None
        _stamp_target(monkeypatch, keys={})
        assert feed.production_target() is None
        _stamp_target(monkeypatch, keys={"k": "not hex"})
        assert feed.production_target() is None

    @pytest.mark.parametrize("flavour", ["installed", "portable", None])
    def test_no_other_build_reads_the_stamp(self, monkeypatch, flavour):
        """A stray ``src/_e2e_target.py`` in a release build must be inert."""
        _unstamped(monkeypatch) if flavour is None else _stamp(monkeypatch, flavour)
        _stamp_target(monkeypatch)
        assert feed.production_target() is None  # production holds no host and no key yet
        assert feed.TRUSTED_KEYS == {}

    def test_the_data_dir_guard_is_relaxed_for_the_e2e_build_only(self, eligible, monkeypatch):
        monkeypatch.setattr(check.paths, "data_dir_override_active", lambda: True)
        monkeypatch.setattr(health.paths, "data_dir_override_active", lambda: True)
        assert check.skip_reason(target=object()) == "data_dir_override"  # installed flavour: skipped
        assert health.health_skip_reason() == "data_dir_override"
        _stamp(monkeypatch, "e2e")
        assert check.skip_reason(target=object()) != "data_dir_override"
        assert health.health_skip_reason() is None


class TestNoReleasePathCanReachIt:
    def _read(self, relative: str) -> str:
        return (_ROOT / relative).read_text(encoding="utf8")

    def test_loopback_is_enabled_in_exactly_one_place_in_src(self):
        hits = [
            str(p.relative_to(_ROOT))
            for p in (_ROOT / "src").rglob("*.py")
            if re.search(r"allow_loopback_http\s*=\s*True", p.read_text(encoding="utf8"))
        ]
        assert hits == [str(Path("src") / "update" / "feed.py")]

    def test_no_release_or_ci_workflow_stamps_the_e2e_flavour(self):
        for name in ("release.yml", "flet-pack.yml", "flet-verify.yml", "ci.yml"):
            text = self._read(f".github/workflows/{name}")
            assert "_e2e_target" not in text and "flavor = 'e2e'" not in text, name

    def test_only_the_e2e_script_stamps_it(self):
        stampers = [
            str(p.relative_to(_ROOT))
            for p in list((_ROOT / "scripts").glob("*.py")) + list((_ROOT / "src").rglob("*.py"))
            if "flavor = 'e2e'" in p.read_text(encoding="utf8")
        ]
        assert stampers == [str(Path("scripts") / "updater_e2e.py")]

    def test_the_stamped_files_are_git_ignored(self):
        ignored = self._read(".gitignore").splitlines()
        for name in ("/src/_build_flavor.py", "/src/_e2e_target.py", "/src/_version.py"):
            assert name in ignored

    def test_no_environment_variable_picks_the_feed_or_the_key(self):
        """The only environment variables the update code reads are the package's own off switch (in the package) and none here."""
        for path in (_ROOT / "src" / "update").glob("*.py"):
            text = path.read_text(encoding="utf8")
            assert "os.environ" not in text and "getenv" not in text, path.name
