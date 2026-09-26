"""The schema-drift matrix (plan 0053 S13b, ``docs/developer/failure-policy.md`` §5 and §12).

Every column a district export can LOSE, run for real: for every bundled config, every
headered source file its contract fixture writes (``tests/test_contract.py``'s builders,
reused — never copied) that an ACTIVE entity reads, and every column in that file's header,
drop that ONE column from that ONE file and run the whole pipeline in-process. A real export
drifts one file at a time, so a column two files carry (a teacher id in the schedule and in
the demographic) is two cases.

**What each case must show is DERIVED, never restated.** There is no per-column expectation
table here that could drift from the code. What a dropped column may do follows from the
single sources the policy already has:

* **who may notice** — the entities whose ``source_files`` name the file (plus Staff for a
  file serving one of ``staff.TEACHING_ASSIGNMENT_SOURCE_ROLES``, the plan-0052 rescue's
  cross-read), closed over ``outcomes.DEPENDS_ON``. Every OTHER entity must be byte-identical
  to the undropped run, outcome and all;
* **which guards must fire** — the fail-closed (a)/(b) guards are ONE call,
  ``columns.require_columns``, and the matrix records every call the code makes (a spy that
  changes nothing). A guard the UNDROPPED run made with the column among its required ones is
  REQUIRED in the case whenever the drop reached it (the case's call at that site saw the
  frame without the column) or the guard vanished (no call at that site, yet its entity
  finished): the case must then stop the run typed (CRITICAL) or leave that entity
  FAILED/``missing_source_column`` (ISOLATABLE). A new ``missing_mapped`` name never
  satisfies such a column — that is how a fail-closed guard regressing to fail-open turns
  the matrix red, where the "never silent" rule below alone would stay green;
* **how a failure must look** — only a typed ``SourceSchemaError`` may stop the run, raised by
  an entity that may notice, naming the dropped column (or the canonical column it stood in
  for), with a guard class the §5 ``require-columns`` table lists for that entity (a
  ``(caller)`` row covers any entity, and the table has one for both classes — so this is a
  check on the TABLE, not on the entity); the run record says ``source_schema`` with that
  entity FAILED/``missing_source_column``, and the last good output is byte-identical. An
  ISOLATABLE raiser may stop the run only when nothing else built — read off the run record
  the pipeline writes (``ledger.finalize_aborted``), since a raised run returns no result;
* **how a contained failure must look** — an ISOLATABLE entity that fails does so as
  FAILED/``missing_source_column`` (never ``transform_error``, which is what a raw
  ``KeyError`` would read as), its CSV absent from the delivery set and archived (P4);
* **never silent** — an entity whose CSV or outcome changed carries a recorded signal of its
  own (a new ``missing_mapped`` name, a new or recounted outcome note, a changed kind) or is a
  ``DEPENDS_ON`` dependent of an entity that does. That single rule is how the matrix checks
  every (c)/(d)/(e) posture — co-teacher columns (§5 #15, owner ruling 2026-09-25), the homeroom
  teacher name (#35), S11's notes, S6's ``missing_mapped`` — without naming any of them.

**Known findings** (:data:`KNOWN_FINDINGS`) are the combinations whose integrated behaviour
contradicts the policy and awaits a decision. Each is an explicit entry with its reason and
where it is tracked, run as ``xfail(strict=True, raises=KnownFindingReproduced)``: the case
still runs and still asserts, it is xfailed ONLY when every problem is a registered
``(kind, entity)`` pair (the same kind in another entity, or any other problem, is a plain
failure), and it turns RED the day the finding stops reproducing so the entry cannot outlive
the defect. **The registry is EMPTY today:** the two findings the matrix first registered —
§5 #41's silent school-year fallback and #33's authorized column reading ``transform_error``
— were owner-ruled on 2026-09-26 and FIXED in the code (both are now ``require_columns``
guards the matrix derives and requires like any other, with planted-regression twins); the
mechanism stays, exercised by synthetic findings, for the next one (§12).

**Reachability.** After the full matrix, :func:`test_the_matrix_reaches_every_require_columns_guard`
asserts every row of the §5 ``require-columns`` table was RAISED by at least one case, or is in
:data:`UNREACHED_GUARDS` with the reason no case can reach it (a stale entry is red too) — so
"the matrix holds the table" is a measured claim, not an assumption.

**Scope, stated.** HEADERLESS files (a ``headers:`` block — SD40's schedule, SD51's daily
absences) are not in the matrix: a column removed from a headerless export SHIFTS every later
column rather than going missing, a different fault this model does not describe (ROADMAP).
The clock is pinned (:data:`_FROZEN_NOW`, the ``src.etl.transformers.base.datetime`` seam) so
the school-year determination and every date filter are deterministic.

**Runtime and where it runs.** The parametrised matrix carries the ``drift_matrix`` marker,
deselected by the default ``addopts`` and run by its OWN CI job (``drift-matrix`` in
``.github/workflows/ci.yml``, ubuntu + windows) — its own time budget, as the plan's sizing
finding asked; :func:`test_ci_runs_the_deselected_matrix` pins that the deselection can never
orphan it. Everything else in this module (the derivation, the checker's doctored twins, one
real case per outcome class and the planted-regression twins) runs in the default suite.
Measured numbers: DECISIONS 2026-09-25 and 2026-09-26 (S13b). Run it locally with
``python -m pytest tests/test_schema_drift_matrix.py -m drift_matrix``.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache, lru_cache
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
import yaml

from src.config.loader import available_configs, load_config
from src.config.models import MappingConfig
from src.etl.column_names import normalize_column_name
from src.etl.errors import GuardKind, SourceSchemaError
from src.etl.outcomes import (
    DEPENDS_ON,
    EntityCriticality,
    EntityOutcome,
    OutcomeKind,
    OutcomeReason,
    criticality_of,
)
from src.etl.pipeline import run_pipeline
from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.staff import TEACHING_ASSIGNMENT_SOURCE_ROLES
from src.history.store import read_run_records
from src.utils.paths import bundle_mappings_dir
from tests._pins import BUNDLED_CONFIG_COUNT
from tests.test_contract import _DISTRICT_SETUP
from tests.test_failure_policy_parity import _CALLER, _doc_text, _table, _unticked

_REPO = Path(__file__).resolve().parents[1]
_CI_WORKFLOW = _REPO / ".github" / "workflows" / "ci.yml"
_PYPROJECT = _REPO / "pyproject.toml"
_MARKER = "drift_matrix"

#: The pinned clock. Deliberately AFTER the fixtures' school year (2025/2026) rolled over, so
#: the calendar fallback DISAGREES with the schedule's own value: were §5 #41's school-year
#: guard to regress to the silent fallback, every Class ID would move — deterministically, not
#: on some days only (the ``school_year_guard_deleted`` planted regression proves the matrix
#: sees it).
_FROZEN_NOW = datetime(2026, 9, 25, 12, 0)


class _FrozenClock(datetime):
    """``datetime`` whose ``now()`` is :data:`_FROZEN_NOW`; ``strptime`` and friends stay real."""

    @classmethod
    def now(cls, tz=None):  # noqa: ARG003 — signature parity with datetime.now
        return _FROZEN_NOW


def _run(sis: str, input_dir: Path, output_dir: Path):
    """One in-process pipeline run under the pinned clock (the transformers' one ``now()`` seam)."""
    with patch("src.etl.transformers.base.datetime", _FrozenClock):
        return run_pipeline(sis, str(input_dir), str(output_dir))


# --------------------------------------------------------------------------- #
# The fail-closed guards the CODE declares — recorded, never restated          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GuardCall:
    """One ``columns.require_columns`` call a run made: where, for whom, over which columns."""

    #: ``module.Class.function`` — the §5 ``require-columns`` table's own spelling
    site: str
    entity: str
    #: a ``GuardKind`` value
    guard: str
    #: the columns the guard asked for, normalised (trim + lower-case, as the check compares)
    required: frozenset[str]
    #: the columns the checked frame carried, normalised
    available: frozenset[str]
    raised: bool


def _site_of(frame) -> str:
    """``module.Class.function`` for a caller frame, spelled as the parity test's AST walk spells it."""
    module = str(frame.f_globals.get("__name__", "")).rsplit(".", 1)[-1]
    return f"{module}.{frame.f_code.co_qualname.replace('.<locals>', '')}"


def _normalised(names: Iterable[object]) -> frozenset[str]:
    return frozenset(normalize_column_name(str(name)) for name in names)


def _spy(original: Callable[..., None], calls: list[GuardCall]) -> Callable[..., None]:
    def require_columns(available, required, *, entity, guard):
        caller = sys._getframe(1)
        available, required = list(available), list(required)
        raised = False
        try:
            original(available, required, entity=entity, guard=guard)
        except SourceSchemaError:
            raised = True
            raise
        finally:
            calls.append(
                GuardCall(
                    _site_of(caller),
                    str(entity),
                    GuardKind(guard).value,
                    _normalised(required),
                    _normalised(available),
                    raised,
                )
            )

    return require_columns


@contextmanager
def _spying_guards() -> Iterator[list[GuardCall]]:
    """Record every ``require_columns`` call made inside the block; behaviour is unchanged.

    The name is wrapped wherever a ``src`` module binds it — each transformer imports it, and
    ``models`` resolves it lazily through the ``columns`` module, which is wrapped too — so a
    call is recorded whichever binding it goes through.
    """
    calls: list[GuardCall] = []
    mp = pytest.MonkeyPatch()
    try:
        for name, module in list(sys.modules.items()):
            bound = getattr(module, "require_columns", None) if name.startswith("src.") else None
            if callable(bound):
                mp.setattr(module, "require_columns", _spy(bound, calls))
        yield calls
    finally:
        mp.undo()


# --------------------------------------------------------------------------- #
# Deriving the cases and who may notice them — from the config alone          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DriftCase:
    """Drop ``column`` (the fixture's header spelling) from ``filename`` in ``sis``'s fixture."""

    sis: str
    filename: str
    column: str

    @property
    def id(self) -> str:
        return f"{self.sis}:{self.filename}:{self.column}"


def _source_map(config: MappingConfig) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """Over the ACTIVE entities: file → the entities naming it, file → its roles, headerless files.

    Keys are lower-cased: a district's file names are matched case-insensitively.
    """
    readers: dict[str, set[str]] = {}
    roles: dict[str, set[str]] = {}
    headerless: set[str] = set()
    active = set(config.active_entities())
    for entity, entity_cfg in config.mappings.items():
        if entity not in active:
            continue
        headerless.update(name.lower() for name in (entity_cfg.headers or {}))
        for role, filename in (entity_cfg.source_files or {}).items():
            readers.setdefault(filename.lower(), set()).add(entity)
            roles.setdefault(filename.lower(), set()).add(role)
    return readers, roles, headerless


def affected_entities(config: MappingConfig, filename: str) -> frozenset[str]:
    """Every ACTIVE entity whose output may legitimately change when ``filename`` loses a column.

    The entities naming the file in ``source_files``; Staff for a file serving a
    ``TEACHING_ASSIGNMENT_SOURCE_ROLES`` role anywhere (its teacher-of-record rescue reads
    those files without declaring them — ``staff._teacher_of_record_ids``); then the closure
    over ``outcomes.DEPENDS_ON`` (a dependent reads its upstream's published state).
    """
    readers, roles, _ = _source_map(config)
    active = set(config.active_entities())
    affected = set(readers.get(filename.lower(), set()))
    if "Staff" in active and roles.get(filename.lower(), set()) & set(TEACHING_ASSIGNMENT_SOURCE_ROLES):
        affected.add("Staff")
    grew = True
    while grew:
        grew = False
        for entity, upstream in DEPENDS_ON.items():
            if entity in active and entity not in affected and upstream & affected:
                affected.add(entity)
                grew = True
    return frozenset(affected)


def _header(path: Path) -> list[str]:
    return list(pd.read_csv(path, nrows=0, dtype=str).columns)


@cache
def _bundled_config(sis: str) -> MappingConfig:
    """The BUNDLED config (never a user-dir one), loaded once per session — read, never mutated."""
    return load_config(sis, bundle_mappings_dir())


def _enumerate(build_dir: Path) -> list[DriftCase]:
    cases: list[DriftCase] = []
    for sis in sorted(_DISTRICT_SETUP):
        fixture = build_dir / sis
        fixture.mkdir()
        _DISTRICT_SETUP[sis](fixture)
        readers, _, headerless = _source_map(_bundled_config(sis))
        for path in sorted(fixture.iterdir(), key=lambda p: p.name.lower()):
            key = path.name.lower()
            if key in headerless or key not in readers:
                continue
            cases.extend(DriftCase(sis, path.name, column) for column in _header(path))
    return cases


def _drift_cases() -> list[DriftCase]:
    """Every case, built at collection in a throwaway directory (the fixtures are tiny)."""
    with tempfile.TemporaryDirectory(prefix="ds_drift_") as tmp:
        return _enumerate(Path(tmp))


CASES: list[DriftCase] = _drift_cases()


# --------------------------------------------------------------------------- #
# Known findings — explicit, reasoned, tracked, and strict                     #
# --------------------------------------------------------------------------- #
class KnownFindingReproduced(AssertionError):
    """Raised when a case's problems are EXACTLY a registered finding's — the only xfail cause."""


@dataclass(frozen=True)
class KnownFinding:
    name: str
    #: The ``(kind, entity)`` problems this finding produces; a case matching it xfails only on
    #: these — the same kind in another entity is a plain failure.
    problems: frozenset[tuple[str, str]]
    reason: str
    tracked_in: str
    matches: Callable[[DriftCase, MappingConfig], bool] = field(repr=False)


#: EMPTY: both findings S13b first registered were owner-ruled on 2026-09-26 and fixed in the
#: code (DECISIONS 2026-09-26). A new entry needs its reason, where it is tracked, and the exact
#: ``(kind, entity)`` pairs it produces — never a widened checker (§12).
KNOWN_FINDINGS: tuple[KnownFinding, ...] = ()


def stale_findings(findings: Iterable[KnownFinding]) -> list[str]:
    """Every registered finding that matches no case — an entry that outlived its combination."""
    return [f.name for f in findings if not any(f.matches(case, _bundled_config(case.sis)) for case in CASES)]


def _finding_for(case: DriftCase, findings: Iterable[KnownFinding] = KNOWN_FINDINGS) -> KnownFinding | None:
    config = _bundled_config(case.sis)
    hits = [finding for finding in findings if finding.matches(case, config)]
    assert len(hits) <= 1, f"{case.id} matches two known findings: {[h.name for h in hits]}"
    return hits[0] if hits else None


def _matrix_params(findings: Iterable[KnownFinding] = KNOWN_FINDINGS) -> list:
    findings = tuple(findings)
    params = []
    for case in CASES:
        finding = _finding_for(case, findings)
        marks = [pytest.mark.drift_matrix, pytest.mark.integration]
        if finding is not None:
            marks.append(
                pytest.mark.xfail(
                    strict=True,
                    raises=KnownFindingReproduced,
                    reason=f"KNOWN FINDING {finding.name}: tracked in {finding.tracked_in}",
                )
            )
        params.append(pytest.param(case, id=case.id, marks=marks))
    return params


# --------------------------------------------------------------------------- #
# The policy checker — pure, over plain facts, so its doctored twins are cheap #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, order=True)
class RequireColumnsRow:
    site: str
    entity: str
    guard: str


def require_columns_rows(text: str | None = None) -> frozenset[RequireColumnsRow]:
    """The §5 ``require-columns`` table as rows — the doc is the single source."""
    if text is None:
        return _doc_require_columns_rows()
    rows = _table(text, "require-columns")
    return frozenset(
        RequireColumnsRow(_unticked(r.get("site", "")), _unticked(r.get("entity", "")), _unticked(r.get("guard", "")))
        for r in rows
    )


@lru_cache(maxsize=1)
def _doc_require_columns_rows() -> frozenset[RequireColumnsRow]:
    return require_columns_rows(_doc_text())


@dataclass(frozen=True)
class RunFacts:
    """What one run left behind, reduced to what the policy talks about."""

    outcomes: Mapping[str, EntityOutcome]
    #: top-level ``<Entity>.csv`` name → sha256 of its bytes
    csvs: Mapping[str, str]
    #: every file under the output dir (archives included) → sha256
    tree: Mapping[str, str] = field(default_factory=dict)
    error: BaseException | None = None
    record: Mapping | None = None
    #: every ``require_columns`` call the run made (:func:`_spying_guards`)
    guard_calls: tuple[GuardCall, ...] = ()


def _changed(entity: str, base: RunFacts, run: RunFacts) -> bool:
    b, r = base.outcomes[entity], run.outcomes.get(entity)
    csv = f"{entity}.csv"
    if r is None or base.csvs.get(csv) != run.csvs.get(csv):
        return True
    return (b.kind, b.reason, b.rows, b.notes, b.missing_mapped) != (
        r.kind,
        r.reason,
        r.rows,
        r.notes,
        r.missing_mapped,
    )


def _own_signal(entity: str, base: RunFacts, run: RunFacts) -> bool:
    """A recorded signal the ENTITY carries itself: a new ``missing_mapped`` name, a new or
    recounted note, or a changed kind (EMPTY / FAILED are outcomes the reader surfaces)."""
    b, r = base.outcomes[entity], run.outcomes.get(entity)
    if r is None:
        return False
    return bool(set(r.missing_mapped) - set(b.missing_mapped) or dict(r.notes) != dict(b.notes) or r.kind is not b.kind)


def _upstream(entity: str) -> set[str]:
    seen: set[str] = set()
    todo = list(DEPENDS_ON.get(entity, ()))
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(DEPENDS_ON.get(name, ()))
    return seen


def required_guards(column: str, base_calls: Iterable[GuardCall], run: RunFacts) -> frozenset[tuple[str, str]]:
    """The fail-closed guards the CODE declares for ``column`` in this case, as ``(site, entity)``.

    A guard the undropped run made with the column among its required ones is REQUIRED here
    when the drop REACHED it — the case made a call at that site, for that entity, that lacks
    the column and either still asks for it or checks the very frame the guard checked in the
    undropped run minus that column (so a guard edited to stop asking is still caught) — or
    when the guard VANISHED: no call at that site at all, yet the entity finished (BUILT or
    EMPTY). Requires nothing: a call that still saw the column (it reached that frame through
    another file), a call over some OTHER frame at the same site (one site may check several),
    and a guard the run never got to because it stopped first.
    """
    col = normalize_column_name(column)
    base_calls = list(base_calls)
    wanted = {(call.site, call.entity) for call in base_calls if col in call.required}
    seen: dict[tuple[str, str], list[GuardCall]] = defaultdict(list)
    for call in run.guard_calls:
        seen[(call.site, call.entity)].append(call)
    required: set[tuple[str, str]] = set()
    for key in wanted:
        calls = seen.get(key)
        if calls:
            guarded_frames = {
                call.available - {col}
                for call in base_calls
                if (call.site, call.entity) == key and col in call.required and col in call.available
            }
            if any(
                col not in call.available and (col in call.required or call.available in guarded_frames)
                for call in calls
            ):
                required.add(key)
            continue
        outcome = run.outcomes.get(key[1])
        if outcome is not None and outcome.kind in (OutcomeKind.BUILT, OutcomeKind.EMPTY):
            required.add(key)
    return frozenset(required)


def _built_beside(entity: str, run: RunFacts) -> list[str]:
    """The OTHER entities a stopped run had built — off the record the pipeline wrote on the
    way out (``ledger.finalize_aborted``), since a raised run returns no result."""
    recorded = (run.record or {}).get("entity_outcomes") or {}
    built = {name for name, entry in recorded.items() if (entry or {}).get("kind") == OutcomeKind.BUILT.value}
    built |= {o.entity for o in run.outcomes.values() if o.kind is OutcomeKind.BUILT}
    return sorted(built - {entity})


def policy_problems(
    *,
    column: str,
    original_header: Iterable[str],
    affected: frozenset[str],
    base: RunFacts,
    run: RunFacts,
    require_rows: frozenset[RequireColumnsRow],
    required: frozenset[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """Every way ``run`` (the dropped column) contradicts the policy, as ``(kind, entity, detail)``.

    ``base`` is the undropped run over the same inputs; ``run.tree`` / ``base.tree`` are the
    output directory before and after (the run was seeded with the base's outputs);
    ``required`` is :func:`required_guards`. Empty = the declared outcome.
    """
    problems: list[tuple[str, str, str]] = []
    exc = run.error
    if exc is not None:
        if not isinstance(exc, SourceSchemaError):
            return [("untyped_failure", "-", type(exc).__name__)]
        # A typed stop is the strictest outcome, so it discharges every required guard.
        entity = exc.entity
        if criticality_of(entity) is EntityCriticality.ISOLATABLE:
            built = _built_beside(entity, run)
            if built:
                problems.append(("isolatable_escaped", entity, f"the run stopped although {built} built"))
        if entity not in affected:
            problems.append(("raiser_not_affected", entity, f"affected = {sorted(affected)}"))
        named = {normalize_column_name(c) for c in exc.columns}
        header = {normalize_column_name(c) for c in original_header}
        # The dropped column, or a canonical column it stood in for (e.g. the schedule's
        # `District Course Code` renamed to `Course Code` before the course join): then none
        # of the named columns was ever in the file under its own name.
        if normalize_column_name(column) not in named and named & header:
            problems.append(("error_misnames_column", entity, f"names {sorted(named)}"))
        rows = {(r.entity, r.guard) for r in require_rows}
        guard = GuardKind(exc.guard).value
        if (entity, guard) not in rows and (_CALLER, guard) not in rows:
            problems.append(("guard_not_in_require_columns_table", entity, guard))
        record = run.record or {}
        entry = (record.get("entity_outcomes") or {}).get(entity, {})
        if (record.get("status"), record.get("error_category")) != ("failed", "source_schema") or (
            entry.get("kind"),
            entry.get("reason"),
        ) != ("failed", "missing_source_column"):
            problems.append(("record_mismatch", entity, f"{record.get('error_category')} / {entry}"))
        if run.tree != base.tree:
            problems.append(("output_touched", entity, "the last good output changed"))
        return problems

    for site, entity in sorted(required):
        outcome = run.outcomes.get(entity)
        contained = (
            criticality_of(entity) is EntityCriticality.ISOLATABLE
            and outcome is not None
            and outcome.kind is OutcomeKind.FAILED
            and outcome.reason is OutcomeReason.MISSING_SOURCE_COLUMN
        )
        if not contained:
            got = "absent" if outcome is None else f"{outcome.kind.value}/{outcome.reason.value}"
            problems.append(
                ("fail_closed_guard_bypassed", entity, f"{site} guards the column, yet the run went on ({got})")
            )

    changed = {entity for entity in base.outcomes if _changed(entity, base, run)}
    signalled = {entity for entity in changed if _own_signal(entity, base, run)}
    for entity in sorted(changed):
        outcome = run.outcomes.get(entity)
        if entity not in affected:
            problems.append(("unaffected_changed", entity, "it reads nothing from the file"))
            continue
        if outcome is not None and outcome.kind is OutcomeKind.FAILED:
            if outcome.reason is not OutcomeReason.MISSING_SOURCE_COLUMN:
                problems.append(("isolated_reason", entity, outcome.reason.value))
            if f"{entity}.csv" in run.csvs:
                problems.append(("substituted", entity, "a FAILED entity's CSV is in the delivery set"))
            continue
        if entity not in signalled and not (_upstream(entity) & signalled):
            problems.append(("silent_change", entity, "changed with no recorded signal"))
    return problems


# --------------------------------------------------------------------------- #
# Running a case                                                               #
# --------------------------------------------------------------------------- #
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _csvs(directory: Path) -> dict[str, str]:
    return {p.name: _sha(p) for p in sorted(directory.glob("*.csv"))}


def _tree(directory: Path) -> dict[str, str]:
    return {str(p.relative_to(directory)): _sha(p) for p in sorted(directory.rglob("*")) if p.is_file()}


@dataclass(frozen=True)
class Baseline:
    sis: str
    input_dir: Path
    output_dir: Path
    facts: RunFacts


def _build_baseline(sis: str, root: Path) -> Baseline:
    input_dir, output_dir = root / "input", root / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()
    _DISTRICT_SETUP[sis](input_dir)
    with _spying_guards() as calls:
        result = _run(sis, input_dir, output_dir)
    facts = RunFacts(
        outcomes={o.entity: o for o in result.entity_outcomes},
        csvs=_csvs(output_dir),
        tree=_tree(output_dir),
        guard_calls=tuple(calls),
    )
    return Baseline(sis, input_dir, output_dir, facts)


#: Something a twin does to the CODE for the length of one case run (a regression, planted).
Tamper = Callable[[pytest.MonkeyPatch], None]


def run_case(
    case: DriftCase, baseline: Baseline, work: Path, *, tamper: Tamper | None = None
) -> tuple[RunFacts, list[str]]:
    """Run ``case`` over a copy of the baseline's inputs, seeded with its outputs.

    ``tamper`` (the twins only) patches the code for the run and is undone before the checker
    looks — the matrix itself never passes one.
    """
    input_dir, output_dir = work / "input", work / "output"
    shutil.copytree(baseline.input_dir, input_dir)
    shutil.copytree(baseline.output_dir, output_dir)
    target = input_dir / case.filename
    frame = pd.read_csv(target, dtype=str, keep_default_na=False)
    header = list(frame.columns)
    assert case.column in header, f"{case.id}: the fixture no longer carries the column"
    frame.drop(columns=[case.column]).to_csv(target, index=False)

    error: BaseException | None = None
    outcomes: dict[str, EntityOutcome] = {}
    mp = pytest.MonkeyPatch()
    try:
        if tamper is not None:
            tamper(mp)
        with _spying_guards() as calls:
            try:
                result = _run(case.sis, input_dir, output_dir)
                outcomes = {o.entity: o for o in result.entity_outcomes}
            except Exception as exc:  # noqa: BLE001 — the checker classifies whatever escaped
                error = exc
    finally:
        mp.undo()
    records = read_run_records() or []
    record = records[0] if records else None
    return RunFacts(outcomes, _csvs(output_dir), _tree(output_dir), error, record, tuple(calls)), header


def _problems_for(
    case: DriftCase, baseline: Baseline, work: Path, *, tamper: Tamper | None = None
) -> tuple[RunFacts, list[tuple[str, str, str]]]:
    run, header = run_case(case, baseline, work, tamper=tamper)
    problems = policy_problems(
        column=case.column,
        original_header=header,
        affected=affected_entities(_bundled_config(case.sis), case.filename),
        base=baseline.facts,
        run=run,
        require_rows=require_columns_rows(),
        required=required_guards(case.column, baseline.facts.guard_calls, run),
    )
    return run, problems


class BaselineCache:
    """The undropped run per config, built on first use and kept for the session.

    Built lazily from inside a test — i.e. while the function-scoped ``isolated_user_profile``
    is active — but under its OWN profile directory (the ``user_data_dir`` seam redirected for
    the build only, as ``tests/test_contract.py``'s ``district_output`` does), so a baseline's
    run record never lands in the case's profile, where ``read_run_records()[0]`` must be the
    case's own run. (An indirectly parametrised module fixture is rebuilt for EVERY case once
    each case is its own parameter set — measured: it doubled the matrix's runtime.)
    """

    def __init__(self, factory: pytest.TempPathFactory) -> None:
        self._factory = factory
        self._built: dict[str, Baseline] = {}

    def get(self, sis: str) -> Baseline:
        if sis not in self._built:
            root = self._factory.mktemp(f"drift_{sis}")
            mp = pytest.MonkeyPatch()
            mp.setattr("src.utils.paths.user_data_dir", lambda: root / ".districtsync")
            try:
                self._built[sis] = _build_baseline(sis, root)
            finally:
                mp.undo()
        return self._built[sis]


@pytest.fixture(scope="session")
def baselines(tmp_path_factory: pytest.TempPathFactory) -> BaselineCache:
    return BaselineCache(tmp_path_factory)


# --------------------------------------------------------------------------- #
# THE MATRIX (marker `drift_matrix` — its own CI job)                          #
# --------------------------------------------------------------------------- #
#: Every ``(site, entity, guard)`` a matrix case made RAISE, and every case that ran — this
#: session's evidence for :func:`test_the_matrix_reaches_every_require_columns_guard`.
_REACHED: set[tuple[str, str, str]] = set()
_RAN: set[str] = set()


@pytest.mark.parametrize("case", _matrix_params())
def test_a_dropped_column_has_its_declared_outcome(baselines: BaselineCache, case: DriftCase, tmp_path: Path) -> None:
    run, problems = _problems_for(case, baselines.get(case.sis), tmp_path / "case")
    _REACHED.update((call.site, call.entity, call.guard) for call in run.guard_calls if call.raised)
    _RAN.add(case.id)
    judge(case.id, problems, _finding_for(case))


#: The ``require-columns`` rows (keyed ``(site, guard)``) no matrix case can make raise, each
#: with WHY. A stale entry — a row some case does reach — is red, so this cannot outlive its
#: reason. The gaps are ROADMAP ("the drift matrix cannot reach every require-columns guard").
UNREACHED_GUARDS: Mapping[tuple[str, str], str] = {
    ("models.FieldAppendYear.apply", "join_key"): (
        "no bundled config maps an append-year ID through the field-map engine (§5 #29), so nothing calls it"
    ),
    ("enrollments.EnrollmentTransformer._homeroom_enrollments", "pii_scope"): (
        "structurally shadowed: the homeroom split's grade column is the Students mapping's Grade, which "
        "Classes' own homeroom guard (which runs first, Enrollments DEPENDS_ON Classes) checks on the same frame"
    ),
    ("enrollments.EnrollmentTransformer._subject_enrollments", "pii_scope"): (
        "structurally shadowed: the subject split's grade column is checked first by Classes' subject guard on "
        "the same schedule frame, and a CRITICAL stop there ends the run"
    ),
    ("blended.BlendedClassDetector.detect", "join_key"): (
        "fixture gap: every contract fixture but SD40's writes a header-only ClassInformation, so detection "
        "stops at 'No class info data found' before #39; SD40's one row falls back to its schedule, which is "
        "headerless and out of the matrix"
    ),
    ("base.BaseTransformer.assign_class_ids", "join_key"): (
        "structurally shadowed: both callers check the Class ID column first in their own guard (Classes' "
        "#29 subject guard, Enrollments' #28/#36 subject guard), and a CRITICAL stop there ends the run"
    ),
    ("staff.StaffTransformer._merge_roster", "join_key"): (
        "config gap: no bundled config gives Staff a second source file (a roster), so the merge returns "
        "before the guard"
    ),
}


def reachability_problems(
    rows: Iterable[RequireColumnsRow],
    reached: Iterable[tuple[str, str, str]],
    unreached: Mapping[tuple[str, str], str],
) -> list[str]:
    """Every ``require-columns`` row no case raised and no entry excuses, and every stale entry."""
    rows = sorted(rows)
    reached = set(reached)
    problems: list[str] = []
    for row in rows:
        hit = any(
            site == row.site and guard == row.guard and row.entity in (_CALLER, entity)
            for site, entity, guard in reached
        )
        key = (row.site, row.guard)
        if hit and key in unreached:
            problems.append(
                f"{row.site} ({row.guard}) is listed as unreachable but a case raised it — remove the entry"
            )
        elif not hit and key not in unreached:
            problems.append(f"no matrix case raised {row.site} ({row.entity}, {row.guard})")
    keys = {(row.site, row.guard) for row in rows}
    problems += [
        f"{site} ({guard}): an unreachable entry that is no require-columns row"
        for site, guard in unreached
        if (site, guard) not in keys
    ]
    return problems


@pytest.mark.drift_matrix
@pytest.mark.integration
def test_the_matrix_reaches_every_require_columns_guard() -> None:
    """Runs AFTER the parametrised matrix (definition order), over what its cases recorded."""
    missing = {case.id for case in CASES} - _RAN
    if missing:
        pytest.skip(f"only part of the matrix ran in this session ({len(missing)} case(s) not run)")
    assert reachability_problems(require_columns_rows(), _REACHED, UNREACHED_GUARDS) == []


def judge(case_id: str, problems: list[tuple[str, str, str]], finding: KnownFinding | None) -> None:
    """Pass on no problem; raise :class:`KnownFindingReproduced` ONLY when every problem is a
    ``(kind, entity)`` pair the registered finding produces; any other problem is a plain
    ``AssertionError``."""
    if finding is not None and problems and {(kind, entity) for kind, entity, _ in problems} <= finding.problems:
        raise KnownFindingReproduced(f"{case_id}: {finding.name} — {problems}")
    assert problems == [], f"{case_id} contradicts failure-policy.md: {problems}"


# --------------------------------------------------------------------------- #
# The machinery, in the default suite                                          #
# --------------------------------------------------------------------------- #
def test_the_matrix_covers_every_bundled_config() -> None:
    bundled = set(available_configs(bundle_mappings_dir()))
    assert len(bundled) == BUNDLED_CONFIG_COUNT
    assert set(_DISTRICT_SETUP) == bundled, "every bundled config needs a contract fixture"
    assert {case.sis for case in CASES} == bundled, "every bundled config contributes cases"


def test_every_readable_headered_source_file_contributes_every_header_column() -> None:
    """Non-vacuity of the enumeration: rebuild one config's fixture and recount by hand."""
    with tempfile.TemporaryDirectory(prefix="ds_drift_check_") as tmp:
        d = Path(tmp)
        _DISTRICT_SETUP["myedbc"](d)
        expected = {
            ("myedbc", p.name, column)
            for p in d.iterdir()
            if p.name in {"StudentDemographicInformation.txt", "StudentSchedule.txt"}
            for column in _header(p)
        }
    got = {(c.sis, c.filename, c.column) for c in CASES}
    assert expected <= got
    assert ("myedbc", "StudentSchedule.txt", "Master Timetable ID") in got
    assert len(CASES) >= 900, len(CASES)  # measured 2026-09-25: 1,013 — a floor, not a pin


def test_headerless_and_unread_files_are_out_of_the_matrix_and_their_neighbours_are_in() -> None:
    files = {(c.sis, c.filename) for c in CASES}
    assert ("sd40myedbc", "SD-40_StudentSchedule.csv") not in files  # headerless (a `headers:` block)
    assert ("sd40myedbc", "SD-40_StudentDemographic.csv") in files  # the twin: its headered neighbour
    assert ("sd51myedbc", "EmergencyContactInformation.txt") not in files  # Family is off: nothing reads it
    assert ("sd51myedbc", "StudentSchedule.txt") in files


def test_affected_entities_follow_source_files_the_staff_rescue_and_depends_on() -> None:
    config = _bundled_config("myedbc")
    assert affected_entities(config, "StudentSchedule.txt") == {"Classes", "Enrollments", "Staff"}
    # Enrollments never names CourseInformation, but reads Classes' published artifacts.
    assert affected_entities(config, "CourseInformation.txt") == {"Classes", "Enrollments"}
    assert affected_entities(config, "StudentDemographicInformation.txt") == {
        "Students",
        "Family",
        "Classes",
        "Enrollments",
        "Staff",
    }
    # The twin: a file nobody names changes nothing, and the name match ignores case.
    assert affected_entities(config, "NoSuchFile.txt") == frozenset()
    assert affected_entities(config, "studentschedule.TXT") == {"Classes", "Enrollments", "Staff"}


def _synthetic_finding(matches: Callable[[DriftCase, MappingConfig], bool]) -> KnownFinding:
    """A doctored finding for the registry's twins — the real registry is empty today."""
    return KnownFinding(
        name="synthetic",
        problems=frozenset({("silent_change", "Classes"), ("silent_change", "Enrollments")}),
        reason="a doctored finding",
        tracked_in="nowhere",
        matches=matches,
    )


_ONE_CASE = DriftCase("myedbc", "StudentSchedule.txt", "Master Timetable ID")


def test_every_known_finding_matches_a_case_and_names_where_it_is_tracked() -> None:
    assert stale_findings(KNOWN_FINDINGS) == []
    for finding in KNOWN_FINDINGS:
        assert finding.tracked_in and finding.reason and finding.problems
        assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in finding.problems), "(kind, entity) pairs"
    # the twins: a finding that matches a real case is live; one that matches none is stale
    assert stale_findings([_synthetic_finding(lambda case, _config: case == _ONE_CASE)]) == []
    assert stale_findings([_synthetic_finding(lambda _case, _config: False)]) == ["synthetic"]


def _xfailed(params: list) -> dict[str, object]:
    marked = {}
    for param in params:
        (case,) = param.values
        xfails = [m for m in param.marks if m.name == "xfail"]
        assert {m.name for m in param.marks} >= {_MARKER, "integration"}
        if xfails:
            (mark,) = xfails
            assert mark.kwargs["strict"] is True and mark.kwargs["raises"] is KnownFindingReproduced
            marked[case.id] = mark
    return marked


def test_exactly_the_known_finding_cases_carry_a_strict_xfail() -> None:
    expected = {case.id for case in CASES if _finding_for(case) is not None}
    assert set(_xfailed(_matrix_params())) == expected
    assert expected == set(), "the registry is empty today — every case must pass outright"
    # non-vacuity: a registered finding marks exactly its case, strict, and nothing else
    synthetic = _synthetic_finding(lambda case, _config: case == _ONE_CASE)
    assert set(_xfailed(_matrix_params([synthetic]))) == {_ONE_CASE.id}


class TestTheJudge:
    """The xfail is earned: only the registered problems xfail, anything else is a real failure."""

    _FINDING = _synthetic_finding(lambda _case, _config: False)

    def test_the_registered_problems_reproduce_the_finding(self) -> None:
        with pytest.raises(KnownFindingReproduced):
            judge("c", [("silent_change", "Classes", "d"), ("silent_change", "Enrollments", "d")], self._FINDING)
        with pytest.raises(KnownFindingReproduced):
            judge("c", [("silent_change", "Classes", "d")], self._FINDING)

    def test_an_extra_problem_is_a_plain_failure_not_the_finding(self) -> None:
        with pytest.raises(AssertionError) as raised:
            judge("c", [("silent_change", "Classes", "d"), ("untyped_failure", "-", "KeyError")], self._FINDING)
        assert type(raised.value) is AssertionError

    def test_the_registered_kind_in_another_entity_is_a_plain_failure(self) -> None:
        for entity in ("Staff", "Students"):
            with pytest.raises(AssertionError) as raised:
                judge("c", [("silent_change", entity, "d")], self._FINDING)
            assert type(raised.value) is AssertionError

    def test_a_fixed_finding_passes_so_the_strict_xfail_turns_red(self) -> None:
        judge("c", [], self._FINDING)  # no raise: pytest reports XPASS(strict) → a failure

    def test_without_a_finding_any_problem_fails_and_none_passes(self) -> None:
        with pytest.raises(AssertionError) as raised:
            judge("c", [("silent_change", "Classes", "d")], None)
        assert type(raised.value) is AssertionError
        judge("c", [], None)


def test_the_require_columns_table_parses() -> None:
    rows = require_columns_rows()
    assert RequireColumnsRow("base.BaseTransformer.apply_row_filters", _CALLER, "pii_scope") in rows
    assert {(row.entity, row.guard) for row in rows} >= {("Classes", "join_key"), (_CALLER, "pii_scope")}
    # non-vacuous without a count literal (the equality with the code is the parity test's)
    assert len(rows) > len(UNREACHED_GUARDS) and {key[0] for key in UNREACHED_GUARDS} <= {row.site for row in rows}


class TestReachability:
    """The reachability check's doctored twins (the real one runs after the matrix)."""

    _ROWS = frozenset(
        {
            RequireColumnsRow("m.f", "Classes", "join_key"),
            RequireColumnsRow("m.helper", _CALLER, "pii_scope"),
        }
    )

    def test_every_row_raised_is_clean(self) -> None:
        reached = {("m.f", "Classes", "join_key"), ("m.helper", "Family", "pii_scope")}
        assert reachability_problems(self._ROWS, reached, {}) == []

    def test_an_unreached_row_is_red_unless_excused(self) -> None:
        reached = {("m.helper", "Family", "pii_scope")}
        assert reachability_problems(self._ROWS, reached, {}) == ["no matrix case raised m.f (Classes, join_key)"]
        assert reachability_problems(self._ROWS, reached, {("m.f", "join_key"): "why"}) == []

    def test_a_literal_entity_row_needs_that_entity_and_that_guard(self) -> None:
        reached = {("m.f", "Staff", "join_key"), ("m.f", "Classes", "pii_scope"), ("m.helper", "X", "pii_scope")}
        assert reachability_problems(self._ROWS, reached, {}) == ["no matrix case raised m.f (Classes, join_key)"]

    def test_a_stale_or_unknown_entry_is_red(self) -> None:
        reached = {("m.f", "Classes", "join_key"), ("m.helper", "Family", "pii_scope")}
        assert reachability_problems(self._ROWS, reached, {("m.f", "join_key"): "why"}) == [
            "m.f (join_key) is listed as unreachable but a case raised it — remove the entry"
        ]
        assert reachability_problems(self._ROWS, reached, {("m.gone", "join_key"): "why"}) == [
            "m.gone (join_key): an unreachable entry that is no require-columns row"
        ]

    def test_every_real_entry_names_a_real_row_with_a_reason(self) -> None:
        keys = {(row.site, row.guard) for row in require_columns_rows()}
        for key, reason in UNREACHED_GUARDS.items():
            assert key in keys and reason.strip(), key


# One real case per outcome class — the positive twins proving the matrix can see each one.
_TWINS = {
    "run_fails_typed": DriftCase("myedbc", "StudentSchedule.txt", "Master Timetable ID"),
    # §5 #41 (owner ruling 2026-09-26): the school-year source column is a fail-closed guard now
    "school_year_stops": DriftCase("myedbc", "StudentSchedule.txt", "School Year"),
    "isolated": DriftCase("unitychristianmyedbc", "EmergencyContactInformation.txt", "Parent Auth / Guardian"),
    # §5 #33 (owner ruling 2026-09-26): the daily authorized flag is named, never transform_error
    "attendance_isolated": DriftCase("sd60myedbc", "Spaces_DailyAbs.txt", "Authorized Am"),
    "signalled": DriftCase("myedbc", "StudentDemographicInformation.txt", "Legal First Name"),
    "unchanged": DriftCase("myedbc", "StudentDemographicInformation.txt", "Previous school number"),
}


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(_TWINS))
def test_one_real_case_per_outcome_class(name: str, baselines: BaselineCache, tmp_path: Path) -> None:
    case = _TWINS[name]
    assert case in CASES, f"{case.id} left the matrix"
    base = baselines.get(case.sis)
    run, problems = _problems_for(case, base, tmp_path / "case")
    assert problems == []
    changed = {e for e in base.facts.outcomes if _changed(e, base.facts, run)}
    required = required_guards(case.column, base.facts.guard_calls, run)
    if name == "run_fails_typed":
        assert isinstance(run.error, SourceSchemaError) and run.error.entity == "Classes"
        assert run.record is not None and run.record["error_category"] == "source_schema"
        assert run.tree == base.facts.tree
        assert any(call.raised and call.entity == "Classes" for call in run.guard_calls)
    elif name == "school_year_stops":
        err = run.error
        assert isinstance(err, SourceSchemaError) and (err.entity, err.columns) == ("Classes", ("school year",))
        assert run.record is not None and run.record["error_category"] == "source_schema"
        assert run.tree == base.facts.tree
        # the guard is DERIVED from the code: the undropped run made it over the column
        assert ("classes.ClassTransformer._require_school_year_source", "Classes") in required
    elif name == "attendance_isolated":
        assert run.error is None and changed == {"StudentAttendance"}, "rostering ships unchanged"
        outcome = run.outcomes["StudentAttendance"]
        assert (outcome.kind, outcome.reason) == (OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN)
        # S7 labels: the column in config spelling; no file — StudentAttendance declares two
        assert (outcome.labels, outcome.file_label) == (("authorized am",), "")
        assert outcome.notes == ()  # the note cannot survive a FAILED outcome (the WARNING is logged)
        assert "StudentAttendance.csv" not in run.csvs
        assert required == {("student_attendance.StudentAttendanceTransformer._build_daily_rows", "StudentAttendance")}
    elif name == "isolated":
        assert run.error is None and changed == {"Family"}
        assert run.outcomes["Family"].kind is OutcomeKind.FAILED
        assert run.outcomes["Family"].reason is OutcomeReason.MISSING_SOURCE_COLUMN
        assert "Family.csv" not in run.csvs
        # the guard is DERIVED from the code: the row filter declared the column in the baseline
        assert required == {("base.BaseTransformer.apply_row_filters", "Family")}
    elif name == "signalled":
        assert run.error is None and changed == {"Students"} and required == frozenset()
        assert "Legal First Name" in run.outcomes["Students"].missing_mapped
    else:
        assert run.error is None and changed == set() and required == frozenset()


def test_the_spy_records_the_guards_by_the_tables_own_site_names(baselines: BaselineCache) -> None:
    calls = baselines.get("myedbc").facts.guard_calls
    sites = {call.site for call in calls}
    assert sites <= {row.site for row in require_columns_rows()}, sites - {r.site for r in require_columns_rows()}
    assert len(sites) >= 5 and not any(call.raised for call in calls)  # a clean run raises nothing


# Planted regressions, one per mechanism: each must turn the matrix RED on a real case.
def _row_filters_fail_open(mp: pytest.MonkeyPatch) -> None:
    """§5 #1 regressed to fail-OPEN: a row filter whose column is absent is skipped."""
    original = BaseTransformer.apply_row_filters

    def fail_open(df, filters, entity_name):
        present = _normalised(df.columns)
        kept = [f for f in filters if normalize_column_name(str(f["column"])) in present]
        return original(df, kept, entity_name)

    mp.setattr(BaseTransformer, "apply_row_filters", staticmethod(fail_open))


def _student_guards_removed(mp: pytest.MonkeyPatch) -> None:
    """Every Students guard (§5 #2/#2a/#3/#27(ii)) deleted."""
    mp.setattr("src.etl.transformers.students.require_columns", lambda *args, **kwargs: None)


def _school_year_guard_removed(mp: pytest.MonkeyPatch) -> None:
    """§5 #41 regressed: the school year silently from the calendar again."""
    mp.setattr(
        "src.etl.transformers.classes.ClassTransformer._require_school_year_source",
        staticmethod(lambda _context: None),
    )


def _attendance_guard_removed(mp: pytest.MonkeyPatch) -> None:
    """§5 #33 regressed: the authorized flag back to #23's untyped category-map miss."""
    mp.setattr("src.etl.transformers.student_attendance.require_columns", lambda *args, **kwargs: None)


def _bulkhead_removed(mp: pytest.MonkeyPatch) -> None:
    """S4's bulkhead gone: every entity treated as CRITICAL, so an isolatable failure stops the run."""
    mp.setattr("src.etl.pipeline.criticality_of", lambda _entity: EntityCriticality.CRITICAL)


_REGRESSIONS: dict[str, tuple[DriftCase, Tamper, str]] = {
    "pii_scope_fails_open": (_TWINS["isolated"], _row_filters_fail_open, "fail_closed_guard_bypassed"),
    "sd60_pii_scope_fails_open": (
        DriftCase("sd60myedbc", "Spaces_EmergencyContactENH.txt", "Parent Auth / Guardian"),
        _row_filters_fail_open,
        "fail_closed_guard_bypassed",
    ),
    "user_id_guard_deleted": (
        DriftCase("sd27myedbc", "StudentDemographicInformation.txt", "Student Number"),
        _student_guards_removed,
        "fail_closed_guard_bypassed",
    ),
    "bulkhead_removed": (_TWINS["isolated"], _bulkhead_removed, "isolatable_escaped"),
    "school_year_guard_deleted": (
        _TWINS["school_year_stops"],
        _school_year_guard_removed,
        "fail_closed_guard_bypassed",
    ),
    "attendance_guard_deleted": (_TWINS["attendance_isolated"], _attendance_guard_removed, "isolated_reason"),
}


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(_REGRESSIONS))
def test_a_planted_regression_turns_the_matrix_red(name: str, baselines: BaselineCache, tmp_path: Path) -> None:
    case, tamper, kind = _REGRESSIONS[name]
    assert case in CASES, f"{case.id} left the matrix"
    base = baselines.get(case.sis)
    _, clean = _problems_for(case, base, tmp_path / "clean")
    assert clean == [], "the twin: without the regression the case conforms"
    _, problems = _problems_for(case, base, tmp_path / "tampered", tamper=tamper)
    assert kind in _kinds(problems), problems


# --------------------------------------------------------------------------- #
# Doctored twins for the checker — each problem kind fires, and only then      #
# --------------------------------------------------------------------------- #
#: A doctored table carrying BOTH real ``(caller)`` rows, as the real one does.
_ROWS = frozenset(
    {
        RequireColumnsRow("classes.f", "Classes", "join_key"),
        RequireColumnsRow("base.helper", _CALLER, "pii_scope"),
        RequireColumnsRow("base.other", _CALLER, "join_key"),
    }
)


def _built(entity: str, rows: int = 3, **kw) -> EntityOutcome:
    return EntityOutcome(entity, OutcomeKind.BUILT, OutcomeReason.NONE, rows, **kw)


def _facts(outcomes: dict[str, EntityOutcome], csvs: dict[str, str] | None = None, **kw) -> RunFacts:
    csvs = csvs if csvs is not None else {f"{e}.csv": "h" for e, o in outcomes.items() if o.kind is OutcomeKind.BUILT}
    return RunFacts(outcomes, csvs, **kw)


_BASE = _facts({"Students": _built("Students"), "Family": _built("Family"), "Staff": _built("Staff")})


def _check(
    run: RunFacts,
    *,
    affected=frozenset({"Students", "Family"}),
    column="X",
    header=("x",),
    base=_BASE,
    rows=_ROWS,
    required=frozenset(),
):
    return policy_problems(
        column=column,
        original_header=header,
        affected=affected,
        base=base,
        run=run,
        require_rows=rows,
        required=required,
    )


def _kinds(problems) -> set[str]:
    return {kind for kind, _, _ in problems}


def _call(site="s.f", entity="Family", *, required=("x",), available=(), raised=False) -> GuardCall:
    return GuardCall(site, entity, "pii_scope", frozenset(required), frozenset(available), raised)


class TestRequiredGuards:
    """Which guards a case must honour — derived from the calls, doctored both ways."""

    def test_a_guard_whose_frame_lost_the_column_is_required(self) -> None:
        base = [_call(available=("x", "y"))]
        run = _facts({"Family": _built("Family")}, guard_calls=(_call(available=("y",), raised=True),))
        assert required_guards("X", base, run) == {("s.f", "Family")}

    def test_a_guard_edited_to_stop_asking_is_still_required_over_the_same_frame(self) -> None:
        base = [_call(available=("x", "y"))]
        stopped_asking = _facts({"Family": _built("Family")}, guard_calls=(_call(required=(), available=("y",)),))
        assert required_guards("X", base, stopped_asking) == {("s.f", "Family")}

    def test_a_guard_that_still_saw_the_column_or_never_asked_for_it_requires_nothing(self) -> None:
        base = [_call(available=("x", "y")), _call("s.g", required=("y",), available=("x", "y"))]
        still = _facts({"Family": _built("Family")}, guard_calls=(_call(available=("x",)),))
        assert required_guards(" x ", base, still) == frozenset()

    def test_another_frame_checked_at_the_same_site_requires_nothing(self) -> None:
        """One site may check two frames (staff + merged); the one that never asked for the
        column and never carried it is not the guard that declared it."""
        base = [_call(available=("x", "t", "a")), _call(required=("t",), available=("t", "x", "b"))]
        # X dropped from the file behind the SECOND frame only: the frame that asks for X keeps it
        run = _facts(
            {"Family": _built("Family")},
            guard_calls=(_call(available=("x", "t", "a")), _call(required=("t",), available=("t", "b"))),
        )
        assert required_guards("X", base, run) == frozenset()

    def test_a_vanished_guard_is_required_when_its_entity_finished(self) -> None:
        base = [_call(available=("x",))]
        finished = _facts({"Family": _built("Family")})
        assert required_guards("X", base, finished) == {("s.f", "Family")}
        stopped = RunFacts(
            {}, {}, error=SourceSchemaError("m", entity="Students", columns=("X",), guard=GuardKind.JOIN_KEY)
        )
        assert required_guards("X", base, stopped) == frozenset()  # the run never got there


class TestThePolicyChecker:
    def test_an_identical_run_has_no_problem(self) -> None:
        assert _check(_BASE) == []

    def test_a_silent_change_is_a_problem_and_a_signalled_one_is_not(self) -> None:
        silent = _facts({**_BASE.outcomes}, {**_BASE.csvs, "Students.csv": "changed"})
        assert _kinds(_check(silent)) == {"silent_change"}
        signalled = _facts(
            {**_BASE.outcomes, "Students": _built("Students", missing_mapped=("X",))},
            {**_BASE.csvs, "Students.csv": "changed"},
        )
        assert _check(signalled) == []

    def test_a_new_or_recounted_note_is_a_signal(self) -> None:
        from src.etl.outcomes import OutcomeNote

        note = next(iter(OutcomeNote))
        noted = _facts(
            {**_BASE.outcomes, "Students": _built("Students", notes=((note, 2),))},
            {**_BASE.csvs, "Students.csv": "changed"},
        )
        assert _check(noted) == []

    def test_a_dependent_inherits_its_upstreams_signal_and_not_its_silence(self) -> None:
        upstream_signalled = _facts(
            {**_BASE.outcomes, "Students": _built("Students", missing_mapped=("X",))},
            {**_BASE.csvs, "Students.csv": "changed", "Family.csv": "changed"},
        )
        assert _check(upstream_signalled) == []  # Family DEPENDS_ON Students
        both_silent = _facts({**_BASE.outcomes}, {**_BASE.csvs, "Students.csv": "c", "Family.csv": "c"})
        assert _check(both_silent) == [
            ("silent_change", "Family", "changed with no recorded signal"),
            ("silent_change", "Students", "changed with no recorded signal"),
        ]

    def test_an_unaffected_entity_that_changes_is_a_problem_even_with_a_signal(self) -> None:
        run = _facts(
            {**_BASE.outcomes, "Staff": _built("Staff", missing_mapped=("X",))}, {**_BASE.csvs, "Staff.csv": "c"}
        )
        assert _kinds(_check(run)) == {"unaffected_changed"}

    def test_an_isolated_failure_must_read_missing_source_column_and_never_ship(self) -> None:
        ok = _facts({**_BASE.outcomes, "Family": EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN)})
        assert _check(ok) == []
        raw = _facts({**_BASE.outcomes, "Family": EntityOutcome.failed("Family", OutcomeReason.TRANSFORM_ERROR)})
        assert _kinds(_check(raw)) == {"isolated_reason"}
        shipped = _facts(
            {**_BASE.outcomes, "Family": EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN)},
            {**_BASE.csvs},
        )
        assert _kinds(_check(shipped)) == {"substituted"}

    def test_a_required_guard_is_not_discharged_by_a_missing_mapped_signal(self) -> None:
        """A fail-closed column that ships with only S6's ``missing_mapped`` is RED (P5(a)/(b))."""
        tolerated = _facts(
            {**_BASE.outcomes, "Family": _built("Family", rows=4, missing_mapped=("X",))},
            {**_BASE.csvs, "Family.csv": "more rows"},
        )
        assert _check(tolerated) == []  # without a required guard, a signalled change is fine
        guarded = frozenset({("base.helper", "Family")})
        assert _kinds(_check(tolerated, required=guarded)) == {"fail_closed_guard_bypassed"}
        critical = frozenset({("students.g", "Students")})
        assert _kinds(_check(_BASE, required=critical)) == {"fail_closed_guard_bypassed"}

    def test_a_required_guard_is_discharged_by_the_isolated_failure_or_a_typed_stop(self) -> None:
        failed = _facts(
            {**_BASE.outcomes, "Family": EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN)}
        )
        assert _check(failed, required=frozenset({("base.helper", "Family")})) == []
        # a CRITICAL entity can only be discharged by the stop, never by a FAILED outcome
        students_failed = _facts(
            {**_BASE.outcomes, "Students": EntityOutcome.failed("Students", OutcomeReason.MISSING_SOURCE_COLUMN)}
        )
        assert "fail_closed_guard_bypassed" in _kinds(
            _check(students_failed, required=frozenset({("students.g", "Students")}))
        )
        stop = self._failed_run(self._schema_error(entity="Students"), entity="Students")
        assert (
            self._check_failed(stop, affected=frozenset({"Students"}), required=frozenset({("s.g", "Students")})) == []
        )

    def _failed_run(
        self, exc: BaseException, *, category="source_schema", tree=None, entity="Classes", beside=None
    ) -> RunFacts:
        """A stopped run in ``run_case``'s REAL shape: no outcomes (a raise returns no result),
        what the pipeline wrote on the way out in the record."""
        entity_outcomes = {entity: {"kind": "failed", "reason": "missing_source_column", "rows": 0}}
        entity_outcomes.update(beside or {})
        record = {"status": "failed", "error_category": category, "entity_outcomes": entity_outcomes}
        return RunFacts({}, {}, tree if tree is not None else {"a.csv": "1"}, exc, record)

    def _schema_error(self, entity="Classes", columns=("X",), guard=GuardKind.JOIN_KEY) -> SourceSchemaError:
        return SourceSchemaError("missing", entity=entity, columns=columns, guard=guard)

    def _check_failed(self, run: RunFacts, **kw):
        base = RunFacts({}, {}, {"a.csv": "1"})
        return _check(run, affected=kw.pop("affected", frozenset({"Classes"})), base=base, **kw)

    def test_a_typed_critical_failure_with_a_matching_record_is_the_declared_outcome(self) -> None:
        assert self._check_failed(self._failed_run(self._schema_error())) == []

    def test_an_untyped_failure_is_a_problem(self) -> None:
        assert _kinds(self._check_failed(self._failed_run(KeyError("x")))) == {"untyped_failure"}

    def test_a_failure_that_touches_the_output_or_misrecords_is_a_problem(self) -> None:
        assert _kinds(self._check_failed(self._failed_run(self._schema_error(), tree={"a.csv": "2"}))) == {
            "output_touched"
        }
        assert _kinds(self._check_failed(self._failed_run(self._schema_error(), category="unknown"))) == {
            "record_mismatch"
        }

    def test_the_raiser_must_be_affected(self) -> None:
        run = self._failed_run(self._schema_error())
        assert _kinds(self._check_failed(run, affected=frozenset({"Students"}))) == {"raiser_not_affected"}

    def test_the_guard_class_must_be_one_the_table_lists_for_the_raiser(self) -> None:
        """Against the REAL table this cannot fire: it has a ``(caller)`` row for both guard
        classes, and a ``(caller)`` row covers any entity. It checks the TABLE — a guard class
        the table no longer lists anywhere for that entity — which only a doctored table shows."""
        staff = self._failed_run(self._schema_error(entity="Staff"), entity="Staff")
        assert self._check_failed(staff, affected=frozenset({"Staff"})) == []  # (caller) join_key covers it
        no_caller_rows = frozenset({RequireColumnsRow("classes.f", "Classes", "join_key")})
        assert _kinds(self._check_failed(staff, affected=frozenset({"Staff"}), rows=no_caller_rows)) == {
            "guard_not_in_require_columns_table"
        }
        assert self._check_failed(self._failed_run(self._schema_error()), rows=no_caller_rows) == []

    def test_the_error_must_name_the_dropped_column_or_what_it_stood_in_for(self) -> None:
        run = self._failed_run(self._schema_error(columns=("Course Code",)))
        # `District Course Code` dropped; the error names the canonical `Course Code`, never in the file.
        assert self._check_failed(run, column="District Course Code", header=("District Course Code",)) == []
        # The doctored twin: the named column WAS in the file under its own name, and was not dropped.
        wrong = self._check_failed(run, column="Teacher ID", header=("Teacher ID", "Course Code"))
        assert _kinds(wrong) == {"error_misnames_column"}

    def test_an_isolatable_error_may_stop_the_run_only_when_nothing_built(self) -> None:
        err = self._schema_error(entity="Family", guard=GuardKind.PII_SCOPE)
        alone = self._failed_run(err, entity="Family", beside={"Classes": {"kind": "not_run", "reason": "run_aborted"}})
        assert self._check_failed(alone, affected=frozenset({"Family"})) == []
        # run_case's real shape for a breached bulkhead: outcomes EMPTY, the built sibling on the record
        beside = self._failed_run(err, entity="Family", beside={"Students": {"kind": "built", "reason": "none"}})
        assert beside.outcomes == {}
        assert _kinds(self._check_failed(beside, affected=frozenset({"Family"}))) == {"isolatable_escaped"}

    def test_the_table_reader_is_the_docs_own(self) -> None:
        doctored = "<!-- failure-policy-table: require-columns -->\n| site | entity | guard | §5 |\n|---|---|---|---|\n"
        doctored += "| `x.f` | Staff | join_key | 1 |\n"
        assert require_columns_rows(doctored) == {RequireColumnsRow("x.f", "Staff", "join_key")}


# --------------------------------------------------------------------------- #
# The deselected matrix still runs somewhere                                   #
# --------------------------------------------------------------------------- #
def _addopts_deselects(addopts: str) -> bool:
    match = re.search(r"-m\s+(['\"])(.*?)\1", addopts)
    return bool(match) and re.search(rf"\bnot\s+{_MARKER}\b", match.group(2)) is not None


def _ci_matrix_steps(workflow_text: str) -> list[str]:
    """Every ``run:`` command in ``ci.yml`` that selects the marker over this module."""
    doc = yaml.safe_load(workflow_text)
    commands = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run = str(step.get("run", ""))
            if f"-m {_MARKER}" in run and "tests/test_schema_drift_matrix.py" in run:
                commands.append(run)
    return commands


def test_ci_runs_the_deselected_matrix() -> None:
    import tomllib

    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    options = pyproject["tool"]["pytest"]["ini_options"]
    assert any(m.startswith(f"{_MARKER}:") for m in options["markers"]), "register the marker"
    assert _addopts_deselects(options["addopts"]), "the default run deselects the matrix"
    assert _ci_matrix_steps(_CI_WORKFLOW.read_text(encoding="utf-8")), "and ci.yml runs it"


def test_the_ci_parity_readers_see_a_doctored_workflow_and_addopts() -> None:
    assert _addopts_deselects(f"-v -m 'not benchmark and not {_MARKER}'")
    assert not _addopts_deselects("-v -m 'not benchmark'")
    step = f"jobs:\n  j:\n    steps:\n      - run: python -m pytest tests/test_schema_drift_matrix.py -m {_MARKER}\n"
    assert _ci_matrix_steps(step)
    assert not _ci_matrix_steps("jobs:\n  j:\n    steps:\n      - run: python -m pytest tests/\n")
