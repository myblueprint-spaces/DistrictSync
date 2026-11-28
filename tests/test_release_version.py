"""DSYNC-113: one derivation of a release build's version, and the check that the installed app agrees with its package.

The plan 0055 lab found the MSI rehearsal's app printing ``DistrictSync 0.0.57`` from an MSI whose ProductVersion was
``0.57.1.0``: ``installer-build`` stamped ``0.0.<run>`` into the app while ``installer-msi`` packed the same folder as
``0.<run>.0`` and ``0.<run>.1``. ``scripts/release_version.py`` is now the ONE place the version is derived; these
tests pin its rules (written before the script, per the charter's red-first entry) and the CLI's ``--version`` line
it compares against (a literal copied out of ``src/``, so it gets a parity test).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location("release_version", _ROOT / "scripts" / "release_version.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rv = _load_script()


class TestThePackageVersion:
    """The version a build stamps into the app AND packs with vpk: the tag's, or the rehearsal's ``0.0.<run>``."""

    @pytest.mark.parametrize(("tag", "want"), [("v3.30.0", "3.30.0"), ("v0.0.1", "0.0.1"), ("v10.200.3000", "10.200.3000")])
    def test_a_tag_build_takes_the_tags_version(self, tag, want):
        assert rv.package_version("tag", tag, "57") == want

    def test_a_rehearsal_takes_a_throwaway_version_that_cannot_collide_with_a_release(self):
        assert rv.package_version("branch", "main", "57") == "0.0.57"

    @pytest.mark.parametrize(
        "tag", ["3.30.0", "v3.30", "v3.30.0.1", "v3.30.0-rc1", "v03.30.0", "v3.030.0", "vX", "v", "", " v3.30.0"]
    )
    def test_a_tag_that_is_not_vX_Y_Z_is_refused_loudly(self, tag):
        with pytest.raises(rv.VersionError):
            rv.package_version("tag", tag, "57")

    @pytest.mark.parametrize("run", ["0", "", "abc", "-1", "5.7", " 57"])
    def test_a_rehearsal_without_a_positive_run_number_is_refused(self, run):
        with pytest.raises(rv.VersionError):
            rv.package_version("branch", "main", run)

    @pytest.mark.parametrize("ref_type", ["", "TAG", "pull_request"])
    def test_an_unknown_ref_type_is_refused(self, ref_type):
        with pytest.raises(rv.VersionError):
            rv.package_version(ref_type, "v3.30.0", "57")


class TestTheMapping:
    """semver ``X.Y.Z`` -> MSI ProductVersion ``X.Y.Z.0`` -> ``--version`` prints ``DistrictSync X.Y.Z``."""

    @pytest.mark.parametrize(("semver", "want"), [("3.30.0", "3.30.0.0"), ("0.0.57", "0.0.57.0")])
    def test_the_msi_product_version_is_the_semver_with_a_zero_fourth_field(self, semver, want):
        assert rv.msi_product_version(semver) == want

    def test_the_msi_limits_are_the_largest_accepted(self):
        assert rv.msi_product_version("255.255.65535") == "255.255.65535.0"

    @pytest.mark.parametrize("semver", ["256.0.0", "0.256.0", "0.0.65536", "0.57", "0.57.1.0", "1.2.x", ""])
    def test_a_version_an_msi_cannot_carry_is_refused(self, semver):
        with pytest.raises(rv.VersionError):
            rv.msi_product_version(semver)

    def test_the_app_prints_the_semver_after_the_product_name(self):
        assert rv.app_version_line("3.30.0") == "DistrictSync 3.30.0"

    def test_the_old_rehearsal_scheme_could_not_have_passed(self):
        """The finding itself: the app's 0.0.57 maps to 0.0.57.0, never to the 0.57.1.0 the MSI carried."""
        assert rv.msi_product_version("0.0.57") != "0.57.1.0"


class TestTheMsiRehearsalPair:
    """MSI N+1 is the version stamped into the app (so N+1's app and package agree); N is one patch below it."""

    def test_n_plus_one_is_the_stamped_version_and_n_is_one_below(self):
        stamped = rv.package_version("branch", "main", "57")
        assert rv.msi_rehearsal_versions(stamped) == ("0.0.56", "0.0.57")

    def test_both_are_valid_msi_versions_and_n_is_lower(self):
        n, n1 = rv.msi_rehearsal_versions("0.0.60000")
        assert rv.msi_product_version(n) == "0.0.59999.0" and rv.msi_product_version(n1) == "0.0.60000.0"

    def test_there_is_no_version_below_patch_zero(self):
        with pytest.raises(rv.VersionError):
            rv.msi_rehearsal_versions("0.0.0")


class TestTheInstalledCheck:
    """``mismatches`` is the decision; ``check_installed`` feeds it the installed copy's three facts."""

    def test_all_agree(self):
        assert rv.mismatches("0.0.57", app_stdout="DistrictSync 0.0.57\n", package="0.0.57", msi="0.0.57.0") == []

    def test_all_agree_without_an_msi(self):
        assert rv.mismatches("3.30.0", app_stdout="DistrictSync 3.30.0", package="3.30.0", msi=None) == []

    def test_the_lab_finding_is_a_mismatch(self):
        found = rv.mismatches("0.57.1", app_stdout="DistrictSync 0.0.57", package="0.57.1", msi="0.57.1.0")
        assert len(found) == 1 and "0.0.57" in found[0]

    def test_a_package_mismatch_is_named(self):
        found = rv.mismatches("3.30.0", app_stdout="DistrictSync 3.30.0", package="3.29.0", msi=None)
        assert len(found) == 1 and "3.29.0" in found[0]

    def test_an_msi_mismatch_is_named(self):
        found = rv.mismatches("3.30.0", app_stdout="DistrictSync 3.30.0", package="3.30.0", msi="3.30.1.0")
        assert len(found) == 1 and "3.30.1.0" in found[0]

    def test_an_unreadable_fact_is_a_mismatch_never_a_pass(self):
        found = rv.mismatches("3.30.0", app_stdout="", package=None, msi="")
        assert len(found) == 3

    def test_check_installed_reads_the_real_files_and_fails_closed(self, tmp_path, monkeypatch):
        current = tmp_path / "current"
        current.mkdir()
        (current / "sq.version").write_bytes(
            b'<?xml version="1.0" encoding="utf-8"?>\n'
            b'<package xmlns="http://schemas.microsoft.com/packaging/2010/07/nuspec.xsd">'
            b"<metadata><id>myBlueprint.DistrictSync</id><version>0.0.57</version></metadata></package>"
        )
        monkeypatch.setattr(rv, "_run_version", lambda exe: "DistrictSync 0.0.57\n")
        monkeypatch.setattr(rv, "_installed_msi_product_version", lambda: "0.0.57.0")
        assert rv.check_installed(tmp_path, "0.0.57", msi=True) == []
        assert rv.check_installed(tmp_path, "0.0.57", msi=False) == []
        monkeypatch.setattr(rv, "_installed_msi_product_version", lambda: "0.57.1.0")
        assert rv.check_installed(tmp_path, "0.0.57", msi=True) != []
        assert rv.check_installed(tmp_path, "0.0.57", msi=False) == []  # the MSI is only read when asked for
        assert rv.check_installed(tmp_path / "absent", "0.0.57", msi=False) != []  # no sq.version -> fail


class TestTheCommandLine:
    @pytest.fixture
    def tag_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
        monkeypatch.setenv("GITHUB_REF_NAME", "v3.30.0")
        monkeypatch.setenv("GITHUB_RUN_NUMBER", "57")

    def test_version_prints_the_derived_version(self, tag_env, capsys):
        assert rv.main(["version"]) == 0
        assert capsys.readouterr().out.strip() == "3.30.0"

    def test_msi_versions_prints_the_pair(self, monkeypatch, capsys):
        monkeypatch.setenv("GITHUB_REF_TYPE", "branch")
        monkeypatch.setenv("GITHUB_REF_NAME", "main")
        monkeypatch.setenv("GITHUB_RUN_NUMBER", "57")
        assert rv.main(["msi-versions"]) == 0
        assert capsys.readouterr().out.strip() == "0.0.56 0.0.57"

    def test_stamp_writes_the_module_with_lf_only(self, tag_env, tmp_path):
        target = tmp_path / "_version.py"
        assert rv.main(["stamp", "--path", str(target)]) == 0
        assert target.read_bytes() == b"version = '3.30.0'\n"

    def test_a_missing_github_variable_fails_loudly(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_REF_TYPE", raising=False)
        monkeypatch.setenv("GITHUB_REF_NAME", "v3.30.0")
        monkeypatch.setenv("GITHUB_RUN_NUMBER", "57")
        assert rv.main(["version"]) == 1
        assert "GITHUB_REF_TYPE" in capsys.readouterr().err

    def test_a_bad_tag_fails_the_step(self, monkeypatch, capsys):
        monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
        monkeypatch.setenv("GITHUB_REF_NAME", "v3.30.0-rc1")
        monkeypatch.setenv("GITHUB_RUN_NUMBER", "57")
        assert rv.main(["version"]) == 1
        assert "v3.30.0-rc1" in capsys.readouterr().err

    def test_check_installed_exits_1_on_a_mismatch_and_0_when_all_agree(self, tag_env, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(rv, "check_installed", lambda root, expected, *, msi: [] if msi else ["app says 0.0.57"])
        assert rv.main(["check-installed", "--root", str(tmp_path), "--msi"]) == 0
        assert rv.main(["check-installed", "--root", str(tmp_path)]) == 1
        assert "app says 0.0.57" in capsys.readouterr().err


def test_the_app_line_is_what_the_real_cli_prints(monkeypatch, capsys):
    """Parity: ``app_version_line`` copies the ``--version`` format out of ``src/main.py``."""
    import src.main as main_module

    monkeypatch.setattr(main_module, "app_version", lambda: "9.8.7")
    assert main_module.cli(["--version"]) == 0
    assert capsys.readouterr().out.strip() == rv.app_version_line("9.8.7")
