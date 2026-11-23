"""DSYNC-67 (plan 0054 S6a) and DSYNC-87: the shape of the installer jobs in ``release.yml``.

The workflow is proven by running it (a dispatch, quoted in the pull request); these tests pin the properties that must
hold on every future edit: the installer is built on a dispatch (the rehearsal, which publishes nothing) and on a ``v*`` tag
(the release attaches it as ``DistrictSync-Setup.exe`` with its checksum - owner decision 2026-11-19, the R15 exception), no
job ever publishes a feed file, only the signing jobs hold ``id-token: write`` and both sit in the ``release`` environment,
the read key for the private package reaches only a checkout step, vpk matches the pinned ``velopack`` package, and the
one-folder pack cannot drift from the one in ``flet-pack.yml``.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests._pins import VELOPACK_VERSION

_WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf8"))


RELEASE = _load("release.yml")
JOBS = RELEASE["jobs"]
# PyYAML reads the bare key ``on`` as ``True``.
TRIGGERS = RELEASE.get("on", RELEASE.get(True))


def _steps_text(job: str) -> str:
    return "\n".join(str(s.get("run", "")) + str(s.get("with", "")) for s in JOBS[job]["steps"])


_PUBLISH_TEXT = (_WORKFLOWS / "release.yml").read_text(encoding="utf8").split("  publish-release:", 1)[1]


class TestWhenTheInstallerIsBuiltAndPublished:
    def test_both_installer_jobs_run_on_a_dispatch_and_on_a_version_tag_only(self):
        for job in ("installer-build", "installer-windows"):
            assert (
                JOBS[job]["if"] == "github.event_name == 'workflow_dispatch' || startsWith(github.ref, 'refs/tags/v')"
            ), job

    def test_publish_release_stays_tag_only_so_a_rehearsal_publishes_nothing(self):
        assert JOBS["publish-release"]["if"] == "startsWith(github.ref, 'refs/tags/v')"

    def test_publish_release_attaches_the_signed_setup_with_its_checksum(self):
        assert set(JOBS["publish-release"]["needs"]) == {"build-flet", "sign-windows", "installer-windows"}
        assert "name: DistrictSync-windows-installer-signed" in _PUBLISH_TEXT
        assert "artifacts/DistrictSync-Setup.exe" in _PUBLISH_TEXT  # the files: list
        assert "sha256sum DistrictSync-Setup.exe " in _PUBLISH_TEXT  # and SHA256SUMS.txt

    def test_no_feed_file_is_published(self):
        # Until S8 the build carries no feed host or trusted key; a published nupkg or manifest would be a feed nobody reads.
        for forbidden in (".nupkg", "releases.win.json", "manifest.json", "RELEASES", "az storage", "upload-batch"):
            assert forbidden not in _PUBLISH_TEXT, forbidden

    def test_the_release_body_says_automatic_updates_are_not_on(self):
        assert "Automatic updates are not switched on yet" in _PUBLISH_TEXT

    def test_a_tag_build_takes_the_tags_version_and_a_rehearsal_a_throwaway_one(self):
        build, sign = _steps_text("installer-build"), _steps_text("installer-windows")
        assert "${GITHUB_REF_NAME#v}" in build and "0.0.${{ github.run_number }}" in build
        assert "GITHUB_REF_NAME.TrimStart('v')" in sign and "0.0.${{ github.run_number }}" in sign

    def test_there_is_no_pull_request_trigger_and_the_tag_trigger_is_unchanged(self):
        assert "pull_request" not in TRIGGERS and "pull_request_target" not in TRIGGERS
        assert TRIGGERS["push"] == {"tags": ["v*"]} and "workflow_dispatch" in TRIGGERS

    def test_the_existing_jobs_are_all_still_there(self):
        assert {"ci-green", "build-flet", "sign-windows", "publish-release"} <= set(JOBS)


class TestWhoHoldsWhat:
    def test_only_the_signing_jobs_hold_id_token_and_both_are_in_the_release_environment(self):
        holders = {n for n, j in JOBS.items() if (j.get("permissions") or {}).get("id-token") == "write"}
        assert holders == {"sign-windows", "installer-windows", "installer-msi"}
        for name in holders:
            assert JOBS[name]["environment"] == "release", name

    def test_the_build_job_has_no_azure_scope_and_no_environment(self):
        build = JOBS["installer-build"]
        assert build["permissions"] == {"contents": "read"} and "environment" not in build
        assert "azure/login" not in str(build["steps"])

    def test_the_read_key_reaches_only_a_checkout_step(self):
        for name, job in JOBS.items():
            for step in job.get("steps", []):
                if "MYB_DESKTOP_SHARED_DEPLOY_KEY" in str(step):
                    assert str(step.get("uses", "")).startswith("actions/checkout@"), (name, step)
                    assert list(step["with"]) and "ssh-key" in step["with"]
        assert "MYB_DESKTOP_SHARED_DEPLOY_KEY" in str(JOBS["installer-build"]["steps"])  # positive twin

    def test_every_action_is_pinned_by_a_full_commit_sha(self):
        for name, job in JOBS.items():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if uses and not uses.startswith("./"):
                    assert re.search(r"@[0-9a-f]{40}\b", uses), (name, uses)


class TestThePackAndTheTools:
    def test_vpk_is_the_version_of_the_pinned_velopack_package(self):
        assert f"dotnet tool install --global vpk --version {VELOPACK_VERSION}" in _steps_text("installer-windows")

    def test_the_pack_id_and_title_are_the_decided_ones(self):
        assert JOBS["installer-windows"]["env"]["PACK_ID"] == "myBlueprint.DistrictSync"
        assert "--packTitle DistrictSync" in _steps_text("installer-windows")

    def test_the_signing_job_packs_signed_and_never_uploads_to_a_release_or_the_feed_itself(self):
        text = _steps_text("installer-windows")
        assert "--azureTrustedSignFile" in text
        for forbidden in ("gh release", "action-gh-release", "az storage", "upload-batch"):
            assert forbidden not in text

    def test_the_one_folder_pack_is_the_same_command_as_flet_packs(self):
        def pack_command(steps):
            hit = [s for s in steps if "flet pack src/main.py" in str(s.get("run", ""))]
            assert len(hit) == 1
            run = hit[0]["run"]
            run = run[run.index("flet pack") :]
            return re.sub(r"\s+", " ", run).strip()

        mine = pack_command(JOBS["installer-build"]["steps"])
        theirs = pack_command(_load("flet-pack.yml")["jobs"]["pack-windows-onedir"]["steps"])
        assert mine == theirs

    def test_the_installed_stamp_is_written_and_the_package_modules_are_asserted_present(self):
        text = _steps_text("installer-build")
        assert "flavor = 'installed'" in text
        for module in ("velopack", "myb_desktop_updater", "myb_start_guard", "ci_assert_frozen_modules"):
            assert module in text

    def test_the_signed_files_are_verified_with_a_timestamp_and_the_install_is_smoked_then_removed(self):
        text = _steps_text("installer-windows")
        assert "TimeStamperCertificate" in text and "signtool.exe" in text
        assert "--artifact installed" in text and "uninstall --silent" in text
        assert "flet_view" in text and "flet.exe" in text and "Update.exe" in text


def test_the_frozen_module_check_names_what_is_missing():
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "ci_assert_frozen_modules.py"
    spec = importlib.util.spec_from_file_location("ci_assert_frozen_modules", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.missing_modules({"a", "b"}, ["a", "c"]) == ["c"]
    assert module.missing_modules({"a"}, ["a"]) == []  # positive twin


class TestTheMachineWideMsiRehearsal:
    """Plan 0055 B1 (DSYNC-93): the per-machine MSI is built, signed, installed, upgraded and removed on a
    dispatch ONLY, and nothing it builds can reach a Release."""

    _JOB = "installer-msi"

    def test_it_runs_on_a_dispatch_only_never_on_a_tag(self):
        assert JOBS[self._JOB]["if"] == "github.event_name == 'workflow_dispatch'"

    def test_publish_release_does_not_need_it_so_it_can_never_stop_or_feed_a_release(self):
        assert self._JOB not in JOBS["publish-release"]["needs"]
        assert "msi" not in _PUBLISH_TEXT.lower()

    def test_it_signs_in_the_release_environment_with_the_pinned_vpk(self):
        job = JOBS[self._JOB]
        assert job["environment"] == "release"
        assert job["env"]["PACK_ID"] == "myBlueprint.DistrictSync"
        text = _steps_text(self._JOB)
        assert f"dotnet tool install --global vpk --version {VELOPACK_VERSION}" in text
        assert "--msi --instLocation PerMachine" in text
        assert "--packTitle DistrictSync" in text
        assert "--azureTrustedSignFile" in text

    def test_both_msis_are_verified_signed_with_a_timestamp(self):
        text = _steps_text(self._JOB)
        assert "verify /pa" in text and "TimeStamperCertificate" in text
        assert '@("msi-n.msi", "msi-n1.msi")' in text

    def test_it_installs_checks_the_acl_smokes_upgrades_and_uninstalls_keeping_profile_and_task(self):
        text = _steps_text(self._JOB)
        for needle in (
            "/i ",  # install
            "S-1-5-32-545",  # the ACL check names the Users SID
            "--cli-smoke --phase all --artifact installed",
            "--require-close",
            "msi-n1.msi",  # the upgrade
            "/x ",  # uninstall
            "DistrictSync-ci-standin",  # the stand-in profile survives
            "DistrictSync_CI_Standin",  # the stand-in task survives
            "install kind:",  # plan 0055 M1: the real install reports itself machine-wide
        ):
            assert needle in text, needle

    def test_the_rehearsal_artifact_is_short_lived_and_not_the_release_name(self):
        upload = [s for s in JOBS[self._JOB]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
        assert len(upload) == 1
        assert upload[0]["with"]["retention-days"] == 1
        assert upload[0]["with"]["name"] == "DistrictSync-windows-msi-rehearsal"
