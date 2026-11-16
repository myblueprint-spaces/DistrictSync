"""Build, check and install the private shared updater package ``myb_desktop_updater`` (plan 0054 S3, DSYNC-62).

The package lives in the private repository ``myblueprint-spaces/myb-desktop-shared``. CI (and a developer who
holds a checkout) installs it like this:

1. A checkout of that repository at exactly ``PINNED_COMMIT`` (the workflow does it with the read-only deploy key).
2. This script re-reads ``git rev-parse HEAD`` and REFUSES anything else, so a moved tag or branch cannot change
   what is built.
3. The wheel is built with ``SOURCE_DATE_EPOCH`` set and the pinned build tools, which makes the build reproducible
   (the same commit gives the same bytes on a developer machine and on the runner).
4. The wheel's SHA-256 must equal ``PINNED_WHEEL_SHA256``, or nothing is installed. That is the hash lock.
5. Only then is the wheel installed (``--no-deps``; its one dependency is pinned in ``requirements-updater.txt``).

Raising the pin: take the new commit and the wheel hash printed by the shared repository's own CI, change the
three constants below together, read the package's changelog, and let the ticketed change be reviewed. Dependabot
cannot do this for a private source, on purpose.

Usage::

    python scripts/updater_package.py commit
    python scripts/updater_package.py install --source <checkout of the shared repository>
    python scripts/updater_package.py install --wheel <a wheel built elsewhere>   # verifies the hash, installs it
    python scripts/updater_package.py verify-wheel <wheel>

Standard library only. The package source never enters this repository.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

#: The commit of ``myblueprint-spaces/myb-desktop-shared`` the package is built from (0.2.0).
#: Newer commit with the G-097 fix (a package taken in but not applied must not apply at next start) is NOT
#: pinned yet; when RosterGate reports it, these three change together.
PINNED_COMMIT = "d9648d7c9d2f30c9d6f671aef10bf121bf4cd96c"
#: The SHA-256 of the wheel that commit builds with the settings below (as printed by the shared repo's own CI).
PINNED_WHEEL_SHA256 = "89e0631af3aec01f518696b4b51eddadd5d4f84e732be1ba6051edcf9fce5f54"
PINNED_VERSION = "0.2.0"

#: Build settings the pinned hash depends on. Same as the shared repository's own CI.
SOURCE_DATE_EPOCH = "1763337600"
BUILD_TOOLS = ("setuptools==84.2.1", "wheel==0.48.0")
PACKAGE_DIR = Path("packages") / "myb_desktop_updater"
WHEEL_NAME = f"myb_desktop_updater-{PINNED_VERSION}-py3-none-any.whl"


class PinError(Exception):
    """The package is not exactly the pinned one. Nothing is installed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_commit(actual: str, expected: str | None = None) -> None:
    expected = PINNED_COMMIT if expected is None else expected
    if actual.strip().lower() != expected:
        raise PinError(f"the shared repository is at {actual.strip()!r}, not the pinned commit {expected}")


def check_wheel(path: Path, expected: str | None = None) -> None:
    expected = PINNED_WHEEL_SHA256 if expected is None else expected
    if not path.is_file():
        raise PinError(f"no wheel at {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise PinError(f"wheel {path.name} has sha256 {actual}, not the pinned {expected}; it was not installed")


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, **kwargs)  # type: ignore[call-overload]  # noqa: S603


def build_wheel(source: Path, out_dir: Path) -> Path:
    """Build the pinned wheel from a checkout of the shared repository; refuse any other commit."""
    head = _run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True).stdout
    check_commit(head)
    status = _run(["git", "-C", str(source), "status", "--porcelain"], capture_output=True).stdout
    if status.strip():
        raise PinError("the shared repository checkout has uncommitted changes; build from a clean checkout")
    _run([sys.executable, "-m", "pip", "install", "--quiet", *BUILD_TOOLS])
    env = {**os.environ, "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH}
    _run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation", "--quiet", "-w", str(out_dir)],
        cwd=source / PACKAGE_DIR,
        env=env,
    )
    wheel = out_dir / WHEEL_NAME
    check_wheel(wheel)
    return wheel


def install_wheel(wheel: Path) -> None:
    check_wheel(wheel)
    _run([sys.executable, "-m", "pip", "install", "--no-deps", "--quiet", str(wheel)])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("commit", help="print the pinned commit")
    verify = sub.add_parser("verify-wheel", help="check a wheel against the pinned SHA-256")
    verify.add_argument("wheel", type=Path)
    install = sub.add_parser("install", help="build (or take) the pinned wheel, check it, install it")
    group = install.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path, help="a checkout of the shared repository at the pinned commit")
    group.add_argument("--wheel", type=Path, help="a wheel built elsewhere; it is hash-checked before install")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "commit":
            print(PINNED_COMMIT)
        elif args.cmd == "verify-wheel":
            check_wheel(args.wheel)
            print(f"ok: {args.wheel.name} matches the pinned sha256")
        elif args.wheel is not None:
            install_wheel(args.wheel)
            print(f"installed {args.wheel.name} (sha256 matched the pin)")
        else:
            with tempfile.TemporaryDirectory() as tmp:
                wheel = build_wheel(args.source, Path(tmp))
                install_wheel(wheel)
            print(f"installed {WHEEL_NAME} built from {PINNED_COMMIT[:8]} (sha256 matched the pin)")
    except PinError as exc:
        print(f"updater package pin check FAILED: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            f"updater package step failed (exit {exc.returncode}): {' '.join(map(str, exc.cmd[:4]))} ...",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
