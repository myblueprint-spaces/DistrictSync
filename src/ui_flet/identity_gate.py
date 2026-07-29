"""``needs_identity`` — should the launch page ASK who looks after this sync?

PURE + COUNTED (no ``flet`` import), and deliberately shaped like ``nav.needs_setup``:
one predicate, one ``AppConfig``, one boolean, guarded by the same
``settings_unreadable()`` honesty check so the two launch gates cannot drift apart.

**NO CALLER YET.** S4a mounts the launch page and calls this; today nothing does, and no
picker is scoped by anything. The design statements below describe what this predicate
WILL gate — they are the contract S4a must build to, not a description of current
behaviour.

**What that will be, and what it is emphatically not.** The launch page is
IDENTIFICATION — it will scope a district list so the highest-consequence wrong click in
the product (picking the wrong district, which ships a wrong roster) is harder to make. It
is NOT authentication: there are no accounts, nothing is unlocked, every mapping ships in
the executable regardless, and every path — a match, no match, a typo, a skip, a crash in
the identity layer itself — must lead INTO the app. So this predicate can only ever make
the app ask a question; it can never withhold anything, and it must never be able to fail
closed.
"""

from __future__ import annotations

from src.config.app_config import AppConfig

__all__ = ["needs_identity"]


def needs_identity(app_config: AppConfig) -> bool:
    """True when the launch page should ask for the admin's work email.

    Three conditions, all of which must hold — each one a state in which asking would be
    the wrong thing to do if it were absent:

    * **the settings file is readable.** Under ``settings_unreadable()`` we could not
      persist the answer (``AppConfig.save`` refuses a settings-free write, and
      ``identity_save`` re-checks at write time), so asking would put a question in front
      of an admin whose answer we would silently drop. G2: no gate, no card, no prompt.
    * **setup is not finished.** A working install is never stopped at a launch page in
      front of its own sync — it gets the dismissible Home card instead (S4b).
    * **no identity is on file.** Asked and answered; a whitespace-only stored value is
      NOT an answer.

    Note what is deliberately absent: no attempt counter, no lockout, no expiry, no
    network check. Each absence is part of the register — this is a question, not a door.
    """
    return (
        not app_config.settings_unreadable()
        and not app_config.has_completed_setup()
        and not app_config.identity_email.strip()
    )
