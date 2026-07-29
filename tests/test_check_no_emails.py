"""`scripts/check_no_emails.py` — the repo-hygiene gate that keeps this PUBLIC repo clean.

A scanner nobody has watched FAIL is worth nothing, so the load-bearing tests here are the
POSITIVE ones: a planted address in `src/`, `config/`, `docs/partner/` and at the repo
root is CAUGHT. The "allowlisted survives" tests are the necessary counterweight (a gate
that fails on everything gets disabled within a week), never the point.

The live repo is asserted clean here too, so the gate cannot rot into a script that is
wired up but never actually run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_no_emails import (
    EMAIL_RE,
    ILLUSTRATIVE_EXAMPLES,
    PUBLISHED_ADDRESSES,
    is_allowed,
    main,
    redact,
    scan,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# A planted address that is NOT allowlisted by any tier: a real-looking district contact.
PLANTED = "j.smith@somedistrict.bc.ca"


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A real (tiny) git repo, so the scanner's ``git ls-files`` walk is exercised for real."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


def _add(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", rel], cwd=repo, check=True)


# --------------------------------------------------------------------------- #
# The POSITIVE side: a planted address is caught                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "rel",
    [
        "src/some_module.py",
        "src/ui_flet/screens/thing.py",
        "config/mappings/sd99_mapping.yaml",
        "docs/partner/installation.md",
        "docs/claugentic-DECISIONS.md",
        "CLAUDE.md",
        "README.md",
        ".github/workflows/ci.yml",
        "scripts/helper.py",
        ".claude/plans/0099-thing.md",
        "Makefile",
    ],
)
def test_a_planted_address_is_caught_anywhere_outside_tests(fake_repo: Path, rel: str) -> None:
    """No path hole: every directory a contact could plausibly be pasted into is scanned.

    The parametrisation IS the assertion — an opt-in path list would silently pass most of
    these rows, which is exactly the failure mode this scope was chosen to avoid.
    """
    _add(fake_repo, rel, f"contact = '{PLANTED}'\n")

    findings, _allowed, _unreadable = scan(fake_repo)

    assert [(f[0], f[2]) for f in findings] == [(rel, PLANTED)]


def test_the_finding_names_the_exact_line(fake_repo: Path) -> None:
    _add(fake_repo, "src/thing.py", f"# line 1\n# line 2\nOWNER = '{PLANTED}'\n")

    findings, _allowed, _unreadable = scan(fake_repo)

    assert findings[0][:2] == ("src/thing.py", 3)


def test_a_freshly_git_added_file_is_scanned(fake_repo: Path) -> None:
    """The pre-commit hook depends on this: a NEW file, added but never committed, counts."""
    _add(fake_repo, "src/brand_new.py", f"X = '{PLANTED}'\n")
    assert scan(fake_repo)[0], "a staged new file must be scanned, or the hook is decorative"


@pytest.mark.parametrize(
    "text",
    [
        "Firstname.Lastname@SD48.BC.CA",  # uppercase
        "first.last+roster@district.ab.ca",  # plus-addressing
        "a@b.co",  # minimal
        "admin_1-x%y@sub.district-name.bc.ca",  # full charset
    ],
)
def test_address_shapes_the_regex_must_not_miss(fake_repo: Path, text: str) -> None:
    _add(fake_repo, "docs/note.md", f"Write to {text} for help.\n")
    assert scan(fake_repo)[0], f"{text!r} slipped past the scanner"


def test_exit_code_is_1_and_stderr_names_the_file(fake_repo: Path, capsys) -> None:
    _add(fake_repo, "src/thing.py", f"X = '{PLANTED}'\n")

    assert main(["--root", str(fake_repo)]) == 1

    err = capsys.readouterr().err
    assert "src/thing.py" in err
    assert "PUBLIC" in err


def test_the_failure_output_does_not_republish_the_address(fake_repo: Path, capsys) -> None:
    """A public CI log must not echo a leaked address — the file:line is what's actionable."""
    _add(fake_repo, "src/thing.py", f"X = '{PLANTED}'\n")

    main(["--root", str(fake_repo)])
    captured = capsys.readouterr()

    assert PLANTED not in captured.err + captured.out
    assert "somedistrict" not in captured.err + captured.out
    assert "j.smith" not in captured.err + captured.out


# --------------------------------------------------------------------------- #
# The counterweight: allowlisted things survive                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("address", sorted(PUBLISHED_ADDRESSES) + sorted(ILLUSTRATIVE_EXAMPLES))
def test_every_allowlisted_literal_survives_anywhere(fake_repo: Path, address: str) -> None:
    _add(fake_repo, "src/thing.py", f"X = '{address}'\n")
    assert scan(fake_repo)[0] == []


def test_an_allowlisted_literal_matches_case_insensitively(fake_repo: Path) -> None:
    _add(fake_repo, "src/thing.py", "X = 'Support@myBlueprint.CA'\n")
    assert scan(fake_repo)[0] == []


@pytest.mark.parametrize(
    "address",
    [
        "anyone@example.com",
        "anyone@example.org",
        "anyone@example.net",
        "anyone@thing.invalid",
        "anyone@thing.test",
        "anyone@thing.example",
    ],
)
def test_iana_reserved_names_are_allowed_anywhere(fake_repo: Path, address: str) -> None:
    """Nobody can register these, so no real person can hold one (RFC 2606 / 6761)."""
    _add(fake_repo, "config/mappings/x_mapping.yaml", f"email: {address}\n")
    assert scan(fake_repo)[0] == []


def test_tests_dir_is_the_only_path_allowance(fake_repo: Path) -> None:
    """The DECLARED GAP, asserted in both directions so it stays exactly one directory."""
    _add(fake_repo, "tests/fixtures.py", f"X = '{PLANTED}'\n")
    _add(fake_repo, "testsuite/fixtures.py", f"X = '{PLANTED}'\n")  # NOT tests/
    _add(fake_repo, "src/tests_helper.py", f"X = '{PLANTED}'\n")  # NOT tests/

    findings, allowed, _unreadable = scan(fake_repo)

    assert sorted(f[0] for f in findings) == ["src/tests_helper.py", "testsuite/fixtures.py"]
    assert [a[0] for a in allowed] == ["tests/fixtures.py"]
    assert "DECLARED GAP" in allowed[0][3]


def test_an_address_inside_undecodable_bytes_is_still_CAUGHT(fake_repo: Path) -> None:
    """INVERTED at the Stage-7 gate — this test previously pinned the SKIP.

    Skipping a file that fails strict UTF-8 was a silent hole: an email address is ASCII,
    so it is perfectly matchable inside a mixed-encoding file, a text file with an embedded
    binary blob, or a file with one stray byte. "No findings" would then be true for a
    reason unrelated to the content — the exact vacuous-green shape this suite exists to
    refuse. Decoding with ``errors="replace"`` keeps the ASCII intact and the match live.
    """
    (fake_repo / "asset.bin").write_bytes(b"\x00\xff\xfe" + PLANTED.encode() + b"\xfe\xff")
    subprocess.run(["git", "add", "asset.bin"], cwd=fake_repo, check=True)

    findings, _allowed, unreadable = scan(fake_repo)

    assert [(f[0], f[2]) for f in findings] == [("asset.bin", PLANTED)]
    assert unreadable == 0, "an openable file is never 'unreadable' — only an unopenable one is"


def test_a_real_binary_asset_produces_no_false_positive(fake_repo: Path) -> None:
    """The counterweight to the inversion above: replacement decoding must not invent hits."""
    (fake_repo / "logo.png").write_bytes(bytes(range(256)) * 8)
    subprocess.run(["git", "add", "logo.png"], cwd=fake_repo, check=True)

    findings, _allowed, _unreadable = scan(fake_repo)

    assert findings == []


def test_an_unopenable_indexed_path_is_counted_not_crashed(fake_repo: Path) -> None:
    """A staged-then-deleted file stays in the index; the walk must survive it."""
    _add(fake_repo, "gone.py", "X = 1\n")
    (fake_repo / "gone.py").unlink()

    findings, _allowed, unreadable = scan(fake_repo)

    assert findings == []
    assert unreadable == 1


# --------------------------------------------------------------------------- #
# Allowlist hygiene                                                            #
# --------------------------------------------------------------------------- #
def test_no_allowlist_entry_is_dead_permission() -> None:
    """Every allowlisted literal must actually OCCUR in the repo it permits.

    An entry with no occurrence is standing permission for an address nobody uses —
    exactly how an allowlist rots into a hole. Deleting the last occurrence should force
    deleting the line.
    """
    _findings, allowed, _unreadable = scan(REPO_ROOT)
    seen = {address.lower() for _rel, _line, address, _reason in allowed}

    unused = sorted((set(PUBLISHED_ADDRESSES) | set(ILLUSTRATIVE_EXAMPLES)) - seen)
    # The co-author trailer is a forward-looking allowance: it lives in commit messages
    # (which this gate does not scan), so it legitimately has no file occurrence.
    assert unused in ([], ["noreply@anthropic.com"]), f"allowlist entries with no occurrence: {unused}"


@pytest.mark.parametrize("address", sorted(PUBLISHED_ADDRESSES) + sorted(ILLUSTRATIVE_EXAMPLES))
def test_allowlist_entries_are_lowercase_and_address_shaped(address: str) -> None:
    """Lookups lowercase the candidate, so an uppercase entry would never match."""
    assert address == address.lower()
    assert EMAIL_RE.fullmatch(address), f"{address!r} would never be produced by the scanner"


def test_the_two_literal_tiers_do_not_overlap() -> None:
    """A literal in both tiers would give two different reasons for the same allowance."""
    assert not set(PUBLISHED_ADDRESSES) & set(ILLUSTRATIVE_EXAMPLES)


def test_is_allowed_reports_which_tier_permitted_it() -> None:
    assert is_allowed("support@myblueprint.ca", "src/x.py").startswith("published:")
    assert is_allowed("doe.john@sd54.bc.ca", "config/x.yaml").startswith("example:")
    assert is_allowed("a@example.com", "src/x.py").startswith("reserved:")
    assert is_allowed(PLANTED, "tests/x.py").startswith("synthetic:")
    assert is_allowed(PLANTED, "src/x.py") is None


def test_redaction_keeps_it_recognisable_without_reusable() -> None:
    assert redact("j.smith@somedistrict.bc.ca") == "j***@s***.bc.ca"
    assert redact("nope") == "***"


# --------------------------------------------------------------------------- #
# The live repo                                                                #
# --------------------------------------------------------------------------- #
def test_this_repository_is_clean() -> None:
    """The gate runs for real against this checkout — so it can never be wired-but-rotten."""
    findings, _allowed, _unreadable = scan(REPO_ROOT)
    assert [(rel, line) for rel, line, _addr in findings] == []


def test_the_live_scan_actually_reads_files(capsys) -> None:
    """Pairs the clean-repo assertion above: prove the scan is not vacuously empty.

    "No findings" would also pass if the walk found no files, or read none of them. This
    asserts the walk saw a substantial tree AND matched real addresses in it.
    """
    _findings, allowed, _unreadable = scan(REPO_ROOT)

    assert len(allowed) > 100, "the scan matched almost nothing — is it reading files at all?"
    assert {rel for rel, _l, _a, _r in allowed} & {"src/ui_flet/screens/help.py", "pyproject.toml"}


def test_the_gate_is_actually_WIRED_into_the_pre_commit_hook() -> None:
    """A scanner nobody invokes is a scanner that never runs.

    Every other test here proves the script WORKS; none of them proves anything CALLS it.
    That is the no-vacuous-greens rule applied to an invocation rather than an assertion —
    deleting the hook line would leave this whole file green.
    """
    hook = (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")

    assert "check_no_emails" in hook
    assert "claugentic-check_architecture_tree" in hook, "the tree gate must survive alongside it"


def test_the_gate_is_actually_WIRED_into_ci() -> None:
    """CI is the backstop that gates the MERGE — the hook can be skipped with --no-verify."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "scripts/check_no_emails.py" in ci


def test_running_the_script_as_a_subprocess_exits_zero() -> None:
    """End-to-end, the way the hook and CI invoke it — argv parsing and git root included."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_no_emails.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK:" in proc.stdout
