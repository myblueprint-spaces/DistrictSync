"""Pure navigation-state model for the Flet shell.

NO ``flet`` import — this is trust-critical, cheaply-tested logic that decides
WHAT the navigation offers and which destination leads on launch, independent of
how it renders.

The rail order is **FIXED** — Home, Convert, Run History, Setup, Mapping, Help —
in every state (D7). Spatial memory is a trust property: a district admin who
opens DistrictSync a few times a year must find the same rail in the same order,
so nothing here reorders by setup state (the earlier state-dependent prominence
reordering read as instability and destroyed spatial memory).

**Since 0038 S6 the launch selection is Home in EVERY state**, so this module has no
state-aware output left at all. It used to select Setup while the install still
``needs_setup``, because the newcomer's work lived on that rail item; Home now HOSTS
the setup wizard itself, so selecting Setup would land the same wizard one rail item
away from where the admin already is — and would make the rail's "you are here"
disagree with the surface. ``needs_setup`` stays here (Home's branch predicate reads
it, and so does the Setup badge rule), but nothing in the nav MODEL varies any more.

``needs_setup`` is re-keyed (Slice 5, D4a) to ``AppConfig.has_completed_setup()`` — the durable
finish-line (an explicit flag, or inferred for installs predating the wizard) — rather than the
schedule flag, so a Firefighter whose task broke is not greeted as a newcomer; the fixed order
here does not depend on that split.

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


@dataclass(frozen=True)
class NavModel:
    """The navigation model: the FIXED ordered destinations, and nothing else.

    Collapsed at 0038 S6 — the ``initial_id`` field went with the state-aware launch
    selection it carried. The launch destination is now derivable from the model itself
    (``initial_destination_id``), which is what a single-field model is for: one fact,
    read one way.
    """

    destinations: tuple[Destination, ...]


def needs_setup(app_config: AppConfig) -> bool:
    """THE single source of the "hasn't finished onboarding" predicate for the whole shell.

    Re-keyed in Slice 5 (D4a) to ``AppConfig.has_completed_setup()`` — the durable finish-line
    (an explicit flag, or inferred for installs predating the wizard), NOT the schedule flag
    directly — so a Firefighter whose task broke (Event-141) is never greeted as a newcomer: a
    completed install stays out of onboarding even when its schedule is later found MISSING
    (schedule live-ness is exclusively ``schedule_status``, read back from the OS). The
    onboarding gate + launch selection both key off this single predicate.

    W2-B adds the second, symmetrical guard: an install whose ``config.json`` EXISTS but
    could not be READ (``settings_unreadable()``) is provably NOT a fresh install — the
    file's existence is a checked fact — so the first-run branch, which asserts "you are a
    new user", is suppressed. Note what this deliberately does NOT do: it does not fake
    ``has_completed_setup()`` True. We stop asserting a state we know to be false without
    asserting the opposite state we cannot verify — Home then reports from the run store,
    which is a separate, intact artifact.

    Three consumers since 0038 S6, all reading THIS one predicate: Home's branch (a) host,
    ``setup.build_setup``'s wizard-vs-Settings choice (through the ``has_completed_setup()``
    it wraps), and the Setup rail badge's first-run suppression.
    """
    if app_config.settings_unreadable():
        return False
    return not app_config.has_completed_setup()


def nav_model() -> NavModel:
    """Build the navigation model (pure, and — since 0038 S6 — config-independent).

    Kept as a factory rather than collapsed into ``DESTINATIONS`` so the shell keeps ONE
    seam to read the rail's shape from, and a future state-aware rail has somewhere to go.
    """
    return NavModel(destinations=DESTINATIONS)


def ordered_destinations(model: NavModel) -> tuple[Destination, ...]:
    """The rail's destinations in their ONE fixed order — identical in every state (D7)."""
    return model.destinations


def initial_destination_id(model: NavModel) -> str:
    """The destination a launch selects: the FIRST one (Home), in every state (0038 S6).

    Derived from the model rather than named as a second constant, so "the rail leads with
    Home" is stated exactly once (``DESTINATIONS``). TOTAL: a hand-built empty model
    degrades to ``""`` rather than raising.
    """
    return model.destinations[0].id if model.destinations else ""


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
