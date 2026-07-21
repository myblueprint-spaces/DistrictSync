"""Convert-screen output-folder visibility + run/deliver gates — the path-BEARING trust logic.

COUNTED (not coverage-omitted): the questions the Convert surface must answer honestly —
"where will my files go?", "may this run fire?", "may I deliver what's on disk, and how
fresh is it?", and "open that folder" — are decided here, unit-tested and single-sourced.
The button's ``disabled`` state and the pre-run caption both read these predicates, so no
silent fallback can creep back in (D9/D10): no district → no alphabetical ``configs[0]``
guess; no output folder → no quiet write into the *input* folder.

**Deliver-from-disk (0034 Slice 2):** the standalone "Deliver the files in your output
folder" action gates through :func:`standalone_deliver_state` (hidden with nothing to
deliver / no delivery setup; a calm route-to-Setup state when only the credential is
missing) and carries the honest vintage line from :func:`freshness_fact` — the admin
always knows how old the files that would ship are.

**ONE narrowed derivation, three readers (FIX-4).** The readiness gate, the freshness
line and the delivery payload MUST describe the same files, or the surface promises a
delivery it cannot perform. :func:`deliverable_files` is that single derivation: the
active district's configured entity CSVs (:func:`configured_output_entities`)
intersected with what is committed on disk, plus that subset's newest mtime. The gate
reads ``present``, the vintage line reads ``newest_mtime_iso``, and ``deliver_job``
ships ``filenames`` — so an output folder holding only ANOTHER config's CSVs (or a
parked spreadsheet) hides the action instead of rendering a READY card whose only
possible outcome is a failed-delivery record for a delivery that was never possible.
Totality is split deliberately: :func:`configured_output_entities` is STRICT (the
payload path must fail loud on a config fault, never mislabel it a delivery failure)
while :func:`deliverable_files` is TOTAL (a broken partner config degrades the view to
"nothing to deliver", never a crashed screen — ``mapping_catalog.summarize_config``'s
pattern).

**Run identity + the anomaly-ack binding (FIX-2):** :class:`RunIdentity` / :func:`run_identity`
/ :func:`ack_authorizes` name WHICH run an action refers to, so the "I've reviewed this —
convert anyway" acknowledgement is a capability scoped to the run it reviewed rather than a
bare boolean any later run could spend. Paired with ``interaction_state``'s ``awaiting_ack``
axis (the view half), this keeps the last write-gate honest.

**Convert cold-state + interaction sweep (0035 W3b):** the pre-setup routing decision
(:func:`show_setup_first_card` + :func:`setup_first_copy`), the mode-aware unset-output
caption (``resolved_output_caption``'s ``setup_completed`` axis), the saved-vs-picked
district heads-up (:func:`district_mismatch_note`), the softened missing-files copy
(:func:`missing_files_copy`), and the busy/idle disabled table
(:func:`interaction_state`) all live HERE, pure and tested — the screen only paints them.

**Why a SEPARATE module from ``convert_result``:** ``ConvertResult`` is the PII-free
result model and must stay *path-free* (a roster path can never enter a summary object).
The output-folder path, by contrast, is app-owned config (never student PII) and belongs
at the view layer — so the path-bearing decisions live HERE, cleanly apart from the
path-free result model. No ``flet`` import; the OS-open helper is effectful-but-mockable
(mirrors ``filepicker.check_writable``).
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404 - launching the OS file browser; no shell, list-form args
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from src.config.loader import load_config
from src.etl.loader import DataLoader
from src.etl.pipeline import configured_entity_order
from src.ui_flet.humanize import friendly_district_name, friendly_timestamp

logger = logging.getLogger(__name__)


def output_dir_is_set(output_dir: str | None) -> bool:
    """Whether a non-blank output folder is configured (the run-gate + caption read this)."""
    return bool((output_dir or "").strip())


def can_run_convert(*, district_chosen: bool, output_dir_set: bool, input_valid: bool) -> bool:
    """The Convert run-gate: an explicit district AND a set output folder AND a valid input.

    Single-sources the gate the Convert button encodes so no silent fallback can return
    (D9/D10). Runtime single-flight (the ``JobRunner``'s ``can_start``) is an orthogonal
    view concern layered on top of this input-completeness gate.
    """
    return bool(district_chosen) and bool(output_dir_set) and bool(input_valid)


def resolved_output_caption(output_dir: str | None, *, setup_completed: bool = True) -> str:
    """The pre-run, read-only caption naming where files will be written (or the unset prompt).

    Set → "Files will be written to <dir> — change it in Settings." (pre-run visibility).
    Unset → MODE-AWARE honesty (0035 W3b): after setup has completed the fix lives on the
    graduated **Settings** surface ("Set your output folder in Settings first …"); BEFORE
    setup completes there is no Settings scroll yet — the Setup *wizard* owns the folder,
    so the caption routes there instead of naming a surface that doesn't exist. Never a
    silent write into the input folder either way.
    """
    if output_dir_is_set(output_dir):
        return f"Files will be written to {(output_dir or '').strip()} — change it in Settings."
    if setup_completed:
        return "Set your output folder in Settings first — DistrictSync doesn't know where to write yet."
    return "Finish setup first — the Setup wizard will set your output folder."


def show_setup_first_card(*, setup_completed: bool, output_dir_set: bool, district_saved: bool) -> bool:
    """Whether Convert leads with the routed "Finish setup first" card (0035 W3b).

    Pre-setup with the run essentials missing → the fix genuinely lives in Setup, so the
    screen leads with a calm card that routes there instead of a dead disabled button.
    A PARTIALLY-set-up install whose essentials are already in place (saved district AND
    output folder) keeps the working form un-nagged — Convert is usable there today, and
    blocking it behind wizard completion would be a regression, not a kindness.
    """
    return not setup_completed and not (output_dir_set and district_saved)


def setup_first_copy() -> tuple[str, str]:
    """The (title, body) copy for the pre-setup "Finish setup first" card — fixed, jargon-free.

    The body names what Setup provides (district + folders) and reassures on effort; the
    view adds the routed "Open Setup" action when the shell injects ``on_navigate`` (and
    stays honest without it — the body stands alone, no dangling "click below").
    """
    return (
        "Finish setup first",
        "DistrictSync needs to know your district and folders before it can convert. "
        "Setup walks you through it in a few minutes.",
    )


def district_mismatch_note(selected: str | None, saved: str | None, *, config_dir: Path | None = None) -> str | None:
    """The amber heads-up when the per-run district pick differs from the saved district.

    ``None`` (no note) when there is no explicit pick, no saved district, or they match —
    the note only fires on a REAL override, so the common path stays quiet. The saved
    district renders via ``friendly_district_name`` (TOTAL — falls back to the raw id,
    never raises); ``config_dir`` is that helper's test seam, threaded through. The note
    names only config-derived district identity — never PII.
    """
    sel = (selected or "").strip()
    sav = (saved or "").strip()
    if not sel or not sav or sel == sav:
        return None
    display = friendly_district_name(sav, config_dir=config_dir)
    return (
        f"This differs from your saved district ({display}) — "
        "this one-time conversion won't change your saved settings."
    )


def missing_files_copy() -> tuple[str, str]:
    """The softened (heading, reassurance) copy over the expected-but-missing file chips.

    0035 W3b: the old "Expected files not found in this folder:" read as a fault. A missing
    source file is legitimate (per-entity skip-on-empty is by design), so the heading is a
    calm observation and the reassurance line states the honest consequence — the run still
    works, and whatever a missing file feeds is skipped, never guessed.
    """
    return (
        "Not found yet — your district's extracts usually include:",
        "You can still convert — anything a missing file feeds is skipped, not guessed.",
    )


@dataclass(frozen=True)
class ConvertInteraction:
    """The disabled-flags the Convert surface paints for one (gates, running, ack) state.

    - ``convert_disabled`` — the Convert button: blocked while a job runs (single-flight,
      matching ``JobRunner``'s guard so the VIEW never offers a dead click) or while any
      input gate is unmet. Deliberately NOT blocked while an acknowledgement is pending:
      starting over is a legitimate escape hatch, and ``_start_convert`` clears the card.
    - ``inputs_disabled`` — the district dropdown + input-folder picker: locked while a job
      runs (the job snapshotted its inputs at start; editing mid-run would show a form that
      no longer matches the work in flight) AND while an anomaly acknowledgement is pending
      (the card asks about ONE specific run — see :func:`ack_authorizes`).
    """

    convert_disabled: bool
    inputs_disabled: bool


def interaction_state(*, gates_ok: bool, job_running: bool, awaiting_ack: bool = False) -> ConvertInteraction:
    """The Convert interaction table (0035 W3b) — pure, the view just paints it.

    Single-sources the busy/idle disabled decisions so the button state can never drift
    from the ``JobRunner`` guard: a running job disables everything (no dead clicks, no
    double-start, no mid-run input edits); idle re-derives the button from the input gates
    (``can_run_convert``) and re-enables the inputs.

    ``awaiting_ack`` (FIX-2) freezes the inputs while the "some files look much smaller than
    usual" card is on screen. The card asks the admin to approve ONE identified run; leaving
    the district dropdown and the folder picker live while it waits let the approval land on
    a run nobody reviewed. This is the VIEW half of that binding — the enforcing half is
    :func:`ack_authorizes` at the write-gate, so a future view edit cannot reopen the hole.
    """
    return ConvertInteraction(
        convert_disabled=job_running or not gates_ok,
        inputs_disabled=job_running or awaiting_ack,
    )


@dataclass(frozen=True)
class RunIdentity:
    """WHICH run a Convert action refers to: the (district, input folder) pair.

    The output folder is deliberately NOT part of the identity — it is not a per-run
    control (it lives in Settings, is captured once at screen build, and the run-gate
    already refuses an unset one), so including it would invalidate acknowledgements for
    a change the admin cannot make from this screen.

    Fields hold the values AS GIVEN (stripped only), so the identity doubles as a faithful
    record of what was reviewed and can be handed straight back as the re-run's arguments;
    the case/separator normalisation lives in the comparison, never in the stored value.
    """

    district: str
    input_dir: str


def run_identity(district: str | None, input_dir: str | None) -> RunIdentity:
    """The identity of a Convert run — TOTAL (``None``/blank collapse to ``""``)."""
    return RunIdentity(district=(district or "").strip(), input_dir=(input_dir or "").strip())


def _comparable(identity: RunIdentity) -> tuple[str, str]:
    """The normalised comparison key: exact district id + an OS-appropriate folder key.

    ``normpath`` collapses ``.``/``..``/duplicate + trailing separators and ``normcase``
    applies the platform's own case rule (a no-op on POSIX, case-folding + separator
    flattening on Windows) — so ``C:\\GDE\\`` and ``c:/gde`` are the same folder on Windows
    and two different folders on Linux, which is exactly what each filesystem means.
    The district is a config id — compared exactly, never case-folded.
    """
    return (identity.district, os.path.normcase(os.path.normpath(identity.input_dir)))


def ack_authorizes(ack: RunIdentity | None, current: RunIdentity) -> bool:
    """Whether a pending anomaly acknowledgement authorizes THIS run's write (FIX-2).

    The acknowledgement is a capability scoped to the run it reviewed: "I looked at the
    numbers for district D out of folder F and they're fine." It authorizes a write only
    when the run about to happen IS that run.

    FAIL-CLOSED by construction — ``None`` never authorizes, and neither does an
    unidentifiable ack (a blank district or blank folder), so a half-built token can never
    be mistaken for consent. The anomaly gate is the last safety net between a truncated
    export and a collapsed roster reaching SpacesEDU; the safe default is "review it again".
    """
    if ack is None or not ack.district or not ack.input_dir:
        return False
    return _comparable(ack) == _comparable(current)


class DeliverReadiness(Enum):
    """The standalone deliver-from-disk affordance's gated state (0034 Slice 2).

    - ``HIDDEN`` — nothing to offer: delivery isn't set up, or nothing in the output
      folder is DELIVERABLE (an action with nothing to act on hides, mirroring the
      post-run deliver card's show-only-when-deliverable pattern).
    - ``NEEDS_CREDENTIAL`` — delivery is set up and files exist, but no password is
      stored/readable for this account → the calm route-to-Setup card (the existing
      ``_delivery_not_ready_card`` precedent), never a button that would instantly fail.
    - ``READY`` — the deliver action shows, freshness-labelled.
    """

    HIDDEN = "hidden"
    NEEDS_CREDENTIAL = "needs_credential"
    READY = "ready"


def standalone_deliver_state(
    *,
    sftp_configured: bool,
    credential_present: bool,
    csvs_present: bool,
) -> DeliverReadiness:
    """The standalone deliver gate: SFTP configured AND deliverable files AND a readable credential.

    Single-sources the decision the Convert screen renders (pure — the view supplies the
    three facts). Ordering matters for honesty: with no delivery setup or nothing to
    deliver the affordance HIDES entirely; only a missing credential earns the explanatory
    not-ready state (it is one Setup visit away from working).

    ``csvs_present`` MUST be ``DeliverableFiles.present`` — the district-narrowed fact, never
    a bare "the folder has some ``*.csv``" (FIX-4). The gate decides whether to OFFER the
    action, so it has to be keyed on the SAME set the action would ship; a wider fact renders
    a card whose click can only fail.
    """
    if not sftp_configured or not csvs_present:
        return DeliverReadiness.HIDDEN
    if not credential_present:
        return DeliverReadiness.NEEDS_CREDENTIAL
    return DeliverReadiness.READY


def _top_level_csvs(output_dir: str | None) -> list[Path]:
    """The committed top-level ``*.csv`` files in the output folder (TOTAL — never raises).

    Top-level only (non-recursive), matching the directory SCOPE ``SFTPUploader.upload_csvs``
    globs, so ``archive_<ts>/`` / ``.bak_<ts>/`` contents are invisible to both. This is the
    raw candidate set ONLY — it is deliberately NOT a "what would ship" answer: the uploader
    additionally narrows its glob by the delivery manifest, so every caller must pass this
    through :func:`_deliverable_paths` before claiming anything about delivery (FIX-4 — the
    unnarrowed read was exactly the dead-end gate). A blank/unreadable folder → ``[]``.
    """
    target = (output_dir or "").strip()
    if not target:
        return []
    try:
        return [p for p in Path(target).glob("*.csv") if p.is_file()]
    except OSError:
        return []


def _deliverable_paths(entity_names: Iterable[str], output_dir: str | None) -> list[Path]:
    """The on-disk paths of the config's entity CSVs — the narrowing every deliver fact shares.

    The entity→filename spelling comes from ``DataLoader.output_filenames`` (one rule, shared
    with the write path and the stale-output detector), so a manifest can never drift from
    what was written.
    """
    candidates = DataLoader.output_filenames(entity_names)
    return [p for p in _top_level_csvs(output_dir) if p.name in candidates]


def _newest_mtime_iso(paths: Iterable[Path]) -> str:
    """The newest mtime across ``paths`` as an ISO string (``""`` when none/unstattable).

    TOTAL: an unstattable file is skipped rather than raising, so a folder being written
    underneath the read degrades to a calm "recently" downstream instead of a crash.
    """
    newest = 0.0
    for path in paths:
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    if newest <= 0:
        return ""
    return datetime.fromtimestamp(newest).isoformat(timespec="seconds")


def configured_output_entities(sis_type: str, *, config_dir: Path | None = None) -> list[str]:
    """The entities the district config is CONFIGURED to produce — STRICT (raises on a fault).

    The single district→entity-names step behind every deliver-from-disk fact: the delivery
    payload (``deliver_job``) and the view's readiness/freshness gate call THIS, so the offer
    and the action can never disagree about which files are in play.

    Derived by ``configured_entity_order`` (``entity_order`` filtered by ``enabled_entities``)
    — never raw ``mappings.keys()``: ``_base`` inheritance leaves inherited-but-disabled
    entities in the mapping, and treating those as deliverable is how a different config's
    CSV sharing the output dir would get shipped (CLAUDE.md, "Output Targeting").

    Raises whatever ``load_config`` raises (``FileNotFoundError`` / ``ValueError``) — a config
    fault on the payload path is a setup fault and must fail LOUD, never be folded into "we
    couldn't send your files". The view's TOTAL wrapper is :func:`deliverable_files`.
    ``config_dir`` is ``load_config``'s test seam, threaded through.
    """
    raw = load_config(sis_type, config_dir).to_raw_dict()
    return configured_entity_order(raw.get("mappings", {}), raw.get("global_config", {}))


@dataclass(frozen=True)
class DeliverableFiles:
    """What deliver-from-disk would ACTUALLY ship from the output folder, and how fresh it is.

    ``filenames`` is the active config's entity CSVs present on disk (the delivery manifest);
    ``newest_mtime_iso`` is the newest mtime **of exactly those files** — never a foreign
    CSV's, so the vintage line can't quote a file that would never ship. ``present`` is the
    readiness fact ``standalone_deliver_state`` consumes: empty ⇒ the action HIDES, because
    ``upload_csvs`` refuses an empty manifest and a retry would re-derive the same emptiness
    (an unsatisfiable loop, mislabelled as an upload failure, that also persisted a FAILED
    delivery record — FIX-4).
    """

    filenames: frozenset[str]
    newest_mtime_iso: str

    @property
    def present(self) -> bool:
        """Whether anything would actually be delivered (the readiness gate's fact)."""
        return bool(self.filenames)


def deliverable_files(
    sis_type: str | None,
    output_dir: str | None,
    *,
    config_dir: Path | None = None,
) -> DeliverableFiles:
    """The deliverable set + its vintage for one district/folder — TOTAL (never raises).

    The view-side entry point: it runs in a RENDER path, where a partner's broken config must
    degrade calmly rather than crash the Convert screen (``mapping_catalog.summarize_config``'s
    established pattern). A blank district, an unloadable config, or an unreadable folder all
    collapse to the EMPTY set — which hides the deliver action, the honest outcome in every
    one of those cases (there is nothing this install could successfully send).

    Degrading here does not soften the payload path: ``deliver_job`` still resolves the same
    set through the STRICT :func:`configured_output_entities` and fails loud on a config fault.
    """
    district = (sis_type or "").strip()
    if not district:
        return DeliverableFiles(filenames=frozenset(), newest_mtime_iso="")
    try:
        entities = configured_output_entities(district, config_dir=config_dir)
    except Exception as exc:  # noqa: BLE001 - total: a render path degrades, it never crashes
        # Logged (never swallowed silently) but never surfaced: the admin-facing consequence
        # is simply "no deliver action", and Mapping is where a broken config is reported.
        logger.warning("Could not resolve the deliverable set for district '%s': %s", district, exc)
        return DeliverableFiles(filenames=frozenset(), newest_mtime_iso="")
    paths = _deliverable_paths(entities, output_dir)
    return DeliverableFiles(
        filenames=frozenset(p.name for p in paths),
        newest_mtime_iso=_newest_mtime_iso(paths),
    )


def deliverable_manifest(entity_names: Iterable[str], output_dir: str | None) -> set[str]:
    """The deliver-from-disk delivery manifest: the ACTIVE CONFIG's entity CSVs on disk.

    Deliver-from-disk has no ``outputs`` to vouch for (it ships an EARLIER build), so the
    authoritative set is *the entities the active config would produce* — resolved by the
    caller via ``configured_entity_order`` (enabled-entities-derived, never raw
    ``mappings.keys()``) — intersected with what is actually committed in the folder.
    Two consequences, both deliberate:

    * a foreign ``*.csv`` (a backup, a spreadsheet export) is NOT in the config's entity
      set, so it never egresses — the same guarantee the run-and-deliver path gets;
    * a config-owned entity with no CSV yet is simply absent from the manifest rather
      than a hard failure — the folder legitimately holds only what past runs wrote.

    The entity→filename spelling comes from ``DataLoader.output_filenames`` (one rule,
    shared with the write path and the stale-output detector).
    """
    return {p.name for p in _deliverable_paths(entity_names, output_dir)}


def freshness_fact(mtime_iso: str, *, now: datetime | None = None) -> str:
    """The plain-language vintage line for the deliver card + confirm dialog.

    "Files last built 2 hours ago." — built on ``humanize.friendly_timestamp`` (TOTAL:
    an empty/unparseable ``mtime_iso`` reads "recently", never a raw string or a crash).
    Fed by ``DeliverableFiles.newest_mtime_iso``, so the vintage always describes the files
    that would actually ship (FIX-4). ``now`` is the test seam, threaded straight through.
    """
    return f"Files last built {friendly_timestamp(mtime_iso, now=now)}."


def open_folder(path: str) -> bool:
    """Open the OS file browser at ``path`` (best-effort; NEVER raises).

    Per-OS dispatch: ``os.startfile`` on Windows, ``open`` on macOS, ``xdg-open`` on Linux.
    ``path`` is the app-owned output folder from config — never student PII, never
    shell-interpolated (list-form args / no ``shell=True``). Returns whether the open was
    dispatched (blank path or a dispatch failure → ``False``, logged at WARNING).
    """
    target = (path or "").strip()
    if not target:
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(target)  # nosec B606 - app-owned output dir, no shell, no user interpolation
        elif sys.platform == "darwin":
            subprocess.run(["open", target], check=False)  # nosec B603 B607 - fixed cmd, list-form, no shell
        else:
            subprocess.run(["xdg-open", target], check=False)  # nosec B603 B607 - fixed cmd, list-form, no shell
    except OSError as exc:
        logger.warning("Could not open the output folder: %s", exc)
        return False
    return True
