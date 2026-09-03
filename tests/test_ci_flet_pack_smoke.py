"""Unit tests for the PURE helpers of ``scripts/ci_flet_pack_smoke.py``.

These helpers carry the release gate's correctness, so they are tested in
isolation:

  * ``resolve_artifact`` — which packed file the smoke actually launches.
  * ``orphan_pids`` — the baseline-delta that decides "zero-orphan close".
  * ``manifest_has_embed`` — the build-time proof that the client is embedded.
  * ``etl_log_candidates`` — where the failure diagnostic looks for the boot log
    (per-OS app-data first, retired legacy ``~/.districtsync`` as fallback).

It also carries the PARITY tests that keep the standalone script honest: the log
markers it greps for are pinned to the ``src`` code that emits them, and its
``override_data_dir`` mirror is pinned to the real ``paths`` seam.

No process-mock theater: the heavy phases (launch / WM_CLOSE / move-aside) need a
real exe + a real desktop and are covered by the 3-OS CI smoke, not here. The
script lives under ``scripts/`` (not an importable package), so ``conftest.py``
loads it by path once and registers it in ``sys.modules`` — hence the plain import
below. Scripts are excluded from ``--cov=src`` => no coverage impact.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

import ci_flet_pack_smoke as smoke  # loaded + registered by tests/conftest.py
import pytest

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
#  resolve_artifact — explicit kind (plan 0045)
#
#  macOS packs BOTH a bare binary and a .app, and they ship to different
#  audiences (the DMG's bundle vs the headless CLI download). These rows pin that
#  each kind stays addressable — the property a reordered `auto` list would have
#  destroyed, leaving one of the two artifacts permanently untestable.
# --------------------------------------------------------------------------- #


def _macos_dual_layout(root: Path, name: str) -> tuple[Path, Path]:
    """Write the two artifacts a macOS `flet pack` really produces. Returns (binary, inner)."""
    binary = root / name
    binary.write_bytes(b"x")
    inner_dir = root / f"{name}.app" / "Contents" / "MacOS"
    inner_dir.mkdir(parents=True)
    inner = inner_dir / name
    inner.write_bytes(b"x")
    return binary, inner


def test_resolve_artifact_binary_kind_picks_the_bare_binary(tmp_path: Path) -> None:
    binary, _inner = _macos_dual_layout(tmp_path, "DistrictSync")
    assert smoke.resolve_artifact(tmp_path, "DistrictSync", "binary") == binary


def test_resolve_artifact_bundle_kind_picks_the_app_inner_exe(tmp_path: Path) -> None:
    _binary, inner = _macos_dual_layout(tmp_path, "DistrictSync")
    assert smoke.resolve_artifact(tmp_path, "DistrictSync", "bundle") == inner


def test_resolve_artifact_binary_kind_never_falls_back_to_the_bundle(tmp_path: Path) -> None:
    # The bundle exists and the bare binary does not. `auto` would happily return the
    # bundle; `binary` must return None instead. Otherwise the CLI smoke could silently
    # start proving the GUI artifact and nobody would ever know the difference.
    inner_dir = tmp_path / "DistrictSync.app" / "Contents" / "MacOS"
    inner_dir.mkdir(parents=True)
    (inner_dir / "DistrictSync").write_bytes(b"x")
    assert smoke.resolve_artifact(tmp_path, "DistrictSync", "bundle") is not None
    assert smoke.resolve_artifact(tmp_path, "DistrictSync", "binary") is None


def test_resolve_artifact_bundle_kind_none_without_an_app(tmp_path: Path) -> None:
    # Windows/Linux dists have no .app: asking for a bundle there is honestly nothing.
    (tmp_path / "DistrictSync").write_bytes(b"x")
    (tmp_path / "DistrictSync.exe").write_bytes(b"x")
    assert smoke.resolve_artifact(tmp_path, "DistrictSync", "bundle") is None


def test_resolve_artifact_auto_is_unchanged_on_the_macos_dual_layout(tmp_path: Path) -> None:
    # The historical behaviour, pinned deliberately: `auto` picks the BARE BINARY when
    # both exist. That is exactly the silent preference that let the .app ship
    # unsmoked for the whole life of the macOS job — so it is documented as a fact
    # here rather than left as an accident of candidate ordering.
    binary, _inner = _macos_dual_layout(tmp_path, "DistrictSync")
    assert smoke.resolve_artifact(tmp_path, "DistrictSync") == binary


# --------------------------------------------------------------------------- #
#  mounted_app_problems — the macOS DMG release gate (plan 0045)
#
#  A DMG is only an improvement over the old bare-binary download if the bundle
#  inside it is intact AS DELIVERED. Every row below is a way an image can look
#  built-but-broken; the non-executable row is the negative twin for the assert
#  that exists precisely because `upload-artifact` strips POSIX mode bits.
# --------------------------------------------------------------------------- #


def _applications_symlink(root: Path) -> None:
    """Create the /Applications drag target, or skip where the OS forbids symlinks.

    Unprivileged Windows without Developer Mode cannot create symlinks. CI runs these
    on ubuntu, so the rows below really do execute — skipping locally is honest about
    a platform limit rather than quietly weakening the assertion.
    """
    try:
        (root / "Applications").symlink_to("/Applications")
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform gate
        pytest.skip(f"symlink creation unavailable on this platform: {exc}")


def _mounted_volume(root: Path, name: str, *, executable: bool = True) -> Path:
    """Build a synthetic mounted-DMG tree: <name>.app + an /Applications symlink."""
    inner_dir = root / f"{name}.app" / "Contents" / "MacOS"
    inner_dir.mkdir(parents=True)
    inner = inner_dir / name
    inner.write_bytes(b"x")
    inner.chmod(0o755 if executable else 0o644)
    _applications_symlink(root)
    return root


def test_mounted_app_problems_clean_volume_is_shippable(tmp_path: Path) -> None:
    vol = _mounted_volume(tmp_path, "DistrictSync")
    assert smoke.mounted_app_problems(vol, "DistrictSync") == []


def test_mounted_app_problems_flags_a_missing_bundle(tmp_path: Path) -> None:
    _applications_symlink(tmp_path)
    problems = smoke.mounted_app_problems(tmp_path, "DistrictSync")
    assert any("no DistrictSync.app" in p for p in problems)


def test_mounted_app_problems_flags_a_bundle_without_an_inner_exe(tmp_path: Path) -> None:
    (tmp_path / "DistrictSync.app" / "Contents" / "MacOS").mkdir(parents=True)
    _applications_symlink(tmp_path)
    problems = smoke.mounted_app_problems(tmp_path, "DistrictSync")
    assert any("no inner executable" in p for p in problems)


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX execute bit to clear")
def test_mounted_app_problems_flags_a_non_executable_inner_exe(tmp_path: Path) -> None:
    # THE negative twin. This is the exact corruption `upload-artifact` inflicts on a
    # loose .app, and shipping it would reproduce the district's bug in a new costume.
    # Without this row, "the exec-bit assert passed" could only ever mean "the assert
    # never looked".
    vol = _mounted_volume(tmp_path, "DistrictSync", executable=False)
    problems = smoke.mounted_app_problems(vol, "DistrictSync")
    assert any("NOT executable" in p for p in problems)


def test_mounted_app_problems_flags_a_real_applications_directory(tmp_path: Path) -> None:
    # If `hdiutil` ever dereferenced the symlink it would copy the runner's whole
    # /Applications into the image. A real directory here is that failure, caught.
    inner_dir = tmp_path / "DistrictSync.app" / "Contents" / "MacOS"
    inner_dir.mkdir(parents=True)
    inner = inner_dir / "DistrictSync"
    inner.write_bytes(b"x")
    inner.chmod(0o755)
    (tmp_path / "Applications").mkdir()
    problems = smoke.mounted_app_problems(tmp_path, "DistrictSync")
    assert any("not a symlink" in p for p in problems)


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


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_override_is_not_in_play(tmp_path: Path, blank: str) -> None:
    home = tmp_path / "u"
    assert smoke.override_data_dir({"DISTRICTSYNC_DATA_DIR": blank}) is None
    cands = smoke.etl_log_candidates(home, "Linux", {"DISTRICTSYNC_DATA_DIR": blank})
    assert cands[0] == home / ".local" / "share" / "DistrictSync" / "etl_tool.log"


def test_override_absent_key_is_not_in_play() -> None:
    assert smoke.override_data_dir({}) is None


def test_relative_override_is_refused_by_the_mirror() -> None:
    # Mirrors paths._override_data_dir: a relative profile path is REFUSED, never
    # absolutized against a CWD the frozen exe is about to delete.
    with pytest.raises(ValueError, match="absolute"):
        smoke.override_data_dir({"DISTRICTSYNC_DATA_DIR": "relative-profile"})


# The ONE value table both sides of the mirror are driven over. Anything added here
# is automatically asserted against BOTH `smoke.override_data_dir` and
# `paths._override_data_dir` — which is the only way the "deliberate mirror" claim
# stays true as either side evolves.
_MIRROR_VALUES = ["<abs-tmp>", "~/dsync", "relative", "", "   ", "\t"]


@pytest.mark.real_user_data_dir
@pytest.mark.parametrize("value", _MIRROR_VALUES)
def test_override_mirror_agrees_with_the_real_paths_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The mirror and the app must resolve the SAME profile, or the smokes are vacuous.

    ``override_data_dir``/``etl_log_candidates`` deliberately do not import ``src``
    (the script stays standalone), so nothing structural keeps the two in step — this
    table does. Both sides must AGREE on every value, including which ones RAISE.
    """
    import platform as _platform

    from src.utils import paths

    raw = str(tmp_path / "seam-profile") if value == "<abs-tmp>" else value
    monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", raw)

    try:
        expected = paths._override_data_dir()
    except ValueError as exc:
        with pytest.raises(ValueError, match="absolute"):
            smoke.override_data_dir(os.environ)
        assert "DISTRICTSYNC_DATA_DIR" in str(exc)
        return

    assert smoke.override_data_dir(os.environ) == expected

    # Only the tmp-dir row walks the full resolver: `user_log_file()` goes through
    # `user_data_dir()`, which CREATES the resolved dir — under `~/dsync` that would
    # litter the developer's home, and under a blank value it would fall through to
    # the REAL ladder and touch the real profile ("never probe the real profile").
    if value != "<abs-tmp>":
        return
    assert smoke.etl_log_candidates(Path.home(), _platform.system(), os.environ)[0] == paths.user_log_file()


# --------------------------------------------------------------------------- #
#  CLI-smoke pure helpers                                                       #
# --------------------------------------------------------------------------- #


def test_last_run_record_parses_the_final_line(tmp_path: Path) -> None:
    tail = (
        '2026-01-01 - src.etl.pipeline - INFO - __DISTRICTSYNC_RUN__ {"status": "failed", "source": "cli"}\n'
        '2026-01-01 - src.etl.pipeline - INFO - __DISTRICTSYNC_RUN__ {"status": "success", "source": "cli"}\n'
    )
    assert smoke._last_run_record(tail) == ({"status": "success", "source": "cli"}, "")


def test_last_run_record_distinguishes_absent_from_unparseable() -> None:
    """ "No record at all" and "a record that would not parse" are different bugs.

    A bare ``None`` made a run that died before the log sink look identical to a
    reformatted/corrupted payload — the CI reader could not tell which to chase.
    """
    missing_record, missing_why = smoke._last_run_record("nothing to see here")
    broken_record, broken_why = smoke._last_run_record("INFO - __DISTRICTSYNC_RUN__ {not json")

    assert missing_record is None and broken_record is None
    assert "no __DISTRICTSYNC_RUN__ line" in missing_why
    assert "did not parse" in broken_why
    assert missing_why != broken_why


def test_file_signature_detects_a_rewrite(tmp_path: Path) -> None:
    """The "history.db untouched by the preview" check rests on this.

    (The corrupt-profile phase's "planted bytes intact" check does NOT — it compares
    ``read_bytes()`` directly, because a same-size rewrite of a 47-byte config is
    exactly the tamper it is looking for.)
    """
    f = tmp_path / "history.db"
    assert smoke._file_signature(f) is None
    f.write_bytes(b"x")
    before = smoke._file_signature(f)
    assert before is not None and before[0] == 1  # (size, mtime_ns) — no dead "exists" flag
    f.write_bytes(b"xx")
    after = smoke._file_signature(f)
    assert after is not None and after != before


def test_cli_smoke_context_gives_each_phase_its_own_output_dir(tmp_path: Path) -> None:
    ctx = smoke.CliSmokeContext.build(tmp_path / "in", tmp_path / "work", {})
    first = ctx.new_output("dry-run")
    second = ctx.new_output("dry-run")
    assert first != second and first.is_dir() and second.is_dir()


def test_cli_smoke_context_log_text_is_empty_when_absent(tmp_path: Path) -> None:
    ctx = smoke.CliSmokeContext.build(tmp_path / "in", tmp_path / "work", {"DISTRICTSYNC_DATA_DIR": str(tmp_path)})
    assert ctx.seam_dir == tmp_path.resolve()
    assert ctx.log_text() == ""


def test_cli_smoke_context_store_path_sits_beside_the_log(tmp_path: Path) -> None:
    # The store assertions must key off the SAME profile the log resolved to — a
    # store path derived from anywhere else would be asserting about another install.
    ctx = smoke.CliSmokeContext.build(tmp_path / "in", tmp_path / "work", {"DISTRICTSYNC_DATA_DIR": str(tmp_path)})
    assert ctx.store_path == tmp_path.resolve() / "history.db"
    assert ctx.store_path.parent == ctx.log_path.parent


def test_corrupt_profile_phase_refuses_to_run_without_the_seam(tmp_path: Path) -> None:
    """Fail LOUD rather than plant a corrupt config.json into whatever profile is real."""
    ctx = smoke.CliSmokeContext.build(tmp_path / "in", tmp_path / "work", {})
    assert ctx.seam_dir is None
    assert smoke._smoke_corrupt_profile(tmp_path / "DistrictSync.exe", ctx) is False


def test_corrupt_profile_phase_prints_the_log_before_it_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The phase's own diagnostic must reach stdout, not the recycle bin.

    It used to ``rmtree`` the whole seam dir in its ``finally`` — which deleted
    ``etl_tool.log`` before ``run_cli_smoke``'s failure path could print it, so a red
    phase 5 reported "(absent)" instead of the traceback that explained it. Now the
    phase prints the slice it already holds in memory, and removes ONLY the planted
    ``config.json``.
    """
    seam = tmp_path / "profile"
    seam.mkdir()
    (seam / "etl_tool.log").write_text("BOOT LINE ONE\nDISTINCTIVE-LOG-MARKER\n", encoding="utf-8")
    ctx = smoke.CliSmokeContext.build(tmp_path / "in", tmp_path / "work", {"DISTRICTSYNC_DATA_DIR": str(seam)})

    # Make the exe "run" without launching anything: an exit-1 result fails the phase.
    def _fake_run(art: Path, args: list[str]) -> object:
        (seam / "etl_tool.log").write_text(
            "BOOT LINE ONE\nDISTINCTIVE-LOG-MARKER\nfailure detail the reader needs\n", encoding="utf-8"
        )
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(smoke, "_run_cli", _fake_run)

    assert smoke._smoke_corrupt_profile(tmp_path / "DistrictSync.exe", ctx) is False

    out = capsys.readouterr().out
    assert "failure detail the reader needs" in out, "the failure branch must print the log tail"
    # Cleanup is narrowed to the plant: the log (and the dir) survive for the caller.
    assert not (seam / "config.json").exists()
    assert (seam / "etl_tool.log").exists()
    assert seam.is_dir()


def test_cli_smoke_phase_order_puts_the_plant_last() -> None:
    # This dict is the ONLY definition of the order, and the workflow runs one
    # `--phase all` invocation so CI cannot hold a second, drifting copy. Three facts
    # are load-bearing: dry-run pins history.db ABSENT before write-run asserts it was
    # created; user-overlay (plan 0044 S7) plants a mapping YAML and needs a working
    # profile for its own negative control, so it follows write-run; and corrupt-profile
    # plants a config.json every later phase would read, so it must go last.
    assert list(smoke.CLI_SMOKE_PHASES) == [
        "version",
        "dry-run",
        "write-run",
        "user-overlay",
        "corrupt-profile",
    ]


def test_cli_smoke_missing_artifact_fails_without_launching(tmp_path: Path) -> None:
    rc = smoke.run_cli_smoke(
        tmp_path / "dist", "DistrictSync", phase="all", input_dir=tmp_path / "in", work_dir=tmp_path / "work"
    )
    assert rc == 1


def _dist_with_artifact(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "DistrictSync.exe").write_bytes(b"x")  # never launched in these tests
    return dist


@pytest.mark.parametrize("phase", ["version", "dry-run", "write-run", "user-overlay", "corrupt-profile", "all"])
def test_cli_smoke_refuses_every_phase_without_the_seam(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], phase: str
) -> None:
    """The seam is a BOUNDARY, not a warning — and it guards EVERY phase.

    Every phase writes into whatever profile the exe resolves (a log, a run store,
    and in phase 5 a corrupt config.json), so refusing only at the one that plants
    bytes left the others free to scribble on a real install.
    """
    rc = smoke.run_cli_smoke(
        _dist_with_artifact(tmp_path),
        "DistrictSync",
        phase=phase,
        input_dir=tmp_path / "in",  # absent: proves the refusal precedes the fixture check
        work_dir=tmp_path / "work",
    )
    assert rc == 1
    assert "DISTRICTSYNC_DATA_DIR" in capsys.readouterr().out


def test_cli_smoke_allow_real_profile_opts_out_of_the_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The opt-out is explicit and deliberate: with it, the run proceeds past the seam
    # boundary and fails on the NEXT gate (the absent fixture) instead.
    rc = smoke.run_cli_smoke(
        _dist_with_artifact(tmp_path),
        "DistrictSync",
        phase="all",
        input_dir=tmp_path / "absent",
        work_dir=tmp_path / "work",
        allow_real_profile=True,
    )
    assert rc == 1
    assert "fixture input dir not found" in capsys.readouterr().out


def test_cli_smoke_missing_fixture_fails_before_any_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", str(tmp_path / "profile"))
    rc = smoke.run_cli_smoke(
        _dist_with_artifact(tmp_path),
        "DistrictSync",
        phase="all",
        input_dir=tmp_path / "absent",
        work_dir=tmp_path / "work",
    )
    assert rc == 1
    assert "fixture input dir not found" in capsys.readouterr().out


def test_cli_smoke_rejects_a_relative_seam_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A relative override is refused at the boundary (mirroring paths._override_data_dir)
    # rather than silently resolving against a CWD the frozen exe deletes on exit.
    monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", "relative-profile")
    rc = smoke.run_cli_smoke(
        _dist_with_artifact(tmp_path),
        "DistrictSync",
        phase="all",
        input_dir=tmp_path / "in",
        work_dir=tmp_path / "work",
    )
    assert rc == 1
    assert "absolute" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
#  Log-marker PARITY — each marker pinned to the src code that emits it          #
# --------------------------------------------------------------------------- #
#
# Every CLI phase keys a check off a literal log substring. The script deliberately
# never imports `src`, so a reworded log line in src/ would not break the build — it
# would quietly turn the matching smoke check into an assertion about a string
# nothing emits any more (a green smoke proving nothing). These tests are the only
# thing tying the two together. `_UNREADABLE_MARKER`'s pair lives beside its
# siblings in tests/test_app_config_crash_safety.py.


def test_banner_marker_is_emitted_by_startup_banner() -> None:
    from src.utils.version import startup_banner

    assert smoke._BANNER_MARKER in startup_banner()


def test_run_record_marker_is_emitted_by_log_run_record(caplog: pytest.LogCaptureFixture) -> None:
    from src.etl import pipeline

    with caplog.at_level(logging.INFO, logger="src.etl.pipeline"):
        pipeline._log_run_record({"status": "success"})
    assert any(smoke._RUN_RECORD_MARKER in r.message for r in caplog.records)


def test_paused_marker_is_emitted_by_the_sync_window_gate(caplog: pytest.LogCaptureFixture) -> None:
    from datetime import date

    from src.config.app_config import AppConfig
    from src.main import _paused_by_sync_window

    cfg = AppConfig(sync_window_enabled=True, sync_window_start="09-01", sync_window_end="06-30")
    logger = logging.getLogger("test_paused_marker")
    with caplog.at_level(logging.INFO, logger="test_paused_marker"):
        assert _paused_by_sync_window(cfg, date(2026, 7, 15), logger) is True
    assert any(smoke._PAUSED_MARKER in r.getMessage() for r in caplog.records)


def test_paused_marker_is_absent_when_the_window_is_off(caplog: pytest.LogCaptureFixture) -> None:
    """The falsifiability half: an in-window (or disabled) run must log NO pause marker.

    Without it, `_PAUSED_MARKER` could be a string that is never emitted at all and
    phase 5's "no sync-window pause" check would pass vacuously forever.
    """
    from datetime import date

    from src.config.app_config import AppConfig
    from src.main import _paused_by_sync_window

    logger = logging.getLogger("test_paused_marker_off")
    with caplog.at_level(logging.INFO, logger="test_paused_marker_off"):
        assert _paused_by_sync_window(AppConfig(), date(2026, 7, 15), logger) is False
    assert not [r for r in caplog.records if smoke._PAUSED_MARKER in r.getMessage()]


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
    # The workflow relies on BOTH defaults: it passes neither --phase nor --input.
    args = smoke._parse_args(["dist", "DistrictSync", "--cli-smoke"])
    assert args.phase == "all"
    assert args.input == smoke.DEFAULT_SMOKE_INPUT
    assert args.input.is_dir(), "the committed SD74 snapshot input is the default fixture"
    assert args.allow_real_profile is False, "the profile seam is required unless explicitly waived"


def test_allow_real_profile_flag_parses() -> None:
    args = smoke._parse_args(["dist", "DistrictSync", "--cli-smoke", "--allow-real-profile"])
    assert args.allow_real_profile is True


def test_cli_smoke_rejects_an_unknown_phase() -> None:
    with pytest.raises(SystemExit):
        smoke._parse_args(["dist", "DistrictSync", "--cli-smoke", "--phase", "nope"])


def test_main_cli_smoke_dispatches_without_launching(tmp_path: Path) -> None:
    # No artifact under dist => run_cli_smoke returns 1 before any subprocess.
    assert smoke.main([str(tmp_path), "DistrictSync", "--cli-smoke"]) == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
