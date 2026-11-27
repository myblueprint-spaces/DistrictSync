"""No account names in logs (plan 0055 N1, DSYNC-100; owner 2026-11-23).

:func:`redact` is applied to every line the app writes to a log FILE (:class:`RedactingFormatter`, installed by
``logger._build_file_handler``) and to the two crash reports written outside logging. It is the one choke point, so a
future log line that carries a path or an account is covered without its author remembering to:

* a profile path (``C:\\Users\\<name>\\...``, ``/home/<name>/...``, ``/Users/<name>/...``, and this process's own home
  folder wherever it lives) keeps its shape with the name replaced by ``<user>``;
* this process's account, ``DOMAIN\\user`` (any case) or the bare user name as a whole word (as Windows spells it),
  becomes ``<account>``.

It is deliberately conservative about everything else: it does not guess at other accounts' names. The places that
WROTE an account (the run record, the log file name, the schedule-check marker, ``ProvisionedBy``) stopped writing it;
this catches what reaches a log line by way of a path or an error message.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Final

PLACEHOLDER_USER: Final = "<user>"
PLACEHOLDER_ACCOUNT: Final = "<account>"

# Folders under a profiles directory that are not a person.
_NOT_A_PERSON = {"public", "default", "default user", "all users", "<user>"}
_PROFILE_PATH = re.compile(
    r"(?P<root>(?<![A-Za-z0-9])[A-Za-z]:[\\/]+Users[\\/]+|(?<![^\s'\"(=:])/(?:home|Users)/)"
    r"(?P<name>[^\\/\r\n\"'<>|:*?]+)",
    re.IGNORECASE,
)


def _replace_profile(match: re.Match[str]) -> str:
    name = match.group("name")
    if name.strip().casefold() in _NOT_A_PERSON:
        return match.group(0)
    return match.group("root") + PLACEHOLDER_USER


def _identity() -> tuple[str, str, str]:
    """``(DOMAIN\\user or "", bare user or "", home folder or "")`` for this process."""
    from src.utils.accounts import UNKNOWN_ACCOUNT, process_account

    account = process_account()
    if account == UNKNOWN_ACCOUNT:
        account = ""
    bare = account.rsplit("\\", 1)[-1] if account else os.environ.get("USERNAME", "").strip()
    try:
        home = str(Path.home())
    except (RuntimeError, OSError, KeyError):
        home = ""
    return (account if "\\" in account else "", bare, home)


def redact(text: str, *, identity: tuple[str, str, str] | None = None) -> str:
    """``text`` with profile paths and this process's account replaced. Pure when ``identity`` is given."""
    if not text:
        return text
    domain_account, bare, home = identity if identity is not None else _identity()
    if home and len(home) > 3:
        text = re.sub(re.escape(home), lambda _m: _home_placeholder(home), text, flags=re.IGNORECASE)
    text = _PROFILE_PATH.sub(_replace_profile, text)
    if domain_account:
        text = re.sub(re.escape(domain_account), PLACEHOLDER_ACCOUNT, text, flags=re.IGNORECASE)
    if len(bare) >= 3:
        text = re.sub(rf"(?<![\w$.-]){re.escape(bare)}(?![\w$-])", PLACEHOLDER_ACCOUNT, text)
    return text


def _home_placeholder(home: str) -> str:
    """The home folder with its last part (the name) replaced: ``C:\\Users\\<user>``, ``/home/<user>``."""
    sep = "\\" if "\\" in home else "/"
    parent = home.rstrip("\\/").rsplit(sep, 1)[0]
    return f"{parent}{sep}{PLACEHOLDER_USER}" if parent else PLACEHOLDER_USER


class RedactingFormatter(logging.Formatter):
    """A ``logging.Formatter`` whose every line, traceback included, goes through :func:`redact`."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._identity = _identity()

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), identity=self._identity)
