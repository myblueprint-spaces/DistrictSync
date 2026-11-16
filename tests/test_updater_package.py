"""DSYNC-62 (plan 0054 S3): how CI gets the private shared updater package, and that it cannot leak.

No private source is read here: these tests cover the pin script and the shape of the workflows.
The package-dependent behaviour is in ``tests/test_update_package_wiring.py``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_KEY = "MYB_DESKTOP_SHARED_DEPLOY_KEY"


def _load_script():
    spec = importlib.util.spec_from_file_location("updater_package", _ROOT / "scripts" / "updater_package.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


up = _load_script()


class TestPins:
    def test_shapes(self):
        assert re.fullmatch(r"[0-9a-f]{40}", up.PINNED_COMMIT)
        assert re.fullmatch(r"[0-9a-f]{64}", up.PINNED_WHEEL_SHA256)
        assert f"myb_desktop_updater-{up.PINNED_VERSION}-py3-none-any.whl" == up.WHEEL_NAME

    def test_build_tools_are_exact_pins(self):
        assert all("==" in tool for tool in up.BUILD_TOOLS)

    def test_requirements_updater_pins_cryptography_and_not_the_private_package(self):
        text = (_ROOT / "requirements-updater.txt").read_text(encoding="utf8")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        assert lines == ["cryptography==50.0.2"]  # one runtime dependency, exact

    def test_public_requirements_never_name_the_private_package(self):
        for name in ("requirements.txt", "requirements-dev.txt"):
            assert "myb_desktop_updater" not in (_ROOT / name).read_text(encoding="utf8").replace("-", "_")


class TestCheckCommit:
    def test_pinned_commit_passes(self):
        up.check_commit(up.PINNED_COMMIT + "\n")

    @pytest.mark.parametrize("other", ["0" * 40, up.PINNED_COMMIT[:8], "main", ""])
    def test_anything_else_is_refused(self, other):
        with pytest.raises(up.PinError):
            up.check_commit(other)


class TestCheckWheel:
    def _wheel(self, tmp_path: Path, payload: bytes = b"a wheel") -> tuple[Path, str]:
        path = tmp_path / up.WHEEL_NAME
        path.write_bytes(payload)
        return path, hashlib.sha256(payload).hexdigest()

    def test_matching_hash_passes(self, tmp_path):
        path, digest = self._wheel(tmp_path)
        up.check_wheel(path, digest)

    def test_a_changed_wheel_is_refused_with_both_hashes(self, tmp_path):
        path, digest = self._wheel(tmp_path)
        with pytest.raises(up.PinError) as info:
            up.check_wheel(path, "f" * 64)
        assert digest in str(info.value) and "f" * 64 in str(info.value)

    def test_a_missing_wheel_is_refused(self, tmp_path):
        with pytest.raises(up.PinError):
            up.check_wheel(tmp_path / "nope.whl")

    def test_cli_verify_wheel_exit_codes(self, tmp_path, capsys):
        path, _ = self._wheel(tmp_path)
        assert up.main(["verify-wheel", str(path)]) == 1  # not the pinned hash
        assert "FAILED" in capsys.readouterr().err

    def test_cli_commit_prints_the_pin(self, capsys):
        assert up.main(["commit"]) == 0
        assert capsys.readouterr().out.strip() == up.PINNED_COMMIT


class TestInstallRefusesBeforeTouchingPip:
    @pytest.fixture
    def calls(self, monkeypatch):
        recorded: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            recorded.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(up, "_run", fake_run)
        return recorded

    def test_a_wheel_with_the_wrong_hash_is_never_installed(self, tmp_path, calls):
        wheel = tmp_path / up.WHEEL_NAME
        wheel.write_bytes(b"tampered")
        assert up.main(["install", "--wheel", str(wheel)]) == 1
        assert calls == []

    def test_a_wheel_with_the_pinned_hash_is_installed_without_dependencies(self, tmp_path, calls, monkeypatch):
        """Positive twin of the refusal above."""
        wheel = tmp_path / up.WHEEL_NAME
        wheel.write_bytes(b"the pinned wheel")
        monkeypatch.setattr(up, "PINNED_WHEEL_SHA256", hashlib.sha256(b"the pinned wheel").hexdigest())
        assert up.main(["install", "--wheel", str(wheel)]) == 0
        assert len(calls) == 1 and "--no-deps" in calls[0] and str(wheel) in calls[0]

    def test_a_checkout_at_another_commit_is_refused_before_building(self, tmp_path, monkeypatch):
        recorded: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            recorded.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="0" * 40 + "\n", stderr="")

        monkeypatch.setattr(up, "_run", fake_run)
        with pytest.raises(up.PinError):
            up.build_wheel(tmp_path, tmp_path / "out")
        assert [c[0] for c in recorded] == ["git"]  # only the rev-parse ran: no pip, no build


def _workflow(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf8"))


def _steps(job: dict) -> list[dict]:
    return job["steps"]


def _shared_checkout(job: dict) -> dict:
    found = [s for s in _steps(job) if str(s.get("with", {}).get("repository", "")).endswith("myb-desktop-shared")]
    assert len(found) == 1
    return found[0]


class TestWorkflowShape:
    @pytest.mark.parametrize("job_name", ["test", "test-windows"])
    def test_the_shared_package_is_fetched_safely(self, job_name):
        job = _workflow("ci.yml")["jobs"][job_name]
        step = _shared_checkout(job)
        assert step["if"] == "steps.updater-key.outputs.present == 'true'"
        assert step["with"]["persist-credentials"] is False
        assert step["with"]["ref"] == "${{ steps.updater-pin.outputs.commit }}"  # never a branch or tag
        assert step["with"]["ssh-key"] == "${{ secrets." + _KEY + " }}"
        installs = [s for s in _steps(job) if "updater_package.py install" in s.get("run", "")]
        assert len(installs) == 1
        assert "requirements-updater.txt" in installs[0]["run"]
        assert "--source .shared-updater" in installs[0]["run"]
        assert installs[0]["if"] == "steps.updater-key.outputs.present == 'true'"

    @pytest.mark.parametrize("job_name", ["test", "test-windows"])
    def test_a_push_to_main_fails_without_the_key_and_requires_the_package(self, job_name):
        job = _workflow("ci.yml")["jobs"][job_name]
        probe = next(s for s in _steps(job) if s.get("id") == "updater-key")
        assert 'GITHUB_EVENT_NAME" = "push"' in probe["run"] and "exit 1" in probe["run"]
        assert probe["env"]["HAS_KEY"] == "${{ secrets." + _KEY + " != '' }}"  # the secret itself is not in env
        runs = [s for s in _steps(job) if "pytest" in s.get("run", "")]
        assert runs and all("DISTRICTSYNC_REQUIRE_UPDATER" in s.get("env", {}) for s in runs)

    def test_the_key_is_named_in_ci_yml_only_and_only_by_those_two_jobs(self):
        ci = (_WORKFLOWS / "ci.yml").read_text(encoding="utf8")
        assert ci.count(_KEY) == 4  # per job: the presence probe and the checkout's ssh-key
        for name in ("flet-pack.yml", "flet-verify.yml", "release.yml"):
            assert _KEY not in (_WORKFLOWS / name).read_text(encoding="utf8"), name

    def test_other_ci_jobs_do_not_get_the_key(self):
        jobs = _workflow("ci.yml")["jobs"]
        for name, job in jobs.items():
            has = _KEY in yaml.safe_dump(job)
            assert has == (name in {"test", "test-windows"}), name

    def test_no_workflow_uses_pull_request_target(self):
        for path in _WORKFLOWS.glob("*.yml"):
            doc = yaml.safe_load(path.read_text(encoding="utf8"))
            triggers = doc.get("on", doc.get(True, {}))  # YAML 1.1 reads a bare `on` key as True
            names = triggers if isinstance(triggers, (list, tuple)) else list(triggers)
            assert "pull_request_target" not in names, path.name

    def test_the_checkout_folder_is_ignored_by_git(self):
        assert "/.shared-updater/" in (_ROOT / ".gitignore").read_text(encoding="utf8").splitlines()
