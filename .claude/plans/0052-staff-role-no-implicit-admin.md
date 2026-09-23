# Plan 0052 — a teaching flag of "N" must stop meaning "administrator"

**Status:** approved by the owner in-conversation 2026-09-22 (twice).
**Trigger:** Unity Christian, live. Matt Zacharias (Network Administrator),
2026-09-22: *"I uncovered an issue in the background of SPACES. All EAs have
been added as admins. This is problematic."* The district's import is on hold.

## The defect

`BaseTransformer.map_role` maps exactly `"y"` → `teacher` and **everything
else** → `administrator`. `administrator` is a real privilege level in
SpacesEDU. 15 of the 16 bundled configs that emit `Staff.csv` inherit this
from `myedbc_mapping.yaml`; SD83 is the only config that overrides it
(`Prefix` → `normalize_staff_role`).

The flag answers "does this person teach", not "is this person an
administrator". A secretary, an EA and a principal are all `N`.

## Measured blast radius (real district drops, counts only)

| District | ships today | flagged `N` → **administrator** | sections whose ONLY teacher is one of them |
|---|---|---|---|
| SD40 (2026-09-10) | 1,127 | 506 (44.9%) | 0 |
| SD51 (2026-09-18) | 207 | 103 (49.8%) | 26 of 566 |
| SD60 (2026-09-18) | 86 | 16 (18.6%) | 1 of 673 |
| SD74 (2026-09-21) | 150 | 74 (49.3%) | 22 of 694 |
| Unity Christian (2026-09-14) | 94 (of 306; 212 already dropped as departed) | **56 (60%)** | **68 of 186** |

**No district is shape "teachers + admins only".** Every export measured
carries the whole staff list.

## Why a bare drop is NOT shippable

Dropping every non-`Y` row would strand 68 of Unity's 186 sections (37%) with
no teacher — attributable to **3 people who are teacher-of-record on 26, 26
and 16 sections each**. Someone teaching 26 sections is a teacher; MyEd's flag
is simply wrong for them. Those rows resolve today only because the blanket
puts them in `Staff.csv` as administrators.

## The rule

> A staff member ships as **`teacher`** when the teaching flag says so **or**
> they are teacher-of-record on a section in this run's timetable. A row with
> no publishable role does not ship at all. `administrator` is emitted **only**
> where the district's export STATES it.

One rule; no role is ever inferred from absence.

## Changes

1. `base.py` — `map_role` returns `NO_STAFF_ROLE` (`""`), never `administrator`.
2. `context.py` — `teacher_of_record_ids()`: union of the teacher-id column
   across the `student_schedule` + `class_info` roles of the **Classes**
   entity's `source_files` (config-driven; the established
   `get_teacher_id_col` / `get_demo_student_col` cross-entity pattern).
   Deliberately NOT `staff_info` — that file lists everyone.
3. `staff.py` — `resolve_staff_roles()` after the field map: rescue unroled
   rows that hold a section, drop the rest. Counts-only logging.
4. Tests: re-point the ~10 assertions pinning `administrator`; add positive
   twins for rescue and drop.
5. Docs: output contract Role row, ROADMAP entry closed, DECISIONS, CHANGELOG.

## Not in scope

- A `staff_role` config block with a required `unmatched:` key, and the
  mapping-intake question "how is an administrator identified in your export?"
  (follow-up; needs district answers first).
- The SpacesEDU-side Import Admin audit and ACSV-028 severity — roster-validation.

## Verification

- SD74 snapshot golden does **not** move: `tests/snapshots/input/StaffInformation.txt`
  is 10 rows, all `Y`; the golden `Staff.csv` is 10 teachers, 0 administrators.
  The branch was never exercised by the fixture.
- SD83 is unaffected: its `row_filters` keeps only `Teacher`/`Administrator`
  rows, so `normalize_staff_role` never blanks a cell.
