"""Build, check and install the private shared updater package ``myb_desktop_updater`` (plan 0054 S3, DSYNC-62).

The package lives in the private repository ``myblueprint-spaces/myb-desktop-shared``. CI (and a developer who
holds a checkout) installs it like this:

1. A checkout of that repository at exactly ``PINNED_COMMIT`` (the workflow does it with the read-only deploy key).
2. This script re-reads ``git rev-parse HEAD`` and REFUSES anything else, so a moved tag or branch cannot change
   what is built, and refuses a checkout with uncommitted changes.
3. The package folder's TREE HASH must equal ``PINNED_TREE_SHA256``. That is the hash lock. It is the same value
   the shared repository's own CI prints (``tools/tree_hash.py``): a SHA-256 over every file's relative path and
   content with line endings normalised, so it is identical on Windows and Linux. (A wheel's own SHA-256 is NOT
   used: the same source builds three different wheels on a Windows CRLF checkout, a Windows LF checkout and
   Linux, measured 2026-11-17, because file modes and line endings go into the archive.)
4. Only then is the wheel built (``SOURCE_DATE_EPOCH``, pinned ``setuptools`` and ``wheel``) and installed
   (``--no-deps``; its one dependency is pinned in ``requirements-updater.txt``).

Raising the pin: take the new commit and the tree hash the shared repository's own CI prints, change the three
constants below together, read the package's changelog, and let the ticketed change be reviewed. Dependabot
cannot do this for a private source, on purpose.

Usage::

    python scripts/updater_package.py commit
    python scripts/updater_package.py tree-hash <package folder>
    python scripts/updater_package.py install --source <checkout of the shared repository>

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

#: The commit of ``myblueprint-spaces/myb-desktop-shared`` the package is built from (tag ``myb_desktop_updater-v0.5.0``,
#: DSYNC-108: the supervised start-up health for a machine-wide install, ``run_health_probe`` / ``supervised_health`` /
#: ``HealthStore.record_failed_probe``, additive over 0.4.1: the start guard, crash exit, kill-on-close child job and
#: ``HealthStore.record_bad``).
PINNED_COMMIT = "32725226927e985255b08a4aa8ae5f1d21435341"
#: The tree hash of ``packages/myb_desktop_updater`` at that commit, as the shared repo's own CI prints it.
PINNED_TREE_SHA256 = "823dd15b3be2ea50b8ddf6fc8bb94ed00ada240c11e8868d5a40ef3c1b50a8d1"
PINNED_VERSION = "0.5.0"

#: Build settings (same as the shared repository's own CI).
SOURCE_DATE_EPOCH = "1763337600"
BUILD_TOOLS = ("setuptools==84.2.1", "wheel==0.48.0")
PACKAGE_DIR = Path("packages") / "myb_desktop_updater"
WHEEL_NAME = f"myb_desktop_updater-{PINNED_VERSION}-py3-none-any.whl"

# The tree-hash rules, copied from the shared repository's ``tools/tree_hash.py`` so the check does not run code
# from the checkout it is checking.
_TOP_SKIP = {"dist", "build"}
_ANY_SKIP = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_SKIP_SUFFIXES = {".pyc", ".pyo"}


class PinError(Exception):
    """The package is not exactly the pinned one. Nothing is installed."""


def _package_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise PinError(f"{folder} is not a folder")
    found: list[tuple[str, Path]] = []
    for path in folder.rglob("*"):
        rel = path.relative_to(folder)
        parts = rel.parts
        if parts[0] in _TOP_SKIP or _ANY_SKIP & set(parts):
            continue
        if any(part.endswith(".egg-info") for part in parts) or path.suffix in _SKIP_SUFFIXES:
            continue
        if path.is_symlink():
            raise PinError(f"{rel.as_posix()} is a link; a shared package holds plain files only")
        if path.is_file():
            found.append((rel.as_posix(), path))
    return [path for _rel, path in sorted(found, key=lambda item: item[0].encode("utf8"))]


def tree_hash(folder: Path) -> str:
    """SHA-256 over each file's relative posix path and its content with CRLF normalised to LF."""
    files = _package_files(folder)
    if not files:
        raise PinError(f"{folder} holds no files to hash")
    digest = hashlib.sha256()
    for path in files:
        data = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.relative_to(folder).as_posix().encode("utf8") + b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def check_commit(actual: str, expected: str | None = None) -> None:
    expected = PINNED_COMMIT if expected is None else expected
    if actual.strip().lower() != expected:
        raise PinError(f"the shared repository is at {actual.strip()!r}, not the pinned commit {expected}")


def check_tree(folder: Path, expected: str | None = None) -> None:
    expected = PINNED_TREE_SHA256 if expected is None else expected
    actual = tree_hash(folder)
    if actual != expected:
        raise PinError(f"package folder {folder} has tree hash {actual}, not the pinned {expected}; nothing was built")


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, **kwargs)  # type: ignore[call-overload]  # noqa: S603


def build_wheel(source: Path, out_dir: Path) -> Path:
    """Build the wheel from a checkout of the shared repository; refuse any other commit or content."""
    head = _run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True).stdout
    check_commit(head)
    status = _run(["git", "-C", str(source), "status", "--porcelain"], capture_output=True).stdout
    if status.strip():
        raise PinError("the shared repository checkout has uncommitted changes; build from a clean checkout")
    check_tree(source / PACKAGE_DIR)
    _run([sys.executable, "-m", "pip", "install", "--quiet", *BUILD_TOOLS])
    env = {**os.environ, "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH}
    _run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation", "--quiet", "-w", str(out_dir)],
        cwd=source / PACKAGE_DIR,
        env=env,
    )
    wheel = out_dir / WHEEL_NAME
    if not wheel.is_file():
        raise PinError(f"the build did not produce {WHEEL_NAME}")
    return wheel


def install_wheel(wheel: Path) -> None:
    _run([sys.executable, "-m", "pip", "install", "--no-deps", "--quiet", str(wheel)])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("commit", help="print the pinned commit")
    tree = sub.add_parser("tree-hash", help="print the tree hash of a package folder")
    tree.add_argument("folder", type=Path)
    install = sub.add_parser("install", help="check the pinned checkout, build the wheel, install it")
    install.add_argument("--source", type=Path, required=True, help="a checkout of the shared repository")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "commit":
            print(PINNED_COMMIT)
        elif args.cmd == "tree-hash":
            print(tree_hash(args.folder))
        else:
            with tempfile.TemporaryDirectory() as tmp:
                install_wheel(build_wheel(args.source, Path(tmp)))
            print(f"installed {WHEEL_NAME} built from {PINNED_COMMIT[:8]} (commit and tree hash matched the pin)")
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
