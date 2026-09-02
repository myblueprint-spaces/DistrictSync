"""Centralized input validation for security-sensitive operations.

All user-supplied values that flow into subprocess calls, crontab entries,
SFTP connections, or config file paths must be validated here before use.
"""

from __future__ import annotations

import re
import shlex
import unicodedata
from datetime import date

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

ALLOWED_SFTP_HOSTS: frozenset[str] = frozenset(
    {
        "sftp.ca.spacesedu.com",
        "sftp.app.spacesedu.com",
        "sftp.myblueprint.ca",
    }
)

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_SIS_TYPE_RE = re.compile(r"^[a-zA-Z0-9_]+$")
_TASK_NAME_RE = re.compile(r"^[a-zA-Z0-9_ -]+$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
# Recurring seasonal-window boundary: a zero-padded ``MM-DD`` (year-independent).
_MONTH_DAY_RE = re.compile(r"^\d{2}-\d{2}$")
# A leap year, so ``02-29`` is a REAL date for the "is this a calendar day?" check —
# the seasonal window recurs annually and must accept Feb 29 (the predicate clamps
# it in non-leap years; see src/etl/sync_window.py).
_MONTH_DAY_PROBE_YEAR = 2000
# Windows run-as account: DOMAIN\user or bare user. Letters, digits, dot,
# underscore, hyphen, and at most ONE backslash domain separator. No
# whitespace or special characters — this value is interpolated into a
# PowerShell ``-User`` / principal ``-UserId`` parameter (passed to
# ``Register-ScheduledTask`` via the child env), so it must stay a clean
# account identifier with no PowerShell-meaningful characters.
_RUN_AS_USER_RE = re.compile(r"^[A-Za-z0-9._-]+(?:\\[A-Za-z0-9._-]+)?$")

# Maximum length for a run-as account string (DOMAIN\user).
_RUN_AS_USER_MAX_LEN = 256

# The admin's identity email (plan 0038). 254 is the RFC 5321 maximum length of a
# deliverable address (the SMTP ``MAIL FROM`` path limit minus its angle brackets), so it
# is the honest ceiling rather than an invented one.
IDENTITY_EMAIL_MAX_LEN = 254

# Local part: the conventional, well-trodden subset. RFC 5322 additionally permits
# ``!#$&'*/=?^`{|}~`` and quoted forms; those are deliberately REFUSED — see
# :func:`validate_identity_email` for why the narrowing is safe here.
_IDENTITY_LOCAL_RE = re.compile(r"^[A-Za-z0-9._%+-]+$")
# Domain: starts alphanumeric, at least one dot, an alphabetic TLD of 2+. Case-insensitive
# here because the admin TYPES this; the config-side twin is the MODULE-LEVEL
# ``src/config/models._DISTRICT_DOMAIN_RE`` (module-level, not a class attribute), which is
# the lowercase-only form because a config author AUTHORS that value and a mixed-case row
# would never match a normalised domain. Deliberately two rules for two jobs; a parity test
# (tests/test_config_district_domains.py) pins that every shipped domain satisfies both.
_IDENTITY_DOMAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$")


# ---------------------------------------------------------------------------
# Validators — each returns the sanitised value or raises ValueError
# ---------------------------------------------------------------------------


# A stored "this config was tested" fact (plan 0044 S3, `AppConfig.creator_verified`)
# is keyed on a sha256 digest of the RESOLVED config: exactly 64 lowercase hex chars.
# Matched with ``fullmatch``, not ``match``: Python's ``$`` also matches BEFORE a final
# newline, so ``match`` accepts a 65-character value ending in "\n" — a value that can
# never equal a computed digest, and so would read as a shape we vouched for and a fact
# that never matches.
_CONFIG_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def is_config_digest(value: object) -> bool:
    """TOTAL predicate: is ``value`` a sha256 hex digest (64 lowercase hex chars)?

    The ONE spelling of the shape both ``app_config`` (write-time validation of
    ``creator_verified`` values) and ``ui_flet.config_editor`` (read-time re-validation
    of a hand-editable ``config.json``) check. Total over any object — a non-string is
    simply not a digest, never a ``TypeError`` — because a malformed stored value must
    read as ABSENT (re-test), never crash the settings load.
    """
    return isinstance(value, str) and _CONFIG_DIGEST_RE.fullmatch(value) is not None


def validate_sis_type(value: str) -> str:
    """Ensure *value* is alphanumeric/underscore only (e.g. ``myedbc``)."""
    value = value.strip()
    if not _SIS_TYPE_RE.match(value):
        raise ValueError(f"Invalid SIS type '{value}'. Must contain only letters, digits, and underscores.")
    return value


def validate_task_name(value: str) -> str:
    """Ensure *value* is safe for use as a Windows Task Scheduler name."""
    value = value.strip()
    if not _TASK_NAME_RE.match(value):
        raise ValueError(
            f"Invalid task name '{value}'. Must contain only letters, digits, spaces, underscores, and hyphens."
        )
    return value


def validate_run_time(value: str) -> tuple[str, str]:
    """Validate ``HH:MM`` format and return ``(hour, minute)`` strings.

    Raises ValueError for malformed or out-of-range values.
    """
    value = value.strip()
    if not _TIME_RE.match(value):
        raise ValueError(f"Invalid run time '{value}'. Expected HH:MM (24-hour) format.")
    hour, minute = value.split(":")
    if not (0 <= int(hour) <= 23):
        raise ValueError(f"Hour must be 00–23, got '{hour}'.")
    if not (0 <= int(minute) <= 59):
        raise ValueError(f"Minute must be 00–59, got '{minute}'.")
    return hour, minute


def validate_run_as_user(user: str) -> str:
    """Validate a Windows run-as account for a PowerShell scheduled-task principal.

    The value flows to ``Register-ScheduledTask``'s ``-User`` and the principal's
    ``-UserId`` (via the spawned PowerShell process's environment, not a shell
    argument list). Accepts a bare username (``jane``) or a ``DOMAIN\\user`` pair
    (``CORP\\jane``). Permits letters, digits, ``.``, ``_``, ``-`` and at most
    one backslash as the domain separator. Rejects empty values, internal
    whitespace, and any special character so the value stays a clean account
    identifier with no PowerShell-meaningful characters.

    Returns the stripped value on success; raises ``ValueError`` otherwise.
    """
    user = user.strip()
    if not user:
        raise ValueError("Run-as user must not be empty.")
    if len(user) > _RUN_AS_USER_MAX_LEN:
        raise ValueError(f"Run-as user is too long (max {_RUN_AS_USER_MAX_LEN} characters).")
    if not _RUN_AS_USER_RE.match(user):
        raise ValueError(
            f"Invalid run-as user '{user}'. Use 'DOMAIN\\user' or 'user' with only "
            "letters, digits, dots, underscores, and hyphens (no spaces or special characters)."
        )
    return user


def validate_month_day(md: str) -> str:
    """Validate a recurring ``MM-DD`` seasonal-window boundary; return the normalized value.

    The seasonal sync window (``AppConfig.sync_window_start`` / ``sync_window_end``)
    recurs every year, so a boundary is a MONTH-DAY, not a full date. Accepts a
    zero-padded ``MM-DD`` that names a real calendar day — including ``02-29``, which
    the window predicate resolves in non-leap years (see ``src/etl/sync_window.py``).
    Rejects a wrong shape (``8-1``, ``0811``, ``08-11-2026``), an out-of-range month
    (``13-01``), and a non-existent day (``02-30``, ``04-31``).

    Reused by both the config-load path and the UI window gate, so "is this a valid
    window boundary?" lives in exactly one place. Returns the stripped value on
    success; raises ``ValueError`` otherwise.
    """
    md = md.strip()
    if not _MONTH_DAY_RE.match(md):
        raise ValueError(f"Invalid month-day '{md}'. Expected zero-padded MM-DD (e.g. '08-11').")
    month, day = int(md[:2]), int(md[3:])
    try:
        # A leap probe year so 02-29 is accepted; this rejects 13-01 / 02-30 / 04-31.
        date(_MONTH_DAY_PROBE_YEAR, month, day)
    except ValueError as exc:
        raise ValueError(f"Invalid month-day '{md}': not a real calendar date (MM-DD).") from exc
    return md


def _is_unusable_character(ch: str) -> bool:
    """True for whitespace and every Unicode ``C*`` category (control / format / surrogate).

    Catches what a strict ASCII regex would also catch, but EARLIER and with a far more
    actionable message — the common real cases are a trailing newline from a paste, a
    non-breaking space from a Word document, and a zero-width or bidi-override character
    smuggled in from a rich-text source. ``C*`` covers Cc (control), Cf (format, incl.
    U+200B and U+202E), Cs, Co and Cn in one rule.
    """
    return ch.isspace() or unicodedata.category(ch).startswith("C")


def validate_identity_email(raw: str) -> str:
    """Validate the admin's typed work email at the BOUNDARY; return it un-normalised.

    The single rejection point for the identity page / Settings section (plan 0038). It
    runs BEFORE ``src.utils.identity.normalize_email`` and before anything is persisted —
    "validate at boundaries", so a malformed value can never reach the stored settings,
    the Help echo, or the matching comparison.

    Two deliberate differences from every sibling validator in this module, both
    load-bearing:

    * **It does not normalise.** Only surrounding whitespace is trimmed; case and Unicode
      form are returned exactly as typed, so the address echoed back to the admin is the
      one they entered. Reduction for COMPARISON is ``identity.normalize_email``'s job and
      lives there alone.
    * **Its messages never quote the value.** Siblings echo the offending input
      (``Invalid SIS type 'x'``); an email address is personal data, so a caller that logs
      ``str(exc)`` would leak it. Each message carries the RULE and an example instead,
      and that is pinned by a test.

    Accepted: a single ``@``; a non-empty local part of ``A-Z a-z 0-9 . _ % + -``; a
    domain starting alphanumeric with at least one dot and a 2+ letter alphabetic TLD;
    total length ≤ :data:`IDENTITY_EMAIL_MAX_LEN`. Rejected: anything else — including CR,
    LF, NUL and other control characters, internal whitespace, zero-width and
    bidirectional-control characters, and non-ASCII.

    **The non-ASCII narrowing is deliberate and it is a real limitation.** This module is
    the security-validator layer, and an allowlist that a reviewer can hold in their head
    is worth more here than RFC 6531 completeness; every district this product serves uses
    ASCII staff addresses. An admin whose address this refuses is not stranded: the launch
    page's "I'm not the person who set this up" path and the SD-number path both enter the
    app with the full unfiltered district list (identification is never a gate). If a real
    partner ever needs an internationalised address, widen the charset here — that is the
    one place to change.

    Returns the trimmed value on success; raises ``ValueError`` otherwise.
    """
    value = raw.strip()
    if not value:
        raise ValueError("Enter the work email address of the person who looks after this sync.")
    if len(value) > IDENTITY_EMAIL_MAX_LEN:
        raise ValueError(f"That email address is too long (the maximum is {IDENTITY_EMAIL_MAX_LEN} characters).")
    if any(_is_unusable_character(ch) for ch in value):
        raise ValueError(
            "That email address contains characters we can't use — remove any spaces, line breaks, "
            "or invisible characters and try again."
        )
    local, at, domain = value.partition("@")
    if not at or "@" in domain:
        raise ValueError("An email address needs exactly one @ — for example, name@yourdistrict.bc.ca.")
    if not _IDENTITY_LOCAL_RE.match(local):
        raise ValueError(
            "The part before the @ doesn't look right. Use letters, digits, and . _ % + - "
            "— for example, name@yourdistrict.bc.ca."
        )
    # Tolerate EXACTLY the one trailing root dot ``identity.normalize_email`` strips, and
    # no more — so the validator accepts precisely the set normalisation can reduce.
    bare_domain = domain[:-1] if domain.endswith(".") else domain
    if not _IDENTITY_DOMAIN_RE.match(bare_domain):
        raise ValueError("The part after the @ doesn't look like a domain — for example, name@yourdistrict.bc.ca.")
    return value


def validate_sftp_host(host: str) -> str:
    """Ensure *host* is in the SpacesEDU SFTP allowlist.

    Raises ValueError if the host is not one of the known SpacesEDU servers.
    """
    host = host.strip().lower()
    if host not in ALLOWED_SFTP_HOSTS:
        allowed = ", ".join(sorted(ALLOWED_SFTP_HOSTS))
        raise ValueError(f"SFTP host '{host}' is not allowed. Permitted hosts: {allowed}")
    return host


def quote_for_shell(value: str) -> str:
    """Shell-quote a value for safe inclusion in crontab or similar."""
    return shlex.quote(str(value))
