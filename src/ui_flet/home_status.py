"""Pure Home status-derivation — the trust core of the sync-health cockpit.

NO ``flet`` import. Given the run records (newest-first, from
``history.store.read_run_records``) + the ``AppConfig`` state, derive a single
``HomeStatus`` — a ``Verdict`` (HEALTHY / WARNING / FAILED) + a plain-language
headline + supporting detail + an optional fix path + an optional ``HomeMetrics``.

**``HomeMetrics`` is DERIVED but no longer RENDERED by Home.** 0038 S7 retired the metric-tile
row; the one number that survived rides the healthy detail's ``size_clause``. The bundle stays
on the model as the pipeline↔UI record-shape contract ``tests/test_pipeline_run_store.py``
reads, and as the honest "no countable build behind this run" signal (``metrics is None``).
Retiring it from the model is a named residual in ``docs/claugentic-ROADMAP.md`` — not a claim
that a screen still paints it.

**Graceful degradation is a first-class OUTPUT, not an exception path** — an
unreadable log (``records is None``) becomes a calm "status unavailable" WARNING,
never a raise. **The derivation is TOTAL:** every field is read via ``.get`` with a
safe default, so a partial/old record never ``KeyError``s; an unparseable timestamp
skips the staleness rule rather than crashing; every path returns a valid
``HomeStatus``.

**Privacy (LIVE/top):** the record's free-text ``error`` (``str(e)`` in the emitter,
which can carry a filesystem path / ``sis_type`` / column name) is **NEVER interpolated
into the admin-facing ``headline``/``detail``** — faults are named by CATEGORY from the
record's structured fields only (status / sftp / anomalies / data_errors). The raw
``error`` belongs solely to IA-6's raw-log expander.

Rule order (first-match-wins; failures above warnings above healthy — a failed sync is
never masked by a later "healthy") mirrors ``03_Run_History._status_cell``'s proven
precedence (status → sftp → data_errors), extended with anomaly + staleness + empty, and
tied to the CLI exit-code contract (1 = ETL fail, 3 = SFTP fail with output present).
**That ordering binds the schedule-attention rule too (W3-B):** a broken nightly schedule is
a WARNING-tier fault, so it outranks every other warning, the empty state and healthy — but
it NEVER outranks a FAILED latest record. When both are true the failure keeps the band and
the single fix CTA, and the schedule fault rides along as a bounded secondary clause.

The pipeline emits entity counts as **FLAT top-level keys** on the record
(``record["Students"]``, ``record["Staff"]``, …) — verified against
``pipeline._emit_run_log`` — NOT nested under an ``entity_counts`` key. ``HomeMetrics``
re-buckets those flat keys into its own ``entity_counts`` dict.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.config.app_config import AppConfig
from src.etl.sync_window import in_sync_window, next_resume_date
from src.ui_flet.humanize import (
    AnomalyVariant,
    friendly_anomaly_detail,
    friendly_date_short,
    friendly_timestamp,
    pluralize,
)
from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus
from src.ui_flet.verdict import Verdict

# The 5 SpacesEDU rostering entities always counted, then the 2 myBlueprint+ entities counted
# ONLY when non-zero (a SpacesEDU district run yields 5 keys, not 7-with-two-zeros).
# ``StudentAttendance`` is deliberately omitted. Its ONE surface is Home's healthy ``size_clause``
# ("It included 8,140 attendance rows.") — nowhere else: Run History's per-entity columns derive
# from these same two tuples, so an attendance-only run renders five zeros there, and the clause
# itself is emitted only from the HEALTHY branch. Still-open in ``docs/claugentic-ROADMAP.md``.
_ROSTERING_ENTITIES: tuple[str, ...] = ("Students", "Staff", "Family", "Classes", "Enrollments")
_MYBLUEPRINT_ENTITIES: tuple[str, ...] = ("CourseInfo", "StudentCourses")

# The SINGLE source of the entity-key → plain-language output-CSV label. The 5 rostering
# entities label to themselves; the myBlueprint+ / attendance keys map to their friendly CSV
# names (``CourseInfo`` → "Courses", ``StudentCourses`` → "Student courses",
# ``StudentAttendance`` → "Attendance"). This is a pure presentation fact (no flet), so both
# the pure ``mapping_catalog`` and the flet views (``components.run_table``, Home, Convert)
# consume ONE definition — a rename here changes every surface at once (DRY). An unknown key
# has no entry; callers fall back to the raw key (``ENTITY_LABELS.get(name, name)``).
ENTITY_LABELS: dict[str, str] = {
    "Students": "Students",
    "Staff": "Staff",
    "Family": "Family",
    "Classes": "Classes",
    "Enrollments": "Enrollments",
    "CourseInfo": "Courses",
    "StudentCourses": "Student courses",
    "StudentAttendance": "Attendance",
}

# --------------------------------------------------------------------------- #
# The healthy line's roster-size clause (0038 S7)                              #
#                                                                             #
# Slim Home drops the metric-tile row, and with it the one thing the tiles     #
# carried that the verdict does not: a SIZE sanity check. A sync that quietly  #
# shrank to 12 students is "delivered to SpacesEDU" by every structured field  #
# on the record, so the healthy line names one number and lets the admin — the #
# only person who knows the district is not that small — see it.               #
# --------------------------------------------------------------------------- #
#
# WHY A SECOND VOCABULARY, beside ``ENTITY_LABELS``. That map names the output CSV
# ("Attendance", "Courses") — a heading. This one names a COUNTABLE THING ("8,140
# attendance rows", "1,204 courses"), which is a different presentation fact and reads
# wrong if borrowed: "8,140 attendance" and "4,812 family" are not sentences. Both forms
# are written out rather than derived, because ``humanize.pluralize`` is a naive ``+ "s"``
# and would render "1 classs".
#
# ORDER IS SEMANTIC. The dict order IS the "which entity leads" rule: the first key this
# config actually produces wins. That is what keeps an attendance-only or myBlueprint+-only
# config off the rostering keys entirely (see ``size_clause``).
#
# THE TABLE IS THE ALLOWLIST. A config may enable a partner-defined entity whose key came
# out of a hand-dropped YAML; an unknown key produces NO clause rather than being echoed
# into admin-facing copy. Same posture as the rest of this module: never render a string
# we did not author.
SIZE_NOUNS: dict[str, tuple[str, str]] = {
    "Students": ("student", "students"),
    "Staff": ("staff record", "staff records"),
    # NOT "families": a ``Family.csv`` row is one parent/guardian contact and a student may
    # have several, so counting rows as families would overstate a number by design.
    "Family": ("family contact", "family contacts"),
    "Classes": ("class", "classes"),
    "Enrollments": ("enrollment", "enrollments"),
    "CourseInfo": ("course", "courses"),
    "StudentCourses": ("student course", "student courses"),
    "StudentAttendance": ("attendance row", "attendance rows"),
}

SIZE_CLAUSE_LEAD = "It included "
"""The size clause's fixed opening — the one literal docs quote (pinned by the copy-parity test)."""

STALE_AFTER_HOURS = 36
"""A nightly job → a successful run older than ~1.5 nightly cycles is "no recent sync".
One generous constant absorbs timezone/clock skew (KISS — no per-tz math for a tool an
admin opens 2-3x/yr); staleness is only ever a WARNING, never a FAILED."""

MISSED_RUN_AFTER_HOURS = 26
"""The missed-run window (owner rule, 2026-07-15): a CONFIRMED-LIVE nightly schedule with no
recorded run inside ~1 nightly cycle (+ 2h skew) means the sync we promised didn't arrive.
Deliberately tighter than the schedule-unaware ``STALE_AFTER_HOURS`` proxy — this rule demands
a LIVE read-back (never the config hint), so it can afford to warn a whole cycle earlier."""

_RUN_HISTORY_FIX = "run_history"

_CHECK_RUN_HISTORY_LABEL = "Check Run History"

# The FIRST fix target that isn't Run History (D4): a broken/missing schedule routes to
# Setup's schedule section, not the read-only run ledger. Slice 3's rail-follow already
# syncs the highlight on this programmatic hop.
_SETUP_FIX = "setup"

# The MISSING fix CTA names the ACTION, not the destination (finding #2b) — the Firefighter reads
# "fix the schedule", not "open a screen"; still routes to Setup (dest_id stays `_SETUP_FIX`).
_OPEN_SETUP_LABEL = "Fix the nightly schedule"

# The FAILED_DELIVERY fix CTA (0032 T2 #4): a failed upload is fixed in Settings' delivery
# section (host/credentials), not in the read-only run ledger — and the label says where it
# goes (the rail label graduates to Settings; "Open Settings" matches Mapping's existing route).
_OPEN_SETTINGS_LABEL = "Open Settings"

# The SECONDARY schedule clause appended to a FAILED detail when the nightly schedule is
# CONFIRMED gone (W3-B). Two real faults, one possible CTA: the failure keeps the band + button
# (it is the dominant fault and the one the admin came to fix), and this fixed, category-only
# sentence stops the Firefighter from fixing the run and walking away believing tonight's sync
# resumes — it positively will not, because we definitively read the task back as absent.
# Authored copy, never a field lifted off the record → the PII-free-by-construction bar holds.
_SCHEDULE_GONE_NOTE = (
    "Your nightly schedule is also no longer registered with Windows — "
    "re-register it in Settings so the sync can run again."
)

# The seasonal-pause headline (B): while an ENABLED window is OUTSIDE its active season, no
# nightly sync arrives BY DESIGN, so the missed-run / stale / fired-but-no-record warnings would
# all FALSE-FIRE every summer night. The pause is a healthy, intentional state — not amber/red —
# so its verdict is HEALTHY (an amber "attention" tone would train the admin to ignore amber). The
# resume date is a PURE fact (``next_resume_date``), rendered PII-free via ``friendly_date_short``.
_PAUSED_HEADLINE = "Paused for the summer"

# --------------------------------------------------------------------------- #
# The empty-store copy — SHARED with Run History (0038 S7 part (i))            #
#                                                                             #
# Two surfaces answer "there are no runs here yet" and they must never split   #
# on WHICH empty state this is. Before S7 each carried its own copy of the     #
# discriminator AND of both headlines, byte-identical by hand — which is       #
# exactly how two surfaces begin to disagree. The headlines, the              #
# no-automation note and the rule itself now live here; the LEAD sentences     #
# stay per-surface on purpose (Run History's lines reference the ledger        #
# beneath them, Home's do not).                                                #
# --------------------------------------------------------------------------- #
EMPTY_FRESH_START_HEADLINE = "Run history starts fresh here"
# A claim about the LEDGER, never about the world. ``store_created_at`` is the only
# discriminator either surface has, and it cannot separate "never ran" from "ran before the
# store existed": ``history.db`` shipped in v3.5.0, ``write_run_record`` is its sole creator,
# and there is no backfill — so an install upgrading from <= v3.4.0 that has synced nightly for
# months arrives here with no stamp. "No sync has run yet" was flatly false for that district;
# "no runs recorded" is true for it AND for the genuine newcomer, which is the only headline
# both readings can carry. (Both surfaces inherit it — the constant is single-sourced.)
EMPTY_NO_RUNS_HEADLINE = "No runs recorded yet"

# Shown to a CONFIRMED-unscheduled install that HAS finished setup (finding #1b): an
# install with no nightly task does not sync on its own, and saying "new syncs will
# appear here" would imply automation nobody set up. Calm WARNING, no fix CTA — a
# manual-only district must not be nagged. Byte-identical on both surfaces because the
# FACT is identical, so it is single-sourced rather than typed twice.
EMPTY_NO_AUTO_SYNC_DETAIL = (
    "Your roster won't sync automatically until you add a nightly schedule — set one up "
    "in Settings whenever you're ready. Manual conversions from the Convert tab appear here too."
)

# HOME's two lead sentences (Run History writes its own — its lines reference the ledger
# below them). The upgrader's claim stays CONDITIONED: a store birth stamp proves a run was
# once recorded, never which build recorded it, so "if you used an earlier version" is the
# strongest honest form.
_FRESH_START_LEAD = (
    "New syncs will appear here from now on. If you used an earlier version, its run history isn't carried over."
)
# NAMES a nightly, so it may only be shown where one is positively signalled (see
# ``_expects_a_nightly``). Until S7 it was near-dead — ``nav.needs_setup`` gated the whole
# empty branch to the wizard — and promoting it to the default lead for every fresh install
# meant an admin who SKIPPED the Schedule step read about automation they had declined. That
# state is Home's INITIAL paint on every mount (the probe is off-thread) and its permanent one
# whenever the read-back is UNKNOWN.
#
# It says "your nightly sync", never "your FIRST": ``_expects_a_nightly`` admits
# ``schedule_registered``, which is exactly what a <= v3.4.0 upgrader has — an install that has
# synced nightly for months and lands in this empty state only because ``history.db`` did not
# exist before v3.5.0. Naming its next sync as the first would be the same ledger-vs-world
# falsehood ``EMPTY_NO_RUNS_HEADLINE`` was rewritten to avoid, two lines further down the page.
# The wording is true for the genuine newcomer AND the upgrader, which is the bar every
# sentence in this branch has to clear.
_FIRST_SYNC_LEAD = "Your nightly sync will appear here."
# The artefact-free lead for the same empty store with NO nightly we can point at. It names
# only the ledger and the two ways a run can reach it, so it is true whether or not this
# install ever gets a schedule. (``EMPTY_NO_AUTO_SYNC_DETAIL`` is the stronger, CONFIRMED-
# missing form — this one covers "we cannot see one", which is not the same claim.)
_NO_RUNS_YET_LEAD = "Whenever a sync runs — nightly, or from the Convert tab — its result appears here."

# --------------------------------------------------------------------------- #
# The first-run welcome band (0038 S6) — the one line above the hosted wizard. #
#                                                                             #
# Home hosts the setup wizard while the install has not reached the finish     #
# line, and the wizard is reachable in states that are anything but new: an    #
# upgrade that ran manually for a year without ever registering a schedule, a  #
# wizard abandoned halfway. "Welcome to DistrictSync" over a populated run      #
# store is the copy failure this band exists to make unrepresentable — so the  #
# greeting is a DERIVED fact, never a constant, and each variant claims only    #
# what its inputs establish. (The register is calm and quiet by design: the    #
# gradient hero retired with `screens/onboarding.py` in this slice, and the    #
# gradient's one home is the launch page — see docs/DESIGN_SYSTEM.md.)         #
# --------------------------------------------------------------------------- #
#
# THE RULE these four variants encode: **never NAME an artefact you know is absent, or
# cannot see.** A reassurance is a claim; "your run history is safe" is a promise about a
# thing, and it may only be made when that thing is known to exist AND is readable. The rule
# cuts BOTH ways and the fourth line is what makes that true — a band that names "everything
# you've already entered" over an install that entered nothing is the same over-claim
# pointed at the settings instead of the store.
#
# The band also carries NO step count. It sits one line above the wizard's own
# "Step 1 of 5" indicator, and two numbers on one screen is a contradiction the moment
# either moves — which the open 5→4 "Finish unnumbered" question (ROADMAP) means is
# coming. The indicator owns the count; the band owns the reassurance.
WELCOME_FRESH = "Welcome — this takes about 3 minutes."
WELCOME_RESUME_WITH_HISTORY = "Let's finish setting up — your files and run history are safe."
# Saved choices, but no run history we may speak for. TWO installs land here, and BOTH have
# something on disk to point at: the half-configured one (choices saved, nothing ever run) —
# "your run history is safe" would reassure it about a thing we know is absent; and the one
# whose run store EXISTS but could not be READ: it is certainly not new, but we cannot see
# the history to promise anything about it. This line names only what we can still see.
WELCOME_RESUME_SETTINGS_ONLY = "Let's finish setting up — everything you've already entered is safe."
# The install that is NOT new and has NOTHING we can name: no saved choices, and the only
# evidence it is established is a run store we could not open. Reached by a readable-but-blank
# `config.json` (which the launch page's identity-only save creates) beside an unreadable
# `history.db`. "Welcome" would be false (the store's existence is a checked fact) and
# "everything you've already entered" would name settings we have positively checked are
# absent — so this variant reassures about NOTHING and simply gets on with the task.
WELCOME_RESUME_PLAIN = "Let's finish setting up."


@dataclass(frozen=True)
class FixAction:
    """A plain-language CTA: the button ``label`` + the ``dest_id`` it navigates to."""

    label: str
    dest_id: str


@dataclass(frozen=True)
class HomeMetrics:
    """What a delivered run shipped: entity counts + plain last-run time + SFTP flag.

    ``entity_counts`` is re-bucketed from the record's flat top-level count keys — the 5
    rostering entities always, the 2 myBlueprint+ entities only when non-zero.

    **No screen renders this.** It fed Home's metric-tile row until 0038 S7 retired it; what
    survives on the model is the record-shape contract the pipeline tests read and the
    ``None``-vs-populated signal that says whether any honest build sits behind the latest run.
    """

    entity_counts: dict[str, int]
    last_run_display: str
    sftp_delivered: bool


@dataclass(frozen=True)
class HomeStatus:
    """The derived sync-health verdict the Home view renders (verdict-first)."""

    verdict: Verdict
    headline: str
    detail: str
    fix: FixAction | None
    metrics: HomeMetrics | None


@dataclass(frozen=True)
class QuickAction:
    """One row of slim Home's quick-action strip: a ``label``, its ``dest_id``, and its TIER."""

    label: str
    dest_id: str
    filled: bool


# The three places a Home visitor actually goes next, in the order they are offered.
QUICK_CONVERT_LABEL = "Convert now"
QUICK_RUN_HISTORY_LABEL = "Run History"
QUICK_SETTINGS_LABEL = "Settings"

_CONVERT_DEST = "convert"

_QUICK_DESTINATIONS: tuple[tuple[str, str], ...] = (
    (QUICK_CONVERT_LABEL, _CONVERT_DEST),
    (QUICK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
    (QUICK_SETTINGS_LABEL, _SETUP_FIX),
)


def quick_actions(fix: FixAction | None) -> tuple[QuickAction, ...]:
    """Slim Home's quick-action strip — the actions offered BESIDE the verdict's fix CTA.

    The strip deliberately never contains the fix itself: a fault and its fix are one
    thought and the view renders that CTA directly under the band (the identity cards sit
    between the two blocks, so moving it down here would separate them). What the strip
    DOES do is drop any destination the fix already carries — "Check Run History" filled
    above an outlined "Run History" is the same button twice.

    **The design-system invariant, stated as arithmetic:** exactly one filled action exists
    on the surface in every state — the fix when there is a fault, "Convert now" when there
    is not. It holds even if a future ``FixAction`` ever routed to Convert (the strip would
    drop Convert, keeping the total at one). Pure + TOTAL: no I/O, no config, no records.
    """
    taken = {fix.dest_id} if fix is not None else set()
    return tuple(
        QuickAction(label, dest_id, filled=(fix is None and dest_id == _CONVERT_DEST))
        for label, dest_id in _QUICK_DESTINATIONS
        if dest_id not in taken
    )


def has_earlier_run_history(*, store_created_at: str | None) -> bool:
    """Whether an EMPTY store is an upgrader's, not a newcomer's — the ONE discriminator (S7 (i)).

    ``store_created_at`` is the run store's birth stamp and ``write_run_record`` is the
    store's SOLE creator, so a stamp is *evidence* that a run was once recorded — even if a
    later quarantine-recreate emptied the table. That evidence is the whole basis for
    telling someone "if you used an earlier version, its run history isn't carried over".

    **Deliberately NOT ``has_completed_setup()``** (0038 S7 part (i), carried from S6's
    gate). The moment the wizard saves, that flag is True — so a brand-new install that has
    never recorded anything was reading a conditional sentence about a past version it
    provably never had, at the one moment it had just finished setting up. Finishing setup
    is evidence about the SETTINGS, never about a run.

    Keyword-only and shared by ``derive_home_status`` and
    ``run_history.derive_history_banner``: both surfaces classify the same empty store, so
    the rule is single-sourced rather than duplicated (it was duplicated, byte-identically,
    until this slice).
    """
    return bool((store_created_at or "").strip())


def _expects_a_nightly(app_config: AppConfig, schedule_status: ScheduleStatus | None) -> bool:
    """Whether ANY positive signal says this install has a nightly schedule (pure + TOTAL).

    The gate on every empty-state sentence that NAMES a nightly sync. Two signals, OR-ed, and
    both must be POSITIVE — the absence of evidence is not evidence of a schedule:

    * a CONFIRMED-LIVE read-back (the probe queried the OS and found the task); or
    * ``schedule_registered`` — the app's own record that it registered one. Weaker than a
      read-back and deliberately admitted anyway: it is the only signal available on Home's
      INITIAL paint (the probe is off-thread) and on every platform without read-back, and it
      is the install's own history rather than a guess about the OS.

    This is the inverse posture to ``_schedule_confirmed_missing`` (which drives the stronger
    "won't sync automatically" copy and demands a CONFIRMED absence). Neither may be inferred
    from the other: "not confirmed missing" is not "present".
    """
    return _schedule_confirmed_live(schedule_status) or bool(app_config.schedule_registered)


def size_clause(counts_record: dict | None, output_entities: Sequence[str], *, expected_sis_type: str) -> str:
    """The healthy line's roster-size sentence, or ``""`` when there is nothing honest to say.

    ``output_entities`` is what THIS district's config actually produces (injected by the
    view from ``mapping_catalog.active_output_entities`` — this module stays I/O-free). It
    is the only thing that can distinguish "this config does not emit Students" from "the
    roster collapsed to zero students", because the run record writes every entity key with
    a defaulted ``0`` and cannot tell them apart. The first produced entity in
    ``SIZE_NOUNS`` order wins, so an attendance-only config counts attendance rows and a
    myBlueprint+-only config counts courses — never "0 students".

    ``expected_sis_type`` is the district those entities were resolved FOR, and it is
    keyword-only + REQUIRED because forgetting it is the unsafe call. ``output_entities``
    describes the district saved **now**; ``counts_record`` was written by whatever ran
    **then**, and the two legitimately diverge — Mapping's Apply rewrites ``sis_type``
    without re-registering the nightly task (so later *scheduled* records still carry the
    OLD district), and Convert records the district picked in its dropdown without saving it
    (which is why the S5 "This run: <district>" pill exists). Applying one district's entity
    list to another district's counts printed a FALSE number under a GREEN band — an
    ``sd51attendance`` record read "It included 0 students." on a saved ``sd48myedbc``.
    The record already carries the authority (``pipeline`` writes ``sis_type`` onto it, and
    ``run_history._district_note`` already reads it), so a KNOWN mismatch simply drops the
    clause. The predicate mirrors ``_district_note``: BOTH sides must be known non-empty to
    establish a difference — an absent record district or an unset active district is not a
    disagreement, and neither is a guess.

    A genuinely-zero count on a config that DOES emit that entity is printed, loudly: that
    is the alarm this clause exists to raise, not a case to hide.

    Returns ``""`` — the clause simply vanishes — whenever the answer would be a guess:
    no counts record, a record from a DIFFERENT district, an unknown/empty
    ``output_entities``, or a produced entity this module has no authored noun for.
    """
    if counts_record is None:
        return ""
    record_sis = str(counts_record.get("sis_type") or "").strip()
    expected = (expected_sis_type or "").strip()
    if record_sis and expected and record_sis != expected:
        return ""
    produced = {str(name) for name in output_entities}
    for key, (singular, plural) in SIZE_NOUNS.items():
        if key not in produced:
            continue
        count = _as_int(counts_record.get(key))
        return f"{SIZE_CLAUSE_LEAD}{count:,} {singular if count == 1 else plural}."
    return ""


def is_stale(
    last_ts: str,
    now: datetime | None = None,
    *,
    stale_after_hours: int = STALE_AFTER_HOURS,
) -> bool:
    """Whether the last successful run's timestamp is older than the staleness window.

    Pure + total. An unparseable ``last_ts`` → ``False`` (can't determine → don't cry
    wolf). Reused by IA-6.
    """
    text = (last_ts or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return False

    reference = now if now is not None else datetime.now(tz=parsed.tzinfo)
    try:
        elapsed_hours = (reference - parsed).total_seconds() / 3600
    except TypeError:
        return False  # naive/aware mismatch — total, treat as "can't determine"
    return elapsed_hours > stale_after_hours


class LatestReason(Enum):
    """The single-source classification of a latest run's fault axis (staleness EXCLUDED).

    The status→reason precedence a *record* carries, independent of when it ran — the one
    place the ``status → sftp → anomalies → data_errors`` order is decided. ``derive_home_status``
    (Home) and ``run_history.derive_history_banner`` + ``run_history.to_run_row`` (IA-6, the 2nd
    consumer) both classify through this, so a Home verdict and a Run-History row/banner can never
    drift. Staleness is a SEPARATE, time-relative axis the caller layers on top of ``CLEAN`` — it
    is deliberately NOT a reason here (a stale run is a clean run that's merely old).
    """

    FAILED_ETL = "failed_etl"  # status != "success" — the dominant fault
    FAILED_DELIVERY = "failed_delivery"  # ETL ok, SFTP attempted + failed (exit-3 shape)
    ANOMALY = "anomaly"  # delivered but a >20% drop looked off
    DATA_WARNINGS = "data_warnings"  # delivered, some rows had field problems + were skipped
    CLEAN = "clean"  # delivered cleanly (a stale run is still CLEAN — staleness is layered on top)


def classify_latest_reason(record: dict) -> LatestReason:
    """Classify a run record's fault axis (first-match precedence, staleness EXCLUDED) — pure + TOTAL.

    The SINGLE source of the ``status → sftp → anomalies → data_errors`` precedence (mirrors
    ``03_Run_History._status_cell``). Every field is read via ``.get`` so a partial/old record never
    ``KeyError``s (a missing ``status`` → non-``success`` → ``FAILED_ETL``, the honest fail-safe
    default). NEVER inspects/returns the free-text ``error`` (privacy) — category only.
    """
    if record.get("status") != "success":
        return LatestReason.FAILED_ETL
    if bool(record.get("sftp_attempted")) and not bool(record.get("sftp_ok")):
        return LatestReason.FAILED_DELIVERY
    anomalies = record.get("anomalies") or []
    if isinstance(anomalies, list) and anomalies:
        return LatestReason.ANOMALY
    if _data_errors_total(record) > 0:
        return LatestReason.DATA_WARNINGS
    return LatestReason.CLEAN


def _data_errors_total(record: dict) -> int:
    """The ``data_errors.total`` count — total: a missing/non-dict ``data_errors`` → ``0``."""
    data_errors = record.get("data_errors")
    if not isinstance(data_errors, dict):
        return 0
    return _as_int(data_errors.get("total", 0))


def is_delivery_only(record: dict) -> bool:
    """Whether this record is a deliver-from-disk attempt (0034 Slice 2) — pure + TOTAL.

    A delivery ships an EARLIER build's committed CSVs, so its record deliberately carries
    no build entity counts (the flat count keys are zeros by shape) — the ``delivery_only``
    rider lets Home / Run History render it as a delivery, never as a 0-row build. Read via
    ``.get`` so every pre-existing record (no rider) classifies as a build, unchanged.
    """
    return bool(record.get("delivery_only"))


def sftp_delivered(record: dict) -> bool:
    """Whether this record's files genuinely reached SpacesEDU (``sftp_ok``) — pure + TOTAL.

    The single-source SFTP-success predicate the CLEAN/healthy detail branches on (0032 T1 #1a):
    Home and Run History both consult it, so neither surface can claim a delivery that never
    happened — a run with no SFTP attempt reads "completed", never "delivered to SpacesEDU".
    """
    return bool(record.get("sftp_ok"))


def verdict_for_reason(reason: LatestReason) -> Verdict:
    """Map a ``LatestReason`` to its ``Verdict`` — total over the enum.

    The single source of "which reason is red vs amber vs green": the two failures are FAILED,
    anomaly/data-warnings are WARNING, CLEAN is HEALTHY. A ``KeyError`` here is a programming error
    (a new reason without a verdict) — surfaced loudly by the totality test, never swallowed.
    """
    return _REASON_VERDICTS[reason]


_REASON_VERDICTS: dict[LatestReason, Verdict] = {
    LatestReason.FAILED_ETL: Verdict.FAILED,
    LatestReason.FAILED_DELIVERY: Verdict.FAILED,
    LatestReason.ANOMALY: Verdict.WARNING,
    LatestReason.DATA_WARNINGS: Verdict.WARNING,
    LatestReason.CLEAN: Verdict.HEALTHY,
}


def _entity_counts(record: dict) -> dict[str, int]:
    """Re-bucket the record's FLAT top-level count keys into a metrics dict.

    Rostering entities always present; myBlueprint+ entities only when non-zero (defensive
    ``int`` coercion so a malformed count never crashes the metrics build)."""
    counts: dict[str, int] = {}
    for name in _ROSTERING_ENTITIES:
        counts[name] = _as_int(record.get(name))
    for name in _MYBLUEPRINT_ENTITIES:
        value = _as_int(record.get(name))
        if value > 0:
            counts[name] = value
    return counts


def _as_int(value: object) -> int:
    """Coerce a record count to ``int``; total — a missing/garbage value → ``0``."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _build_metrics(record: dict, *, now: datetime | None, counts_record: dict | None = None) -> HomeMetrics:
    """Populate the ``HomeMetrics`` bundle from a delivered-success record.

    ``counts_record`` (default: the record itself) supplies the entity counts — a
    delivery-only latest carries no build counts of its own, so the caller passes the
    newest BUILD record instead (the roster the delivery actually shipped).
    """
    return HomeMetrics(
        entity_counts=_entity_counts(counts_record if counts_record is not None else record),
        last_run_display=friendly_timestamp(str(record.get("timestamp", "")), now=now),
        sftp_delivered=sftp_delivered(record),
    )


def _counts_source(records: list[dict], latest: dict) -> dict | None:
    """The record whose entity counts describe what the latest run/delivery shipped.

    A build record IS its own counts source. A delivery-only latest shipped the newest
    SUCCESSFUL build's committed CSVs (a failed build never commits — atomic ``save_all``
    rolls back — and its record carries zero counts), so it falls back to the newest
    ``status == "success"`` build; with no successful build on record there is no honest
    count → ``None`` (no number at all — never a "0 Students" lie).

    Its return is what the roster-size clause is keyed on, which is why the clause's
    different-district guard is applied HERE rather than to ``records[0]``: this walk-back can
    land on a build from a district the admin has since switched away from.
    """
    if not is_delivery_only(latest):
        return latest
    for record in records:
        if not is_delivery_only(record) and record.get("status") == "success":
            return record
    return None


def derive_home_status(
    records: list[dict] | None,
    app_config: AppConfig,
    *,
    now: datetime | None = None,
    store_created_at: str | None = None,
    schedule_status: ScheduleStatus | None = None,
    output_entities: Sequence[str] = (),
) -> HomeStatus:
    """Derive the Home sync-health verdict from the run records + config (pure, TOTAL).

    Assumes a configured install — the dispatcher (IA-3b) gates un-onboarded installs to
    onboarding via ``nav.needs_setup``, so these rules only run for ``not needs_setup``.
    Evaluated top-down, first-match-wins.

    ``store_created_at`` (the run store's ``meta.created_at``, ``None`` when the store was
    never created) is the established-install signal for the fresh-start empty state — the
    view injects it from ``store.store_meta()`` so this stays pure/I-O-free.

    ``schedule_status`` (D4) is the injected tri-state schedule read-back (the view fetches it
    off-thread). When it reports ``attention`` (a schedule the config expected but the OS no
    longer has, or one that fired without completing) it is the dominant WARNING-tier trust
    fault, routed to Setup — never back into onboarding — but it is still bound by the module's
    failures-above-warnings precedence and so NEVER masks a FAILED latest record (W3-B). A
    ``None`` (not yet probed / non-applicable) or UNKNOWN schedule is silently ignored — Home
    NEVER asserts an unconfirmed schedule.

    ``output_entities`` (0038 S7) is the ordered set of entity keys the ACTIVE district config
    actually produces, injected by the view (``mapping_catalog.active_output_entities``) so this
    module keeps its no-I/O contract. It feeds ONLY the healthy line's roster-size clause. Its
    default is the CONSERVATIVE value, not a convenient one: an unsupplied/unknown answer makes
    the clause vanish, so forgetting it can cost a true sentence but can never produce a false
    number (see ``size_clause`` — this is the shape CLAUDE.md's "no permissive default on a
    safety-relevant parameter" rule asks for; the SUPPLY is pinned separately, by a Home render
    test, because a silently-absent clause is invisible from inside this module). Those entities
    describe the district saved NOW, so the clause is additionally gated on the counts record's
    OWN ``sis_type`` agreeing with ``app_config.sis_type`` — a record from a district the admin
    has since switched away from prints no number rather than a wrong one.
    """
    # Rule: status unavailable (the never-crash floor) — the reader couldn't read the store.
    if records is None:
        return HomeStatus(
            verdict=Verdict.WARNING,
            headline="Sync status unavailable",
            detail="We couldn't read the run history right now — your nightly sync may still be running normally.",
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: schedule needs attention (D4) — the read-back contradicts the config (task gone
    # while expected, or fired-but-no-record). The dominant WARNING-tier trust fault: even a clean
    # last run can't reassure if the nightly won't run again. Routed to Setup, NEVER to onboarding.
    #
    # W3-B: it is a WARNING, so the module's own "failures above warnings" precedence binds it —
    # it must NOT return above the FAILED_ETL / FAILED_DELIVERY rules below. A failed scheduled run
    # is exactly the state that also trips ``attention``, so the early return was silently
    # downgrading red to amber and dropping the failure from the copy entirely (and, because
    # ``screens/home.py`` paints the record verdict first and re-derives when the async probe
    # lands, the admin watched the alarm downgrade itself). The failure wins the band + the single
    # fix CTA; the schedule fault is surfaced as a secondary clause on the FAILED details below.
    # The seasonal-pause fact (B): an ENABLED window with today OUTSIDE its active season. Computed
    # once, consulted at the empty-state slot AND after the FAILED reasons below — it must beat the
    # missed-run / stale / anomaly / data-warning rules (whose "we expected a sync"/"no recent sync"
    # copy would FALSE-FIRE every summer night), but NEVER a FAILED latest (a real failure is not a
    # summer no-op) nor a genuinely-MISSING schedule (a gone task makes "resumes <date>" a lie).
    #
    # W3-B FIX: that MISSING carve-out was only ever half-wired — it fired via ``_schedule_attention``,
    # which is None unless ``attention`` is True, and a MISSING task the config NO LONGER expects
    # (expected=False — e.g. "Remove nightly sync" left the window enabled) has attention=False. So a
    # confirmed-gone schedule fell straight through to the paused headline: green "resumes <date>"
    # over a task that will never resume. The pause is now gated on a NOT-confirmed-MISSING read-back
    # (mirrors the stated intent ``schedule_status is None or state is not MISSING``), so the honest
    # "add a nightly schedule" copy surfaces even when the schedule-attention rule stays silent.
    paused = sync_window_paused(app_config, now=now) and not _schedule_confirmed_missing(schedule_status)

    schedule_attention = _schedule_attention(schedule_status)
    # Surface schedule attention UNLESS the latest record is a failure (it owns the band, W3-B) OR
    # we are in a seasonal pause AND this is the LIVE fired-but-no-record contradiction — a by-design
    # false alarm in summer (the paused nightly fires but writes no record). A definitively-MISSING
    # task (``state is MISSING``) still surfaces: "resumes <date>" would be untrue if it is gone.
    if (
        schedule_attention is not None
        and not _latest_is_failure(records)
        and not (paused and schedule_status is not None and schedule_status.state is ScheduleState.LIVE)
    ):
        return schedule_attention

    # The missed-run fact (owner rule, 2026-07-15): a CONFIRMED-LIVE schedule, an established
    # store, and no run record inside the window. Computed once, consulted at two slots below —
    # it must beat the empty-state reassurance AND the warning-tier record rules (whose copy
    # would describe a run that is over a day old by construction), but it must NEVER mask a
    # FAILED verdict (failures above warnings — amber can't downgrade red).
    missed_run = _is_missed_run(records, now=now, store_created_at=store_created_at, schedule_status=schedule_status)

    # Rule: no runs yet (empty but readable, configured). Two honest sub-states — the
    # run store is fresh for EVERY install after this update (no backfill from the polluted
    # log), so an UPGRADER must NOT be told its history simply does not exist:
    #   * the store already exists (``has_earlier_run_history``) → "history starts fresh" —
    #     earlier runs live only in the old diagnostic log and aren't shown here;
    #   * otherwise → the calm "waiting for the first sync".
    # 0038 S7 part (i) narrowed the discriminator to the store's birth stamp ALONE. Slice 5
    # (D4a) had OR-ed in ``has_completed_setup()``, which the wizard flips the instant it saves
    # — so from S6 (Home HOSTS the wizard) a brand-new install landed, at its peak moment, on a
    # conditional sentence about "an earlier version" it provably never had. Finishing setup is
    # evidence about the settings, never about a run. Newcomer-vs-upgrader is now decided by the
    # only artefact that is evidence of a RUN; the copy stays conditioned ("If you used an
    # earlier version…") because a re-created store cannot prove WHICH version wrote it.
    # The schedule sentences derive from the LIVE/MISSING read-back, never the raw config flag,
    # and now apply to BOTH sub-states (the never-run install is exactly the one that most needs
    # to know its roster won't sync on its own).
    if not records:
        # Rule: seasonal pause (empty store) — outside an enabled window no run is expected, so an
        # empty store is calm, not a missed run. Beats the missed-run/fresh-start empty sub-states.
        if paused:
            return _paused_status(app_config, now=now)
        # Rule: missed run (empty store) — a LIVE schedule over an ESTABLISHED store with no
        # runs at all is not a calm fresh start: the nightly we promised never arrived.
        if missed_run:
            return _missed_run_status()
        upgrade = has_earlier_run_history(store_created_at=store_created_at)
        if app_config.has_completed_setup() and _schedule_confirmed_missing(schedule_status):
            # Honest (finding #1b): a completed install with NO nightly schedule does NOT sync on
            # its own — say so plainly instead of "new syncs will appear" / "your nightly sync
            # will appear" (both imply automation that isn't set up). Calm WARNING, NO fix CTA/badge
            # — a manual-only district must not be nagged. Only fires on a CONFIRMED-absent
            # read-back (MISSING), never on an unconfirmed None/UNKNOWN (which would falsely deny a
            # schedule we simply can't see).
            detail = EMPTY_NO_AUTO_SYNC_DETAIL
        elif upgrade:
            detail = _FRESH_START_LEAD
            if _schedule_is_live(schedule_status):
                detail += f" Your next nightly sync is scheduled for {schedule_status.next_run_display}."  # type: ignore[union-attr]
        elif _schedule_is_live(schedule_status):
            # An install with an empty store and a CONFIRMED-LIVE nightly: name the time instead
            # of the generic wait. "first" is deliberately absent HERE and in ``_FIRST_SYNC_LEAD``
            # alike — an empty store is not proof of a first sync (the <= v3.4.0 upgrader has
            # none, and ``_expects_a_nightly`` admits it via ``schedule_registered``), while the
            # scheduled time is true either way. The upgrade branch above says "next" rather than
            # nothing because a store stamp IS evidence a run was already recorded.
            detail = f"Your nightly sync is scheduled for {schedule_status.next_run_display}."  # type: ignore[union-attr]
        elif _expects_a_nightly(app_config, schedule_status):
            detail = _FIRST_SYNC_LEAD
        else:
            # No stamp, no confirmed schedule and no registration on record — nothing here may
            # name a nightly sync at all.
            detail = _NO_RUNS_YET_LEAD
        return HomeStatus(
            verdict=Verdict.WARNING,
            headline=EMPTY_FRESH_START_HEADLINE if upgrade else EMPTY_NO_RUNS_HEADLINE,
            detail=detail,
            fix=None,  # nothing to fix — just wait for the first run
            metrics=None,
        )

    latest = records[0]

    # Classify the latest record's fault axis via the SINGLE-SOURCE precedence (shared with IA-6's
    # Run History so a Home verdict + a Run-History row/banner can never drift). Staleness is a
    # separate time-relative axis layered on top of the CLEAN reason below. Each branch keeps its
    # OWN Home copy — the reason drives ONLY the verdict selection, never the wording. NEVER
    # interpolate the record's free-text `error` (privacy) — every headline/detail is a FIXED
    # category sentence (only the record's own timestamp is rendered, via `friendly_timestamp`).
    reason = classify_latest_reason(latest)

    # Rule: last run failed — the dominant fault (precedence over SFTP/anomaly/data-errors).
    # 0032 T1 #1b: never the hard-coded "Last night's…" — a failed latest can be any age, so
    # the copy derives from the record's own timestamp ("recently" when unknown/unparseable).
    if reason is LatestReason.FAILED_ETL:
        return HomeStatus(
            verdict=verdict_for_reason(reason),
            headline="Last sync failed",
            detail=_with_schedule_note(
                f"The sync that ran {friendly_timestamp(str(latest.get('timestamp', '')), now=now)} "
                "hit a problem and didn't finish.",
                schedule_status,
            ),
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: SFTP delivery failed (ETL succeeded but the roster didn't reach SpacesEDU).
    # A delivery-only failure built nothing this run — say so (0034 Slice 2 honesty).
    # 0032 T2 #4: the fix lives in Settings' delivery section (host/credentials), so the CTA
    # routes there — not to the read-only run ledger — and the label says where it goes.
    if reason is LatestReason.FAILED_DELIVERY:
        return HomeStatus(
            verdict=verdict_for_reason(reason),
            headline="Your roster didn't reach SpacesEDU",
            detail=_with_schedule_note(
                "The upload of your saved files failed."
                if is_delivery_only(latest)
                else "The data was built but the upload failed.",
                schedule_status,
            ),
            fix=FixAction(_OPEN_SETTINGS_LABEL, _SETUP_FIX),
            metrics=None,
        )

    # Rule: seasonal pause — outside an enabled window, no nightly sync is expected. Slotted BELOW
    # the two FAILED reasons (a real failure still surfaces in summer) and ABOVE missed-run / stale /
    # anomaly / data-warnings / healthy — those describe an expected nightly cadence that is moot
    # while the season is intentionally paused. The pause is HEALTHY-toned; nothing is wrong.
    if paused:
        return _paused_status(app_config, now=now)

    # Rule: missed run — the newest record is older than the window while the schedule is LIVE.
    # Slotted below the two FAILED reasons (a red verdict is never downgraded to this amber) and
    # above the warning-tier reasons: when it fires, their copy ("the last sync…") would describe
    # a run over a day old — "nothing arrived last night" is the fresher, more actionable fact.
    if missed_run:
        return _missed_run_status()

    # Rule: anomaly / >20% drop — delivered but suspicious → attention, not failure.
    if reason is LatestReason.ANOMALY:
        anomalies = latest.get("anomalies") or []
        return HomeStatus(
            verdict=verdict_for_reason(reason),
            headline="Something looked off in the last sync",
            detail=friendly_anomaly_detail(len(anomalies), variant=AnomalyVariant.HOME),
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: data errors present — delivered, no anomaly, but some records were skipped.
    if reason is LatestReason.DATA_WARNINGS:
        total_data_errors = _data_errors_total(latest)
        return HomeStatus(
            verdict=verdict_for_reason(reason),
            headline=f"Completed with {total_data_errors} data {pluralize('warning', total_data_errors)}",
            detail="A few records had field problems and were skipped — the sync still delivered.",
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: stale — a clean delivered success, but too old (a nightly run may have been missed).
    timestamp = str(latest.get("timestamp", ""))
    if is_stale(timestamp, now):
        return HomeStatus(
            verdict=Verdict.WARNING,
            headline="No recent sync",
            detail=(
                f"The last successful sync was {friendly_timestamp(timestamp, now=now)} — "
                "a nightly run may have been missed."
            ),
            fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
            metrics=None,
        )

    # Rule: healthy — a recent, clean success. The reassurance the surface exists to give,
    # honest on BOTH axes (0032 T1 #1a/#1c): "syncing" (ongoing automation) is asserted only on a
    # CONFIRMED-LIVE schedule read-back — anything less keeps the record-scoped "up to date"; and
    # "delivered to SpacesEDU" only when the record's SFTP axis says it genuinely shipped — a
    # local-only run says where the files actually went. A clean delivery-only latest counts as a
    # fresh sync (its sftp_ok is the delivery), but its COUNTS come from the newest BUILD record —
    # or no number at all, never zeros.
    counts_record = _counts_source(records, latest)
    when = friendly_timestamp(timestamp, now=now)
    detail = (
        f"Last sync delivered to SpacesEDU {when}."
        if sftp_delivered(latest)
        else f"Last sync completed {when} — files were written to your output folder."
    )
    # 0038 S7: the one number slim Home keeps after the tile row retires — a size-plausibility
    # check the verdict cannot give. It appends to EITHER delivery phrasing and drops out
    # entirely when there is nothing honest to count (see ``size_clause``).
    #
    # ``expected_sis_type`` is read off the ``AppConfig`` this function ALREADY holds rather than
    # taken as a second injected parameter: unlike ``output_entities`` (a YAML read this module
    # may not do) the district id needs no I/O, so there is no supply to forget and the guard
    # cannot be bypassed from the view. It is applied to the record ``_counts_source`` RETURNED,
    # not to ``records[0]`` — the delivery-only fallback walks back to an older successful BUILD,
    # which can be a different district again.
    clause = size_clause(counts_record, output_entities, expected_sis_type=app_config.sis_type)
    return HomeStatus(
        verdict=Verdict.HEALTHY,
        headline=(
            "Your roster is syncing" if _schedule_confirmed_live(schedule_status) else "Your roster is up to date"
        ),
        detail=f"{detail} {clause}" if clause else detail,
        fix=None,
        metrics=_build_metrics(latest, now=now, counts_record=counts_record) if counts_record is not None else None,
    )


def _latest_is_failure(records: list[dict]) -> bool:
    """Whether the newest record's fault axis is FAILED-tier — the schedule-warning guard (W3-B).

    Derived from the SINGLE-SOURCE ``classify_latest_reason`` → ``verdict_for_reason`` pair rather
    than a hand-listed pair of reasons, so a future FAILED-tier reason automatically outranks the
    schedule warning without editing this guard (one place decides what "a failure" means).
    Pure + TOTAL: an empty list has no latest to fail, and ``classify_latest_reason`` never raises.
    """
    return bool(records) and verdict_for_reason(classify_latest_reason(records[0])) is Verdict.FAILED


def _with_schedule_note(detail: str, schedule_status: ScheduleStatus | None) -> str:
    """Append the secondary schedule clause to a FAILED detail when the nightly is CONFIRMED gone.

    Fires ONLY on an expected-but-MISSING read-back — the one schedule fact the failure banner
    cannot convey ("even once you fix this run, nothing is registered to run again"). The LIVE
    fired-but-no-record contradiction is deliberately EXCLUDED: its own copy ("your last scheduled
    run reported a problem") is the SAME category the FAILED band already names, with less
    precision, and that schedule is still registered — restating it would duplicate, not inform
    (category-only faults). Either way the Setup rail badge keeps carrying the attention signal
    independently (``schedule_status.needs_setup_badge``), so nothing is lost.
    """
    if _schedule_confirmed_gone(schedule_status):
        return f"{detail} {_SCHEDULE_GONE_NOTE}"
    return detail


def _schedule_confirmed_gone(schedule_status: ScheduleStatus | None) -> bool:
    """Whether the read-back DEFINITIVELY confirms an EXPECTED nightly schedule is absent.

    ``attention`` narrows an unexpected MISSING out (a manual-only district was never promised a
    nightly); ``state is MISSING`` narrows the LIVE contradiction flavor out. ``None``/UNKNOWN can
    never satisfy either — the D4 honesty invariant (we don't speak of a schedule we can't see).
    """
    return schedule_status is not None and schedule_status.attention and schedule_status.state is ScheduleState.MISSING


def _schedule_attention(schedule_status: ScheduleStatus | None) -> HomeStatus | None:
    """The schedule-attention verdict when the read-back needs a Setup fix, else ``None`` (D4).

    Renders ``schedule_status``'s single-source copy (category-only, PII-free) as a WARNING
    routed to Setup. Only fires on the ``attention`` signal (expected-MISSING or a fired-but-
    no-record contradiction); a clean LIVE, an unexpected MISSING, and every UNKNOWN return
    ``None`` — Home never nags and never asserts an unconfirmed schedule. The CALLER additionally
    withholds it when the latest record is FAILED-tier (W3-B) — this builder stays single-purpose.
    """
    if schedule_status is None or not schedule_status.attention:
        return None
    return HomeStatus(
        verdict=Verdict.WARNING,
        headline=schedule_status.headline,
        detail=schedule_status.detail,
        fix=FixAction(_OPEN_SETUP_LABEL, _SETUP_FIX),
        metrics=None,
    )


def _is_missed_run(
    records: list[dict],
    *,
    now: datetime | None,
    store_created_at: str | None,
    schedule_status: ScheduleStatus | None,
) -> bool:
    """Whether a CONFIRMED-LIVE schedule produced no run record inside the missed-run window.

    Every fact must POSITIVELY hold (when in doubt, stay silent — a false "missed run" on day
    one costs more trust than a one-day-late first warning; owner rule, 2026-07-15):

    - the read-back is LIVE (the probe result, NEVER the config hint alone — D4 honesty);
    - the fresh-start guard: the store's ``created_at`` is itself older than the window (a
      day-one install hasn't missed anything yet; ``None``/unparseable → silent);
    - no record's timestamp falls inside the last ``MISSED_RUN_AFTER_HOURS`` — an empty,
      readable store counts (nothing ever arrived); with records, the newest must be
      POSITIVELY older than the window (unparseable → can't establish the gap → silent).
    """
    if schedule_status is None or schedule_status.state is not ScheduleState.LIVE:
        return False
    if not is_stale(store_created_at or "", now, stale_after_hours=MISSED_RUN_AFTER_HOURS):
        return False
    if records:
        return is_stale(str(records[0].get("timestamp", "")), now, stale_after_hours=MISSED_RUN_AFTER_HOURS)
    return True


def _missed_run_status() -> HomeStatus:
    """The missed-run WARNING — a LIVE schedule promised a nightly sync and none arrived."""
    return HomeStatus(
        verdict=Verdict.WARNING,
        headline="We expected a nightly sync that didn't arrive",
        detail=(
            "Your nightly schedule is registered, but no sync has been recorded in the last day. "
            "If this computer was off overnight, the next sync should arrive normally — otherwise "
            "check Run History and the schedule in Settings."
        ),
        fix=FixAction(_CHECK_RUN_HISTORY_LABEL, _RUN_HISTORY_FIX),
        metrics=None,
    )


def has_prior_runs(records: list[dict] | None, *, store_created_at: str | None) -> bool:
    """Whether this install has ever recorded a run (pure, TOTAL, deliberately generous).

    Three signals, OR-ed, and the generosity is the point — every wrong answer in the
    ``True`` direction costs a slightly formal welcome line, while a wrong ``False`` greets
    a district that has been syncing for a year as a brand-new install:

    * a run record in hand;
    * the store's birth stamp (``write_run_record`` is its sole creator, so a stamp means a
      run WAS recorded, even if a later quarantine-recreate left the table empty);
    * ``records is None`` — the store exists but could not be READ. The file's existence is
      a checked fact, exactly as ``AppConfig.settings_unreadable`` treats a torn
      ``config.json``: we stop asserting "you are new" without asserting anything else.
    """
    return records is None or bool(records) or bool(store_created_at)


def _has_saved_choices(app_config: AppConfig) -> bool:
    """Whether the admin has already entered any of the wizard's OWN answers.

    Deliberately the three fields the wizard collects and the band's copy refers to — the
    folders and the district. Advisory state (window geometry, the launch-page identity
    answer) is excluded on the same reasoning as ``_ADVISORY_FIELD_PREFIXES``: answering
    "who looks after this sync" is not setup progress, and treating it as such would tell
    every admin who used the launch page that they have something half-done.
    """
    return bool(app_config.input_dir.strip() or app_config.output_dir.strip() or app_config.sis_type.strip())


def welcome_band_line(*, has_run_history: bool, has_saved_choices: bool, run_history_readable: bool) -> str:
    """The line above the hosted wizard, keyed on what this install can be said to HAVE.

    Two questions, deliberately separate, because they have different answers for the
    UNREADABLE store: *is this install new?* (``has_run_history`` — no) and *may we make a
    promise about its run history?* (``run_history_readable`` — no, we could not read it).
    Collapsing them would greet an established install as new; ignoring the second would
    tell an admin their run history is safe when we could not open it.

    ``run_history_readable`` is REQUIRED, not defaulted: the permissive value is the
    over-claiming one, and CLAUDE.md's rule is that the unsafe call be unrepresentable
    rather than merely discouraged.

    Every branch is a claim about an artefact, so each one is gated on that artefact being
    checked-present — including the LAST. Falling through to the settings-only line on an
    install whose ``has_saved_choices`` we just checked as ``False`` would name the entered
    settings anyway: the same over-claim as promising an unreadable run history, aimed at
    the other artefact. The plain line exists for exactly that arm.
    """
    if not (has_run_history or has_saved_choices):
        return WELCOME_FRESH
    if has_run_history and run_history_readable:
        return WELCOME_RESUME_WITH_HISTORY
    if not has_saved_choices:
        # Reaching here with nothing saved means ``has_run_history`` carried the branch and
        # the store was UNREADABLE (a readable one would have returned above). Established,
        # and nothing we can name — so the line names nothing.
        return WELCOME_RESUME_PLAIN
    return WELCOME_RESUME_SETTINGS_ONLY


def welcome_band(app_config: AppConfig, *, records: list[dict] | None, store_created_at: str | None) -> str:
    """The ONE call the Home host makes — the band line for this install (pure, TOTAL)."""
    return welcome_band_line(
        has_run_history=has_prior_runs(records, store_created_at=store_created_at),
        has_saved_choices=_has_saved_choices(app_config),
        # ``None`` is ``read_run_records``'s "the store exists but would not open".
        run_history_readable=records is not None,
    )


def sync_window_paused(app_config: AppConfig, *, now: datetime | None) -> bool:
    """Whether an ENABLED seasonal window is currently OUTSIDE its active season (pure + TOTAL).

    Reuses the ENGINE predicate ``sync_window.in_sync_window`` (single source — the nightly gate
    and Home read the SAME window logic, so a night the engine pauses is exactly a night Home
    calls paused). ``today`` is derived from the injected ``now`` seam (``date.today()`` is never
    called in pure code), mirroring the rest of this module. Fail-safe: disabled, unset, or a
    MALFORMED window (which should be gated at save) all return ``False`` — behaving as year-round
    rather than ever suppressing a real warning behind a broken window.
    """
    if not app_config.sync_window_enabled:
        return False
    start = (app_config.sync_window_start or "").strip()
    end = (app_config.sync_window_end or "").strip()
    if not start or not end:
        return False
    today = (now if now is not None else datetime.now()).date()
    try:
        return not in_sync_window(today, start, end)
    except ValueError:
        # A malformed boundary (gated at save, but be TOTAL) → behave as year-round; never crash,
        # never hide a real fault behind a broken window.
        return False


def _paused_status(app_config: AppConfig, *, now: datetime | None) -> HomeStatus:
    """The calm HEALTHY-toned seasonal-pause state — "Paused for the summer — resumes <date>".

    A pause is intentional and healthy (the admin configured a school-year window), so the verdict
    is HEALTHY, not a WARNING — an amber tone here would erode the meaning of amber. The resume
    date is the pure ``next_resume_date`` fact rendered PII-free (``friendly_date_short`` → "Aug
    11", never a raw ISO / ``"MM-DD"``); if it can't be derived the copy degrades to a timeless
    phrasing rather than asserting a date it doesn't have.
    """
    resume = _friendly_resume(app_config, now=now)
    if resume:
        detail = (
            f"DistrictSync pauses the nightly sync over the summer break and resumes on {resume}. "
            "Nothing is wrong — this is your seasonal schedule."
        )
    else:
        detail = (
            "DistrictSync pauses the nightly sync outside your active season. "
            "Nothing is wrong — this is your seasonal schedule."
        )
    return HomeStatus(
        verdict=Verdict.HEALTHY,
        headline=_PAUSED_HEADLINE,
        detail=detail,
        fix=None,
        metrics=None,
    )


def _friendly_resume(app_config: AppConfig, *, now: datetime | None) -> str:
    """The plain "Aug 11" date the window re-opens, or ``""`` when it can't be derived (TOTAL)."""
    start = (app_config.sync_window_start or "").strip()
    if not start:
        return ""
    today = (now if now is not None else datetime.now()).date()
    try:
        return friendly_date_short(next_resume_date(today, start))
    except ValueError:
        return ""


def _schedule_confirmed_live(schedule_status: ScheduleStatus | None) -> bool:
    """Whether the read-back POSITIVELY confirms a LIVE nightly schedule (state alone).

    The healthy-headline scope (0032 T1 #1c): "Your roster is syncing" asserts ongoing
    automation, so it demands a CONFIRMED-LIVE read-back; anything less (``None`` / UNKNOWN /
    MISSING) keeps the schedule-neutral "up to date" claim. Unlike ``_schedule_is_live`` it
    does not require a next-run display — the assertion is that the schedule exists, not a
    promise to name its time.
    """
    return schedule_status is not None and schedule_status.state is ScheduleState.LIVE


def _schedule_is_live(schedule_status: ScheduleStatus | None) -> bool:
    """Whether the injected read-back confirms a LIVE schedule with a known next-run time."""
    return (
        schedule_status is not None
        and _schedule_confirmed_live(schedule_status)
        and bool(schedule_status.next_run_display)
    )


def _schedule_confirmed_missing(schedule_status: ScheduleStatus | None) -> bool:
    """Whether the read-back DEFINITIVELY confirms no schedule (MISSING) — the honest-nudge signal.

    Only ``MISSING`` (the cmdlet queried the task and it's absent) may drive the "won't sync
    automatically" empty-state copy; ``None``/``UNKNOWN`` (not probed / couldn't confirm) never do
    (they'd falsely deny a schedule we can't see — the D4 honesty invariant, inverted).
    """
    return schedule_status is not None and schedule_status.state is ScheduleState.MISSING
