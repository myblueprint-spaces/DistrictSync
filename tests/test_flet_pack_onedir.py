"""DSYNC-60 (plan 0054 S1): the one-folder Windows build's shared args and workflow shape.

``scripts/flet_pack_args.py`` is the one list of hidden imports the one-folder job reads. The
one-file pack step still spells its imports inline (that job is deliberately untouched), so the
parity tests below keep the two from drifting. The shape tests pin the properties the owner
asked for: the new job cannot affect a release or a pull request, takes no secret, and is never
published.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"


def _load_args_module():
    """``scripts/`` is not a package: load the shared-args script by path, registered before exec."""
    spec = importlib.util.spec_from_file_location("flet_pack_args", _ROOT / "scripts" / "flet_pack_args.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


args = _load_args_module()


def _load(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf8"))


def _one_file_step_text() -> str:
    steps = _load("flet-pack.yml")["jobs"]["pack"]["steps"]
    hits = [s for s in steps if s.get("name") == "Pack windowed offline Flet exe"]
    assert len(hits) == 1, "the one-file pack step was renamed or duplicated: update this test"
    return hits[0]["run"]


def _modules_named_by_one_file_step() -> set[str]:
    text = _one_file_step_text()
    literal = set(re.findall(r"--hidden-import\s+([A-Za-z_][\w.]*)", text))
    # modules chosen by a `${{ runner.os == ... && 'mod' || 'mod' }}` expression
    quoted = set(re.findall(r"'((?:src|keyring)\.[\w.]+)'", text))
    return literal | quoted


# --------------------------------------------------------------------------- #
#  the shared list
# --------------------------------------------------------------------------- #


def test_one_file_step_and_shared_list_name_the_same_modules() -> None:
    inline = _modules_named_by_one_file_step()
    shared = set(args.all_modules())
    assert inline - shared == set(), f"the one-file step names modules the shared list lacks: {sorted(inline - shared)}"
    assert shared - inline == set(), f"the shared list names modules the one-file step lacks: {sorted(shared - inline)}"


def test_the_inline_scan_actually_finds_modules() -> None:
    """Positive twin: an empty scan would make the parity test above vacuously equal."""
    inline = _modules_named_by_one_file_step()
    assert "flet" in inline and "src.etl.transformers.registry" in inline
    assert "win32timezone" in inline and "keyring.backends.libsecret" in inline
    assert len(inline) > 30


@pytest.mark.parametrize("os_name", ["windows", "macos", "linux"])
def test_every_os_gets_the_common_modules_and_no_duplicates(os_name: str) -> None:
    mods = args.hidden_imports(os_name)
    assert set(args.COMMON) <= set(mods)
    assert len(mods) == len(set(mods))


def test_windows_list_carries_the_windows_only_modules() -> None:
    win = set(args.hidden_imports("windows"))
    assert {"src.scheduler.windows", "keyring.backends.Windows", "pythoncom", "win32timezone"} <= win
    assert "keyring.backends.macOS" not in win  # negative twin


def test_unknown_os_raises() -> None:
    with pytest.raises(ValueError):
        args.hidden_imports("plan9")


def test_flags_are_hidden_import_pairs() -> None:
    flags = args.as_flags("windows").split()
    assert flags[0::2] == ["--hidden-import"] * (len(flags) // 2)
    assert "src.scheduler.task_com" in flags[1::2]


def test_cli_prints_flags_and_rejects_bad_input(capsys: pytest.CaptureFixture[str]) -> None:
    assert args.main(["windows"]) == 0
    assert "--hidden-import flet" in capsys.readouterr().out
    assert args.main([]) == 2
    assert args.main(["plan9"]) == 2


# --------------------------------------------------------------------------- #
#  workflow shape: the new job cannot touch a release or a pull request
# --------------------------------------------------------------------------- #


def test_onedir_job_runs_only_when_asked() -> None:
    job = _load("flet-pack.yml")["jobs"]["pack-windows-onedir"]
    assert "inputs.windows_onedir" in str(job["if"])
    assert job["runs-on"] == "windows-latest"


def test_the_one_file_job_is_skippable_only_by_the_new_input() -> None:
    pack = _load("flet-pack.yml")["jobs"]["pack"]
    assert "onedir_only" in str(pack["if"])
    # the release does not pass it, so the one-file job always runs on a release
    release = _load("release.yml")["jobs"]["build-flet"]
    assert "onedir_only" not in str(release.get("with", {}))


def test_release_does_not_ask_for_the_onedir_build() -> None:
    """S1 leaves the release untouched: no one-folder job, so it cannot fail or delay a release."""
    release = _load("release.yml")["jobs"]["build-flet"]
    assert "windows_onedir" not in str(release.get("with", {}))
    assert release["with"]["upload"] is True  # positive twin: we read the right job


def test_pull_requests_never_get_the_onedir_job() -> None:
    verify = _load("flet-verify.yml")["jobs"]["pack"]["with"]
    assert "workflow_dispatch" in str(verify["windows_onedir"])
    assert "pull_request" in str(verify["linux_only"])  # positive twin


def test_onedir_job_holds_no_secret_and_no_token_scope() -> None:
    text = (_WORKFLOWS / "flet-pack.yml").read_text(encoding="utf8")
    job_text = text[text.index("  pack-windows-onedir:") :]
    assert "secrets." not in job_text
    assert "id-token" not in job_text
    assert "azure/login" not in job_text
    top = _load("flet-pack.yml")["permissions"]
    assert top == {"contents": "read"}


def test_onedir_job_signs_and_publishes_nothing() -> None:
    text = (_WORKFLOWS / "flet-pack.yml").read_text(encoding="utf8")
    job_text = text[text.index("  pack-windows-onedir:") :]
    assert "trusted-signing" not in job_text and "signtool" not in job_text
    assert "action-gh-release" not in job_text
    uploads = re.findall(r"name: (DistrictSync-[\w.-]+)", job_text)
    assert uploads == ["DistrictSync-windows-onedir"]


def test_onedir_job_reads_the_shared_list_and_bundles_the_client() -> None:
    steps = _load("flet-pack.yml")["jobs"]["pack-windows-onedir"]["steps"]
    pack = next(s for s in steps if "Pack the one-folder" in s.get("name", ""))["run"]
    assert "scripts/flet_pack_args.py windows" in pack
    assert " -D " in pack and ";flet_view" in pack
    names = [s.get("name", "") for s in steps]
    assert any("Negative twin" in n for n in names)
    # the negative twin must run AFTER the smoke it is the twin of
    assert names.index(next(n for n in names if "GUI smoke" in n)) < names.index(
        next(n for n in names if "Negative twin" in n)
    )
