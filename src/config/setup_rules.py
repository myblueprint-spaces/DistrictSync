"""The rules a save of the district and the two folders obeys — ONE source for the window and the command line.

Moved here, unchanged, from the Flet layer (DSYNC-109) so the headless ``--configure`` command
(:mod:`src.config.district_setup`) applies EXACTLY the rules the window's Setup applies, without importing
``flet`` or the UI package (the config layer never imports either; ``tests/test_architecture_fitness.py`` rule (a)):

* **the folder rules** — :func:`validate_input_dir` (the input folder must EXIST and be a folder) and
  :func:`validate_output_dir` (the output folder's PARENT must exist; the folder itself may not exist yet, the
  conversion creates it). From ``src/ui_flet/filepicker.py``, which re-exports them for ``setup_state``, the
  ``PickerField`` validators and every screen that imported them from there;
* **the activation rule** — :func:`activation_allowed` and the stored facts it reads
  (:func:`stored_verified_digest`, :func:`verified_is_current`): a district authored on THIS computer may become the
  one this install converts only after passing a test conversion as it now reads. From
  ``src/ui_flet/config_editor.py``, which re-exports them for every UI call site.

PURE apart from the read-only ``stat`` calls the folder rules make; no write, no log line, no path echoed anywhere
but the returned message (and those messages name no path).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.utils.validators import is_config_digest, validate_sis_type

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.config.app_config import AppConfig


# --------------------------------------------------------------------------- #
# Validation result — a small typed value consumed uniformly by PickerField /  #
# setup_state (so a UI line + a save-gate read the same shape).                #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating a picked path: ``ok`` + a plain-language ``message``."""

    ok: bool
    message: str


# --------------------------------------------------------------------------- #
# Boundary validation — both PURE (RC3)                                        #
#   * validate_input_dir   — PURE (read-only stat: exists + is_dir)            #
#   * validate_output_dir  — PURE-STRUCTURAL (parent exists + is_dir)          #
# Paths are resolved first (``Path.resolve()``) to normalize. UNC paths        #
# (``\\\\server\\share``) and symlinks are ACCEPTED — district servers use     #
# them, and ``resolve()`` follows symlinks to a real target before the stat.   #
# --------------------------------------------------------------------------- #
def validate_input_dir(path: str) -> ValidationResult:
    """PURE: an input GDE folder must EXIST and be a DIRECTORY.

    Mirrors ``run_pipeline``'s ``input_dir.exists() and input_dir.is_dir()``
    boundary (``src/etl/pipeline.py:292``). Rejects a missing path AND a path
    that points at a file. Read-only stat — no side effects.
    """
    if not path or not path.strip():
        return ValidationResult(False, "Choose the folder that holds your MyEd BC extract files.")
    resolved = Path(path).resolve()
    if not resolved.exists():
        return ValidationResult(False, "That folder doesn't exist. Pick an existing folder.")
    if not resolved.is_dir():
        return ValidationResult(False, "That's a file, not a folder. Pick the folder it lives in.")
    return ValidationResult(True, "Looks good — this folder is ready to read.")


def validate_output_dir(path: str) -> ValidationResult:
    """PURE-STRUCTURAL: the output folder's PARENT must exist and be a directory.

    The output folder itself may not exist yet (the loader creates it), so we
    validate the *parent* is a real directory — which rejects a path whose
    parent is a file (an impossible location). This is read-only stat: it is
    cheap enough to run on every keystroke, which is why it stays structural.

    It deliberately does NOT answer "can we write there?" — that needs a real
    filesystem effect and belongs at run time, where
    ``etl.loader.output_target_problem`` performs it (and where its durable
    backstop, the loader's backup-and-restore atomic ``save_all``, lives).
    """
    if not path or not path.strip():
        return ValidationResult(False, "Choose where DistrictSync should write the output CSV files.")
    resolved = Path(path).resolve()
    parent = resolved.parent
    if not parent.exists() or not parent.is_dir():
        return ValidationResult(False, "That location isn't valid — its parent folder doesn't exist.")
    if resolved.exists() and not resolved.is_dir():
        return ValidationResult(False, "That's a file, not a folder. Pick a folder to write into.")
    return ValidationResult(True, "Looks good — output will be written here.")


# --------------------------------------------------------------------------- #
# The activation rule: has a district authored here passed a test conversion?   #
# --------------------------------------------------------------------------- #
def _safe_sis_type(value: object) -> str:
    """TOTAL wrapper over :func:`src.utils.validators.validate_sis_type`: ``""`` on refusal."""
    if not isinstance(value, str):
        return ""
    try:
        return validate_sis_type(value)
    except ValueError:
        return ""


def stored_verified_digest(app_config: AppConfig, sis_id: str) -> str | None:
    """The digest recorded when ``sis_id`` last passed the gate, or ``None``. TOTAL.

    Re-validates at READ time (the ``identity_gate.stored_identity_email`` precedent):
    ``config.json`` is hand-editable and ``AppConfig``'s load-time type check sees only
    the CONTAINER, so ``{"sd93custom": 123}`` loads clean. Everything malformed — a
    non-dict map, an id that is not a valid config id, a nested dict, a 63-character or
    upper-cased value — reads as ABSENT, i.e. "please run the test again". That is the
    only safe direction: an absent fact can force another test run, never unlock one.

    Deliberately NOT gated on ``authoring.is_custom_sis_id``. Activation is gated on the
    config's ORIGIN (S2's ``ConfigSummary.origin == "user"``), so a support-handed
    user-dir override of a SHIPPED id (``sd40myedbc``) must stay activatable after
    passing this same gate.

    Reads ``creator_verified`` through ``getattr`` so the rule is testable against any
    settings object and a profile written by an older build (no such field) answers
    "absent" rather than raising.
    """
    stored = getattr(app_config, "creator_verified", None)
    if not isinstance(stored, dict):
        return None
    key = _safe_sis_type(sis_id)
    if not key:
        return None
    value = stored.get(key)
    return value if is_config_digest(value) else None


def verified_is_current(stored: str | None, current: str | None) -> bool:
    """True only when a digest was stored AND matches what would convert today.

    Two ``None``s are NOT "fine": an unknown stored fact and an unloadable config are
    the two states that most need another test run, and reading them as agreement would
    activate a district nobody has run. Equality is over the whole digest string; the
    shape was already validated where each value came from.
    """
    return stored is not None and current is not None and stored == current


@dataclass(frozen=True)
class ActivationVerdict:
    """May this district become the one this install converts, and if not, why not.

    TWO booleans rather than one ``Literal``, deliberately: a future third refusal
    reason added to a single enum would read as "allowed" at every ``verdict ==
    "allowed"`` call site that had not been updated, whereas an unset ``allowed``
    can only ever refuse.

    Attributes:
        allowed: the switch may proceed.
        needs_test: it may not, because this district was authored HERE and has not
            passed a test conversion as it currently reads.
    """

    allowed: bool
    needs_test: bool


def activation_allowed(
    app_config: AppConfig,
    *,
    sis_id: str,
    origin: str,
    current_digest: str | None,
) -> ActivationVerdict:
    """Whether ``sis_id`` may be made the district this install converts. PURE, TOTAL.

    THE one verified-fact comparison behind every surface that can write ``sis_type``
    onto a district authored on this computer. There are FIVE such surfaces — the
    wizard's standard District pick, the wizard's creator activation, Mapping's Apply,
    Mapping's panel activation and the Settings folders card's Save (that last one only
    when it changes the district) — reaching this function through FOUR call sites:
    :func:`screens.creator.creator_gate_current` (which serves BOTH creator
    activations, in the wizard and in Mapping's panel), the wizard's District step and
    the folders card in :mod:`screens.setup`, and Mapping's Apply. The headless
    ``--configure`` (:mod:`src.config.district_setup`, DSYNC-109) is a fifth call site
    applying the folders card's rule without a window. Every call site, ONE comparison: a second spelling of "has this been tested?" is how one
    surface activates what another refuses.

    The rule:

    * ``origin != "user"`` — a SHIPPED mapping (or one whose provenance could not be
      read, which :func:`mapping_catalog._origin_of` already fails OPEN on) ⇒ allowed,
      and ``current_digest`` is never read. Shipped configs are ours; gating them would
      strand every install that has one.
    * ``origin == "user"`` ⇒ allowed only when the digest recorded at the last passing
      test conversion still equals what would convert TODAY
      (:func:`verified_is_current`), else :attr:`ActivationVerdict.needs_test`.

    ``current_digest`` is a required keyword with NO default (CLAUDE.md: no permissive
    default on a safety-relevant parameter). It is the EFFECTFUL half — the caller
    computes it with ``authoring.current_digest(sis_id)`` — and a default of ``None``
    would let a caller that forgot it refuse every user-authored district instead of
    failing to compile. Callers pass ``None`` deliberately on the bundled branch, so the
    20 shipped rows never pay a config load.

    Takes ``origin`` as a plain string rather than the ``ConfigSummary`` both view call
    sites hold: that is the only field the rule reads, and importing the catalog's
    published shape into this pure module would put a UI type in front of a truth table.
    """
    if origin != "user":
        return ActivationVerdict(allowed=True, needs_test=False)
    current = verified_is_current(stored_verified_digest(app_config, sis_id), current_digest)
    return ActivationVerdict(allowed=current, needs_test=not current)
