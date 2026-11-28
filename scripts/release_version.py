"""DSYNC-113: the ONE derivation of an installer build's version, and the check that the installed app agrees.

``release.yml`` used to spell the version three ways: ``installer-build`` stamped ``0.0.<run>`` into the app,
``installer-windows`` re-derived the same string in PowerShell, and ``installer-msi`` packed the same folder as
``0.<run>.0`` / ``0.<run>.1``, so the MSI rehearsal's app printed ``DistrictSync 0.0.57`` from an MSI whose
ProductVersion was ``0.57.1.0`` (the plan 0055 lab). Every one of those now calls this script.

The version and its mapping (documented in ``docs/developer/release.md`` -> Versioning):

* the package version (semver ``X.Y.Z``): a ``v*`` tag's own (``v3.30.0`` -> ``3.30.0``; anything that is not
  ``vX.Y.Z`` is REFUSED), or on a manual dispatch the throwaway ``0.0.<run number>``. It is stamped into the app
  (``src/_version.py``, what ``app_version()`` returns) and passed to ``vpk pack --packVersion``;
* the MSI ProductVersion: ``X.Y.Z.0`` (what Velopack writes; MSI limits are 255.255.65535);
* what the app prints: ``DistrictSync X.Y.Z`` (``--version``, ``src/main.py``; pinned by a parity test).

The MSI rehearsal packs ONE folder twice. N+1 is the stamped version (so N+1's app and package agree and are
checked); N is one patch below it and its app reports N+1's version, by construction.

Usage (inputs are GitHub's ``GITHUB_REF_TYPE`` / ``GITHUB_REF_NAME`` / ``GITHUB_RUN_NUMBER``)::

    python scripts/release_version.py version                      # print X.Y.Z
    python scripts/release_version.py stamp [--path src/_version.py]
    python scripts/release_version.py msi-versions                 # print "N N+1"
    python scripts/release_version.py check-installed --root <install root> [--msi]

Exit 0 on success; 1 on a refused input or a mismatch, with the reason on stderr.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess  # nosec B404 - runs the installed exe under test, fixed argv, no shell
import sys
import tempfile
import xml.etree.ElementTree as ET  # nosec B405 - parses vpk's own sq.version from the install under test
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parent.parent
_DEFAULT_STAMP: Final = _REPO / "src" / "_version.py"

#: ``vX.Y.Z`` with no leading zeros, nothing else (every release tag to date has this shape).
_TAG: Final = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_SEMVER: Final = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_RUN: Final = re.compile(r"[1-9][0-9]*")
#: Windows Installer's ProductVersion field limits: major.minor.build = 255.255.65535.
_MSI_LIMITS: Final = (255, 255, 65535)
#: What ``--version`` prints before the version (``src/main.py``; pinned by ``tests/test_release_version.py``).
APP_NAME: Final = "DistrictSync"
_VERSION_TIMEOUT_S: Final = 120


class VersionError(ValueError):
    """An input the version cannot be derived from, or a version an MSI cannot carry."""


def package_version(ref_type: str, ref_name: str, run_number: str) -> str:
    """The semver a build stamps into the app and packs: the tag's, or the rehearsal's ``0.0.<run>``."""
    if ref_type == "tag":
        if not _TAG.fullmatch(ref_name):
            raise VersionError(f"tag {ref_name!r} is not vX.Y.Z; a release tag must be (docs/developer/release.md)")
        return ref_name[1:]
    if ref_type == "branch":
        if not _RUN.fullmatch(run_number):
            raise VersionError(f"run number {run_number!r} is not a positive integer")
        return f"0.0.{run_number}"
    raise VersionError(f"ref type {ref_type!r} is neither 'tag' nor 'branch'")


def _parts(semver: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(semver)
    if not match:
        raise VersionError(f"{semver!r} is not X.Y.Z")
    major, minor, patch = (int(g) for g in match.groups())
    return major, minor, patch


def msi_product_version(semver: str) -> str:
    """The MSI ProductVersion Velopack writes for ``semver``: ``X.Y.Z.0``, refused past the MSI limits."""
    parts = _parts(semver)
    if any(value > limit for value, limit in zip(parts, _MSI_LIMITS, strict=True)):
        raise VersionError(f"{semver} is past the MSI ProductVersion limits 255.255.65535")
    return f"{semver}.0"


def app_version_line(semver: str) -> str:
    """What the installed app's ``--version`` prints for ``semver``."""
    return f"{APP_NAME} {semver}"


def msi_rehearsal_versions(stamped: str) -> tuple[str, str]:
    """The MSI rehearsal's (N, N+1): N+1 is the stamped version, N one patch below it; both valid MSI versions."""
    major, minor, patch = _parts(stamped)
    if patch == 0:
        raise VersionError(f"{stamped} has no version one patch below it")
    lower = f"{major}.{minor}.{patch - 1}"
    msi_product_version(lower)
    msi_product_version(stamped)
    return lower, stamped


def mismatches(expected: str, *, app_stdout: str, package: str | None, msi: str | None = None) -> list[str]:
    """Every way the installed copy disagrees with ``expected``; empty means it agrees.

    ``msi`` is the installed MSI's ProductVersion, ``""`` when it could not be read, or ``None`` (the default) when
    there is no MSI to check. An unreadable fact is a mismatch, never a pass.
    """
    found: list[str] = []
    want_line = app_version_line(expected)
    if app_stdout.strip() != want_line:
        found.append(f"the installed app's --version printed {app_stdout.strip()!r}, want {want_line!r}")
    if package != expected:
        found.append(f"the installed package version (current/sq.version) is {package!r}, want {expected!r}")
    if msi is not None:
        want_msi = msi_product_version(expected)
        if msi != want_msi:
            found.append(f"the installed MSI ProductVersion is {msi!r}, want {want_msi!r}")
    return found


def _run_version(exe: Path) -> str:  # pragma: no cover - runs the real installed exe (CI only)
    """``<exe> --version``'s stdout, run on a throwaway profile so the check never writes a real one."""
    with tempfile.TemporaryDirectory(prefix="dsync-version-check-") as profile:
        env = {**os.environ, "DISTRICTSYNC_DATA_DIR": profile}
        proc = subprocess.run(  # nosec B603 - fixed argv on the installed exe under test, no shell
            [str(exe), "--version"], capture_output=True, text=True, timeout=_VERSION_TIMEOUT_S, check=False, env=env
        )
    if proc.returncode != 0:
        return f"<exit {proc.returncode}> {proc.stdout.strip()} {proc.stderr.strip()}".strip()
    return proc.stdout


def _installed_msi_product_version() -> str | None:  # pragma: no cover - Windows registry (CI only)
    """The installed DistrictSync MSI's version, through the app's own reader (the one ``--diagnose`` prints)."""
    sys.path.insert(0, str(_REPO))
    from src.utils.installation import msi_product_version as installed

    return installed()


def _read_package_version(sq_version: Path) -> str | None:
    """``<metadata><version>`` from Velopack's ``current/sq.version`` (a nuspec), or ``None`` when absent."""
    try:
        root = ET.fromstring(sq_version.read_bytes())  # nosec B314 - vpk's own file in the install under test
    except (OSError, ET.ParseError):
        return None
    for metadata in root:
        if metadata.tag.rsplit("}", 1)[-1] == "metadata":
            for child in metadata:
                if child.tag.rsplit("}", 1)[-1] == "version":
                    return (child.text or "").strip() or None
    return None


def check_installed(root: Path, expected: str, *, msi: bool) -> list[str]:
    """Read the installed copy's three facts under ``root`` and return every mismatch with ``expected``."""
    current = root / "current"
    return mismatches(
        expected,
        app_stdout=_run_version(current / f"{APP_NAME}.exe") if current.is_dir() else "",
        package=_read_package_version(current / "sq.version"),
        msi=(_installed_msi_product_version() or "") if msi else None,  # unreadable -> "" -> a mismatch
    )


def _from_environment() -> str:
    names = ("GITHUB_REF_TYPE", "GITHUB_REF_NAME", "GITHUB_RUN_NUMBER")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise VersionError(f"not set: {', '.join(missing)} (this script reads GitHub Actions' variables)")
    return package_version(*(os.environ[name] for name in names))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the package version")
    stamp = sub.add_parser("stamp", help="write src/_version.py")
    stamp.add_argument("--path", type=Path, default=_DEFAULT_STAMP)
    sub.add_parser("msi-versions", help="print the MSI rehearsal's N and N+1")
    check = sub.add_parser("check-installed", help="fail unless the installed app agrees with its package")
    check.add_argument("--root", type=Path, required=True, help="the Velopack install root (holds current/)")
    check.add_argument("--msi", action="store_true", help="also check the installed MSI's ProductVersion")
    args = parser.parse_args(argv)

    try:
        version = _from_environment()
        if args.command == "version":
            print(version)
        elif args.command == "stamp":
            args.path.write_bytes(f"version = '{version}'\n".encode())  # bytes: no CRLF on Windows
            print(args.path.read_text(encoding="utf8"), end="")
        elif args.command == "msi-versions":
            print(" ".join(msi_rehearsal_versions(version)))
        else:
            problems = check_installed(args.root, version, msi=args.msi)
            if problems:
                for problem in problems:
                    print(f"::error::{problem}", file=sys.stderr)
                return 1
            what = f"app {app_version_line(version)!r}, package {version}"
            print(what + (f", MSI {msi_product_version(version)}" if args.msi else "") + ": they agree")
    except VersionError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
