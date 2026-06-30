"""PLAT-3 release-gate smoke for the packed ``DistrictSync-flet`` exe.

Productionizes the throwaway PLAT-0b verifier (``ci_verify_pack.py``) against the
REAL ``src/ui_flet/launcher.py`` artifact and folds in the plan-gate Required
Changes (1-5). It proves three independent things about the packed artifact, kept
as SEPARATE axes so a hiccup in one cannot muddy another's verdict:

  1. **No console (Windows):** the PE Optional-Header ``Subsystem == 2``
     (``IMAGE_SUBSYSTEM_WINDOWS_GUI``) — a deterministic property, not a vibe.
  2. **Offline embed (all OS, gating):** move ``~/.flet`` aside, set
     ``FLET_CLIENT_URL`` to an unreachable host, launch, and poll for
     ``~/.flet/client`` to reappear. With the cache gone and download impossible,
     the ONLY way the client cache can repopulate is extraction of the *embedded*
     bundle — so "cache repopulated" == "client is bundled offline" == "window
     booted". (The build-time cache-populated + packed-archive asserts live in the
     workflow; this is the runtime confirmation.)
  3. **Zero-orphan close (Windows gating via ``--require-close``; Linux/macOS
     INFO-only):** baseline-snapshot ``flet``/``flet.exe`` PIDs BEFORE launch, wait
     for a real top-level window owned by a tree PID to EXIST, post ``WM_CLOSE``
     via pure ``ctypes``/``user32`` (no pywin32), tear down the whole tree, and
     assert ZERO new ``flet``/``flet.exe`` orphans remain (baseline-delta, safe on
     shared runners). ``shell.py`` exits via ``os._exit(0)`` so only the orphan
     COUNT is asserted — never an exit code or a graceful-close log line.

The real process model is ``exe -> re-exec'd python host -> separate flet/flet.exe
view`` (PLAT-0), so the tree walk follows descendants of BOTH the launched PID and
any re-exec'd host child named ``DistrictSync-flet``.

On ANY failure the launcher's boot traceback is in ``~/.districtsync/etl_tool.log``
(it writes there, not stdout, because the exe is windowed) — so a failure prints
that file.

Usage::

    python scripts/ci_flet_pack_smoke.py <dist_dir> <base_name> [--require-close]

Exit 0 = all gating phases passed (close gated only with ``--require-close``);
exit 1 = a gating phase failed.

The PURE helpers (``resolve_artifact``, ``orphan_pids``, ``manifest_has_embed``) are
import-safe and unit-tested in ``tests/test_ci_flet_pack_smoke.py``. Everything
that touches a real process / the filesystem lives under ``run_smoke``.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import platform
import shutil
import subprocess  # nosec B404 — launches the packed artifact under test, by design
import sys
import time
from collections.abc import Iterable
from pathlib import Path

# --- single-source, env-overridable timeouts (seconds) ---------------------- #
# Embed and close are SEPARATE axes with independent budgets (R3/R6/R7): a slow
# close must never eat into the embed verdict. Defaults are deliberately generous
# for the larger-than-spike exe extracting one-file under Defender / xvfb.
EMBED_TIMEOUT_S = float(os.environ.get("SMOKE_EMBED_TIMEOUT", "150"))
WINDOW_WAIT_S = float(os.environ.get("SMOKE_WINDOW_WAIT", "60"))
CLOSE_TIMEOUT_S = float(os.environ.get("SMOKE_CLOSE_TIMEOUT", "40"))
# A launch that outlives EMBED_TIMEOUT_S without repopulating the cache but is
# still running == "alive but slow" (inconclusive) => one extra launch retry; a
# dead process that never repopulated == true FAIL. See _poll_embed / _phase_embed.

_OSN = platform.system()  # "Windows" / "Linux" / "Darwin"
_HOME = Path.home()
_FLET_HOME = _HOME / ".flet"
_FLET_BAK = _HOME / ".flet_ci_bak"
_ETL_LOG = _HOME / ".districtsync" / "etl_tool.log"

# Image names of the Flutter view process across OSes (lowercased).
_VIEW_NAMES = {"flet", "flet.exe"}


# ===========================================================================
#  PURE helpers (no process / FS side effects) — unit-tested
# ===========================================================================


def resolve_artifact(dist: Path, name: str) -> Path | None:
    """Resolve the packed artifact path under ``dist`` for base ``name``.

    Tries, in order: a Windows ``.exe``, a bare POSIX binary, and a macOS
    ``.app`` bundle's inner ``MacOS/<name>`` executable. Returns the first that
    exists, else ``None``. Pure: only filesystem ``exists`` checks, no launch.
    """
    candidates = [
        dist / f"{name}.exe",
        dist / name,
        dist / f"{name}.app" / "Contents" / "MacOS" / name,
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def orphan_pids(baseline: Iterable[int], current: Iterable[int]) -> set[int]:
    """Return the NEW view PIDs that survived close (baseline-delta).

    ``baseline`` = view PIDs present BEFORE launch; ``current`` = view PIDs present
    AFTER teardown. Only PIDs absent from the baseline count as orphans — so a
    co-tenant's unrelated ``flet.exe`` on a shared runner is never blamed on us.
    Pure set arithmetic.
    """
    return set(current) - set(baseline)


def manifest_has_embed(manifest_text: str) -> bool:
    """Whether a PyInstaller build manifest proves the Flet client is embedded.

    ``flet pack`` adds the client tree as ``(bin_path, "flet_desktop/app")`` and
    compresses it to a per-OS archive (``flet-windows.zip`` / ``flet-macos.tar.gz``
    / ``flet-linux-*.tar.gz``) — so the manifest (e.g. ``Analysis-00.toc``) must
    reference BOTH the ``flet_desktop/app`` destination AND a client archive name.
    Requiring the archive name (not just ``flet_desktop``, which is also a code
    module) is what makes this a real embed proof. Separator-agnostic (Windows
    backslashes vs POSIX slashes in the TOC). Pure string scan.
    """
    text = manifest_text.replace("\\\\", "/").replace("\\", "/").lower()
    has_dest = "flet_desktop/app" in text
    has_archive = any(marker in text for marker in ("flet-windows.zip", "flet-macos.tar.gz", "flet-linux"))
    return has_dest and has_archive


# ===========================================================================
#  Process-tree helpers (psutil) — side-effecting
# ===========================================================================


def _view_pids() -> set[int]:
    """Current PIDs whose process image name is the Flutter view (``flet``/``flet.exe``)."""
    import psutil

    pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            nm = (proc.info["name"] or "").lower()
        except Exception:  # nosec B112 — a vanished/zombie proc is simply skipped
            continue
        if nm in _VIEW_NAMES:
            pids.add(proc.info["pid"])
    return pids


def _tree_pids(root_pid: int) -> set[int]:
    """All live PIDs in ``root_pid``'s tree, including any re-exec'd ``DistrictSync-flet`` host.

    Walks descendants of the launched root AND, defensively, descendants of any
    process whose image is ``DistrictSync-flet*`` (the re-exec'd python host the
    onefile spawns) — the real model is ``exe -> python host -> flet view``.
    """
    import psutil

    pids: set[int] = set()
    roots: list[int] = [root_pid]

    # Add any DistrictSync-flet host process as an extra walk root (belt-and-braces
    # for the re-exec'd host whose parent may not chain back to root_pid cleanly).
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            nm = (proc.info["name"] or "").lower()
        except Exception:  # nosec B112 — vanished proc skipped
            continue
        if nm.startswith("districtsync-flet"):
            roots.append(proc.info["pid"])

    for rpid in roots:
        try:
            parent = psutil.Process(rpid)
        except Exception:  # nosec B112 — root already gone
            continue
        pids.add(rpid)
        try:
            for child in parent.children(recursive=True):
                pids.add(child.pid)
        except Exception:  # nosec B110 — partial tree is still useful
            pass
    return pids


def _kill_tree(root_pid: int, timeout: float) -> None:
    """Terminate the whole tree and wait (bounded) for exit; SIGKILL stragglers."""
    import psutil

    targets: list[psutil.Process] = []
    for pid in _tree_pids(root_pid):
        try:
            targets.append(psutil.Process(pid))
        except Exception:  # nosec B112 — already gone
            continue
    for proc in targets:
        try:
            proc.terminate()
        except Exception:  # nosec B112 — already gone / access denied
            continue
    _gone, alive = psutil.wait_procs(targets, timeout=timeout)
    for proc in alive:
        with contextlib.suppress(Exception):  # nosec B110 — best-effort SIGKILL
            proc.kill()


# ===========================================================================
#  Windows window enumeration + WM_CLOSE (pure ctypes/user32, no pywin32)
# ===========================================================================


def _windows_tree_window_exists(tree: set[int]) -> bool:
    """True if any VISIBLE top-level window is owned by a PID in ``tree`` (Windows)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    found = {"ok": False}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in tree:
            found["ok"] = True
            return False  # stop enumerating
        return True

    user32.EnumWindows(_cb, 0)
    return found["ok"]


def _windows_post_close(tree: set[int]) -> int:
    """Post ``WM_CLOSE`` to every visible top-level window owned by a tree PID.

    Returns the number of windows messaged. Pure ctypes/user32 — no pywin32 dep.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    WM_CLOSE = 0x0010
    count = {"n": 0}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in tree:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            count["n"] += 1
        return True

    user32.EnumWindows(_cb, 0)
    return count["n"]


# ===========================================================================
#  Filesystem move-aside / restore (hardened — R2)
# ===========================================================================


def _move_flet_aside() -> bool:
    """Move ``~/.flet`` aside so only the embedded bundle can repopulate it.

    Returns True if a move happened. GUARDS that ``~/.flet`` is genuinely absent
    afterwards (R5) — a stale ``~/.flet/client`` would otherwise read as an instant
    false-PASS on the embed check.
    """
    if not _FLET_HOME.exists():
        return False
    if _FLET_BAK.exists():
        shutil.rmtree(_FLET_BAK, ignore_errors=True)
    os.replace(_FLET_HOME, _FLET_BAK)
    if _FLET_HOME.exists():
        raise RuntimeError(
            f"move-aside FAILED: {_FLET_HOME} still present after move — embed check "
            "would be a false-PASS off a stale cache; aborting."
        )
    print(f"moved {_FLET_HOME} aside -> {_FLET_BAK} (forcing the embedded-bundle path)")
    return True


def _restore_flet(moved: bool) -> None:
    """Defensively restore ``~/.flet`` from the aside copy (R2).

    Idempotent + fail-loud: if the original was never moved there is nothing to do;
    otherwise remove whatever the run left at ``~/.flet`` (the freshly-extracted
    client) and ``os.replace`` the saved copy back. On failure, raise with an
    actionable "runner ~/.flet may be corrupted" — never swallow. MUST run only
    AFTER the process tree is fully torn down (a live view holds handles on the
    extracted client).
    """
    if not moved:
        return
    if not _FLET_BAK.exists():
        # Nothing to restore from — but the move-aside guard means this only
        # happens if something external deleted the backup; fail loud.
        raise RuntimeError(f"restore FAILED: backup {_FLET_BAK} is gone — runner ~/.flet may be corrupted.")
    try:
        if _FLET_HOME.exists():
            shutil.rmtree(_FLET_HOME, ignore_errors=True)
        os.replace(_FLET_BAK, _FLET_HOME)
        print(f"restored {_FLET_HOME}")
    except Exception as exc:  # fail loud — a half-restored cache must be visible
        raise RuntimeError(
            f"restore FAILED ({exc!r}): runner ~/.flet may be corrupted — backup at {_FLET_BAK}, target {_FLET_HOME}."
        ) from exc


def _print_etl_log() -> None:
    """Print the launcher's boot traceback log (where a windowed exe writes failures)."""
    print(f"--- {_ETL_LOG} (launcher boot log) ---")
    try:
        if _ETL_LOG.exists():
            for line in _ETL_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]:
                print(f"   {line}")
        else:
            print("   (absent — launcher never reached the early-failure path)")
    except Exception as exc:  # diagnostics must not mask the real failure
        print(f"   (could not read log: {exc!r})")


# ===========================================================================
#  Phases
# ===========================================================================


def _check_pe_subsystem(art: Path) -> bool:
    """Windows-only: assert the PE Optional-Header Subsystem is 2 (WINDOWS_GUI)."""
    import pefile

    pe = pefile.PE(str(art), fast_load=True)
    try:
        sub = pe.OPTIONAL_HEADER.Subsystem
    finally:
        pe.close()
    label = "WINDOWS_GUI (no console)" if sub == 2 else f"subsystem {sub} (NOT GUI)"
    print(f"PE subsystem = {sub} ({label})")
    return sub == 2


def _launch(art: Path) -> subprocess.Popen[str]:
    env = {**os.environ, "FLET_CLIENT_URL": "http://127.0.0.1:9"}  # unreachable
    return subprocess.Popen(  # nosec B603 — fixed artifact path under test, no shell
        [str(art)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _poll_embed(proc: subprocess.Popen[str], deadline: float) -> tuple[bool, bool]:
    """Poll for ``~/.flet/client`` to reappear.

    Returns ``(embedded, alive_but_slow)``. ``embedded`` True == the cache
    repopulated (window booted off the embedded bundle). ``alive_but_slow`` is True
    only on the inconclusive path: the deadline passed but the process is still
    running and made no progress — caller may retry the launch once.
    """
    client_dir = _FLET_HOME / "client"
    while time.time() < deadline:
        if client_dir.exists():
            return True, False
        if proc.poll() is not None:  # process died — one last check, then verdict
            return client_dir.exists(), False
        time.sleep(0.5)
    # Deadline hit. If still alive, it's alive-but-slow (inconclusive); if it died
    # without repopulating, that's a true dead FAIL.
    if client_dir.exists():
        return True, False
    return False, proc.poll() is None


def _phase_embed(art: Path) -> tuple[bool, subprocess.Popen[str] | None]:
    """Run the offline-embed phase with one slow-boot retry. Returns (passed, live_proc)."""
    for attempt in (1, 2):
        proc = _launch(art)
        deadline = time.time() + EMBED_TIMEOUT_S
        embedded, slow = _poll_embed(proc, deadline)
        if embedded:
            print(f"offline-embed: PASS (attempt {attempt})")
            return True, proc
        if slow and attempt == 1:
            print("offline-embed: inconclusive (alive but slow) — retrying launch once")
            _kill_tree(proc.pid, timeout=10)
            continue
        # dead, or slow on the retry -> FAIL
        print(f"offline-embed: FAIL (attempt {attempt}; {'still alive' if slow else 'process died'})")
        return False, proc
    return False, None


def _phase_close(proc: subprocess.Popen[str], baseline: set[int], gating: bool) -> bool:
    """Zero-orphan close phase.

    On Windows (or any OS where windows enumerate): wait for a real top-level
    window owned by a tree PID, post ``WM_CLOSE``, tear the tree down, and assert
    no NEW view orphans (baseline-delta). On non-Windows, no portable ``WM_CLOSE``
    exists: do a best-effort ``terminate()`` teardown and report the orphan count
    as INFO. ``gating`` decides whether a non-zero orphan count fails the run.
    """
    tree = _tree_pids(proc.pid)
    print(f"process tree PIDs: {sorted(tree)}")

    if _OSN == "Windows":
        # Wait (bounded) for a window to EXIST before posting WM_CLOSE — "window
        # never painted" is its own distinct failure, not a close failure.
        win_deadline = time.time() + WINDOW_WAIT_S
        window_seen = False
        while time.time() < win_deadline:
            tree = _tree_pids(proc.pid)
            if _windows_tree_window_exists(tree):
                window_seen = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        if not window_seen:
            print("close: FAIL — no top-level window ever painted for the tree")
            _print_etl_log()
            if gating:
                return False
        else:
            posted = _windows_post_close(tree)
            print(f"close: posted WM_CLOSE to {posted} window(s)")

    # Tear the FULL tree down and wait for exit BEFORE any ~/.flet restore (R2).
    _kill_tree(proc.pid, timeout=CLOSE_TIMEOUT_S)

    # Baseline-delta orphan sweep: only NEW view PIDs count.
    remaining = _view_pids()
    orphans = orphan_pids(baseline, remaining)
    print(f"close: new-view orphans after teardown: {sorted(orphans)}")

    if orphans:
        if gating:
            print("close: FAIL — orphaned view process(es) survived")
            return False
        print("close: INFO (non-gating) — orphan(s) present; reported, not failing")
        return True
    print("close: PASS — zero new-view orphans")
    return True


def run_smoke(dist: Path, name: str, require_close: bool) -> int:
    """Run all gating phases against the packed artifact. Returns a process exit code."""
    print(f"== PLAT-3 flet pack smoke on {_OSN} ==")
    art = resolve_artifact(dist, name)
    if not art:
        print(f"FAIL: no artifact under {dist} for base name '{name}'")
        with contextlib.suppress(Exception):  # nosec B110 — diagnostics only
            print("dist dir contents:", sorted(p.name for p in dist.iterdir()))
        return 1
    print(f"artifact: {art}")
    print(f"size: {os.path.getsize(art) / 1e6:.1f} MB")

    # Phase 1 — no-console (Windows only, gating).
    if _OSN == "Windows":
        try:
            if not _check_pe_subsystem(art):
                print("FAIL: expected GUI subsystem (no console)")
                return 1
        except Exception as exc:
            print(f"FAIL: PE subsystem check errored: {exc!r}")
            return 1

    # Baseline view PIDs BEFORE any launch (orphan baseline-delta).
    baseline = _view_pids()

    moved = False
    embed_ok = False
    close_ok = True
    proc: subprocess.Popen[str] | None = None
    try:
        moved = _move_flet_aside()

        # Phase 2 — offline embed (gating, all OS).
        embed_ok, proc = _phase_embed(art)
        if not embed_ok:
            _print_etl_log()

        # Phase 3 — zero-orphan close. Gating only on Windows + --require-close.
        if proc is not None:
            close_gating = require_close and _OSN == "Windows"
            close_ok = _phase_close(proc, baseline, gating=close_gating)
    finally:
        # Ensure the tree is dead before restoring ~/.flet (R2): if the embed phase
        # bailed before close ran, the process may still hold the extracted client.
        if proc is not None and proc.poll() is None:
            _kill_tree(proc.pid, timeout=CLOSE_TIMEOUT_S)
        # surface the artifact's own captured output (diagnostic)
        if proc is not None:
            try:
                out = proc.communicate(timeout=5)[0]
            except Exception:
                out = ""
            if out:
                print("--- artifact output (first 40 lines) ---")
                for line in out.splitlines()[:40]:
                    print(f"   {line}")
        _restore_flet(moved)  # fails loud on corruption — intentionally not swallowed

    # Verdict — embed and close are separate axes.
    print(f"\nembed: {'PASS' if embed_ok else 'FAIL'}  |  close: {'PASS' if close_ok else 'FAIL'}")
    if not embed_ok:
        return 1
    if require_close and _OSN == "Windows" and not close_ok:
        return 1
    return 0


def _assert_embed(manifest: Path) -> int:
    """Build-time embed assert (RC1b): scan a PyInstaller manifest for the client.

    Reuses the PURE ``manifest_has_embed`` helper so the workflow and the unit test
    share one source of truth. Returns a process exit code.
    """
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        print(f"FAIL: cannot read manifest {manifest}: {exc!r}")
        return 1
    if manifest_has_embed(text):
        print(f"embed-assert: PASS — {manifest} references the bundled Flet client")
        return 0
    print(f"FAIL: {manifest} does NOT reference an embedded Flet client archive")
    return 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke the packed DistrictSync-flet exe.")
    parser.add_argument(
        "--assert-embed",
        type=Path,
        metavar="MANIFEST",
        help="build-time check only: assert a PyInstaller manifest embeds the Flet client, then exit.",
    )
    parser.add_argument("dist", type=Path, nargs="?", help="dist directory containing the artifact")
    parser.add_argument("name", nargs="?", help="artifact base name (e.g. DistrictSync-flet)")
    parser.add_argument(
        "--require-close",
        action="store_true",
        help="gate on the zero-orphan close (Windows); absent => close is INFO-only.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.assert_embed is not None:
        return _assert_embed(args.assert_embed)
    if args.dist is None or args.name is None:
        print("FAIL: dist dir and artifact name are required (or use --assert-embed).")
        return 2
    return run_smoke(args.dist, args.name, args.require_close)


if __name__ == "__main__":
    sys.exit(main())
