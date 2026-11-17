"""When DistrictSync checks for an update, and when it must not (plan 0054 S4, DSYNC-63).

The shared package does the check itself: fetching the signed manifest, verifying it, staging the files.
This module owns only the decisions that are DistrictSync's:

**Two triggers.** ``after_run`` (after the app's OWN scheduled run, never before or during a conversion)
and ``window`` (when the window opens). A hand-run CLI, a district's own cron and manual Convert never
trigger a check. Both are bounded by ``MIN_INTERVAL`` so neither hammers the feed.

**Zero connections unless every guard passes.** In order, the first that applies wins and NOTHING is
opened: ``portable`` (not the installed flavour), ``not_installed`` (no Velopack install around the exe),
``machine_scope`` (the shared profile; plan 0049, a later plan owns that setup), ``data_dir_override``
(``DISTRICTSYNC_DATA_DIR`` is a test, rehearsal or support profile), ``off_in_settings``
(``AppConfig.update_enabled``), ``not_configured`` (no feed host or trusted key yet), ``package_missing``,
``off_switch`` (the package's own switch: ``DISTRICTSYNC_UPDATES=off`` or its marker file), ``unknown_version``
(a development stamp), ``recent``.

**It can never change a run.** Staging is the most this slice does; applying is a later slice (S5), so a
check leaves the running version exactly as it was. Every failure, slowness and refusal is a RESULT, logged
and recorded, never an exception to the caller. The conversion's exit code and run record are decided
before this runs and cannot be altered by it.

**A wall-clock budget.** The package bounds the whole check (``deadline_seconds``, ``BUDGET_SECONDS``): a cut-off is the
refusal ``timed_out``. Our own watchdog thread from slice S4 is gone because the package's now covers it. Kept: the
``MIN_INTERVAL`` gate (so a window opened ten times a day fetches the manifest once), and the ``except`` around the
package call (a check must never change a run, whatever the package raises).

What this is NOT: the one place the feed host or keys are decided (``src.update.feed``), nor the place the
package is imported (``src.update.package``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from src.config.app_config import AppConfig
from src.update import apply
from src.update.feed import FeedTarget, production_target
from src.update.package import HEALTH_FILE, UPDATER_STATE_FOLDER, app_identity, load_updater
from src.update.startup import install_root
from src.utils import paths
from src.utils.build_flavor import is_e2e_build, is_installed_build
from src.utils.version import app_version

logger = logging.getLogger(__name__)

TRIGGER_AFTER_RUN: Final = "after_run"
TRIGGER_WINDOW: Final = "window"

#: The longest a whole check may take, manifest and download together (plan 0054 section 4: 5 minutes). Passed to the
#: package as ``deadline_seconds``; the package enforces it. The per-request timeout below is within it.
BUDGET_SECONDS: Final = 300.0
REQUEST_TIMEOUT_SECONDS: Final = 15.0
#: Attempts closer together than this are skipped (reason ``recent``).
MIN_INTERVAL: Final = timedelta(hours=6)

LAST_CHECK_FILE: Final = "last-check.json"
STAGING_FOLDER: Final = "staging"

# Outcome statuses.
SKIPPED: Final = "skipped"
NONE_NEWER: Final = "none"
STAGED: Final = "staged"
HANDED_OFF: Final = "handed_off"  # given to the installer; applied after this process exits
REFUSED: Final = "refused"
ERROR: Final = "error"


@dataclass(frozen=True)
class CheckOutcome:
    status: str  # skipped | none | staged | handed_off | refused | error
    reason: str  # a short code, never free text, never an address or a path
    version: str | None = None


def updater_state_dir() -> Path:
    """Where the update state lives: inside the profile, so ``DISTRICTSYNC_DATA_DIR`` and uninstall behave."""
    return paths.user_data_dir() / UPDATER_STATE_FOLDER


def _in_machine_scope() -> bool:
    try:
        return paths.is_machine_scope()
    except paths.MachineScopeRefused:
        return True  # a refused shared profile is still "not the per-user profile": never check from it


def _read_last(state_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((state_dir / LAST_CHECK_FILE).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_last(state_dir: Path, outcome: CheckOutcome, trigger: str, now: datetime) -> None:
    record = {"at": now.isoformat(), "trigger": trigger, **asdict(outcome)}
    temp = state_dir / f".{uuid.uuid4().hex}.tmp"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf8")
        os.replace(temp, state_dir / LAST_CHECK_FILE)
    except OSError:
        temp.unlink(missing_ok=True)
        logger.debug("could not record the update check", exc_info=True)


def skip_reason(
    *,
    config: AppConfig | None = None,
    target: FeedTarget | None = None,
    updater: ModuleType | None = None,
    state_dir: Path | None = None,
    now: datetime | None = None,
    ignore_interval: bool = False,
) -> str | None:
    """The first reason a check must not happen, or ``None`` when it may. Opens no connection."""
    if not is_installed_build():
        return "portable"
    if install_root() is None:
        return "not_installed"
    if _in_machine_scope():
        return "machine_scope"
    if paths.data_dir_override_active() and not is_e2e_build():
        return "data_dir_override"
    if not (AppConfig.load() if config is None else config).update_enabled:
        return "off_in_settings"
    if (production_target() if target is None else target) is None:
        return "not_configured"
    try:
        module = load_updater() if updater is None else updater
    except ImportError:
        return "package_missing"
    if module is None:
        return "portable"
    directory = updater_state_dir() if state_dir is None else state_dir
    if not module.updates_enabled(app_identity(module), directory):
        return "off_switch"
    if module.parse_version(app_version()) is None:
        return "unknown_version"
    if not ignore_interval:
        current = datetime.now(UTC) if now is None else now
        with contextlib.suppress(TypeError, ValueError):
            if current - datetime.fromisoformat(str(_read_last(directory).get("at"))) < MIN_INTERVAL:
                return "recent"
    return None


def run_update_check(
    trigger: str,
    *,
    config: AppConfig | None = None,
    target: FeedTarget | None = None,
    updater: ModuleType | None = None,
    state_dir: Path | None = None,
    now: datetime | None = None,
    budget: float = BUDGET_SECONDS,
    installer: Any | None = None,
    running: Callable[[], int | None] | None = None,
) -> CheckOutcome:
    """Run one update check if every guard allows it, and hand a verified update to the installer when that is safe.

    Never raises. Applying happens after this process exits (the package's hand-off), never during, and never restarts
    the app. ``target``/``updater``/``state_dir``/``installer``/``running`` are test seams: no production call site
    passes them.
    """
    try:
        return _run(trigger, config, target, updater, state_dir, now, budget, installer, running)
    except Exception:  # noqa: BLE001 - a check must never change the caller's result
        logger.warning("update check failed unexpectedly; nothing was changed", exc_info=True)
        return CheckOutcome(ERROR, "unexpected")


def _run(
    trigger: str,
    config: AppConfig | None,
    target: FeedTarget | None,
    updater: ModuleType | None,
    state_dir: Path | None,
    now: datetime | None,
    budget: float,
    installer: Any | None,
    running: Callable[[], int | None] | None,
) -> CheckOutcome:
    resolved_target = production_target() if target is None else target
    reason = skip_reason(config=config, target=resolved_target, updater=updater, state_dir=state_dir, now=now)
    if reason is not None or resolved_target is None:
        return CheckOutcome(SKIPPED, reason or "not_configured")
    module = load_updater() if updater is None else updater
    if module is None:  # unreachable: skip_reason returned None, so the installed build has its package
        return CheckOutcome(SKIPPED, "portable")
    directory = updater_state_dir() if state_dir is None else state_dir
    directory.mkdir(parents=True, exist_ok=True)
    identity = app_identity(module)
    health = module.HealthStore(directory / HEALTH_FILE)
    fetcher = module.FeedFetcher(
        resolved_target.host,
        allow_loopback_http=resolved_target.allow_loopback_http,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    when = datetime.now(UTC) if now is None else now

    try:
        # The package bounds the WHOLE check (manifest and download together, enforced as bytes arrive) and reports a
        # cut-off as the refusal ``timed_out``; it does not re-download a package already staged and matching.
        result = module.check_for_update(
            fetcher,
            resolved_target.manifest_path,
            resolved_target.trusted_keys,
            when,
            app_version(),
            directory / STAGING_FOLDER,
            health.last_sequence(),
            health.bad_versions(),
            resolved_target.channel,
            identity,
            directory,
            deadline_seconds=budget,
        )
    except Exception as exc:  # noqa: BLE001 - a check must never change the caller's result
        logger.warning("update check raised %s; nothing was changed", type(exc).__name__)
        outcome = CheckOutcome(ERROR, "raised")
    else:
        if result is None:
            outcome = CheckOutcome(NONE_NEWER, "up_to_date")
        elif isinstance(result, module.UpdateRefused):
            outcome = CheckOutcome(REFUSED, str(result.reason))
        else:
            outcome = _hand_off(module, result, trigger, config, directory, identity, health, when, installer, running)
    _write_last(directory, outcome, trigger, when)
    return outcome


def _hand_off(
    module: ModuleType,
    staged: Any,
    trigger: str,
    config: AppConfig | None,
    directory: Path,
    identity: Any,
    health: Any,
    when: datetime,
    installer: Any | None,
    running: Callable[[], int | None] | None,
) -> CheckOutcome:
    """Give a staged update to the installer if it is safe to; else leave it staged and say why (a code)."""
    version = str(staged.version)
    try:
        if trigger == TRIGGER_WINDOW:
            settings = AppConfig.load() if config is None else config
            if not apply.window_may_request_apply(settings, when.astimezone()):
                return CheckOutcome(STAGED, "not_applied_keep_clear_of_nightly", version)
        result = module.hand_off(
            staged,
            apply.default_installer() if installer is None else installer,
            identity,
            directory,
            apply.other_instances_counter(module) if running is None else running,
            health.is_healthy,
        )
    except Exception:  # noqa: BLE001 - an apply problem must leave the update staged and the run untouched
        logger.warning("handing the update to the installer failed; it stays staged", exc_info=True)
        return CheckOutcome(STAGED, "not_applied_error", version)
    if isinstance(result, module.HandoffRefused):
        return CheckOutcome(STAGED, f"not_applied_{result.reason}", version)
    return CheckOutcome(HANDED_OFF, "handed_off", version)


def last_check_summary(state_dir: Path | None = None) -> dict[str, Any]:
    """The recorded last attempt (``at``, ``trigger``, ``status``, ``reason``, ``version``), or ``{}``."""
    return _read_last(updater_state_dir() if state_dir is None else state_dir)
