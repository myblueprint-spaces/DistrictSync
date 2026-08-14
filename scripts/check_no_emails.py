#!/usr/bin/env python3
"""Repo-hygiene gate: no plaintext email address may be committed to this PUBLIC repo.

DistrictSync is open source and handles student PII, and plan 0038 adds a feature whose
whole subject matter is email addresses. The realistic failure is mundane: someone pastes
a district contact into a config comment, a doc, a plan file, or a Python constant while
wiring identity up, and it is public forever. This gate makes that a red build instead of
a disclosure.

Scope: EVERY git-tracked file. Not an opt-in path list — a contact hardcoded as a Python
constant, or dropped into `CLAUDE.md`, is precisely the mistake this exists to catch, and
a path list is exactly how such a file gets missed.

=== The allowlist model (why literals, not patterns) ===

A future legitimate address must be a VISIBLE ALLOWLIST LINE, never an invisible path
hole. So the allowance tiers are, in order of how much they permit:

1. ``PUBLISHED_ADDRESSES`` — exact literals. An organisation's own published contact.
   Allowing an exact string can never permit any OTHER address, which is what makes this
   tier safe to keep short and to review at a glance.
2. ``ILLUSTRATIVE_EXAMPLES`` — exact literals again, kept in a SEPARATE tier purely so a
   reviewer can tell "a real published address" apart from "a made-up example in a
   comment or doc" without reading each line's rationale.
3. ``RESERVED_DOMAINS`` — RFC 2606 / RFC 6761 names (``example.com``, ``.invalid``,
   ``.test``, …). IANA reserves these so nobody can register them, so no real person can
   ever hold such an address. Provably safe to allow anywhere.
4. ``SYNTHETIC_DATA_PATHS`` — the ONE path-scoped allowance: ``tests/``. See the declared
   gap below.

**DECLARED GAP — read this before trusting the gate.** ``tests/`` is exempt wholesale.
That tree is synthetic-by-contract (CLAUDE.md's top privacy rule: no real data in the
repo) and its fixtures deliberately use district-shaped addresses at REAL district
domains — ``…@sd74.bc.ca`` in the frozen SD74 golden, for one — which are mechanically
indistinguishable from a real staff address. Those goldens are also byte-frozen, so a
per-line pragma would corrupt the very files a contract test compares byte-for-byte. So:
**a real address pasted under ``tests/`` is NOT caught here.** The defence there is the
no-real-data rule plus review. Everything else — ``src/``, ``config/``, ``docs/``,
``scripts/``, ``.github/``, ``.claude/``, and every repo-root file — is fully scanned,
which covers the places plan 0038 actually touches.

Second declared gap: this matches EMAIL-SHAPED text. An address written to evade a
scanner (``name AT district DOT ca``) is not caught, and is not the failure mode this
guards — the realistic mistake is a copy-paste, not an evasion.

Third declared gap — **the hook reads the WORKTREE, not the index.** :func:`scan` lists
index paths but reads each file from disk, so the two diverge in one direction that
matters: a leak that was ``git add``ed and then EDITED OUT of the working copy is **staged
for commit yet invisible here**, so the hook passes and the leak lands in the commit. (The
opposite direction is harmless: an unstaged leak in the working copy fails the hook early,
which is a false alarm at worst.) Reading index blobs would close it; that is a tracked
ROADMAP item, and CI — which runs against a clean checkout where worktree == index —
catches this case today. Say "the hook reduces the chance of a leak"; do not say it
prevents one.

Findings are printed REDACTED. A public CI log echoing a leaked address would be a second
publication of it; the file:line is what the author needs anyway.

Usage:
    python scripts/check_no_emails.py          # exit 1 on any unallowed address
    python scripts/check_no_emails.py --list   # print every hit, allowed ones included
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Tier 1 — real, published organisational addresses. Exact literals.           #
# --------------------------------------------------------------------------- #
PUBLISHED_ADDRESSES: dict[str, str] = {
    # The FORMER support address. No longer surfaced by the product (owner decision
    # 2026-08-13 re-pointed SUPPORT_EMAIL to the SpacesEDU address below); still
    # allowlisted because it remains a legitimate published myBlueprint address and
    # is quoted in the plan/decision log and in this scanner's own tests.
    "support@myblueprint.ca": "myBlueprint published support address (former product contact)",
    # The product's published support + partner-enquiry address — src/ui_flet/screens/help.py's
    # SUPPORT_EMAIL (and via it Home's not-listed card), src/main.py's failure hint,
    # docs/index.md, README.md, and the partner docs.
    "hello@spacesedu.com": "SpacesEDU published support / partner-contact address",
    # pyproject.toml's `authors` package metadata — the maintainer's own work address,
    # published deliberately as part of packaging metadata.
    "shan.peiris@myblueprint.ca": "pyproject.toml package-author metadata (maintainer's own)",
    # The Co-Authored-By trailer used on agent-assisted commits; appears in files only if
    # a doc ever quotes the trailer.
    "noreply@anthropic.com": "git co-author trailer (a no-reply sink, not a person)",
}

# --------------------------------------------------------------------------- #
# Tier 2 — made-up examples in comments and docs. Exact literals, separate     #
# tier so "published address" vs "illustration" is legible at a glance.        #
# --------------------------------------------------------------------------- #
ILLUSTRATIVE_EXAMPLES: dict[str, str] = {
    # Student-email TEMPLATES: the local part is a metavariable, not a person.
    "firstname+lastname+admission-year@learn60.ca": "SD60 student-email template (CHANGELOG, partner docs)",
    "firstlast+2-digit-admission-year@learn60.ca": "SD60 student-email template (config comment)",
    "firstlast+admission-yy@learn60.ca": "SD60 student-email template (architecture tree)",
    # Made-up people illustrating SD54's surname.firstname convention, in a config comment.
    "doe.john@sd54.bc.ca": "SD54 email-convention example (John Doe)",
    "samplesurname.placeholder@sd54.bc.ca": "SD54 double-barrelled-surname example (invented names)",
    # Placeholders in the plan / decision log.
    "first.last@sdnn.bc.ca": "placeholder address in the decision log",
    "x@sdnn.bc.ca": "placeholder address in a plan-review note",
    # An SFTP connection string in a partner doc, not an email address.
    "district_x@sftp.ca.spacesedu.com": "sample SFTP user@host line in the partner docs",
    # The canned examples inside the identity primitives and their validator's messages.
    "name@yourdistrict.bc.ca": "canned example in validators.validate_identity_email's messages",
    "name+roster@district.ca": "plus-addressing example in src/utils/identity.py's docstring",
}

# --------------------------------------------------------------------------- #
# Tier 3 — IANA-reserved names. Nobody can register these, so no real person   #
# can hold such an address (RFC 2606 s2-3, RFC 6761).                          #
# --------------------------------------------------------------------------- #
RESERVED_DOMAINS: frozenset[str] = frozenset({"example.com", "example.net", "example.org"})
RESERVED_TLDS: tuple[str, ...] = (".example", ".invalid", ".test", ".localhost")

# --------------------------------------------------------------------------- #
# Tier 4 — the ONE path-scoped allowance. See the DECLARED GAP above.          #
# --------------------------------------------------------------------------- #
SYNTHETIC_DATA_PATHS: tuple[str, ...] = ("tests/",)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}")


def is_allowed(address: str, rel_path: str) -> str | None:
    """Return the ALLOWANCE REASON for ``address`` at ``rel_path``, or ``None`` if it is a finding."""
    lowered = address.lower()
    if reason := PUBLISHED_ADDRESSES.get(lowered):
        return f"published: {reason}"
    if reason := ILLUSTRATIVE_EXAMPLES.get(lowered):
        return f"example: {reason}"
    domain = lowered.rpartition("@")[2]
    if domain in RESERVED_DOMAINS or domain.endswith(RESERVED_TLDS):
        return "reserved: an IANA-reserved name nobody can register"
    if rel_path.startswith(SYNTHETIC_DATA_PATHS):
        return "synthetic: under tests/ (DECLARED GAP — see this script's docstring)"
    return None


def redact(address: str) -> str:
    """``a.person@example.com`` -> ``a***@e***.com`` — enough to recognise, not to reuse."""
    local, at, domain = address.rpartition("@")
    if not at:
        return "***"
    head, _, tail = domain.partition(".")
    return f"{local[:1]}***@{head[:1]}***{'.' + tail if tail else ''}"


def tracked_files(root: Path) -> list[str]:
    """Every path in the git index — includes files freshly ``git add``ed, so the hook sees them."""
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [path for path in out.split("\0") if path]


def scan(root: Path) -> tuple[list[tuple[str, int, str]], list[tuple[str, int, str, str]], int]:
    """Return ``(findings, allowed, unreadable_count)``.

    Reads the WORKTREE copy of each tracked path. See the third declared gap in this
    module's docstring for the one direction where that diverges from the index and what
    covers it.

    **Undecodable bytes are DECODED WITH REPLACEMENT, never skipped.** An email address is
    ASCII, so it is still matchable inside a file that merely fails strict UTF-8 — a mixed
    encoding, a stray byte, a text file with an embedded binary blob. Skipping such a file
    would create exactly the kind of silent hole this gate exists to avoid: "no findings"
    for a reason unrelated to the content. Only a file that cannot be OPENED at all
    (deleted-but-still-indexed, permission denied) counts as unreadable, and that count is
    reported so an unexpectedly large one is visible rather than inferred.
    """
    findings: list[tuple[str, int, str]] = []
    allowed: list[tuple[str, int, str, str]] = []
    unreadable = 0
    for rel in tracked_files(root):
        try:
            text = (root / rel).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            unreadable += 1  # deleted-but-still-indexed, or unopenable
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in EMAIL_RE.finditer(line):
                address = match.group(0)
                reason = is_allowed(address, rel)
                if reason is None:
                    findings.append((rel, lineno, address))
                else:
                    allowed.append((rel, lineno, address, reason))
    return findings, allowed, unreadable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="also print every ALLOWED address and its reason")
    parser.add_argument("--root", default=None, help="repo root (default: the git root of the cwd)")
    args = parser.parse_args(argv)

    root = (
        Path(args.root).resolve()
        if args.root
        else Path(
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
            ).stdout.strip()
        )
    )

    findings, allowed, unreadable = scan(root)

    if args.list:
        print(f"Allowed ({len(allowed)} occurrences, {unreadable} unreadable/binary files skipped):")
        for rel, lineno, address, reason in allowed:
            print(f"  {rel}:{lineno}: {redact(address)}  [{reason}]")
        print()

    if not findings:
        print(f"OK: no unallowed email addresses in {len(tracked_files(root))} tracked files.")
        return 0

    print(f"FAIL: {len(findings)} unallowed email address(es) in tracked files.\n", file=sys.stderr)
    for rel, lineno, address in findings:
        print(f"  {rel}:{lineno}: {redact(address)}", file=sys.stderr)
    print(
        "\nThis repository is PUBLIC. Either remove the address, or — if it is an "
        "organisation's own published contact — add it as an explicit line in "
        "scripts/check_no_emails.py with a reason. Never widen a path allowance to hide one.\n"
        "(Addresses above are redacted on purpose: a public CI log must not republish a leak.)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
