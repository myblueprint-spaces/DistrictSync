"""``needs_identity`` — the launch-page predicate truth table (plan 0038 S3).

The identity page is IDENTIFICATION for list-scoping, never a gate that can fail closed:
there are no accounts, nothing is unlocked, and every path leads INTO the app. This
predicate therefore only ever answers "is it worth ASKING right now?" — and it answers
False in every state where asking would be wrong:

* UNREADABLE settings (G2) — we cannot persist an answer, so asking would trap the admin
  in a question we can't record;
* an install that already finished setup — it gets the dismissible Home card (S4b), never
  a launch page in front of a working sync;
* an identity already on file — asked and answered.

Mirrors ``nav.needs_setup``'s shape deliberately (same module family, same
``settings_unreadable()`` guard), so the two gates cannot drift apart.
"""

from __future__ import annotations

import itertools

import pytest

from src.config.app_config import AppConfig, ConfigLoadState
from src.ui_flet.identity_gate import needs_identity
from src.ui_flet.nav import needs_setup


def _cfg(
    *,
    load_state: ConfigLoadState = ConfigLoadState.LOADED,
    setup_completed: bool = False,
    identity_email: str = "",
) -> AppConfig:
    return AppConfig(
        input_dir="/in",
        output_dir="/out",
        sis_type="sd48myedbc",
        setup_completed=setup_completed,
        identity_email=identity_email,
        load_state=load_state,
    )


# --------------------------------------------------------------------------- #
# The truth table — all three inputs, both values each                          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("unreadable", "completed", "email", "expected", "why"),
    [
        (False, False, "", True, "the only True row: readable, unfinished setup, no identity"),
        (False, False, "admin@sd48.bc.ca", False, "already answered"),
        (False, False, "   ", True, "a whitespace-only stored value is NOT an answer"),
        (False, True, "", False, "a configured install gets the Home card, never a launch gate"),
        (False, True, "admin@sd48.bc.ca", False, "configured and answered"),
        (True, False, "", False, "G2 — UNREADABLE can never be asked (the answer could not be saved)"),
        (True, True, "", False, "UNREADABLE outranks everything"),
        (True, False, "admin@sd48.bc.ca", False, "UNREADABLE outranks a stored value too"),
    ],
)
def test_needs_identity_truth_table(unreadable, completed, email, expected, why):
    cfg = _cfg(
        load_state=ConfigLoadState.UNREADABLE if unreadable else ConfigLoadState.LOADED,
        setup_completed=completed,
        identity_email=email,
    )
    assert needs_identity(cfg) is expected, why


def test_absent_config_a_genuinely_fresh_install_is_asked():
    """ABSENT (no ``config.json`` at all) is the archetypal first launch — ask."""
    assert needs_identity(AppConfig(load_state=ConfigLoadState.ABSENT)) is True


def test_unreadable_never_asks_across_every_other_combination():
    """G2 as a SWEEP, not a sample: UNREADABLE ⟹ False for every other input combination."""
    for completed, email in itertools.product((False, True), ("", "   ", "admin@sd48.bc.ca")):
        cfg = _cfg(load_state=ConfigLoadState.UNREADABLE, setup_completed=completed, identity_email=email)
        assert needs_identity(cfg) is False, f"UNREADABLE asked with {completed=} {email=}"


def test_identity_alone_never_completes_setup():
    """Storing an identity must not make an unconfigured install look configured.

    ``identity_*`` is advisory metadata about WHO is looking after the sync; it is not a
    setup step. If it leaked into the finish-line, an admin who typed an email at the
    launch page would land on a dashboard for a sync that was never configured.
    """
    cfg = AppConfig(identity_email="admin@sd48.bc.ca", identity_sd_number="48", load_state=ConfigLoadState.LOADED)

    assert cfg.has_completed_setup() is False
    assert cfg.is_complete() is False
    assert needs_setup(cfg) is True


def test_the_two_gates_agree_on_the_unreadable_row():
    """``needs_identity`` and ``needs_setup`` share the ``settings_unreadable()`` guard.

    Both suppress under UNREADABLE for the same reason (we know this is not a fresh
    install, and we cannot record an answer). Pinned so a future edit to one is a visible
    divergence from the other rather than a silent one.
    """
    cfg = _cfg(load_state=ConfigLoadState.UNREADABLE)

    assert needs_identity(cfg) is False
    assert needs_setup(cfg) is False


def test_module_imports_no_flet():
    """PURE + COUNTED: the gate is importable with no UI toolkit in the graph."""
    import ast
    import inspect

    import src.ui_flet.identity_gate as module

    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "flet" not in imported
