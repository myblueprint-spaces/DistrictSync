"""The shared relaunch helper — DSYNC-114.

Both pre-shell windows (machine setup, auto-grant) re-launch DistrictSync once the shared profile is usable. They
used ``os.execv(argv[0], argv)``, and on Windows ``os.execv`` does NOT quote its arguments: the MSI's
``C:\\Program Files\\...\\DistrictSync.exe`` split at the space, the child's argparse saw
``Files\\myBlueprint.DistrictSync\\...`` as an unrecognised argument and exited 2, and the old window's view was never
closed, so "Working…" stayed on screen.

These tests pin the replacement, :mod:`src.ui_flet.relaunch`: the argv survives the trip through a Windows command
line, the window is closed BEFORE the new process starts, the spawn is a list with no shell, and a frozen parent tells
the PyInstaller bootloader to start a fresh instance. ``subprocess.Popen`` and ``os._exit`` are always replaced here —
no test ever starts the app or ends the test runner.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ui_flet import grant_access, relaunch

# --------------------------------------------------------------------------- #
# CommandLineToArgvW, implemented here so the round trip runs on every OS       #
# --------------------------------------------------------------------------- #


def _command_line_to_argv(cmdline: str) -> list[str]:
    """Split a Windows command line the way ``CommandLineToArgvW`` does.

    The program name (first token) is special: it ends at the next quote if it starts with one, otherwise at the first
    whitespace, and a backslash never escapes anything in it. Every later token follows the 2n / 2n+1 backslash rules,
    with ``""`` inside a quoted run read as one literal quote.
    """
    args: list[str] = []
    i, n = 0, len(cmdline)
    # The program name.
    if i < n and cmdline[i] == '"':
        end = cmdline.find('"', i + 1)
        end = n if end == -1 else end
        args.append(cmdline[i + 1 : end])
        i = end + 1
    else:
        start = i
        while i < n and cmdline[i] not in " \t":
            i += 1
        args.append(cmdline[start:i])
    # The arguments.
    while True:
        while i < n and cmdline[i] in " \t":
            i += 1
        if i >= n:
            return args
        buf: list[str] = []
        in_quotes = False
        while i < n:
            ch = cmdline[i]
            if ch == "\\":
                slashes = 0
                while i < n and cmdline[i] == "\\":
                    slashes += 1
                    i += 1
                if i < n and cmdline[i] == '"':
                    buf.append("\\" * (slashes // 2))
                    if slashes % 2:
                        buf.append('"')
                        i += 1
                else:
                    buf.append("\\" * slashes)
                continue
            if ch == '"':
                if in_quotes and i + 1 < n and cmdline[i + 1] == '"':
                    buf.append('"')
                    i += 2
                    continue
                in_quotes = not in_quotes
                i += 1
                continue
            if ch in " \t" and not in_quotes:
                break
            buf.append(ch)
            i += 1
        args.append("".join(buf))


def _native_command_line_to_argv(cmdline: str) -> list[str]:
    """The real ``shell32.CommandLineToArgvW`` — the cross-check for the splitter above (Windows only)."""
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    count = ctypes.c_int(0)
    argv = shell32.CommandLineToArgvW(cmdline, ctypes.byref(count))
    try:
        return [argv[k] for k in range(count.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(argv, wintypes.HLOCAL))


MSI_EXE = r"C:\Program Files\myBlueprint.DistrictSync\current\DistrictSync.exe"
PROFILE_EXE = r"C:\Users\Jane Q Admin\AppData\Local\DistrictSync\current\DistrictSync.exe"
PLAIN_EXE = r"C:\Apps\DistrictSync.exe"

ROUND_TRIP_CASES = [
    pytest.param([MSI_EXE], id="msi-path-with-spaces"),
    pytest.param([PROFILE_EXE], id="profile-path-with-spaces"),
    pytest.param([PLAIN_EXE], id="path-without-spaces"),
    pytest.param([MSI_EXE, "--input", r"D:\MyEd BC\extracts", "--quality"], id="args-with-spaces"),
    pytest.param([MSI_EXE, "C:\\trailing slash\\", 'a "quoted" word', ""], id="trailing-backslash-quote-empty"),
    pytest.param([r"C:\Program Files\Python313\python.exe", "-m", "src.main"], id="source-checkout"),
]


class TestTheCommandLineRoundTrip:
    @pytest.mark.parametrize("argv", ROUND_TRIP_CASES)
    def test_the_argv_survives_the_windows_command_line(self, argv):
        """What the spawner hands CreateProcess (``list2cmdline``) splits back into the same argv."""
        assert _command_line_to_argv(subprocess.list2cmdline(argv)) == argv

    @pytest.mark.skipif(sys.platform != "win32", reason="the native splitter is Windows-only")
    @pytest.mark.parametrize("argv", ROUND_TRIP_CASES)
    def test_the_splitter_agrees_with_windows_itself(self, argv):
        """Positive twin of the splitter: the test's own parser is the real one, not a convenient one."""
        line = subprocess.list2cmdline(argv)
        assert _command_line_to_argv(line) == _native_command_line_to_argv(line)

    def test_the_unquoted_join_os_execv_produced_does_not_round_trip(self):
        """Negative twin — DSYNC-114 itself: ``os.execv`` on Windows joins with bare spaces, so the exe path splits
        and the child's argparse sees ``Files\\...`` as an argument. The splitter must catch exactly that."""
        argv = [MSI_EXE]
        split = _command_line_to_argv(" ".join(argv))
        assert split != argv
        assert split[1].startswith("Files\\myBlueprint.DistrictSync")

    @pytest.mark.parametrize("exe", [MSI_EXE, PROFILE_EXE])
    def test_the_frozen_relaunch_argv_round_trips(self, monkeypatch, exe):
        """The real argv source (``reexec_argv``) for a frozen exe installed under a path with a space."""
        monkeypatch.setattr(grant_access.sys, "frozen", True, raising=False)
        monkeypatch.setattr(grant_access.sys, "executable", exe)
        monkeypatch.setattr(grant_access.sys, "argv", [exe])
        argv = grant_access.reexec_argv()
        assert _command_line_to_argv(subprocess.list2cmdline(argv)) == [exe]


# --------------------------------------------------------------------------- #
# The spawn: a list, no shell, its own process group, a fresh PyInstaller env   #
# --------------------------------------------------------------------------- #


class _PopenRecorder:
    """Stands in for ``subprocess.Popen``: records the call, starts nothing."""

    def __init__(self, order: list[str] | None = None, *, raises: BaseException | None = None) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []
        self.order = order
        self.raises = raises

    def __call__(self, args: object, **kwargs: object) -> MagicMock:
        self.calls.append((args, kwargs))
        if self.order is not None:
            self.order.append("spawn")
        if self.raises is not None:
            raise self.raises
        return MagicMock()


@pytest.fixture
def popen(monkeypatch):
    recorder = _PopenRecorder()
    monkeypatch.setattr(relaunch.subprocess, "Popen", recorder)
    return recorder


class TestTheSpawn:
    def test_it_spawns_a_list_never_a_string_and_never_through_a_shell(self, popen):
        relaunch.spawn([MSI_EXE, "--quality"])
        ((args, kwargs),) = popen.calls
        assert isinstance(args, list) and args == [MSI_EXE, "--quality"]
        assert kwargs.get("shell", False) is False
        assert kwargs["close_fds"] is True

    def test_the_spawned_process_inherits_no_stdio(self, popen):
        relaunch.spawn([PLAIN_EXE])
        ((_, kwargs),) = popen.calls
        assert kwargs["stdin"] is kwargs["stdout"] is kwargs["stderr"] is subprocess.DEVNULL

    def test_a_tuple_is_handed_over_as_a_list(self, popen):
        relaunch.spawn((PLAIN_EXE, "--diagnose"))
        ((args, _),) = popen.calls
        assert args == [PLAIN_EXE, "--diagnose"] and isinstance(args, list)

    def test_an_empty_argv_is_refused_before_anything_starts(self, popen):
        with pytest.raises(ValueError):
            relaunch.spawn([])
        assert popen.calls == []

    @pytest.mark.skipif(sys.platform != "win32", reason="creation flags are Windows-only")
    def test_on_windows_it_is_detached_in_its_own_process_group(self, popen):
        relaunch.spawn([PLAIN_EXE])
        ((_, kwargs),) = popen.calls
        assert kwargs["creationflags"] == subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    def test_the_flags_are_detached_plus_new_group_on_windows_and_none_elsewhere(self):
        assert relaunch.creation_flags(windows=True) == 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_GROUP
        assert relaunch.creation_flags(windows=False) == 0

    def test_a_frozen_parent_asks_the_bootloader_for_a_fresh_instance(self, popen, monkeypatch):
        monkeypatch.setattr(relaunch.sys, "frozen", True, raising=False)
        monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI12345")
        relaunch.spawn([PLAIN_EXE])
        ((_, kwargs),) = popen.calls
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env[relaunch.RESET_ENVIRONMENT_VAR] == "1"

    def test_a_source_checkout_leaves_the_environment_alone(self, popen, monkeypatch):
        """Positive twin: the PyInstaller switch is only for a frozen parent."""
        monkeypatch.delattr(relaunch.sys, "frozen", raising=False)
        monkeypatch.setenv("DISTRICTSYNC_DATA_DIR", r"C:\Temp\profile")
        relaunch.spawn([PLAIN_EXE])
        ((_, kwargs),) = popen.calls
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert relaunch.RESET_ENVIRONMENT_VAR not in env
        assert env["DISTRICTSYNC_DATA_DIR"] == r"C:\Temp\profile"

    def test_the_child_environment_is_a_copy_not_the_parent_s(self):
        base = {"PATH": "x"}
        env = relaunch.child_environment(base, frozen=True)
        assert env == {"PATH": "x", relaunch.RESET_ENVIRONMENT_VAR: "1"}
        assert base == {"PATH": "x"}
        assert relaunch.child_environment(base, frozen=False) == {"PATH": "x"}


# --------------------------------------------------------------------------- #
# The order: close the window, start the new process, end this one             #
# --------------------------------------------------------------------------- #


@pytest.fixture
def order(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(relaunch.subprocess, "Popen", _PopenRecorder(calls))
    monkeypatch.setattr(relaunch.os, "_exit", lambda code: calls.append(f"exit:{code}"))
    monkeypatch.setattr(relaunch.grant_access, "reexec_argv", lambda: [MSI_EXE])
    return calls


def _page(order: list[str], *, destroy=None) -> MagicMock:  # noqa: ANN001
    page = MagicMock()
    if destroy is None:

        async def destroy() -> None:
            order.append("destroy")

    page.window.destroy = destroy
    return page


class TestTheRelaunch:
    def test_it_closes_the_window_then_spawns_then_exits_zero(self, order):
        asyncio.run(relaunch.relaunch(_page(order)))
        assert order == ["destroy", "spawn", "exit:0"]

    def test_it_awaits_destroy_rather_than_merely_calling_it(self, order):
        page = _page(order, destroy=AsyncMock())
        asyncio.run(relaunch.relaunch(page))
        page.window.destroy.assert_awaited_once()
        assert order == ["spawn", "exit:0"]

    def test_the_spawned_argv_is_the_reexec_argv(self, monkeypatch, order):
        recorder = _PopenRecorder(order)
        monkeypatch.setattr(relaunch.subprocess, "Popen", recorder)
        asyncio.run(relaunch.relaunch(_page(order)))
        ((args, _),) = recorder.calls
        assert args == [MSI_EXE]

    def test_a_window_that_will_not_close_still_relaunches(self, order):
        async def boom() -> None:
            order.append("destroy")
            raise RuntimeError("the view is already gone")

        asyncio.run(relaunch.relaunch(_page(order, destroy=boom)))
        assert order == ["destroy", "spawn", "exit:0"]

    def test_a_close_that_never_answers_is_timed_out_and_still_relaunches(self, order, monkeypatch):
        monkeypatch.setattr(relaunch, "DESTROY_TIMEOUT_SECONDS", 0.01)

        async def hang() -> None:
            order.append("destroy")
            await asyncio.sleep(60)

        asyncio.run(relaunch.relaunch(_page(order, destroy=hang)))
        assert order == ["destroy", "spawn", "exit:0"]

    def test_a_close_cancelled_by_flet_shutting_down_still_relaunches(self, order):
        """The view exiting ends ``ft.run``, which cancels the pending ``destroy`` — the relaunch must survive that."""

        async def cancelled() -> None:
            order.append("destroy")
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(relaunch.relaunch(_page(order, destroy=cancelled)))
        assert order == ["destroy", "spawn", "exit:0"]

    def test_a_spawn_that_fails_exits_one_not_zero(self, order, monkeypatch):
        monkeypatch.setattr(relaunch.subprocess, "Popen", _PopenRecorder(order, raises=FileNotFoundError(2, "gone")))
        asyncio.run(relaunch.relaunch(_page(order)))
        assert order == ["destroy", "spawn", "exit:1"]

    def test_the_seam_schedules_the_relaunch_on_the_page_loop(self):
        page = MagicMock()
        seam = relaunch.relaunch_seam(page)
        page.run_task.assert_not_called()
        seam()
        page.run_task.assert_called_once_with(relaunch.relaunch, page)


# --------------------------------------------------------------------------- #
# No exec left anywhere in src/                                                #
# --------------------------------------------------------------------------- #


def _os_exec_calls(source: str) -> list[str]:
    """Every ``os.exec*`` / ``os.spawn*`` attribute in ``source`` (the detector both tests below share)."""
    import ast

    return [
        f"{node.lineno}:os.{node.attr}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr.startswith(("exec", "spawn"))
    ]


class TestNoUnquotedExec:
    def test_nothing_in_src_calls_an_os_exec_or_spawn_function(self):
        """``os.exec*`` / ``os.spawn*`` do not quote on Windows. Every relaunch goes through the list-based spawn."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src"
        offenders = [
            f"{path.relative_to(root)}:{hit}"
            for path in sorted(root.rglob("*.py"))
            for hit in _os_exec_calls(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []

    def test_the_detector_finds_an_exec_and_a_spawn_in_synthetic_source(self):
        """Positive twin: the walk above really does see ``os.execv`` and ``os.spawnv``."""
        source = "import os\nos.execv(a, b)\nos.spawnv(0, a, b)\n"
        assert _os_exec_calls(source) == ["2:os.execv", "3:os.spawnv"]

    def test_both_windows_wire_the_shared_relaunch(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "ui_flet"
        for name in ("grant_window.py", "machine_setup_window.py"):
            assert "relaunch.relaunch_seam(page)" in (root / name).read_text(encoding="utf-8"), name
