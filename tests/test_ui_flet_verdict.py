"""Tests for src/ui_flet/verdict.py — the pure verdict-mapping spine.

The trust core: ``verdict_visuals`` is total over ``Verdict``, every visual's
``color`` is one of the AA-safe verdict tokens, and every visual carries a
non-colour cue (a non-empty ``icon`` AND a non-empty ``tone``) — so "a verdict is
never communicated by colour alone" is a TESTED invariant of the model, not just
an intention that a future consumer could quietly drop.
"""

from __future__ import annotations

import pytest

from src.ui_flet import tokens
from src.ui_flet.verdict import Verdict, VerdictVisual, verdict_visuals

# The only colours a text-bearing verdict may paint (the bright brand accents must
# never leak in — see tokens.py's healthy-verdict note).
_AA_SAFE_VERDICT_COLORS = {
    tokens.color_status_healthy,
    tokens.color_status_warning,
    tokens.color_status_failed,
}


class TestVerdictVisualsTotality:
    def test_total_over_every_enum_member(self):
        """Every Verdict member maps to a VerdictVisual (no KeyError, none missing)."""
        for v in Verdict:
            visual = verdict_visuals(v)
            assert isinstance(visual, VerdictVisual)

    def test_three_verdicts_exist(self):
        assert {v.name for v in Verdict} == {"HEALTHY", "WARNING", "FAILED"}


class TestNonColourCueInvariant:
    @pytest.mark.parametrize("v", list(Verdict))
    def test_every_visual_has_a_non_empty_icon(self, v: Verdict):
        """The icon is the structural non-colour cue — never empty for any verdict."""
        assert verdict_visuals(v).icon.strip(), f"{v} has no icon cue"

    @pytest.mark.parametrize("v", list(Verdict))
    def test_every_visual_has_a_non_empty_tone_label(self, v: Verdict):
        """The tone label is the non-colour text cue — never empty for any verdict."""
        assert verdict_visuals(v).tone.strip(), f"{v} has no tone label"

    @pytest.mark.parametrize("v", list(Verdict))
    def test_every_visual_has_a_non_empty_headline(self, v: Verdict):
        assert verdict_visuals(v).headline.strip(), f"{v} has no headline"


class TestVerdictColours:
    @pytest.mark.parametrize("v", list(Verdict))
    def test_colour_is_an_aa_safe_verdict_token(self, v: Verdict):
        """A verdict's colour is always one of the AA-safe verdict tokens."""
        assert verdict_visuals(v).color in _AA_SAFE_VERDICT_COLORS, v

    def test_each_verdict_maps_to_its_own_token(self):
        assert verdict_visuals(Verdict.HEALTHY).color == tokens.color_status_healthy
        assert verdict_visuals(Verdict.WARNING).color == tokens.color_status_warning
        assert verdict_visuals(Verdict.FAILED).color == tokens.color_status_failed

    def test_warning_is_amber_not_the_sky_accent(self):
        """Regression: warning is the amber verdict colour, never MB_ACCENT (RC1)."""
        assert verdict_visuals(Verdict.WARNING).color == "#B45309"
        assert verdict_visuals(Verdict.WARNING).color != tokens.MB_ACCENT


class TestVerdictVisualIsFrozen:
    def test_dataclass_is_immutable(self):
        visual = verdict_visuals(Verdict.HEALTHY)
        with pytest.raises(Exception):  # noqa: B017 - frozen dataclass raises FrozenInstanceError
            visual.color = "#000000"  # type: ignore[misc]
