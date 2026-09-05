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

# The glob that finds what the format above wrote. Kept beside it so the writer and the
# only reaper of these files can never disagree about which files are quarantine copies.
_QUARANTINE_GLOB = "config.corrupt-*.json"

# Fields that describe the LOAD, not the settings. Never written to disk and never
# accepted from it — one frozenset drives BOTH the save payload and the load allowlist,
# so persisted-vs-transient can never drift between the two.
_TRANSIENT_FIELDS = frozenset({"load_state"})

# Ambient window state, NOT a setting the admin chose. The ``window_*`` naming is the
# contract (see the geometry block on :class:`AppConfig`) so a future window field joins
# the set automatically rather than being forgotten in a hand-maintained list.
_GEOMETRY_FIELD_PREFIX = "window_"

# ADVISORY field families — persisted, but NOT settings that make the sync work. Used by
# :meth:`AppConfig._carries_chosen_settings` to tell an admin's real settings write apart
# from a write that carries nothing worth overwriting an unreadable file for:
#
# * ``window_`` — ambient shell geometry (the original member; behaviour unchanged).
# * ``identity_`` — who looks after this sync (plan 0038). Advisory for exactly the same
#   reason: it scopes a picker and echoes on Help, and it changes NOTHING about which
#   district converts, from where, to where, or when. So an identity-only save on a
#   profile we FAILED TO READ must be refused by the existing machinery rather than
#   trading the admin's real folders/district/delivery settings for invented blanks.
#
# * ``creator_`` — the in-progress state of a self-service district (plan 0044). Nothing
#   in the ETL, CLI or scheduler reads either field: the sync runs off ``sis_type`` + the
#   YAML. The token is resume convenience; the tested-fact only decides whether the UI
#   OFFERS activation, and it is keyed on a digest of the RESOLVED config, so losing it
#   can only force another test run — never unlock one. Both degrade to "ask again", the
#   property that put ``identity_`` here: the write must be refusable on a profile we
#   failed to read without trapping the admin.
#
#   What the prefix does NOT cover: :meth:`AppConfig.activate_creator_config`, which
#   writes ``sis_type`` beside them and is deliberately NON-advisory — it carries a
#   chosen setting (the district this install converts), so it takes the same
#   write-under-UNREADABLE posture as the wizard's District step.
#
# A prefix contract rather than a hand-maintained name list, so a future field in any
# family joins automatically. Note the deliberate near-miss it also protects: the seasonal
# window's fields are named ``sync_window_*`` precisely so they do NOT start with
# ``window_`` — they ARE admin choices (see the naming-contract comment on those fields).
_IDENTITY_FIELD_PREFIX = "identity_"
_CREATOR_FIELD_PREFIX = "creator_"
_ADVISORY_FIELD_PREFIXES: tuple[str, ...] = (
    _GEOMETRY_FIELD_PREFIX,
    _IDENTITY_FIELD_PREFIX,
    _CREATOR_FIELD_PREFIX,
)


@dataclass(frozen=True)
class ClearOutcome:
    """What :meth:`AppConfig.identity_clear` actually managed to do.

    Three outcomes hide behind a bare ``True``, and they warrant three different sentences
    to the admin: nothing to sweep, everything swept, or some copies still on disk because
    a file was locked. Returning the counts is what lets the caller's note be true in all
    three — a single "we also deleted the older copies" line is a false claim in two of
    them, including the one where EVERY unlink failed.
    """

    cleared: bool  # the settings file was written
    removed: int  # quarantine copies unlinked
    remaining: int  # quarantine copies still on disk (locked, or nothing to sweep)


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

    # Seasonal sync window (owner decision 2026-07-21) — an OPT-IN recurring
    # school-year window that governs the app's OWN automatic nightly run only. The
    # scheduled task stays a plain daily trigger; the ENGINE decides each night
    # whether to run (inside the window) or pause (outside, over summer) — see
    # ``src/etl/sync_window.py`` (the pure predicate) and the gate in ``src/main.py``.
    # Stored as recurring ``"MM-DD"`` boundaries (NOT full dates) because the window
    # recurs every year with nothing to re-arm. Default OFF, so existing installs and
    # partners without a window keep running year-round, byte-identical. Additive with
    # safe defaults so old ``config.json`` files load unchanged (back-compat).
    #
    # NAMING CONTRACT (do not break): these are admin CHOICES and MUST be counted by
    # :meth:`_carries_chosen_settings`, so they deliberately use the ``sync_window_``
    # prefix (starts with ``sync_``) — NOT the ``window_`` geometry prefix
    # (``_GEOMETRY_FIELD_PREFIX``) that marks ambient shell geometry as a NON-choice.
    # Renaming them to ``window_*`` would silently make a window-only save look like
    # "nothing the admin chose" and get refused under UNREADABLE provenance.
    sync_window_enabled: bool = False  # opt-in; default off = year-round (byte-identical)
    sync_window_start: str = ""  # "MM-DD" — first day of the season (e.g. "08-11")
    sync_window_end: str = ""  # "MM-DD" — last day of the season (e.g. "07-06")

    # Onboarding (D4a): the durable "reached the setup finish line at least once" fact,
    # kept DISTINCT from the schedule's live-ness (which is read back from the OS, never
    # trusted from a flag). Set explicitly by the wizard's finish line in Slice 8; until
    # then it is inferred on load from the old finish-line condition (see load()).
    setup_completed: bool = False

    # Identity — WHO looks after this sync (plan 0038). Advisory metadata, never a
    # credential and never a setting the ETL reads: it scopes the district pickers so the
    # highest-consequence wrong click is harder to make, echoes read-only on Help, and is
    # the recipient a future failure notification would go to. It is NOT authentication —
    # there are no accounts, every mapping ships in the binary regardless, and every path
    # enters the app.
    #
    # NAMING CONTRACT (do not break): the ``identity_`` prefix is load-bearing. It puts
    # all three fields in ``_ADVISORY_FIELD_PREFIXES``, which is what makes an
    # identity-only save on an UNREADABLE profile get REFUSED by the existing
    # ``_carries_chosen_settings`` machinery instead of replacing the admin's real
    # settings with blanks. Rename the prefix and that protection silently disappears.
    #
    # Additive with safe defaults, so a v3.8.x ``config.json`` loads unchanged.
    identity_email: str = ""  # as TYPED (case preserved); normalisation happens at compare time
    identity_prompt_dismissed: bool = False  # the Home card was dismissed — permanent, Settings-recoverable
    identity_sd_number: str = ""  # "my district isn't listed yet" — the SD number they told us

    # Self-service district authoring — the IN-PROGRESS state of a district an admin is
    # setting up themselves (plan 0044). Advisory metadata, never a setting the sync reads:
    # what converts is ``sis_type`` + the YAML on disk.
    #
    # * ``creator_pending_sis`` is the RESUME token — the id of a district whose setup was
    #   started and not activated. Losing it costs the admin the resume, never the file.
    # * ``creator_verified`` maps a config id → the digest of the RESOLVED config that
    #   PASSED a test conversion. It only decides whether the UI OFFERS activation; because
    #   it is keyed on the resolved digest, losing it (or failing to write it) can only
    #   force another test run, never unlock one.
    #
    # NAMING CONTRACT (do not break): the ``creator_`` prefix is load-bearing for the same
    # reason ``identity_``'s is — it puts both fields in ``_ADVISORY_FIELD_PREFIXES``, which
    # is what makes a creator-only save on a profile we FAILED TO READ get REFUSED instead
    # of replacing the admin's real settings with blanks.
    #
    # ``creator_verified`` uses ``field(default_factory=dict)`` — a bare ``{}`` default
    # raises ``ValueError: mutable default`` at class-definition time.
    #
    # Additive with safe defaults, so a v3.14.x ``config.json`` loads unchanged.
    creator_pending_sis: str = ""  # the id of a self-service district still being set up
    creator_verified: dict[str, str] = field(default_factory=dict)  # config id → tested resolved digest

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

        The ADVISORY families are excluded by the ``_ADVISORY_FIELD_PREFIXES`` contract:
        window geometry is ambient shell state, ``identity_*`` is "who looks after this"
        metadata and ``creator_*`` is the in-progress state of a district being set up —
        none of them is a setting that makes the sync work, so a save carrying only those
        leaves the actual settings wholly invented. This is exactly what separates the
        shell's advisory exit-time geometry save (refused) and an identity-only or
        creator-only save on an unreadable profile (also refused) from a save carrying a
        real admin choice (allowed) — including :meth:`activate_creator_config`, whose
        ``sis_type`` IS such a choice.

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
            if f.name not in _TRANSIENT_FIELDS and not f.name.startswith(_ADVISORY_FIELD_PREFIXES)
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

    def _guarded_field_write(
        self,
        updates: dict[str, object],
        *,
        allowed: frozenset[str],
        refuse_when_unreadable: bool,
        subject: str,
        writer: str,
    ) -> bool:
        """THE write discipline every NAMED settings writer shares. Applies, then saves.

        Extracted from :meth:`identity_save` (plan 0044 S3), because its obligations were
        never identity's: they are what ANY narrow, named write into this shared,
        hand-editable, atomically-replaced document owes, and a second family
        (``creator_*``) would otherwise have re-implemented them by eye. Five obligations,
        discharged in THIS order — the order is itself part of the contract:

        1. **KEY and VALUE are validated for every update BEFORE any ``setattr``.** The key
           must be a member of ``allowed`` — membership, NOT ``hasattr``, because
           ``hasattr`` also answers True for every METHOD on this class, so
           ``identity_save(identity_save="x")`` would bind a string over the bound method
           and permanently disable the choke point on that instance while reporting
           success. The value must satisfy the field's declared type via
           :func:`_value_fits`, because ``config.json`` is re-read through that same
           predicate: a mis-typed value written here (``identity_email=None`` →
           ``"identity_email": null``) makes the WHOLE document UNREADABLE on the next
           load, dropping the admin's district, folders and delivery settings to defaults.
           Both raise loudly — a caller passing the wrong type has a bug, and a
           silently-coerced value would look like a save that worked. Validation runs to
           COMPLETION first, so a bad key late in a multi-field call cannot leave the
           instance half-mutated.
        2. **The unreadable-profile guard lives on the WRITE, not on a boot-time
           decision** — for the families where it applies (``refuse_when_unreadable``). It
           re-checks :meth:`settings_unreadable` on THIS instance at write time: a gate
           predicate evaluated at launch can be stale, a config can have become unreadable
           since, and an instance can reach a caller by another route. Checked BEFORE
           anything is applied, so a refusal has no mutation to undo.
        3. **Apply, then ``save()``** — ONE save for the whole payload, never one per
           field, so no caller can leave a torn pair of fields on disk (see
           :meth:`activate_creator_config` for why that matters).
        4. **The two handled failures are swallowed, logged and REPORTED** through the
           return value (:class:`SettingsOverwriteRefused`, ``OSError``). These writers
           carry advisory or admin-triggered data; a failed save may never trap the admin
           in front of the app.
        5. **The INSTANCE is rolled back on ANY failure.** All-or-nothing applies at two
           levels and the second is easy to miss: this ``AppConfig`` is SHARED (the
           Settings scroll hands ONE instance to the folders, schedule, delivery and
           identity sections), so a refused write that left the new value on the object
           would (a) render a value the disk does not have and (b) get committed silently
           by the next unrelated ``Save`` on any other section.

        ``refuse_when_unreadable`` is a REQUIRED keyword with NO default — the house rule
        for a safety-relevant parameter (``write_overlay(overwrite=)``,
        ``_store_run_record(dry_run=)``): "may this write replace settings we FAILED TO
        READ?" is answered explicitly at every call site, never inherited from a default.
        ``subject`` is the admin-facing log NOUN and ``writer`` the developer-facing API
        name used in the two raise messages, so each wrapper keeps its own wording
        (identity's are pinned byte-identical by ``tests/test_app_config_identity.py``,
        which is the equivalence proof for this extraction).

        Returns ``True`` iff the settings were written.

        Raises ``AttributeError`` for an unwritable key and ``TypeError`` for a value that
        would corrupt the settings document. Neither is caught here: they are programming
        errors in the CALLER, not runtime conditions to degrade around.
        """
        field_types = _settings_field_types()
        for name, value in updates.items():
            if name not in allowed:
                raise AttributeError(
                    f"{writer}() only writes {_allowed_fields_phrase(allowed)} "
                    f"({', '.join(sorted(allowed))}); got {name!r}. "
                    "Route any other settings change through AppConfig.save()."
                )
            if not _value_fits(value, field_types[name]):
                raise TypeError(
                    f"{writer}() got a {type(value).__name__} for {name!r}, which is declared "
                    f"{field_types[name]!r}. Writing it would make config.json unreadable on the next "
                    "load, dropping the admin's district, folders and delivery settings to defaults."
                )

        # Checked BEFORE anything is applied: on an unreadable profile there is no write to
        # attempt, so there must be no mutation to undo either.
        if refuse_when_unreadable and self.settings_unreadable():
            logger.warning(
                "Not saving %s: the settings file could not be read this session, "
                "so the saved settings are left untouched. We'll ask again next time.",
                subject,
            )
            return False

        previous = {name: getattr(self, name) for name in updates}
        for name, value in updates.items():
            setattr(self, name, value)
        try:
            self.save()
        except SettingsOverwriteRefused:
            # Belt-and-braces for an advisory family (the check above should have caught
            # it), and the REAL path for a non-advisory one, which reaches save()'s own
            # rule deliberately. Already logged at WARNING by save().
            self._restore(previous)
            return False
        except OSError as exc:
            logger.warning("Could not save %s (%s). Nothing else was changed.", subject, exc)
            self._restore(previous)
            return False
        return True

    def identity_save(self, **updates: object) -> bool:
        """THE choke point for every identity write. Applies ``identity_*`` fields, then saves.

        **Every future identity writer must go through here** — the launch page, the Home
        cards, the Settings section, and anything Phase 2 adds. Not a convenience wrapper:
        it names the one allowlist identity may write and the one posture it takes, and a
        caller that hand-rolls ``cfg.identity_email = ...; cfg.save()`` silently drops
        every obligation :meth:`_guarded_field_write` discharges (read it for the
        mechanism — validation before mutation, the write-time unreadable guard, the
        single save, the swallowed failures and the instance rollback).

        The two identity-specific facts this wrapper decides:

        * ``refuse_when_unreadable=True`` — identity is ADVISORY. It scopes a picker and
          echoes on Help; it changes NOTHING about which district converts, from where, to
          where, or when. So an identity-only save on a profile we FAILED TO READ must be
          refused rather than trading the admin's real folders / district / delivery
          settings for invented blanks. The gate simply asks again next launch.
        * the allowlist is :data:`_IDENTITY_FIELD_NAMES` — so a NON-identity field is
          refused with the same loudness and this entry point can never become a back door
          for writing ``sis_type``. Identity resolution must NEVER rewrite the configured
          district: a product rule, enforced structurally at the single write point rather
          than trusted to every future call site.

        Returns ``True`` iff the settings were written. Callers persist best-effort and
        then continue regardless — never gate entry into the app on this.

        Raises ``AttributeError`` for an unwritable key and ``TypeError`` for a value that
        would corrupt the settings document (see :meth:`_guarded_field_write`).
        """
        return self._guarded_field_write(
            dict(updates),
            allowed=_IDENTITY_FIELD_NAMES,
            refuse_when_unreadable=True,
            subject="who looks after this sync",
            writer="identity_save",
        )

    def creator_save(self, **updates: object) -> bool:
        """THE choke point for every ``creator_*`` write (plan 0044). Prunes, then saves.

        The self-service twin of :meth:`identity_save`, with the same posture for the same
        reason: the resume token and the tested-fact are ADVISORY (see the
        ``_ADVISORY_FIELD_PREFIXES`` comment — nothing in the ETL, CLI or scheduler reads
        either, and both degrade to "ask again"), so ``refuse_when_unreadable=True`` and a
        failed write reports rather than raises.

        Two things this wrapper adds over the shared discipline:

        * **it refuses any non-``creator_*`` key loudly**, ``sis_type`` most of all. A
          creator flow that could pin the district here would BE the back door that
          obligation exists to prevent — activation is a separate, deliberately named and
          validated method (:meth:`activate_creator_config`).
        * **it PRUNES ``creator_verified`` on every save**, so the map is bounded by the
          number of configs actually in the user's ``mappings/`` dir rather than growing
          for the life of the install (see :func:`_pruned_verified_configs`). The pruned
          map is part of the payload, so it is snapshotted and rolled back with everything
          else if the save fails.

        Returns ``True`` iff the settings were written.
        """
        payload: dict[str, object] = dict(updates)
        pending = payload.get("creator_verified", self.creator_verified)
        # A non-dict is left EXACTLY as passed so the shared validation rejects it loudly;
        # pruning it would be inventing a value for a caller with a bug.
        payload["creator_verified"] = _pruned_verified_configs(pending) if isinstance(pending, dict) else pending
        return self._guarded_field_write(
            payload,
            allowed=_CREATOR_FIELD_NAMES,
            refuse_when_unreadable=True,
            subject="your district setup progress",
            writer="creator_save",
        )

    def activate_creator_config(self, *, sis_type: str, digest: str) -> bool:
        """Make a self-service district the one this install converts — in ONE save.

        The moment plan 0044 exists for, and the only place a creator flow may touch
        ``sis_type``. Deliberately NOT routed through :meth:`creator_save`, which must
        refuse a non-``creator_*`` key or become exactly the back door that refusal
        prevents; and deliberately not a bare ``cfg.sis_type = ...; cfg.save()``, which
        drops validation and rollback on the write that matters most.

        **ONE save, three fields.** Two saves would leave a torn state — the district
        active while the resume token still stands — and the resumed flow's Discard would
        then delete a LIVE config. So the district, the cleared token and the tested-fact
        are one atomic payload.

        **NON-advisory (``refuse_when_unreadable=False``), because it carries a chosen
        setting.** ``sis_type`` off its default is exactly what
        :meth:`_carries_chosen_settings` counts, so on an UNREADABLE profile ``save()``
        writes — after :func:`_preserve_unreadable_predecessor` quarantines the bytes it
        replaces — precisely as it does for the wizard's standard District step. That
        parity is the claim: an admin who just tested a district they set up themselves is
        not treated worse than one who picked a shipped mapping. It also inherits that
        path's known residual (an unmerged single-section write; see :meth:`save` and the
        ROADMAP entry) — named, not solved, and recoverable from the quarantine copy.

        Both arguments are validated BEFORE anything is applied: ``sis_type`` through
        :func:`src.utils.validators.validate_sis_type` (this value becomes a ``--sis``
        argument and a filename stem) and ``digest`` through
        :func:`src.utils.validators.is_config_digest` — a malformed digest stored here
        would read as ABSENT on the way back out, silently asking for another test run.

        Returns ``True`` iff the settings were written; ``False`` on the two handled save
        failures, with the instance rolled back (the caller stays on the step it was on
        and can press again).

        Raises ``ValueError`` for an invalid ``sis_type`` or ``digest`` — a caller reaching
        here without a tested config has a bug, and a coerced value would activate
        something the gate never checked.
        """
        from src.utils.validators import is_config_digest, validate_sis_type

        validated_sis = validate_sis_type(sis_type)
        if not is_config_digest(digest):
            raise ValueError(
                "activate_creator_config() needs the sha256 digest of the RESOLVED config that "
                "passed the test conversion (64 lowercase hex characters). A malformed digest "
                "would read as absent on the next load, so it is refused rather than stored."
            )
        verified = _pruned_verified_configs(self.creator_verified)
        verified[validated_sis] = digest
        return self._guarded_field_write(
            {"sis_type": validated_sis, "creator_pending_sis": "", "creator_verified": verified},
            allowed=_ACTIVATION_FIELD_NAMES,
            refuse_when_unreadable=False,
            subject="the district you set up",
            writer="activate_creator_config",
        )

    def _restore(self, previous: dict[str, Any]) -> None:
        """Put back the pre-call values after a refused/failed identity write.

        The other half of "nothing else was changed": the message is only true if the
        in-memory object agrees with the disk, because this instance outlives the call and
        is shared across every Settings section.
        """
        for name, value in previous.items():
            setattr(self, name, value)

    def identity_clear(self) -> ClearOutcome:
        """Remove who looks after this sync — from ``config.json`` AND its quarantine copies.

        "Blank clears" is only true if the value is actually gone from the disk, and
        ``config.json`` is not the only place it lives: :func:`_preserve_unreadable_predecessor`
        copies an unreadable settings file aside as ``config.corrupt-<ts>.json`` byte-for-byte,
        and nothing else prunes those copies. A stored address therefore survives in every
        quarantine snapshot taken after it was written, in the same directory, indefinitely
        — so an "erasure" that only empties ``config.json`` leaves the address readable on
        disk (the containment model recorded in ``tests/test_identity_pii_guards.py``).

        Three fields, one act, because they are one question:

        * ``identity_email`` and ``identity_sd_number`` are the answer;
        * ``identity_prompt_dismissed`` is reset to ``False`` so the ask can come BACK. Left
          set, clearing would wedge the states: no stored identity, and no surface willing
          to ask for one again.

        **The purge is gated on there having BEEN something to erase**, and that gate is the
        whole reason this method is more than two lines. The quarantine copies are the
        admin's settings-recovery snapshots, and the population most likely to own one is
        the population whose settings file went unreadable — who may well have no stored
        identity at all. Deleting their only recoverable copy because they pressed Save on
        an already-empty field would be destroying data to accomplish nothing. So
        ``had_identity`` is read BEFORE the write, and a no-op clear touches no file but
        ``config.json``.

        Ordering is deliberate for the same reason: purge only AFTER a confirmed write. A
        refused save (an UNREADABLE profile) means nothing was cleared, so there is nothing
        to follow through on. The purge itself is best-effort — a locked file logs and is
        skipped, which is exactly why the outcome carries ``remaining``: the caller may not
        tell the admin the copies are gone when some of them are still there.

        **The residual this does NOT cover, stated rather than implied:** a crash between
        ``mkstemp`` and ``os.replace`` inside :func:`_atomic_write_text` can leave a
        ``.config.json.<rand>.tmp`` staging file holding a full settings payload. Those are
        removed on every handled failure and are not matched by :data:`_QUARANTINE_GLOB`, so
        an erasure does not sweep them; a power loss at exactly the wrong instant is the
        only way to make one, and it survives until something else writes the profile.

        Returns a :class:`ClearOutcome` — ``cleared`` (the settings were written, the same
        contract as :meth:`identity_save`, which this routes through as the ONE identity
        write path), plus how many quarantine copies were ``removed`` and how many are
        ``remaining``. A caller that only needs the boolean reads ``.cleared``.
        """
        had_identity = bool(self.identity_email.strip() or self.identity_sd_number.strip())
        cleared = self.identity_save(
            identity_email="",
            identity_sd_number="",
            identity_prompt_dismissed=False,
        )
        if not (cleared and had_identity):
            return ClearOutcome(cleared=cleared, removed=0, remaining=0)
        removed, remaining = _purge_quarantined_settings()
        return ClearOutcome(cleared=True, removed=removed, remaining=remaining)

    def sftp_is_configured(self) -> bool:
        """Return True if SFTP has been enabled and configured."""
        if not (self.sftp_enabled and self.sftp_host and self.sftp_username and self.sftp_remote_path):
            return False
        from src.utils.validators import ALLOWED_SFTP_HOSTS

        return self.sftp_host.strip().lower() in ALLOWED_SFTP_HOSTS


# The exact set of field names :meth:`AppConfig.identity_save` may write. DERIVED from the
# dataclass, never hand-listed, so a new ``identity_*`` field is writable the moment it is
# declared and a renamed one cannot leave a stale entry behind.
#
# Membership is the guard — deliberately NOT ``hasattr``. Every METHOD on this class also
# answers ``hasattr`` True, so a ``hasattr``-based check accepts
# ``identity_save(identity_save="x")``: it would bind a string over the bound method and
# permanently disable the choke point on that instance while reporting success.
_IDENTITY_FIELD_NAMES: frozenset[str] = frozenset(
    f.name for f in fields(AppConfig) if f.name.startswith(_IDENTITY_FIELD_PREFIX)
)

# The exact set :meth:`AppConfig.creator_save` may write — derived the same way, for the
# same reasons (plan 0044).
_CREATOR_FIELD_NAMES: frozenset[str] = frozenset(
    f.name for f in fields(AppConfig) if f.name.startswith(_CREATOR_FIELD_PREFIX)
)

# What :meth:`AppConfig.activate_creator_config` may write: the creator family PLUS the one
# non-advisory field it exists to set. Written as the creator set plus ``sis_type`` rather
# than a hand-listed triple so the two can never drift, and stated as an allowlist even
# though that method builds its own payload — the guard is what makes "activation touches
# nothing else" checkable rather than asserted.
_ACTIVATION_FIELD_NAMES: frozenset[str] = _CREATOR_FIELD_NAMES | frozenset({"sis_type"})


def _allowed_fields_phrase(allowed: frozenset[str]) -> str:
    """Name an allowlist the way its writer's error message should read.

    DERIVED from the allowlist rather than passed in, so a wrapper cannot describe itself
    as writing a family it does not write. A single-family allowlist reads as
    ``"identity_* settings fields"`` (which is what keeps ``identity_save``'s message
    byte-identical through the extraction); a mixed one — ``activate_creator_config``'s
    ``creator_* + sis_type`` — cannot honestly claim a prefix, so it says only "these",
    and the message lists every member either way.
    """
    prefixes = {name.split("_", 1)[0] for name in allowed if "_" in name}
    if len(prefixes) == 1 and all("_" in name for name in allowed):
        return f"{next(iter(prefixes))}_* settings fields"
    return "these settings fields"


def _pruned_verified_configs(verified: dict[str, str]) -> dict[str, str]:
    """Drop ``creator_verified`` entries whose config is no longer a USER-dir file.

    Bounds the map by the number of configs actually in the admin's ``mappings/`` dir: a
    district that was set up, tested and later discarded leaves an entry that can never be
    consulted again, and without a prune the map grows for the life of the install.

    The origin test is ``"user"``, not merely "resolves": a stale entry whose id now hits a
    BUNDLED config must not survive as a tested-fact about a file the admin never tested.
    Nothing here decides activation — the digest comparison does — so pruning can only ever
    cost an extra test run.

    TOTAL by construction. The import is deferred (this module must not drag the YAML
    loader into every settings read) and ANY failure — an unreadable mappings dir, an
    exotic id, a loader change — prunes NOTHING and returns the map unchanged rather than
    blocking the write it is only tidying. Returns a NEW dict, never the argument, so the
    caller's rollback snapshot still holds the original object.
    """
    try:
        from src.config.loader import resolve_config_path

        kept: dict[str, str] = {}
        for sis_id, digest in verified.items():
            resolved = resolve_config_path(sis_id)
            if resolved is not None and resolved.origin == "user":
                kept[sis_id] = digest
    except Exception as exc:  # noqa: BLE001 — a tidy-up may never block a settings write
        logger.debug("Could not check which tested configs still exist (%s); none were pruned.", exc)
        return dict(verified)
    dropped = len(verified) - len(kept)
    if dropped:
        # Counts only: an id is not PII, but the diagnostic has no use for it either.
        logger.debug("Dropped %d tested-config record(s) whose mapping file is gone.", dropped)
    return kept


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


def _purge_quarantined_settings() -> tuple[int, int]:
    """Unlink every ``config.corrupt-*.json`` beside the settings file. → ``(removed, remaining)``.

    The counterpart to :func:`_preserve_unreadable_predecessor`, and the ONLY thing that
    ever removes what it wrote. Called from :meth:`AppConfig.identity_clear`, because those
    copies hold a byte-for-byte duplicate of whatever ``config.json`` contained when they
    were taken — including an identity the admin has just asked us to forget.

    Best-effort and never fatal: a copy held open by an editor or an AV scanner logs at
    WARNING and is left alone. The clear itself has already succeeded by the time this runs
    (see :meth:`AppConfig.identity_clear` for why that order matters), so a failure here
    costs a stale copy, never the clear.

    **Both numbers are returned because both are load-bearing to the admin.** A caller that
    only knew "the purge ran" would say "we deleted the older copies" even when every single
    unlink failed — the exact shape of over-claim the trust architecture exists to prevent.
    """
    removed = 0
    remaining = 0
    directory = config_file_path().parent
    try:
        stale = sorted(directory.glob(_QUARANTINE_GLOB))
    except OSError as exc:
        logger.warning("Could not list older settings copies in %s (%s); none were removed.", directory, exc)
        # Unknown, and "unknown" must not read as "none left" — a listing we could not do
        # is a folder we cannot claim is clean.
        return 0, 1
    for path in stale:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            remaining += 1
            logger.warning("Could not remove the older settings copy %s (%s); it is still on disk.", path.name, exc)
    if removed:
        logger.info("Removed %d older settings copy/copies alongside %s.", removed, config_file_path().name)
    return removed, remaining


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
