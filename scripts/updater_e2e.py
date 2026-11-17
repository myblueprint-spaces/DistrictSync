"""End-to-end proof of the installed build's update, health and rollback, on a REAL Velopack install (plan 0054 S5, DSYNC-64).

Run by hand on a Windows machine, NEVER in a release and never against the real feed. What it does, in order:

1. Builds three one-folder copies of the app (3.99.1, 3.99.2, 3.99.3) in the ``e2e`` flavour: the installed flavour plus a
   loopback feed and a TEST signing key stamped into the build (``src/_e2e_target.py``, git-ignored, removed afterwards).
   3.99.3 is the "bad build": its bundled ``logging.conf`` is corrupted, so it dies in its own start-up AFTER the start has
   been recorded.
2. Packs them unsigned with ``vpk`` under the real pack id ``myBlueprint.DistrictSync`` and installs 3.99.1 for the current
   user (``Setup.exe --silent``), with ``DISTRICTSYNC_DATA_DIR`` pointing at a throwaway profile so the real profile is never
   touched.
3. Serves a loopback feed signed with the test key, and runs the installed exe the way the nightly does (``--source
   scheduled``, with an input folder that does not exist so every run FAILS, which proves a failing nightly still updates
   and that "healthy" is start-up completing, not conversion success):
   a. a TAMPERED manifest is refused and the version stays 3.99.1;
   b. a good manifest for 3.99.2 is staged, verified, kept-safe and handed off; after the process exits 3.99.2 is running;
   c. 3.99.2 starts healthy; a manifest then offers the bad 3.99.3, which installs; its next four starts die; the fourth
      restores 3.99.2 from the kept copy, records 3.99.3 bad, and it is never offered again.
4. Uninstalls, removes the throwaway profile and the build leftovers, and checks the machine is as it was.

Prerequisites (see ``docs/developer/updates.md``): a Python with the project requirements, ``flet``/``flet-cli`` and
``pyinstaller`` and the shared ``myb_desktop_updater`` wheel installed; ``vpk`` 1.2.161; and the Flet client cached
(``flet_desktop.ensure_client_cached()``). No other DistrictSync process may be running (the package refuses to apply under
a running copy, and this script refuses to start if one is).

Usage::

    python scripts/updater_e2e.py --vpk <vpk.exe> [--python <venv python>] [--work <folder>] [--keep]

Exit code 0 only if every assertion held. Prints a transcript.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flet_pack_args  # noqa: E402  (scripts/ is not a package)

PACK_ID = "myBlueprint.DistrictSync"
VERSIONS = ("3.99.1", "3.99.2", "3.99.3")
# RFC 6979 appendix A.2.5 test key: public, published, worthless. It signs only this script's feeds.
TEST_PRIVATE = bytes.fromhex("C9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721")
STAMPED = ("src/_version.py", "src/_build_flavor.py", "src/_e2e_target.py")


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


class Check:
    failures: list[str] = []

    @classmethod
    def that(cls, condition: bool, what: str) -> None:
        say(("PASS  " if condition else "FAIL  ") + what)
        if not condition:
            cls.failures.append(what)


# ---------------------------------------------------------------------------------------------- feed server


class Feed:
    """A loopback http feed whose manifest and files can be swapped between scenarios."""

    def __init__(self, port: int) -> None:
        self.manifest = b""
        self.files: dict[str, bytes] = {}
        self.hits: list[str] = []
        feed = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                feed.hits.append(self.path)
                body = feed.manifest if self.path == "/manifest.json" else feed.files.get(self.path.lstrip("/"))
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                if body is not None:
                    self.wfile.write(body)

            def log_message(self, *_a: object) -> None:
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------------------------- build + pack


def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    kwargs.setdefault("check", True)
    return subprocess.run(cmd, text=True, **kwargs)  # type: ignore[call-overload]  # noqa: S603


def stamp(version: str, port: int, public_hex: str) -> None:
    (REPO / "src/_version.py").write_text(f"version = '{version}'\n", encoding="utf8")
    (REPO / "src/_build_flavor.py").write_text("flavor = 'e2e'\n", encoding="utf8")
    (REPO / "src/_e2e_target.py").write_text(
        f"HOST = '127.0.0.1:{port}'\nKEYS = {{'e2e-test-key': '{public_hex}'}}\n", encoding="utf8"
    )


def unstamp() -> None:
    for name in STAMPED:
        (REPO / name).unlink(missing_ok=True)


def build(python: str, version: str, dist: Path, *, break_start: bool) -> Path:
    say(f"building {version} (e2e flavour){' - BAD BUILD: corrupt logging.conf' if break_start else ''}")
    flet = str(Path(python).parent / "flet.exe")
    flags = flet_pack_args.as_flags("windows", installed=True).split()
    for module in (*flet_pack_args.E2E_EXTRA, "myb_desktop_updater", "myb_desktop_updater.velopack_installer"):
        flags += ["--hidden-import", module]
    env = {**os.environ, "FLET_DESKTOP_FLAVOR": "light"}
    cmd = [
        flet, "pack", "src/main.py", "--name", "DistrictSync", "--yes", "-D", "--distpath", str(dist),
        "--icon", "assets/districtsync.ico", "--add-data", "config;config", "--add-data", "assets;assets",
        *flags,
        "--pyinstaller-build-args=--paths", "--pyinstaller-build-args=.",
        "--pyinstaller-build-args=--exclude-module", "--pyinstaller-build-args=streamlit",
        "--pyinstaller-build-args=--exclude-module", "--pyinstaller-build-args=src.ui",
    ]  # fmt: skip
    run(cmd, cwd=REPO, env=env)
    folder = dist / "DistrictSync"
    if break_start:
        (folder / "_internal" / "config" / "logging.conf").write_text(
            "[[[ this is not a logging config\n", encoding="utf8"
        )
    return folder


def pack(vpk: str, version: str, folder: Path, out: Path) -> None:
    say(f"packing {version} with vpk (unsigned)")
    run([vpk, "pack", "--packId", PACK_ID, "--packVersion", version, "--packDir", str(folder), "--mainExe", "DistrictSync.exe",
         "--packTitle", "DistrictSync", "--runtime", "win-x64", "--outputDir", str(out), "--delta", "None", "--skip-updates"])  # fmt: skip


def manifest_for(version: str, out: Path, host: str, *, tamper: bool = False) -> tuple[bytes, dict[str, bytes]]:
    """The signed manifest offering one release, and the files it lists (the full package + a one-entry listing)."""
    from myb_desktop_updater import seal_document

    full = out / f"{PACK_ID}-{version}-full.nupkg"
    listing = json.loads((out / "releases.win.json").read_text(encoding="utf8"))
    kept = [
        a for a in listing["Assets"] if a["Version"] == version and a["Type"] == "Full"
    ]  # only what exists on the feed
    files = {full.name: full.read_bytes(), "releases.win.json": json.dumps({"Assets": kept}).encode("utf8")}
    document = {
        "schema": 1, "app": "DistrictSync", "channel": "stable", "sequence": 10 + VERSIONS.index(version),
        "expires": (datetime.now(UTC) + timedelta(days=30)).isoformat(), "feed_host": host,
        "releases": [{"version": version, "files": [
            {"name": n, "sha256": hashlib.sha256(d).hexdigest(), "size": len(d), "url": f"http://{host}/{n}"}
            for n, d in files.items()]}],
    }  # fmt: skip
    raw = seal_document(document, TEST_PRIVATE, "e2e-test-key")
    if tamper:
        envelope = json.loads(raw)
        envelope["signature"] = envelope["signature"][:-4] + "AAAA"
        raw = json.dumps(envelope).encode("utf8")
    return raw, files


# ---------------------------------------------------------------------------------------------- the installed app


class Installed:
    def __init__(self, root: Path, profile: Path, work: Path) -> None:
        self.root, self.profile, self.work = root, profile, work
        self.exe = root / "current" / "DistrictSync.exe"
        self.env = {**os.environ, "DISTRICTSYNC_DATA_DIR": str(profile)}

    def version(self) -> str | None:
        """The installed version as Velopack records it (works even when the app itself cannot start)."""
        try:
            text = (self.root / "current" / "sq.version").read_text(encoding="utf8")
        except OSError:
            return None
        found = re.search(r"<version>([^<]+)</version>", text)
        return found.group(1) if found else None

    def nightly(self, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        """One scheduled run, with an input folder that does not exist: the run fails, as a bad night does.

        A window-less (GUI-subsystem) exe that dies on an unhandled exception shows a modal error box and WAITS for it
        to be dismissed, so a crashed start can hang. Task Scheduler would end such a task at its time limit; this
        does the same: past ``timeout`` the process is killed and the start counts as died (exit code -1).
        """
        try:
            return subprocess.run(  # noqa: S603
                [str(self.exe), "--sis", "myedbc", "--input", str(self.work / "no-such-input"), "--output", str(self.work / "out"),
                 "--source", "scheduled"],
                env=self.env, capture_output=True, text=True, timeout=timeout, check=False, cwd=self.work,
            )  # fmt: skip
        except subprocess.TimeoutExpired as exc:
            say(f"the run did not end within {timeout}s (a crashed windowed exe waits on its error box); killed it")
            return subprocess.CompletedProcess(exc.cmd, -1, "", "")

    def updater_file(self, name: str) -> dict[str, object]:
        try:
            return dict(json.loads((self.profile / "updater" / name).read_text(encoding="utf8")))
        except (OSError, ValueError):
            return {}

    def forget_last_check(self) -> None:
        (self.profile / "updater" / "last-check.json").unlink(missing_ok=True)  # the 6-hour interval is not under test

    def wait_for_version(self, wanted: str, seconds: int = 120, settle: int = 25) -> bool:
        """Wait for Velopack to finish applying ``wanted``, then let its post-update hooks and clean-up settle.

        The settle matters: a hand-off asked for while the previous apply's ``Update.exe`` is still working fails with
        ``apply_failed`` (seen on a real run). In production that is just a staged update retried at the next check.
        """
        end = time.time() + seconds
        while time.time() < end:
            if self.version() == wanted:
                time.sleep(settle)
                return True
            time.sleep(2)
        return False


def other_districtsync_running() -> bool:
    ran = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq DistrictSync.exe", "/NH"], capture_output=True, text=True, check=False
    )  # noqa: S603, S607
    return "DistrictSync.exe" in ran.stdout


# ---------------------------------------------------------------------------------------------- the scenarios


def scenarios(vpk: str, python: str, work: Path, reuse: bool = False) -> None:
    from myb_desktop_updater import p256

    local = Path(os.environ["LOCALAPPDATA"])
    root = local / PACK_ID
    Check.that(not root.exists(), f"nothing is installed at {root} before the proof starts")
    Check.that(not other_districtsync_running(), "no other DistrictSync process is running")
    if Check.failures:
        return
    port_file = work / "port.txt"  # the feed's port is stamped into every build, so a reused pack needs the same one
    port = int(port_file.read_text()) if reuse and port_file.exists() else free_port()
    port_file.write_text(str(port))
    host = f"127.0.0.1:{port}"
    public_hex = p256.public_key(TEST_PRIVATE).hex()
    packs: dict[str, Path] = {}
    try:
        for version in VERSIONS:
            out = work / f"out-{version}"
            if reuse and (out / "releases.win.json").exists():
                say(f"reusing the packed {version} from {out}")
                packs[version] = out
                continue
            stamp(version, port, public_hex)
            try:
                folder = build(python, version, work / f"dist-{version}", break_start=(version == "3.99.3"))
            finally:
                unstamp()
            pack(vpk, version, folder, out)
            packs[version] = out
        feed = Feed(port)
        profile = work / "profile"
        shutil.rmtree(profile, ignore_errors=True)
        profile.mkdir()
        say("installing 3.99.1 for the current user (Setup.exe --silent)")
        run([str(packs["3.99.1"] / f"{PACK_ID}-win-Setup.exe"), "--silent"])
        app = Installed(root, profile, work)
        Check.that(
            app.wait_for_version("3.99.1", 60), "3.99.1 is installed under %LOCALAPPDATA%\\myBlueprint.DistrictSync"
        )

        say("--- a. a tampered manifest is refused")
        feed.manifest, feed.files = manifest_for("3.99.2", packs["3.99.2"], host, tamper=True)
        result = app.nightly()
        Check.that(result.returncode == 1, "the failing nightly exits 1, exactly as it would without any update")
        last = app.updater_file("last-check.json")
        Check.that(
            last.get("status") == "refused" and last.get("reason") == "bad_signature",
            f"the check recorded bad_signature ({last})",
        )
        time.sleep(12)
        Check.that(app.version() == "3.99.1", "the running version is still 3.99.1")
        Check.that(not (profile / "updater" / "staging").exists(), "nothing was staged")

        say("--- b. a good manifest: staged, verified, kept safe, handed off, applied after exit")
        app.forget_last_check()
        feed.manifest, feed.files = manifest_for("3.99.2", packs["3.99.2"], host)
        result = app.nightly()
        Check.that(
            result.returncode == 1, "the failing nightly still exits 1 (the update never changes a run's result)"
        )
        last = app.updater_file("last-check.json")
        Check.that(
            last.get("status") == "handed_off" and last.get("version") == "3.99.2",
            f"the check handed 3.99.2 to the installer ({last})",
        )
        Check.that(app.wait_for_version("3.99.2"), "after the nightly exited, 3.99.2 is the installed version")
        kept = sorted(p.name for p in (profile / "updater").rglob("*.nupkg") if "3.99.1" in p.name)
        Check.that(bool(kept), f"the running 3.99.1 package was kept before applying ({kept})")

        say("--- c. a bad build: installs, dies at start, is rolled back, never offered again")
        app.nightly()  # 3.99.2 starts (recorded) and reaches the healthy mark before ETL, though the run itself fails
        health = app.updater_file("health.json")
        Check.that(
            health.get("healthy") == "3.99.2",
            f"3.99.2 is recorded healthy although its run failed ({health.get('healthy')})",
        )
        app.forget_last_check()
        feed.manifest, feed.files = manifest_for("3.99.3", packs["3.99.3"], host)
        app.nightly()
        Check.that(app.wait_for_version("3.99.3"), "the bad 3.99.3 was taken (it is a signed, verified, newer version)")
        for number in (1, 2, 3):
            result = app.nightly()
            Check.that(
                result.returncode != 0, f"start {number} of 3.99.3 dies in its own start-up (exit {result.returncode})"
            )
            Check.that(app.version() == "3.99.3", f"after failed start {number} it is still 3.99.3 (tolerated)")
        result = app.nightly()
        Check.that(
            app.wait_for_version("3.99.2"), "the fourth start asked for the kept copy; after it exited 3.99.2 is back"
        )
        health = app.updater_file("health.json")
        Check.that("3.99.3" in [str(v) for v in health.get("bad", [])], f"3.99.3 is recorded bad ({health.get('bad')})")
        app.forget_last_check()
        feed.hits.clear()
        app.nightly()
        last = app.updater_file("last-check.json")
        Check.that(
            last.get("status") == "none",
            f"the feed still offers 3.99.3 but it is not offered to this app again ({last})",
        )
        Check.that(app.version() == "3.99.2", "and 3.99.2 stays")

        say("--- d. G-097: a package that is only WAITING in the install folder is not applied by a restart")
        waiting = root / "packages" / f"{PACK_ID}-3.99.3-full.nupkg"
        if not waiting.exists():
            shutil.copyfile(packs["3.99.3"] / waiting.name, waiting)
        Check.that(waiting.exists(), "a newer full package (3.99.3) is waiting in the install's packages folder")
        app.forget_last_check()
        app.nightly()
        app.nightly()
        time.sleep(30)
        Check.that(app.version() == "3.99.2", "after two restarts the waiting package was NOT applied: still 3.99.2")
        Check.that(waiting.exists(), "and the waiting package is still there, untouched")
        say(
            "premise twin: the waiting package really is applicable (Update.exe apply, as an auto-apply-on start would do)"
        )
        run([str(root / "Update.exe"), "apply", "--norestart"], check=False, timeout=120)
        Check.that(
            app.wait_for_version("3.99.3", 90, settle=5),
            "asked to apply it explicitly, 3.99.3 becomes the installed version",
        )
        feed.close()
    finally:
        pass


def clean_up(work: Path, keep: bool) -> None:
    say("cleaning up")
    root = Path(os.environ["LOCALAPPDATA"]) / PACK_ID
    update = root / "Update.exe"
    for _attempt in range(2):
        if update.exists():
            time.sleep(25)  # let any apply still in flight finish before uninstalling
            with contextlib.suppress(Exception):
                run([str(update), "uninstall", "--silent"], check=False, timeout=120)
            time.sleep(8)
    unstamp()
    for leftover in ("build", "DistrictSync.spec"):
        target = REPO / leftover
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink(missing_ok=True)
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    Check.that(not root.exists(), f"{root} is gone (the test install was removed)")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vpk", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--reuse", action="store_true", help="reuse packs already built in --work")
    parser.add_argument("--keep", action="store_true", help="keep the work folder (builds, packs, profile)")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("this proof needs Windows", file=sys.stderr)
        return 2
    work = args.work or Path(tempfile.mkdtemp(prefix="dsync-e2e-"))
    work.mkdir(parents=True, exist_ok=True)
    say(f"work folder: {work}")
    try:
        scenarios(args.vpk, args.python, work, args.reuse)
    except subprocess.CalledProcessError as exc:
        Check.that(False, f"a step failed: {' '.join(map(str, exc.cmd[:3]))} ... (exit {exc.returncode})")
    finally:
        clean_up(work, args.keep)
    say(
        "RESULT: "
        + ("ALL ASSERTIONS HELD" if not Check.failures else f"{len(Check.failures)} FAILED: {Check.failures}")
    )
    return 0 if not Check.failures else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
