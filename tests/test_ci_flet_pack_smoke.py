"""Unit tests for the PURE helpers of ``scripts/ci_flet_pack_smoke.py``.

These helpers carry the release gate's correctness, so they are tested in
isolation:

  * ``resolve_artifact`` — which packed file the smoke actually launches.
  * ``orphan_pids`` — the baseline-delta that decides "zero-orphan close".
  * ``manifest_has_embed`` — the build-time proof that the client is embedded.
  * ``etl_log_candidates`` — where the failure diagnostic looks for the boot log
    (per-OS app-data first, retired legacy ``~/.districtsync`` as fallback).

No process-mock theater: the heavy phases (launch / WM_CLOSE / move-aside) need a
real exe + a real desktop and are covered by the 3-OS CI smoke, not here. The
script lives under ``scripts/`` (not an importable package) so it is loaded by
path via ``importlib.util``. Scripts are excluded from ``--cov=src`` => no
coverage impact.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci_flet_pack_smoke.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_flet_pack_smoke", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register BEFORE exec (the canonical importlib recipe): `@dataclass` resolves
    # `cls.__module__` through `sys.modules`, so an unregistered path-load blows up
    # on any dataclass in the script.
    sys.modules[spec.name] = module
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
    # The LIGHT Linux client: distro + flavor + arch are all in the filename. This is
    # the shape that proves an exact-name marker list was only ever working by accident.
    toc = "('flet_desktop/app/flet-linux-ubuntu-22.04-light-x64.tar.gz', '/tmp/x', 'DATA')"
    assert smoke.manifest_has_embed(toc) is True


def test_manifest_has_embed_linux_full_toc() -> None:
    # …and the FULL Linux client (no flavor token) matches the same rule.
    toc = "('flet_desktop/app/flet-linux-ubuntu-22.04-x64.tar.gz', '/tmp/x', 'DATA')"
    assert smoke.manifest_has_embed(toc) is True


def test_manifest_has_embed_macos_toc() -> None:
    toc = "('flet_desktop/app/flet-macos.tar.gz', '/tmp/x', 'DATA')"
    assert smoke.manifest_has_embed(toc) is True


@pytest.mark.parametrize(
    "archive",
    ["flet-windows-light.zip", "flet-macos-light.tar.gz", "flet-windows-x64-light.zip"],
)
def test_manifest_has_embed_flavor_suffixed_win_mac_names(archive: str) -> None:
    """A flavor token in the Windows/macOS archive name still reads as an embed.

    HONEST SCOPE: `flet_cli/commands/pack.py` @0.85.3 names those two archives
    flavor-INDEPENDENTLY (`flet-windows.zip` / `flet-macos.tar.gz`) — verified, so
    these are not names flet emits today. They pin the generalization's intent: if
    upstream ever extends the Linux naming convention (which already carries the
    flavor) to the other OSes, the release gate must not start reporting "no
    embedded client" for a perfectly good exe.
    """
    assert smoke.manifest_has_embed(f"('flet_desktop/app/{archive}', '/tmp/x', 'DATA')") is True


def test_manifest_without_archive_is_not_embed() -> None:
    # flet_desktop appears as a code module but no client archive => NOT embedded.
    toc = "('flet_desktop/__init__.py', '/site/flet_desktop/__init__.py', 'DATA')"
    assert smoke.manifest_has_embed(toc) is False


def test_manifest_archive_without_app_dest_is_not_embed() -> None:
    # Archive name present but not under the flet_desktop/app dest => not the embed.
    toc = "('elsewhere/flet-windows.zip', '/tmp/x', 'DATA')"
    assert smoke.manifest_has_embed(toc) is False


def test_manifest_dest_and_archive_on_separate_entries_is_not_embed() -> None:
    """The two halves must be the SAME path — the dest alone vouches for nothing.

    A `flet_desktop/app/` code entry plus an unrelated archive elsewhere in the TOC
    is exactly the false-PASS shape the archive requirement exists to exclude.
    """
    toc = "('flet_desktop/app/__init__.py', '/x', 'DATA'),\n('elsewhere/flet-windows.zip', '/tmp/x', 'DATA')"
    assert smoke.manifest_has_embed(toc) is False


@pytest.mark.parametrize("name", ["flet-windows.txt", "flet-windows", "other-windows.zip", "flet-android.zip"])
def test_manifest_non_client_archive_at_the_dest_is_not_embed(name: str) -> None:
    # Not loosened to a bare `flet-` prefix: the OS token AND a real archive
    # extension both stay required, because the archive name IS the proof.
    assert smoke.manifest_has_embed(f"('flet_desktop/app/{name}', '/tmp/x', 'DATA')") is False


def test_manifest_empty_is_not_embed() -> None:
    assert smoke.manifest_has_embed("") is False


# --------------------------------------------------------------------------- #
#  etl_log_candidates (per-OS boot-log resolution, no src import)
# --------------------------------------------------------------------------- #


def test_etl_log_candidates_windows_uses_localappdata(tmp_path: Path) -> None:
    home = tmp_path / "u"
    local = tmp_path / "elsewhere" / "Local"
    cands = smoke.etl_log_candidates(home, "Windows", {"LOCALAPPDATA": str(local)})
    assert cands[0] == local / "DistrictSync" / "etl_tool.log"


def test_etl_log_candidates_windows_without_localappdata_falls_back(tmp_path: Path) -> None:
    home = tmp_path / "u"
    cands = smoke.etl_log_candidates(home, "Windows", {})
    assert cands[0] == home / "AppData" / "Local" / "DistrictSync" / "etl_tool.log"


def test_etl_log_candidates_macos(tmp_path: Path) -> None:
    home = tmp_path / "u"
    cands = smoke.etl_log_candidates(home, "Darwin", {})
    assert cands[0] == home / "Library" / "Application Support" / "DistrictSync" / "etl_tool.log"


def test_etl_log_candidates_linux_uses_xdg_data_home(tmp_path: Path) -> None:
    home = tmp_path / "u"
    xdg = tmp_path / "xdg-data"
    cands = smoke.etl_log_candidates(home, "Linux", {"XDG_DATA_HOME": str(xdg)})
    assert cands[0] == xdg / "DistrictSync" / "etl_tool.log"


def test_etl_log_candidates_linux_default(tmp_path: Path) -> None:
    home = tmp_path / "u"
    cands = smoke.etl_log_candidates(home, "Linux", {})
    assert cands[0] == home / ".local" / "share" / "DistrictSync" / "etl_tool.log"


def test_etl_log_candidates_legacy_dir_is_secondary_on_every_os(tmp_path: Path) -> None:
    home = tmp_path / "u"
    for osn in ("Windows", "Darwin", "Linux"):
        cands = smoke.etl_log_candidates(home, osn, {})
        assert cands[1] == home / ".districtsync" / "etl_tool.log"
        assert len(cands) == 2


# --------------------------------------------------------------------------- #
#  DISTRICTSYNC_DATA_DIR — the profile seam, mirrored (never imported)          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("osn", ["Windows", "Darwin", "Linux"])
def test_override_wins_outright_and_drops_the_legacy_fallback(tmp_path: Path, osn: str) -> None:
    """Step 0 in the app is "wins outright" — the mirror must not keep a fallback.

    A fallback here would be the "never probe the real profile" hazard: a smoke
    pointed at a throwaway profile would quietly read the RUNNER's real log and
    report on it.
    """
    home = tmp_path / "u"
    override = tmp_path / "seam-profile"
    cands = smoke.etl_log_candidates(home, osn, {"DISTRICTSYNC_DATA_DIR": str(override)})
    assert cands == [override.resolve() / "etl_tool.log"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_override_is_not_in_play(tmp_path: Path, blank: str) -> None:
    home = tmp_path / "u"
    assert smoke.override_data_dir({"DISTRICTSYNC_DATA_DIR": blank}) is None
    cands = smoke.etl_log_candidates(home, "Linux", {"DISTRICTSYNC_DATA_DIR": blank})
    assert cands[0] == home / ".local" / "share" / "DistrictSync" / "etl_tool.log"


def test_override_absent_key_is_not_in_play() -> None:
    assert smoke.override_data_dir({}) is None


@pytest.mark.real_user_data_dir
def test_override_mirror_agrees_with_the_real_paths_seam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror and the app must resolve the SAME profile, or the smokes are vacuous.

    ``etl_log_candidates`` deliberately does not import ``src`` (the script stays
    standalone), so nothing structural keeps the two in step — this test does.
    """
    import platform as _platform

    from src.utils import paths

    override = tmp_path / "seam-profile"
    monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(override))

    assert smoke.override_data_dir(os.environ) == paths._override_data_dir()
    assert smoke.etl_log_candidates(Path.home(), _platform.system(), os.environ)[0] == paths.user_log_file()


# --------------------------------------------------------------------------- #
#  CLI-smoke pure helpers                                                       #
# --------------------------------------------------------------------------- #


def test_last_run_record_parses_the_final_line(tmp_path: Path) -> None:
    tail = (
        '2026-01-01 - src.etl.pipeline - INFO - __DISTRICTSYNC_RUN__ {"status": "failed", "source": "cli"}\n'
        '2026-01-01 - src.etl.pipeline - INFO - __DISTRICTSYNC_RUN__ {"status": "success", "source": "cli"}\n'
    )
    assert smoke._last_run_record(tail) == {"status": "success", "source": "cli"}


def test_last_run_record_absent_or_unparseable_is_none() -> None:
    assert smoke._last_run_record("nothing to see here") is None
    assert smoke._last_run_record("INFO - __DISTRICTSYNC_RUN__ {not json") is None


def test_file_signature_detects_a_rewrite(tmp_path: Path) -> None:
    # The "history.db untouched" and "planted bytes intact" checks rest on this.
    f = tmp_path / "history.db"
    assert smoke._file_signature(f) is None
    f.write_bytes(b"x")
    before = smoke._file_signature(f)
    assert before is not None and before[0] is True
    f.write_bytes(b"xx")
    assert smoke._file_signature(f) != before


def test_cli_smoke_context_gives_each_phase_its_own_output_dir(tmp_path: Path) -> None:
    ctx = smoke.CliSmokeContext.build(tmp_path / "in", tmp_path / "work", {})
    first = ctx.new_output("dry-run")
    second = ctx.new_output("dry-run")
    assert first != second and first.is_dir() and second.is_dir()


def test_cli_smoke_context_log_text_is_empty_when_absent(tmp_path: Path) -> None:
    ctx = smoke.CliSmokeContext.build(tmp_path / "in", tmp_path / "work", {"DISTRICTSYNC_DATA_DIR": str(tmp_path)})
    assert ctx.seam_dir == tmp_path.resolve()
    assert ctx.log_text() == ""


def test_corrupt_profile_phase_refuses_to_run_without_the_seam(tmp_path: Path) -> None:
    """Fail LOUD rather than plant a corrupt config.json into whatever profile is real."""
    ctx = smoke.CliSmokeContext.build(tmp_path / "in", tmp_path / "work", {})
    assert ctx.seam_dir is None
    assert smoke._smoke_corrupt_profile(tmp_path / "DistrictSync.exe", ctx) is False


def test_cli_smoke_phase_order_puts_the_plant_last() -> None:
    # The corrupt-profile phase deletes the seam dir on the way out, so it can only
    # ever run last within a single `--phase all` invocation.
    assert list(smoke.CLI_SMOKE_PHASES) == ["version", "dry-run", "write-run", "corrupt-profile"]


def test_cli_smoke_missing_artifact_fails_without_launching(tmp_path: Path) -> None:
    rc = smoke.run_cli_smoke(
        tmp_path / "dist", "DistrictSync", phase="all", input_dir=tmp_path / "in", work_dir=tmp_path / "work"
    )
    assert rc == 1


def test_cli_smoke_missing_fixture_fails_before_any_phase(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "DistrictSync.exe").write_bytes(b"x")  # never launched — the fixture check comes first
    rc = smoke.run_cli_smoke(
        dist, "DistrictSync", phase="all", input_dir=tmp_path / "absent", work_dir=tmp_path / "work"
    )
    assert rc == 1


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


def test_cli_smoke_mode_parses_with_a_phase_and_paths(tmp_path: Path) -> None:
    args = smoke._parse_args(
        ["dist", "DistrictSync", "--cli-smoke", "--phase", "write-run", "--input", str(tmp_path), "--work-dir", "w"]
    )
    assert args.cli_smoke is True
    assert args.phase == "write-run"
    assert args.input == tmp_path
    assert args.work_dir == Path("w")


def test_cli_smoke_defaults_to_all_phases_and_the_snapshot_fixture() -> None:
    args = smoke._parse_args(["dist", "DistrictSync", "--cli-smoke"])
    assert args.phase == "all"
    assert args.input == smoke.DEFAULT_SMOKE_INPUT
    assert args.input.is_dir(), "the committed SD74 snapshot input is the default fixture"


def test_cli_smoke_rejects_an_unknown_phase() -> None:
    with pytest.raises(SystemExit):
        smoke._parse_args(["dist", "DistrictSync", "--cli-smoke", "--phase", "nope"])


def test_main_cli_smoke_dispatches_without_launching(tmp_path: Path) -> None:
    # No artifact under dist => run_cli_smoke returns 1 before any subprocess.
    assert smoke.main([str(tmp_path), "DistrictSync", "--cli-smoke"]) == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
