# DistrictSync output contract

| | |
|---|---|
| **contract_version** | `1.0.0` |
| **emitted_by** | DistrictSync >= v3.8.1 |
| **published_reference** | SpacesEDU *Advanced CSV* v1.0 (2025-07-23) — [Google Doc `1BePvuk5rg-YjUUvdwjb3X3Z0JWEUc5AtVjDfR3nub0U`](https://docs.google.com/document/d/1BePvuk5rg-YjUUvdwjb3X3Z0JWEUc5AtVjDfR3nub0U) |
| **status** | Maintained mirror. **Confirmation is recorded PER ROW — there is no doc-wide confirmation stamp.** |
| **mechanical mirror** | `tests/contract_schema.py` (the data) · `tests/test_contract.py` (the sweep) · `tests/test_output_contract_doc.py` (this doc ↔ that data) |

### Changelog

| contract_version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-07-29 | First publication. Documents the 8 emitted entities as of DistrictSync v3.8.0/v3.8.1, the per-entity BOM rule, the delivery envelope, the attendance knobs, and the myBlueprint+ course feeds as internal spec. No emitted bytes changed by this document. |

> `contract_version` versions **this document's statement of the contract**, not the app. Bump MINOR for an additive, backward-compatible statement (a new entity, a new documented knob); bump MAJOR when an emitted column set, column order, filename, or encoding changes — which by the trust chain below requires importer re-confirmation *before* merge.

---

## Trust chain

Three links, each with a different kind of authority. Read top-down: each link is a mirror of the one above it, never a replacement.

| # | Link | Authority | What it can and cannot tell you |
|---|---|---|---|
| 1 | **The live SpacesEDU importer** | **Source of truth.** External to this repo, unversioned, changes without notice. | It decides whether a delivery is accepted. It is not readable from here, so nothing in this repo can *prove* what it does. |
| 2 | **This document** | **Maintained mirror**, with dated per-row confirmations. | It states what we emit and — where a row says so — what the owner confirmed against link 1 on a given date. A row without a confirmation date has *not* been confirmed, whatever other evidence it carries. |
| 3 | **`tests/contract_schema.py` + `tests/test_contract.py`** | **Mechanical enforcement of link 2.** | It proves the code still emits what this document says. It says nothing about whether the importer accepts it. |

**Our tests guard OUR side of the handshake only.** A green suite means "we still emit what we said we emit". It does not mean "SpacesEDU will accept this". The only instrument that settles link 1 is an owner check against the live importer, recorded here as a dated row.

### Re-confirmation trigger

Any change to one of these **requires fresh importer confirmation before merge**, and a `contract_version` MAJOR bump here:

- a base `field_map` **key set or key order** for any entity in `config/mappings/myedbc_mapping.yaml` (this is what sets the emitted columns and their order);
- `DataLoader._NO_BOM_ENTITIES` (the per-entity BOM rule);
- the attendance knobs — `global_config.attendance.date_format`, `attendance.daily.category_map`, or `attendance.daily.portion`;
- the entity → filename rule (`DataLoader.csv_filename`) or the delivery envelope (zip name, standalone-file rule);
- **any `to_csv` writer option this document marks GUARANTEED** — quoting, escaping, delimiter, the empty-string representation of `NaN`, the header row, the index column. These are all currently pandas *defaults* that `DataLoader._write_csv` never passes explicitly, so a pandas upgrade or a stray keyword could change an emitted byte **without touching anything named above**. They are contract because we published them, not because anything pins them;
- **the source constants behind the two owner-confirmed semantics** — `BaseTransformer.DEFAULT_STATUS_COLUMN_ALIASES` / `DEFAULT_ACTIVE_VALUES` (which decide the emitted `EnrollStatus` value) and `global_config.cross_enrollment` (which decides which school reaches `SchoolCode`). The two rows the owner confirmed describe *behaviour*, not just a header spelling: changing these changes what was confirmed, while leaving every column name intact.

A red `tests/test_contract.py` is the mechanical face of this trigger: **a red order test is a partner-visible contract change requiring importer re-confirmation, not a test edit.**

---

## How to read the per-entity tables

Every verdict row carries two independent axes. Do not collapse them.

**Status** — has the owner confirmed this against the *live importer*?

| Value | Meaning |
|---|---|
| `confirmed 2026-07-27` | The owner (who is the SpacesEDU team) explicitly confirmed this row against the live importer on that date. |
| `pending owner confirmation` | Not yet explicitly confirmed against the live importer — **regardless of how strong the other evidence is**. An accepted landing state, not debt. |
| `internal spec` | No external authority exists. **This document is the authority**; the ratifier is named in that section. |

**Basis** — what evidence we actually have.

| Value | Meaning |
|---|---|
| `observed import` | A real delivery was accepted (or rejected) by the live importer and we recorded the outcome. |
| `importer code read` | Someone read the importer's code. |
| `owner knowledge` | The owner stated it from knowledge of the product. |
| `published Doc` | Stated in one of the published references below. |
| `emitted` | This is simply what DistrictSync writes today, read from the code/config. Says nothing about acceptance. |
| `internal` | Internal spec (Confluence / this document). |

**Guarantee** — is this a promise or a courtesy? Five values are used across the tables; all five are listed so none reads as an unexplained blank:

- **GUARANTEED** — we commit to emitting exactly this. Changing it is partner-visible and hits the re-confirmation trigger.
- **TOLERATED** — leniency we *rely on* or *accept* but do **not** treat as contract. A TOLERATED item may stop working without any change on our side, and we may not depend on it in new code. The SD22 header-spelling variants (below) are the clearest example: the importer appears to accept them, but **that leniency is not our contract and is not something we emit.**
- **TOLERATED at most** — we do not emit this and make no promise about it; if it works, that is the importer being lenient. Used for shapes we *could* emit but do not (e.g. the `dd-MMM-yyyy` attendance date the published Doc documents).
- **not claimed** — we make **no** statement in either direction. Used where the honest answer is "nobody has tested this" (line-ending tolerance) or where a source comment asserts more than the evidence carries.
- **n/a** — the row is not a partner-facing promise at all but an internal safety rule of ours (the delivery manifest, the `archive_<ts>/` exclusion). Listed because it protects the partner, not because they depend on its shape.

Where the Guarantee applies to a *specific aspect*, the cell says which — e.g. "GUARANTEED (the emitted name)" promises the filename we write, not the published rule's wording.

---

## Delivery envelope

What actually leaves the machine, and under what names.

| Aspect | Value | Status | Basis | Guarantee |
|---|---|---|---|---|
| Entity → filename | `<EntityName>.csv`, exactly — `Students.csv`, `Staff.csv`, `Family.csv`, `Classes.csv`, `Enrollments.csv`, `CourseInfo.csv`, `StudentCourses.csv`, `StudentAttendance.csv`. `DataLoader.csv_filename` is the intended single source and every *write/detect/manifest* path routes through it — but it is not literally the only spelling: `sftp/uploader.py` re-spells the literal `"StudentAttendance.csv"` to split that feed out of the zip. | pending owner confirmation | emitted | GUARANTEED |
| Rostering bundle | The rostering + course CSVs ship inside one zip named `districtsync_<district>_YYYY-MM-DD.zip` (e.g. `districtsync_sd40_2026-04-10.zip`); `districtsync_YYYY-MM-DD.zip` when no district is supplied. Single source: `sftp/uploader.build_zip_name`. | pending owner confirmation | emitted | GUARANTEED |
| Zip idempotency | The date stamp makes a retry a re-put over the same remote name rather than a duplicate delivery. | pending owner confirmation | emitted | GUARANTEED |
| `StudentAttendance.csv` | Ships **standalone, outside the zip**, put into the same remote directory. SpacesEDU's nightly check looks for it by name and it must not pollute the Advanced-CSV bundle. | pending owner confirmation | observed import, owner knowledge | GUARANTEED |
| Attendance filename rule | The published BC/Aspen attendance Doc states a "file name must end with" rule. DistrictSync satisfies it by emitting exactly `StudentAttendance.csv`; the Doc's literal wording is **not mirrored in this repo**, so this row is a cited-not-quoted reference. The 2026-06-19 incident's error text (*"Unexpected file: StudentAttendance.csv"*) confirms the importer identifies this feed by name. | pending owner confirmation | published Doc (cited, not quoted), observed import | GUARANTEED (the emitted name) |
| Delivery manifest | Only files the run *vouched for* are uploaded — `DataLoader.output_filenames(outputs)`, passed as the required keyword-only `manifest=` to `SFTPUploader.upload_csvs`. A stray `*.csv` an admin drops in the output folder never egresses. | n/a (our own safety rule) | emitted | GUARANTEED |
| `archive_<ts>/` | Stale entity CSVs this run did not produce are **moved** into this subfolder, never deleted. The uploader globs `*.csv` **top-level only**, so archived files structurally cannot ship. | n/a | emitted | GUARANTEED |
| `.tmp_<ts>_<uid>/` · `.bak_<ts>_<uid>/` | Staging and pre-commit backup dirs used by the atomic write. Both are removed at the end of a **COMPLETED** run; a `.bak_*` stranded by an interrupted run is deliberately **retained** (archived into `archive_<ts>_recovered/`, never auto-deleted — it is the only copy of the pre-commit originals). Neither can ship: both are subfolders, and the uploader globs top-level only. | n/a | emitted | GUARANTEED (neither can ship) |

---

## Encoding, line endings, quoting

### BOM matrix

`DataLoader.csv_encoding(entity)` is the **code source of truth**; `tests/contract_schema.NO_BOM_ENTITIES` is this contract's statement of the same rule, and `test_contract.test_loader_encoding_policy_matches_the_contract` pins the two together so they cannot drift.

<!-- contract-table: bom-matrix -->

| Entity | Encoding | Why | Status | Basis |
|---|---|---|---|---|
| Students | `utf-8-sig` (BOM) | Districts open these in Excel; without the BOM, Excel mangles non-ASCII names. | pending owner confirmation | emitted |
| Staff | `utf-8-sig` (BOM) | as above | pending owner confirmation | emitted |
| Family | `utf-8-sig` (BOM) | as above | pending owner confirmation | emitted |
| Classes | `utf-8-sig` (BOM) | as above | pending owner confirmation | emitted |
| Enrollments | `utf-8-sig` (BOM) | as above | pending owner confirmation | emitted |
| CourseInfo | `utf-8-sig` (BOM) | as above | internal spec | emitted |
| StudentCourses | `utf-8-sig` (BOM) | as above | internal spec | emitted |
| **StudentAttendance** | **`utf-8` (NO BOM)** | The attendance importer is BOM-strict and case-sensitive on the first header. | pending owner confirmation | **observed import** |

**The StudentAttendance exception is not theoretical.** On 2026-06-19 a delivery was rejected with *"Unexpected file: StudentAttendance.csv"* plus a cascading *"Invalid date format"*, even though the data matched the spec. Root cause: every CSV was written `utf-8-sig`, so the importer's BOM-strict header check read the first header as `﻿School Number` ≠ `School Number` — the file was not recognized and column mapping broke, which then mis-validated the date column. The fix was the `_NO_BOM_ENTITIES` allowlist. The rostering CSVs were unaffected because they reach SpacesEDU through the zip path, which tolerates the BOM. See `docs/claugentic-DECISIONS.md` → 2026-06-19.

The two encoding classes are asserted end-to-end on real bytes, one config per class: `sd51myedbc` for the BOM class, `sd51attendance` for the bare `School Number` first header.

### Line endings — stated truthfully

**We do not pin line endings.** `DataFrame.to_csv` writes to a path with pandas' default `lineterminator`, which is `os.linesep`. Therefore:

| Where the run happens | Emitted line ending |
|---|---|
| Windows (the district-server case, and the Windows exe) | **CRLF** (`\r\n`) |
| Linux / macOS artifacts | **LF** (`\n`) |

So the *same* config emitting the *same* data produces byte-different files on different platforms. This **matches pandas' documented default (`lineterminator=os.linesep`) and is not pinned by a test** — see Q3. CRLF is RFC-4180's line ending, so the Windows shape is the standards-conformant one; LF is the common tolerated shape.

| Row | Status | Basis | Guarantee |
|---|---|---|---|
| Importer tolerance for LF vs CRLF | **pending owner confirmation** — **owner question Q3: "we emit CRLF on Windows and LF on the Mac/Linux artifacts — does the importer care?"** | none (this has never been tested either way) | not claimed |

A `lineterminator="\r\n"` pin in `DataLoader._write_csv` would make this deterministic. It is deliberately **not** done here (this document's slice is zero-runtime-change) and is tracked in `docs/claugentic-ROADMAP.md` — it needs its own snapshot-gated slice because it changes emitted bytes on the Linux/macOS artifacts.

### Quoting and empty values

| Aspect | Value | Status | Basis | Guarantee |
|---|---|---|---|---|
| Quoting | pandas `QUOTE_MINIMAL` — a field is quoted only when it contains the delimiter, a quote character, or a newline. | pending owner confirmation | emitted | GUARANTEED |
| Escaping | RFC-4180 style: an embedded `"` is doubled (`""`) inside a quoted field. | pending owner confirmation | emitted | GUARANTEED |
| Delimiter | `,` (comma), always. Input delimiters are sniffed; output never is. | pending owner confirmation | emitted | GUARANTEED |
| Missing values | `NaN` / `None` are written as the **empty string**, not `NaN` / `NULL` / `\N`. | pending owner confirmation | emitted | GUARANTEED |
| Header row | Always present, always the first line, always exactly the columns in this document's order. | pending owner confirmation | emitted | GUARANTEED |
| Index column | Never written (`index=False`). | pending owner confirmation | emitted | GUARANTEED |

---

## Per-entity columns

**Emitted order below is the order the entities are defined in the base `config/mappings/myedbc_mapping.yaml`.** Within each entity, the column order is the base `field_map` **key order**.

Scope of the universality claim, stated precisely: no bundled config alters any entity's column set or order — verified across all eleven bundled configs and pinned by `test_contract.test_column_order_matches_contract`. That is a property **held by the configs and enforced by a test**, not one guaranteed by the `_base` merge rule: a config *could* override a `field_map`, and a user-dropped YAML in the app-data `mappings/` dir shadows a bundled config entirely. **This document describes the shipped set.**

Emitted columns are treated as **POSITIONAL**. Order-sensitivity is **established in code** only for `StudentAttendance` ("exact case-sensitive order", `src/etl/transformers/student_attendance.py`); for the rostering and course feeds it is **not** confirmed with the partner, so we pin the emitted order and require re-confirmation before changing it rather than claiming the importer would reject a reorder. *(Note the word: "established in code" is a statement about our own source, deliberately NOT the dated `confirmed <date>` Status value above, which means only "the owner checked this against the live importer".)*

### 1. `Students.csv`

<!-- contract-table: Students -->

| # | Column | Source / semantics | Status | Basis | Guarantee |
|---|---|---|---|---|---|
| 1 | User ID | The pupil number (`Student Number`). The join key every other feed references. | pending owner confirmation | emitted | GUARANTEED |
| 2 | Student Number | Same pupil number, repeated as the district-facing identifier. | pending owner confirmation | emitted | GUARANTEED |
| 3 | First Name | Legal first name. | pending owner confirmation | emitted | GUARANTEED |
| 4 | Last Name | Legal surname. | pending owner confirmation | emitted | GUARANTEED |
| 5 | Date of Birth | Source date normalized to ISO `YYYY-MM-DD`. | pending owner confirmation | emitted | GUARANTEED |
| 6 | Grade | CEDS-coded grade via the `grade_to_ceds` transform (2-char codes such as `03`, `KG`, `12`). | pending owner confirmation | emitted | GUARANTEED |
| 7 | EnrollStatus | Derived, never copied — see *EnrollStatus resolution* below. | **confirmed 2026-07-27** | owner knowledge | GUARANTEED |
| 8 | SchoolCode | The school number the student rosters under. Under `cross_enrollment.collapse` this is the **home** school. | **confirmed 2026-07-27** | owner knowledge | GUARANTEED |
| 9 | Homeroom | Homeroom label as supplied by the district. | pending owner confirmation | emitted | GUARANTEED |
| 10 | PreRegSchoolCode | Next/pre-registration school code; blank for most rows. | pending owner confirmation | emitted | GUARANTEED |
| 11 | Preferred First Name | "Usual" first name; blank when the district supplies none. | pending owner confirmation | emitted | GUARANTEED |
| 12 | Preferred Last Name | "Usual" surname; blank when the district supplies none. | pending owner confirmation | emitted | GUARANTEED |
| 13 | Community Hours | Always the empty string today (fixed `value: ""` in the base). | pending owner confirmation | emitted | GUARANTEED |
| 14 | Literacy Test Completed | Always the empty string today (fixed `value: ""` in the base). | pending owner confirmation | emitted | GUARANTEED |
| 15 | Email Address | Either a district-supplied address or one generated from an `email format` template (`{student number}@sd51.bc.ca`, optionally `sanitize`d and with a derived date part). | pending owner confirmation | emitted | GUARANTEED |

**EnrollStatus resolution** (`BaseTransformer.compute_enroll_status` — the single source for "is this student active"):

1. Row has a **non-blank status value** → status decides. The trimmed value is emitted when it is in `active_values` (default `["Active", "PreReg"]` — the Advanced CSV spec's expected values); anything else becomes `Inactive`. The withdraw date is **not** consulted: an authoritative live status beats a lingering withdraw date on a re-enrolled student.
2. No status column, or a blank status on that row → fall back to the withdraw-date column. A past or unparseable date → `Inactive`; otherwise `Active`. Four input date formats are recognized.
3. Neither column present → `Active`, with one warning.

The status column is auto-resolved from the alias list `("enrollment status", "enrolment status")` — matched against the **normalized, lower-cased** frame (the extractor lowercases every column name on load), which is why the constant is spelled in lower case. Both real spellings occur: two-L in real MyEd exports, one-L in SD40's injected headers. A district overrides `active_values` per config.

**Rows that reach the file:** only rows whose label is not `Inactive`. A pupil enrolled at several schools produces one row per school unless the district enables `cross_enrollment.collapse`, which keeps the home-school row (first wins) — SD60 today. When it is enabled the student still keeps their enrollments and classes at **every** school (see `Enrollments.csv`); when it is not, `Students.csv` legitimately carries one row per school and the quality report will flag those as duplicates on `User ID`.

**Zero-orphan invariant:** the surviving `User ID` set is published as the active roster and every student row in `Family.csv`, `Classes.csv` and `Enrollments.csv` is filtered against it, so no other feed can reference a student absent from this file.

### 2. `Staff.csv`

<!-- contract-table: Staff -->

| # | Column | Source / semantics | Status | Basis | Guarantee |
|---|---|---|---|---|---|
| 1 | User ID | Teacher ID. | pending owner confirmation | emitted | GUARANTEED |
| 2 | First Name | Staff first name. | pending owner confirmation | emitted | GUARANTEED |
| 3 | Last Name | Staff surname. | pending owner confirmation | emitted | GUARANTEED |
| 4 | Email | Staff email address as supplied. | pending owner confirmation | emitted | GUARANTEED |
| 5 | Role | `map_role` transform: the teaching-staff flag `Y` → `teacher`, anything else → `administrator`. Those two values are the entire vocabulary and are asserted per config. | pending owner confirmation | emitted | GUARANTEED |
| 6 | School ID | School number. | pending owner confirmation | emitted | GUARANTEED |

### 3. `Family.csv`

<!-- contract-table: Family -->

| # | Column | Source / semantics | Status | Basis | Guarantee |
|---|---|---|---|---|---|
| 1 | First Name | Contact first name. | pending owner confirmation | emitted | GUARANTEED |
| 2 | Last Name | Contact surname. | pending owner confirmation | emitted | GUARANTEED |
| 3 | Email | Contact email address. | pending owner confirmation | emitted | GUARANTEED |
| 4 | Student User ID | The pupil number this contact belongs to — must exist in `Students.csv`. | pending owner confirmation | emitted | GUARANTEED |

A district may narrow the source rows with `row_filters` before mapping — SD60 keeps only rows whose `Parent Auth / Guardian` is `Y`, excluding non-guardian emergency contacts. A `row_filters` column missing from the extract fails **loud** rather than silently keeping everyone.

### 4. `Classes.csv`

<!-- contract-table: Classes -->

| # | Column | Source / semantics | Status | Basis | Guarantee |
|---|---|---|---|---|---|
| 1 | Class ID | `<Master Timetable ID>_<school year end year>` — see *Appended-year Class IDs* below. | pending owner confirmation | emitted | GUARANTEED |
| 2 | Name | Composite display name — see *The name-config composite* below. Truncated at a word boundary to 100 characters. | pending owner confirmation | emitted | GUARANTEED |
| 3 | Grade | Grade level of the class. | pending owner confirmation | emitted | GUARANTEED |
| 4 | School ID | School number. | pending owner confirmation | emitted | GUARANTEED |
| 5 | Start Date | Academic-period start, derived from `academic_start_month_day` plus the resolved school year. Every bundled config auto-derives (`use_academic_year: true`); a pinned literal date is a supported but currently unused escape hatch. | pending owner confirmation | emitted | GUARANTEED |
| 6 | End Date | Academic-period end, derived from `academic_end_month_day`. | pending owner confirmation | emitted | GUARANTEED |

**Appended-year Class IDs.** `Class ID` is `f"{master_timetable_id}_{school_year}"`, where `school_year` is the academic year's **end** year (MyEd BC convention; `school_year_naming: "end"`). The year suffix is what keeps a section reused across years from colliding. Classes and Enrollments compute the ID through the **same** `BaseTransformer.assign_class_ids`, so the two files can never disagree. A blended class overrides the ID via the blended map before that fallback applies.

**The name-config composite.** `Name` is built from four *configured* source columns (`primary teacher flag`, `teacher last name`, `course title`, `section letter`) as:

```
<Teacher last> <Course title> (<Section>) <Year>
```

The flag column gates the teacher part **only when it is both configured and actually present in the row**: in that case the teacher name is included only if the flag reads `y`. If no flag column is configured *or* the configured one is absent from the row, the teacher name is used **unconditionally** — the absent-column case falls into the same unconditional branch, which is why SD60 (no primary-teacher flag in its schedule) still gets teacher names. Empty parts are omitted (no doubled spaces, no stray parentheses). Example: `Liu Math 10 (A) 2026`.

**Which classes exist:** homeroom classes are auto-generated for the configured `homeroom_grades`, subject classes come from the schedule, and blended classes (same teacher/time spanning 2+ grade levels) merge into one. `global_config.excluded_course_codes` drops bookkeeping sections (SD40 excludes `ATT--AM` / `ATT--PM`) before any of it. Homeroom-class creation is filtered to the active roster, so a homeroom with no active students is not emitted.

### 5. `Enrollments.csv`

<!-- contract-table: Enrollments -->

| # | Column | Source / semantics | Status | Basis | Guarantee |
|---|---|---|---|---|---|
| 1 | Class ID | Must exist in `Classes.csv` — asserted per config. | pending owner confirmation | emitted | GUARANTEED |
| 2 | User ID | Student pupil number or teacher ID, whichever the row is. Never empty or `nan`. | pending owner confirmation | emitted | GUARANTEED |
| 3 | Role | `student` or `teacher` — the entire vocabulary, asserted per config. | pending owner confirmation | emitted | GUARANTEED |
| 4 | School ID | School number. | pending owner confirmation | emitted | GUARANTEED |

**Dedup key:** `(Class ID, User ID, Role)`. Homeroom, subject, blended and co-teacher rows are unioned and then deduplicated on that triple, so the same person cannot appear twice in the same class in the same role.

**Referential integrity, both directions:** every `Class ID` must resolve in `Classes.csv` (the blended-orphan regression guard), and every *student* row is filtered to the active roster (the zero-orphan invariant). Teacher rows are deliberately **not** roster-filtered — staff are not in `Students.csv`. Invalid teacher IDs (`nan`, blank) are dropped.

**Cross-enrollment:** when a district enables `cross_enrollment.collapse`, the student collapses to one `Students.csv` row but keeps an enrollment row at **every** school they actually attend.

### 6. `CourseInfo.csv` (myBlueprint+ — internal spec)

<!-- contract-table: CourseInfo -->

| # | Column | Source / semantics | Status | Basis | Guarantee |
|---|---|---|---|---|---|
| 1 | Course Code | District course code from the course catalog. | internal spec | internal | GUARANTEED |
| 2 | Alternate Course Code | Fixed empty string today. | internal spec | internal | GUARANTEED |
| 3 | School ID | School number. | internal spec | internal | GUARANTEED |
| 4 | Course Name | Course title. | internal spec | internal | GUARANTEED |
| 5 | Course Description | Fixed empty string today. | internal spec | internal | GUARANTEED |
| 6 | Discipline | Fixed empty string today. | internal spec | internal | GUARANTEED |
| 7 | Department | Fixed empty string today. | internal spec | internal | GUARANTEED |
| 8 | Type | Fixed empty string today. | internal spec | internal | GUARANTEED |
| 9 | Grade | Grade level from the catalog. | internal spec | internal | GUARANTEED |
| 10 | MaxGrade | Fixed empty string today. | internal spec | internal | GUARANTEED |
| 11 | Credit Value | Credit value from the catalog. | internal spec | internal | GUARANTEED |
| 12 | IntegrationId | Fixed empty string today. | internal spec | internal | GUARANTEED |
| 13 | Year Offered | Fixed empty string today. | internal spec | internal | GUARANTEED |

**Row selection.** Course codes matching the configured `excluded_course_code_patterns` are dropped before mapping — by default kindergarten/early-grade variants (`^.{5}-K`), `X`-prefix courses, and `ATT`-prefix attendance bookkeeping. The early-grade regex is *derived* from `course_start_grade` (default 10, so grades 10–12), which a district lowers to 8 or 9 — never below.

**Dedup key:** `(Course Code, School ID)`. The same course offered at several schools keeps one row per school; accidental duplicates within a school collapse.

### 7. `StudentCourses.csv` (myBlueprint+ — internal spec)

<!-- contract-table: StudentCourses -->

| # | Column | Source / semantics | Status | Basis | Guarantee |
|---|---|---|---|---|---|
| 1 | Student ID | Pupil number; must exist in `Students.csv` when Students is enabled. | internal spec | internal | GUARANTEED |
| 2 | Course Code | Cleaned course code — see *Course-code cleaning* below. | internal spec | internal | GUARANTEED |
| 3 | IntegrationId | Blank unless the district maps it. | internal spec | internal | GUARANTEED |
| 4 | Course Name | Course title resolved from the course catalog. | internal spec | internal | GUARANTEED |
| 5 | Completion Date | `DL Completion Date` from the history row, `dd-MMM-yyyy`. Blank for an in-progress selection. | internal spec | internal | GUARANTEED |
| 6 | Final Mark | Final mark from the history row. Blank for an in-progress selection. | internal spec | internal | GUARANTEED |
| 7 | Credits Earned | Credits actually earned (a pass). | internal spec | internal | GUARANTEED |
| 8 | Alternate Course Code | Blank unless the district maps it. | internal spec | internal | GUARANTEED |
| 9 | Potential Credits Earned | Credits a currently-enrolled course could earn. | internal spec | internal | GUARANTEED |
| 10 | Term Grade | Term grade where the source supplies one. | internal spec | internal | GUARANTEED |

**Row selection** — the transcript join, in two passes over three GDEs (course history + course selection + course info):

1. **History pass** — one row per kept history record. `W` (withdrawn) marks and pattern-excluded course codes are skipped.
2. **Selection pass** — a current selection is emitted only when it adds something: **no history** → include; **already passed** or **already in progress** → exclude; **failed with a later start date** (a retake) or a null-date fallback → include.

**Course-code cleaning**, two layers: (a) if `Full Course Code` ends with `-<Section>`, the section suffix is stripped; (b) if the code contains a configured flavor substring (`HUB`, `HOL`, `DL`, `---`), it is truncated to its first 7 characters. Catalog lookups then try an exact `(course code, school number)` match, falling back to a 7-character prefix match.

**Dedup key** (as used by the quality report): `(Student ID, Course Code, Completion Date)`.

**Configurable columns:** since 2026-07-20 every source column read here resolves through the district config — output-keyed reads through the entity `field_map`, auxiliary inputs through the optional per-entity `source_columns:` block (`full_course_code`, `section`, `dl_start_date`). The output column list is derived from the `field_map` keys, so **this table and the YAML cannot _silently_ disagree** — a divergence surfaces as a red order test rather than as a quietly different CSV.

### 8. `StudentAttendance.csv`

<!-- contract-table: StudentAttendance -->

| # | Column | Source / semantics | Status | Basis | Guarantee |
|---|---|---|---|---|---|
| 1 | School Number | School number. **Must be the literal first header with no BOM** (see the BOM matrix). | pending owner confirmation | observed import | GUARANTEED |
| 2 | Absence Date | Formatted per `global_config.attendance.date_format` (default ISO `yyyy-MM-dd`). | pending owner confirmation | observed import | GUARANTEED |
| 3 | Absence Category | Derived for the K-7 daily band, passed through for the 8-12 period band — the two are **not** one vocabulary (see below). | pending owner confirmation | observed import | GUARANTEED |
| 4 | Student Number | Pupil number. | pending owner confirmation | observed import | GUARANTEED |

Only these four columns are emitted. The SpacesEDU attendance spec permits dropping every optional field after `Student Number`, so the previous 28-column shape (24 always-blank columns) was reduced on 2026-06-19. Column order here is **case-sensitive, and order-sensitivity is established in our own code** for this entity — the one entity for which that is true. (Again: "established in code", not the dated `confirmed <date>` Status value.)

**Row multiplicity is intentional.** A full-day K-7 absence emits **two identical rows** (one per half-day). There is no `drop_duplicates` on this entity, and the quality report's duplicate check skips it for exactly that reason.

---

## The attendance feed in detail

### The contract surface is the knob, not a hardcoded literal

`global_config.attendance.date_format` is what this contract exposes. Default `"yyyy-MM-dd"`; friendly tokens `yyyy`, `yy`, `MMMM`, `MMM`, `MM`, `dd` with literal separators (so `"dd-MMM-yyyy"` is expressible). An unsupported token **fails loud**. Input GDE dates in any of the four recognized formats (`dd-MMM-yyyy`, ISO, `m/d/yyyy`, `d/m/yyyy`) are parsed and reformatted to it — **input recognition is unaffected by this setting**.

| Row | Status | Basis | Guarantee |
|---|---|---|---|
| Default `yyyy-MM-dd` is what the live importer requires | **confirmed 2026-07-30 (owner)** — Q1a settled: ISO is REQUIRED, not merely preferred | owner confirmation against the live importer; corroborated by the 2026-06-22 observed rejection | GUARANTEED as the emitted default |
| The base config comment asserting the importer "requires" ISO | **confirmed 2026-07-30 (owner)** — the comment was ahead of its evidence when written; the claim is now earned rather than inferred from one rejection | owner confirmation | GUARANTEED |
| `dd-MMM-yyyy` (the shape the published BC/Aspen Doc documents) is still accepted | **REFUTED 2026-07-30 (owner)** — the live importer does **not** accept it | owner confirmation (rank 1) **over** the published Doc (rank 2) | NOT accepted — see the footgun note below |

> **⚠ The `attendance.date_format` knob can express a shape the importer rejects.** `dd-MMM-yyyy` is
> expressible (the token vocabulary supports it) and, as of Q1a, would produce a file the live importer
> refuses. Nothing validates that today — an unsupported *token* fails loud, but a well-formed
> *non-ISO shape* does not. A district config setting it is a silent delivery failure at 3am. Tracked
> in `docs/claugentic-ROADMAP.md`; the candidate fix is to accept only ISO unless an explicit
> acknowledgement key is set, per CLAUDE.md's "make the unsafe call unrepresentable" rule.
>
> **This is a rank-1-over-rank-2 divergence:** the published BC/Aspen Doc documents `DD-MMM-YYYY`, and
> the live importer refuses it. The Doc is wrong or stale on this point and should be corrected —
> divergence register row 1.

### Two bands, two vocabularies — never one merged list

The output is the union of two independent bands. **They are different kinds of data and must not be documented as one category list.**

**K-7 daily band** (`StudentDailyAbsences.txt`) — the category is **DERIVED by us from the configured `category_map`**, so this vocabulary is **ours to promise**:

| `(Absent Code, Authorized)` | Emitted category | Meaning |
|---|---|---|
| `A` , `N` | `A` | absent, unexcused |
| `A` , `Y` | `A-E` | authorized absence |
| `T` , `N` | `L` | tardy = late |
| `T` , `Y` | `L-E` | excused late |

A non-blank pair **absent from the map raises** — it is never silently dropped or mis-bucketed, so a gap is fixed in config, never in code. A row with a **blank Absent Code is dropped** before any of this (no absence to report).

Row multiplicity then comes from the configured `portion` rule, and **the order of the checks matters**:

1. **Tardy first** — if the Absent Code equals the configured `tardy_code` (`T`), the row emits `tardy_rows` (1). This wins *before* the portion is even read, so a tardy recorded against a full-day portion is still ONE row, not two.
2. Otherwise, if `Portion Absent` equals `full_day_value` (`1.0`), emit `full_day_rows` (2) — a full day is two half-days.
3. Otherwise emit `default_rows` (1) — a partial-day absence, or an unparseable portion.

**8-12 period band** (`StudentPeriodAbsences.txt`) — the category is **PASSED THROUGH from the district's own extract**. The GDE column already holds final codes (`A`, `A-E`, `L`, `AD`, `AL`, `OffSite`, `ISS`, …). SpacesEDU aggregates per-day itself using per-entry weights (8-12 = 0.25/entry, 4 entries = one day, capped at 1/day), so we emit one output row per period-absence row — no AM/PM collapse, no derivation, no filtering. Rows with a blank category **or** a blank student number are dropped; nothing else is.

> **This half is district data we do not control.** We cannot promise its vocabulary, and we do not filter it: SpacesEDU is *understood to* ignore non-accepted codes rather than reject the file — **basis: owner knowledge, recorded 2026-06-19; not confirmed against the live importer.** That understanding is why we pass the category through unfiltered, so it is worth re-checking under Q1. Any statement about which period-band codes are *accepted* belongs to the importer, not to us.

### Published references — precedence ladder

Highest authority first. **The live importer always wins.**

| Rank | Reference | Nature |
|---|---|---|
| 1 | **The live SpacesEDU importer** | Source of truth. Unversioned; the owner is the SpacesEDU team and is the only party who can query it. |
| 2 | *Attendance Import for SpacesEDU (BC & Aspen)* — Google Doc `15u_f7Jd91KDkk5ovyg69eaG1QVjXSZBd4tKnaTXzAmo`, v1.0 (2024-09-26) | Partner-facing. MyEdBC/Aspen GDE columns, `DD-MMM-YYYY` dates, category vocabularies, the filename-must-end-with rule. |
| 3 | *Attendance Import for SpacesEDU* (generic) — Google Doc `1s-Ai0OD70A0LJLz_n8QtA1z7cAlxdN4c0xc6yQ4Tgvs` | Partner-facing, SIS-agnostic. 4 required fields; K-7 0.5/entry vs 8-12 0.25/entry weighting. |
| 4 | [SpacesEDU Help Centre — *How do attendance imports work?*](https://help.spacesedu.com/en-ca/article/how-do-attendance-imports-work-4gc9ay/) | **User-facing.** Written for district admins, not as a format spec. Never cite it against a lower-ranked engineering statement. |

### Owner question Q1b — verbatim

The original Q1 asked two things at once — the date format and the category vocabulary. **The date half
(Q1a) was settled by the owner on 2026-07-30: ISO `yyyy-MM-dd` is REQUIRED and `dd-MMM-yyyy` is not
accepted.** The vocabulary half remains open and is restated here on its own, because it is the half with
live consequences:

> **Q1b — attendance category vocabulary: does the live importer IGNORE an unaccepted code, or REJECT the file?**
> The published Docs list the categories `A`, `AD`, `A-E`, `A-E OffSite`, `AL`, `AL-E`, `L`, `L AUTH`, `L-E`. DistrictSync DERIVES only `A`, `A-E`, `L`, `L-E` for the K-7 daily band — that vocabulary is ours to promise — and PASSES THROUGH the district's own codes unfiltered for the 8-12 period band, including values the Docs never list (`OffSite`, `ISS`, …). That pass-through rests on an understanding recorded 2026-06-19 and never confirmed: that SpacesEDU ignores non-accepted codes rather than rejecting the file. Which of the Docs' values does the live importer actually accept today, and what does it do with one it does not — skip the row, or refuse the whole feed?

Until Q1b is answered, every row above that references it stays `pending owner confirmation`. **It is the
first thing to settle in the certification pass**, not the last: if the answer is "refuse the whole feed",
the unfiltered pass-through is a live defect — one unexpected code in one district's extract would kill
that district's entire attendance delivery, silently, on a nightly run.

---

## `CourseInfo` and `StudentCourses` are INTERNAL SPEC

**No partner-facing document exists for the two myBlueprint+ course feeds.** This document is therefore their **authority**, not a mirror of one. The substance is inlined above (full column lists in emitted order, row selection, dedup keys, course-code cleaning) so that **this file remains self-sufficient if the internal pages move or are archived.**

**Provenance** (internal, cited not reproduced): Confluence REQ *"[PathwaysEDU/SpacesEDU] Support for Advanced CSV files"*, page `3761406457`; Confluence SKB *"Imports"*, page `3847160494`.

**Ratifier:** the **owner**. Changes to these two feeds are partner-visible, so an owner decision precedes merge — the same bar as a confirmed row, applied to a spec we write ourselves rather than one we mirror.

### Header-spelling variants — TOLERATED, not contract

A real-world SD22 `courseinfo.csv` sample (file id held in the team's internal Drive; deliberately **not reproduced in this document**, and scrubbed from the plan file that previously carried it — git history still retains that pre-existing occurrence) uses different header spellings from ours:

| Ours (GUARANTEED — what we emit) | SD22 sample variant | Status |
|---|---|---|
| `Course Code` | `CourseCode` | pending owner confirmation |
| `School ID` | `SchoolID` | pending owner confirmation |
| `IntegrationId` | `Integration Id` | pending owner confirmation |

That the importer appears to accept both spellings is **importer leniency — TOLERATED, and explicitly not part of this contract.** We emit the left-hand column, always. No DistrictSync code may depend on the right-hand spellings being accepted.

### Owner question Q2 — verbatim

> **Q2 — CourseInfo/StudentCourses header spellings: which spelling does the live importer canonically expect?**
> The SD22 sample shows `CourseCode` / `SchoolID` / `Integration Id` where DistrictSync emits `Course Code` / `School ID` / `IntegrationId`. Are both accepted by the live importer, and if so which is canonical — i.e. should DistrictSync switch, or is our spelling the one to document as canonical in the internal spec?

---

## Config schema — the other contract

The output CSVs are one contract; the **mapping YAML schema** is the other. Phase 2's mapping creator will generate configs against it, so the compatibility rules are stated here rather than rediscovered later.

### The additive-key rule

**New config keys are added, never repurposed.** A key's meaning is fixed once shipped; a changed meaning needs a new key and a MAJOR config-format bump. This is what lets an older app read a newer config without mis-driving a conversion.

`MappingConfig` (the root model) is `extra="ignore"` — an unknown top-level key is dropped rather than rejected. That is **forward compatibility**: a config carrying a key only a newer build understands still loads and runs on an older build.

**Be precise about how far that leniency reaches** — it is the Pydantic **default everywhere except five leaf models**. `MappingConfig`, `GlobalConfig` and `EntityConfig` all declare no `model_config`, so they inherit `extra="ignore"`. Only `EmailDerivedDate`, `FieldEmailFormat`, `FieldEnrollStatus`, `RowFilter` and `CrossEnrollmentConfig` declare `extra="forbid"`. So a typo in `global_config` or in an entity block is **silently dropped, not rejected** — see the two rows in the matrix below and the `enabled_entities` consequence spelled out there.

### Two-direction compatibility matrix

Version is `<major>.<minor>` as a **quoted string** (`'1.9'`). A bare YAML float is rejected loud, because PyYAML collapses `1.10` to `1.1` and the information is unrecoverable once parsed.

| Config version vs the build's supported version | Behaviour | Rationale |
|---|---|---|
| Same major, **older or equal** minor | Loads silently. | In range. |
| Same major, **newer** minor | Loads, with a loud **WARNING** naming both versions. | Same-major semantics are safe; the config may use features this build ignores. |
| **Different major** (older *or* newer) | **Fails loud** (`ValueError`), naming the supported major. | An out-of-major-range config must never silently drive a conversion. |
| Unknown **top-level** key (`MappingConfig`) | **Ignored.** | Forward compatibility — the Pydantic default, declared deliberately. |
| Unknown key in **`global_config`** or in an **entity block** | **Ignored** (`GlobalConfig` / `EntityConfig` declare no `model_config`, so they inherit `extra="ignore"`). | Forward compatibility again — but note the cost: **typos here are silent.** A mistyped `enabled_entities` (e.g. `enabled_entites:`) is dropped, leaving the field at its `[]` default, and empty `enabled_entities` means **ALL defined mappings are enabled** — so a one-character typo can widen a config's output rather than narrow it. Phase 2's creator should validate key names on the way in rather than rely on the loader. |
| Unknown key in one of the **five leaf models** — `EmailDerivedDate`, `FieldEmailFormat`, `FieldEnrollStatus`, `RowFilter`, `CrossEnrollmentConfig` | **Rejected** (`extra="forbid"`). | These are small closed value objects where a typo is far more likely than a forward-compat key, so typo-catching wins. |

`_base:` inheritance is a recursive deep merge with cycle detection. **Only dicts merge key-by-key; every other value — including lists — REPLACES wholesale.** An override that wants to extend a list must restate the whole list. A user-dir YAML shadows a same-named bundled config entirely (logged at INFO, never silent).

### Versioning convention

The loader's rule is: bump MINOR when the bundled configs start using new same-major features; bump MAJOR only on a breaking config-format change, migrating every bundled config in the same release. **Scoped to ETL-affecting keys** — a purely presentational addition (a display label, a UI-only list) does not require a version bump, because it cannot change what a conversion produces. See `docs/claugentic-DECISIONS.md`.

---

## Expected outputs per config

This table is **hand-written and GATED AGAINST** the enforced contract by `tests/test_output_contract_doc.py` — there is no generator. The **CSVs the contract sweep asserts** column is gated against `tests/contract_schema.EXPECTED_ENTITIES`; the **Entities enabled** column against each config's real `active_entities()`. So the table cannot silently diverge from what the tests enforce, but it is maintained by hand.

<!-- contract-table: expected-outputs -->

| Config | Entities enabled | CSVs the contract sweep asserts | Note |
|---|---|---|---|
| `myedbc` | Students, Staff, Family, Classes, Enrollments | Students, Staff, Family, Classes, Enrollments | The base config every district inherits. |
| `sd40myedbc` | Students, Staff, Family, Classes, Enrollments | Students, Staff, Family, Classes, Enrollments | CSV extracts, headerless schedule, `ATT--*` exclusions. |
| `sd48myedbc` | Students, Staff, Family, Classes, Enrollments | Students, Staff, Family, Classes, Enrollments | Renamed source files. |
| `sd51myedbc` | Students, Staff, Family, Classes, Enrollments, StudentAttendance | Students, Staff, Family, Classes, Enrollments | **Enables StudentAttendance**, but the contract fixture supplies no absence GDEs on purpose — that pins skip-on-empty (a missing attendance drop must never block rostering). In production with absence GDEs present it emits six files. |
| `sd54myedbc` | Students, Staff, Family, Classes, Enrollments | Students, Staff, Family, Classes, Enrollments | No status column: withdraw-date-only active detection. |
| `sd60myedbc` | Students, Staff, Family, Classes, Enrollments | Students, Staff, Family, Classes, Enrollments | Family `row_filters`, cross-enrollment collapse, generated emails. |
| `sd74myedbc` | Students, Staff, Family, Classes, Enrollments | Students, Staff, Family, Classes, Enrollments | The frozen snapshot district. |
| `sd83myedbc` | Students, Staff, Family, Classes, Enrollments, CourseInfo, StudentCourses | Students, Staff, Family, Classes, Enrollments, CourseInfo, StudentCourses | Full myBlueprint+ tier; extended homeroom grades (through 08), `course_start_grade: 9`, Date of Birth withheld. Standard MyEd BC file naming assumed pending real GDE samples. |
| `sd51attendance` | StudentAttendance | StudentAttendance | Attendance-only tier — no roster anchor is a legitimate delivery here. |
| `mbp_all` | Students, Staff, Family, Classes, Enrollments, CourseInfo, StudentCourses | Students, Staff, Family, Classes, Enrollments, CourseInfo, StudentCourses | Full myBlueprint+ tier. |
| `mbp_core` | Students, CourseInfo, StudentCourses | Students, CourseInfo, StudentCourses | Minimal myBlueprint+ tier. |
| `mbponly` | CourseInfo, StudentCourses | CourseInfo, StudentCourses | Course feeds only. |

---

## Divergence register

Where this contract differs from a published reference, and what we intend to do about it.

| # | Divergence | Detail | Action |
|---|---|---|---|
| 1 | Attendance **date format** | We emit ISO `yyyy-MM-dd`; the BC/Aspen Doc documents `DD-MMM-YYYY`, which the live importer **rejects** (owner, 2026-07-30 — Q1a). The Doc is wrong or stale on this point, and it is rank 2 against the importer's rank 1. | **feed back to the published Doc** — CONFIRMED divergence, no longer pending |
| 2 | Attendance **category vocabulary** | The Docs list nine values; our derived K-7 map emits four (`A`, `A-E`, `L`, `L-E`). The 8-12 band passes district values through, including `OffSite` / `ISS`, which are not in the Docs' accepted list. | **pending Q1b** — see the blast-radius note under Open owner questions |
| 3 | **BOM rule undocumented** | No published reference states that the attendance feed must be BOM-free. We learned it from a rejection. | **feed back to the published Doc** |
| 4 | Advanced CSV Doc **scope** | Doc v1.0 covers only the 5 rostering CSVs. The two myBlueprint+ course feeds and StudentAttendance are outside it. | **accepted divergence** — courses are internal spec here; attendance has its own two Docs |
| 5 | Advanced CSV Doc **lags the live importer** | Owner statement (2026-07-27): the live importer is authoritative and the Doc lags slightly as functionality has evolved. `EnrollStatus` and `SchoolCode` as emitted are confirmed correct. | **feed back to the published Doc** |
| 6 | Course-file **header spellings** | SD22 sample uses `CourseCode` / `SchoolID` / `Integration Id`; we emit `Course Code` / `School ID` / `IntegrationId`. Importer leniency is TOLERATED, not contract. | **pending Q2** |
| 7 | **Line endings** | RFC-4180 specifies CRLF. We emit `os.linesep`: CRLF on Windows, LF on the Linux/macOS artifacts. | **pending Q3**, then either pin `lineterminator` (roadmap) or record as accepted divergence |

---

## Open owner questions

**Q1a is SETTLED (owner, 2026-07-30).** Three remain `pending owner confirmation` — an accepted landing
state, a **status field, not debt**. Each is stated verbatim beside the rows it governs; collected here
with the exact check that would settle it, so the certification pass is a short bench test rather than a
research task. **Ordered by blast radius, not by number.**

> **✅ Q1a — attendance date format. SETTLED: ISO `yyyy-MM-dd` is REQUIRED.**
> `dd-MMM-yyyy` — the shape the published BC/Aspen Doc documents — is **not accepted** by the live
> importer. Confirmed by the owner on 2026-07-30, corroborated by the 2026-06-22 observed rejection.
> Consequences already applied above: the default row and the base config comment are confirmed, the
> "still accepted" row is REFUTED, divergence register row 1 is confirmed (the Doc needs correcting),
> and the `attendance.date_format` footgun is recorded in the ROADMAP.

> **Q1b — attendance category vocabulary: does the live importer IGNORE an unaccepted code, or REJECT the file?**
> The published Docs list the categories `A`, `AD`, `A-E`, `A-E OffSite`, `AL`, `AL-E`, `L`, `L AUTH`, `L-E`. DistrictSync DERIVES only `A`, `A-E`, `L`, `L-E` for the K-7 daily band — that vocabulary is ours to promise — and PASSES THROUGH the district's own codes unfiltered for the 8-12 period band, including values the Docs never list (`OffSite`, `ISS`, …). That pass-through rests on an understanding recorded 2026-06-19 and never confirmed: that SpacesEDU ignores non-accepted codes rather than rejecting the file. Which of the Docs' values does the live importer actually accept today, and what does it do with one it does not — skip the row, or refuse the whole feed?

**🔴 Settle Q1b FIRST.** Everything else here is a documentation question; this one is a live correctness
question with district-wide blast radius. *The check:* import a `StudentAttendance.csv` carrying one row
with a category outside the Docs' nine (e.g. `ISS`) alongside several valid rows. Does the importer
(a) accept the file and skip that row, (b) accept the file and import the row, or (c) reject the whole
file? Then repeat for the Docs' values we never emit (`AD`, `AL`, `A-E OffSite`, `AL-E`, `L AUTH`) to
learn which are genuinely live. **If the answer is (c), this is a defect rather than a doc gap** — the
unfiltered pass-through needs a filter or a build-time loud failure, ahead of release.

> **Q2 — CourseInfo/StudentCourses header spellings: which spelling does the live importer canonically expect?**
> The SD22 sample shows `CourseCode` / `SchoolID` / `Integration Id` where DistrictSync emits `Course Code` / `School ID` / `IntegrationId`. Are both accepted by the live importer, and if so which is canonical — i.e. should DistrictSync switch, or is our spelling the one to document as canonical in the internal spec?

*The check:* import one `CourseInfo.csv` with our spelling and one with the SD22 spelling; note which
import cleanly. **Lowest urgency of the three** — it only forces a change if we decide to switch, and
switching is a partner-visible output change needing its own snapshot-gated slice.

> **Q3 — line-ending tolerance: we emit CRLF on Windows and LF on the Mac/Linux artifacts — does the importer care?**
> If it does not, we can leave `os.linesep` and record an accepted divergence. If it does, `DataLoader._write_csv` needs a `lineterminator` pin, which changes emitted bytes on two of the three build platforms and therefore needs its own snapshot-gated slice.

*The check:* import the same file twice, once CRLF-terminated and once LF-terminated. **Low urgency in
practice** — districts run the Windows exe, so CRLF is what actually ships today, and the LF shape is
only reachable from an artifact nobody currently delivers from.

---

## Related

- `tests/contract_schema.py` — the machine-readable mirror of the tables above.
- `tests/test_contract.py` — the 11-config sweep that enforces it end-to-end.
- `tests/test_output_contract_doc.py` — the drift gate between this document and that data.
- `src/etl/loader.py` — `csv_filename`, `csv_encoding`, `select_ordered`, the atomic write.
- `src/sftp/uploader.py` — `build_zip_name`, the manifest, the standalone-attendance rule.
- `config/mappings/myedbc_mapping.yaml` — the base `field_map`s that *are* the column order.
- `docs/claugentic-DECISIONS.md` — the dated incidents this document cites.
- `docs/developer/adding-transformer.md` — the checklist for adding a new output entity.
