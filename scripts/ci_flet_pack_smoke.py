"""Release-gate smoke for the packed ``DistrictSync`` exe.

Productionizes the throwaway PLAT-0b verifier (``ci_verify_pack.py``) against the
Flet-default ``src/main.py`` artifact (no-argv branch → ``src/ui_flet/launcher.py``)
and folds in the plan-gate Required Changes (1-5). It proves three independent things
about the packed artifact, kept as SEPARATE axes so a hiccup in one cannot muddy
another's verdict:

  1. **No console (Windows):** the PE Optional-Header ``Subsystem == 2``
     (``IMAGE_SUBSYSTEM_WINDOWS_GUI``) — a deterministic property, not a vibe.
  2. **Offline embed (all OS, gating):** move ``~/.flet`` aside, set
     ``FLET_CLIENT_URL`` to an unreachable host, launch, and poll for
     ``~/.flet/client`` to reappear. With the cache gone and download impossible,
     the ONLY way the client cache can repopulate is extraction of the *embedded*
     bundle — so "cache repopulated" == "client is bundled offline" == "window
     booted". (The build-time cache-populated + packed-archive asserts live in the
     workflow; this is the runtime confirmation.)

     **What the window now shows (0038 S4a):** on a fresh CI profile the first paint
     is the LAUNCH PAGE ("who looks after this sync?"), not the app body — the
     identity gate mounts between the geometry block and the rail. Nothing here
     changes: this script asserts a window EXISTS and closes with zero orphans, never
     what is drawn in it, and the launch page is a normal Flet tree in the same
     window with the same close handlers (bound ABOVE the gate precisely so the
     WM_CLOSE below still lands). The rendered-content gate remains the pre-tag
     QA-checklist walk, which now covers the launch page explicitly.
  3. **Zero-orphan close (Windows gating via ``--require-close``; Linux/macOS
     INFO-only):** baseline-snapshot ``flet``/``flet.exe`` PIDs BEFORE launch, wait
     for a real top-level window owned by a tree PID to EXIST, post ``WM_CLOSE``
     via pure ``ctypes``/``user32`` (no pywin32), tear down the whole tree, and
     assert ZERO new ``flet``/``flet.exe`` orphans remain (baseline-delta, safe on
     shared runners). ``shell.py`` exits via ``os._exit(0)`` so only the orphan
     COUNT is asserted — never an exit code or a graceful-close log line.

The real process model is ``exe -> re-exec'd python host -> separate flet/flet.exe
view`` (PLAT-0), so the tree walk follows descendants of BOTH the launched PID and
any re-exec'd host child named ``DistrictSync``.

On ANY failure the launcher's boot traceback is in ``etl_tool.log`` under the
per-OS DistrictSync app-data dir (it writes there, not stdout, because the exe is
windowed) — so a failure prints that file, probing the retired legacy
``~/.districtsync`` as a secondary fallback.

The same artifact has a SECOND branch — ``--sis/--input/--output``, the one every
district runs nightly — which no CI job exercised at all. ``--cli-smoke`` adds five
phases against the real exe (``--version`` · a dry run · a real write run · a
conversion through a planted USER-DIR mapping overlay · a boot on a corrupt profile);
see the CLI-smokes section below for what each proves. Exactly ONE of the five
(``write-run``) converts the fixture end-to-end and writes CSVs; the other four assert
version/preview/user-overlay/degradation behaviour.

``--cli-smoke`` REFUSES to run without ``DISTRICTSYNC_DATA_DIR`` (the throwaway-profile
seam) unless ``--allow-real-profile`` is passed — the phases write logs, a run store and
(in phases 4 and 5) a corrupt ``config.json`` and a mapping overlay, so an unset seam
would silently exercise the operator's real profile.

Usage::

    python scripts/ci_flet_pack_smoke.py <dist_dir> <base_name> [--require-close]
    python scripts/ci_flet_pack_smoke.py <dist_dir> <base_name> --cli-smoke [--phase P]
    python scripts/ci_flet_pack_smoke.py <mountpoint> <base_name> --artifact bundle
    python scripts/ci_flet_pack_smoke.py --assert-embed <manifest>
    python scripts/ci_flet_pack_smoke.py --assert-mounted-app <mountpoint> <base_name>

**macOS ships TWO artifacts** (plan 0045), so a macOS caller says which one it means
via ``--artifact``: ``bundle`` is the ``.app`` inside the released DMG — the district's
download, and the one the GUI smoke above must prove — while ``binary`` is the bare
CLI binary kept for a headless Mac. Before plan 0045 the resolver's fixed candidate
order silently picked the bare binary on macOS, so every macOS smoke exercised the
artifact districts do NOT download and none exercised the one they do.
``--assert-mounted-app`` is the companion gate: it inspects a MOUNTED DMG for an
intact, still-executable bundle, because ``upload-artifact`` strips POSIX mode bits
and a disk image is how those bits reach a district at all.

Exit 0 = all gating phases passed (close gated only with ``--require-close``);
exit 1 = a gating phase failed.

The PURE helpers (``resolve_artifact``, ``mounted_app_problems``, ``orphan_pids``,
``manifest_has_embed``, ``override_data_dir``, ``etl_log_candidates``) are import-safe
and unit-tested in ``tests/test_ci_flet_pack_smoke.py``. Everything that touches a real
process / the filesystem lives under ``run_smoke`` / ``run_cli_smoke``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import shutil
import subprocess  # nosec B404 — launches the packed artifact under test, by design
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# Which of a pack's artifacts a caller means. macOS produces two (see
# ``resolve_artifact``); Windows and Linux produce one, so ``auto`` is right there.
ArtifactKind = Literal["auto", "binary", "bundle"]

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

# Image names of the Flutter view process across OSes (lowercased).
_VIEW_NAMES = {"flet", "flet.exe"}


# ===========================================================================
#  PURE helpers (no process / FS side effects) — unit-tested
# ===========================================================================


def resolve_artifact(dist: Path, name: str, kind: ArtifactKind = "auto") -> Path | None:
    """Resolve the packed artifact path under ``dist`` for base ``name``.

    ``kind`` selects WHICH artifact, because a macOS ``flet pack`` produces TWO and
    they ship to different audiences (plan 0045):

    * ``auto``   — the historical candidate order: Windows ``.exe``, then the bare
      POSIX binary, then the ``.app`` bundle's inner ``MacOS/<name>``. Unchanged, so
      Windows/Linux callers keep byte-identical behaviour.
    * ``binary`` — ONLY the bare binary (or ``.exe``). Never the bundle.
    * ``bundle`` — ONLY the ``.app``'s inner executable.

    The selector exists instead of a reordered ``auto`` list because macOS ships both
    artifacts: the ``.app`` (inside the DMG, the district's download) and the bare
    binary (the headless/CLI download). Reordering would silently repoint every
    existing call site and leave one of the two untestable; naming the kind keeps
    both addressable and makes each CI step say which artifact it is proving.

    Returns the first candidate that exists, else ``None``. Pure: only filesystem
    ``exists`` checks, no launch.
    """
    exe = dist / f"{name}.exe"
    binary = dist / name
    bundle = dist / f"{name}.app" / "Contents" / "MacOS" / name
    candidates = {
        "auto": [exe, binary, bundle],
        "binary": [exe, binary],
        "bundle": [bundle],
    }[kind]
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def mounted_app_problems(mount: Path, name: str) -> list[str]:
    """Return the reasons a mounted DMG is not shippable — empty list means it is.

    This is the assert that would have caught the bug plan 0045 fixes. The macOS
    release published a bare, extension-less binary that Finder could only open in a
    text editor; the replacement is a DMG, and a DMG is only better if the bundle
    inside it is intact **as delivered**. Three things are checked, each a way the
    image can look built-but-broken:

    * ``<name>.app`` exists on the volume — the image actually carries an app.
    * ``Contents/MacOS/<name>`` exists AND has the executable bit. ``upload-artifact``
      strips POSIX modes, which is precisely why the image is built on the runner;
      this asserts the mode really did survive into the image.
    * ``Applications`` is a SYMLINK. It is what makes the mounted window a drag
      target, and checking it is a symlink also catches ``hdiutil`` having followed
      the link and copied the runner's real ``/Applications`` into the image.

    Pure: ``exists`` / ``is_symlink`` / ``os.access`` checks against a path, so it is
    unit-testable against a synthetic tree on any OS — including the non-executable
    negative twin, which a shell ``test -x`` could not carry.
    """
    problems: list[str] = []
    app = mount / f"{name}.app"
    inner = app / "Contents" / "MacOS" / name
    applications = mount / "Applications"

    if not app.is_dir():
        problems.append(f"no {name}.app on the mounted volume ({app})")
    elif not inner.exists():
        problems.append(f"bundle has no inner executable at {inner}")
    elif not os.access(inner, os.X_OK):
        problems.append(f"inner executable is NOT executable (mode bits lost): {inner}")

    if not applications.is_symlink():
        what = "missing" if not applications.exists() else "present but not a symlink"
        problems.append(f"/Applications drag target is {what}: {applications}")

    return problems


def orphan_pids(baseline: Iterable[int], current: Iterable[int]) -> set[int]:
    """Return the NEW view PIDs that survived close (baseline-delta).

    ``baseline`` = view PIDs present BEFORE launch; ``current`` = view PIDs present
    AFTER teardown. Only PIDs absent from the baseline count as orphans — so a
    co-tenant's unrelated ``flet.exe`` on a shared runner is never blamed on us.
    Pure set arithmetic.
    """
    return set(current) - set(baseline)


# The embedded Flet client, as it appears in a PyInstaller TOC: the
# `flet_desktop/app` destination + an `flet-<os-token>…` archive with a real
# archive extension, in ONE path. Applied to lowercased, forward-slashed text.
CLIENT_ARCHIVE_RE = re.compile(r"flet_desktop/app/flet-(?:windows|macos|linux)[a-z0-9._-]*\.(?:zip|tar\.gz)")


def manifest_has_embed(manifest_text: str) -> bool:
    """Whether a PyInstaller build manifest proves the Flet client is embedded.

    ``flet pack`` adds the client tree as ``(bin_path, "flet_desktop/app")`` and
    compresses it to a per-OS archive — so the manifest (e.g. ``Analysis-00.toc``)
    must carry a client archive AT that destination. Requiring the archive NAME
    (not just ``flet_desktop``, which is also a code module) is what makes this a
    real embed proof, and requiring it in the SAME path as the destination is what
    stops an unrelated ``flet-windows.zip`` elsewhere in the TOC from vouching for
    a bundle that has no client in it.

    Matched as OS token + a real archive extension rather than the three exact
    filenames it used to hardcode: the Linux client already encodes distro, arch
    AND desktop flavor in its name (``flet-linux-ubuntu-22.04-light-x64.tar.gz``),
    so an exact-name list only ever worked there by prefix accident. (Verified in
    ``flet_cli/commands/pack.py`` @0.85.3: Windows/macOS names are currently
    flavor-independent — this is resilience to that changing, not a fix for a
    break today.) Deliberately NOT loosened to a bare ``flet-`` prefix: the
    archive name IS the proof, so the OS token and the extension both stay
    required. Separator-agnostic (Windows backslashes vs POSIX slashes). Pure
    string scan.
    """
    text = manifest_text.replace("\\\\", "/").replace("\\", "/").lower()
    return CLIENT_ARCHIVE_RE.search(text) is not None


def override_data_dir(env: Mapping[str, str]) -> Path | None:
    """The ``DISTRICTSYNC_DATA_DIR`` profile override, or ``None`` when not in play.

    A deliberate non-importing MIRROR of ``src/utils/paths._override_data_dir`` — same
    normalization (blank means unset, ``~`` expands) and the same REFUSAL of a relative
    value (``ValueError``), kept in lockstep by ``tests/test_ci_flet_pack_smoke.py``'s
    parity test. Pure.

    Raises:
        ValueError: the value is set but not absolute.
    """
    raw = env.get("DISTRICTSYNC_DATA_DIR", "").strip()
    if not raw:
        return None
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise ValueError(f"DISTRICTSYNC_DATA_DIR must be an absolute path (got {raw!r})")
    return expanded.resolve()


def etl_log_candidates(home: Path, osn: str, env: Mapping[str, str]) -> list[Path]:
    """Candidate ``etl_tool.log`` paths, the per-OS app-data location first.

    Mirrors ``src/utils/paths.user_data_dir()`` WITHOUT importing ``src`` (this
    script must stay standalone): ``DISTRICTSYNC_DATA_DIR`` if set, else Windows
    ``%LOCALAPPDATA%\\DistrictSync``, macOS ``~/Library/Application Support/
    DistrictSync``, Linux ``$XDG_DATA_HOME`` (default ``~/.local/share``)
    ``/DistrictSync``. The retired legacy ``~/.districtsync`` stays as the
    secondary fallback (a pre-migration profile on an old runner image). Pure path
    arithmetic — no filesystem access.

    The override is step 0 in the app and **wins outright** — so it wins outright
    here too, with the legacy fallback dropped. Mirroring that exactly is the whole
    point: a smoke that fell back would silently read (and report on) the runner's
    REAL profile instead of the throwaway one the exe was pointed at.
    """
    override = override_data_dir(env)
    if override is not None:
        return [override / "etl_tool.log"]
    if osn == "Windows":
        local = env.get("LOCALAPPDATA")
        data_root = Path(local) if local else home / "AppData" / "Local"
    elif osn == "Darwin":
        data_root = home / "Library" / "Application Support"
    else:
        xdg = env.get("XDG_DATA_HOME")
        data_root = Path(xdg) if xdg else home / ".local" / "share"
    return [
        data_root / "DistrictSync" / "etl_tool.log",
        home / ".districtsync" / "etl_tool.log",
    ]


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
    """All live PIDs in ``root_pid``'s tree, including any re-exec'd ``DistrictSync`` host.

    Walks descendants of the launched root AND, defensively, descendants of any
    process whose image is ``DistrictSync*`` (the re-exec'd python host the
    onefile spawns) — the real model is ``exe -> python host -> flet view``.
    """
    import psutil

    pids: set[int] = set()
    roots: list[int] = [root_pid]

    # Add any DistrictSync host process as an extra walk root (belt-and-braces
    # for the re-exec'd host whose parent may not chain back to root_pid cleanly).
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            nm = (proc.info["name"] or "").lower()
        except Exception:  # nosec B112 — vanished proc skipped
            continue
        if nm.startswith("districtsync"):
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


def _print_etl_log(label: str = "launcher boot log") -> None:
    """Print the exe's own log file — the only place a windowed/frozen build reports failures.

    Probes the per-OS app-data location first, then the retired legacy
    ``~/.districtsync`` as a secondary fallback; prints the first log found.

    ``label`` names WHICH log this is for the reader: under the GUI smoke it is the
    launcher's boot traceback, under ``--cli-smoke`` it is the CLI run log (same file,
    different failure story) — a header that says "launcher boot log" while a CLI
    conversion failed sends the reader looking for the wrong thing.
    """
    for log in etl_log_candidates(_HOME, _OSN, os.environ):
        print(f"--- {log} ({label}) ---")
        try:
            if log.exists():
                for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]:
                    print(f"   {line}")
                return
            print("   (absent)")
        except Exception as exc:  # diagnostics must not mask the real failure
            print(f"   (could not read log: {exc!r})")
    print("(no boot log found — launcher never reached the early-failure path)")


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


def run_smoke(dist: Path, name: str, require_close: bool, kind: ArtifactKind = "auto") -> int:
    """Run all gating phases against the packed artifact. Returns a process exit code.

    ``dist`` need not be a build directory: on macOS the release-gating call passes a
    MOUNTED DMG's mountpoint with ``kind="bundle"``, so the phases below run against
    the exact bytes a district downloads rather than an intermediate build output.
    """
    print(f"== PLAT-3 flet pack smoke on {_OSN} ==")
    art = resolve_artifact(dist, name, kind)
    if not art:
        print(f"FAIL: no {kind} artifact under {dist} for base name '{name}'")
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


# ===========================================================================
#  CLI smokes — the packed artifact's OTHER branch (--sis/--input/--output)
# ===========================================================================
#
# The GUI smoke above proves the exe opens a window. Nothing proved the same exe
# still CONVERTS — the branch every district actually runs nightly. These five
# phases run the real artifact against the committed SD74 snapshot extract and assert
# EXIT CODES first, strings second. Only `write-run` converts end-to-end and writes
# CSVs; `version` starts the exe, `dry-run` previews without writing, `user-overlay`
# converts through a mapping config planted in the profile (never bundled in the exe),
# and `corrupt-profile` boots on a planted config.
#
# Every phase is pointed at a throwaway profile via DISTRICTSYNC_DATA_DIR (the
# `src/utils/paths` step-0 seam): platformdirs ignores LOCALAPPDATA on Windows, so
# that env var is the only way to keep a frozen exe off the runner's real profile.
# `run_cli_smoke` REFUSES to run any phase without it (--allow-real-profile opts out).

# 120s per invocation: the slowest observed phase is the write run over the SD74
# fixture, and a one-file exe's self-extract dominates it. Generous enough for a cold
# Defender scan, tight enough that a hung exe fails the job in minutes, not in five.
CLI_TIMEOUT_S = float(os.environ.get("SMOKE_CLI_TIMEOUT", "120"))

# The fixture: the committed SD74 snapshot extract, converted through the LIVE
# sd74myedbc mapping (the exe bundles config/mappings — not the frozen snapshot
# config). Row COUNTS and values are never asserted here; the golden snapshot test
# owns those. These phases prove the packed artifact runs, writes, and logs.
SMOKE_SIS = "sd74myedbc"
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SMOKE_INPUT = _REPO_ROOT / "tests" / "snapshots" / "input"

# The 5 SpacesEDU rostering entities sd74myedbc emits.
_ROSTERING_ENTITIES = ("Students", "Staff", "Family", "Classes", "Enrollments")
_ROSTER_ANCHOR_CSV = "Students.csv"
_UTF8_BOM = b"\xef\xbb\xbf"

# Log markers the phases key off (all owned by src/, cited at their source).
_RUN_RECORD_MARKER = "__DISTRICTSYNC_RUN__"  # pipeline._log_run_record
_BANNER_MARKER = "data dir:"  # utils/version.startup_banner
_UNREADABLE_MARKER = "could not be read as settings"  # AppConfig.load, UNREADABLE provenance
_PAUSED_MARKER = "Sync paused"  # main._paused_by_sync_window


@dataclass(frozen=True)
class CliSmokeContext:
    """Everything the CLI phases share: the fixture, a scratch root, and the profile seam."""

    input_dir: Path
    work_dir: Path
    seam_dir: Path | None
    log_path: Path

    @classmethod
    def build(cls, input_dir: Path, work_dir: Path, env: Mapping[str, str]) -> CliSmokeContext:
        return cls(
            input_dir=input_dir,
            work_dir=work_dir,
            seam_dir=override_data_dir(env),
            log_path=etl_log_candidates(_HOME, _OSN, env)[0],
        )

    @property
    def store_path(self) -> Path:
        """The run store (``history.db``) inside the SAME profile the log resolved to.

        Derived from ``log_path.parent`` rather than ``seam_dir`` so it is a plain
        ``Path`` (never ``None``) and stays correct under ``--allow-real-profile``,
        where there is no seam. With the seam in play — the gated default — this IS
        ``seam_dir / "history.db"``.
        """
        return self.log_path.parent / "history.db"

    def new_output(self, label: str) -> Path:
        """A fresh, unique output dir — never shared between phases (or reruns)."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=f"out-{label}-", dir=self.work_dir))

    def log_text(self) -> str:
        """The exe's log as text, or ``""`` when it does not exist yet."""
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def _expect(ok: bool, label: str, detail: str = "") -> bool:
    """Report one check and return it. Never short-circuits — every check is reported."""
    print(f"   {'ok  ' if ok else 'FAIL'} {label}{f' -> {detail}' if detail else ''}")
    return ok


def _run_cli(art: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the packed artifact's CLI branch and capture stdout/stderr.

    ``capture_output`` PIPES are sound even for the GUI-subsystem Windows exe:
    ``_attach_parent_console``'s policy #2 (src/main.py) is "all streams already
    usable -> no-op", and where it does attach, policy #4 rebinds ONLY the dead
    (``None``) streams. Either way our pipes survive, so the CLI's ``print``
    output really does reach this process.
    """
    return subprocess.run(  # nosec B603 — fixed artifact path under test, no shell
        [str(art), *args],
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_S,
        check=False,
    )


def _convert_args(ctx: CliSmokeContext, out: Path, *extra: str) -> list[str]:
    """The standard conversion invocation (absolute paths — cwd is never assumed)."""
    return ["--sis", SMOKE_SIS, "--input", str(ctx.input_dir), "--output", str(out), *extra]


def _dump(proc: subprocess.CompletedProcess[str]) -> None:
    """Print the artifact's captured output (diagnostics for a failed phase)."""
    for stream, text in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        if text:
            print(f"   --- artifact {stream} (last 30 lines) ---")
            for line in text.splitlines()[-30:]:
                print(f"      {line}")


def _file_signature(path: Path) -> tuple[int, int] | None:
    """``(size, mtime_ns)`` for a touched/untouched comparison; ``None`` when absent.

    Absence is carried by the ``None``, so an always-``True`` "exists" element in the
    tuple would be dead weight — comparing two signatures already answers both
    "still absent?" and "still byte-identical?".
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _last_run_record(log_tail: str) -> tuple[dict[str, Any] | None, str]:
    """Parse the LAST ``__DISTRICTSYNC_RUN__`` payload in a log slice.

    Returns ``(record, reason)``: the parsed dict and ``""`` on success, else ``None``
    plus a reason that DISTINGUISHES the two failures — "the exe never logged a run
    record" (a run that died before the sink, or a log the smoke is not reading) and
    "it logged one but the payload is not JSON" (a corrupted/reformatted line) are
    completely different bugs, and a bare ``None`` made them look identical.
    """
    lines = [ln for ln in log_tail.splitlines() if _RUN_RECORD_MARKER in ln]
    if not lines:
        return None, f"no {_RUN_RECORD_MARKER} line in this run's log slice"
    try:
        return json.loads(lines[-1].split(f"{_RUN_RECORD_MARKER} ", 1)[1]), ""
    except (IndexError, ValueError) as exc:
        return None, f"{_RUN_RECORD_MARKER} line found but its payload did not parse ({exc})"


def _smoke_version(art: Path, ctx: CliSmokeContext) -> bool:
    """1 — ``--version``: the exe starts, reports its stamped version, and opens its log sink.

    The log assertion is the load-bearing half: it proves the frozen logging config
    resolved to the SAME profile this script computes (``etl_log_candidates``), so
    every later phase is reading the artifact's real output rather than an empty path.
    """
    proc = _run_cli(art, ["--version"])
    stdout = proc.stdout.strip()
    text = ctx.log_text()
    banners = [ln for ln in text.splitlines() if _BANNER_MARKER in ln]
    checks = [
        _expect(proc.returncode == 0, "exit 0", f"got {proc.returncode}"),
        _expect(stdout.startswith("DistrictSync "), "stdout starts with 'DistrictSync '", stdout[:60]),
        _expect(bool(text), f"log written at the seam-resolved path {ctx.log_path}"),
        _expect(bool(banners), "startup banner in the log", banners[-1][-90:] if banners else "absent"),
    ]
    if not all(checks):
        _dump(proc)
    return all(checks)


def _smoke_dry_run(art: Path, ctx: CliSmokeContext) -> bool:
    """2 — ``--dry-run``: the packed exe previews a real conversion and writes nothing.

    Row counts are deliberately NOT asserted (the SD74 golden owns values). The
    run-store assertion is flag 7's end-to-end proof: a preview must not enter the
    ledger, checked on the real artifact rather than only in unit tests.
    """
    out = ctx.new_output("dry-run")
    before_log = len(ctx.log_text())
    store = ctx.store_path
    store_before = _file_signature(store)

    proc = _run_cli(art, _convert_args(ctx, out, "--dry-run"))
    tail = ctx.log_text()[before_log:]
    record, why = _last_run_record(tail)
    missing = [name for name in _ROSTERING_ENTITIES if name not in proc.stdout]
    store_after = _file_signature(store)

    checks = [
        _expect(proc.returncode == 0, "exit 0", f"got {proc.returncode}"),
        _expect("=== DRY RUN" in proc.stdout, "'=== DRY RUN' banner on stdout"),
        _expect(not missing, "all 5 rostering entities previewed", f"missing {missing}" if missing else ""),
        _expect(not list(out.glob("*.csv")), "no CSV written by the preview"),
        _expect(record is not None, f"{_RUN_RECORD_MARKER} line in the log", why),
        _expect(
            bool(record) and record.get("status") == "success",
            "record status=success",
            f"got {record.get('status') if record else why!r}",
        ),
        _expect(
            bool(record) and record.get("source") == "cli",
            "record source=cli",
            f"got {record.get('source') if record else why!r}",
        ),
        # UNCONDITIONAL (never skipped on a missing seam — `run_cli_smoke` refuses
        # before any phase runs, so a skip here could only ever be vacuous).
        _expect(
            store_after == store_before,
            "history.db untouched by the preview (flag 7)",
            f"{store}: before={store_before} after={store_after}",
        ),
    ]
    if not all(checks):
        _dump(proc)
    return all(checks)


def _smoke_write_run(art: Path, ctx: CliSmokeContext) -> bool:
    """3 — a REAL write run: the packed exe's atomic-write path, exercised nowhere else.

    Asserts the delivered shape only — the five CSVs exist and are non-empty, the
    roster anchor carries the UTF-8 BOM SpacesEDU/Excel needs, and no ``.tmp_*``
    staging dir survived the transactional commit.

    It also carries flag 7's POSITIVE half: the store file MUST exist afterwards.
    Phase 2 pinned ``history.db`` absent, so "created here" is unambiguous — without
    it, a store that never wrote anything at all would satisfy the preview's
    untouched-check and look like a pass.
    """
    out = ctx.new_output("write-run")
    proc = _run_cli(art, _convert_args(ctx, out))

    empty = [
        name
        for name in _ROSTERING_ENTITIES
        if not (out / f"{name}.csv").is_file() or not (out / f"{name}.csv").stat().st_size
    ]
    anchor = out / _ROSTER_ANCHOR_CSV
    head = anchor.read_bytes()[:3] if anchor.is_file() else b""
    staging = [p.name for p in out.glob(".tmp_*")]
    store = ctx.store_path

    checks = [
        _expect(proc.returncode == 0, "exit 0", f"got {proc.returncode}"),
        _expect(not empty, "all 5 rostering CSVs written non-empty", f"missing/empty {empty}" if empty else ""),
        _expect(head == _UTF8_BOM, f"{_ROSTER_ANCHOR_CSV} starts with the UTF-8 BOM", repr(head)),
        _expect(not staging, "no .tmp_* staging dir left behind", str(staging) if staging else ""),
        _expect(store.is_file(), "the real run entered the run store (history.db created)", str(store)),
    ]
    if not all(checks):
        _dump(proc)
    return all(checks)


# The user-dir overlay phase 5 plants (plan 0044 S7 §7.2). TWO constants, one fact:
# `_SD93_OVERLAY_SPEC` is the district FACTS as the kwargs `src.config.authoring.OverlaySpec`
# takes, and `_SD93_OVERLAY_YAML` is what the authoring layer emits for them. This script
# never imports `src` (it runs against a FROZEN artifact, not this checkout), so the YAML is
# a literal — and `tests/test_config_authoring.py` ties the literal back to
# `authoring.build_overlay` for that very spec, so it cannot drift into a shape the app would
# never write. The renames reproduce the SD74 snapshot extract's real filenames, which is
# what makes the fixture in `DEFAULT_SMOKE_INPUT` loadable through a `myedbc` base.
_SD93_OVERLAY_SIS = "sd93custom"
_SD93_OVERLAY_SPEC: dict[str, Any] = {
    "sd_number": 93,
    "district_name": "SD93 - Packed exe smoke",
    "district_domains": ("sd93.bc.ca",),
    "base": "myedbc",
    "source_file_renames": {
        "StaffInformationEnhanced.txt": "StaffInformation.txt",
        "EmergencyContactInformation.txt": "ParentInformation.txt",
        "StudentSchedule.txt": "studentcourseselection.txt",
        "ClassInformationEnh.txt": "ClassInfoEnhanced.txt",
    },
}
_SD93_OVERLAY_YAML = """_base: myedbc
district_name: SD93 - Packed exe smoke
district_domains:
- sd93.bc.ca
global_config:
  school_year_sources:
    student_schedule: studentcourseselection.txt
mappings:
  Classes:
    source_files:
      student_schedule: studentcourseselection.txt
      staff_info: StaffInformation.txt
      class_info: ClassInfoEnhanced.txt
  Enrollments:
    source_files:
      student_schedule: studentcourseselection.txt
  Family:
    source_files:
      emergency_contacts: ParentInformation.txt
  Staff:
    source_files:
      staff_info: StaffInformation.txt
"""


def _smoke_user_overlay(art: Path, ctx: CliSmokeContext) -> bool:
    """5 — a USER-DIR mapping overlay: the frozen exe reads one and converts through it.

    **What this phase claims:** the MECHANISM — a packed one-file exe resolves
    ``paths.user_mappings_dir()`` inside the profile seam, finds a district config that is
    NOT bundled in the exe, deep-merges its ``_base:`` against the bundled base, and runs
    the real conversion through the result. That is the whole of plan 0044's promise that a
    district can add its own mapping without a release.

    **What it does NOT claim:** anything about a district's own content. No CI gate ever
    validates a user-dir config (that is why the FAILED-run floor in
    ``tests/test_pipeline_run_store.py`` exists) — this proves only that the path works.

    Shaped like phase 4, for the same reason: it plants bytes, so it REFUSES without
    ``DISTRICTSYNC_DATA_DIR`` and ``--allow-real-profile`` does not extend to it. The
    NEGATIVE CONTROL runs BEFORE the plant — the exe must not already know
    ``sd93custom`` — so the exit 0 afterwards cannot be true for any other reason.
    """
    if ctx.seam_dir is None:
        print("   FAIL requires DISTRICTSYNC_DATA_DIR — refusing to plant a mapping config in a real profile")
        return False

    overlay = ctx.seam_dir / "mappings" / f"{_SD93_OVERLAY_SIS}_mapping.yaml"
    args = ["--sis", _SD93_OVERLAY_SIS, "--input", str(ctx.input_dir)]
    try:
        control_out = ctx.new_output("user-overlay-control")
        control = _run_cli(art, [*args, "--output", str(control_out), "--dry-run"])

        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(_SD93_OVERLAY_YAML, encoding="utf-8")

        out = ctx.new_output("user-overlay")
        before_log = len(ctx.log_text())
        proc = _run_cli(art, [*args, "--output", str(out), "--dry-run"])
        tail = ctx.log_text()[before_log:]
        missing = [name for name in _ROSTERING_ENTITIES if name not in proc.stdout]

        checks = [
            _expect(
                control.returncode != 0,
                "the un-planted id exits NON-ZERO (the exe does not already know it)",
                f"got {control.returncode}",
            ),
            _expect(proc.returncode == 0, "exit 0 with the overlay in the profile", f"got {proc.returncode}"),
            _expect("=== DRY RUN" in proc.stdout, "'=== DRY RUN' banner on stdout"),
            _expect(
                not missing,
                "all 5 rostering entities previewed through the overlay",
                f"missing {missing}" if missing else "",
            ),
            _expect(
                _SD93_OVERLAY_SIS in tail,
                "this run's log slice names the user-dir district",
                f"looked for {_SD93_OVERLAY_SIS!r} in {len(tail)} new log chars",
            ),
            _expect(not list(out.glob("*.csv")), "no CSV written by the preview"),
        ]
        if not all(checks):
            _dump(proc)
            print(f"   --- {ctx.log_path} (this phase's new log lines) ---")
            for line in tail.splitlines()[-40:]:
                print(f"      {line}")
        return all(checks)
    finally:
        # ONLY the planted YAML. The `mappings/` dir it needed is left in place — an empty
        # dir is not a config, and the profile dir is the caller's ($RUNNER_TEMP in CI).
        overlay.unlink(missing_ok=True)


def _smoke_corrupt_profile(art: Path, ctx: CliSmokeContext) -> bool:
    """4 — boot on a CORRUPT ``config.json``: degrade honestly, and never overwrite it.

    ``--dry-run --source scheduled`` is the one CLI shape that actually LOADS
    ``AppConfig`` (the sync-window gate); ``--version`` exits before any config read,
    so a probe built on it would prove nothing. Runs LAST because it plants bytes into
    the profile, and removes ONLY the planted ``config.json`` in a ``finally`` — the
    profile dir itself belongs to the caller (``$RUNNER_TEMP`` in CI) and every one of
    this phase's diagnostics lives inside it: deleting the whole dir here destroyed
    ``etl_tool.log`` before the failure path could print it, so a red phase 4 reported
    "(absent)" instead of the traceback that explained it.
    """
    if ctx.seam_dir is None:
        # Defence in depth: `run_cli_smoke` already refuses an unset seam, and
        # --allow-real-profile does NOT extend to this phase — it plants bytes.
        print("   FAIL requires DISTRICTSYNC_DATA_DIR — refusing to plant a corrupt config in a real profile")
        return False

    planted = b'{"sis_type": "sd74myedbc", "output_dir": "/tmp/rost'  # truncated mid-document
    config_file = ctx.seam_dir / "config.json"
    out = ctx.new_output("corrupt-profile")
    try:
        ctx.seam_dir.mkdir(parents=True, exist_ok=True)
        config_file.write_bytes(planted)
        before_log = len(ctx.log_text())

        proc = _run_cli(art, _convert_args(ctx, out, "--dry-run", "--source", "scheduled"))
        tail = ctx.log_text()[before_log:]
        after = config_file.read_bytes() if config_file.is_file() else b""

        checks = [
            _expect(
                proc.returncode == 0, "exit 0 (a corrupt profile degrades, never crashes)", f"got {proc.returncode}"
            ),
            _expect(
                "=== DRY RUN" in proc.stdout,
                "the run PROCEEDED (not aborted, not window-paused)",
                f"stdout tail {proc.stdout.strip().splitlines()[-1:]}",
            ),
            _expect(
                _PAUSED_MARKER not in tail,
                "no sync-window pause on a defaults-only config",
                f"found {_PAUSED_MARKER!r} in this run's log slice",
            ),
            _expect(
                _UNREADABLE_MARKER in tail,
                "UNREADABLE provenance logged — the plant was READ",
                f"looked for {_UNREADABLE_MARKER!r} in {len(tail)} new log chars",
            ),
            _expect(
                after == planted,
                "planted bytes byte-identical (never silently rewritten)",
                f"{len(planted)}B planted, {len(after)}B on disk",
            ),
        ]
        if not all(checks):
            _dump(proc)
            # Print the log slice BEFORE the finally-cleanup — the failure story is in
            # it, and it is already in memory here.
            print(f"   --- {ctx.log_path} (this phase's new log lines) ---")
            for line in tail.splitlines()[-40:]:
                print(f"      {line}")
        return all(checks)
    finally:
        # Remove ONLY what this phase planted. The profile dir is the caller's.
        config_file.unlink(missing_ok=True)


# Ordered — and this dict is the ONLY place the order lives. The workflow runs a single
# `--phase all` invocation precisely so CI cannot disagree with it. Three ordering facts:
# `dry-run` pins history.db ABSENT, which is what makes `write-run`'s "the store was
# created" check unambiguous; `user-overlay` sits after `write-run` because its own
# negative control needs the profile to be a working one, not a bare dir; and
# `corrupt-profile` plants a config.json every later phase would read, so it runs LAST.
CLI_SMOKE_PHASES: dict[str, Callable[[Path, CliSmokeContext], bool]] = {
    "version": _smoke_version,
    "dry-run": _smoke_dry_run,
    "write-run": _smoke_write_run,
    "user-overlay": _smoke_user_overlay,
    "corrupt-profile": _smoke_corrupt_profile,
}

_NO_SEAM_FAIL = (
    "FAIL: DISTRICTSYNC_DATA_DIR is not set — the CLI smokes write a log, a run store and "
    "(phase 4) a corrupt config.json, so an unset seam would exercise the REAL user profile. "
    "Point it at a throwaway absolute path, or pass --allow-real-profile if you truly mean to."
)


def run_cli_smoke(
    dist: Path,
    name: str,
    *,
    phase: str,
    input_dir: Path,
    work_dir: Path,
    allow_real_profile: bool = False,
    kind: ArtifactKind = "auto",
) -> int:
    """Run one CLI smoke phase (or ``all``) against the packed artifact. Returns an exit code.

    The throwaway-profile seam is a BOUNDARY, not a warning: without
    ``DISTRICTSYNC_DATA_DIR`` every phase is refused up front (not just the one that
    plants bytes), because they all write into whatever profile the exe resolves.
    ``allow_real_profile`` is the explicit, deliberate opt-out — it does NOT extend to
    ``corrupt-profile``, which refuses on its own regardless.
    """
    art = resolve_artifact(dist, name, kind)
    if not art:
        print(f"FAIL: no {kind} artifact under {dist} for base name '{name}'")
        return 1
    try:
        ctx = CliSmokeContext.build(input_dir, work_dir, os.environ)
    except ValueError as exc:  # a set-but-relative DISTRICTSYNC_DATA_DIR
        print(f"FAIL: {exc}")
        return 1
    print(f"== CLI smoke ({phase}) on {_OSN} ==")
    print(f"artifact: {art}")
    print(f"fixture:  {ctx.input_dir}")
    print(f"profile:  {ctx.seam_dir if ctx.seam_dir else '(DISTRICTSYNC_DATA_DIR unset — real profile!)'}")
    print(f"scratch:  {ctx.work_dir}")
    if ctx.seam_dir is None and not allow_real_profile:
        print(_NO_SEAM_FAIL)
        return 1
    if not ctx.input_dir.is_dir():
        print(f"FAIL: fixture input dir not found: {ctx.input_dir}")
        return 1

    selected = CLI_SMOKE_PHASES if phase == "all" else {phase: CLI_SMOKE_PHASES[phase]}
    for label, fn in selected.items():
        print(f"-- {label} --")
        try:
            passed = fn(art, ctx)
        except Exception as exc:  # noqa: BLE001 - a smoke reports failures, it never raises them
            print(f"   FAIL {label} errored: {exc!r}")
            passed = False
        if not passed:
            print(f"cli smoke: FAIL ({label})")
            _print_etl_log("CLI run log")
            return 1
        print(f"   {label}: PASS")
    print("cli smoke: PASS")
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


def _assert_mounted_app(mount: Path, name: str) -> int:
    """macOS release gate: report a mounted DMG's layout problems. Exit code.

    Reuses the PURE ``mounted_app_problems`` so the workflow and the unit rows share
    one source of truth (same shape as ``_assert_embed``). Prints EVERY problem
    rather than the first, so one CI run tells the whole story.
    """
    if not mount.is_dir():
        print(f"FAIL: mountpoint does not exist or is not a directory: {mount}")
        return 1
    problems = mounted_app_problems(mount, name)
    if not problems:
        print(f"dmg-assert: PASS — {mount} carries an executable {name}.app + /Applications symlink")
        return 0
    print(f"FAIL: mounted image at {mount} is not shippable:")
    for problem in problems:
        print(f"  - {problem}")
    with contextlib.suppress(Exception):  # nosec B110 — diagnostics only
        print("volume contents:", sorted(p.name for p in mount.iterdir()))
    return 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke the packed DistrictSync exe.")
    parser.add_argument(
        "--assert-embed",
        type=Path,
        metavar="MANIFEST",
        help="build-time check only: assert a PyInstaller manifest embeds the Flet client, then exit.",
    )
    parser.add_argument(
        # Two values, not one-plus-the-positional: `--assert-mounted-app MNT NAME`
        # would otherwise bind NAME to the `dist` positional and leave `name` None.
        "--assert-mounted-app",
        nargs=2,
        metavar=("MOUNTPOINT", "NAME"),
        help=(
            "macOS release gate: assert a MOUNTED DMG carries an intact, executable "
            "NAME.app beside an /Applications symlink, then exit."
        ),
    )
    parser.add_argument("dist", type=Path, nargs="?", help="dist directory containing the artifact")
    parser.add_argument("name", nargs="?", help="artifact base name (e.g. DistrictSync)")
    parser.add_argument(
        "--artifact",
        choices=["auto", "binary", "bundle"],
        default="auto",
        help=(
            "which packed artifact to smoke when a pack produced more than one (macOS): "
            "`binary` = the bare CLI binary, `bundle` = the .app. Default `auto` keeps "
            "the historical candidate order."
        ),
    )
    parser.add_argument(
        "--require-close",
        action="store_true",
        help="gate on the zero-orphan close (Windows); absent => close is INFO-only.",
    )
    parser.add_argument(
        "--cli-smoke",
        action="store_true",
        help="run the packed artifact's CLI branch instead of the windowed GUI smoke.",
    )
    parser.add_argument(
        "--phase",
        choices=[*CLI_SMOKE_PHASES, "all"],
        default="all",
        help="which --cli-smoke phase to run (default: all, in order).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_SMOKE_INPUT,
        help="GDE fixture dir for the --cli-smoke conversions (default: the SD74 snapshot input).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="scratch root for --cli-smoke output dirs (default: a fresh temp dir).",
    )
    parser.add_argument(
        "--allow-real-profile",
        action="store_true",
        help=(
            "run --cli-smoke against the REAL user profile when DISTRICTSYNC_DATA_DIR is unset "
            "(default: refuse). Does not cover the corrupt-profile phase, which plants bytes."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.assert_embed is not None:
        return _assert_embed(args.assert_embed)
    if args.assert_mounted_app is not None:
        mount, app_name = args.assert_mounted_app
        return _assert_mounted_app(Path(mount), app_name)
    if args.dist is None or args.name is None:
        print("FAIL: dist dir and artifact name are required (or use --assert-embed).")
        return 2
    if args.cli_smoke:
        work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix="districtsync-cli-smoke-"))
        return run_cli_smoke(
            args.dist,
            args.name,
            phase=args.phase,
            input_dir=args.input,
            work_dir=work_dir,
            allow_real_profile=args.allow_real_profile,
            kind=args.artifact,
        )
    return run_smoke(args.dist, args.name, args.require_close, args.artifact)


if __name__ == "__main__":
    sys.exit(main())
