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

Crash safety (W2-B) — the settings file is the ONE artifact whose loss silently
resets a working install to first-run, so both directions are hardened:

* **The write is atomic and durable.** :func:`_atomic_write_text` stages the payload
  in a sibling temp file, ``fsync``s it, then promotes it with a single
  ``os.replace`` — the same reasoning ``src/etl/loader.py`` documents for its commit:
  ``os.replace`` is an atomic same-filesystem overwrite, whereas ``shutil.move``
  degrades to copy2+unlink on Windows and tears *within* the file. A crash at any
  point leaves the previous ``config.json`` byte-intact. Unlike the loader there is
  no ``.bak_*`` sidecar: the loader needs one because it commits N entity CSVs as a
  single unit, while this is ONE file — the single ``os.replace`` IS the whole
  transaction, and a backup would add a second failure mode for no gain.
* **The read is honest.** :meth:`AppConfig.load` reports :class:`ConfigLoadState`, so
  an existing-but-unreadable file is never indistinguishable from a genuinely absent
  one. ``load()`` stays a PURE read (it never moves or rewrites anything) so every
  call in a session agrees; the unreadable bytes are preserved as
  ``config.corrupt-<ts>.json`` by ``save()``, at the only moment they would otherwise
  be destroyed.
* **The write CONSULTS the read.** ``load_state`` is not decoration — it is an input to
  :meth:`AppConfig.save`. A config loaded :attr:`ConfigLoadState.UNREADABLE` holds
  DEFAULTS THIS MODULE INVENTED, never values it read, so writing it verbatim replaces
  settings we failed to read with settings nobody chose. ``save()`` therefore refuses
  the write that carries no admin choice at all, and quarantines the predecessor on the
  write that does — both decided from ``load_state``, never re-derived from the disk.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from enum import Enum
from functools import cache
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from src.utils import paths

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.json"

# Name for the preserved bytes of an unreadable predecessor. Deliberately mirrors the
# run store's ``history.corrupt-<ts>.db`` convention so the two quarantine artifacts
# read alike in a support ticket.
_QUARANTINE_NAME_FMT = "config.corrupt-%Y%m%d-%H%M%S.json"

# Fields that describe the LOAD, not the settings. Never written to disk and never
# accepted from it — one frozenset drives BOTH the save payload and the load allowlist,
# so persisted-vs-transient can never drift between the two.
_TRANSIENT_FIELDS = frozenset({"load_state"})

# Ambient window state, NOT a setting the admin chose. The ``window_*`` naming is the
# contract (see the geometry block on :class:`AppConfig`) so a future window field joins
# the set automatically rather than being forgotten in a hand-maintained list. Used by
# :meth:`AppConfig._carries_chosen_settings` to tell an admin's settings write apart from
# the shell's advisory geometry write.
_GEOMETRY_FIELD_PREFIX = "window_"


class SettingsOverwriteRefused(RuntimeError):
    """A save was refused because it would replace settings we FAILED TO READ.

    Raised by :meth:`AppConfig.save` for exactly one shape: an instance whose
    ``load_state`` is :attr:`ConfigLoadState.UNREADABLE` and whose settings are still the
    untouched defaults ``load()`` invented — a payload that provably contains nothing the
    admin chose. Writing it would atomically and durably swap the admin's district,
    folders and delivery settings for blanks.

    Raising (rather than returning quietly) follows the contract this module already
    holds for a failed promote: *a settings write that did not happen must never look
    like one that did.* The only reachable caller of a settings-free save is the shell's
    advisory window-geometry save on app exit, which is deliberately failure-tolerant
    (``except Exception`` → DEBUG log → keep closing), so the refusal can neither block
    nor crash the close — and ``save()`` logs at WARNING before raising so the event
    reaches the support log regardless of what the caller does with the exception.
    """


def config_file_path() -> Path:
    """Resolve the ``config.json`` path at call time, through the single paths seam."""
    return paths.user_data_dir() / CONFIG_FILENAME


class ConfigLoadState(str, Enum):
    """Where a loaded :class:`AppConfig`'s values CAME from — the trust-bar seam.

    A trust instrument may not assert a state it did not check, and the most damaging
    unverified assertion this app can make is *"you are a new user"* to an admin whose
    settings file merely failed to read. These three states keep that distinguishable:

    * :attr:`ABSENT` — no ``config.json``. A genuinely fresh install; onboarding is correct.
    * :attr:`LOADED` — read and parsed from disk. The values are the admin's own.
    * :attr:`UNREADABLE` — a ``config.json`` EXISTS but could not be read as settings
      (torn write, undecodable bytes, not a JSON object, nonsense value types, or an
      OS-level read failure). The values in hand are DEFAULTS we fell back to, never
      values we read — and the install is provably not a new one.
    """

    ABSENT = "absent"
    LOADED = "loaded"
    UNREADABLE = "unreadable"


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

    # TRANSIENT provenance (W2-B) — set by ``load()`` from what it OBSERVED, never
    # persisted and never accepted from the file it describes (``_TRANSIENT_FIELDS``
    # gates both directions, so a hand-edited config.json cannot forge it). Excluded
    # from ``__eq__``: how a config was read is not a settings difference.
    load_state: ConfigLoadState = field(default=ConfigLoadState.ABSENT, compare=False, repr=False)

    @classmethod
    def load(cls) -> AppConfig:
        """Load config from disk — a PURE read that reports HOW it went.

        Never raises and never mutates the filesystem. The returned config always
        carries a :attr:`load_state`:

        * no file → defaults + :attr:`ConfigLoadState.ABSENT` (a genuine fresh install);
        * readable → the admin's values + :attr:`ConfigLoadState.LOADED`;
        * present but unreadable → defaults + :attr:`ConfigLoadState.UNREADABLE`, logged
          at ERROR (not WARNING — losing an admin's settings is a loud event) naming the
          file so the log points at something actionable.

        The read is deliberately non-mutating. ``AppConfig.load()`` is called on nearly
        every UI surface, so quarantining the bad file HERE (the run store's
        ``write_run_record`` pattern) would report UNREADABLE on the first call and
        ABSENT on every later one — dumping a configured admin back into onboarding one
        screen later, which is precisely the failure this hardening removes. Preserving
        the bytes therefore lives in :meth:`save`, at the only moment they would
        otherwise be destroyed.
        """
        config_file = config_file_path()
        try:
            raw = config_file.read_bytes()
        except FileNotFoundError:
            return cls(load_state=ConfigLoadState.ABSENT)
        except OSError as exc:
            logger.error(
                "The settings file %s exists but could not be read (%s). Running on defaults for "
                "this session — your saved settings are still on disk and this install is NOT "
                "treated as a new install.",
                config_file,
                exc,
            )
            return cls(load_state=ConfigLoadState.UNREADABLE)

        cfg = _config_from_bytes(raw)
        if cfg is None:
            logger.error(
                "The settings file %s could not be read as settings (it looks truncated or "
                "corrupt). Running on defaults for this session; the file is left untouched and "
                "will be preserved as a config.corrupt-*.json copy the next time settings are "
                "saved. This install is NOT treated as a new install.",
                config_file,
            )
            return cls(load_state=ConfigLoadState.UNREADABLE)
        return cfg

    def save(self) -> None:
        """Persist config to disk atomically and durably (creates parent dir if needed).

        A reader can only ever observe the complete previous document or the complete
        new one — see :func:`_atomic_write_text`.

        **The write consults the read.** ``load_state`` decides both guards, so neither
        re-derives anything from the disk:

        * an UNREADABLE-provenance instance that carries **no admin choice at all** (see
          :meth:`_carries_chosen_settings`) is REFUSED — nothing is written, the file on
          disk is left byte-intact, and a transient read failure therefore self-heals on
          the next load instead of being cemented into blanks;
        * an UNREADABLE-provenance instance that DOES carry admin choices writes, but only
          after :func:`_preserve_unreadable_predecessor` copies the bytes it is about to
          replace aside — bytes this config never read, whether or not they happen to
          parse at this moment.

        Be precise about what that second branch does, because it is broader than the
        wizard: **ONE** non-default field unlocks a write of the **whole** in-hand
        document, and the payload is never merged onto a re-read of disk. **Not even the
        wizard is a guaranteed full repair:** its Delivery and Schedule steps are
        deliberately skippable, and its District step is itself a single-section save of
        only ``sis_type`` — which under UNREADABLE provenance writes the invented document
        and then re-tags the instance LOADED, so the later steps never re-trigger the
        guard. Any single-section save reaching this branch replaces the settings it never
        read with invented defaults. `screens/mapping.py`'s Apply is exactly that shape: it sets
        only ``sis_type``. The displaced bytes survive as ``config.corrupt-*.json``, so
        this costs recovery effort rather than data, and it is strictly better than the
        pre-fix behaviour (which clobbered with no copy at all) — but it is a residual,
        not a solved problem. Tracked in ``docs/claugentic-ROADMAP.md``; the candidate
        fixes (merge onto a re-read here, or a ``settings_unreadable()`` guard on the
        non-wizard surfaces) need a product call on the copy, not a mechanical patch.

        A SUCCESSFUL save re-tags the instance :attr:`ConfigLoadState.LOADED`, because it
        now holds exactly what is on disk — it just put it there. Without that transition
        a long-lived config (the Setup wizard keeps ONE instance across every step of the
        repair) would stay UNREADABLE forever and quarantine its own freshly-written good
        bytes on every subsequent save, littering ``config.corrupt-*.json`` copies.

        Raises :class:`SettingsOverwriteRefused` for the first case, or the underlying
        ``OSError`` if the payload cannot be written (disk full, permission denied) — a
        settings write that did not happen must never look like one that did (the
        provenance is likewise NOT advanced on a failed write).
        """
        config_file = config_file_path()
        load_was_unreadable = self.settings_unreadable()
        if load_was_unreadable and not self._carries_chosen_settings():
            logger.warning(
                "Refusing to overwrite the settings file %s: it could not be read this session, and this "
                "save carries no settings you chose (window position only). Your saved settings are left "
                "untouched on disk.",
                config_file,
            )
            raise SettingsOverwriteRefused(
                f"{config_file} could not be read this session; refusing to replace it with defaults"
            )
        config_dir = config_file.parent
        config_dir.mkdir(parents=True, exist_ok=True)
        _restrict_directory(config_dir)
        _preserve_unreadable_predecessor(config_file, load_was_unreadable=load_was_unreadable)
        _atomic_write_text(config_file, json.dumps(self._persisted_dict(), indent=2))
        self.load_state = ConfigLoadState.LOADED
        logger.info(f"App config saved to {config_file}")

    def _persisted_dict(self) -> dict[str, Any]:
        """The settings payload written to disk — every field except the transient ones."""
        return {k: v for k, v in asdict(self).items() if k not in _TRANSIENT_FIELDS}

    def _carries_chosen_settings(self) -> bool:
        """True when ANY settings field differs from the constructor default.

        Provenance, not shape. Every settings field in a payload is either (a) something
        a caller explicitly supplied or (b) a default this module invented. On an
        UNREADABLE load every field is (b) — so if none has since moved off its default,
        the document is 100% invention and writing it is pure destruction.

        Window geometry is excluded by the ``window_*`` prefix contract: it is ambient
        window state, not a setting the admin chose, so a geometry-only mutation leaves
        the settings wholly invented. This is exactly what separates the shell's advisory
        exit-time geometry save (refused) from a save carrying an admin choice (allowed).

        Note the asymmetry this predicate deliberately does NOT resolve: it answers "is
        this payload entirely invented?", not "is this payload a complete repair?". One
        chosen field is enough to unlock the write — see :meth:`save` for what that costs
        a single-section caller.

        Only consulted when ``load_state`` is UNREADABLE — a LOADED config whose settings
        genuinely still are the defaults (a launched-but-never-configured install) READ
        those values, invents nothing, and saves normally.
        """
        defaults = AppConfig()
        return any(
            getattr(self, f.name) != getattr(defaults, f.name)
            for f in fields(AppConfig)
            if f.name not in _TRANSIENT_FIELDS and not f.name.startswith(_GEOMETRY_FIELD_PREFIX)
        )

    def settings_unreadable(self) -> bool:
        """True when a ``config.json`` EXISTS on disk but could not be read as settings.

        The honesty seam with TWO consumers, one per direction:

        * ``nav.needs_setup`` (read side) — the file's existence is a CHECKED fact, so
          "this is a brand-new install" is known to be false and must not be asserted;
        * :meth:`save` (write side) — the values in hand are defaults this module
          invented, so they may not silently replace the ones on disk.

        Deliberately does NOT fake the opposite — :meth:`has_completed_setup` stays a
        fact about what was actually read (False here), because "we could not confirm"
        is the honest answer, not "you're set up".
        """
        return self.load_state is ConfigLoadState.UNREADABLE

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


# --------------------------------------------------------------------------- #
# Parsing — ONE definition of "readable as settings".                          #
# --------------------------------------------------------------------------- #
@cache
def _settings_field_types() -> dict[str, Any]:
    """The declared runtime type of every PERSISTED field, resolved once.

    Derived from the dataclass annotations, so the type check below has exactly one
    source of truth — adding a field to :class:`AppConfig` validates it automatically,
    with no parallel table to forget.
    """
    return {name: hint for name, hint in get_type_hints(AppConfig).items() if name not in _TRANSIENT_FIELDS}


def _value_fits(value: object, annotation: Any) -> bool:
    """Whether a JSON value is usable as ``annotation`` (total — unknown forms pass).

    Deliberately permissive at the edges and strict where it matters:

    * a JSON int satisfies ``float`` (``800`` is a fine window width) but ``bool`` never
      satisfies ``int`` — Python makes ``bool`` an ``int`` subclass, so ``"sftp_port": true``
      would otherwise sail through and reach ``paramiko`` as a port number;
    * an annotation this function does not recognise returns ``True`` — a type check is a
      safety net, and a net that rejects what it does not understand would turn a future
      annotation style into a false "your settings are corrupt".
    """
    origin = get_origin(annotation)
    if origin is UnionType or origin is Union:
        return any(_value_fits(value, arg) for arg in get_args(annotation))
    if annotation is type(None):
        return value is None
    if origin is not None:  # a parameterised generic: dict[str, object], list[...] …
        return isinstance(value, origin)
    if annotation is bool:
        return isinstance(value, bool)
    if annotation is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if annotation is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(annotation, type):
        return isinstance(value, annotation)
    return True


def _config_from_bytes(raw: bytes) -> AppConfig | None:
    """Parse ``config.json`` bytes into an :class:`AppConfig`, or ``None`` if unreadable.

    ``None`` means the bytes are not a settings document: undecodable, not JSON, not a
    JSON *object*, or a **known key holding a value of the wrong declared type**.

    That last check is real, not advisory (it was documented before it was implemented —
    fixed here). ``config.json`` is hand-editable, untrusted input, and a wrong-typed
    value is not merely inert: it is carried through the session and then PERSISTED BACK
    verbatim by the next save, cementing the corruption instead of quarantining it. So a
    ``"sis_type": {}`` — falsy, hence invisible to ``is_complete()`` — or a
    ``"sftp_port": "22"`` makes the whole document UNREADABLE, which routes it into the
    honest fallback: defaults for the session, onboarding suppressed, bytes preserved as
    ``config.corrupt-*.json`` by the repairing save. Validate at boundaries; the settings
    file is one.

    THE single definition of "corrupt" — :meth:`AppConfig.load` reports
    :attr:`ConfigLoadState.UNREADABLE` on ``None`` and
    :func:`_preserve_unreadable_predecessor` quarantines on ``None``, so the read path
    and the preserve path can never disagree about which files are salvage-worthy.
    Unknown/extra keys are IGNORED, not rejected (and therefore not type-checked) —
    forward-compatibility with configs written by a newer build is a deliberate, tested
    behaviour.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    field_types = _settings_field_types()
    filtered: dict[str, Any] = {}
    for key, value in data.items():
        if key not in field_types:
            continue  # unknown/extra key — forward-compat, ignored not rejected
        if not _value_fits(value, field_types[key]):
            # Key + found type ONLY: config.json holds district folder paths and the
            # delivery username, and a diagnostic is not a place for either.
            logger.debug("Settings key %r holds a %s, which is not its declared type", key, type(value).__name__)
            return None
        filtered[key] = value
    try:
        cfg = AppConfig(**filtered)
        # Back-compat inference (D4a): bake the durable finish-line fact through the
        # single-source derivation so an install predating the flag (complete config +
        # a registered schedule = the OLD finish line) is never dropped back into
        # first-run onboarding after this update. An explicitly-persisted True is kept.
        cfg.setup_completed = cfg.has_completed_setup()
    except (TypeError, ValueError) as exc:
        # The floor BEHIND the type check, not a duplicate of it: ``_value_fits`` is
        # deliberately permissive at its edges (an annotation form it does not recognise
        # passes), so a future exotic field could still admit a value that blows up here.
        # Not dead code — the last thing between a bad document and a crashed load().
        logger.debug("Settings document rejected: %s", exc)
        return None
    cfg.load_state = ConfigLoadState.LOADED
    return cfg


# --------------------------------------------------------------------------- #
# Write path — atomic promote, durable payload, owner-only throughout.         #
# --------------------------------------------------------------------------- #
def _restrict_directory(config_dir: Path) -> None:
    """Owner-only (0o700) on the app-data dir on Unix; no-op on Windows (best-effort).

    Best-effort by design: the directory may live on a filesystem without POSIX modes
    (a mounted share), where failing the whole save over a cosmetic permission tighten
    would be worse than the exposure. The FILE's 0o600 is not best-effort — it is set
    on the staging descriptor before any settings bytes exist (see
    :func:`_atomic_write_text`).
    """
    if sys.platform == "win32":
        return
    try:
        os.chmod(config_dir, 0o700)
    except OSError as exc:
        logger.debug("Could not restrict permissions on %s (%s)", config_dir, exc)


def _atomic_write_text(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` so a reader can NEVER observe a partial document.

    Stage → fsync → promote:

    1. ``tempfile.mkstemp`` in the TARGET'S OWN DIRECTORY (same filesystem, so the
       promote is a true rename) — and, on POSIX, created ``0o600`` by the C library
       before a single settings byte is written, so the staging file never widens the
       permission window (an explicit ``chmod`` re-asserts it for good measure).
    2. ``flush`` + ``os.fsync`` the payload. Without this, ``os.replace`` is atomic only
       with respect to the *name*: a power loss could promote a file whose data never
       left the page cache.
    3. ``os.replace`` — an ATOMIC same-filesystem overwrite. Deliberately not
       ``shutil.move``, which degrades to copy2+unlink on Windows and tears *within*
       the file (the exact bug ``src/etl/loader.py::_commit_staged`` documents).
    4. ``fsync`` the directory so the rename record itself is durable (POSIX only).

    On any failure the staging file is removed and the error PROPAGATES — the previous
    ``config.json`` is untouched, and the caller learns the write did not happen.
    """
    directory = target.parent
    fd, staged_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(directory))
    staged = Path(staged_name)
    promoted = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if sys.platform != "win32":
            os.chmod(staged, 0o600)
        os.replace(staged, target)
        promoted = True
    finally:
        # Covers exceptions AND KeyboardInterrupt/SystemExit — a torn save must not
        # leave staging litter in the admin's app-data folder.
        if not promoted:
            with contextlib.suppress(OSError):
                staged.unlink()
    _fsync_directory(directory)


def _fsync_directory(directory: Path) -> None:
    """``fsync`` a directory so a just-completed rename survives a power loss (POSIX only).

    No-op on Windows, which cannot open a directory handle this way (NTFS journals the
    rename metadata regardless). A failure is logged at DEBUG and swallowed — a NARROW,
    deliberate exception to fail-loud: the payload is already fsynced and atomically in
    place, so this call only tightens the durability of the *rename record*, and some
    filesystems (container overlays, network shares) reject directory ``fsync``
    outright, where raising would break every save for zero correctness gain.
    """
    if sys.platform == "win32":
        return
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError as exc:
        logger.debug("Could not open %s to fsync the settings rename (%s)", directory, exc)
        return
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        logger.debug("Could not fsync %s after promoting the settings file (%s)", directory, exc)
    finally:
        os.close(dir_fd)


def _preserve_unreadable_predecessor(config_file: Path, *, load_was_unreadable: bool) -> None:
    """Copy an UNREADABLE ``config.json`` aside before :meth:`AppConfig.save` overwrites it.

    Quarantine lives here rather than in ``load()`` on purpose (see
    :meth:`AppConfig.load`): the read path must stay pure so every load in a session
    agrees, while this is the single moment the unreadable bytes would be destroyed. A
    truncated JSON document is usually a readable PREFIX, so preserving it lets an admin
    (or support) recover their folders / district / SFTP settings by eye instead of
    reconstructing them from memory.

    ``load_was_unreadable`` is the saving config's OWN ``load_state``, and it is
    AUTHORITATIVE: when it is ``True`` the bytes are preserved without being re-parsed,
    because they are bytes this config never read — whether they happen to parse *now* is
    irrelevant, and re-deriving that verdict from the disk was the bug. A read failure
    that had cleared by save time (a transient sharing violation, an AV lock, a
    permissions blip) read back as "readable, nothing to preserve", and the admin's
    district / folders / delivery settings were replaced with no recoverable copy.

    The parse survives only for the ``False`` branch, as defence in depth for a config
    with no load provenance at all (a directly-constructed :class:`AppConfig` saving over
    a file it never saw): it can only ADD a quarantine, never skip one.

    Best-effort and never fatal: the save that FIXES the broken settings must not be
    blocked by a failure to archive the broken ones (logged at WARNING).
    """
    try:
        raw = config_file.read_bytes()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Could not inspect the existing settings file %s before replacing it (%s)", config_file, exc)
        return

    if not load_was_unreadable and _config_from_bytes(raw) is not None:
        return  # we read it fine and it still parses — the normal path, nothing to preserve

    quarantine = config_file.with_name(datetime.now().strftime(_QUARANTINE_NAME_FMT))
    try:
        # O_EXCL + 0o600 in one call: the copy is owner-only from creation (it holds the
        # same settings as config.json) and can never clobber an earlier quarantine.
        quarantine_fd = os.open(str(quarantine), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(quarantine_fd, "wb") as handle:
            handle.write(raw)
    except OSError as exc:
        logger.warning("Could not preserve the unreadable settings file %s (%s); it will be replaced", config_file, exc)
        return

    logger.error(
        "The existing settings file %s could not be read as settings; its contents were preserved "
        "as %s before being replaced.",
        config_file.name,
        quarantine.name,
    )
