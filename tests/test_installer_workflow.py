"""DSYNC-67 (plan 0054 S6a): the shape of the installer rehearsal jobs in ``release.yml``.

The workflow is proven by running it (a dispatch, quoted in the pull request); these tests pin the properties that must
hold on every future edit: nothing here can touch a tag release, only the signing jobs hold ``id-token: write`` and both sit
in the ``release`` environment, the read key for the private package reaches only a checkout step, vpk matches the pinned
``velopack`` package, and the one-folder pack cannot drift from the one in ``flet-pack.yml``.
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


class TestTheRehearsalCannotTouchARelease:
    def test_both_installer_jobs_run_on_a_dispatch_only(self):
        for job in ("installer-build", "installer-windows"):
            assert JOBS[job]["if"] == "github.event_name == 'workflow_dispatch'", job

    def test_publish_release_does_not_wait_for_or_use_them(self):
        assert set(JOBS["publish-release"]["needs"]) == {"build-flet", "sign-windows"}
        text = (_WORKFLOWS / "release.yml").read_text(encoding="utf8")
        publish = text.split("  publish-release:", 1)[1]
        assert "installer" not in publish.lower()

    def test_there_is_no_pull_request_trigger_and_the_tag_trigger_is_unchanged(self):
        assert "pull_request" not in TRIGGERS and "pull_request_target" not in TRIGGERS
        assert TRIGGERS["push"] == {"tags": ["v*"]} and "workflow_dispatch" in TRIGGERS

    def test_the_existing_jobs_are_all_still_there(self):
        assert {"ci-green", "build-flet", "sign-windows", "publish-release"} <= set(JOBS)


class TestWhoHoldsWhat:
    def test_only_the_signing_jobs_hold_id_token_and_both_are_in_the_release_environment(self):
        holders = {n for n, j in JOBS.items() if (j.get("permissions") or {}).get("id-token") == "write"}
        assert holders == {"sign-windows", "installer-windows"}
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

    def test_it_packs_signed_and_never_uploads_to_a_release_or_the_feed(self):
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
        for module in ("velopack", "myb_desktop_updater", "myb_start_guard"):
            assert module in text

    def test_the_signed_files_are_verified_with_a_timestamp_and_the_install_is_smoked_then_removed(self):
        text = _steps_text("installer-windows")
        assert "TimeStamperCertificate" in text and "signtool.exe" in text
        assert "--artifact installed" in text and "uninstall --silent" in text
        assert "flet_view" in text and "flet.exe" in text and "Update.exe" in text
