# DistrictSync ETL failure policy

| | |
|---|---|
| **What this is** | The ONE written rule set for how the ETL fails: at what scope, for which entity, in which direction, with which bounded reason, and what the admin sees. Read it before adding a check, an entity or a config knob. |
| **Plan** | `.claude/plans/0053-etl-failure-policy.md` (slices S0–S15). Decisions: `docs/claugentic-DECISIONS.md` 2026-09-23. Register: one `docs/claugentic-INVARIANTS.md` row per rule P1–P16. |
| **Verified against** | commit `8d33664` (v3.25.0). Every code `file:line` below was read at that commit, except the rows plan 0053 S1–S6 flipped (2026-09-24), which cite symbols instead of lines so they do not drift. Citations into docs this change also edits (`faq.md`, the ROADMAP) are by question heading or item title, never by line, so they do not drift. |
| **Pinned by** | `tests/test_failure_policy_parity.py` (since S2): the §3 criticality table == `outcomes.ENTITY_CRITICALITY` + `DEPENDS_ON`, and the §6 vocabulary table == every member of the enums it lists, each with a non-vacuity check and a doctored-doc twin; since S3, every §6 member also has copy (`failure_copy.FAILED_CATEGORY_COPY` / `OUTCOME_TIER` / `outcome_sentence`). Since S4 it also ties `docs/partner/faq.md` to the code: the FAQ's two criticality bullets name exactly §3's CRITICAL and ISOLATABLE sets, and the isolatable one carries the "pending confirmation" clause exactly while `output-contract.md` says `Q5-status: open`. S11 extends it to the §5 site tags. Every other row is held true by review. |
| **Style** | Tables first, graded like `docs/developer/output-contract.md`. Every rule row says what the code does TODAY and carries a Status. |

---

## §0 Status legend

| Status | Meaning |
|---|---|
| `ENFORCED` | Today's code behaves as the row states, at the cited line. |
| `PLANNED (0053 Sn)` | The row states the TARGET. Its *Today* cell states what the code actually does now. Slice `Sn` flips the row to `ENFORCED` **in the same change** that makes it true (P14). |

A row may never say `ENFORCED` for behaviour that does not exist. A slice touches only its own rows.

## §1 The asymmetric-risk rule (P1)

Every posture below derives from this rule. It is the tie-breaker when two rules seem to disagree.

| Rule | Statement | Today | Status |
|---|---|---|---|
| **H1** — never widen delivered PII | A scoping rule that cannot be applied fails **CLOSED** at the smallest scope containing the fault. | Every PII-scope guard fails closed (`base.apply_row_filters`, `grades.filter_to_grade_scope`), and since S4 at the smallest scope §3 allows: an ISOLATABLE entity's guard (Family's guardian `row_filters`) leaves only that entity out — everything else ships — while a CRITICAL entity's (Staff's `row_filters` at SD83, the Students grade scope) still fails the run (`pipeline.run_transform`'s bulkhead). Two fail-OPEN H1 exposures remain: `base.py:295-301` ships every student Active when neither a status nor a withdraw-date column exists, and `filter_to_active` returns the frame unfiltered on an empty roster OR when the frame lacks its student column (`base.py:366-368`) — the first only when the Students output has no `User ID` column (`students.py:46-47`, §5 #27), the second e.g. a contacts export without its student-number column (`family.py:33`), which sends every contact, withdrawn students' included. Both are (e) safety heuristics failing open in the surplus direction; `filter_to_active` never filters to empty by design (`base.py:351`). The likelier roster fault is the OPPOSITE direction: a missing User ID SOURCE column publishes the roster `{'<NA>'}`, which the empty-roster guard does not catch, so every downstream student row is dropped (an H2 shrink — §5 #27). | `ENFORCED` scope (S4) · S11 for both exposures; D10 for the all-Active default (`base.py:295-301`) only |
| **H2** — never silently shrink or omit a deactivating file **because of a detected fault** | The run fails; the shrunken file never ships. | A detected fault in a CRITICAL entity fails the run with nothing written — except `enrollments.py:159-161`, which catches `(KeyError, MergeError)` and ships whatever homeroom rows were built. A detected fault in an ISOLATABLE entity (S4) never ships a SHRUNKEN file either: that entity's whole file is left out and the run is PARTIAL — an omission the owner accepted per entity in D1 (§3); whether an ABSENT file deactivates anything in SpacesEDU is Q5, open. | `PLANNED (0053 S10)` |
| **H3** — surplus or harmless rows | Fail **OPEN**, with a signal RECORDED on the run. | Direction correct at `staff.py:281-299` (keep all staff rather than empty `Staff.csv`), but the signal is a log line only — nothing reaches the run record. | `PLANNED (0053 S11)` |
| **H4** — an optional value is blank | Blank the value; record it. | A mapped column absent from the source is still blanked by the field-map engine (`base.py:753`), and since S6 the source observation (§10) records it: one WARNING per entity naming the column in config spelling, and the column on that entity's outcome as `missing_mapped` — whenever every file the entity reads was observed with a header row. A blank VALUE in a present column is still unrecorded (S11's note). | `ENFORCED` (an absent column, S6) · note `PLANNED (0053 S11)` |
| Deliberate exclusions | Departed staff, plan 0052's un-roled staff and withdrawn students are **configured or heuristic** exclusions, not detected faults: H2 does not reverse them. They stay under the existing >20% drop anomaly (`pipeline.py:492`) and the delivery-integrity gate. | As stated. | `ENFORCED` |

## §2 Layer stack and scope ladder

| # | Layer (each owns one question) | Today | Status |
|---|---|---|---|
| 1 | Config parse — is the mapping well-formed? | Pydantic at load; the pipeline records `config` and exits 1 (`pipeline.py:869-881`); Convert raises the typed `ConfigLoadError` and records `config` too (`convert_job`, S5). A typo'd `GlobalConfig`/`EntityConfig`/`Field*` key is silently dropped. | `PLANNED (0053 S12)` |
| 2 | Extract — could each file be read? | Missing file → empty frame + ERROR log (`extractor.py:131-135`); unparseable (or a case-insensitive name collision) → `ExtractionError`, an `EtlError` whose class category is `input_unreadable` (`extractor.ExtractionError`), which fails the run with that category. | `ENFORCED` |
| 3 | Source observation — does each entity's OWN file carry its mapped columns? | `pipeline.observe_source_columns`, called by BOTH entry points after the input is read and before the transform (AST-pinned): per entity, `preflight.missing_columns_by_entity` compares its `field_map` + `row_filters` columns with its own files' headers; the misses are logged (one `MAPPED COLUMNS MISSING` WARNING per entity) and carried on the outcome as `missing_mapped`. Advisory — see §10. | `ENFORCED` (S6) |
| 4 | Per-entity transform — did this entity build? | Every configured entity gets exactly one outcome per run in an `outcomes.OutcomeLedger` (`pipeline.run_transform`): EMPTY with its reason or BUILT with its row count at the existing branches. A raise is recorded FAILED and decided by §3 at the ONE bulkhead: an ISOLATABLE entity's data errors are rolled back, one `ENTITY NOT BUILT` ERROR line is logged with the traceback, and the loop continues; a CRITICAL entity marks every later one NOT_RUN and re-raises the SAME object. If nothing was BUILT and something FAILED, the first isolated exception is re-raised (its own category, never `no_output`). `BaseException` is never caught. | `ENFORCED` (recording S2, isolation S4) |
| 5 | Delivery-integrity gate — can this output set be vouched for? | `check_delivery_integrity` (`pipeline.py:404-470`): `no_output`, `incomplete_roster`. Unchanged by this plan. | `ENFORCED` |
| 6 | Atomic write | `save_all` backup-and-restore commit; a write `OSError` → `OutputWriteError` (`pipeline.py:1002`). | `ENFORCED` |
| 7 | Deliver | Manifest = files this run wrote (`pipeline.py:1026`, `uploader.py:512-521`). | `ENFORCED` |
| 8 | Run record | Flat per-entity counts + `status` + `error_category` + `entity_outcomes` (`pipeline.build_run_record`; the outcomes key since S2, `None` only when no ledger existed). Exactly one per build attempt on both entry points — Convert's raised attempts since S5 (§7). | `ENFORCED` |
| 9 | Copy mappers | ONE copy source, `src/ui_flet/failure_copy.py`: a failed record's detail on Home (`home_status.failed_detail`) and Run History is its `error_category`'s `FAILED_CATEGORY_COPY` entry, Convert's category-FAILED statuses read the same table, and Convert's `on_error` card is `error_card_copy(exc)` — the exception's category by TYPE. Only `no_input` / `input_unreadable` name the input folder. The closing tail says "nothing was sent" only where the surface can prove it (Convert: delivery was requested; a record: an upload was attempted and failed), else "nothing new was saved". A FAILED entity in a completed run is `LatestReason.PARTIAL` (WARNING) on all three — produced since S4 by the entity bulkhead. | `ENFORCED` |

| Scope (narrow → wide) | Who may choose it | Today | Status |
|---|---|---|---|
| cell | The field-map engine, per row (`base.py:765-800`) — blank that cell, record to `data_errors` | As stated. | `ENFORCED` |
| column | The field-map engine, column-level (`base.py:755-760`) — blank the column, record | As stated. | `ENFORCED` |
| entity | **Only** `run_transform`'s one boundary, and only for an ISOLATABLE entity (§3) | As stated (`pipeline.run_transform`, S4); AST-pinned as the one broad handler there (§11). | `ENFORCED` |
| run | A CRITICAL entity's raise; no usable input; config, extraction, integrity or output fault | As stated; also an isolated failure in a run that built nothing (re-raised as itself). | `ENFORCED` |

Transformers RAISE and never catch-to-continue at entity scope; only the orchestrator widens a fault (P2). No broad handler exists under `src/etl/transformers` outside the field-map engine (AST-pinned since S4, `tests/test_pipeline_entity_isolation.py`). Today's one violation is the NARROW catch at `enrollments.py:159-161` (`PLANNED (0053 S10)`).

## §3 Entity criticality (P3)

**Status of this table: `ENFORCED` — as a declaration since S2 and as behaviour since S4.** It is the code's `outcomes.ENTITY_CRITICALITY` + `outcomes.DEPENDS_ON`, row for row (`tests/test_failure_policy_parity.py`), and `pipeline.run_transform`'s bulkhead is the one place that branches on it: an ISOLATABLE entity's raise leaves that entity out and the run completes (PARTIAL, exit 0); a CRITICAL one fails the run as before (pinned per entity by `tests/test_pipeline_entity_isolation.py`). Decided by the owner 2026-09-23 (D1). `depends_on` lists CODE dependencies on another entity's published context state only.

<!-- failure-policy-table: criticality -->
| entity | criticality | rationale | promotion evidence | depends_on |
|---|---|---|---|---|
| Students | CRITICAL | The roster anchor (`outcomes.ROSTER_ANCHOR_ENTITY`); publishes `context.active_student_ids` (`students.py:47`); a user missing from a delivered `Students.csv` is marked Inactive (`faq.md`, "What happens to students or staff no longer in the file?"). | none — the anchor is never promoted | — |
| Staff | CRITICAL | What happens to a user missing from a delivered `Staff.csv` depends on the district's import settings (`faq.md`, "What happens to students or staff no longer in the file?"); what an ABSENT file does is unknown (Q5a). | would need the Q5a answer + a DECISIONS entry | — |
| Family | ISOLATABLE | Unity 2026-09-22: a report flip under the same filename failed the whole roster. Absence already ships (`tests/test_pipeline_delivery_integrity.py:581-590`; SD51 builds no `Family.csv`); publishes no context state; no entity reads it. Whether a guardian missing from a delivery is unlinked is open (Q5e). | absence-already-ships evidence + owner decision D1 (2026-09-23) | Students |
| Classes | CRITICAL | Publishes `context.class_artifacts` (`classes.py:49`), which Enrollments requires (`enrollments.py:37-45`). | none while Enrollments is CRITICAL | Students |
| Enrollments | CRITICAL | Whether a user missing from a delivered `Enrollments.csv` is removed from the class depends on the district's import settings (`faq.md`, "What happens to enrollments no longer in the file?"). | would need the Q5a answer + a DECISIONS entry | Classes, Students |
| CourseInfo | ISOLATABLE | isolatable by owner decision 2026-09-23, ahead of partner evidence; Q5d open. A standalone feed (output-contract *Delivery envelope*); publishes no context state; no entity reads its output. It may ship without StudentCourses. | owner decision D1 only — S14 may demote it on the Q5c/Q5d answer | — |
| StudentCourses | ISOLATABLE | isolatable by owner decision 2026-09-23, ahead of partner evidence; Q5d open. Reads the CourseInformation SOURCE file, never the CourseInfo output (`student_courses.py:112-114`); publishes no context state. It may ship without CourseInfo. | owner decision D1 only — S14 may demote it on the Q5c/Q5d answer | Students |
| StudentAttendance | ISOLATABLE | An unmapped absence code raises (`student_attendance.py:349-353`) and today kills rostering — which output-contract.md:558 says a missing attendance drop must never do. Publishes no context state; nothing reads it; absence already ships on nights without absence files. | absence-already-ships evidence + owner decision D1 (2026-09-23); Q5c open | — |

**Rules.** An entity not in this table is CRITICAL (`criticality_of`, S2). An entity that any other entity `depends_on` must be CRITICAL. **Promotion** to ISOLATABLE needs all of: (a) a recorded partner answer OR evidence that its absence already ships; (b) it publishes no `TransformContext` state; (c) no CRITICAL entity reads its output; (d) its dependency is declared here; (e) a dated DECISIONS entry. **Demotion** (S14) is one entity per change, with a contract fixture and a DECISIONS entry.

**Footnote (Q5).** CourseInfo and StudentCourses are ISOLATABLE by OWNER DECISION, not by partner evidence. Whether the two must arrive together is Q5d in `docs/developer/output-contract.md` — open. Until answered, each may ship on a night the other failed.

**Evidence (2026-09-24).** The owner reports that the import follows a hierarchy (users alone; Enrollments need Classes; Family needs users) — consistent with the CRITICAL set and DEPENDS_ON; Unity's association-removal setting is OFF. None of it answers Q5; see "Evidence so far" in `docs/developer/output-contract.md`.

## §4 Never substitute (P4)

| Rule | Today | Status |
|---|---|---|
| A FAILED or EMPTY entity is never replaced: no last-good CSV, no unfiltered rows, no partial frame, no filter-less mapping. | An EMPTY entity is skipped; an ISOLATABLE entity that FAILED is left out of `outputs` whole — nothing substituted, its rows in no output file (S4, the Unity plain-report contract fixture); a CRITICAL raise fails the run with nothing written. One partial frame ships: `enrollments.py:159-161`. | `ENFORCED` (EMPTY / FAILED, S4) · partial frame `PLANNED (0053 S10)` |
| Its previous CSV leaves the delivery glob. | Stale CSVs are moved to `archive_<ts>/` (`pipeline.py:1010`); the manifest is this run's files only (`pipeline.py:1026`). | `ENFORCED` |
| Its absence is reported on every run it persists. | A completed run whose own record has a FAILED outcome is PARTIAL / WARNING on Home, Run History and Convert, derived from that record alone (S3), and a delivery-only record inherits its build's outcomes (`home_status.build_record_for`). Since S4 the bulkhead produces that record; the vanished-entity anomaly fires only the first night (the next night has no previous CSV to compare against), and the second consecutive night is still PARTIAL (pinned, `tests/test_pipeline_entity_isolation.py`). | `ENFORCED` |

## §5 Missing-column matrix (P5) — keyed on what the column GUARDS

| Class | The column guards | Required posture | Today | Status |
|---|---|---|---|---|
| (a) `pii_scope` | WHO may be delivered (row_filters, grade scope) | Fail CLOSED; typed `SourceSchemaError(guard=PII_SCOPE)`; entity scope if ISOLATABLE, run if CRITICAL | Fails closed at the entity's declared scope (S4). Sites #1 and #4 raise the typed `SourceSchemaError(guard=PII_SCOPE)` naming the config's columns and a column COUNT only (S1); #5 and #6 are still untyped / unguarded | `ENFORCED` (entity scope, S4) · #5/#6 `PLANNED (0053 S9/S10)` |
| (b) `join_key` | An identity or join (merge keys, class artifacts, home school) | Fail CLOSED, typed; never a raw `KeyError`, never a partial result | Mixed: sites #2/#3 raise the typed `SourceSchemaError(guard=JOIN_KEY)` naming the config's columns and a column COUNT only (S1); elsewhere untyped `ValueError`s with an actionable message, raw pandas `KeyError`s, one partial ship | `PLANNED (0053 S10)` |
| (c) `contract_field` | An output field the importer requires | Rows lacking it are excluded and COUNTED; all excluded ⇒ EMPTY with a recorded reason | Family's email exclusion conforms (log count); an all-excluded entity is recorded EMPTY/`no_rows_after_transform` (S2); the exclusion COUNT reaches no record | `ENFORCED` (EMPTY + reason, S2) · recorded count `PLANNED (0053 S11)` |
| (d) `optional_field` | A value that may be blank | Blank + one aggregated WARNING + an outcome note | A mapped column ABSENT from the entity's own file(s): blank + one WARNING per entity + `missing_mapped` on its outcome (S6, the source observation). A blank value in a present column: still silent | `ENFORCED` (absent column: WARNING + `missing_mapped`, S6) · note `PLANNED (0053 S11)` |
| (e) `safety_heuristic` | A fail-open safety filter | Fail OPEN only in the surplus direction; always recorded | Direction mostly right; several sites silent | `PLANNED (0053 S11)` |

**Site catalogue.** One row per site; S11 turns this into a bijection with `# failure-policy: <class>` tags in `src/etl`. The catalogue is best-effort until S11, whose tag↔catalogue bijection test makes it complete by construction; a site found later is added with its class and target slice.

<!-- failure-policy-table: sites -->
| # | Site | Class | Today | Status |
|---|---|---|---|---|
| 1 | `base.apply_row_filters` | (a) | Checks EVERY filter column first; one `SourceSchemaError(guard=PII_SCOPE)` names all missing ones in config spelling + the source's column count, never its headers. Entity scope for Family (ISOLATABLE — the Unity plain-report fixture), run scope for Staff (CRITICAL — SD83's `Prefix`) | `ENFORCED` |
| 2 | `StudentTransformer._collapse_cross_enrollment` — the `home_school_column` check | (b) | `SourceSchemaError(guard=JOIN_KEY)`, config spelling + column count | `ENFORCED` |
| 2a | `students.py:132-150` cross-enrollment SchoolCode / User ID source columns (`working[school_col]` `:143`, `sort_values`/`drop_duplicates` on `user_id_col` `:149-150`) | (b) | Raw pandas `KeyError`, no presence check; both resolved by bare `str(field_map.get(...))` (`:132-133`), so a `{column: …}`-shaped entry becomes the text of a dict | `PLANNED (0053 S9/S10)` |
| 3 | `StudentTransformer` email `derived_dates` column check | (b) | `SourceSchemaError(guard=JOIN_KEY)`, config spelling + column count | `ENFORCED` |
| 4 | `grades.filter_to_grade_scope` | (a) | `SourceSchemaError(guard=PII_SCOPE)`, the resolved grade column + column count (was a raw `KeyError` listing every header). Run scope — its only caller is Students, which is CRITICAL (§3) | `ENFORCED` |
| 5 | `grades.split_by_homeroom_grades` `grades.py:340/:342` (`df[grade_col]`) | (a) | Implicit pandas `KeyError` | `PLANNED (0053 S10)` |
| 6 | `students.py:193` grade column for the student scope (`resolve_column` ignores a bare string) | (a) | A bare-string rename is ignored | `PLANNED (0053 S9)` |
| 7 | `enrollments.py:37-45` class artifacts absent | (b) | Raises with an actionable message | `ENFORCED` |
| 8 | `classes.py:389-393` course merge `course_df[[SCHOOL_NUMBER, COURSE_CODE, COURSE_TITLE]]` | (b) | Raw pandas `KeyError` | `PLANNED (0053 S10)` |
| 9 | `classes.py:400-404` staff merge `staff_df[[teacher_id_col, LAST_NAME]]` | (b) | Raw pandas `KeyError` | `PLANNED (0053 S10)` |
| 10 | `enrollments.py:159-161` homeroom merge | (b) | **DEFECT** — catches `(KeyError, MergeError)`, ships a partial set | `PLANNED (0053 S10)` |
| 11 | `classes.py:155`, `enrollments.py:116`, `:312` — `.get("Homeroom", "homeroom").lower()`; the hardcoded schedule `"grade"` literal at `classes.py:309`, `enrollments.py:194`, `blended.py:649`, `:685`; the hardcoded ClassInformation `"primary teacher"` / `"section letter"` literals at `enrollments.py:288-289` | (b) homeroom · (a) grade · (b) co-teacher | `Homeroom` breaks on the `{column: …}` shape; the schedule grade and co-teacher columns are not configurable at all (Configurable Columns rule) — a missing grade surfaces as #5's raw `KeyError`, a missing co-teacher column as #15's silent omission | `PLANNED (0053 S9)` |
| 12 | `staff.py:273-275` status column absent | (e) | **DEFECT** — silent return, every staff row kept | `PLANNED (0053 S11)` |
| 13 | `staff._merge_roster` `staff.py:340-345` — guard (empty frame, `teacher_id_col` absent from the staff file, `STAFF_SOURCEID` absent from the roster) | (e) | Roster merge skipped silently | `PLANNED (0053 S11)` |
| 13a | `staff._merge_roster` `staff.py:347` — `roster_df[[teacher_id_col, STAFF_SOURCEID]]`; the guard checks `teacher_id_col` on the STAFF file only | (b) | A roster file lacking the teacher-id column raises a raw pandas `KeyError` (category `unknown`) | `PLANNED (0053 S10)` |
| 14 | `base.filter_to_active` `base.py:366-368` | (e) / H1 | WARN, returns the frame unfiltered on an empty roster or an absent student column — withdrawn students' guardians ship in Family (`family.py:33`, e.g. a contacts export without its student-number column) and their enrollments ship too. Surplus direction; never filters to empty by design (`base.py:351`) | `PLANNED (0053 S11)` |
| 15 | `enrollments.py:288-336` co-teacher enrollments — the `None` returns at `:290-293` (primary-teacher, school or staff-id column absent), Path 1's skips at `:310` (no homeroom classes, or no section column) and `:313` (homeroom or school column absent from the homeroom classes), and Path 2's at `:336` (no Master Timetable ID column) | (b) | **DEFECT** — silent: every co-teacher enrollment row (or every row of that path) is OMITTED from `Enrollments.csv` (a deactivating file). SHRINK direction, so H2 applies, not (e)'s surplus-only fail-open | `PLANNED (0053 S10/S11)` |
| 16 | `blended._teacher_from_frame` `:551`, `_build_teacher_name_map` `:591` | (d) | Silent — only the teacher-name segment of the blended class name is omitted (`blended.py:565-566`, `:615-616`); inconsistent with `_build_grade_map`'s WARN (`:654`) | `PLANNED (0053 S11)` |
| 17 | `base.py:288-294` withdraw-date fallback (no status column) | (e) | INFO only | `PLANNED (0053 S11)` |
| 18 | `base.py:295-301` all-Active default | (e) / H1 | One WARNING; everyone ships Active | `PLANNED (0053 S11 + D10)` |
| 19 | `apply_field_map` direct read `base.py:753`, target an identity/join field (`User ID`, `Student User ID`, `Class ID`, `School ID`) | (b) | Intended blank — the whole identity column ships blank. Since S6 an ABSENT column is observed: one WARNING per entity + `missing_mapped` on its outcome (when every file the entity reads was observed); it still fails nothing. For Students `User ID` the blank also poisons the published roster — see #27 | `ENFORCED` (observation, S6) · `PLANNED (0053 S11)` |
| 20 | `family._exclude_rows_without_email` `family.py:40` | (c) | Excludes + counts once — the POSITIVE exemplar | `ENFORCED` |
| 21 | `staff.filter_departed_staff` `staff.py:250-307` | (e) | Unrecognised vocabulary / would-empty → WARN + keep all (`:281-299`) | `ENFORCED` (direction; the record note is S11) |
| 22 | `staff.resolve_staff_roles` `staff.py:102-207` (plan 0052) — the rescue-then-drop rule, EXCLUDING its evidence reader (#22a) | (e) | Rescue-then-drop; counts logged | `ENFORCED` |
| 22a | `staff._teacher_of_record_ids` `staff.py:241-244` — the plan-0052 rescue's evidence reader | (b) | **DEFECT** — silent `continue` when a schedule/ClassInformation frame lacks the teacher-id column: the rescue finds nobody and every un-roled teacher is DROPPED from `Staff.csv`. SHRINK direction caused by a detected fault (H2) | `PLANNED (0053 S10/S11)` |
| 23 | `student_attendance._derive_category` unmapped `(Absent Code, Authorized)` | (b)-shaped vocabulary | Raises; since S4 contained at entity scope (StudentAttendance is ISOLATABLE), so rostering still ships — a config whose ONLY entity is attendance still fails, since nothing was built. Its message echoes a value only when it is inside the closed vocabulary — an upper-case code, a `Y`/`N`/blank flag (S4, §8) | `ENFORCED` |
| 24 | `apply_field_map` direct read `base.py:753`, target an importer-required field (e.g. Family `Email`) | (c) | Intended blank; for Family the blank rows are then excluded (#20). Since S6 an ABSENT column is observed: one WARNING per entity + `missing_mapped` on its outcome (when every file the entity reads was observed), and an entity that then kept no row is EMPTY/`missing_source_column` instead of `no_rows_after_transform` (SD51 2026-09-18) | `ENFORCED` (observation + reason, S6) · `PLANNED (0053 S11)` |
| 25 | `apply_field_map` direct read `base.py:753`, any other target | (d) | Intended blank. Since S6 an ABSENT column is observed: one WARNING per entity + `missing_mapped` on its outcome (when every file the entity reads was observed) | `ENFORCED` (observation, S6) · `PLANNED (0053 S11)` |
| 26 | Sibling intended-blank reads: `models.FieldTransform` `models.py:191-194`; `models.FieldAppendYear` with append disabled `models.py:273` | classed by target, as #19/#24/#25 | Intended blank. Since S6 an ABSENT column is observed: one WARNING per entity + `missing_mapped` on its outcome (when every file the entity reads was observed) | `ENFORCED` (observation, S6) · `PLANNED (0053 S11)` |
| 27 | `students.py:46-47` — the published roster, two triggers | (i) (e) / H1 · (ii) (b) / H2 | (i) No `User ID` OUTPUT column ⇒ no roster ⇒ every downstream `filter_to_active` (#14) fails open (surplus). (ii) The realistic one: the User ID SOURCE column is absent ⇒ `apply_field_map` writes scalar `pd.NA` (`base.py:753`) ⇒ the roster is `{'<NA>'}`, which `base.py:366` (empty-roster guard only) does not catch ⇒ every downstream filter fails CLOSED and drops ALL student rows in Family, homeroom classes, Enrollments and StudentCourses — a silent shrink | (i) `PLANNED (0053 S11)` · (ii) `PLANNED (0053 S10)` |
| 28 | `enrollments.py:210` student subject rows, `:225` non-blended teacher rows, `:149` homeroom teacher rows (the `_y` column exists only when `:128` found the staff-ID column) — join-key column absent | (b) | **DEFECT** — rows silently left out; a schedule without its student-ID column ships timetable classes with teachers only, unenrolling every timetable student (homeroom student rows come from the demographic file, `enrollments.py:64`, `:108-145`, and are unaffected) | `PLANNED (0053 S10)` |
| 29 | `base.assign_class_ids` `base.py:554-567` → `generate_class_id` `row.get(mt_id_col, "")` `:543-547`; `models.FieldAppendYear.apply` `models.py:274-277`; `classes.py:325-326` `merged.get(school_col, "")` | (b) | Silent blank identity/join value — every subject `Class ID` (or `School ID`) ships blank | `PLANNED (0053 S10)` |
| 30 | `classes.py:155-162` homeroom column absent | (b) | Raw pandas `KeyError` from `drop_duplicates` (`:159`); the guard after it (`:161-162`) would skip homeroom classes silently (D9) | `PLANNED (0053 S10)` |
| 31 | `blended._resolve_working_frame` `blended.py:180-188`; `_build_course_title_map` `:692-697` | (d)/(e) | ClassInformation columns missing → INFO fallback to the schedule, or WARN + no blended detection; course-title map WARN + `{}` — unrecorded | `PLANNED (0053 S11)` |
| 32 | `course_codes.filter_excluded_course_codes` `course_codes.py:40-54`; `filter_excluded_course_code_patterns` `:57-79` (column via `resolve_course_code_column` `:33-37`) | (e) | Silent: no course-code column ⇒ the configured exclusions are not applied and the surplus rows ship (e.g. SD40's `ATT--AM/PM` sections as classes; CourseInfo's K/X/ATT patterns) | `PLANNED (0053 S11)` |
| 33 | `student_attendance.py:237-252` (daily band), `:324-333` (period band) — `record.get(col)` on configured columns | (b)/(c) | Silent, per band. **Daily:** absent code ⇒ every row dropped (EMPTY); absent student, school or date ⇒ rows ship with that key field blank (`:249-252`); absent portion ⇒ every full day counted as one row (`:244`, `:375-382`); absent authorized ⇒ every code misses the category map and raises via #23 (`:345-353`). **Period:** absent category or student ⇒ every row dropped (EMPTY, `:326-328`); absent school or date ⇒ blank key fields | `PLANNED (0053 S11)` (entity-scoped from S4) |
| 34 | `student_courses.py:235-248` (history), `:369-375` (selection) — `record.get(col)` on configured columns | (b)/(c) | Silent: an absent student-ID or course-code column skips every row (entity silently EMPTY); `SCHOOL_NUMBER` reads blank (`:244`, `:374`) | `PLANNED (0053 S11)` |
| 34a | `student_courses._build_info_lookups` `student_courses.py:193-208` — CourseInformation lookups (`record.get(cols["course_code"])` `:200`, `SCHOOL_NUMBER` `:203`, title/credit `:204`) | (b) key · (d) title/credit | Silent: an absent course-code column leaves both lookups empty, so every transcript row loses its title and credit value; an absent title or credit column blanks that field | `PLANNED (0053 S11)` |
| 35 | `classes._homeroom_name` `classes.py:182`, `:270-271` (`row[teacher_name_col]`) — homeroom teacher-name column | (d) | Raw pandas `KeyError`; fails the run (fail-CLOSED on an optional display value). The same column IS guarded at `classes.py:238` | `PLANNED (0053 S10)` |
| 36 | `enrollments.py:212`, `:226` — schedule `SCHOOL_NUMBER` in the subject-enrollment slices (the guards at `:210`/`:225` do not check it) | (b) | Raw pandas `KeyError` (distinct from #28's silent omission) | `PLANNED (0053 S10)` |
| 37 | `naming.generate_class_name` `naming.py:61-73` — subject class name | (d) | Silent default `"Unknown Course"` plus a hardcoded `"title"` fallback column (Configurable Columns rule); blank teacher / section segments | `PLANNED (0053 S9/S11)` |
| 38 | `classes._assign_grades` `classes.py:437-448` — subject class `Grade` (`row.get(col, "")`) | (d) | Silent blank `Grade` | `PLANNED (0053 S11)` |
| 39 | `blended._add_session_key` `blended.py:211-224` over the hardcoded `SESSION_TIME_COMPONENTS` (`:49`) + `SCHOOL_NUMBER` | (b) | Silent: an absent component is dropped from the key. An absent time column merges distinct sections into one blended class, re-keying their students to `BLENDED_` Class IDs; an absent `SCHOOL_NUMBER` lets a blend cross schools and ships a blank `School ID` (`:358`). S9 makes the components configurable; S10 fails closed | `PLANNED (0053 S9/S10)` |

## §6 Typed errors and closed vocabularies (P6)

Classification is by TYPE only (`isinstance`), never by message text. Exemplar: the scheduler's HRESULT-keyed `task_com.MSG_*` canonicals and `windows._fail` (`windows.py:217`) — one bounded vocabulary, one log shape.

| Rule | Today | Status |
|---|---|---|
| Every boundary-crossing ETL failure is an `EtlError` with a bounded `category` | `src/etl/errors.py` holds the taxonomy (`RunErrorCategory`, `EtlError`, `SourceSchemaError`, `NoUsableInputError`, `ConfigLoadError`, and S3's `OutputFolderUnsetError`); `DeliveryIntegrityError`, `OutputWriteError` and `ExtractionError` are `EtlError`s carrying enum members; the four §8 sites raise `SourceSchemaError`. Still untyped: the raw pandas `KeyError` and untyped `ValueError` sites catalogued in §5 | `ENFORCED` (taxonomy + every carrier) · remaining sites `PLANNED (0053 S10)` |
| The classifier reads no exception text | `errors.classify_error_category` branches on `isinstance` only; no usable input is the TYPE `NoUsableInputError`. AST-pinned (`tests/test_etl_errors.py`: no `str(` call, no string-`in` test) | `ENFORCED` |
| Every closed-enum member maps to copy, a verdict and a row here | `failure_copy.FAILED_CATEGORY_COPY` is TOTAL over `RunErrorCategory` except `NONE` (which raises), `outcome_sentence` over every valid (kind, reason), `OUTCOME_TIER` over `OutcomeKind`; failed categories are `Verdict.FAILED` via `LatestReason.FAILED_ETL`. Pinned by `tests/test_ui_flet_failure_copy.py` (derived from the enums) and `tests/test_failure_policy_parity.py` (this table ↔ the copy) | `ENFORCED` |
| Config typos are loud, origin-keyed (P15): a bundled config RAISES at load; a user-dir overlay WARNS at run and is REFUSED at authoring; `Field*` leaf models forbid extras everywhere; the root stays `extra="ignore"` | `GlobalConfig`/`EntityConfig`/`FieldTransform`/`FieldNameConfig` accept and drop an unknown key (`enabled_entites`, `transfrom:`) | `PLANNED (0053 S12)` |

<!-- failure-policy-table: vocabularies -->
| enum | member | value | produced today by | Status |
|---|---|---|---|---|
| RunErrorCategory | NONE | `none` | every completed run (`pipeline.py:1072`) | `ENFORCED` |
| RunErrorCategory | NO_INPUT | `no_input` | input folder missing (`run_pipeline`'s early exit); every required file missing/empty — `NoUsableInputError`, by type | `ENFORCED` |
| RunErrorCategory | NO_OUTPUT | `no_output` | `check_delivery_integrity` (`pipeline.py:452-457`) | `ENFORCED` |
| RunErrorCategory | INCOMPLETE_ROSTER | `incomplete_roster` | `check_delivery_integrity` (`pipeline.py:459-468`) | `ENFORCED` |
| RunErrorCategory | CONFIG | `config` | config load (`run_pipeline`'s early exit); any `FileNotFoundError`; `ConfigLoadError` — Convert's config load (`convert_job`, S5) | `ENFORCED` |
| RunErrorCategory | DATA | `data` | any UNTYPED `ValueError` (e.g. `DataLoader.select_ordered`'s missing output column) — no longer the row_filters missing column, which is `source_schema` | `ENFORCED` |
| RunErrorCategory | OUTPUT | `output` | output pre-flight (`pipeline.py:896-909`); `OutputWriteError` (`:378-401`); Convert's unset-output-folder gate error `OutputFolderUnsetError` (S3 — card copy only, never recorded) | `ENFORCED` |
| RunErrorCategory | UNKNOWN | `unknown` | everything else — incl. a raw pandas `KeyError` | `ENFORCED` |
| RunErrorCategory | SOURCE_SCHEMA | `source_schema` | `SourceSchemaError` — §5 sites #1–#4 | `ENFORCED` |
| RunErrorCategory | INPUT_UNREADABLE | `input_unreadable` | `ExtractionError` — an unparseable file, or a case-insensitive filename collision | `ENFORCED` |
| OutcomeKind | BUILT | `built` | `run_transform`: the entity's transform produced rows (they are in `outputs`) | `ENFORCED` |
| OutcomeKind | EMPTY | `empty` | `run_transform`: the entity was skipped with nothing to build (always with one of the four EMPTY reasons) | `ENFORCED` |
| OutcomeKind | FAILED | `failed` | `run_transform`: the entity's own transform raised — an ISOLATABLE entity is then left out and the run continues, a CRITICAL one re-raises (S4) | `ENFORCED` |
| OutcomeKind | NOT_RUN | `not_run` | every entity after a raising one, and every entity when the run fails before the loop (`OutcomeLedger.finalize_aborted`) | `ENFORCED` |
| OutcomeReason | NONE | `none` | every BUILT outcome | `ENFORCED` |
| OutcomeReason | NO_SOURCE_FILES_DECLARED | `no_source_files_declared` | `run_transform`: the mapping declares no source file for the entity (incl. an `entity_order` name with no mapping) | `ENFORCED` |
| OutcomeReason | SOURCE_FILES_EMPTY | `source_files_empty` | `run_transform`: every source file of the entity was missing or empty | `ENFORCED` |
| OutcomeReason | NO_ROWS_AFTER_TRANSFORM | `no_rows_after_transform` | `run_transform`: the entity had input and its transform kept no row (e.g. every contact excluded for a blank email) | `ENFORCED` |
| OutcomeReason | MISSING_SOURCE_COLUMN | `missing_source_column` | FAILED: `outcomes.reason_for` — the transform raised a `SourceSchemaError` (§5 sites #1–#4), by type. EMPTY (S6): `outcomes.apply_observation` — an EMPTY/`no_rows_after_transform` entity whose source observation found a mapped column absent from its own file(s) | `ENFORCED` |
| OutcomeReason | TRANSFORM_ERROR | `transform_error` | `outcomes.reason_for`: any other raise from the transform; also what `outcomes_from_record` reads an unknown kind/reason as | `ENFORCED` |
| OutcomeReason | RUN_ABORTED | `run_aborted` | every NOT_RUN outcome | `ENFORCED` |

Guard kinds (`GuardKind`: `PII_SCOPE`, `JOIN_KEY`) and the leaf errors (`SourceSchemaError`, `NoUsableInputError`, `ConfigLoadError`) are named in the plan's *Naming table* and live in `src/etl/errors.py` (S1). `SourceSchemaError`'s `entity`, `columns` (config spelling, never empty) and `guard` are required keyword-only. One exception: site #4 (`grades.filter_to_grade_scope`) carries the RESOLVED, lower-cased grade column (or the default `grade`), not the config's spelling, until S9's resolver lands. The persisted `error_category` is normalised to the enum's plain `.value` in ONE place, `pipeline.build_run_record`, so the store column and the record JSON cannot disagree. `OutcomeKind`, `OutcomeReason`, the legal kind→reason table `VALID_REASONS` and `EntityOutcome` (frozen; an illegal state — BUILT with no rows, a reason invalid for its kind, rows on a non-BUILT outcome, an unusable `missing_mapped` — is REFUSED at construction) live in `src/etl/outcomes.py` (S2; `missing_mapped` and the EMPTY/`missing_source_column` pair S6). `apply_observation` is the ONE place an observation changes a reason, and only EMPTY/`no_rows_after_transform` ever changes — a FAILED reason stays the exception's type.

## §7 Surfacing, record keys, symmetric sinks (P7, P8, P12)

| Rule | Today | Status |
|---|---|---|
| Every configured entity has exactly one outcome per run, in the record as `entity_outcomes` | `build_run_record` writes `entity_outcomes` (`{entity: {kind, reason, rows}}`, configured order, plus `missing_mapped` — a list of config-spelling column names — only on an entity the source observation found something for, S6) on every record the CLI and Convert write, the log line and the store sharing one dict; `None` only where no ledger existed (input-folder check, config load, output pre-flight, a delivery-only record). The ledger is built at the same point on both entry points, and `OutcomeLedger.complete()` refuses a missing outcome. Its `rows` is the rows the TRANSFORM produced — the flat count keys keep their own meaning (`build_run_record`'s docstring). Read by `home_status.classify_latest_reason` and `convert_result.summarize` through the TOTAL `outcomes_from_record` (S3) | `ENFORCED` |
| A FAILED outcome in a successful run is PARTIAL / WARNING on Home, Run History and Convert every run it persists, derived from that run's own record | `LatestReason.PARTIAL` → `Verdict.WARNING` (precedence status → delivery → PARTIAL → anomaly → data warnings), worded by `failure_copy.partial_copy` on all three surfaces; a record without the key classifies exactly as before (snapshot-pinned). Produced since S4 by the bulkhead, on both entry points; Convert's anomaly-ack prompt also names why the file is missing | `ENFORCED` |
| Every build attempt on every entry point writes exactly one record through `build_run_record` | The CLI records every failure (`run_pipeline`'s failure sink; a dry run stores nothing by design). Since S5 Convert does too: its config load raises the typed `ConfigLoadError` and records `config` (`entity_outcomes` `None`) — and records any OTHER load raise by its type, as the pipeline's outer sink does; ONE broad handler in `convert_job` (`# noqa: BLE001 — symmetric failure sink (failure-policy §7); always re-raised`), from `to_raw_dict` through the quality report and closing BEFORE the SFTP leg, records `failed` through `convert._record_failed_attempt` — the category by TYPE (`classify_error_category`) and the completed ledger (`finalize_aborted`) — the same status, `error_category`, counts and completed ledger `run_pipeline`'s sink records for the same fault — then re-raises the SAME object to `on_error`. Recording is suppressed-on-failure, so it can never mask the fault. Four deliberate non-records: an unset output folder, an unusable output folder (pre-flight or the write's `OSError`), `NO_INPUT`, `NEEDS_ANOMALY_ACK` (DECISIONS 2026-09-24). A raise after `save_all` committed records `failed` though the files were written. `entity_outcomes`, `status` and `error_category` are REQUIRED keyword-only with no default on `convert._record_manual_run` (S5; `entity_outcomes` also on `build_run_record`, `run_transform` (`ledger`) and both `PipelineResult` / `ConvertResult`, S2). Pinned by `tests/test_convert_failure_record.py` (one record per attempt, identity, parity with the pipeline's record for injected faults, the non-records beside recording twins, the signature, and an AST pin that the sink ends in a bare `raise` before the SFTP leg). **What a record MEANS to Home is owner decision D14:** a FAILED MANUAL record never sets the verdict (`home_status.verdict_records`, shared by Home, the Run History banner and the probe timestamp the Setup badge reads); Run History's rows list it | `ENFORCED` |
| Partner assumption (P13): a rule that depends on SpacesEDU import behaviour cites a dated confirmation row in `output-contract.md` or the open question; until confirmed the conservative branch applies, and partner docs never claim an omitted file has no side effects | Q5 is open (`Q5-status: open`); `docs/partner/faq.md` states the criticality rule and makes no such claim — its isolatable bullet says the consequence for earlier-linked records "is pending confirmation" | `ENFORCED` (review; since S4 also `tests/test_failure_policy_parity.py`: the FAQ's bullets ↔ §3's sets, the pending clause ↔ `Q5-status`, with doctored twins) |
| Record evolution is additive JSON only — no `runs` DDL, CHECK or `user_version` change until ROADMAP:50 is fixed; readers are TOTAL | No DDL since v1; `store.py` readers are total | `ENFORCED` |
| An unknown kind/reason from a newer build reads as FAILED/TRANSFORM_ERROR (errs toward a warning) | `outcomes.outcomes_from_record` is TOTAL (never raises): a non-mapping → `()`, a blank key dropped, an unknown kind or reason → FAILED/`transform_error`, a known-but-impossible entry dropped (`tests/test_etl_outcomes.py`) | `ENFORCED` |
| Exit codes: `0` success (incl. a partial run, D3) · `1` ETL failure · `2` empty stdin / conflicting flags · `3` delivery failed | As stated; a partial run exits `0` with record `status` `success`, `error_category` `none` and the FAILED outcome in `entity_outcomes` (S4) | `ENFORCED` |

## §8 Privacy and labels (P9)

| Rule | Today | Status |
|---|---|---|
| Records, banners and cards carry closed-set codes humanised through total copy tables — never `str(e)`, cell values or paths | The store carries `error_category` only; Home, Run History and Convert word it through the total tables in `failure_copy` (S3), whose only variable text is an authored entity phrase (an unknown entity key reads "one of your files") and counts — sentinel-swept in `tests/test_ui_flet_failure_copy.py`; the free-text error is never read | `ENFORCED` |
| No OBSERVED header text in any exception message or log line (a headerless file read without its header makes row 1 a pupil) | The four sites that dumped `sorted(df.columns)` (§5 #1–#4) now carry the COUNT only (`errors.available_columns_note`); pinned by the sentinel sweep in `tests/test_etl_errors.py` and end to end in `tests/test_pipeline_run_store.py` — since S4 on both the `Pipeline failed` line and the `ENTITY NOT BUILT` traceback. The one ISOLATABLE site that echoed CELL values, the unmapped absence code (§5 #23), shows a value only when it is an upper-case code or a `Y`/`N`/blank flag (S4) | `ENFORCED` |
| Config-DECLARED file and column labels may appear in copy and record, membership-validated against the resolved config and sanitised (D4, decided 2026-09-23) | Copy: not implemented — the S6 sentence for EMPTY/`missing_source_column` names no column. Record: since S6 an outcome's `missing_mapped` carries column names in CONFIG spelling, drawn from the resolved config's own `field_map`/`row_filters` (membership by construction — never an observed header) and refused unless trimmed and printable; the same names appear in the `MAPPED COLUMNS MISSING` log line. S7's `safe_label` (length cap, file labels, copy) is not built | `PLANNED (0053 S7)` |

## §9 Column resolution (P10)

| Rule | Today | Status |
|---|---|---|
| ONE resolver, `columns.resolve_source_column`, with one shape policy built on `models.classify_field`; presence via `columns.require_columns` | Three resolvers with different bare-string rules: `base.resolve_column` (`base.py:512-527`, ignores a bare string), `family._student_number_col` (`family.py:79`), `student_courses._field_map_source` (`student_courses.py:169`), plus inline `.get(...).lower()` sites (§5 #11); `context.get_students_config` still reads the dead `global_config["mappings"]` path | `PLANNED (0053 S9)` |
| New shared behaviour goes in composed modules, not new `BaseTransformer` methods | Review-held | `PLANNED (0053 S9/S13a)` |

## §10 Source observation never enforces (P11)

| Rule | Today | Status |
|---|---|---|
| Missing mapped columns are attributed per entity over that entity's OWN files; the observation never raises, never gates, keeps preflight's soundness rule | `preflight.missing_columns_by_entity` compares each ACTIVE entity's `field_map` + `row_filters` columns (never `source_columns`, the documented cross-file auxiliary reads) with its own `source_files`' headers, and makes no claim for an entity unless every one of those files was observed with a header row. Where each entity's field_map is READ from is declared once, `preflight.OBSERVATION_SCOPE` (completeness-pinned against the registry): OWN_FILES for every entity except Enrollments (ALL_FILES — its id-role pair's teacher column is also read from ClassInformation, a Classes file) and StudentAttendance (NO_CLAIM — its field_map values are placeholders). `pipeline.observe_source_columns` — the one call site on BOTH entry points (AST-pinned) — wraps the derivation, and separately each entity's note + warning, in a broad handler logging at DEBUG (one entity's refused note never costs another entity its observation), so a raise there leaves delivered bytes, status, category and counts identical (raise-isolation twin, `tests/test_pipeline_run_store.py`). The creator's `preflight_report` keeps its own file-agnostic, cross-entity claim | `ENFORCED` (S6) |
| Guards are enforced once, where the column is used (§5) | As §5 states per site | see §5 |

## §11 Layering and justified broad excepts (P16)

| Rule | Today | Status |
|---|---|---|
| `src/etl`, `src/config`, `src/history`, `src/quality` never import flet or `src.ui_flet`; an explicit `FLET_FREE_MODULES` list never imports flet | True by inspection; no test pins it | `PLANNED (0053 S13a)` |
| Transformers never import `src.etl.pipeline` | True by inspection; unpinned | `PLANNED (0053 S13a)` |
| Exactly one entity-scope broad handler, in `run_transform` | The bulkhead's `except Exception` (reasoned `noqa: BLE001`) is the only broad handler in `run_transform`, and none exists under `src/etl/transformers` outside the field-map engine — both AST-pinned with doctored twins (`tests/test_pipeline_entity_isolation.py`, S4) | `ENFORCED` |
| Every in-scope `except Exception` carries a reasoned `# noqa: BLE001 — <reason>` | Some do (`base.py:786`, `pipeline.py` failure sink, Convert's symmetric failure sink in `convert_job` — S5, AST-pinned to end in a bare `raise`); ruff `BLE` is not selected | `PLANNED (0053 S13a)` |

## §12 Checklists and DoD delta

| When you… | You must… |
|---|---|
| add an entity | declare it in `outcomes.ENTITY_CRITICALITY` (from S2 — unlisted means CRITICAL) with a §3 row; if it publishes `TransformContext` state it is CRITICAL; classify every column it reads into §5 (a)–(e) and state what happens if the column vanishes |
| add a check on a column | pick its §5 class with the §13 tree; raise a typed error for (a)/(b); never catch-to-continue inside a transformer; add a §5 row and (from S11) its `# failure-policy:` tag |
| add a config knob | say which §5 class a missing referenced column falls into; bundled typos must fail at load (S12) |
| add a district | run its real drop; any column the district lacks must land in a §5 class you can name — see `docs/developer/adding-district.md` |
| add a failure reason or category | add the enum member, its copy row (S3) and its §6 row in the SAME change |
| finish a slice | flip your own rows here, update the parity test (from S2), record DECISIONS |

## §13 Choose your posture — five questions

Ask in order; the first YES decides.

1. **Could this widen delivered PII if the check were skipped?** → (a) `pii_scope`: fail CLOSED, typed, at the smallest scope containing the fault.
2. **Does the column key an identity or a join?** → (b) `join_key`: fail CLOSED, typed; never a raw `KeyError`, never ship what was half-built.
3. **Is the output field one the importer requires?** → (c) `contract_field`: exclude and COUNT the rows lacking it; all excluded ⇒ EMPTY with a reason.
4. **Is there a recoverability argument for failing OPEN** (a surplus row is recoverable, an empty deactivating file is not)? → (e) `safety_heuristic`: fail open in the surplus direction only, and RECORD it. Otherwise → (d) `optional_field`: blank, warn once, note it.
5. **Which entity scope contains the fault?** The transformer raises; `run_transform` alone decides entity vs run from §3. Never decide scope inside a transformer.

## §14 Patterns in use, with in-repo exemplars

| Pattern | Exemplar in this repo |
|---|---|
| Typed errors + canonical, injective messages; one log anchor | `src/scheduler/task_com.py` `MSG_*` + `windows._fail` / `_FAIL_LOG_FORMAT` (`windows.py:192`, `:217`) |
| Totality test over a closed enum (a new member without copy is RED) | `launcher._MACHINE_SCOPE_CAUSES` (`src/ui_flet/launcher.py:43`), pinned in `tests/test_ui_flet_launcher.py` |
| Reader before producer (an inert slice lands first) | plan 0049's inert provisioning slices (S-1b before S-2); here S3 before S4 |
| Origin-keyed severity (bundled RAISE, user-dir WARN — never invert) | `config/loader._apply_user_dir_domains_floor` (`loader.py:358`) |
| Exclude-and-count, never ship a blank the importer rejects | `family._exclude_rows_without_email` (`family.py:40`) |
| Measurement gate, then STOP and escalate | plan 0052 (staff role) — measured on real drops before the default changed |
| Required keyword-only safety parameter (no permissive default) | `upload_csvs(..., *, manifest)`; `_store_run_record(..., *, dry_run)` (`pipeline.py:668`) |
