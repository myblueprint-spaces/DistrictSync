"""Pure trust copy + state for the Setup SFTP "Test connection" flow (COUNTED, no flet import).

Slice 7 (D6): a successful Test must name WHAT it verified — which host, as which
user, with which credential source — and it must NEVER assert the nightly sync *can
deliver* for settings that aren't saved yet (that is a future outcome the test never
checked). This trust decision lives here as a unit-tested pure helper instead of in
the coverage-omitted view glue (``screens/setup.py``), mirroring how ``setup_gates``
single-sources the pure submit-gate predicates.

Three pure functions, no I/O:
  - ``sftp_form_differs_from_saved`` — did the admin edit host/user/port/remote away
    from the persisted ``AppConfig``? (drives the "unsaved" softening).
  - ``sftp_test_copy`` — the (headline, detail) for a successful Test, honest about
    provenance (stored vs typed credential) and about unsaved edits.
  - ``parse_port`` — the total form-port parse (blank → 22, unparseable → ``None``),
    the seam that lets the view show the fixed ``PORT_ERROR_*`` copy for a port typo
    instead of misreporting it as a host-allowlist failure.
"""

from __future__ import annotations

from typing import Literal

from src.config.app_config import AppConfig

# The credential the Test authenticated with: the OS-keyring one (blank password field)
# vs the one the admin just typed into the form (not yet persisted).
Provenance = Literal["stored", "typed"]

_HEADLINE = "SFTP connection succeeded"

# Present-tense, test-scoped softener for unsaved settings — NEVER promises the nightly
# sync can deliver for values that aren't in the saved config yet.
_UNSAVED_TAIL = "These settings work — click Save to use them for the nightly sync."

# Appended when auth worked but the account can't list the remote folder (upload-only
# delivery accounts). DUPLICATED here — not imported — to keep this module pure: importing
# ``src.sftp.uploader`` would drag paramiko into this module's import graph. This mirrors the
# TAIL of ``uploader.LISTING_DENIED_NOTE`` (which prefixes "Connected and signed in. ");
# the view-layer equality check against the full ``LISTING_DENIED_NOTE`` lives in
# ``screens/setup._show_result``, not here.
_LISTING_DENIED_TAIL = "This account can't list the remote folder — that's normal for upload-only delivery accounts."

# The FIXED inline error for a non-numeric port on Test/Save — distinct from the host-allowlist
# error (a port typo used to fall into the same ``except ValueError`` and misreport as "That SFTP
# host isn't allowed"). Single-sourced here (the pure copy layer) so both view sites can't drift.
PORT_ERROR_HEADLINE = "That port isn't a number"
PORT_ERROR_DETAIL = "The port must be a number — SpacesEDU delivery usually uses 22."


def parse_port(port: str, default: int = 22) -> int | None:
    """Best-effort int() of a form port string; None (never a raise) when unparseable.

    Blank falls back to ``default`` (22 — the standard SFTP port). The view's Test/Save
    handlers use this as their pre-parse seam so a port typo gets the fixed port error
    above rather than misreporting as a host-allowlist failure.
    """
    try:
        return int((port or "").strip() or default)
    except (TypeError, ValueError):
        return None


def sftp_form_differs_from_saved(
    cfg: AppConfig,
    *,
    host: str,
    username: str,
    remote_path: str,
    port: str,
) -> bool:
    """True when any of host/user/remote-path/port in the form differ from the SAVED config.

    Strings are compared stripped; the port is normalized to int (form ports arrive as
    strings, e.g. ``"22"`` vs the saved int ``22``). An unparseable port can never equal
    the saved int, so it reads as a difference (the softer, safer copy). The password is
    NOT part of this comparison — it never lives in ``AppConfig``.
    """

    def _norm(value: str) -> str:
        return (value or "").strip()

    if _norm(host) != _norm(cfg.sftp_host):
        return True
    if _norm(username) != _norm(cfg.sftp_username):
        return True
    if _norm(remote_path) != _norm(cfg.sftp_remote_path):
        return True
    return parse_port(port) != cfg.sftp_port


def sftp_test_copy(
    *,
    provenance: Provenance,
    unsaved_edits: bool,
    host: str,
    username: str,
    listing_denied: bool = False,
) -> tuple[str, str]:
    """The (headline, detail) for a SUCCESSFUL Test — provenance- and unsaved-honest.

    - ``stored`` credential: names the credential manager as the source.
    - ``typed`` credential: names it as just-entered AND prompts "Save to keep it"
      (a typed password is not persisted until Save, so the nightly would still use
      the old stored one).
    - ``unsaved_edits``: appends the test-scoped "these settings work — Save to use
      them" softener; the copy NEVER claims the nightly sync can deliver for values
      that aren't saved.
    - ``listing_denied``: auth worked but the account can't LIST the remote folder
      (upload-only delivery accounts) — appends the fixed reassurance LAST (after the
      unsaved softener). Delivery uses ``put``, not ``list``, so this is still a success.

    The only state that makes no Save prompt is stored-credential + saved-settings —
    the one case where exactly-this is already what the nightly sync uses.
    """
    connected = f"Connected to {host} as {username} "
    if provenance == "typed":
        detail = connected + "using the password you just entered — click Save to keep it."
    else:
        detail = connected + "using the password saved in this computer's credential manager."

    if unsaved_edits:
        detail = f"{detail} {_UNSAVED_TAIL}"

    if listing_denied:
        detail = f"{detail} {_LISTING_DENIED_TAIL}"

    return _HEADLINE, detail
