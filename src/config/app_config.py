"""Runtime application configuration (non-sensitive settings only).

Stores the partner's setup wizard choices to disk as ``config.json`` under the
per-user app-data directory (``paths.user_data_dir()`` — the platform-standard
location: ``%LOCALAPPDATA%\\DistrictSync`` / ``~/Library/Application Support/DistrictSync``
/ ``$XDG_DATA_HOME/DistrictSync``). SFTP passwords are NOT stored here — they are
stored in the OS credential store via the ``keyring`` library.

The config path is resolved through ``paths.user_data_dir()`` at CALL time (not an
import-time constant) so it flows through the single app-data seam: the test
isolation fixture can redirect it, and the app-data location (incl. the one-time
legacy relocation) is owned entirely by ``paths.py`` — the single source of truth.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from src.utils import paths

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.json"


def config_file_path() -> Path:
    """Resolve the ``config.json`` path at call time, through the single paths seam."""
    return paths.user_data_dir() / CONFIG_FILENAME


@dataclass
class AppConfig:
    """Partner-configured runtime settings."""

    # ETL paths
    input_dir: str = ""
    output_dir: str = ""
    # No district is pre-selected (D9, Slice 8): a fresh install starts with an empty
    # district so the Setup wizard's District step shows the "Choose your district"
    # placeholder and the admin picks explicitly — never a silent "myedbc" default that
    # a district might not notice is wrong. is_complete()/setup_state gate on this being
    # non-blank, so an empty sis_type can never reach run_pipeline via the UI. The CLI is
    # unaffected (--sis is required there, never defaulted from AppConfig).
    sis_type: str = ""

    # Scheduling
    schedule_time: str = "03:00"  # HH:MM (24-hour)
    schedule_task_name: str = "DistrictSync_Daily"
    schedule_registered: bool = False
    # The durable "what was ACTUALLY registered" facts (plan 0034 Slice 3) — written ONLY on a
    # confirmed successful register (and cleared on a confirmed unregister), never inferred:
    # ``schedule_unattended`` records whether the task was registered WITH a Windows password
    # (LogonType Password — runs while signed out), so a Settings-Save re-register can never
    # silently downgrade it to logged-on-only without the admin's explicit choice. NEVER a
    # password — a boolean fact only (the I1/I3 password contract is untouched).
    # ``schedule_task_args`` records the task-baked args (input/output/district/sftp/run time)
    # the live task actually carries, so the Settings reconcile compares against reality rather
    # than a mount-time snapshot (a Mapping district switch + no-edit Save must re-register).
    # Both are additive with defaults — old config.json files load unchanged (back-compat).
    schedule_unattended: bool = False
    schedule_task_args: dict[str, object] | None = None

    # Onboarding (D4a): the durable "reached the setup finish line at least once" fact,
    # kept DISTINCT from the schedule's live-ness (which is read back from the OS, never
    # trusted from a flag). Set explicitly by the wizard's finish line in Slice 8; until
    # then it is inferred on load from the old finish-line condition (see load()).
    setup_completed: bool = False

    # SFTP (non-sensitive only)
    sftp_enabled: bool = False
    sftp_host: str = ""
    sftp_port: int = 22
    sftp_username: str = ""
    sftp_remote_path: str = "/files"

    # Window geometry (0032 T2 #8): the last-seen window bounds, persisted on exit by the
    # Flet shell and restored CLAMPED to the current work area at the next launch — the
    # saved values are never trusted raw (see ``src/ui_flet/geometry.py``: an off-screen
    # position is pulled back so the title bar is always reachable). Additive with safe
    # defaults so old config.json files load unchanged; ``None`` = "never saved".
    window_width: float | None = None
    window_height: float | None = None
    window_left: float | None = None
    window_top: float | None = None
    window_maximized: bool = False

    @classmethod
    def load(cls) -> AppConfig:
        """Load config from disk, returning defaults if the file doesn't exist."""
        config_file = config_file_path()
        if not config_file.exists():
            return cls()
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
            # Only pass known fields to avoid errors on old config files
            known = {f for f in cls.__dataclass_fields__}
            filtered = {k: v for k, v in data.items() if k in known}
            cfg = cls(**filtered)
            # Back-compat inference (D4a): bake the durable finish-line fact through the
            # single-source derivation so an install predating the flag (complete config +
            # a registered schedule = the OLD finish line) is never dropped back into
            # first-run onboarding after this update. An explicitly-persisted True is kept.
            cfg.setup_completed = cfg.has_completed_setup()
            return cfg
        except Exception as exc:
            logger.warning(f"Could not read app config ({exc}); using defaults")
            return cls()

    def save(self) -> None:
        """Persist config to disk (creates parent directory if needed)."""
        config_file = config_file_path()
        config_dir = config_file.parent
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file.write_text(
            json.dumps(asdict(self), indent=2),
            encoding="utf-8",
        )
        # Restrict permissions on Unix (config contains SFTP host/username)
        if sys.platform != "win32":
            try:
                os.chmod(config_dir, 0o700)
                os.chmod(config_file, 0o600)
            except OSError:
                pass
        logger.info(f"App config saved to {config_file}")

    def is_complete(self) -> bool:
        """Return True if the minimum required settings are present."""
        if not (self.input_dir and self.output_dir and self.sis_type):
            return False
        from src.utils.validators import _SIS_TYPE_RE

        return bool(_SIS_TYPE_RE.match(self.sis_type))

    def has_completed_setup(self) -> bool:
        """The durable "reached the setup finish line at least once" fact (D4a).

        ``True`` when the wizard explicitly recorded completion (``setup_completed`` — set in
        Slice 8) OR — the back-compat inference for installs predating the flag — the OLD
        finish-line condition holds (complete config + a registered schedule). This is the
        SINGLE place the two facts are OR-ed, so ``nav.needs_setup`` (and any onboarding gate)
        reads ``schedule_registered`` only through this sanctioned inference, never as a
        live-ness signal. Robust whether the config was loaded (baked in ``load()``) or
        constructed directly.
        """
        return self.setup_completed or (self.is_complete() and self.schedule_registered)

    def sftp_is_configured(self) -> bool:
        """Return True if SFTP has been enabled and configured."""
        if not (self.sftp_enabled and self.sftp_host and self.sftp_username and self.sftp_remote_path):
            return False
        from src.utils.validators import ALLOWED_SFTP_HOSTS

        return self.sftp_host.strip().lower() in ALLOWED_SFTP_HOSTS
