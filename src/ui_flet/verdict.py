"""Pure verdict-mapping spine for the Flet UI — the product's trust core.

NO ``flet`` import — this is trust-critical, cheaply-tested logic that decides
WHAT a sync verdict *looks and reads like* (green / amber / red, plus a
plain-language headline and a non-colour cue), independent of how it renders.
``components.py`` consumes this mapping to paint a ``HealthVerdictBanner``;
**deriving** which ``Verdict`` from real state (AppConfig / run-history /
staleness) is IA-3's job, not this module's.

Mirrors the ``nav.py`` precedent: a flet-free pure model carrying ``ft.Icons``
member names as plain strings (e.g. ``"CHECK_CIRCLE_ROUNDED"``) that the view
resolves to ``ft.Icons.<NAME>`` — so this stays testable without a display.

**"Verdict is never colour-alone" is a structural, tested invariant:** every
``VerdictVisual`` carries a non-empty ``icon`` AND a non-empty ``tone`` label, so
the accessibility cue can never be dropped by a future consumer (the test asserts
it for every enum member). The ``color`` is always one of the AA-safe verdict
tokens (healthy green-700, warning amber-700, failed red-600) — the bright brand
accents must never leak in as a text-bearing verdict colour.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.ui_flet import tokens


class Verdict(str, Enum):
    """The three sync-health verdicts, in escalating-attention order."""

    HEALTHY = "healthy"
    WARNING = "warning"
    FAILED = "failed"


@dataclass(frozen=True)
class VerdictVisual:
    """How one verdict looks and reads.

    Attributes:
        color: an AA-safe verdict-token hex (the fill/text colour the banner paints).
        icon: an ``ft.Icons`` member NAME (string), resolved in ``components.py``.
        headline: the default plain-language headline an admin reads.
        tone: a short non-colour cue label ("Healthy" / "Attention" / "Failed") —
            the structural guarantee that a verdict is never communicated by colour
            alone.
    """

    color: str
    icon: str
    headline: str
    tone: str


# The single source of "what healthy/warning/failed looks and reads like". Total
# over ``Verdict`` (the test asserts every member is present + each colour is an
# AA-safe verdict token + each carries a non-empty icon and tone).
_VERDICT_VISUALS: dict[Verdict, VerdictVisual] = {
    Verdict.HEALTHY: VerdictVisual(
        color=tokens.color_status_healthy,
        icon="CHECK_CIRCLE_ROUNDED",
        headline="Your roster is syncing",
        tone="Healthy",
    ),
    Verdict.WARNING: VerdictVisual(
        color=tokens.color_status_warning,
        icon="WARNING_AMBER_ROUNDED",
        headline="Needs your attention",
        tone="Attention",
    ),
    Verdict.FAILED: VerdictVisual(
        color=tokens.color_status_failed,
        icon="ERROR_ROUNDED",
        headline="Last sync failed",
        tone="Failed",
    ),
}


def verdict_visuals(v: Verdict) -> VerdictVisual:
    """Map a ``Verdict`` to its ``VerdictVisual`` (total over the enum).

    Pure: no I/O, no ``flet``. A ``KeyError`` here is a programming error (a new
    enum member without a visual) — surfaced loudly by the totality test, never
    swallowed at runtime.
    """
    return _VERDICT_VISUALS[v]
