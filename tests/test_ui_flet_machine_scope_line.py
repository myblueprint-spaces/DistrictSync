"""Plan 0049 S-2a.4 — "whose settings are these?", and the registry seam behind it.

Two pure pieces, one question. ``home_status.machine_scope_line`` decides the sentence;
``diagnostics.machine_scope_provenance`` supplies the two values it may name. Neither reads the
registry in these tests: the line takes its inputs as arguments, and the provenance reader is
exercised through the ``read_hklm_values`` seam.

**The governing rule is the ``None``.** On all 20 districts in the field the scope line does not
render at all. "Settings for your account only" answers a question none of them asked, lands on
the surface S7 deliberately stripped back to the verdict, and breaks this slice's own byte-identity
promise — so ``None`` is not a degraded case here, it is the ONLY correct answer everywhere today.
That is also why ``machine_scope`` is a parameter rather than something the two view sites branch
on themselves: one rule, two renderers, no drift.

What the sentence is FOR is the second administrator. A colleague provisioned this computer, and
the app their district described now behaves differently for reasons nothing else on screen
explains. The provenance form names who to ask, and the date they can be asked about.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.ui_flet.home_status import (
    MACHINE_SCOPE_LINE_PLAIN,
    MACHINE_SCOPE_LINE_WITH_PROVENANCE,
    machine_scope_line,
)
from src.utils import diagnostics

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "ui_flet"

_WHO = "CORP\\admin"
_WHEN = "2026-09-18T10:00:00"


class TestThePerUserInstallSeesNothing:
    """The byte-identity promise, stated as the first thing in the file because it is the whole
    shipped behaviour: every install that exists today takes this branch."""

    @pytest.mark.parametrize(
        ("provisioned_by", "provisioned_at"),
        [(_WHO, _WHEN), ("", ""), (_WHO, ""), ("", _WHEN), ("  ", "garbage")],
        ids=["both", "neither", "who-only", "when-only", "junk"],
    )
    def test_it_is_none_whatever_the_registry_happens_to_hold(self, provisioned_by: str, provisioned_at: str) -> None:
        """A stale HKLM key left behind by an uninstall, or a hand-written one, must not make a
        per-user install claim its settings are shared. The scope decides, nothing else."""
        assert (
            machine_scope_line(machine_scope=False, provisioned_by=provisioned_by, provisioned_at=provisioned_at)
            is None
        )

    def test_positive_twin_the_same_inputs_do_render_on_a_shared_install(self) -> None:
        """Without this the ``None`` above would pass on a function that returns ``None`` always."""
        line = machine_scope_line(machine_scope=True, provisioned_by=_WHO, provisioned_at=_WHEN)
        assert line is not None


class TestTheProvenanceForm:
    """Both values present → the sentence that names who and when."""

    def test_it_names_the_account_and_a_plain_calendar_date(self) -> None:
        line = machine_scope_line(machine_scope=True, provisioned_by=_WHO, provisioned_at=_WHEN)
        assert line == MACHINE_SCOPE_LINE_WITH_PROVENANCE.format(who=_WHO, when="Sep 18, 2026")

    def test_the_date_is_never_the_raw_iso_string(self) -> None:
        """The value comes off a registry key as an ISO stamp. Printing it verbatim on the app's
        calmest surface would be the one place DistrictSync speaks machine at an admin."""
        line = machine_scope_line(machine_scope=True, provisioned_by=_WHO, provisioned_at=_WHEN)
        assert line is not None
        assert _WHEN not in line
        assert "T10:00" not in line

    def test_surrounding_whitespace_in_the_name_is_trimmed(self) -> None:
        line = machine_scope_line(machine_scope=True, provisioned_by=f"  {_WHO}  ", provisioned_at=_WHEN)
        assert line == MACHINE_SCOPE_LINE_WITH_PROVENANCE.format(who=_WHO, when="Sep 18, 2026")

    def test_the_two_forms_are_different_sentences(self) -> None:
        """Positive twin for the degradation tests below: there really are two outcomes, so a
        "falls back to plain" assertion is not passing on two identical strings."""
        assert MACHINE_SCOPE_LINE_WITH_PROVENANCE != MACHINE_SCOPE_LINE_PLAIN


class TestTheDegradedFormNeverRendersHalfASentence:
    """The key is hand-editable and the elevated writer is a separate process. "Set up by  on
    Sep 18, 2026" is worse than the fact alone — so either value missing drops BOTH, and the
    remaining sentence still states the scope, which is the part the reader actually needs.
    """

    @pytest.mark.parametrize(
        ("provisioned_by", "provisioned_at"),
        [
            ("", _WHEN),
            ("   ", _WHEN),
            (_WHO, ""),
            (_WHO, "   "),
            (_WHO, "not-a-date"),
            (_WHO, "18/09/2026"),
            ("", ""),
        ],
        ids=[
            "no-name",
            "blank-name",
            "no-date",
            "blank-date",
            "unparseable-date",
            "non-iso-date",
            "neither",
        ],
    )
    def test_it_falls_back_to_the_plain_form(self, provisioned_by: str, provisioned_at: str) -> None:
        line = machine_scope_line(machine_scope=True, provisioned_by=provisioned_by, provisioned_at=provisioned_at)
        assert line == MACHINE_SCOPE_LINE_PLAIN

    def test_the_plain_form_still_states_the_scope(self) -> None:
        """Losing the provenance may not lose the ANSWER — a second administrator reading a
        shared install needs to know it is shared even when nobody recorded who shared it."""
        assert "Shared settings on this computer" in MACHINE_SCOPE_LINE_PLAIN

    def test_the_plain_form_claims_no_provenance(self) -> None:
        assert "set up by" not in MACHINE_SCOPE_LINE_PLAIN

    @pytest.mark.parametrize(
        ("provisioned_by", "provisioned_at"),
        [("", _WHEN), (_WHO, "not-a-date"), ("", "")],
        ids=["no-name", "unparseable-date", "neither"],
    )
    def test_no_rendered_line_ever_carries_an_empty_slot(self, provisioned_by: str, provisioned_at: str) -> None:
        """The concrete shape of the failure this branch exists to prevent."""
        line = machine_scope_line(machine_scope=True, provisioned_by=provisioned_by, provisioned_at=provisioned_at)
        assert line is not None
        assert "by  on" not in line
        assert "on ." not in line
        assert "{" not in line and "}" not in line


class TestMachineScopeProvenance:
    """The registry seam. TOTAL by contract — every failure reduces to ``("", "")``.

    Routed through :func:`read_hklm_values` rather than a second ``winreg`` call so the value
    NAMES are spelled exactly once in the process: a reader that invented its own spelling would
    render the degraded form forever and nothing would notice, because the degraded form is also
    the correct answer on every install that has no key at all.
    """

    def test_it_returns_provisioned_by_first_then_provisioned_at(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ORDER is the contract, and it is load-bearing: both call sites unpack it as
        ``provisioned_by, provisioned_at`` and hand the pair straight to ``machine_scope_line``.
        Swapped, the name lands where the date belongs, ``friendly_absolute_date`` refuses to
        parse an account name, and the provenance form can never render on any install.
        """
        monkeypatch.setattr(
            diagnostics,
            "read_hklm_values",
            lambda: {"MachineScope": 1, "ProvisionedAt": _WHEN, "ProvisionedBy": _WHO},
        )
        assert diagnostics.machine_scope_provenance() == (_WHO, _WHEN)

    def test_an_absent_key_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every install in the field today, plus every non-Windows host."""

        def _missing() -> dict[str, object]:
            raise FileNotFoundError("the machine-scope key is Windows-only")

        monkeypatch.setattr(diagnostics, "read_hklm_values", _missing)
        assert diagnostics.machine_scope_provenance() == ("", "")

    def test_an_unreadable_key_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A real read failure — a filtered token, a policy-locked hive. Advisory copy may never
        trap an admin, so it degrades rather than raising into a view build."""

        def _denied() -> dict[str, object]:
            raise PermissionError("denied")

        monkeypatch.setattr(diagnostics, "read_hklm_values", _denied)
        assert diagnostics.machine_scope_provenance() == ("", "")

    @pytest.mark.parametrize(
        "values",
        [
            {"MachineScope": 1},
            {"ProvisionedBy": _WHO},
            {"ProvisionedAt": _WHEN},
            {"ProvisionedBy": 7, "ProvisionedAt": _WHEN},
            {"ProvisionedBy": _WHO, "ProvisionedAt": None},
            {"ProvisionedBy": "   ", "ProvisionedAt": "   "},
        ],
        ids=["switch-only", "name-only", "date-only", "non-string-name", "non-string-date", "blank-both"],
    )
    def test_a_partial_or_mistyped_key_degrades(
        self, monkeypatch: pytest.MonkeyPatch, values: dict[str, object]
    ) -> None:
        """A REG_DWORD written where a REG_SZ belongs is a hand-edit, not a crash — and a half
        answer is exactly what the plain form exists to replace, so an empty slot on either side
        must reach the line as ``""``."""
        monkeypatch.setattr(diagnostics, "read_hklm_values", lambda: values)
        by, at = diagnostics.machine_scope_provenance()
        assert "" in (by, at)

    def test_it_never_raises_whatever_the_key_holds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Totality, swept: this is called during a view build with no floor beneath it."""
        for values in ({}, {"ProvisionedBy": b"bytes"}, {"ProvisionedAt": 0}, {"ProvisionedBy": [], "x": 1}):
            monkeypatch.setattr(diagnostics, "read_hklm_values", lambda v=values: v)
            assert isinstance(diagnostics.machine_scope_provenance(), tuple)


class TestTheSeamAndTheLineComposeIntoTheRenderedSentence:
    """The two halves are only useful joined, and the join is where an ordering mistake hides:
    each half is individually self-consistent, and only the composed sentence is wrong."""

    def test_a_fully_provisioned_registry_produces_the_provenance_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            diagnostics,
            "read_hklm_values",
            lambda: {"MachineScope": 1, "ProvisionedAt": _WHEN, "ProvisionedBy": _WHO},
        )
        provisioned_by, provisioned_at = diagnostics.machine_scope_provenance()
        line = machine_scope_line(machine_scope=True, provisioned_by=provisioned_by, provisioned_at=provisioned_at)
        assert line == MACHINE_SCOPE_LINE_WITH_PROVENANCE.format(who=_WHO, when="Sep 18, 2026")

    def test_an_absent_registry_key_produces_the_plain_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The degraded twin: a machine-scoped install whose key lost its display values still
        gets a true sentence, never a broken one."""

        def _missing() -> dict[str, object]:
            raise FileNotFoundError("absent")

        monkeypatch.setattr(diagnostics, "read_hklm_values", _missing)
        provisioned_by, provisioned_at = diagnostics.machine_scope_provenance()
        line = machine_scope_line(machine_scope=True, provisioned_by=provisioned_by, provisioned_at=provisioned_at)
        assert line == MACHINE_SCOPE_LINE_PLAIN


def _calls(relative: str, name: str) -> list[ast.Call]:
    tree = ast.parse((_SRC / relative).read_text(encoding="utf-8"), filename=relative)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


class TestBothRenderersGoThroughTheOneRule:
    """Home and Settings render the same sentence. A second site that branched on the scope
    itself, or formatted its own string, is how "shared settings" and "set up by X" start
    disagreeing on one computer — which is precisely the confusion this line exists to end.
    """

    @pytest.mark.parametrize("relative", ["screens/home.py", "screens/setup.py"], ids=["home", "settings"])
    def test_the_renderer_calls_the_shared_rule(self, relative: str) -> None:
        calls = _calls(relative, "machine_scope_line")
        assert calls, f"{relative} no longer renders the scope line"
        for call in calls:
            assert "machine_scope" in {kw.arg for kw in call.keywords}

    @pytest.mark.parametrize("relative", ["screens/home.py", "screens/setup.py"], ids=["home", "settings"])
    def test_the_scope_comes_from_the_one_pinned_predicate(self, relative: str) -> None:
        """``paths.is_machine_scope()`` is THE predicate — decided and pinned once per process.
        A view that re-derived the scope could disagree with the store selection and the log
        sink, which is the split-brain plan 0049 exists to remove."""
        source = (_SRC / relative).read_text(encoding="utf-8")
        assert "is_machine_scope" in source

    @pytest.mark.parametrize("relative", ["screens/home.py", "screens/setup.py"], ids=["home", "settings"])
    def test_the_renderer_does_not_respell_either_sentence(self, relative: str) -> None:
        """Single source of truth: the copy lives in ``home_status``, and a screen that inlined
        it would drift the moment the wording is reviewed."""
        source = (_SRC / relative).read_text(encoding="utf-8")
        assert "Shared settings on this computer" not in source
