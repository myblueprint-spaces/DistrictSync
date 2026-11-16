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
        assert re.fullmatch(r"[0-9a-f]{64}", up.PINNED_TREE_SHA256)
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


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


class TestTreeHash:
    FILES = {"pyproject.toml": b"[project]\nname = 'x'\n", "src/pkg/__init__.py": b"VALUE = 1\n"}

    def test_matches_an_independent_computation_of_the_documented_rule(self, tmp_path):
        """Path, NUL, then the SHA-256 of the content, files in byte order of their posix paths."""
        root = _tree(tmp_path / "p", self.FILES)
        digest = hashlib.sha256()
        for name in sorted(self.FILES, key=lambda n: n.encode()):
            digest.update(name.encode() + b"\0" + hashlib.sha256(self.FILES[name]).digest())
        assert up.tree_hash(root) == digest.hexdigest()

    def test_line_endings_do_not_change_it(self, tmp_path):
        lf = _tree(tmp_path / "lf", self.FILES)
        crlf = _tree(tmp_path / "crlf", {k: v.replace(b"\n", b"\r\n") for k, v in self.FILES.items()})
        assert up.tree_hash(lf) == up.tree_hash(crlf)

    def test_a_changed_byte_changes_it(self, tmp_path):
        a = _tree(tmp_path / "a", self.FILES)
        b = _tree(tmp_path / "b", {**self.FILES, "src/pkg/__init__.py": b"VALUE = 2\n"})
        assert up.tree_hash(a) != up.tree_hash(b)

    def test_an_added_file_changes_it(self, tmp_path):
        a = _tree(tmp_path / "a", self.FILES)
        b = _tree(tmp_path / "b", {**self.FILES, "src/pkg/extra.py": b""})
        assert up.tree_hash(a) != up.tree_hash(b)

    def test_build_leftovers_are_ignored(self, tmp_path):
        a = _tree(tmp_path / "a", self.FILES)
        b = _tree(
            tmp_path / "b",
            {**self.FILES, "build/lib/x.py": b"1", "x.egg-info/PKG-INFO": b"1", "src/pkg/__pycache__/m.pyc": b"1"},
        )
        assert up.tree_hash(a) == up.tree_hash(b)

    def test_a_missing_folder_is_refused(self, tmp_path):
        with pytest.raises(up.PinError):
            up.tree_hash(tmp_path / "nope")

    def test_check_tree(self, tmp_path):
        root = _tree(tmp_path / "p", self.FILES)
        up.check_tree(root, up.tree_hash(root))
        with pytest.raises(up.PinError) as info:
            up.check_tree(root, "f" * 64)
        assert up.tree_hash(root) in str(info.value) and "f" * 64 in str(info.value)

    def test_cli_tree_hash_prints_it(self, tmp_path, capsys):
        root = _tree(tmp_path / "p", self.FILES)
        assert up.main(["tree-hash", str(root)]) == 0
        assert capsys.readouterr().out.strip() == up.tree_hash(root)


class TestInstall:
    """``_run`` is replaced: nothing here touches git, pip or the network."""

    @pytest.fixture
    def run(self, monkeypatch, tmp_path):
        recorded: list[tuple[list[str], dict]] = []
        state = {"head": up.PINNED_COMMIT + "\n", "status": ""}

        def fake_run(cmd, **kwargs):
            recorded.append((list(cmd), kwargs))
            if cmd[:2] == ["git", "-C"] and "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=state["head"], stderr="")
            if cmd[:2] == ["git", "-C"] and "status" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=state["status"], stderr="")
            if "wheel" in cmd and "pip" in cmd:  # the build: produce the wheel where it was asked to
                (Path(cmd[cmd.index("-w") + 1]) / up.WHEEL_NAME).write_bytes(b"wheel")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(up, "_run", fake_run)
        source = tmp_path / "shared"
        _tree(source / up.PACKAGE_DIR, TestTreeHash.FILES)
        monkeypatch.setattr(up, "PINNED_TREE_SHA256", up.tree_hash(source / up.PACKAGE_DIR))
        return source, recorded, state

    @staticmethod
    def _pip_calls(recorded):
        return [c for c, _k in recorded if c[0] == sys.executable]

    def test_a_clean_checkout_at_the_pin_is_built_and_installed(self, run):
        """Positive twin of every refusal below."""
        source, recorded, _state = run
        assert up.main(["install", "--source", str(source)]) == 0
        pips = self._pip_calls(recorded)
        assert any("setuptools==84.2.1" in c for c in pips)
        build = next(k for c, k in recorded if "wheel" in c and "pip" in c)
        assert build["env"]["SOURCE_DATE_EPOCH"] == up.SOURCE_DATE_EPOCH
        install = pips[-1]
        assert "--no-deps" in install and install[-1].endswith(up.WHEEL_NAME)

    def test_another_commit_is_refused_before_any_pip_call(self, run):
        source, recorded, state = run
        state["head"] = "0" * 40 + "\n"
        assert up.main(["install", "--source", str(source)]) == 1
        assert self._pip_calls(recorded) == []

    def test_a_dirty_checkout_is_refused_before_any_pip_call(self, run):
        source, recorded, state = run
        state["status"] = " M packages/myb_desktop_updater/src/x.py\n"
        assert up.main(["install", "--source", str(source)]) == 1
        assert self._pip_calls(recorded) == []

    def test_changed_content_is_refused_before_any_pip_call(self, run):
        source, recorded, _state = run
        (source / up.PACKAGE_DIR / "src" / "pkg" / "__init__.py").write_bytes(b"VALUE = 99\n")
        assert up.main(["install", "--source", str(source)]) == 1
        assert self._pip_calls(recorded) == []


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
