"""The creator surface (plan 0044 S3): "Set up my district".

An admin whose district ships no mapping answers four questions here, presses Continue
(which WRITES an overlay into their own profile and changes nothing else), then on the
creator-only "Your files" step runs a TEST conversion that writes nothing and — only if it
worked — chooses "Use this district", the ONE act that makes it the district this install
converts.

VIEW glue (coverage-omitted). Every decision this surface makes is COUNTED elsewhere: the
form + gate + stored-fact rules and the two field rules in ``ui_flet/config_editor.py``,
the step shape in ``setup_flow`` (creator mode), the write/delete/digest in
``config/authoring``, the three settings writes in ``AppConfig`` (``creator_save`` /
``activate_creator_config``). This module is assembly + I/O.

**The dependency runs wizard → creator and NEVER back** (plan 0044 S3 review, SHOULD 5).
Nothing here may reach back into the wizard module: S6's Mapping surface becomes
:func:`build_creator`'s SECOND host, and a creator that depended on the wizard would make
the wizard the only place it can live. Shared view glue therefore sits in ``components.py``
(``inflight_row``), and the two facts the wizard needs about a pending creator setup —
:func:`pending_creator_sis` and :func:`creator_gate_current` — are exported from HERE for it
to read. The rule is greppable on purpose: no import line in this file may name the wizard.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import flet as ft

from src.config.app_config import AppConfig
from src.config.authoring import (
    ALLOWED_BASES,
    CREATOR_ENTITIES,
    current_digest,
    delete_overlay,
    derive_sis_id,
    folded_filename,
    overlay_path,
    read_authored_with,
    resolved_digest,
    validate_source_filename,
    write_overlay,
)
from src.ui_flet import components, tokens
from src.ui_flet.config_editor import (
    CEDS_GRADE_ORDER,
    CreatorForm,
    FileFormRow,
    GateOutcome,
    GateState,
    SourceFileSlot,
    base_label,
    derive_domains,
    distinct_source_files,
    file_form_rows,
    files_primary_action,
    gate_outcome_for,
    has_unsaved_renames,
    humanize_config_error,
    overlay_staleness,
    pending_renames,
    renames_from_resolved,
    sd_number_from_text,
    seed_entities,
    split_domains,
    stored_verified_digest,
    validate_domains,
    verified_is_current,
)
from src.ui_flet.filepicker import validate_output_dir
from src.ui_flet.identity_gate import (
    resolve_sd_number,
    sd_number_digits,
    stored_identity_domain,
)
from src.ui_flet.job_runner import GateRefused, JobRunner, creator_gate_job
from src.ui_flet.mapping_catalog import reset_catalog_cache
from src.ui_flet.verdict import Verdict
from src.utils.version import app_version

logger = logging.getLogger(__name__)


#: The two surfaces ``build_creator`` renders. A ``Literal`` rather than an Enum for the
#: same reason ``setup_flow.FlowMode`` is one: the caller already holds a plain string and
#: the value needs no behaviour of its own.
CreatorStage = Literal["forms", "files"]

# ---- the forms ----------------------------------------------------------- #
CREATOR_ENTRY_LABEL = "Set up my district"
CREATOR_START_TITLE = "Your starting point"
CREATOR_START_PROMPT = (
    "Pick the standard MyEd BC mapping closest to what your district sends. DistrictSync fills in "
    "the rest from it, and you confirm the details below."
)
CREATOR_START_FIELD_LABEL = "Starting point"
CREATOR_IDENTITY_TITLE = "Your district"
CREATOR_IDENTITY_PROMPT = (
    "Confirm your district's details. These name your setup in the district list — nothing here is sent anywhere."
)
CREATOR_SD_FIELD_LABEL = "District number"
CREATOR_NAME_FIELD_LABEL = "District name"
CREATOR_DOMAINS_FIELD_LABEL = "Staff email domains"
CREATOR_DOMAINS_HELPER = (
    "Separate more than one with a comma, like sd48.bc.ca. Leave it blank if your district has none."
)
CREATOR_DOMAIN_INVALID_NOTE = (
    "One of those email domains isn't a plain domain name like sd48.bc.ca. Please check the list."
)
CREATOR_SD_INVALID_NOTE = "Enter your district number as digits, like 48."
CREATOR_NAME_REQUIRED_NOTE = "Add your district's name so it reads clearly in the district list."
CREATOR_ENTITIES_TITLE = "Which files to produce"
CREATOR_ENTITIES_PROMPT = (
    "Choose the CSV files this district should produce. Your starting point's usual set is ticked."
)
CREATOR_ENTITIES_EMPTY_NOTE = "Choose at least one file to produce."
CREATOR_GRADES_TITLE = "Which grades"
CREATOR_GRADES_INHERIT_LABEL = "Use my starting point's grades"
CREATOR_GRADES_PROMPT = "Which grades should be rostered? Students in every other grade are left out."
CREATOR_HOMEROOM_PROMPT = "Which of those grades get one homeroom class instead of their timetable classes?"
CREATOR_GRADES_EMPTY_NOTE = "Choose at least one grade to roster, or use your starting point's grades."
CREATOR_CONTINUE_LABEL = "Continue"
CREATOR_DISCARD_LABEL = "Discard this district"
CREATOR_DISCARDED_NOTE = "Discarded. Nothing was kept, and your district list is back as it was."
CREATOR_WRITE_FAILED_NOTE = "We couldn't save your district's mapping just now — nothing was changed."
CREATOR_RESUME_REFUSED_NOTE = (
    "Your district's mapping is saved, but we couldn't remember where you got to. If you close "
    "DistrictSync you'll answer these questions again."
)
CREATOR_ACTIVATE_FAILED_NOTE = (
    "We couldn't switch this computer over to your district just now — nothing was changed. Please try again."
)
CREATOR_FINISH_NEEDS_GATE_NOTE = (
    'Go back to "Your files", run the test conversion, then choose "Use this district" — after that you can finish.'
)

# ---- the "Your files" gate step ------------------------------------------ #
#: The creator-only gate step's title. The wizard reads it from here: its ``_STEP_TITLES``
#: is the single source of every step title and must name this one.
FILES_STEP_TITLE = "Your files"
#: S3's ``FILES_INHERITED_NOTE`` said "the standard MyEd BC names your starting point uses",
#: which stops being true the moment a row carries this district's own name. This says what
#: the step is FOR instead, in both states.
FILES_INTRO_NOTE = (
    "DistrictSync looks for these files in your input folder. If your district's files are named "
    "differently, set the name yours uses beside each one."
)
FILES_MISSING_NOTE = (
    "We can't see these ones in your input folder. A test conversion carries on without them, and "
    "whatever they feed comes out empty. If your district calls them something else, set that name above."
)
FILES_KEEP_STANDARD_LABEL = "Use the standard name"
FILES_TYPED_NAME_LABEL = "Type the name your district uses if it isn't in the list"
FILES_USED_FOR_PREFIX = "Used for: "
FILES_SCHOOL_YEAR_CLAUSE = " It also tells DistrictSync which school year the data is from."
FILES_SAVE_LABEL = "Save these file names"
FILES_SAVED_NOTE = "Saved. DistrictSync will look for these file names from now on."
FILES_UNSAVED_NOTE = "These file names aren't saved yet. Save them, then run a test conversion against them."
FILES_NAME_INVALID_NOTE = (
    "One of those file names can't be used. Give just the file name as it appears in your input "
    'folder — no folder path, and none of : \\ / < > " | ? *'
)
FILES_NAME_DUPLICATE_NOTE = (
    "Two of these files would end up with the same name. Each one needs the name your district actually uses for it."
)
FILES_NAME_IS_STANDARD_NOTE = (
    "One of those names is the standard name of another file on this list — and that file has a "
    "name of its own here, so the two would swap places. Please check them."
)
GATE_RUN_LABEL = "Run a test conversion"
GATE_RUNNING_CAPTION = "Testing your files… nothing is written and nothing is sent."
GATE_PASSED_HEADLINE = "The test conversion worked"
GATE_PASSED_DETAIL = "Here's how many rows each file would hold. Nothing was written and nothing was sent."
GATE_FAILED_HEADLINE = "The test conversion didn't finish"
GATE_REFUSED_NO_OUTPUT_NOTE = "Pick your output folder on the step before this one first — the test didn't run."
GATE_STALE_VERSION_NOTE = "A different version of DistrictSync set this district up, so please run the test again."
GATE_STALE_BASE_NOTE = (
    "The standard mapping your district builds on has changed since it was set up, so please run the test again."
)
GATE_CONFIRM_LABEL = "Use this district"
GATE_RERUN_LABEL = "Test it again"
GATE_ACTIVATED_NOTE = "This computer now converts your district."
GATE_RESAVED_NOTE = "Saved. Your district's file names changed, so please run the test conversion once more."

#: Plain-language names for the seven authorable entities (the vocabulary map — an admin
#: reads "Families", never ``Family``, and never a raw entity key).
_ENTITY_LABELS: dict[str, str] = {
    "Students": "Students",
    "Staff": "Staff",
    "Family": "Families",
    "Classes": "Classes",
    "Enrollments": "Enrollments",
    "CourseInfo": "Course list",
    "StudentCourses": "Student courses",
}


def creator_shipped_note(number: str) -> str:
    """The starting-point card's note when DistrictSync already ships that district's mapping.

    The owner's decision (plan 0044 S3, open question 1) is to ALLOW a self-service district
    beside a shipped one — the district whose export legitimately differs from the shipped
    assumption is the case support hits most — while RECOMMENDING the shipped mapping first,
    because when one exists it usually is the right answer.
    """
    return (
        f"DistrictSync already ships a mapping for SD{number}. If your MyEd BC files match the standard "
        "layout, that one is usually the right choice — your own setup sits beside it in the district list."
    )


def _entity_label(name: str) -> str:
    """Plain-language entity name, falling back to the key (TOTAL — a new entity still renders)."""
    return _ENTITY_LABELS.get(name, name)


def _resolved_base(base: str) -> object | None:
    """The RESOLVED starting-point config, or ``None`` when it cannot be loaded. TOTAL.

    Only used to SEED the entity ticks; a failure there is not a reason to refuse the form
    (``write_overlay`` resolves the base itself and fails loudly with a bounded note).
    """
    try:
        from src.config.loader import load_config

        return load_config(base)
    except Exception:  # noqa: BLE001 - total: the seed falls back to every authorable entity
        logger.warning("Could not resolve the starting-point config %r for the creator form.", base)
        return None


def _seed_entity_ticks(base: str) -> set[str]:
    """The entity ticks a fresh form opens with: the resolved base's own list (else all of them)."""
    resolved = _resolved_base(base)
    if resolved is None:
        return set(CREATOR_ENTITIES)
    return set(seed_entities(resolved))  # type: ignore[arg-type]


def creator_form_for_new(cfg: AppConfig) -> CreatorForm:
    """A FRESH creator form, prefilled from what this install already knows. TOTAL.

    The district number comes from the launch page's not-listed answer (``identity_sd_number``)
    and the domains from ``config_editor.derive_domains`` over the stored identity domain plus
    the vendored table — both PREFILLS, correctable in the field they land in. Every step is
    guarded: a prefill that cannot be validated is simply not offered.
    """
    base = ALLOWED_BASES[0]
    # ``or 0`` = "no usable number yet", the form's own default: an unusable prefill is
    # simply not offered, and the field the admin types into is the correction.
    sd_number = sd_number_from_text(getattr(cfg, "identity_sd_number", "") or "") or 0
    form = CreatorForm(base=base, sd_number=sd_number)
    with contextlib.suppress(ValueError):
        form = form.with_domains(derive_domains(stored_identity_domain(cfg), sd_number))
    return form


def creator_form_from_overlay(sis_id: str) -> CreatorForm:
    """Rebuild a creator form from the overlay already on disk (RESUME). TOTAL.

    Reads the RESOLVED config (so an inherited value reads exactly as it will convert) plus the
    overlay's ``authored_with`` provenance for the starting point — the one fact the resolved
    model drops. Any failure answers a default form for the district number in the id: the
    overlay is still on disk and the gate will report whatever is wrong with it, so a form that
    cannot be rehydrated must not block the step.
    """
    sd_number = sd_number_from_text(sis_id) or 0
    fallback = CreatorForm(sd_number=sd_number)
    try:
        from src.config.loader import load_config

        config = load_config(sis_id)
        provenance = read_authored_with(sis_id)
        base = provenance.base if (provenance is not None and provenance.base in ALLOWED_BASES) else ALLOWED_BASES[0]
        raw_global = config.to_raw_dict().get("global_config", {})
        form = CreatorForm(
            base=base,
            sd_number=sd_number,
            district_name=config.district_name or "",
            domains=tuple(config.district_domains or ()),
            entities=tuple(name for name in CREATOR_ENTITIES if name in set(config.global_config.enabled_entities)),
        )
        rostered = raw_global.get("student_rostering_grades")
        if isinstance(rostered, list) and rostered:
            homeroom = raw_global.get("homeroom_grades")
            keep = [code for code in (homeroom or []) if code in set(rostered)]
            form = form.with_rostered(rostered).with_homeroom(keep)
        # The filename form's own answer, recovered from the config on disk (plan 0044 S4):
        # whatever the district's config names at a base reference site IS the rename map,
        # so the rows on screen and the map the next Save writes are ONE value. Per-entry
        # suppression, because a HAND-EDITED name the filename boundary refuses must not
        # cost the admin the rest of a rehydrated form.
        resolved_base = _resolved_base(base)
        if resolved_base is not None:
            resumed = renames_from_resolved(resolved_base, config)  # type: ignore[arg-type]
            for original, renamed in dict(resumed.renames).items():
                try:
                    form = form.with_rename(original, renamed)
                except ValueError:
                    logger.warning("A source file name in %r's mapping is not a usable filename.", sis_id)
        return form
    except Exception:  # noqa: BLE001 - total: a broken overlay is the gate's story, not the form's
        logger.warning("Could not rehydrate the creator form for %r; opening the defaults.", sis_id)
        return fallback


def _resolved_config(sis_id: str) -> object | None:
    """The district's OWN resolved config (base + overlay), or ``None``. TOTAL.

    Separate from :func:`_resolved_base`, which resolves the STARTING POINT: the two answer
    different questions on the Files step (what a file is CALLED in the base, versus what
    this district's config reads today) and both are needed to show one row per file.
    """
    try:
        from src.config.loader import load_config

        return load_config(sis_id)
    except Exception:  # noqa: BLE001 - total: the gate reports what is wrong with the config
        logger.warning("Could not resolve the self-service config %r.", sis_id)
        return None


def _creator_expected_files(sis_id: str) -> tuple[str, ...]:
    """The source filenames ``sis_id``'s resolved config expects. TOTAL — ``()`` on any failure."""
    config = _resolved_config(sis_id)
    if config is None:
        return ()
    try:
        from src.etl.pipeline import advisory_expected_files

        return tuple(advisory_expected_files(config))
    except Exception:  # noqa: BLE001 - total: no list is better than a wrong list
        logger.warning("Could not resolve the expected source files for %r.", sis_id)
        return ()


@dataclass(frozen=True)
class _FilesModel:
    """Everything the Files step needs from DISK, resolved ONCE per mount.

    One bundle rather than two functions because both facts come from the SAME pair of
    resolved configs (the starting point and this district's own): a second entry point
    would load and validate both again on every mount for one extra tuple.

    Attributes:
        slots: one row per source file this district reads (see :func:`distinct_source_files`).
        divergent: base filenames this district's config names in more than one way — a
            hand edit only. The surface opens DIRTY on those so the unsaved warning prompts
            the repairing Save (plan 0044 S4 review, SHOULD 4).
    """

    slots: tuple[SourceFileSlot, ...] = ()
    divergent: tuple[str, ...] = ()


def _creator_files_model(base: str, sis_id: str) -> _FilesModel:
    """The Files step's rows — one per source file this district reads. TOTAL — empty on failure.

    Two configs, two jobs:

    * the SLOTS come from the resolved STARTING POINT, because a rename is keyed by the
      base's own filename (that is what ``authoring._build_renames`` propagates from), and
      because the row set must not move when a name changes;
    * ``expected`` comes from the DISTRICT's own config, so
      ``pipeline.advisory_expected_files`` stays the single source for "which files matter"
      WITH the narrowing this district's entity selection and grade scopes earn — then it is
      translated back into base-name space through the renames the config on disk already
      expresses. BOTH spellings are offered to the filter, so a hand-edited config that
      diverges on one file still gets its row (the row is where that gets repaired).

    Empty when the starting point cannot be resolved — the state in which ``write_overlay``
    would fail too, and the gate carries the diagnosis.
    """
    resolved_base = _resolved_base(base)
    if resolved_base is None:
        return _FilesModel()
    try:
        from src.etl.pipeline import advisory_expected_files

        current = _resolved_config(sis_id) if (sis_id or "").strip() else None
        divergent: tuple[str, ...] = ()
        if current is None:
            expected = list(advisory_expected_files(resolved_base))
        else:
            resumed = renames_from_resolved(resolved_base, current)  # type: ignore[arg-type]
            divergent = resumed.divergent
            back = {new: original for original, new in dict(resumed.renames).items()}
            names = list(advisory_expected_files(current))
            expected = [*names, *(back[name] for name in names if name in back)]
        slots = distinct_source_files(resolved_base, expected=expected)  # type: ignore[arg-type]
        return _FilesModel(slots=slots, divergent=divergent)
    except Exception:  # noqa: BLE001 - total: no rows is better than wrong rows
        logger.warning("Could not derive the source file rows for %r.", sis_id)
        return _FilesModel()


def _folder_filenames(path: str) -> tuple[str, ...]:
    """The filenames sitting in ``path`` (Convert's precedent). TOTAL — ``()`` on any failure."""
    try:
        return tuple(sorted(entry.name for entry in Path(path).iterdir() if entry.is_file()))
    except (OSError, ValueError):
        return ()


def _grade_chips(
    codes: tuple[str, ...],
    chosen: set[str],
    on_toggle: Callable[[str, bool], None],
) -> ft.Control:
    """A wrapped row of grade tick boxes over the CEDS vocabulary (built via ``check_row``)."""
    return ft.Row(
        wrap=True,
        spacing=tokens.space_md,
        run_spacing=tokens.space_sm,
        controls=[
            components.check_row(
                code,
                value=code in chosen,
                on_toggle=lambda ticked, code=code: on_toggle(code, ticked),
            )
            for code in codes
        ],
    )


def build_creator(  # pragma: no cover - Flet view glue
    page: ft.Page,
    *,
    cfg: AppConfig,
    sis_id: str,
    form: CreatorForm,
    on_written: Callable[[str, CreatorForm, str], None],
    on_files_saved: Callable[[CreatorForm, str], None],
    on_activated: Callable[[], None],
    on_discarded: Callable[[], None],
    stage: CreatorStage = "forms",
    pending: dict[str, str] | None = None,
) -> ft.Control:
    """The creator's own surface — the HOST seam (plan 0044 S3 §3.5).

    Same shape and reasoning as ``build_setup(page, *, on_schedule_changed, on_complete)``:
    the host owns what happens after each payoff, this surface owns the work. S3 wires ONE
    host (the wizard: ``stage="forms"`` is the District step's creator branch,
    ``stage="files"`` is the creator-only "Your files" step); S6's Mapping surface is the
    second, and naming the callbacks now is what stops it becoming a second wizard.

    Args:
        cfg: the SHARED ``AppConfig`` — every write goes through ``creator_save`` /
            ``activate_creator_config``, so this surface can never touch ``sis_type``
            except through the one validated activation method.
        sis_id: the pending district's config id, or ``""`` before the first write.
        form: what the admin has answered so far (frozen; the host stores the latest).
        on_written: ``(sis_id, form, note)`` after a SUCCESSFUL overlay write — the token
            and the catalog invalidation have already happened. ``note`` is a plain sentence
            for the host to show on the surface it moves to (only ever
            ``CREATOR_RESUME_REFUSED_NOTE``: the file is on disk and the step is re-visitable,
            so only resume convenience was lost and the host still advances).
        on_files_saved: ``(form, note)`` after a SUCCESSFUL save of the file-name form —
            the overlay is on disk and the catalog invalidation has already happened. A
            SEPARATE callback from ``on_written`` deliberately (plan 0044 S4): that one
            ADVANCES a step, and a callback whose meaning depends on which step the host
            is showing is how a second wizard starts. The host stores the form, drops any
            memoised gate fact and re-renders the step it is on; ``note`` is
            :data:`FILES_SAVED_NOTE`, or :data:`GATE_RESAVED_NOTE` when the save landed on
            a district this computer is already converting.
        on_activated: after ``sis_type`` genuinely became this district in ONE save.
        on_discarded: after the overlay was deleted, the token cleared and the catalog
            invalidated — the host re-mounts its standard surface.
        stage: which surface to render (see :data:`CreatorStage`).
        pending: the filename form's pending rename map, OWNED BY THE HOST and mutated in
            place — the one piece of state that has to outlive a re-mount (plan 0044 S4
            review, BLOCKING 2). A host that re-renders this step (the wizard does, on every
            hop and after every save) would otherwise hand back a surface whose rows had
            forgotten what was picked, while its own footer Continue stayed open and
            advanced past it. NOT a sixth callback: the host does not need to be TOLD when a
            row changes, it needs to be able to ASK — which it does with
            ``config_editor.has_unsaved_renames(pending, form.renames)``, the same
            comparison this surface tiers its Save on, so the two can never disagree about
            whether something is pending. ``None`` (S6's Mapping host, which owns no step
            footer) means this surface keeps a private dict for its own lifetime.

    Never fires a callback on a failure: a refused write, a ``None`` digest and a refused
    activation all keep the admin where they are behind a bounded note.
    """
    host = ft.Column(spacing=tokens.space_xl)
    # The rows' own map: the host's when it owns one, else this surface's for its lifetime.
    # Seeded from the config on disk only while UNTOUCHED — every row the admin has answered
    # leaves a key behind (``""`` for "use the standard name"), so an empty map is the one
    # state that cannot be a choice, and re-seeding a touched map would discard the picks
    # BLOCKING 2 exists to keep.
    row_names: dict[str, str] = {} if pending is None else pending
    if not row_names:
        row_names.update(dict(form.renames))
    st: dict[str, object] = {
        "form": form,
        "sd_text": str(form.sd_number) if form.sd_number else "",
        "name_text": form.district_name,
        "domains_text": ", ".join(form.domains),
        "entities": set(form.entities) if form.entities else _seed_entity_ticks(form.base),
        "note": "",
        # The filename form (S4). ``pending`` is the RAW per-row string the admin has
        # chosen or typed (``""`` = keep the standard name); ``saved`` is what the config on
        # disk says, so "is there anything unsaved?" is one comparison rather than a flag
        # anything could forget to set. The map itself belongs to the HOST when it passes
        # one (see the ``pending`` argument) and is mutated IN PLACE, never rebound.
        # ``model`` is the mount-time memo: the resolved base, the expected-file list and
        # the divergence report are I/O and cannot change while the step is on screen.
        "pending": row_names,
        "saved": dict(form.renames),
        "model": None,
        # Divergence is a fact about the config on DISK, so it is read from the model once
        # and then OWNED here: a Save repairs it, and a memo that could not be cleared would
        # leave the surface permanently dirty. ``None`` = not yet read.
        "divergent": None,
        "gate": GateOutcome(state=GateState.NOT_RUN),
        "running": False,
        "activated": False,
        "mounted": False,
        # The stored-gate fact, probed at most ONCE per mount (``None`` = not yet probed).
        # It is I/O — ``current_digest`` loads and validates the resolved config — and
        # ``_files_controls`` runs on every render (a test run alone paints three), so a
        # re-derivation per render would re-read the whole config each time.
        "gate_current": None,
    }
    runner = JobRunner()

    def _render() -> None:
        host.controls = list(_forms_controls() if stage == "forms" else _files_controls())
        if st["mounted"]:
            page.update()

    def _set_note(text: str) -> None:
        st["note"] = text
        _render()

    def _note_row() -> list[ft.Control]:
        """The inline note, never colour-alone (a glyph rides beside the words)."""
        if not st["note"]:
            return []
        return [
            ft.Row(
                spacing=tokens.space_sm,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=18, color=tokens.color_status_failed),
                    ft.Text(str(st["note"]), size=tokens.type_body, color=tokens.color_status_failed, expand=True),
                ],
            )
        ]

    def _discard_row() -> ft.Control:
        """The text-tier escape, on EVERY creator surface (never a dead end)."""
        return ft.Row(
            controls=[components.text_button(CREATOR_DISCARD_LABEL, _discard, icon=ft.Icons.DELETE_OUTLINE_ROUNDED)]
        )

    # ---- discard --------------------------------------------------------- #
    def _discard(_e: ft.ControlEvent | None = None) -> None:
        pending = (sis_id or "").strip()
        if pending:
            try:
                delete_overlay(pending)  # False = nothing there; idempotent success, not an error
            except ValueError:
                logger.warning("Refused to delete a non-self-service config while discarding.", exc_info=True)
            except OSError:
                logger.warning("Could not delete the self-service overlay while discarding.", exc_info=True)
        cfg.creator_save(creator_pending_sis="")
        reset_catalog_cache()
        on_discarded()

    # ---- the four forms -------------------------------------------------- #
    def _on_base(e: ft.ControlEvent) -> None:
        chosen = (e.control.value or ALLOWED_BASES[0]).strip()
        if chosen not in ALLOWED_BASES:
            return
        st["form"] = st["form"].with_base(chosen)  # type: ignore[union-attr]
        st["entities"] = _seed_entity_ticks(chosen)  # a new starting point re-seeds its own list
        _render()

    def _on_sd(e: ft.ControlEvent) -> None:
        st["sd_text"] = e.control.value or ""

    def _on_name(e: ft.ControlEvent) -> None:
        st["name_text"] = e.control.value or ""

    def _on_domains(e: ft.ControlEvent) -> None:
        st["domains_text"] = e.control.value or ""

    def _on_entity(name: str, ticked: bool) -> None:
        ticks: set[str] = st["entities"]  # type: ignore[assignment]
        ticks.add(name) if ticked else ticks.discard(name)

    def _on_inherit_grades(inherit: bool) -> None:
        current: CreatorForm = st["form"]  # type: ignore[assignment]
        if inherit:
            st["form"] = current.with_rostered(None)  # clears BOTH halves of the chain
        else:
            # Opening the question must ANSWER IT AS IT STANDS — never narrow anything. So the
            # rostered set opens at the WHOLE CEDS vocabulary (behaviourally identical to
            # inheriting: it includes ``UG`` and ``Other``, so no student is left out), and only
            # the HOMEROOM half is seeded from the starting point's own list. The chain therefore
            # starts VALID (homeroom ⊆ rostered) and the admin NARROWS from a full roster.
            #
            # Seeding ``rostered`` from the homeroom list instead — as this did until the S3
            # review — silently de-rostered every grade the starting point does not give a
            # homeroom to: on ``myedbc`` merely opening the question and pressing Continue wrote
            # ``student_rostering_grades: [IT…07]`` plus the ``homeroom`` class sentinel, and
            # every grade 8-12 student left the roster without anyone being asked.
            seed = _starting_point_grades(current.base)
            st["form"] = current.with_rostered(CEDS_GRADE_ORDER).with_homeroom(seed)
        _render()

    def _starting_point_grades(base: str) -> tuple[str, ...]:
        """The starting point's own homeroom grades (the seed for the grades question). TOTAL."""
        resolved = _resolved_base(base)
        if resolved is None:
            return ()
        declared = getattr(resolved.global_config, "homeroom_grades", None) or ()  # type: ignore[attr-defined]
        return tuple(code for code in CEDS_GRADE_ORDER if code in set(declared))

    def _on_rostered(code: str, ticked: bool) -> None:
        current: CreatorForm = st["form"]  # type: ignore[assignment]
        chosen = set(current.rostered or ())
        chosen.add(code) if ticked else chosen.discard(code)
        if not chosen:
            _set_note(CREATOR_GRADES_EMPTY_NOTE)
            return
        st["note"] = ""
        st["form"] = current.with_rostered(sorted(chosen, key=CEDS_GRADE_ORDER.index))
        _render()

    def _on_homeroom(code: str, ticked: bool) -> None:
        current: CreatorForm = st["form"]  # type: ignore[assignment]
        chosen = set(current.homeroom or ())
        chosen.add(code) if ticked else chosen.discard(code)
        st["form"] = current.with_homeroom(sorted(chosen, key=CEDS_GRADE_ORDER.index))
        _render()

    def _continue(_e: ft.ControlEvent | None = None) -> None:
        """The creator District step's Continue IS the write (plan 0044 S3 §3.4).

        Cheap field problems answer with their OWN note and never attempt a write; anything
        the authoring layer refuses answers with the bounded write-failed note plus
        ``humanize_config_error``'s category — never the exception text, which quotes values.
        """
        sd_number = sd_number_from_text(str(st["sd_text"]))
        if not sd_number:
            _set_note(CREATOR_SD_INVALID_NOTE)
            return
        name = str(st["name_text"]).strip()
        if not name:
            _set_note(CREATOR_NAME_REQUIRED_NOTE)
            return
        try:
            domains = validate_domains(split_domains(str(st["domains_text"])))
        except ValueError:
            _set_note(CREATOR_DOMAIN_INVALID_NOTE)  # the note NEVER echoes what was typed
            return
        ticks: set[str] = st["entities"]  # type: ignore[assignment]
        if not ticks:
            _set_note(CREATOR_ENTITIES_EMPTY_NOTE)
            return
        try:
            candidate = (
                st["form"]  # type: ignore[union-attr]
                .with_district(sd_number=sd_number, district_name=name)
                .with_domains(domains)
                .with_entities(sorted(ticks, key=CREATOR_ENTITIES.index))
            )
            new_sis = derive_sis_id(candidate.sd_number)
            write_overlay(candidate.to_overlay_spec(), overwrite=True)
        except Exception as exc:  # noqa: BLE001 - bounded copy on screen, full trace in the log
            logger.warning("Could not write the self-service mapping overlay.", exc_info=True)
            _set_note(f"{CREATOR_WRITE_FAILED_NOTE} {humanize_config_error(exc)}")
            return
        st["form"] = candidate
        st["note"] = ""
        # A CORRECTED district number renames the config, so the overlay this step was already
        # holding is now an ORPHAN — nothing points at it, it rides every picker, and Discard
        # (which only ever knows the pending id) would leave it behind, making
        # ``CREATOR_DISCARDED_NOTE``'s "Nothing was kept" false. Deleted here, AFTER the new
        # file is safely on disk, under exactly the suppression ``_discard`` uses: a failure to
        # tidy up is never a reason to fail a write that succeeded.
        old_sis = (sis_id or "").strip()
        if old_sis and old_sis != new_sis:
            try:
                delete_overlay(old_sis)
            except ValueError:
                logger.warning("Refused to delete a non-self-service config while renumbering.", exc_info=True)
            except OSError:
                logger.warning("Could not delete the superseded self-service overlay.", exc_info=True)
        # The resume token, then the catalog invalidation (S2's rule: the UI caller invalidates,
        # right after a successful write) — in that order, so a refused token still leaves every
        # picker showing the district whose file is now on disk.
        token_ok = cfg.creator_save(creator_pending_sis=new_sis)
        reset_catalog_cache()
        on_written(new_sis, candidate, "" if token_ok else CREATOR_RESUME_REFUSED_NOTE)

    def _forms_controls() -> list[ft.Control]:
        current: CreatorForm = st["form"]  # type: ignore[assignment]
        start_rows: list[ft.Control] = [
            ft.Text(CREATOR_START_TITLE, size=tokens.type_section, weight=ft.FontWeight.W_700, color=tokens.color_text),
            ft.Text(CREATOR_START_PROMPT, size=tokens.type_body, color=tokens.color_muted),
        ]
        shipped_for = _shipped_district_number(str(st["sd_text"]) or str(current.sd_number or ""))
        if shipped_for:
            start_rows.append(
                ft.Text(creator_shipped_note(shipped_for), size=tokens.type_body, color=tokens.color_muted)
            )
        start_rows.append(
            ft.Dropdown(
                label=CREATOR_START_FIELD_LABEL,
                value=current.base,
                options=[ft.dropdown.Option(key=base, text=base_label(base)) for base in ALLOWED_BASES],
                on_select=_on_base,  # Dropdown's value-change is on_select on 0.85.3
                border_color=tokens.color_border,
            )
        )

        identity_rows: list[ft.Control] = [
            ft.Text(
                CREATOR_IDENTITY_TITLE, size=tokens.type_section, weight=ft.FontWeight.W_700, color=tokens.color_text
            ),
            ft.Text(CREATOR_IDENTITY_PROMPT, size=tokens.type_body, color=tokens.color_muted),
            ft.Row(
                spacing=tokens.space_lg,
                controls=[
                    ft.TextField(
                        label=CREATOR_SD_FIELD_LABEL,
                        value=str(st["sd_text"]),
                        width=170,
                        max_length=4,
                        on_change=_on_sd,
                        autofocus=True,
                    ),
                    ft.TextField(
                        label=CREATOR_NAME_FIELD_LABEL,
                        value=str(st["name_text"]),
                        expand=True,
                        max_length=120,
                        on_change=_on_name,
                    ),
                ],
            ),
            ft.TextField(
                label=CREATOR_DOMAINS_FIELD_LABEL,
                value=str(st["domains_text"]),
                helper=CREATOR_DOMAINS_HELPER,  # TextField's helper field is `helper` on 0.85.3
                on_change=_on_domains,
            ),
        ]

        ticks: set[str] = st["entities"]  # type: ignore[assignment]
        entity_rows: list[ft.Control] = [
            ft.Text(
                CREATOR_ENTITIES_TITLE, size=tokens.type_section, weight=ft.FontWeight.W_700, color=tokens.color_text
            ),
            ft.Text(CREATOR_ENTITIES_PROMPT, size=tokens.type_body, color=tokens.color_muted),
            ft.Row(
                wrap=True,
                spacing=tokens.space_xl,
                run_spacing=tokens.space_sm,
                controls=[
                    components.check_row(
                        _entity_label(name),
                        value=name in ticks,
                        on_toggle=lambda ticked, name=name: _on_entity(name, ticked),
                    )
                    for name in CREATOR_ENTITIES
                ],
            ),
        ]

        inherit = current.rostered is None
        grade_rows: list[ft.Control] = [
            ft.Text(
                CREATOR_GRADES_TITLE, size=tokens.type_section, weight=ft.FontWeight.W_700, color=tokens.color_text
            ),
            components.check_row(
                CREATOR_GRADES_INHERIT_LABEL,
                value=inherit,
                on_toggle=_on_inherit_grades,
            ),
        ]
        if not inherit:
            grade_rows.extend(
                [
                    ft.Text(CREATOR_GRADES_PROMPT, size=tokens.type_body, color=tokens.color_muted),
                    _grade_chips(CEDS_GRADE_ORDER, set(current.rostered or ()), _on_rostered),
                    ft.Text(CREATOR_HOMEROOM_PROMPT, size=tokens.type_body, color=tokens.color_muted),
                    _grade_chips(tuple(current.rostered or ()), set(current.homeroom or ()), _on_homeroom),
                ]
            )

        controls: list[ft.Control] = [
            components.card(ft.Column(spacing=tokens.space_md, controls=start_rows)),
            components.card(ft.Column(spacing=tokens.space_md, controls=identity_rows)),
            components.card(ft.Column(spacing=tokens.space_md, controls=entity_rows)),
            components.card(ft.Column(spacing=tokens.space_md, controls=grade_rows)),
        ]
        controls.extend(_note_row())
        controls.append(
            ft.Row(
                spacing=tokens.space_lg,
                controls=[
                    components.primary_button(CREATOR_CONTINUE_LABEL, _continue, icon=ft.Icons.ARROW_FORWARD_ROUNDED),
                ],
            )
        )
        controls.append(_discard_row())
        return controls

    # ---- the filename form (S4) ------------------------------------------ #
    def _model() -> _FilesModel:
        """This district's rows + divergence report, derived ONCE per mount (``st["model"]``)."""
        if st["model"] is None:
            st["model"] = _creator_files_model(str(st["form"].base), sis_id)  # type: ignore[union-attr]
        return st["model"]  # type: ignore[return-value]

    def _slots() -> tuple[SourceFileSlot, ...]:
        return _model().slots

    def _divergent() -> tuple[str, ...]:
        """The base filenames the config on DISK names in more than one way. Owned here.

        Read from the mount-time model once and then held, because a Save REPAIRS it: a
        surface that re-read the memo would stay dirty forever after the repair.
        """
        if st["divergent"] is None:
            st["divergent"] = _model().divergent
        return st["divergent"]  # type: ignore[return-value]

    def _pending_renames() -> dict[str, str]:
        """The rename map the ROWS currently express (``config_editor.pending_renames``)."""
        return pending_renames(st["pending"])  # type: ignore[arg-type]

    def _effective_renames() -> dict[str, str]:
        """The rename map to SHOW: pending, with any refused name replaced by the name in force.

        A typed name the filename boundary refuses is kept in its field — that is what a
        field is for, and correcting it is the admin's next move — but it may not be
        PAINTED as the name in force, offered as a list option, or chipped as present/absent
        (plan 0044 S4 review, NOTE 6): a refused value is not a name this district has, and
        a row that showed it would be answering the wrong question about the wrong file.
        The row falls back to the name the config on disk holds, else the standard name.
        """
        chosen: dict[str, str] = {}
        for original, name in _pending_renames().items():
            try:
                validate_source_filename(name)
            except ValueError:
                in_force = dict(st["saved"]).get(original, "")  # type: ignore[arg-type]
                if in_force:
                    chosen[original] = in_force
                continue
            chosen[original] = name
        return chosen

    def _unsaved() -> bool:
        """Whether anything on screen is not in the config on disk — INCLUDING a hand edit.

        A divergent config (one base file named two ways) has no single saved answer, so the
        rows can only show one of them: the step opens dirty and the unsaved warning asks
        for the Save that repairs it.
        """
        if _divergent():
            return True
        return has_unsaved_renames(st["pending"], st["saved"])  # type: ignore[arg-type]

    def _on_pick_name(original: str, value: str) -> None:
        """A folder file (or "use the standard name") picked from the row's list."""
        st["pending"][original] = value or ""  # type: ignore[index]
        st["note"] = ""
        _render()

    def _on_type_name(original: str, value: str) -> None:
        """A typed name. No re-render WHILE typing — the field owns the caret.

        The tier, the chip and the unsaved warning catch up on ``on_blur`` (below), never
        only on Save: a typed name that left the step reading "run a test conversion" as
        its filled primary would run that test against the config on DISK and pass, and the
        confirm beside it would activate a district whose typed name was never written
        (plan 0044 S4 review, BLOCKING 1).
        """
        st["pending"][original] = value or ""  # type: ignore[index]

    def _save_names(_e: ft.ControlEvent | None = None) -> None:
        """The filename form's ONE write. Cheap local problems answer FIRST, with no write.

        Every write load-backs through the real loader, which is why this is one action for
        the whole form rather than a write per row.
        """
        slots = _slots()
        try:
            candidate: CreatorForm = st["form"]  # type: ignore[assignment]
            for slot in slots:
                candidate = candidate.with_rename(slot.original, str(st["pending"].get(slot.original, "")))  # type: ignore[union-attr]
        except ValueError:
            # Never echoed: the note names the CHECK, and a refused name can carry control
            # characters of its own.
            logger.info("A source file name on the Files step was refused by the filename boundary.")
            _set_note(FILES_NAME_INVALID_NOTE)
            return

        pending = _pending_renames()
        # Two ROWS on one file. Broader than the authoring layer's refusal (which only sees
        # two renames onto one target) because a name typed onto a file another row KEEPS is
        # the same mistake: which columns would win is a question the ETL has no answer for.
        by_name: dict[str, list[str]] = {}
        for row in file_form_rows(slots, renames=pending, present=()):
            by_name.setdefault(folded_filename(row.effective), []).append(row.slot.original)
        if any(len(originals) > 1 for originals in by_name.values()):
            _set_note(FILES_NAME_DUPLICATE_NOTE)
            return
        # The CHAIN shape ``A -> B, B -> C``: reachable from this form, because the folder
        # may well hold another row's standard name. Pre-checked here so the admin gets this
        # sentence rather than the authoring layer's bounded write-failure copy.
        renamed_originals = {folded_filename(original) for original in pending}
        if any(
            folded_filename(name) in renamed_originals and folded_filename(name) != folded_filename(original)
            for original, name in pending.items()
        ):
            _set_note(FILES_NAME_IS_STANDARD_NOTE)
            return

        already = bool(st["activated"]) or _gate_already_passed()
        try:
            write_overlay(candidate.to_overlay_spec(), overwrite=True)
        except Exception as exc:  # noqa: BLE001 - bounded copy on screen, full trace in the log
            logger.warning("Could not save this district's source file names.", exc_info=True)
            _set_note(f"{CREATOR_WRITE_FAILED_NOTE} {humanize_config_error(exc)}")
            return

        st["form"] = candidate
        st["saved"] = dict(candidate.renames)
        # Mutated IN PLACE, never rebound: the map may be the HOST's (see ``pending``), and
        # a host still holding the old object would gate its Continue on a stale answer.
        st["pending"].clear()  # type: ignore[union-attr]
        st["pending"].update(candidate.renames)  # type: ignore[union-attr]
        # Whatever the config on disk disagreed with itself about has just been written one
        # way at every site, so the repair is done and the surface is clean again.
        st["divergent"] = ()
        st["note"] = ""
        # The config that just landed is NOT the one any earlier test conversion ran, so the
        # gate re-opens: the passed outcome, this session's activation flag and the memoised
        # stored-gate fact all describe files this district no longer reads. Nothing is
        # written to settings — the stored digest simply stops matching, which is the
        # hash-keyed fail-safe doing its job (an absent fact can only force another test).
        st["gate"] = GateOutcome(state=GateState.NOT_RUN)
        st["activated"] = False
        st["gate_current"] = None
        reset_catalog_cache()
        on_files_saved(candidate, GATE_RESAVED_NOTE if already else FILES_SAVED_NOTE)

    def _used_for_caption(slot: SourceFileSlot) -> str:
        """The row's caption: what this file feeds, plus the school-year clause when it applies."""
        entities = tuple(dict.fromkeys(entity for entity, _role in slot.references))
        caption = f"{FILES_USED_FOR_PREFIX}{', '.join(_entity_label(name) for name in entities)}."
        return f"{caption}{FILES_SCHOOL_YEAR_CLAUSE}" if slot.names_school_year else caption

    def _name_row(row: FileFormRow, folder: tuple[str, ...]) -> ft.Control:
        """One file: what it is, the name in force, and the two ways to change it.

        ``row.effective`` is the name IN FORCE (see :func:`_effective_renames`), so it is
        what the chip and the list show; the typed field keeps the raw text, refused or not,
        because that is the only place a mistyped name can be corrected.
        """
        slot = row.slot
        selected = "" if row.effective == slot.original else row.effective
        typed = str(st["pending"].get(slot.original, "") or "")  # type: ignore[union-attr]
        offered = list(folder)
        if selected and selected not in offered:
            # A typed name the folder does not hold is still the name in force, so the list
            # has to be able to SHOW it selected rather than silently reading "standard".
            offered.append(selected)
        options = [ft.dropdown.Option(key="", text=FILES_KEEP_STANDARD_LABEL)]
        options.extend(ft.dropdown.Option(key=name, text=name) for name in offered)
        return ft.Column(
            spacing=tokens.space_sm,
            controls=[
                ft.Row(
                    spacing=tokens.space_md,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text(
                            slot.label,
                            size=tokens.type_body,
                            weight=ft.FontWeight.W_600,
                            color=tokens.color_text,
                        ),
                        components.FileChip(row.effective, present=row.present),
                    ],
                ),
                ft.Row(
                    spacing=tokens.space_lg,
                    controls=[
                        ft.Dropdown(
                            label=slot.original,  # the STANDARD name, read-only, labelling its own row
                            value=selected,
                            options=options,
                            expand=True,
                            border_color=tokens.color_border,
                            on_select=lambda e, original=slot.original: _on_pick_name(original, e.control.value or ""),
                        ),
                        ft.TextField(
                            label=FILES_TYPED_NAME_LABEL,
                            value=typed,
                            helper=_used_for_caption(slot),  # TextField's helper field is `helper`
                            expand=True,
                            on_change=lambda e, original=slot.original: _on_type_name(original, e.control.value or ""),
                            # The tier + the warning catch up when the caret leaves (BLOCKING
                            # 1): re-rendering per keystroke would take the caret with it.
                            on_blur=lambda _e: _render(),
                        ),
                    ],
                ),
            ],
        )

    # ---- the gate -------------------------------------------------------- #
    def _output_ok() -> bool:
        return validate_output_dir(str(cfg.output_dir or "")).ok

    def _gate_outcome(*, result: object | None, error: BaseException | None) -> GateOutcome:
        return gate_outcome_for(
            result=result,  # type: ignore[arg-type]
            error=error,
            output_dir_valid=_output_ok(),
            expected_files=_creator_expected_files(sis_id),
            present_files=_folder_filenames(str(cfg.input_dir or "")),
        )

    def _run_gate(_e: ft.ControlEvent | None = None) -> None:
        """Run the TEST conversion off the UI thread — exactly Convert's ``JobRunner`` shape.

        REFUSES while anything on screen is unsaved (plan 0044 S4 review, BLOCKING 1). The
        run reads the config on DISK, so a test against names it does not hold reports on
        the wrong files — and passing it would put a confirm beside a verdict about a
        district nobody has tested as it now reads. ``files_primary_action`` already keeps
        this button off the primary tier in that state; this is the load-bearing half,
        because a button is still pressable at its outlined tier.
        """
        if _unsaved():
            _set_note(FILES_UNSAVED_NOTE)
            return
        runner.state.reset()
        st["running"] = True
        st["note"] = ""
        st["gate"] = GateOutcome(state=GateState.RUNNING)
        _render()

        def _on_done(result: object) -> None:
            st["running"] = False
            st["gate"] = _gate_outcome(result=result, error=None)
            _render()

        def _on_error(exc: BaseException) -> None:
            # Privacy: the raw exception (a path, a column name, a pasted value) NEVER reaches
            # the screen — the trace goes to the log, the card carries a bounded category.
            logger.error("The creator test conversion failed for %r.", sis_id, exc_info=exc)
            st["running"] = False
            if isinstance(exc, GateRefused):
                st["gate"] = GateOutcome(state=GateState.REFUSED_NO_OUTPUT_DIR)
            else:
                st["gate"] = _gate_outcome(result=None, error=exc)
            _render()

        started = runner.run(
            page,
            lambda: creator_gate_job(sis_id, input_dir=str(cfg.input_dir or ""), output_dir=str(cfg.output_dir or "")),
            on_done=_on_done,
            on_error=_on_error,
        )
        if not started:  # already running — the single-flight guard held
            st["running"] = True
            _render()

    def _activate(_e: ft.ControlEvent | None = None) -> None:
        """The ONE act that makes this district the one this install converts.

        Defensively refuses while something is unsaved too: the confirm can only be reached
        through a passed test, which :func:`_run_gate` already refuses to run in that state,
        but the two must not be one guard's width apart — this is the act with a
        consequence, and "the district you activated is not the one you tested" is the exact
        failure S4's gate exists to prevent.
        """
        if _unsaved():
            _set_note(FILES_UNSAVED_NOTE)
            return
        digest = current_digest(sis_id)
        if digest is None or not cfg.activate_creator_config(sis_type=sis_id, digest=digest):
            _set_note(CREATOR_ACTIVATE_FAILED_NOTE)
            return
        st["activated"] = True
        st["note"] = ""
        reset_catalog_cache()
        on_activated()

    def _staleness_notes() -> list[str]:
        fact = overlay_staleness(
            read_authored_with(sis_id),
            running_version=app_version(),
            current_base_digest=_base_digest_for(sis_id),
        )
        notes: list[str] = []
        if fact.version_differs:
            notes.append(GATE_STALE_VERSION_NOTE)
        if fact.base_changed:
            notes.append(GATE_STALE_BASE_NOTE)
        return notes

    def _gate_already_passed() -> bool:
        """Whether this district's gate is ALREADY passed and still current. Memoised.

        ONE spelling of one fact: :func:`creator_gate_current` — the same function the
        wizard's ``files_step_satisfied`` input reads — rather than a second, drifting
        ``verified_is_current(stored_verified_digest(...), current_digest(...))`` in the
        view. Memoised per mount (see ``st["gate_current"]``); a hand edit made while the
        surface is open is caught on the next mount, which is where the step's staleness
        notes are derived too.
        """
        if st["gate_current"] is None:
            st["gate_current"] = creator_gate_current(cfg, sis_id)
        return bool(st["gate_current"])

    def _files_controls() -> list[ft.Control]:
        outcome: GateOutcome = st["gate"]  # type: ignore[assignment]
        already = bool(st["activated"]) or _gate_already_passed()
        passed = outcome.state is GateState.PASSED
        controls: list[ft.Control] = []

        if outcome.state is GateState.PASSED:
            controls.append(
                components.HealthVerdictBanner(
                    Verdict.HEALTHY, headline=GATE_PASSED_HEADLINE, detail=GATE_PASSED_DETAIL
                )
            )
            if outcome.counts:
                controls.append(
                    ft.Row(
                        wrap=True,
                        spacing=tokens.space_lg,
                        run_spacing=tokens.space_lg,
                        controls=[
                            components.metric_tile(_entity_label(name), f"{count:,}")
                            for name, count in outcome.counts.items()
                        ],
                    )
                )
        elif outcome.state is GateState.FAILED:
            controls.append(
                components.HealthVerdictBanner(Verdict.FAILED, headline=GATE_FAILED_HEADLINE, detail=outcome.note)
            )
        elif outcome.state is GateState.REFUSED_NO_OUTPUT_DIR:
            controls.append(
                components.HealthVerdictBanner(
                    Verdict.WARNING, headline=GATE_FAILED_HEADLINE, detail=GATE_REFUSED_NO_OUTPUT_NOTE
                )
            )

        if st["running"]:
            controls.append(components.inflight_row(GATE_RUNNING_CAPTION))

        for note in _staleness_notes():
            controls.append(ft.Text(note, size=tokens.type_body, color=tokens.color_status_warning))

        # ONE presence source: the rows, their chips and the missing-file list all read the
        # PENDING effective names, so nothing on this card can contradict anything else on
        # it. (The gate's own ``GateOutcome.missing_files`` — the config on DISK, after a run
        # — stays the authoritative report, which is why it is derived separately.)
        folder = _folder_filenames(str(cfg.input_dir or ""))
        rows = file_form_rows(_slots(), renames=_effective_renames(), present=folder)
        unsaved = _unsaved()
        action = files_primary_action(unsaved=unsaved, passed=passed, already=already)

        def _tiered(label: str, handler, *, primary: bool, icon: str) -> ft.Control:  # noqa: ANN001
            """The 3-tier rule applied from ONE decision: filled when this is the primary."""
            if primary:
                return components.primary_button(
                    label,
                    handler,
                    disabled=bool(st["running"]),
                    disabled_bgcolor=tokens.color_border,
                    icon=icon,
                )
            return components.secondary_button(label, handler, disabled=bool(st["running"]), icon=icon)

        file_rows: list[ft.Control] = [ft.Text(FILES_INTRO_NOTE, size=tokens.type_body, color=tokens.color_muted)]
        file_rows.extend(_name_row(row, folder) for row in rows)
        if any(not row.present for row in rows):
            file_rows.append(ft.Text(FILES_MISSING_NOTE, size=tokens.type_body, color=tokens.color_status_warning))
        if unsaved:
            file_rows.append(ft.Text(FILES_UNSAVED_NOTE, size=tokens.type_body, color=tokens.color_status_warning))
        file_rows.append(
            ft.Row(
                controls=[_tiered(FILES_SAVE_LABEL, _save_names, primary=action == "save", icon=ft.Icons.SAVE_OUTLINED)]
            )
        )
        controls.append(components.card(ft.Column(spacing=tokens.space_lg, controls=file_rows)))

        controls.extend(_note_row())

        # ONE filled primary in EVERY state, decided by the pure ``files_primary_action``:
        # the save while a name on screen is not in the config, the run while there is
        # nothing to confirm, the confirm once a test has passed, and — once this district is
        # genuinely active — none at all (the step footer's Continue takes that tier).
        actions: list[ft.Control] = []
        if already:
            controls.append(
                ft.Row(
                    spacing=tokens.space_sm,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=18, color=tokens.color_status_healthy),
                        ft.Text(GATE_ACTIVATED_NOTE, size=tokens.type_body, color=tokens.color_status_healthy),
                    ],
                )
            )
        if action == "confirm":
            actions.append(components.primary_button(GATE_CONFIRM_LABEL, _activate, icon=ft.Icons.CHECK_ROUNDED))
        actions.append(
            _tiered(
                GATE_RERUN_LABEL if (passed or already) else GATE_RUN_LABEL,
                _run_gate,
                primary=action == "run",
                icon=ft.Icons.PLAY_ARROW_ROUNDED,
            )
        )
        controls.append(ft.Row(spacing=tokens.space_lg, controls=actions))
        controls.append(_discard_row())
        return controls

    _render()
    st["mounted"] = True
    return host


def _shipped_district_number(text: str) -> str:
    """The district number in ``text`` when DistrictSync ALREADY ships a mapping for it. TOTAL.

    Drives the starting-point card's recommendation (see :func:`creator_shipped_note`). Reads
    ``available_configs()`` — the bundled ids plus anything in the profile's ``mappings/`` dir —
    exactly as Home's not-listed card does, and answers ``""`` for a district we do not ship
    (that admin has nothing to be pointed at).
    """
    digits = sd_number_digits(text or "")
    if not digits:
        return ""
    try:
        from src.config.loader import available_configs

        shipped = resolve_sd_number(digits, available_configs())
    except Exception:  # noqa: BLE001 - total: no recommendation is better than a wrong one
        return ""
    return digits if shipped else ""


def pending_creator_sis(app_config: AppConfig) -> str:
    """The self-service district still being set up, or ``""``. TOTAL — SELF-HEALING.

    Creator mode is entered on TWO facts together: a stored resume token AND an overlay
    actually on disk for it. A token whose file is gone (deleted by hand, or a discard whose
    settings write was refused) is CLEARED here — via the one sanctioned advisory write path —
    so the wizard opens the standard walk instead of a six-step flow around a district that
    does not exist. A refused clear is not an error: the next mount simply tries again, and
    nothing about the standard walk depends on the token being gone.
    """
    pending = (getattr(app_config, "creator_pending_sis", "") or "").strip()
    if not pending:
        return ""
    try:
        exists = overlay_path(pending).exists()
    except (ValueError, OSError):
        exists = False  # an invalid id can never name an overlay this app wrote
    if exists:
        return pending
    logger.info("Clearing a district-setup resume token with no mapping file on disk.")
    app_config.creator_save(creator_pending_sis="")
    return ""


def creator_gate_current(app_config: AppConfig, sis_id: str) -> bool:
    """Whether ``sis_id``'s "Your files" gate is passed AND still current. TOTAL.

    The two halves the flow's ``files_step_satisfied`` fact is defined as: the overlay is on
    disk, and the digest recorded when it last passed the test conversion still equals the
    digest of what would convert TODAY (so a hand edit to the overlay, or a vendor change to
    the starting point it inherits, re-closes the gate). Any failure answers ``False`` — the
    only safe direction, since an absent fact can force another test run but never unlock one.
    """
    sis = (sis_id or "").strip()
    if not sis:
        return False
    try:
        if not overlay_path(sis).exists():
            return False
        return verified_is_current(stored_verified_digest(app_config, sis), current_digest(sis))
    except (ValueError, OSError):
        return False


def _base_digest_for(sis_id: str) -> str | None:
    """The resolved digest of the STARTING POINT ``sis_id`` was authored against. TOTAL.

    ``None`` — "unknown", which :func:`overlay_staleness` never reads as stale — when the
    provenance block is absent or its base no longer loads.
    """
    provenance = read_authored_with(sis_id)
    if provenance is None:
        return None
    try:
        from src.config.loader import load_config

        return resolved_digest(load_config(provenance.base))
    except Exception:  # noqa: BLE001 - total: an unloadable base is unknown, not stale
        return None
