"""Pure navigation-state model for the Flet shell.

NO ``flet`` import — this is trust-critical, cheaply-tested logic that decides
WHAT the navigation offers and (later) which group leads, independent of how it
renders. ``shell.py`` renders a FLAT rail this slice (there is nothing to be
prominent about yet); the grouped, state-aware *prominence* wiring lands at IA-1,
but the model is built + tested NOW so that wiring is a render change, not a
logic change.

Icon names are Flet ``ft.Icons`` member names (e.g. ``"HOME_ROUNDED"``) carried
as plain strings so this module stays flet-free; ``shell.py`` resolves them to
``ft.Icons.<NAME>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.config.app_config import AppConfig


class NavGroup(str, Enum):
    """Navigation groups, in display order."""

    GET_STARTED = "Get started"
    EVERYDAY = "Everyday"
    ADVANCED = "Advanced"


@dataclass(frozen=True)
class Destination:
    """One navigation target: stable ``id``, plain-language ``label``, icon name, group."""

    id: str
    label: str
    icon: str  # ft.Icons member name (resolved in shell.py)
    selected_icon: str  # ft.Icons member name for the selected state
    group: NavGroup


# The complete destination set (ordered as the rail shows them). Stable ids are
# the contract the placeholder host + future surfaces key off; labels are the
# product's plain-language voice (an admin reads "Home", never a raw id).
DESTINATIONS: tuple[Destination, ...] = (
    Destination("home", "Home", "HOME_OUTLINED", "HOME_ROUNDED", NavGroup.EVERYDAY),
    Destination("convert", "Convert", "SYNC_ALT_OUTLINED", "SYNC_ALT_ROUNDED", NavGroup.EVERYDAY),
    Destination("run_history", "Run History", "HISTORY_OUTLINED", "HISTORY_ROUNDED", NavGroup.EVERYDAY),
    Destination("setup", "Setup", "ROCKET_LAUNCH_OUTLINED", "ROCKET_LAUNCH_ROUNDED", NavGroup.GET_STARTED),
    Destination("mapping", "Mapping", "TUNE_OUTLINED", "TUNE_ROUNDED", NavGroup.ADVANCED),
    Destination("help", "Help", "HELP_OUTLINE_ROUNDED", "HELP_ROUNDED", NavGroup.ADVANCED),
)


@dataclass(frozen=True)
class NavModel:
    """Resolved navigation state: ordered destinations, the group->destinations map,
    and which group leads given the current ``AppConfig``."""

    destinations: tuple[Destination, ...]
    groups: dict[NavGroup, tuple[Destination, ...]]
    prominent_group: NavGroup


def _prominent_group(app_config: AppConfig) -> NavGroup:
    """Which group the UI should lead with, derived from real setup state.

    An admin who hasn't finished setup (no usable paths/SIS, or no schedule
    registered) is led to **Get started**; a fully-configured, scheduled install
    leads with **Everyday** (their day-to-day cockpit). Reads ``AppConfig``'s own
    predicates — never re-derives "configured".
    """
    if not app_config.is_complete() or not app_config.schedule_registered:
        return NavGroup.GET_STARTED
    return NavGroup.EVERYDAY


def nav_model(app_config: AppConfig) -> NavModel:
    """Build the navigation model for the given runtime config (pure)."""
    groups: dict[NavGroup, list[Destination]] = {group: [] for group in NavGroup}
    for dest in DESTINATIONS:
        groups[dest.group].append(dest)
    return NavModel(
        destinations=DESTINATIONS,
        groups={group: tuple(items) for group, items in groups.items()},
        prominent_group=_prominent_group(app_config),
    )
