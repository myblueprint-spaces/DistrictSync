"""Unit tests for the PURE helpers of ``scripts/ci_flet_pack_smoke.py``.

These three helpers carry the release gate's correctness, so they are tested in
isolation:

  * ``resolve_artifact`` — which packed file the smoke actually launches.
  * ``orphan_pids`` — the baseline-delta that decides "zero-orphan close".
  * ``manifest_has_embed`` — the build-time proof that the client is embedded.

No process-mock theater: the heavy phases (launch / WM_CLOSE / move-aside) need a
real exe + a real desktop and are covered by the 3-OS CI smoke, not here. The
script lives under ``scripts/`` (not an importable package) so it is loaded by
path via ``importlib.util``. Scripts are excluded from ``--cov=src`` => no
coverage impact.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci_flet_pack_smoke.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_flet_pack_smoke", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load()


# --------------------------------------------------------------------------- #
#  resolve_artifact
# --------------------------------------------------------------------------- #


def test_resolve_artifact_finds_windows_exe(tmp_path: Path) -> None:
    (tmp_path / "DistrictSync-flet.exe").write_bytes(b"x")
    assert smoke.resolve_artifact(tmp_path, "DistrictSync-flet") == (tmp_path / "DistrictSync-flet.exe")


def test_resolve_artifact_finds_bare_posix_binary(tmp_path: Path) -> None:
    (tmp_path / "DistrictSync-flet").write_bytes(b"x")
    assert smoke.resolve_artifact(tmp_path, "DistrictSync-flet") == (tmp_path / "DistrictSync-flet")


def test_resolve_artifact_finds_macos_app_bundle(tmp_path: Path) -> None:
    inner = tmp_path / "DistrictSync-flet.app" / "Contents" / "MacOS"
    inner.mkdir(parents=True)
    (inner / "DistrictSync-flet").write_bytes(b"x")
    assert smoke.resolve_artifact(tmp_path, "DistrictSync-flet") == (inner / "DistrictSync-flet")


def test_resolve_artifact_prefers_exe_over_bare(tmp_path: Path) -> None:
    # An .exe and a bare file with the same base name: .exe is the Windows artifact.
    (tmp_path / "DistrictSync-flet.exe").write_bytes(b"x")
    (tmp_path / "DistrictSync-flet").write_bytes(b"x")
    assert smoke.resolve_artifact(tmp_path, "DistrictSync-flet").name == "DistrictSync-flet.exe"


def test_resolve_artifact_missing_returns_none(tmp_path: Path) -> None:
    assert smoke.resolve_artifact(tmp_path, "DistrictSync-flet") is None


# --------------------------------------------------------------------------- #
#  orphan_pids (baseline-delta)
# --------------------------------------------------------------------------- #


def test_orphan_pids_only_new_pids_count() -> None:
    # PID 100 was already running before launch (co-tenant) -> not our orphan.
    # PID 999 is new and survived -> a real orphan.
    assert smoke.orphan_pids({100, 200}, {100, 999}) == {999}


def test_orphan_pids_clean_close_is_empty() -> None:
    # Everything new exited; only the pre-existing baseline PID remains.
    assert smoke.orphan_pids({100}, {100}) == set()


def test_orphan_pids_ignores_vanished_baseline() -> None:
    # A baseline PID that exited is not an orphan (set difference, not symmetric).
    assert smoke.orphan_pids({100, 200}, {300}) == {300}


def test_orphan_pids_accepts_arbitrary_iterables() -> None:
    # Helper takes any Iterable[int], not just sets.
    assert smoke.orphan_pids([1, 2], (2, 3)) == {3}


# --------------------------------------------------------------------------- #
#  manifest_has_embed
# --------------------------------------------------------------------------- #


def test_manifest_has_embed_windows_toc() -> None:
    toc = r"""
  ('config\\logging.conf', 'C:\\repo\\config\\logging.conf', 'DATA'),
  ('flet_desktop\\app\\flet-windows.zip', 'C:\\tmp\\flet-windows.zip', 'DATA'),
"""
    assert smoke.manifest_has_embed(toc) is True


def test_manifest_has_embed_posix_toc() -> None:
    toc = "('flet_desktop/app/flet-linux-ubuntu-22.04-light-x64.tar.gz', '/tmp/x', 'DATA')"
    assert smoke.manifest_has_embed(toc) is True


def test_manifest_has_embed_macos_toc() -> None:
    toc = "('flet_desktop/app/flet-macos.tar.gz', '/tmp/x', 'DATA')"
    assert smoke.manifest_has_embed(toc) is True


def test_manifest_without_archive_is_not_embed() -> None:
    # flet_desktop appears as a code module but no client archive => NOT embedded.
    toc = "('flet_desktop/__init__.py', '/site/flet_desktop/__init__.py', 'DATA')"
    assert smoke.manifest_has_embed(toc) is False


def test_manifest_archive_without_app_dest_is_not_embed() -> None:
    # Archive name present but not under the flet_desktop/app dest => not the embed.
    toc = "('elsewhere/flet-windows.zip', '/tmp/x', 'DATA')"
    assert smoke.manifest_has_embed(toc) is False


def test_manifest_empty_is_not_embed() -> None:
    assert smoke.manifest_has_embed("") is False


# --------------------------------------------------------------------------- #
#  _assert_embed entrypoint (thin wrapper over the pure helper)
# --------------------------------------------------------------------------- #


def test_assert_embed_pass(tmp_path: Path) -> None:
    manifest = tmp_path / "Analysis-00.toc"
    manifest.write_text("('flet_desktop/app/flet-windows.zip', '/x', 'DATA')", encoding="utf-8")
    assert smoke._assert_embed(manifest) == 0


def test_assert_embed_fail(tmp_path: Path) -> None:
    manifest = tmp_path / "Analysis-00.toc"
    manifest.write_text("('flet_desktop/__init__.py', '/x', 'DATA')", encoding="utf-8")
    assert smoke._assert_embed(manifest) == 1


def test_assert_embed_missing_manifest(tmp_path: Path) -> None:
    assert smoke._assert_embed(tmp_path / "nope.toc") == 1


# --------------------------------------------------------------------------- #
#  arg parsing — embed-only mode vs smoke mode
# --------------------------------------------------------------------------- #


def test_main_requires_dist_and_name_without_assert_embed() -> None:
    # Neither --assert-embed nor positional args => usage error exit code 2.
    assert smoke.main([]) == 2


def test_main_assert_embed_dispatches(tmp_path: Path) -> None:
    manifest = tmp_path / "Analysis-00.toc"
    manifest.write_text("('flet_desktop/app/flet-macos.tar.gz', '/x', 'DATA')", encoding="utf-8")
    assert smoke.main(["--assert-embed", str(manifest)]) == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
