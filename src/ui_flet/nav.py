"""Pure navigation-state model for the Flet shell.

NO ``flet`` import — this is trust-critical, cheaply-tested logic that decides
WHAT the navigation offers and which destination leads on launch, independent of
how it renders.

The rail order is **FIXED** — Home, Convert, Run History, Setup, Mapping, Help —
in every state (D7). Spatial memory is a trust property: a district admin who
opens DistrictSync a few times a year must find the same rail in the same order,
so nothing here reorders by setup state (the earlier state-dependent prominence
reordering read as instability and destroyed spatial memory). The ONLY state-aware
decision is the *initial selection*: a launch lands on **Setup** while the install
still ``needs_setup`` (a newcomer starts where the work is), otherwise on the first
destination (Home).

Bounded remainder: the launch predicate re-keys from ``needs_setup`` to a durable
``setup_completed`` fact in Slice 5 — a Firefighter whose task broke should not be
greeted as a newcomer; the fixed order here does not depend on that split.

Icon names are Flet ``ft.Icons`` member names (e.g. ``"HOME_ROUNDED"``) carried
as plain strings so this module stays flet-free; ``shell.py`` resolves them to
``ft.Icons.<NAME>``.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.config.app_config import AppConfig


@dataclass(frozen=True)
class Destination:
    """One navigation target: stable ``id``, plain-language ``label``, icon names."""

    id: str
    label: str
    icon: str  # ft.Icons member name (resolved in shell.py)
    selected_icon: str  # ft.Icons member name for the selected state


# The complete destination set in ONE FIXED display order — identical in every
# state (D7: stable IA / spatial memory). Stable ids are the contract the shell's
# screen map + programmatic navigation key off; labels are the product's
# plain-language voice (an admin reads "Home", never a raw id).
DESTINATIONS: tuple[Destination, ...] = (
    Destination("home", "Home", "HOME_OUTLINED", "HOME_ROUNDED"),
    Destination("convert", "Convert", "SYNC_ALT_OUTLINED", "SYNC_ALT_ROUNDED"),
    Destination("run_history", "Run History", "HISTORY_OUTLINED", "HISTORY_ROUNDED"),
    Destination("setup", "Setup", "ROCKET_LAUNCH_OUTLINED", "ROCKET_LAUNCH_ROUNDED"),
    Destination("mapping", "Mapping", "TUNE_OUTLINED", "TUNE_ROUNDED"),
    Destination("help", "Help", "HELP_OUTLINE_ROUNDED", "HELP_ROUNDED"),
)

# The destination a launch selects while the install still needs setup (the
# newcomer starts where the work is). Once set up, the launch lands on the first
# destination instead.
_INITIAL_WHEN_SETUP_NEEDED = "setup"


@dataclass(frozen=True)
class NavModel:
    """Resolved navigation state: the FIXED ordered destinations + the launch selection."""

    destinations: tuple[Destination, ...]
    initial_id: str


def needs_setup(app_config: AppConfig) -> bool:
    """THE single source of the "not fully set up" predicate for the whole shell.

    ``True`` when the install isn't both configured (usable paths/SIS) AND scheduled —
    the same boundary the Home dispatcher's onboarding branch uses and that the launch
    selection here keys off. Single-sourced so "configured" is never re-derived.
    """
    return not (app_config.is_complete() and app_config.schedule_registered)


def nav_model(app_config: AppConfig) -> NavModel:
    """Build the navigation model for the given runtime config (pure).

    Order is FIXED (``DESTINATIONS``); only ``initial_id`` is state-aware.
    """
    return NavModel(destinations=DESTINATIONS, initial_id=_initial_id(app_config))


def _initial_id(app_config: AppConfig) -> str:
    """The launch selection: Setup while ``needs_setup``, else the first destination.

    ``DESTINATIONS`` is a fixed, non-empty module constant, so ``[0]`` is always safe;
    ``prominent_initial_id`` carries the empty-model totality (a hand-built ``NavModel``
    may still set ``initial_id=""``).
    """
    return _INITIAL_WHEN_SETUP_NEEDED if needs_setup(app_config) else DESTINATIONS[0].id


def ordered_destinations(model: NavModel) -> tuple[Destination, ...]:
    """The rail's destinations in their ONE fixed order — identical in every state (D7)."""
    return model.destinations


def prominent_initial_id(model: NavModel) -> str:
    """The id of the destination to select on launch (Setup while unset, else the first)."""
    return model.initial_id


def selected_index_for(dest_id: str, ordered: tuple[Destination, ...]) -> int:
    """The rail ``selected_index`` for ``dest_id`` within ``ordered`` (fallback ``0``).

    The single source the rail uses for its INITIAL highlight AND the shell uses to
    SYNC the highlight on programmatic navigation — so a user click and a code-driven
    hop can never land the highlight on different indices. Unknown id → ``0`` (total).
    """
    for index, dest in enumerate(ordered):
        if dest.id == dest_id:
            return index
    return 0
