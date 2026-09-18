"""Plan 0046 C — the wiring the type checker cannot reach, plus the A9 limitation note.

Two separate jobs in one file, both about SUPPLY rather than logic:

1. **Every ``probe_schedule`` call site supplies ``foreign_account``.** ``mypy`` excludes
   ``src/ui_flet`` and every screen wraps its probe in ``contextlib.suppress(Exception)`` /
   ``page.run_thread``, so a forgotten REQUIRED keyword-only argument would raise a ``TypeError``
   inside a worker thread, be swallowed whole, and silently kill the entire schedule probe on that
   surface — the badge, the readout and the verdict all going quiet with no trace. Nothing else in
   the stack can catch that, so it is caught here, statically, over the real source.
2. **``sync_window_foreign_note``** — the A9 limitation, surfaced not solved.

Plan 0049 S-2a.1 adds ``shared_records`` to both supply contracts, and the extension is MANDATORY
rather than tidy: these sweeps check keyword PRESENCE, so a second required keyword-only argument
is invisible to them. Adding ``shared_records`` without adding a parallel assertion would leave
the new argument protected by nothing at all — the exact failure mode item 1 exists to prevent,
reintroduced by the change that quotes it. Each fact gets its own assertion so a call site that
supplies one and forgets the other still fails.

The AST walk is deliberate: a substring grep would pass on a call that merely MENTIONS the kwarg
in a neighbouring comment, which is exactly the shape a careless edit leaves behind.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.config.app_config import AppConfig
from src.ui_flet.screens.setup import (
    SYNC_WINDOW_FOREIGN_NOTE,
    sync_window_foreign_note,
)

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "ui_flet"

#: Every module that probes the schedule. Named EXPLICITLY rather than discovered, so deleting a
#: call site is a visible test edit rather than a silently-shrinking sweep.
_PROBE_CALL_SITES: tuple[str, ...] = (
    "screens/home.py",
    "screens/run_history.py",
    "screens/setup.py",
    "screens/mapping.py",
    "shell.py",
)

_FOREIGN = "CONTOSO\\svc_districtsync"


def _probe_calls(path: pathlib.Path) -> list[ast.Call]:
    """Every ``probe_schedule(...)`` call in a module, as AST nodes."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "probe_schedule"
    ]


class TestEveryProbeCallSiteSuppliesTheAccount:
    @pytest.mark.parametrize("relative", _PROBE_CALL_SITES, ids=_PROBE_CALL_SITES)
    def test_the_call_site_exists_and_passes_foreign_account(self, relative: str) -> None:
        calls = _probe_calls(_SRC / relative)
        assert calls, f"{relative} no longer calls probe_schedule — update _PROBE_CALL_SITES"
        for call in calls:
            supplied = {kw.arg for kw in call.keywords}
            assert "foreign_account" in supplied, (
                f"{relative}:{call.lineno} calls probe_schedule without foreign_account — a "
                "TypeError there is swallowed by the screen's own suppress() and kills the probe"
            )

    @pytest.mark.parametrize("relative", _PROBE_CALL_SITES, ids=_PROBE_CALL_SITES)
    def test_the_call_site_also_passes_shared_records(self, relative: str) -> None:
        """Plan 0049 S-2a.1, and a SEPARATE assertion on purpose.

        The test above would pass unchanged on a call site that supplies ``foreign_account`` and
        forgets ``shared_records`` — the sweep reads keyword NAMES, so the second required
        keyword-only argument is invisible to it. The consequence is identical and identically
        silent: a ``TypeError`` raised inside the screen's own worker thread, swallowed by its
        ``suppress(Exception)``, taking the whole schedule probe down with it.
        """
        calls = _probe_calls(_SRC / relative)
        assert calls, f"{relative} no longer calls probe_schedule — update _PROBE_CALL_SITES"
        for call in calls:
            supplied = {kw.arg for kw in call.keywords}
            assert "shared_records" in supplied, (
                f"{relative}:{call.lineno} calls probe_schedule without shared_records — a "
                "TypeError there is swallowed by the screen's own suppress() and kills the probe"
            )

    @pytest.mark.parametrize("relative", _PROBE_CALL_SITES, ids=_PROBE_CALL_SITES)
    def test_it_resolves_through_the_one_sanctioned_resolver(self, relative: str) -> None:
        """``foreign_task_account`` fails to ``""`` on everything; a hand-rolled comparison
        against ``AppConfig.schedule_run_as_user`` would not, and would read ONE facet of an
        atomic triple."""
        source = (_SRC / relative).read_text(encoding="utf-8")
        assert "foreign_task_account" in source

    @pytest.mark.parametrize("relative", _PROBE_CALL_SITES, ids=_PROBE_CALL_SITES)
    def test_the_shared_fact_comes_from_the_one_pinned_predicate(self, relative: str) -> None:
        """``paths.is_machine_scope()`` is THE predicate — pinned once per process at both entry
        points. A screen that re-derived the scope (an env read, a directory comparison) could
        answer differently from the store selection and the log sink, which is the split-brain
        plan 0049 exists to remove."""
        source = (_SRC / relative).read_text(encoding="utf-8")
        assert "is_machine_scope" in source

    def test_the_sweep_itself_is_not_vacuous(self) -> None:
        """Positive twin for the AST walk: it really does see a missing kwarg."""
        tree = ast.parse("probe_schedule(name, hint_registered=True)")
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        assert "foreign_account" not in {kw.arg for kw in call.keywords}

    def test_the_shared_records_sweep_is_not_vacuous_either(self) -> None:
        """Positive twin for the 0049 assertion, and the sharper half of it: a call carrying
        ``foreign_account`` but NOT ``shared_records`` — the exact half-done edit the parallel
        assertion exists to catch — must be seen as missing."""
        tree = ast.parse("probe_schedule(name, hint_registered=True, foreign_account=acct)")
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        supplied = {kw.arg for kw in call.keywords}
        assert "foreign_account" in supplied
        assert "shared_records" not in supplied


def _sync_window_paused_calls(relative: str) -> list[ast.Call]:
    """Every ``sync_window_paused(...)`` call in a module, as AST nodes."""
    tree = ast.parse((_SRC / relative).read_text(encoding="utf-8"), filename=relative)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sync_window_paused"
    ]


class TestSyncWindowPausedCallSitesAreForeignAware:
    """The A9 fix is single-sourced in ``sync_window_paused``, but each VIEW caller must pass the
    account it has — a forgotten one silently restores the false green."""

    @pytest.mark.parametrize("relative", ["screens/home.py", "shell.py"], ids=["home", "shell"])
    def test_the_view_callers_pass_foreign_account(self, relative: str) -> None:
        calls = _sync_window_paused_calls(relative)
        assert calls, f"{relative} no longer calls sync_window_paused"
        for call in calls:
            assert "foreign_account" in {kw.arg for kw in call.keywords}, (
                f"{relative}:{call.lineno} would render a seasonal pause that is not in force"
            )

    @pytest.mark.parametrize("relative", ["screens/home.py", "shell.py"], ids=["home", "shell"])
    def test_the_view_callers_also_pass_shared_records(self, relative: str) -> None:
        """Plan 0049 S-2a.1 — the mirror-image false report, and the reason this needs its own
        assertion rather than trusting the one above.

        Without the fact, a machine-scoped install is told every summer night that a nightly sync
        it deliberately paused failed to arrive. These two callers are not wrapped in a
        ``suppress`` like the probe is, but they ARE inside ``page.run_thread`` workers, so a
        ``TypeError`` here takes the Setup badge and Home's schedule card with it.
        """
        calls = _sync_window_paused_calls(relative)
        assert calls, f"{relative} no longer calls sync_window_paused"
        for call in calls:
            assert "shared_records" in {kw.arg for kw in call.keywords}, (
                f"{relative}:{call.lineno} would suppress a seasonal pause that IS in force"
            )

    def test_this_sweep_is_not_vacuous_either(self) -> None:
        """Positive twin: the half-done edit — the account supplied, the scope forgotten — is
        seen as missing."""
        tree = ast.parse("sync_window_paused(cfg, now=None, foreign_account=acct)")
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        supplied = {kw.arg for kw in call.keywords}
        assert "foreign_account" in supplied
        assert "shared_records" not in supplied


class TestSyncWindowForeignNote:
    """A9 — stated only when it is BOTH enabled here AND unenforceable there."""

    def test_it_appears_only_for_an_enabled_window_on_a_foreign_principal(self) -> None:
        cfg = AppConfig(sync_window_enabled=True, sync_window_start="08-11", sync_window_end="07-06")
        note = sync_window_foreign_note(cfg, foreign_account=_FOREIGN)
        assert note is not None
        assert _FOREIGN in note

    @pytest.mark.parametrize(
        ("enabled", "account"),
        [(True, ""), (False, _FOREIGN), (False, "")],
        ids=["enabled-same-account", "disabled-foreign", "disabled-same-account"],
    )
    def test_every_other_combination_is_silent(self, enabled: bool, account: str) -> None:
        cfg = AppConfig(sync_window_enabled=enabled, sync_window_start="08-11", sync_window_end="07-06")
        assert sync_window_foreign_note(cfg, foreign_account=account) is None

    def test_it_names_the_one_remedy_that_actually_works_today(self) -> None:
        assert "remove the nightly schedule" in SYNC_WINDOW_FOREIGN_NOTE

    def test_it_never_implies_districtsync_enforces_the_pause(self) -> None:
        """It states a LIMITATION. Nothing here may suggest the app handles it, or that a
        policy-blocked / foreign-principal sync is fixable inside DistrictSync."""
        assert "won't apply" in SYNC_WINDOW_FOREIGN_NOTE
        for forbidden in ("we pause", "DistrictSync pauses", "will pause", "automatically"):
            assert forbidden not in SYNC_WINDOW_FOREIGN_NOTE

    def test_the_note_is_rendered_by_the_schedule_section(self) -> None:
        source = (_SRC / "screens" / "setup.py").read_text(encoding="utf-8")
        assert "window_foreign_slot" in source
        assert "_paint_window_foreign_note" in source
