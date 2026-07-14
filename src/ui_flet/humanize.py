"""Humanization helpers for the Flet UI — turn machine ids into the words an admin speaks.

PURE + COUNTED (no flet import): trust-surface copy must never show a raw config id
(``sd48myedbc``) or a raw ISO timestamp to a non-technical admin. IA-2 needs only
``friendly_district_name``; IA-3 adds ``friendly_timestamp`` (the run-log verdict copy);
IA-9 consolidates the cross-surface humanization here: ``pluralize`` (the "1 warning /
N warnings" plural, formerly a private copy in Home + Run History), ``friendly_anomaly_detail``
(the anomaly summary, parametrized by ``AnomalyVariant`` — formerly triplicated across
Home / Run History / Convert with intentionally different voices), and ``friendly_sftp_reason``
(a bounded, category-mapped SFTP-failure reason so the raw ``test_connection`` string never
reaches an admin card). Kept minimal by design (YAGNI) — total functions, not a framework.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


def pluralize(word: str, count: int) -> str:
    """Return ``word`` at ``count == 1``, else the naive ``word + "s"`` plural. TOTAL.

    The single source of the "1 warning / N warnings" pluralization the trust surfaces
    render (Home / Run History previously each carried a byte-identical private copy).
    Total over 0 / negative (both → plural), matching the prior copies' behaviour.
    """
    return word if count == 1 else f"{word}s"


class AnomalyVariant(Enum):
    """Which surface's anomaly-detail voice ``friendly_anomaly_detail`` should speak.

    The three surfaces intentionally differ (verified in-plan): Home is plain, Run History
    scopes it to "the most recent run", and Convert adds a "Review … before delivering" CTA
    (and switches the pronoun at plural). A single flattened string would degrade three
    voices, so the shared helper is parametrized by variant, single-sourcing only the
    pluralization + structure. NOT a "tense" — Convert is a distinct member, not a tense of
    the other two.
    """

    HOME = "home"
    HISTORY = "history"
    CONVERT = "convert"


def friendly_anomaly_detail(count: int, *, variant: AnomalyVariant) -> str:
    """The plain-language anomaly summary for a surface — NEVER the raw ``ANOMALY:`` string.

    Single source of the anomaly detail copy across Home / Run History / Convert. Each
    ``variant`` returns the BYTE-FOR-BYTE string that surface historically produced (the
    consolidated copies of the former ``_anomaly_detail``); the pluralization + structure is
    shared, the voice/CTA is the variant. TOTAL — never raises, never surfaces a raw string.
    """
    plural = count != 1
    if variant is AnomalyVariant.HOME:
        if not plural:
            return "One roster file was smaller than usual."
        return f"{count} roster files were smaller than usual."
    if variant is AnomalyVariant.HISTORY:
        if not plural:
            return "One roster file was smaller than usual in the most recent run."
        return f"{count} roster files were smaller than usual in the most recent run."
    # AnomalyVariant.CONVERT — adds the "Review … before delivering" CTA + the pronoun swap.
    if not plural:
        return "One roster file has far fewer rows than last time. Review it before delivering."
    return f"{count} roster files have far fewer rows than last time. Review them before delivering."


# Bounded, category-mapped SFTP-failure reasons — the substrings we match on (case-insensitive)
# → the fixed, admin-safe reason. Ordered by diagnostic priority: auth first (a rejected
# credential can also mention the host), then host-resolution, then reachability, then remote
# path. Every branch returns a FIXED string; the raw ``test_connection`` return NEVER passes
# through (privacy: a raw paramiko/socket string can carry host/socket/path detail).
_SFTP_REASON_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("authentication", "auth", "password"),
        "The username or password wasn't accepted.",
    ),
    (
        ("getaddrinfo", "name or service", "nodename", "resolve"),
        "Couldn't find that host — double-check the SFTP host.",
    ),
    (
        ("timed out", "timeout", "unreachable", "refused", "network"),
        "Couldn't reach the server — check the host and your network.",
    ),
    (
        ("no such file", "not found", "permission"),
        "Connected, but the remote folder wasn't accessible — check the remote path.",
    ),
)

_SFTP_REASON_FALLBACK = (
    "Couldn't connect to SpacesEDU. Check the host, username, password, and remote path, then try again."
)


def friendly_sftp_reason(raw: str) -> str:
    """Map a raw SFTP-test failure string → a bounded, admin-safe category reason. TOTAL.

    ``uploader.test_connection`` returns ``(bool, str)`` where the ``str`` is a raw
    paramiko/socket exception message (a CORE return — IA-9 sanitizes it VIEW-side, never
    touching the core). This maps that raw string to one of four actionable categories
    (auth / host-resolution / reachability / remote-path) via a case-insensitive substring
    match, with a MANDATORY catch-all so an unmapped failure can NEVER fall through to the
    raw string. The admin learns *why* (the category their next action differs on) without
    ever seeing a raw machine string.
    """
    lowered = (raw or "").lower()
    for needles, reason in _SFTP_REASON_RULES:
        if any(needle in lowered for needle in needles):
            return reason
    return _SFTP_REASON_FALLBACK


def friendly_district_name(sis_type: str, *, config_dir: Path | None = None) -> str:
    """Map a SIS id to its human ``district_name``; TOTAL — never raises, never crashes a view.

    - empty/whitespace ``sis_type`` → ``""`` (no district chosen → generic hero copy).
    - else load the config and return its ``district_name`` stripped IFF non-empty,
      otherwise fall back to the raw ``sis_type``.
    - any load failure (FileNotFoundError / ValueError / unexpected) → fall back to the
      raw ``sis_type`` (an admin sees ``sd48myedbc`` at worst, never a blank or a crash),
      logged at ``warning`` (mirrors ``screens/setup._district_options``).

    ``config_dir`` is a test seam passed straight through to ``loader.load_config``
    (whose own ``config_dir`` arg overrides the ``~/.districtsync`` search dirs), so
    this is unit-testable against a fixture mappings dir without a home dependency.
    """
    sis = sis_type.strip()
    if not sis:
        return ""
    try:
        from src.config.loader import load_config

        cfg = load_config(sis, config_dir)
        name = (cfg.district_name or "").strip()
        return name if name else sis
    except Exception as exc:  # noqa: BLE001 - total: any load failure falls back to the raw id
        logger.warning("friendly_district_name(%r) fell back to the raw id: %s", sis, exc)
        return sis


def friendly_timestamp(iso: str, *, now: datetime | None = None) -> str:
    """Turn a run-log ISO timestamp into a plain relative phrase an admin reads.

    TOTAL — never raises, never surfaces the raw ISO. An empty or unparseable input
    falls back to a safe generic ("recently"), so a partial/old record can never leak
    a raw string or crash a trust surface.

    - empty / whitespace / unparseable ``iso`` → ``"recently"``.
    - < 1 minute → ``"just now"``.
    - < 1 hour → ``"{n} minutes ago"`` (``"a minute ago"`` at 1).
    - < 24 hours → ``"{n} hours ago"`` (``"an hour ago"`` at 1).
    - < 2 days → ``"yesterday at H:MM AM/PM"``.
    - < 7 days → ``"{n} days ago"``.
    - else → ``"{n} weeks ago"``.

    ``now`` is the test seam (defaults to ``datetime.now()`` in the parsed value's
    naive/aware kind so the delta is well-defined).
    """
    text = (iso or "").strip()
    if not text:
        return "recently"
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return "recently"

    reference = now if now is not None else datetime.now(tz=parsed.tzinfo)
    try:
        delta_seconds = (reference - parsed).total_seconds()
    except TypeError:
        # naive/aware mismatch between a caller-supplied `now` and `parsed` — total.
        return "recently"

    if delta_seconds < 0:
        # A future/clock-skewed timestamp — don't invent "-3 hours"; treat as recent.
        return "just now"

    minutes = delta_seconds / 60
    hours = delta_seconds / 3600
    days = delta_seconds / 86400

    if minutes < 1:
        return "just now"
    if hours < 1:
        n = int(minutes)
        return "a minute ago" if n == 1 else f"{n} minutes ago"
    if days < 1:
        n = int(hours)
        return "an hour ago" if n == 1 else f"{n} hours ago"
    if days < 2:
        return f"yesterday at {parsed.strftime('%-I:%M %p')}" if _supports_dash_strftime() else _plain_time(parsed)
    if days < 7:
        return f"{int(days)} days ago"
    weeks = int(days // 7)
    return "a week ago" if weeks == 1 else f"{weeks} weeks ago"


def _supports_dash_strftime() -> bool:
    """Whether ``%-I`` (no-pad hour) is honoured — glibc yes, Windows/msvcrt no."""
    try:
        return datetime(2020, 1, 3, 9, 5).strftime("%-I") == "9"
    except ValueError:
        return False


def _plain_time(dt: datetime) -> str:
    """Cross-platform "yesterday at 3:00 AM" without ``%-I`` (strips the leading zero)."""
    return f"yesterday at {dt.strftime('%I:%M %p').lstrip('0')}"
