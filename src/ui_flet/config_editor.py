"""Pure form/gate logic for the district config creator (plan 0044 slice 3).

NO ``flet`` import and NO I/O — everything here is a function of values the view
already holds, which is what makes the creator's riskiest decisions testable
headless: what an overlay will SAY (``CreatorForm`` → ``OverlaySpec``), whether the
test-conversion gate passed, and whether an activated district still matches what
was actually tested.

Four families live here:

* **The form.** Frozen :class:`CreatorForm` with ``with_*`` returns, so a step can
  never half-mutate shared state. It holds RESOLVED values (a base id, the district
  facts, the entity selection, the grade scopes) and :meth:`CreatorForm.to_overlay_spec`
  derives the emission. The grade chain ``homeroom_grades ⊆ class_rostering_grades ⊆
  student_rostering_grades`` holds BY DERIVATION from one question ("which grades are
  rostered?") plus one subset question ("which of those get a homeroom class instead of
  a timetable?"), so ``GlobalConfig.check_rostering_grade_scopes`` can only ever confirm
  it — the admin never has to reconstruct a chain rule from an error message.
* **The filename form** (slice 4). :class:`SourceFileSlot` /
  :func:`distinct_source_files` derive ONE row per FILE the district's config reads (the
  unit an admin renames is the file, not the entity: one answer fixes Classes,
  Enrollments and the school-year lookup together), :func:`file_form_rows` pairs those
  with the name in force and its presence in the folder, :func:`renames_from_resolved` is
  the resume inverse, and :func:`files_primary_action` decides which action is the step's
  ONE filled primary.
* **The gate.** :class:`GateState` / :class:`GateOutcome` / :func:`gate_outcome_for`
  reduce the test conversion's outcome — the worker's result or exception, whether the
  output folder is usable, and the expected-vs-present file lists — to one derived fact.
  Pure over INJECTED facts: the view runs the worker and stats the folder, this module
  decides what it means.
* **The stored facts.** :func:`stored_verified_digest` re-validates the hand-editable
  ``creator_verified`` map at READ time (the ``identity_gate.stored_identity_email``
  precedent), :func:`verified_is_current` compares it against what would convert today,
  and :func:`overlay_staleness` turns an overlay's ``authored_with`` provenance into the
  two booleans the Files step's note reads.

**Privacy.** Nothing here returns a district name, a domain, a path or a roster row.
:func:`humanize_config_error` in particular maps an exception to a BOUNDED category
string and never echoes the message — Pydantic validation errors quote the offending
value, and the likeliest bad value is a pasted personal email address.

**Fail-safe direction.** Every read is TOTAL and fails toward "test it again": a
malformed stored digest reads as ABSENT, two unknown digests never compare equal, and
unknown provenance is never reported as stale.

A fourth, smaller family sits in front of those: the **field rules** —
:func:`split_domains` and :func:`sd_number_from_text`, what a raw text field's string
MEANS. They live here rather than in the view because the four-digit bound on a district
number is safety-relevant (it becomes a filename stem and a ``--sis`` argument), and a
safety rule belongs where it can be tested directly.

Layering: imports the config layer (``authoring`` / ``models`` /
``bc_district_domains``), ``src.utils.validators``, the ETL's CEDS grade vocabulary —
the same co-ownership ``models._ceds_grade_codes`` documents, since every grade-scope
question is asked in the CEDS OUTPUT space and a second table would drift — and the pure
``ui_flet.identity_gate`` for its ONE digit rule (``sd_number_digits``), so the creator
reads "SD48" exactly as the launch page's not-listed answer does.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

import yaml

from src.config.authoring import ALLOWED_BASES, CREATOR_ENTITIES, OverlaySpec, validate_source_filename
from src.config.bc_district_domains import domains_for, presumptive_domain
from src.config.models import (
    CLASS_ROSTERING_HOMEROOM_SENTINEL,
    MappingConfig,
    is_valid_district_domain,
)
from src.etl.transformers.grades import CEDS_MAPPING
from src.ui_flet.identity_gate import sd_number_digits
from src.utils.validators import is_config_digest, validate_sis_type

if TYPE_CHECKING:  # pragma: no cover - typing only; no runtime import of either layer
    from src.config.app_config import AppConfig
    from src.config.authoring import AuthoredWith
    from src.etl.pipeline import PipelineResult


#: The CEDS grade vocabulary in DISPLAY ORDER, derived from :data:`CEDS_MAPPING`'s
#: VALUES. Deliberately not ``grades.CEDS_GRADE_CODES``: that is an unordered
#: ``frozenset``, and a grade picker has to read youngest-to-oldest. ``dict.fromkeys``
#: de-duplicates while preserving the table's order (many source spellings map to one
#: code). NOTHING may case-normalise these codes — ``"Other"`` is the one mixed-case
#: member, and a lower-cased ``"other"`` silently de-rosters a whole cohort.
CEDS_GRADE_ORDER: tuple[str, ...] = tuple(dict.fromkeys(CEDS_MAPPING.values()))

#: Plain-language names for the four :data:`ALLOWED_BASES`, declared HERE so no picker
#: ever shows a raw config id (``mbponly`` tells an admin nothing). Each names what the
#: starting point PRODUCES, because that is the question the choice answers — the
#: entity lists are the ones those configs declare in ``enabled_entities``.
BASE_LABELS: Mapping[str, str] = {
    "myedbc": "Standard MyEd BC rostering (SpacesEDU)",
    "mbp_all": "Full myBlueprint+ (rostering and courses)",
    "mbp_core": "myBlueprint+ core (students and courses)",
    "mbponly": "myBlueprint+ courses only",
}

#: A plain-language name per DISTINCT source filename across all four
#: :data:`ALLOWED_BASES` — admin-facing copy, so DATA rather than something derived:
#: ``ClassInformationEnh.txt`` split on capitals reads "Class Information Enh", which is
#: worse than the filename it came from. :func:`file_label` falls back to the filename, so
#: a base that grows a new source file still renders a row (the parity test is what says
#: it needs a label).
FILE_LABELS: Mapping[str, str] = {
    "StudentDemographicInformation.txt": "Student details",
    "StaffInformationEnhanced.txt": "Staff details",
    "EmergencyContactInformation.txt": "Parent and guardian contacts",
    "StudentSchedule.txt": "Student timetables",
    "CourseInformation.txt": "Course list",
    "ClassInformationEnh.txt": "Class details",
    "StudentCourseHistory.txt": "Past courses",
    "StudentCourseSelection.txt": "Course choices",
}

#: The bounded categories :func:`humanize_config_error` answers with. One constant per
#: string so the view renders a reviewed sentence and never an exception message.
CONFIG_ERROR_GRADES = (
    "The grade choices don't work together — every homeroom grade has to be one of the rostered grades."
)
CONFIG_ERROR_DOMAIN = "One of the email domains isn't a plain district domain (like sd48.bc.ca)."
CONFIG_ERROR_MISSING_BASE = "The starting point this district builds on isn't in this version of DistrictSync."
CONFIG_ERROR_UNREADABLE = "This district's mapping file couldn't be read."
CONFIG_ERROR_OTHER = "This district's mapping can't be used as it stands."


# ---------------------------------------------------------------------------
# Field rules (what a text field's raw string MEANS)
# ---------------------------------------------------------------------------
#: Whatever separates one typed domain from the next: a comma, a semicolon, or plain
#: whitespace. An admin pasting a list from an email will use any of the three.
_DOMAIN_SEPARATORS = re.compile(r"[,;\s]+")

#: The district number's upper bound, in DIGITS. Safety-relevant, not cosmetic — see
#: :func:`sd_number_from_text`.
_MAX_SD_NUMBER_DIGITS = 4


def split_domains(text: str) -> tuple[str, ...]:
    """The domains a text field holds, split on commas / semicolons / whitespace. TOTAL.

    Blank entries are dropped (a trailing comma is not an error) and the ORDER the admin
    typed is preserved. Deliberately NOT de-duplicated and NOT shape-checked here: this
    is the field's reading of the raw string, and :func:`validate_domains` is the ONE
    boundary that decides whether each entry is a usable district domain (it also
    de-duplicates). Splitting that decision across two functions is how a note that
    should have said "that isn't a domain" ends up saying nothing at all.
    """
    return tuple(chunk.strip() for chunk in _DOMAIN_SEPARATORS.split(text or "") if chunk.strip())


def sd_number_from_text(text: str) -> int | None:
    """The district number a text field holds, or ``None`` when it holds none we can use.

    Delegates the digit rule to :func:`src.ui_flet.identity_gate.sd_number_digits` (the
    single source, shared with the launch page's not-listed answer): the first run of
    digits, leading zeros dropped, so ``"SD48"`` / ``"#48"`` / ``"048"`` / ``" 48 "`` are
    all district 48.

    **Bounded at four digits deliberately** — this is a safety rule, not a cosmetic one.
    The value becomes a filename stem AND a ``--sis`` argument baked into a scheduled
    task, so a pasted phone number must fail the field's own note rather than author
    ``sd6045551234custom``.

    ``0`` is representable (an admin can type it) and is refused downstream by
    :func:`src.config.authoring.derive_sis_id`, which takes a POSITIVE int — the caller's
    own falsy check catches it first and paints the field's note.
    """
    digits = sd_number_digits(text or "")
    if not digits or len(digits) > _MAX_SD_NUMBER_DIGITS:
        return None
    return int(digits)


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    """De-duplicate while preserving first-seen order (``dict.fromkeys`` over a tuple)."""
    return tuple(dict.fromkeys(values))


def derive_domains(identity_domain: str, sd_number: int) -> tuple[str, ...]:
    """PREFILL the district's public staff domain(s). TOTAL — never raises.

    The stored identity domain FIRST (``identity_gate.stored_identity_domain``, computed
    by the view — this module reads no settings), then every vendored domain
    :func:`src.config.bc_district_domains.domains_for` knows for ``sd_number``,
    order-stable and de-duplicated. The admin's own address leads because it is the one
    value we know is real for THIS install; the table is owner-supplied placeholder data.

    ONLY when both are empty does the conventional ``sd<N>.bc.ca`` guess apply
    (:func:`src.config.bc_district_domains.presumptive_domain`) — a prefill, never a
    claim of fact, and never mixed IN with a known domain, where it would look equally
    authoritative.

    An identity domain that is not a bare lowercase domain is DROPPED rather than
    prefilled: it would only be refused again by :func:`validate_domains` at the
    boundary, and dropping it can never widen anything (the district still shows in
    every unmatched picker state when the list ends up empty).

    Total over a non-positive or non-``int`` ``sd_number`` (the form's default is ``0``
    and the view prefills before anything validates the number): the table answers
    ``()`` for any int and the presumptive guess is simply not offered.
    """
    known: list[str] = []
    if is_valid_district_domain(identity_domain):
        known.append(identity_domain)
    usable_sd = isinstance(sd_number, int) and not isinstance(sd_number, bool) and sd_number > 0
    if usable_sd:
        known.extend(domains_for(sd_number))
    if known:
        return _ordered_unique(known)
    if usable_sd:
        return (presumptive_domain(sd_number),)
    return ()


def validate_domains(values: Iterable[str]) -> tuple[str, ...]:
    """Return the kept domains, de-duplicated in order. Raises on an invalid entry.

    THE boundary check for the creator's domain field, delegating the shape rule to
    :func:`src.config.models.is_valid_district_domain` (S1's single source, shared with
    the model validator and the loader's user-dir floor) rather than re-spelling it.

    The message NEVER echoes the value and never says which one it was beyond its
    position: the likeliest bad entry is a pasted personal email address, and this
    message reaches a log. Same rule ``OverlaySpec.__post_init__`` already follows.

    Blank entries are dropped (a trailing empty row in a list of text fields is not an
    error); an empty result is legitimate — "this district claims no staff domain".
    """
    entries = [value for value in values if not (isinstance(value, str) and not value.strip())]
    for index, value in enumerate(entries, start=1):
        if not is_valid_district_domain(value):
            raise ValueError(
                f"Email domain {index} of {len(entries)} is not a bare lowercase domain name "
                "(e.g. 'sd48.bc.ca'). The offending value is deliberately not quoted here."
            )
    return _ordered_unique(entries)


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


def seed_entities(resolved_base: MappingConfig) -> tuple[str, ...]:
    """The entity selection a form STARTS from: the resolved base's own list.

    Filtered to :data:`src.config.authoring.CREATOR_ENTITIES`, so ``StudentAttendance``
    is absent by construction (it stays vendor-authored — see that tuple's rationale),
    and ordered by that same tuple so toggling a checkbox can never reorder the
    selection.

    All four :data:`ALLOWED_BASES` declare their ``enabled_entities`` in
    ``CREATOR_ENTITIES`` order already, which is why canonicalising the order here is
    free: an UNTOUCHED seeded selection still compares equal to the base's list, so
    ``build_overlay``'s minimal emission omits the key entirely. Pinned by a test —
    if a future base ever declares a different order, that test is where it surfaces.
    """
    declared = set(resolved_base.global_config.enabled_entities)
    return tuple(name for name in CREATOR_ENTITIES if name in declared)


# ---------------------------------------------------------------------------
# Source files (the filename form — plan 0044 slice 4)
# ---------------------------------------------------------------------------
#: One place a filename is referenced from in a resolved config:
#: ``("mappings", <entity>, <role>)`` or ``("school_year_sources", <key>)``. The same
#: shape ``authoring._reference_sites`` keys its propagation map by — that one is a DICT
#: for propagation, this walk is ORDERED for display, and both must agree on what a site
#: is or a row on screen would not be the thing the write moves.
_SiteKey = tuple[str, ...]


def file_label(filename: str) -> str:
    """The plain-language name for a source filename, falling back to the filename itself.

    TOTAL, for the same reason :func:`base_label` is: a base that grows a source file with
    no :data:`FILE_LABELS` row still renders a usable row instead of a blank one, and the
    parity test is what says the row is missing.
    """
    return FILE_LABELS.get(filename, filename)


def _ordered_reference_sites(config: MappingConfig) -> tuple[tuple[_SiteKey, str], ...]:
    """Every filename reference in ``config``, in a DETERMINISTIC display order.

    Entities in :data:`CREATOR_ENTITIES` order, then any remaining entity key sorted (so
    a vendor-only entity is never silently dropped from the walk), roles in the config's
    own order, then ``global_config.school_year_sources``.

    The order matters: ``pipeline.advisory_expected_files`` returns ``list(set)``, whose
    order varies with ``PYTHONHASHSEED``, and the Files step's rows must not move between
    runs. So the ROW ORDER is this walk's, never ``expected``'s.
    """
    remaining = sorted(name for name in config.mappings if name not in CREATOR_ENTITIES)
    sites: list[tuple[_SiteKey, str]] = []
    for entity in (*CREATOR_ENTITIES, *remaining):
        entity_cfg = config.mappings.get(entity)
        if entity_cfg is None:
            continue
        for role, filename in entity_cfg.source_files.items():
            sites.append((("mappings", entity, role), filename))
    for key, filename in config.global_config.school_year_sources.items():
        sites.append((("school_year_sources", key), filename))
    return tuple(sites)


@dataclass(frozen=True)
class SourceFileSlot:
    """One FILE the district's config reads — the unit the filename form is keyed by.

    The unit is the FILE, not the entity/role: setting ``StudentSchedule.txt`` once fixes
    Classes, Enrollments AND the school-year lookup together (SD74's real shape), which is
    why ``OverlaySpec.source_file_renames`` is keyed the same way.

    Attributes:
        original: the base config's own filename — what a rename is keyed BY.
        label: the plain-language name (:func:`file_label`).
        references: the ACTIVE ``(entity, role)`` sites naming it, in walk order. Entity
            KEYS, never labels: ``setup``/``creator``'s ``_entity_label`` owns that
            vocabulary, and a second table would drift from it.
        names_school_year: ``global_config.school_year_sources`` also names this file, so
            a new name moves the school-year lookup — and with it every
            ``append_year_to_id`` Class ID. The one propagation worth saying out loud.
    """

    original: str
    label: str
    references: tuple[tuple[str, str], ...] = ()
    names_school_year: bool = False


def distinct_source_files(resolved_base: MappingConfig, *, expected: Iterable[str]) -> tuple[SourceFileSlot, ...]:
    """The files the Files step shows a row for, de-duplicated and in a stable order.

    Walks ``resolved_base`` through :func:`_ordered_reference_sites`, keeping a site only
    when its entity is one this config actually produces (``active_entities()``) and its
    filename is in ``expected``; the first site to name a file decides that row's place.

    ``expected`` is INJECTED rather than derived here so ``pipeline.advisory_expected_files``
    stays the SINGLE source for "which files matter" — including its one narrowing (a fully
    homeroom-scoped config's ``student_schedule``/``class_info`` roles feed no surviving
    class, so offering a row for them would be offering a name that changes nothing).

    A school-year file that no ACTIVE entity reads gets NO slot: ``extract_required_files``
    never loads it, so the row would be equally inert. ``names_school_year`` therefore only
    ever ANNOTATES a row that exists for an entity's sake.
    """
    wanted = {name for name in expected if isinstance(name, str) and name}
    active = resolved_base.active_entities()
    year_files = set(resolved_base.global_config.school_year_sources.values())

    references: dict[str, list[tuple[str, str]]] = {}
    order: list[str] = []
    for site, filename in _ordered_reference_sites(resolved_base):
        if site[0] != "mappings":
            continue
        _, entity, role = site
        if entity not in active or filename not in wanted:
            continue
        if filename not in references:
            references[filename] = []
            order.append(filename)
        references[filename].append((entity, role))

    return tuple(
        SourceFileSlot(
            original=filename,
            label=file_label(filename),
            references=tuple(references[filename]),
            names_school_year=filename in year_files,
        )
        for filename in order
    )


@dataclass(frozen=True)
class FileFormRow:
    """One rendered row: the slot, the name in force, and whether the folder holds it.

    Composition, not duplication — the slot is carried whole rather than re-spelled, so a
    row can never disagree with the file it describes.

    Attributes:
        slot: the file this row is for.
        effective: the district's name for it (the renamed name, else ``slot.original``).
        present: the input folder holds a file of that name (case-insensitively).
    """

    slot: SourceFileSlot
    effective: str
    present: bool


def file_form_rows(
    slots: Iterable[SourceFileSlot],
    *,
    renames: Mapping[str, str],
    present: Iterable[str],
) -> tuple[FileFormRow, ...]:
    """Pair each slot with the name in force and its presence in the input folder.

    Presence reuses :func:`missing_files`' case-INSENSITIVE fold — ONE fold in the product,
    so ``studentcourseselection.txt`` can never read as absent against a mapping that says
    ``StudentCourseSelection.txt`` while the extractor loads it perfectly well.

    (Deliberately no separate ``present_files(...)`` helper: it would duplicate that fold
    to hand back its own input.)
    """
    rows = tuple(slots)
    effective: list[str] = []
    for slot in rows:
        renamed = renames.get(slot.original) if isinstance(renames, Mapping) else None
        chosen = renamed.strip() if isinstance(renamed, str) else ""
        effective.append(chosen or slot.original)
    absent = set(missing_files(effective, present))
    return tuple(
        FileFormRow(slot=slot, effective=name, present=name not in absent)
        for slot, name in zip(rows, effective, strict=True)
    )


def renames_from_resolved(resolved_base: MappingConfig, resolved_current: MappingConfig) -> dict[str, str]:
    """The rename map a district's CURRENT config already expresses — the RESUME inverse.

    For every reference site in the base, if the current config names something else there,
    record ``base name -> current name`` (first-seen per original, same walk as
    :func:`distinct_source_files`). TOTAL: an identical pair answers ``{}``.

    A hand-edited DIVERGENT config — two sites naming one base file two different ways —
    collapses to its FIRST-SEEN name here, and the next Save writes that one name to every
    site. That is a visible repair rather than a silent loss, because the rows on screen and
    the written map are ONE value: whatever this returns is what the form shows.
    """
    current = dict(_ordered_reference_sites(resolved_current))
    found: dict[str, str] = {}
    for site, original in _ordered_reference_sites(resolved_base):
        now = current.get(site)
        if not isinstance(now, str) or not now or now == original:
            continue
        found.setdefault(original, now)
    return found


def files_primary_action(*, unsaved: bool, passed: bool, already: bool) -> Literal["save", "run", "confirm", "none"]:
    """Which of the Files step's actions is the ONE filled primary. PURE.

    * ``"save"`` — there are file names on screen the config on disk does not have.
      ``unsaved`` WINS over everything: a test conversion run against names the config
      lacks reports on the wrong files, and confirming one would activate a district
      nobody has tested as it now reads.
    * ``"none"`` — this district is already active and current: the step's footer
      Continue is the screen's one filled action, so the body offers none.
    * ``"confirm"`` — a test passed and the district is not active yet.
    * ``"run"`` — nothing has been tested yet.

    Lifting the branch out of the view is what lets "exactly ONE filled primary" be
    asserted over all EIGHT input combinations rather than read off a render.
    """
    if unsaved:
        return "save"
    if already:
        return "none"
    if passed:
        return "confirm"
    return "run"


# ---------------------------------------------------------------------------
# The form
# ---------------------------------------------------------------------------


def _grade_tuple(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    """Canonicalise a grade selection to CEDS display order, refusing unknown codes."""
    chosen = set()
    for value in values:
        if value not in CEDS_GRADE_ORDER:
            # No echo of a hand-passed value is needed here (a picker only offers the
            # vocabulary), but the message must name the FIELD so a programming error
            # is actionable. Case is significant — see CEDS_GRADE_ORDER.
            raise ValueError(f"{label} contains a value that is not a CEDS grade code (case-sensitive).")
        chosen.add(value)
    return tuple(code for code in CEDS_GRADE_ORDER if code in chosen)


@dataclass(frozen=True)
class CreatorForm:
    """What the admin has answered so far. FROZEN — every edit returns a new form.

    Frozen + ``with_*`` returns so a step handler can never half-mutate state shared
    with another step: the view holds one form and replaces it, exactly as the pure
    ``setup_flow`` inputs are rebuilt rather than patched.

    The two grade fields are the whole chain and they move TOGETHER: both ``None``
    (UNANSWERED ⇒ inherit the base's scopes, the correct answer for the K-12 districts
    the base was written for) or both answered. ``homeroom ⊆ rostered`` is enforced at
    CONSTRUCTION, so no reachable form state can emit a chain the loader refuses — which
    is the whole reason the derivation lives here rather than in a Continue-time
    validator the admin has to decode.

    Attributes:
        base: one of :data:`ALLOWED_BASES` (rendered via :data:`BASE_LABELS`).
        sd_number: the BC district number; decides the config id.
        district_name: presentation only.
        domains: the district's PUBLIC staff domains (may legitimately be empty).
        entities: the CSVs to produce. ``()`` means NOT SEEDED yet (inherit the base's
            list); an explicitly EMPTY selection is refused by :meth:`with_entities`,
            because "produce nothing" can only end at the delivery floor.
        rostered: CEDS codes whose students are rostered at all, or ``None`` to inherit.
            An explicitly EMPTY selection is refused by :meth:`with_rostered`.
        homeroom: the subset of ``rostered`` that gets a homeroom class instead of
            timetable classes — ``()`` is a legitimate answer (a secondary-only district
            has to SAY it) — or ``None`` to inherit, only while ``rostered`` is ``None``
            too.
        renames: base filename → the name this district's extract uses, keyed by the
            FILE (see :class:`SourceFileSlot`). Empty = the base's standard MyEd BC
            names. Every entry is validated at :func:`with_rename` AND re-validated at
            construction, so no reachable form state emits a filename the authoring
            boundary refuses.
    """

    base: str = ALLOWED_BASES[0]
    sd_number: int = 0
    district_name: str = ""
    domains: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    rostered: tuple[str, ...] | None = None
    homeroom: tuple[str, ...] | None = None
    renames: Mapping[str, str] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.base not in ALLOWED_BASES:
            raise ValueError(f"base must be one of {list(ALLOWED_BASES)} (got {self.base!r}).")
        unknown = [name for name in self.entities if name not in CREATOR_ENTITIES]
        if unknown:
            raise ValueError(f"entities contains {unknown}, which self-service cannot author.")
        for label, values in (("rostered", self.rostered), ("homeroom", self.homeroom)):
            if values is not None:
                _grade_tuple(values, label=label)
        if self.rostered is not None and not self.rostered:
            # Guarded at CONSTRUCTION, not only in `with_rostered`: an explicit empty
            # rostered scope emits `class_rostering_grades: []`, which the loader refuses
            # with an "EMPTY list" error that `humanize_config_error` would mislabel as a
            # chain problem. Refusing here keeps the module's claim true — no reachable
            # form state emits a chain the loader refuses. `None` still means "inherit".
            raise ValueError(
                "rostered must name at least one grade — an empty grade scope sends no students at all. "
                "Use None to inherit the starting point's grades."
            )
        if (self.rostered is None) != (self.homeroom is None):
            # The two halves are ONE question ("which grades are rostered, and which of
            # those get a homeroom class instead of a timetable?"), so a half-answer is
            # unrepresentable. It also cannot be emitted safely: a rostered scope with an
            # INHERITED homeroom list leaves the chain's lower bound at the base's K-7,
            # which `check_rostering_grade_scopes` correctly refuses for an 8-12 district
            # (`build_overlay`'s chain-companion docstring says so — a secondary-only
            # district has to SAY `homeroom_grades=()`).
            raise ValueError(
                "rostered and homeroom must both be answered or both be left to inherit — a rostered scope with "
                "an inherited homeroom list is a chain the loader refuses."
            )
        if self.homeroom:
            outside = [code for code in self.homeroom if code not in (self.rostered or ())]
            if outside:
                raise ValueError(
                    f"homeroom grades {outside} are not rostered grades. A homeroom grade must be one of the "
                    "rostered grades — the chain homeroom ⊆ class ⊆ student is what makes the config load."
                )
        # Re-validated here, not only in `with_rename`, so a DIRECT construction cannot
        # bypass the filename boundary (the `rostered` precedent). Labelled generically:
        # the message reaches a log, and the actionable fact is which CHECK failed.
        for original, renamed in dict(self.renames).items():
            if not isinstance(original, str) or not original.strip():
                raise ValueError("renames keys must be the base filenames they replace.")
            validate_source_filename(renamed, label="file name")

    # -- edits ------------------------------------------------------------
    def with_base(self, base: str) -> CreatorForm:
        """Switch the starting point. The caller re-seeds ``entities`` (which needs the
        RESOLVED base, i.e. I/O this module does not do — see :func:`seed_entities`)."""
        return dataclasses.replace(self, base=base)

    def with_district(self, *, sd_number: int | None = None, district_name: str | None = None) -> CreatorForm:
        """Set the district number and/or name (both presentation-time values)."""
        return dataclasses.replace(
            self,
            sd_number=self.sd_number if sd_number is None else sd_number,
            district_name=self.district_name if district_name is None else district_name,
        )

    def with_domains(self, values: Iterable[str]) -> CreatorForm:
        """Replace the domain list, validated at this boundary (:func:`validate_domains`)."""
        return dataclasses.replace(self, domains=validate_domains(values))

    def with_entities(self, values: Iterable[str]) -> CreatorForm:
        """Replace the entity selection. Refuses an EMPTY explicit selection.

        Emitting nothing is not a configuration — it produces no output CSVs at all and
        can only end at the delivery floor (the same refusal ``OverlaySpec`` makes). The
        refusal lives here so the view can keep Continue open with a plain note instead
        of discovering it at write time.
        """
        chosen = _ordered_unique(values)
        if not chosen:
            raise ValueError(
                "Choose at least one CSV to produce — a district that produces nothing has nothing to send."
            )
        # Validated BEFORE the canonical re-ordering, which would otherwise DROP an
        # unauthorable entity silently — the permissive default this project bans.
        unknown = [name for name in chosen if name not in CREATOR_ENTITIES]
        if unknown:
            raise ValueError(f"entities contains {unknown}, which self-service cannot author.")
        return dataclasses.replace(self, entities=tuple(name for name in CREATOR_ENTITIES if name in set(chosen)))

    def with_rostered(self, values: Iterable[str] | None) -> CreatorForm:
        """Set the rostered grades (``None`` = inherit the base's scopes).

        Narrowing the rostered set NARROWS ``homeroom`` with it, rather than raising:
        a grade that is no longer rostered gets nothing either way, so keeping it in the
        homeroom list could only produce an invalid chain the admin never asked for.
        Answering this question also ANSWERS the homeroom one — as ``()`` until the admin
        picks — because the two are one question and a half-answer cannot be emitted.
        ``None`` clears both: the whole chain returns to inherited.
        """
        if values is None:
            return dataclasses.replace(self, rostered=None, homeroom=None)
        rostered = _grade_tuple(values, label="rostered")
        if not rostered:
            raise ValueError(
                "Choose at least one grade to roster — an empty grade scope sends no students at all, which can "
                "only end at the delivery floor. Leave the question unanswered to inherit the starting point's grades."
            )
        # (`__post_init__` re-checks this, so a direct construction cannot bypass it.)
        homeroom = tuple(code for code in (self.homeroom or ()) if code in rostered)
        return dataclasses.replace(self, rostered=rostered, homeroom=homeroom)

    def with_homeroom(self, values: Iterable[str] | None) -> CreatorForm:
        """Set the homeroom subset. ``()`` is a real answer; ``None`` clears the pair.

        A value outside ``rostered`` RAISES: that is the chain rule, enforced at
        construction so no reachable form state can emit an overlay the loader refuses.
        ``None`` returns the WHOLE chain to inherited (both fields), since a rostered
        scope over an inherited homeroom list is exactly the state that fails to load.
        """
        if values is None:
            return dataclasses.replace(self, rostered=None, homeroom=None)
        return dataclasses.replace(self, homeroom=_grade_tuple(values, label="homeroom"))

    def with_rename(self, original: str, new: str) -> CreatorForm:
        """Set (or clear) the name this district's extract uses for ``original``.

        DROPS the entry when ``new`` is blank/whitespace-only or — after stripping —
        equal to ``original``: a "rename" to the standard name is not one, and the
        emission has to stay MINIMAL (an entry naming the base's own filename would emit
        a ``source_files`` override that only forks the base).

        Anything else is validated through :func:`authoring.validate_source_filename` —
        the ONE filename boundary, shared with ``OverlaySpec`` — so a traversal, a
        drive-relative/ADS colon, a control character, a reserved device name or an
        over-long name is refused HERE, with nothing written.

        A blank ``original`` raises (a row with no file is a programming error). An
        ``original`` the base does not reference is NOT rejected here: this module holds
        no resolved base, the form only ever passes slot originals, and
        ``authoring._build_renames`` fails loud at write time — one check, at the layer
        that can see the base.
        """
        if not isinstance(original, str) or not original.strip():
            raise ValueError("with_rename needs the base filename it is replacing.")
        renames = dict(self.renames)
        chosen = new.strip() if isinstance(new, str) else new
        if not isinstance(chosen, str) or not chosen or chosen == original:
            renames.pop(original, None)
        else:
            renames[original] = validate_source_filename(chosen, label="file name")
        return dataclasses.replace(self, renames=renames)

    # -- emission ---------------------------------------------------------
    def to_overlay_spec(self, *, source_file_renames: Mapping[str, str] | None = None) -> OverlaySpec:
        """Derive the :class:`~src.config.authoring.OverlaySpec` this form describes.

        The grade derivation is the point of the whole form:

        * ``student_rostering_grades = rostered`` — the outermost bound;
        * ``class_rostering_grades = rostered``, or the
          ``models.CLASS_ROSTERING_HOMEROOM_SENTINEL`` EXACTLY when every rostered grade
          is also a homeroom grade (the sentinel is the honest spelling of "roster
          exactly the homeroom grades", and it is what SD83 ships);
        * ``homeroom_grades = homeroom``.

        So ``homeroom ⊆ class ⊆ student`` holds by derivation and
        ``GlobalConfig.check_rostering_grade_scopes`` can only confirm it. An UNANSWERED
        field emits ``None`` — minimal emission stays the authoring layer's job, and an
        inherited scope is the right answer for a district that never asked the question.

        ``source_file_renames`` defaults to :attr:`renames` — the filename form's own
        answer — so S3's call sites are unchanged and the Files step passes nothing. The
        keyword still WINS when given (``is not None``, so an explicit ``{}`` really does
        mean "no renames" rather than falling back to the form's).
        """
        rostered = self.rostered
        homeroom = self.homeroom
        class_scope: tuple[str, ...] | Literal["homeroom"] | None
        if rostered is None:
            class_scope = None
        elif rostered and homeroom is not None and tuple(homeroom) == tuple(rostered):
            class_scope = CLASS_ROSTERING_HOMEROOM_SENTINEL
        else:
            class_scope = rostered

        return OverlaySpec(
            sd_number=self.sd_number,
            district_name=self.district_name,
            district_domains=tuple(self.domains),
            base=self.base,
            enabled_entities=tuple(self.entities) or None,
            homeroom_grades=homeroom,
            class_rostering_grades=class_scope,
            student_rostering_grades=rostered,
            source_file_renames=dict(self.renames if source_file_renames is None else source_file_renames),
        )


# ---------------------------------------------------------------------------
# The test-conversion gate
# ---------------------------------------------------------------------------


class GateState(Enum):
    """The states of the creator's test-conversion gate — the ONE activation gate."""

    NOT_RUN = "not_run"  # nothing pressed yet
    RUNNING = "running"  # the worker is in flight (set by the view, not derived)
    PASSED = "passed"  # a dry run completed; counts are trustworthy
    FAILED = "failed"  # the run raised or exited; `note` carries a bounded category
    REFUSED_NO_OUTPUT_DIR = "refused_no_output_dir"  # never started: no usable output folder


@dataclass(frozen=True)
class GateOutcome:
    """What the gate knows. PII-free by construction — counts, filenames, a category.

    Attributes:
        state: see :class:`GateState`.
        counts: entity name → row count, from the dry run's ``PipelineResult``.
        missing_files: expected source files the folder does not hold (the MAPPING's
            spelling, since that is the name to fix).
        note: the bounded error category on ``FAILED`` (see
            :func:`humanize_config_error`), and ``""`` in every other state — the view
            owns the copy for the states that need no diagnosis, so no screen constant
            is duplicated here.
    """

    state: GateState
    counts: Mapping[str, int] = dataclasses.field(default_factory=dict)
    missing_files: tuple[str, ...] = ()
    note: str = ""


def missing_files(expected: Iterable[str], present: Iterable[str]) -> tuple[str, ...]:
    """Expected source files the folder does not hold, in ``expected`` order.

    Case-INSENSITIVE, matching what the extractor actually does on disk (Convert's
    precedent, ``screens/convert.py``): a district's ``students.txt`` against a
    mapping's ``Students.txt`` was once reported as missing while the ETL loaded it
    perfectly well — a false alarm on the very screen an admin uses to decide whether
    their extract is complete. The MAPPING's spelling is what is returned.
    """
    present_folded = {name.lower() for name in present if isinstance(name, str)}
    return _ordered_unique(name for name in expected if name.lower() not in present_folded)


def humanize_config_error(exc: BaseException) -> str:
    """Map a config/test-run failure to a BOUNDED category sentence. TOTAL.

    NEVER the raw message: Pydantic validation errors quote the offending value, a
    ``FileNotFoundError`` carries a path, and the likeliest bad value an admin types is
    a pasted personal email address. The exception's text is INSPECTED to choose a
    category and then discarded — the full detail belongs in the log, which the view
    writes.

    Categories, in the order they are decided (a missing file is an ``OSError`` too, so
    the narrower answer has to come first): missing base → unreadable file → bad grades
    → bad domain → everything else. The first two are decided by TYPE, which is what the
    loader actually raises for an absent ``_base`` (``FileNotFoundError``) and for a torn
    or non-YAML file. ``SystemExit`` — how ``run_pipeline`` reports a config-level
    refusal — lands in the last category, since it carries no diagnosis of its own.
    """
    if isinstance(exc, FileNotFoundError):
        return CONFIG_ERROR_MISSING_BASE
    if isinstance(exc, (yaml.YAMLError, UnicodeDecodeError, OSError)):
        return CONFIG_ERROR_UNREADABLE
    text = str(exc).lower()
    if any(token in text for token in ("grade", "homeroom", "rostering")):
        return CONFIG_ERROR_GRADES
    if "domain" in text:
        return CONFIG_ERROR_DOMAIN
    return CONFIG_ERROR_OTHER


def gate_outcome_for(
    *,
    result: PipelineResult | None,
    error: BaseException | None,
    output_dir_valid: bool,
    expected_files: Iterable[str],
    present_files: Iterable[str],
) -> GateOutcome:
    """Reduce the test conversion's facts to one :class:`GateOutcome`. PURE.

    Precedence is deliberate:

    1. **No usable output folder ⇒ ``REFUSED_NO_OUTPUT_DIR``**, whatever else is true.
       The refusal is real, not cosmetic: ``DataLoader.__init__`` mkdirs
       unconditionally and falls back to a CWD-relative ``data/output`` on a blank
       path, so a run without a validated folder creates a directory somewhere nobody
       asked for. The worker refuses too — this is the state that SAYS so.
    2. **An exception ⇒ ``FAILED``**, with a bounded category in ``note`` and the
       missing-file list still derived (a failed run is exactly when "your extract is
       missing these files" is the useful sentence).
    3. **A result ⇒ ``PASSED``**, carrying its entity counts. Missing files are
       reported alongside rather than downgrading the verdict: a per-entity
       skip-on-empty is legitimate, and the run DID complete.
    4. **Neither ⇒ ``NOT_RUN``.** ``RUNNING`` is the view's own transient state (it
       cannot be derived from a result that does not exist yet), so it is never
       returned here.

    Raises:
        ValueError: both a result and an error were passed — a run cannot both
            succeed and fail, and silently preferring one would hide a bug in the
            worker's handoff.
    """
    if result is not None and error is not None:
        raise ValueError("gate_outcome_for got both a result and an error; a test run has exactly one outcome.")

    if not output_dir_valid:
        return GateOutcome(state=GateState.REFUSED_NO_OUTPUT_DIR)

    absent = missing_files(expected_files, present_files)
    if error is not None:
        return GateOutcome(state=GateState.FAILED, missing_files=absent, note=humanize_config_error(error))
    if result is None:
        return GateOutcome(state=GateState.NOT_RUN)
    return GateOutcome(
        state=GateState.PASSED,
        counts=dict(getattr(result, "entity_counts", {}) or {}),
        missing_files=absent,
    )


# ---------------------------------------------------------------------------
# The stored facts: what was tested, and what wrote the file
# ---------------------------------------------------------------------------


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
class StalenessFact:
    """Why the Files step may ask for another test run, as two independent booleans.

    Attributes:
        version_differs: a DIFFERENT build wrote this overlay.
        base_changed: the base it was authored against no longer resolves to the same
            config (a vendor fix arrived with an app update).
    """

    version_differs: bool = False
    base_changed: bool = False


def overlay_staleness(
    authored: AuthoredWith | None,
    *,
    running_version: str,
    current_base_digest: str | None,
) -> StalenessFact:
    """Compare an overlay's provenance against the running build. TOTAL.

    ``authored is None`` ⇒ both flags False. UNKNOWN provenance is not staleness: every
    hand-written config and every config written before this key existed reads that way,
    and telling those admins their district is out of date would be a fabricated fault.
    The same reasoning applies to a blank field inside the block, and to a
    ``current_base_digest`` of ``None`` (the base could not be loaded — a fact the
    activation digest already covers).

    Activation safety never rests here: that is
    :func:`verified_is_current` over ``authoring.current_digest``. These two booleans
    only decide whether a NOTE explains why another test run is being asked for.
    """
    if authored is None:
        return StalenessFact()
    version_differs = bool(authored.app_version) and authored.app_version != running_version
    base_changed = (
        current_base_digest is not None and bool(authored.base_digest) and authored.base_digest != current_base_digest
    )
    return StalenessFact(version_differs=version_differs, base_changed=base_changed)


def base_label(base: str) -> str:
    """The plain-language name for a base id, falling back to the id itself.

    TOTAL so an id outside :data:`BASE_LABELS` (only reachable if ``ALLOWED_BASES``
    grows without its label — the pinning test is what catches that) still renders
    something rather than blanking the row.
    """
    return BASE_LABELS.get(base, base)
