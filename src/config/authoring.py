"""Authoring layer for USER-authored district mapping overlays (plan 0044 slice 1).

This is the engine behind the district self-service config editor: given a
district's facts — SD number, name, public staff domains, a starting (base)
config, entity selection, grade scopes and per-file filename renames — it emits
a THIN overlay YAML into :func:`src.utils.paths.user_mappings_dir` that the
existing loader, CLI and app can already run (``--sis sd93custom``).

Three properties define the layer:

* **Thin overlay, never a fork.** Only what DIFFERS from the resolved base is
  emitted, so vendor fixes to the bundled base keep flowing through ``_base:``
  deep merge on every app update. A materialised full copy would fork the base
  and silently strand self-serve districts on the day of authoring.
* **No ``version:`` is ever emitted.** The overlay inherits the base's declared
  version through ``loader._resolve_inheritance``, so it never claims a config
  minor it does not use and the bundled-only declared-range parity test stays
  untouched (plan 0044 review #5).
* **Nothing invalid reaches disk.** :func:`write_overlay` builds, then load-backs
  through the real :func:`src.config.loader.validate_overlay` (the same resolve →
  version-gate → user-dir domains floor → Pydantic pipeline ``load_config`` runs),
  and only then writes — atomically. A failed load-back writes NOTHING.

Layering: this module does file I/O (so it is COUNTED, i.e. covered) and depends
only on the config layer, ``src.utils.validators``, ``src.utils.paths`` and
``yaml``. It must never import ``flet`` (UI) or ``pandas`` (ETL) — the creator UI
calls into it, not the other way round.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from src.config.loader import load_config, resolve_config_path, validate_overlay
from src.config.models import MappingConfig, is_valid_district_domain
from src.utils.paths import user_mappings_dir
from src.utils.validators import validate_sis_type
from src.utils.version import app_version

logger = logging.getLogger(__name__)


#: The starting points a district admin may build on — the NON-district-scoped
#: shipped configs. This is a REVIEWED LIST, deliberately not a parameter and not
#: derived from ``available_configs()``: basing an overlay on another district's
#: config would inherit that district's column renames, email formats and grade
#: policy, which is never what a new district wants and is invisible in the thin
#: overlay it produces. Widening this tuple is a code review, by design.
ALLOWED_BASES: tuple[str, ...] = ("myedbc", "mbp_all", "mbp_core", "mbponly")

#: The entities self-service may select. ``StudentAttendance`` is deliberately
#: ABSENT — it stays vendor-authored (plan 0044 non-goal): it needs a per-band
#: ``source_files`` role plus a headerless ``headers`` block and the
#: ``global_config.attendance`` derivation knobs, none of which this layer emits,
#: and it sits outside the ``active_student_ids`` cascade. Enabling it from a form
#: would produce a config that looks configured and rosters nothing.
CREATOR_ENTITIES: tuple[str, ...] = (
    "Students",
    "Staff",
    "Family",
    "Classes",
    "Enrollments",
    "CourseInfo",
    "StudentCourses",
)

#: The self-service config id shape: ``sd<num>custom``. A DISTINCT namespace from
#: the shipped ``sd<num>myedbc`` ids, so a district that later gets a vendor config
#: never collides (both are listed, and both stay distinguishable). It satisfies
#: ``validators._SIS_TYPE_RE`` and ``identity_gate._SD_CONFIG_RE`` unchanged.
#: Lowercase-only: this id becomes a filename stem and a ``--sis`` argument, and a
#: case-variant twin on a case-insensitive filesystem would shadow the original.
CUSTOM_SIS_ID_RE = re.compile(r"^sd\d+custom$")


def _file_header(sis_id: str) -> str:
    """Build the comment header written at the top of every emitted overlay.

    Names the authoring path so a support engineer reading the file knows it was
    NOT hand-rolled, states the honest guarantee that hand edits are validated only
    on the next load, and — since ``sis`` is no longer emitted into the body (see
    ``_ROOT_KEY_ORDER``) — carries the config id here instead, so it stays visible
    to a human reading the file even though the loader never reads a comment.
    """
    return (
        f"# DistrictSync self-service mapping config '{sis_id}' — generated; edits are validated "
        "on next load.\n"
        "# Safe to read and to hand-edit — anything this file does not set is inherited from its "
        "`_base:` config.\n"
    )


#: The overlay's ROOT key order. Emission order is content, not cosmetics: an admin
#: or support engineer opening this file should read the district's identity first
#: and the mechanical overrides after.
#:
#: ``sis`` is DELIBERATELY ABSENT (plan 0044 review fix #2). ``MappingConfig.sis``
#: is the SIS PRODUCT NAME (e.g. ``"MyEducationBC"`` — every ``ALLOWED_BASES``
#: entry declares it), not the config id; the id is the filename stem / ``--sis``
#: argument, tracked separately. Emitting the id as ``sis:`` made a self-service
#: district the only one whose pipeline log line (``Loaded config: sis=...``) read
#: a fabricated product name, and it violated this layer's own minimal-emission
#: rule (a value identical to the base should never be restated). ``sis`` is
#: therefore always INHERITED via ``_base:`` like any other unset key; the id
#: itself is carried in the file's header comment instead — see
#: :func:`_file_header`.
#: ``authored_with`` (plan 0044 S3) is LAST deliberately: it is machine-written
#: PROVENANCE, not a district fact, so it must never sit between the keys an admin
#: reads. ``MappingConfig`` declares ``extra="ignore"``, so the loader never reads it
#: and it can never change what converts — see :func:`authored_with`.
_ROOT_KEY_ORDER: tuple[str, ...] = (
    "_base",
    "district_name",
    "district_domains",
    "global_config",
    "mappings",
    "authored_with",
)


def is_custom_sis_id(sis_id: object) -> bool:
    """TOTAL predicate: does ``sis_id`` have the self-service ``sd<num>custom`` shape?

    Total over ANY object (a non-string is simply not a custom id, never a
    TypeError) because the callers are gates: :func:`delete_overlay` refuses
    anything this rejects, and slice 2's picker badge asks it of whatever id a
    catalog row carries.
    """
    return isinstance(sis_id, str) and CUSTOM_SIS_ID_RE.match(sis_id) is not None


def derive_sis_id(sd_number: int) -> str:
    """Return the ``sd<num>custom`` config id for ``sd_number``.

    The result is passed through :func:`src.utils.validators.validate_sis_type`
    rather than trusted: this value becomes a filename stem AND a ``--sis``
    command-line argument baked into a scheduled task, so the one boundary check
    the rest of the app relies on runs here too.

    Raises:
        ValueError: ``sd_number`` is not a positive int. ``bool`` is rejected
            explicitly — it is an ``int`` subclass, so ``True`` would otherwise
            silently author ``sd1custom``.
    """
    if isinstance(sd_number, bool) or not isinstance(sd_number, int):
        raise ValueError(f"sd_number must be a positive int (got {type(sd_number).__name__}).")
    if sd_number <= 0:
        raise ValueError(f"sd_number must be a positive int (got {sd_number}).")
    return validate_sis_type(f"sd{sd_number}custom")


#: Windows reserved device names (case-insensitive), checked against the filename's
#: STEM before its first dot: ``PureWindowsPath`` and the Win32 filesystem resolve
#: ``CON``/``CON.txt``/``con.anything`` alike to the console device, never to a file
#: on disk — so a rename to one of these would silently point the extractor at a
#: device instead of a GDE file (or hang reading one) rather than raising cleanly.
_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
)

#: Characters Windows forbids in a filename, plus the two that name a DIFFERENT
#: file/stream than the bare name suggests: ``:`` (drive-relative paths like
#: ``"C:x.txt"`` and NTFS Alternate Data Streams like ``"x.txt:stream"``) and the
#: rest (``<>"|?*``) reserved by the Win32 filesystem outright.
_FORBIDDEN_FILENAME_CHARS = frozenset(':<>"|?*')

#: The longest filename component most filesystems (Windows/NTFS, Linux/ext4,
#: macOS/APFS) accept. Refusing past this keeps the failure at authoring time
#: rather than as an opaque OS error the night of the first sync.
_MAX_FILENAME_LENGTH = 255


def _require_bare_filename(value: object, *, label: str) -> str:
    """Validate that ``value`` is a bare filename — a BOUNDARY, not a nicety.

    Every filename here is joined onto the admin's chosen input directory by the
    extractor (``self.input_path / filename``), so anything that can make that join
    escape the chosen folder — or resolve to something other than a plain file in
    it — is rejected:

    * a path separator (``/`` or ``\\``) or a bare ``.``/``..`` component — the
      classic directory-traversal shape;
    * a colon anywhere in the name — on Windows, ``PureWindowsPath("D:/inputs") /
      "C:x.txt"`` evaluates to ``C:x.txt``, DISCARDING the input directory entirely
      (a drive-relative path), and ``"x.txt:stream"`` names an NTFS Alternate Data
      Stream rather than the file itself — both silently defeat the folder the admin
      picked, without ever raising;
    * any of ``<>"|?*`` — forbidden by the Win32 filesystem outright;
    * any control character (``ord(ch) < 32``, including ``\\n``/``\\t``), anywhere
      in the string, not just at the edges — these are invisible in a form field and
      some can be mistaken for a path/argument separator downstream;
    * a Windows reserved device name (``CON``, ``PRN``, ``AUX``, ``NUL``,
      ``COM1``–``COM9``, ``LPT1``–``LPT9``), matched case-insensitively against the
      stem before the first dot — ``"con.txt"`` resolves to the console device, not
      a file, on Windows;
    * longer than 255 characters — past most filesystems' filename limit.

    Leading/trailing whitespace is refused too: it survives YAML round-tripping
    invisibly and would make the file "missing" for reasons nobody can see in the
    form.

    Messages stay generic and do not need to echo the value — filenames are not
    PII, but the actionable information is which check failed, not a echoed string
    that may itself contain control characters.
    """
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string filename (got {type(value).__name__}).")
    if value == "":
        raise ValueError(f"{label} must not be empty.")
    if value != value.strip():
        raise ValueError(f"{label} has leading or trailing whitespace — use the exact filename.")
    if "/" in value or "\\" in value:
        raise ValueError(f"{label} must be a bare filename, with no folder path (got {value!r}).")
    if "\x00" in value:
        raise ValueError(f"{label} must not contain a NUL character.")
    if value in (".", ".."):
        raise ValueError(f"{label} must be a filename, not a directory reference (got {value!r}).")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{label} must not contain a control character.")
    if any(ch in _FORBIDDEN_FILENAME_CHARS for ch in value):
        raise ValueError(
            f"{label} must not contain any of {sorted(_FORBIDDEN_FILENAME_CHARS)!r} — a ':' makes a "
            "drive-relative path or an NTFS alternate-data-stream name, and the rest are forbidden by "
            "the Windows filesystem outright."
        )
    stem = value.split(".", 1)[0]
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{label} must not be a Windows reserved device name (got {value!r}).")
    if len(value) > _MAX_FILENAME_LENGTH:
        raise ValueError(f"{label} must be at most {_MAX_FILENAME_LENGTH} characters long.")
    return value


def validate_source_filename(value: object, *, label: str = "file name") -> str:
    """THE public source-filename boundary: returns ``value`` or raises ``ValueError``.

    A thin, deliberate wrapper over :func:`_require_bare_filename` so a FORM can validate
    ONE typed filename without building an :class:`OverlaySpec` — and so the product keeps
    exactly ONE filename boundary. Every rule (traversal, drive-relative/ADS colons, Win32
    forbidden characters, control characters, reserved device names, length, edge
    whitespace) lives in that function and is shared by both entry points; a second
    spelling in the UI layer is how a validated map still reads the wrong file.

    ``label`` names the FIELD in the message, so a form can say which row failed without
    echoing what was typed.
    """
    return _require_bare_filename(value, label=label)


def folded_filename(value: object) -> str:
    """A filename's case-folded identity — how the FILESYSTEM sees it, not how it is typed.

    Windows and macOS treat ``b.TXT`` and ``B.txt`` as ONE file, so every rule about two
    filenames being "the same" has to fold. THE one spelling of that fold in the product,
    PUBLIC because the rule is not confined to this layer (plan 0044 S4 review, SHOULD 5):
    the chain refusal in :meth:`OverlaySpec.__post_init__`, the duplicate-target refusal in
    :func:`_build_renames`, ``config_editor.missing_files``' presence check and the Files
    step's two-rows-on-one-file refusal all ask it. A rule enforced with ``.lower()`` in one
    place, ``.casefold()`` in another and ``.strip().casefold()`` in a third is not a rule —
    ``.lower()`` alone leaves ``STRASSE.txt``/``straße.txt`` and ``İ``-cased pairs disagreeing
    across two checks that must answer identically.

    TOTAL: a non-string answers ``""``.
    """
    return value.strip().casefold() if isinstance(value, str) else ""


@dataclass(frozen=True)
class OverlaySpec:
    """The district facts a self-service overlay is built from.

    Frozen: :func:`build_overlay` is pure and :func:`write_overlay` load-backs the
    result, so a spec that mutated between build and validate would break the one
    guarantee this layer sells.

    ``__post_init__`` validates only what is CHEAP AND LOCAL — a non-blank name,
    the domain shape (via the single-source :func:`is_valid_district_domain`), the
    base allowlist, the entity allowlist, the bare-filename boundary on every
    rename target (:func:`validate_source_filename`) and the CHAIN refusal — a
    rename target may not also be a rename original, folded. Grade CODES are deliberately NOT validated here: the chain
    ``homeroom_grades ⊆ class_rostering_grades ⊆ student_rostering_grades`` and the
    CEDS vocabulary are properties of the RESOLVED config, and
    ``GlobalConfig.check_rostering_grade_scopes`` is their single source. Duplicating
    that here would be a second, weaker spelling of a rule that already fires at
    load-back — where it can see the inherited values.

    Attributes:
        sd_number: BC school-district number; decides the config id.
        district_name: Human-readable district name (presentation only).
        district_domains: The district's PUBLIC staff email domain(s). May be
            empty ("unclaimed" — the district then shows in every unmatched
            picker state).
        base: One of :data:`ALLOWED_BASES`; becomes ``_base:``.
        enabled_entities: ``None`` inherits the base's list. Otherwise a non-empty
            selection from :data:`CREATOR_ENTITIES`.
        homeroom_grades: ``None`` inherits. CEDS OUTPUT codes (``"KG"``, ``"01"``…).
        class_rostering_grades: ``None`` inherits; a list of CEDS codes; or the
            ``"homeroom"`` sentinel ("roster exactly the homeroom grades").
        student_rostering_grades: ``None`` inherits; a list of CEDS codes.
        source_file_renames: ORIGINAL base filename → the district's filename.
            Keyed by the base filename (not by entity/role) because the unit an
            admin actually renames is the FILE — see :func:`build_overlay`.
    """

    sd_number: int
    district_name: str
    district_domains: tuple[str, ...]
    base: str
    enabled_entities: tuple[str, ...] | None = None
    homeroom_grades: tuple[str, ...] | None = None
    class_rostering_grades: tuple[str, ...] | Literal["homeroom"] | None = None
    student_rostering_grades: tuple[str, ...] | None = None
    source_file_renames: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validates the id shape / positivity as a side effect (fail at construction,
        # not three calls later inside a write).
        derive_sis_id(self.sd_number)

        if not isinstance(self.district_name, str) or not self.district_name.strip():
            raise ValueError("district_name must be a non-blank string.")

        for index, domain in enumerate(self.district_domains, start=1):
            if not is_valid_district_domain(domain):
                # NEVER echo the value: the likeliest bad entry is a pasted personal
                # email address, and this message reaches a log. Same rule the model
                # validator and the loader's user-dir floor already follow.
                raise ValueError(
                    f"district_domains entry {index} of {len(self.district_domains)} is not a bare "
                    "lowercase domain name (e.g. 'sd48.bc.ca'). The offending value is deliberately "
                    "not quoted here."
                )

        if self.base not in ALLOWED_BASES:
            raise ValueError(f"base must be one of {list(ALLOWED_BASES)} (got {self.base!r}).")

        if self.enabled_entities is not None:
            if not self.enabled_entities:
                raise ValueError(
                    "enabled_entities must name at least one entity — an empty list produces no "
                    "output CSVs at all, which can only end at the delivery floor. Pass None to "
                    "inherit the base's selection."
                )
            unknown = [name for name in self.enabled_entities if name not in CREATOR_ENTITIES]
            if unknown:
                raise ValueError(
                    f"enabled_entities contains {unknown}, which self-service cannot author. "
                    f"Allowed: {list(CREATOR_ENTITIES)}."
                )

        renames = dict(self.source_file_renames)
        for original, new in renames.items():
            if not isinstance(original, str) or not original.strip():
                raise ValueError("source_file_renames keys must be non-blank base filenames.")
            validate_source_filename(new, label=f"source_file_renames[{original!r}]")

        # A rename TARGET may never also be a rename ORIGINAL. ``{"A.txt": "B.txt",
        # "B.txt": "C.txt"}`` clears unknown-original, target-collision AND no-divergence
        # today — and A's role then reads the base's B file, i.e. the WRONG DATA with every
        # other guard green. Folded, because ``b.TXT`` and ``B.txt`` are one file on
        # Windows. A self-rename (``A.txt`` → ``a.txt``) is NOT a chain and stays legal.
        folded_originals = {folded_filename(original): original for original in renames}
        chained = sorted(
            f"{original!r} -> {new!r}"
            for original, new in renames.items()
            if folded_filename(new) in folded_originals and folded_filename(new) != folded_filename(original)
        )
        if chained:
            raise ValueError(
                f"source_file_renames chains one file onto another file's own name: {chained}. "
                "A file this district renames cannot also be the new name of a different file — "
                "the first would end up reading the second's data."
            )


# ---------------------------------------------------------------------------
# Pure build
# ---------------------------------------------------------------------------


#: One place a filename is referenced from in a resolved config.
#: ``("mappings", <entity>, <role>)`` or ``("school_year_sources", <key>)``.
_ReferenceSite = tuple[str, ...]


def _reference_sites(resolved_base: MappingConfig) -> dict[_ReferenceSite, str]:
    """Every filename reference in the resolved base, keyed by its site.

    Covers EVERY entity's ``source_files`` — enabled or not. A disabled entity's
    stale reference is not harmless: the moment a later edit enables it, the
    config would read a filename this district never delivers, and nothing would
    have flagged it. It also covers ``global_config.school_year_sources``, whose
    silent fallback to the date heuristic would move every ``append_year_to_id``
    Class ID if it were left behind.
    """
    sites: dict[_ReferenceSite, str] = {}
    for entity, entity_cfg in resolved_base.mappings.items():
        for role, filename in entity_cfg.source_files.items():
            sites[("mappings", entity, role)] = filename
    for key, filename in resolved_base.global_config.school_year_sources.items():
        sites[("school_year_sources", key)] = filename
    return sites


def _grade_value(value: tuple[str, ...] | list[str] | str | None) -> list[str] | str | None:
    """Normalise a grade-scope value to its EMITTED (YAML) shape, for comparison.

    ONE function for BOTH sides of the minimality comparison — the spec's tuples and
    the resolved base's lists — so the two can never be normalised differently and
    make a key look changed when it is not.

    A tuple/list becomes a list (the shape ``_deep_merge`` and Pydantic both see); a
    string passes through unchanged (the only string a validated ``GlobalConfig`` or
    an ``OverlaySpec`` can hold here is the ``"homeroom"`` sentinel, which is compared,
    never rewritten); ``None`` stays ``None`` (= "inherit", emit nothing) and must never
    collapse to ``[]`` — the ETL distinguishes "not set" from "an empty scope".
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return list(value)


def _build_renames(
    spec: OverlaySpec,
    sites: Mapping[_ReferenceSite, str],
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Propagate ``spec.source_file_renames`` to EVERY site that names each original.

    Returns ``(per-entity {entity: {role: new}}, school_year_sources {key: new})``,
    each carrying ONLY the changed keys — which is safe precisely because
    ``loader._deep_merge`` merges dicts key-by-key (lists REPLACE, dicts do not).

    Fails LOUD in two shapes, both of which would otherwise be silent:

    * an ``original`` that appears NOWHERE in the resolved base — a typo would
      simply no-op, and the district would discover it as a missing file at 2 a.m.;
    * two distinct originals renamed to the SAME target (compared CASE-FOLDED, via
      :func:`folded_filename`) — that collapses two source roles onto one file, which is
      never what a filename form means.

    (Not an error: a target that equals a filename the base ALREADY uses at another
    site. Roles legitimately share files in the base — ``CourseInformation.txt`` is
    named by three entities — so filename equality is normal. The invariant that
    matters is that no ONE original ends up spelled two ways; see
    :func:`_assert_no_divergence`.)
    """
    renames = dict(spec.source_file_renames)
    if not renames:
        return {}, {}

    known = set(sites.values())
    unknown = sorted(original for original in renames if original not in known)
    if unknown:
        raise ValueError(
            f"source_file_renames names {unknown}, which the resolved base config never "
            f"references. Rename an existing source file — the base uses: {sorted(known)}."
        )

    # Grouped by the FOLDED target: ``a.txt`` and ``A.txt`` are one file on Windows, so a
    # case-sensitive grouping would let two roles collapse onto one file undetected.
    by_target: dict[str, list[str]] = {}
    for original, new in renames.items():
        by_target.setdefault(folded_filename(new), []).append(original)
    collisions = {renames[originals[0]]: sorted(originals) for originals in by_target.values() if len(originals) > 1}
    if collisions:
        raise ValueError(
            f"source_file_renames maps several different source files onto one filename: {collisions}. "
            "Two source roles reading the same file is never intended — give each its own filename."
        )

    entity_overrides: dict[str, dict[str, str]] = {}
    year_sources: dict[str, str] = {}
    for site, filename in sites.items():
        if filename not in renames:
            continue
        new = renames[filename]
        if site[0] == "mappings":
            _, entity, role = site
            entity_overrides.setdefault(entity, {})[role] = new
        else:
            year_sources[site[1]] = new
    return entity_overrides, year_sources


def _assert_no_divergence(
    spec: OverlaySpec,
    sites: Mapping[_ReferenceSite, str],
    overlay: Mapping[str, Any],
) -> None:
    """Enforce the emission invariant: *no two references to one file may diverge*.

    Checked against the EMITTED overlay rather than against the rename map, so it
    actually tests the emission: for every reference site, the effective filename is
    what the overlay says (when it overrides that site) else the base's value. Group
    those by the site's ORIGINAL filename — each group must resolve to exactly ONE
    filename, and to the renamed one when a rename was asked for.

    Raises ``ValueError`` (never bare ``assert``: this must hold in an optimised
    frozen exe too).
    """
    renames = dict(spec.source_file_renames)
    emitted_entities = overlay.get("mappings", {})
    emitted_year_sources = overlay.get("global_config", {}).get("school_year_sources", {})

    effective_by_original: dict[str, set[str]] = {}
    for site, original in sites.items():
        if site[0] == "mappings":
            _, entity, role = site
            effective = emitted_entities.get(entity, {}).get("source_files", {}).get(role, original)
        else:
            effective = emitted_year_sources.get(site[1], original)
        effective_by_original.setdefault(original, set()).add(effective)

    for original, effective in sorted(effective_by_original.items()):
        expected = renames.get(original, original)
        if effective != {expected}:
            raise ValueError(
                f"Emitted overlay diverges on source file {original!r}: its references resolve to "
                f"{sorted(effective)} but must all resolve to {expected!r}. Refusing to write a "
                "config whose entities disagree about which file they read."
            )


def resolved_digest(config: MappingConfig) -> str:
    """Fingerprint a RESOLVED config: sha256 of its canonical JSON dump. Lowercase hex.

    This is the fact the creator's activation gate is keyed on (plan 0044 S3): the UI
    offers "use this district" only while the digest of what would convert TODAY still
    equals the digest of what was actually test-run.

    **The whole validated model, not a subset.** ``to_raw_dict()`` is the trap it would
    be natural to fall into — it carries only ``mappings`` + ``global_config``, so a
    changed ``district_domains`` or ``version`` would be INVISIBLE to the fingerprint.
    A hand-maintained "fields that matter" list is the other trap: a second spelling of
    the config schema that drifts the first time a key is added. Cost, named rather than
    hidden: a cosmetic ``district_name`` fix also invalidates and re-asks for a test run.
    That is the safe direction — over-firing only re-asks, while under-firing would
    activate something untested.

    **Fail-safe in BOTH directions.** An edit to the overlay OR to the vendor base moves
    the dump (the base's values are resolved INTO this model by ``_base`` deep merge), so
    an app update that changes a base cannot go unnoticed; and because a stored digest
    only ever *matches*, a REFUSED invalidation write leaves a non-matching value behind
    — the fact never depends on its own write succeeding.

    **:func:`authored_with` is invisible here** — ``MappingConfig`` declares
    ``extra="ignore"``, so the provenance key never enters ``model_dump`` and re-writing
    provenance can never self-invalidate the gate.

    Canonical form: ``json.dumps(..., sort_keys=True, separators=(",", ":"),
    ensure_ascii=False)`` — key order, whitespace and unicode escaping all pinned, so the
    digest is stable across loads, processes and platforms.
    """
    canonical = json.dumps(
        config.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def authored_with(resolved_base: MappingConfig, *, base: str, app_version: str) -> dict[str, str]:
    """Build the overlay's ``authored_with`` provenance block. PURE.

    Three facts, all strings: the app version that authored the file, the base id it was
    authored against, and :func:`resolved_digest` of that RESOLVED base. Together they
    answer the only question provenance is asked — "was this district set up against a
    different build or a different base than the one running now?" — which the Files step
    turns into "please run the test again".

    ADVISORY by construction: activation safety is the digest of the district's OWN
    resolved config (:func:`resolved_digest` via ``current_digest``), so this block can
    never permit a run, and a missing or stale one is "unknown provenance", never "stale".
    That is why :func:`build_overlay` may legitimately default it to ``None``.

    Pure on purpose — the version comes in as an argument (``write_overlay`` supplies
    :func:`src.utils.version.app_version`), so building an overlay never reads the
    environment and S1's emission goldens stay byte-stable.
    """
    return {
        "app_version": app_version,
        "base": base,
        "base_digest": resolved_digest(resolved_base),
    }


def build_overlay(
    spec: OverlaySpec,
    *,
    resolved_base: MappingConfig,
    authored_with: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the thin overlay dict for ``spec`` against an already-resolved base. PURE.

    Emits, in this order: ``_base``, ``district_name``, ``district_domains``, then
    ``global_config`` and ``mappings`` — each of the last two only when it has
    content. ``district_domains`` is emitted even when EMPTY: an explicit empty list
    is the honest "this district claims no staff domain" statement, and the loader's
    user-dir floor accepts it.

    **No ``sis:`` either.** ``MappingConfig.sis`` is the SIS PRODUCT NAME, not the
    config id — see :data:`_ROOT_KEY_ORDER` for why it is always left to inherit
    from ``_base``.

    **No ``version:``.** The overlay inherits the base's, which is already supported
    and pinned — see the module docstring.

    **Minimal emission.** A ``global_config`` key is emitted only when it DIFFERS
    from the resolved base's value (``enabled_entities`` and the grade lists compared
    as ORDERED lists — order is meaningful for entity selection and harmless to
    preserve for grades; ``None`` in the spec means "inherit" and emits nothing). So
    vendor fixes to the base keep reaching this district on app update.

    **THE CHAIN-COMPANION TRAP (the one deliberate exception to minimality).**
    Whenever the emission includes ``class_rostering_grades`` or
    ``student_rostering_grades``, it ALSO emits ``homeroom_grades`` explicitly — the
    spec's value if given, else the resolved base's list, EVEN when that is identical
    to the base and minimality would omit it. Why: ``_deep_merge`` REPLACES lists,
    while the chain ``homeroom_grades ⊆ class_rostering_grades ⊆
    student_rostering_grades`` validates on the RESOLVED config
    (``GlobalConfig.check_rostering_grade_scopes``). An overlay that emitted a narrower
    scope alone would leave the chain's lower bound INHERITED — so the district's
    rostering scope would silently change, or stop loading entirely, the day the vendor
    base's homeroom list changed, and the error would name a key this overlay never
    set. Pinning the companion makes the resolved chain self-contained: what was
    validated at authoring time is what loads later.

    (This is a pin, not a rescue: a secondary-only district still has to SAY
    ``homeroom_grades=()`` — inheriting K-7 under ``student_rostering_grades=("08",…)``
    is a genuine chain violation and is correctly refused at load-back.)

    **Rename propagation + the no-divergence invariant.** See :func:`_build_renames`
    and :func:`_assert_no_divergence`.

    **``authored_with``** (plan 0044 S3) is emitted VERBATIM when given and omitted
    entirely when ``None``, which keeps this function PURE — no version lookup, no file
    read — and leaves S1's emission goldens untouched. ``write_overlay`` always supplies
    it (see :func:`authored_with` for why a ``None`` default is safe: provenance is
    advisory and can never permit a run).

    Raises:
        ValueError: base outside :data:`ALLOWED_BASES` (defence in depth — the spec
            already refuses one), an unknown or colliding rename, or a divergent
            emission.
    """
    if spec.base not in ALLOWED_BASES:
        raise ValueError(f"base must be one of {list(ALLOWED_BASES)} (got {spec.base!r}).")

    overlay: dict[str, Any] = {
        "_base": spec.base,
        "district_name": spec.district_name,
        "district_domains": list(spec.district_domains),
    }

    base_global = resolved_base.global_config
    global_config: dict[str, Any] = {}

    if spec.enabled_entities is not None:
        wanted = list(spec.enabled_entities)
        if wanted != list(base_global.enabled_entities):
            global_config["enabled_entities"] = wanted

    grade_emissions: dict[str, Any] = {}
    for name in ("class_rostering_grades", "student_rostering_grades"):
        wanted_value = _grade_value(getattr(spec, name))
        if wanted_value is None:
            continue
        if wanted_value != _grade_value(getattr(base_global, name)):
            grade_emissions[name] = wanted_value

    homeroom_wanted = _grade_value(spec.homeroom_grades)
    base_homeroom = list(base_global.homeroom_grades)
    emit_homeroom = homeroom_wanted is not None and homeroom_wanted != base_homeroom
    if grade_emissions:
        # The chain-companion rule (see the docstring) — unconditional, because the
        # inherited list is exactly what would break the resolved chain.
        emit_homeroom = True
    if emit_homeroom:
        global_config["homeroom_grades"] = homeroom_wanted if homeroom_wanted is not None else base_homeroom
    global_config.update(grade_emissions)

    sites = _reference_sites(resolved_base)
    entity_overrides, year_sources = _build_renames(spec, sites)
    if year_sources:
        global_config["school_year_sources"] = year_sources

    if global_config:
        overlay["global_config"] = global_config
    if entity_overrides:
        overlay["mappings"] = {entity: {"source_files": roles} for entity, roles in sorted(entity_overrides.items())}

    if authored_with is not None:
        overlay["authored_with"] = dict(authored_with)

    _assert_no_divergence(spec, sites, overlay)
    # Emission order is content (see _ROOT_KEY_ORDER) and `yaml.safe_dump(sort_keys=False)`
    # preserves insertion order, so rebuild the dict in the declared order rather than
    # relying on the order the branches above happened to run in.
    return {key: overlay[key] for key in _ROOT_KEY_ORDER if key in overlay}


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def _resolve_base_config(base: str, search_dirs: Sequence[Path] | None) -> MappingConfig:
    """Load and validate the base config the overlay will inherit from.

    ``search_dirs is None`` (the real path) → :func:`load_config`, which resolves the
    real USER-then-BUNDLED pair and reports the winning tier's origin correctly.

    A given ``search_dirs`` is a TEST SEAM: ``load_config``'s own override takes a
    SINGLE ``config_dir``, which cannot express the two-tier pair (a base that itself
    declares ``_base:`` would then fail to find its parent in the other dir), so the
    pair-aware public entry points are used instead — :func:`resolve_config_path` to
    locate, then :func:`validate_overlay` to resolve + gate + validate the bytes.
    Consequence, stated rather than hidden: down that seam the base is validated with
    ``origin="user"``, so an invalid ``district_domains`` row in it would be dropped
    with a WARN instead of raising. That only ever applies to injected dirs; the real
    path keeps the bundled config's loud raise.
    """
    if search_dirs is None:
        return load_config(base)
    located = resolve_config_path(base, search_dirs=search_dirs)
    if located is None:
        tried = ", ".join(str(directory) for directory in search_dirs)
        raise FileNotFoundError(f"Base config '{base}_mapping.yaml' not found in any of: {tried}")
    raw = yaml.safe_load(located.path.read_text(encoding="utf-8")) or {}
    return validate_overlay(raw, search_dirs=search_dirs)


def _atomic_write_text(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` so a reader never observes a partial document.

    Stage in a sibling temp file in the TARGET'S OWN directory (same filesystem, so
    the promote is a true rename) → ``fsync`` the payload → ``os.replace``, which is
    an ATOMIC same-filesystem overwrite. Deliberately not ``shutil.move``, which
    degrades to copy2+unlink on Windows and tears *within* the file — the same
    reasoning ``src/config/app_config._atomic_write_text`` and
    ``src/etl/loader._commit_staged`` document. The private helper there is
    intentionally NOT imported: settings and mapping YAMLs are different artifacts
    with different permission needs, and cross-importing a private would couple them.

    On any failure the staging file is removed and the error PROPAGATES: an existing
    overlay is left byte-intact and the caller learns the write did not happen.
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
        os.replace(staged, target)
        promoted = True
    finally:
        # Covers exceptions AND KeyboardInterrupt/SystemExit — a torn write must not
        # leave staging litter in the admin's mappings folder, where `available_configs`
        # globs and a support engineer reads.
        if not promoted:
            try:
                staged.unlink()
            except OSError:  # pragma: no cover - best-effort cleanup
                logger.debug("Could not remove staging file for '%s'.", target.name)


def overlay_path(sis_id: str) -> Path:
    """Return the user-dir path a self-service overlay for ``sis_id`` lives at.

    Validates the id (it becomes a filename stem) and resolves through
    :func:`user_mappings_dir` at CALL time, never an import-time constant, so the
    test-isolation and ``DISTRICTSYNC_DATA_DIR`` seams redirect it too.
    """
    return user_mappings_dir() / f"{validate_sis_type(sis_id)}_mapping.yaml"


def write_overlay(
    spec: OverlaySpec,
    *,
    overwrite: bool,
    search_dirs: Sequence[Path] | None = None,
) -> Path:
    """Build, LOAD-BACK, then atomically write ``spec``'s overlay. Returns its path.

    ``overwrite`` is a REQUIRED keyword with NO default: replacing a district's active
    mapping config is safety-relevant, so the unsafe call is unrepresentable rather
    than defaulted (a fresh create passes ``False`` and gets ``FileExistsError`` if
    something is already there; an edit/re-visit passes ``True``).

    Order is load-bearing — build → validate → write. The load-back runs the REAL
    :func:`src.config.loader.validate_overlay`, so a config that could not load is
    refused BEFORE any bytes reach the user's ``mappings/`` dir: the dir never holds a
    file the app cannot read, which matters because a broken file there SHADOWS a
    bundled config of the same name.

    Every written overlay carries an ``authored_with`` provenance block (plan 0044 S3)
    — the running :func:`src.utils.version.app_version`, the base id and the base's
    :func:`resolved_digest`. The loader ignores the key (``extra="ignore"``), so it can
    never change what converts; :func:`read_authored_with` reads it back.

    Logs the id and counts only — never the district name, and never a domain.

    Does NOT invalidate the UI's memoised catalog
    (``src/ui_flet/mapping_catalog.reset_catalog_cache``) — the calling UI layer does,
    because this module must not import the UI layer.

    Raises:
        FileExistsError: the target exists and ``overwrite`` is False.
        ValueError / FileNotFoundError: from :func:`build_overlay` or the load-back.
        OSError: the write itself failed (nothing was promoted).
    """
    sis_id = derive_sis_id(spec.sd_number)
    target = overlay_path(sis_id)
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"A mapping config for '{sis_id}' already exists at '{target}'. Pass overwrite=True to replace it."
        )

    resolved_base = _resolve_base_config(spec.base, search_dirs)
    overlay = build_overlay(
        spec,
        resolved_base=resolved_base,
        # ALWAYS stamped (plan 0044 S3) — an overlay with no provenance is one nobody
        # can ask "which build wrote this, against which base?" of, and the answer is
        # what the Files step turns into "run the test again". The version comes from
        # THE single lookup so a frozen exe reports its tag, not "dev".
        authored_with=authored_with(resolved_base, base=spec.base, app_version=app_version()),
    )
    validate_overlay(overlay, search_dirs=search_dirs, label=sis_id)

    text = _file_header(sis_id) + yaml.safe_dump(
        overlay,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    _atomic_write_text(target, text)
    logger.info(
        "Wrote self-service mapping overlay '%s' (base '%s'): %d global_config key(s), "
        "%d entity override(s), %d filename rename(s).",
        sis_id,
        spec.base,
        len(overlay.get("global_config", {})),
        len(overlay.get("mappings", {})),
        len(spec.source_file_renames),
    )
    return target


def delete_overlay(sis_id: str) -> bool:
    """Delete a SELF-SERVICE overlay from the user mappings dir. Returns "was there".

    Two refusals, both fail-loud ``ValueError``:

    * an id that is not :func:`is_custom_sis_id`. A user-dir file may also be a
      hand-placed HOTFIX override of a bundled config (e.g. ``sd40myedbc``, the
      documented partner-customisation path) — deleting one through the self-service
      path would silently revert a district to the shipped mapping mid-season. Only
      ids this layer could have AUTHORED are deletable here.
    * a target that does not resolve INSIDE ``user_mappings_dir()``. Defence in depth:
      :func:`validate_sis_type` already excludes separators and dots, so this can only
      fire if that boundary ever loosens — which is exactly when a deletion must stop.

    Does NOT invalidate the UI's memoised catalog
    (``src/ui_flet/mapping_catalog.reset_catalog_cache``) — the calling UI layer does,
    because this module must not import the UI layer.

    Returns:
        ``False`` when there was no such file (idempotent — a caller retrying a
        discard is not an error), ``True`` when a file was unlinked.
    """
    sis_id = validate_sis_type(sis_id)
    if not is_custom_sis_id(sis_id):
        raise ValueError(
            f"delete_overlay refuses '{sis_id}': it is not a self-service config id "
            "(sd<number>custom). A user-dir file with any other id is a hand-placed override of a "
            "shipped config and must be removed by hand."
        )
    root = user_mappings_dir().resolve()
    target = (root / f"{sis_id}_mapping.yaml").resolve()
    if root not in target.parents:
        raise ValueError(f"delete_overlay refuses a target outside the user mappings dir: '{target}'.")
    if not target.exists():
        logger.info("No self-service mapping overlay to delete for '%s'.", sis_id)
        return False
    target.unlink()
    logger.info("Deleted self-service mapping overlay '%s'.", sis_id)
    return True


# ---------------------------------------------------------------------------
# Read-back: provenance + the current resolved digest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthoredWith:
    """The provenance a written overlay carries — what wrote it, and against what.

    Frozen: it is a fact read off disk, and every consumer (the Files step's staleness
    note in S3, the Mapping card's in S6) only ever compares it.

    Attributes:
        app_version: the DistrictSync version that authored the file (``"dev"`` for an
            unbuilt source checkout — see :func:`src.utils.version.app_version`).
        base: the ``_base`` id it was authored against.
        base_digest: :func:`resolved_digest` of that base AS RESOLVED at authoring time,
            so a later vendor change to the base is detectable.
    """

    app_version: str
    base: str
    base_digest: str


def read_authored_with(sis_id: str) -> AuthoredWith | None:
    """Read a config's ``authored_with`` provenance block. TOTAL — ``None`` on anything else.

    Reads the YAML TEXT (via :func:`src.config.loader.resolve_config_path`, so the
    user-then-bundled tiers resolve exactly as the loader would) rather than a validated
    model, because ``MappingConfig`` declares ``extra="ignore"`` and therefore DROPS this
    key — it is provenance for humans and for the staleness note, never config.

    ``None`` — meaning "unknown provenance", which is never treated as stale — for every
    one of: no such config; the file unreadable or not valid YAML; no ``authored_with``
    key (every hand-written and every bundled config); a value that is not a mapping; or
    any of the three fields missing or not a string. Total on purpose: this is an
    advisory read on a hand-editable file, and it must not be able to break a mount.
    """
    try:
        located = resolve_config_path(sis_id)
        if located is None:
            return None
        raw = yaml.safe_load(located.path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            return None
        block = raw.get("authored_with")
        if not isinstance(block, dict):
            return None
        values = [block.get(name) for name in ("app_version", "base", "base_digest")]
        if not all(isinstance(value, str) for value in values):
            return None
        version, base, digest = values
        return AuthoredWith(app_version=str(version), base=str(base), base_digest=str(digest))
    except (OSError, ValueError, yaml.YAMLError):
        logger.debug("Could not read provenance for config '%s'.", sis_id)
        return None


def current_digest(sis_id: str) -> str | None:
    """:func:`resolved_digest` of what ``sis_id`` would convert with RIGHT NOW, or ``None``.

    The effectful companion to :func:`resolved_digest`: loads through the REAL
    :func:`src.config.loader.load_config` — the same resolve → version-gate → validate
    path the pipeline runs — so the fingerprint covers the district's overlay AND
    everything it inherits from its base.

    TOTAL: any load failure (absent config, unreadable file, failed version gate, invalid
    values after a hand edit) answers ``None``. ``None`` can only ever mean "not current"
    — :func:`src.ui_flet.config_editor.verified_is_current` reads two ``None``s as False —
    so a broken config closes the activation gate instead of holding it open on a
    fingerprint nobody could compute.
    """
    try:
        return resolved_digest(load_config(sis_id))
    except Exception:  # noqa: BLE001 - TOTAL by contract; any load failure means "not current"
        logger.debug("Could not compute the resolved digest for config '%s'.", sis_id)
        return None
