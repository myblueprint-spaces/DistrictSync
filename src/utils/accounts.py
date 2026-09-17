"""Who is running — COUNTED, total primitives for naming the current account.

Two pure-ish helpers with exactly one job each:

* :func:`process_account` — the OS account this process runs as, as a display string
  (``DOMAIN\\user`` when Windows tells us both parts). It is read by
  :mod:`src.utils.paths` for the machine-scope log-file name and, from S-1a-ii, stamped
  onto a run record so Run History can say WHICH account a night ran as. **Never
  raises**: a path resolver calls it, so a surprise here would take the log sink down
  with it.
* :func:`sanitise_account_for_filename` — pure and TOTAL. It names a FILE, so it may
  never return ``""`` and may never return a path separator: a ``DOMAIN\\user`` that
  survived as ``domain\\user`` would redirect the log into a subdirectory (or fail the
  open outright) on exactly the shared install where two writers must not contend on one
  rotating handler.

**Why this is its own module** (plan 0049 S-1a-i.2): three consumers in three layers —
``utils/paths`` (the log name), ``etl/pipeline`` and ``ui_flet/screens/convert`` (the run
record). ``pipeline`` importing an account helper *from the path resolver* would be worse
coupling than a small module of counted primitives; :mod:`src.utils.identity` is the
standing precedent for this shape.

Deliberately NOT here: ``scheduler/windows.current_run_as_user()`` and
``scheduler/elevation._current_user()``. Both name the account for a SECURITY decision (a
task principal, an owner-only DACL grant) rather than for display, and re-pointing either
at this module would be churn on a security-adjacent path for no functional gain. The
near-duplicate stands, deliberately.
"""

from __future__ import annotations

import getpass
import os
import re

# The characters a log-file name may contain after sanitising. Lowercase only, so two
# spellings of one account can never produce two rotating handlers on one file.
_SAFE_CHARS = re.compile(r"[^a-z0-9._-]")
_REPEATS = re.compile(r"_{2,}")

# Long enough to stay recognisable (``corp_some.long.service``), short enough that the
# name plus ``etl_tool-`` and ``.log`` cannot approach a path-length limit.
_MAX_LEN = 32

# The answer when there is nothing usable to name. A literal, never an empty string.
UNKNOWN_ACCOUNT = "unknown"


def process_account() -> str:
    """The account this process runs as — ``DOMAIN\\user``, a bare user, or ``unknown``.

    Resolution, in order: ``USERDOMAIN``+``USERNAME`` when Windows supplies both (the
    form a scheduled task's principal is written in), else :func:`getpass.getuser`
    (which covers POSIX and a Windows session with no domain), else
    :data:`UNKNOWN_ACCOUNT`.

    Never raises — ``getpass.getuser`` raises ``OSError`` when it can find no login name
    at all, and that must degrade to a name, not to a dead log sink.
    """
    domain = os.environ.get("USERDOMAIN", "").strip()
    username = os.environ.get("USERNAME", "").strip()
    if domain and username:
        return f"{domain}\\{username}"
    try:
        resolved = getpass.getuser().strip()
    except (OSError, KeyError):
        # The only documented failures: no login name in the environment (OSError since
        # 3.13) and no passwd entry for the uid (KeyError). Anything else still raises —
        # this is a narrowed swallow with a stated contract, not a silent catch-all.
        return UNKNOWN_ACCOUNT
    return resolved or UNKNOWN_ACCOUNT


def sanitise_account_for_filename(value: str) -> str:
    """Reduce an account name to a safe file-name fragment. Pure and TOTAL.

    ``CORP\\jane`` → ``corp_jane`` · ``CORP\\svc$`` → ``corp_svc`` · ``""`` → ``unknown``.

    Rules, in order: lowercase → every character outside ``[a-z0-9._-]`` (path separators
    included) becomes ``_`` → runs of ``_`` collapse to one → leading/trailing ``_`` are
    trimmed → truncate to 32 characters → trim again (a cut must not leave a dangling
    separator) → :data:`UNKNOWN_ACCOUNT` if nothing is left.

    The guarantees the caller relies on: the result is non-empty, contains no ``/`` or
    ``\\``, and is stable for a given input (no environment read — the CALLER decides
    whose name this is).
    """
    reduced = _SAFE_CHARS.sub("_", value.lower())
    reduced = _REPEATS.sub("_", reduced).strip("_")
    return reduced[:_MAX_LEN].strip("_") or UNKNOWN_ACCOUNT
