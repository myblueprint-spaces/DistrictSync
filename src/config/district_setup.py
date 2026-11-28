"""Headless ``--configure --sis <district> --input <folder> --output <folder>`` (DSYNC-109, plan 0055 parity).

The window's Settings folders card, without a window: it saves the district (``sis_type``) and the two folders
(``input_dir``, ``output_dir``) to the ACTIVE profile through ``AppConfig.load()`` / ``save()``, which on a machine-wide
install is the shared ``C:\\ProgramData\\DistrictSync`` profile. Without it a Server Core computer could not satisfy
``--schedule-register``'s ``RegisterBlock.INCOMPLETE`` gate, because the window was the only writer of those three.

Another CALLER of the window's rules, never a second spelling of them (all in :mod:`src.config.setup_rules`):

* **the district** must be a mapping config on this computer (:func:`src.config.loader.resolve_config_path`, the
  lookup the window's catalog derives each row's origin from) and a valid id (``validate_sis_type``, lower-cased as a
  conversion reads ``--sis``). Never guessed (D9): ``--sis`` is required. When the district CHANGES, a mapping
  authored on this computer must have passed a test conversion as it now reads (:func:`activation_allowed`, the ONE
  comparison every ``sis_type`` writer consults). As on the folders card, the check guards the district CHANGE: a
  folders-only edit on the district this install already converts activates nothing;
* **the input folder** must EXIST and be a folder (:func:`validate_input_dir`);
* **the output folder's PARENT** must exist and the path must not be a file (:func:`validate_output_dir`); the folder
  itself may not exist yet and is NOT created here, the conversion creates it.

Every rule is checked BEFORE anything is written: a refusal writes nothing at all. The folders are saved ABSOLUTE
(``os.path.abspath``), because the nightly runs from another working folder; a path the window's picker returns is
already absolute, so this changes nothing the window would save.

**Elevation.** None, as in the window: the save is a plain ``AppConfig.save()``. On a machine-wide install the shared
profile is writable by the administrator who set the computer up (granted Modify by machine setup, unelevated) and
by an elevated Administrators token; another account is refused earlier, when the profile is pinned. A save Windows
still refuses is reported as one line (:data:`SAVE_DENIED_LINE`), never retried elevated.

**Bounded output.** The log carries the outcome only, never a folder path; the one confirmation line printed to the
console repeats the folders the administrator typed.
"""

from __future__ import annotations

import logging
import os

from src.config.app_config import AppConfig, SettingsOverwriteRefused
from src.config.authoring import current_digest
from src.config.loader import resolve_config_path
from src.config.setup_rules import activation_allowed, validate_input_dir, validate_output_dir
from src.utils.validators import validate_sis_type

logger = logging.getLogger(__name__)

#: How every refusal starts: nothing was written, then why.
NOTHING_SAVED = "Nothing was saved:"
#: A district authored on this computer that has not passed a test conversion as it now reads.
NEEDS_TEST_LINE = (
    "this mapping was set up on this computer and has not passed a test conversion as it now reads. "
    "Run one under Mapping in DistrictSync's window, then configure again."
)
#: Windows refused the write to the settings folder.
SAVE_DENIED_LINE = (
    f"{NOTHING_SAVED} this account cannot change DistrictSync's settings on this computer. Run it as the "
    "administrator who set this computer up, or from an administrator command prompt."
)
#: A nightly registered with the earlier settings keeps running them until it is scheduled again.
RESCHEDULE_NOTE = (
    "A nightly is already scheduled with the earlier settings: run --schedule-register again to use these."
)


def saved_line(sis: str, input_dir: str, output_dir: str) -> str:
    """The one confirmation line (console only; the log never carries the folders)."""
    return f"Saved: district {sis}, input folder {input_dir}, output folder {output_dir}."


def configure(*, sis: str, input_dir: str, output_dir: str) -> tuple[int, str]:
    """Save the district and both folders. Returns ``(exit_code, message)``: 0 saved, 1 refused (nothing written)."""
    try:
        sis_id = validate_sis_type(sis.lower())
    except ValueError:
        return 1, f"{NOTHING_SAVED} --sis must be a district id: letters, digits and underscores only."
    resolved = resolve_config_path(sis_id)
    if resolved is None:
        logger.info("Headless configure refused: no such district mapping.")
        return 1, f"{NOTHING_SAVED} --sis: there is no district mapping called '{sis_id}' on this computer."

    input_abs, output_abs = os.path.abspath(input_dir), os.path.abspath(output_dir)
    for flag, result in (("--input", validate_input_dir(input_abs)), ("--output", validate_output_dir(output_abs))):
        if not result.ok:
            logger.info("Headless configure refused: the %s folder failed the window's folder rule.", flag)
            return 1, f"{NOTHING_SAVED} {flag}: {result.message}"

    cfg = AppConfig.load()
    if sis_id != cfg.sis_type:
        verdict = activation_allowed(
            cfg,
            sis_id=sis_id,
            origin=resolved.origin,
            # ``None`` on the bundled branch deliberately: the rule never reads it there.
            current_digest=current_digest(sis_id) if resolved.origin == "user" else None,
        )
        if not verdict.allowed:
            logger.info("Headless configure refused: the district has not passed a test conversion.")
            return 1, f"{NOTHING_SAVED} {NEEDS_TEST_LINE}"

    changed = (cfg.sis_type, cfg.input_dir, cfg.output_dir) != (sis_id, input_abs, output_abs)
    cfg.sis_type, cfg.input_dir, cfg.output_dir = sis_id, input_abs, output_abs
    try:
        cfg.save()
    except PermissionError:
        # INFO, not ERROR: the console's WARNING+ log handler would print it beside the one refusal line.
        logger.info("Headless configure refused: the settings file could not be written (permission denied).")
        return 1, SAVE_DENIED_LINE
    except SettingsOverwriteRefused:
        # save() has already logged why (the superseded-profile fence); the console gets the next step.
        return (
            1,
            f"{NOTHING_SAVED} these settings were replaced by another settings folder. Run DistrictSync.exe --diagnose.",
        )
    except OSError as exc:
        logger.info("Headless configure refused: the settings file could not be written (%s).", type(exc).__name__)
        return 1, f"{NOTHING_SAVED} the settings file could not be written ({type(exc).__name__}); see the log."
    logger.info("Headless configure saved the district and both folders.")
    message = saved_line(sis_id, input_abs, output_abs)
    if changed and cfg.schedule_registered:
        message += "\n" + RESCHEDULE_NOTE
    return 0, message
