# Adding a District Config

This guide explains how to add support for a new school district that exports MyEdBC GDE files with non-standard filenames or column names.

---

## Background

Most districts export GDE files with the standard naming (`StudentDemographicInformation.txt`, `StaffInformationEnhanced.txt`, etc.) and identical column names. For those districts, the base `myedbc` config works as-is.

Some districts have differences:

| District | Difference |
|----------|-----------|
| SD40 – New Westminster | GDE files are CSV format with SD-40_/SD40- prefix; Student Schedule has no headers (injected via `headers:` config) |
| SD48 – Sea to Sky | Uses Student Demographic Enhanced and Staff Information (non-enhanced) |
| SD74 – Gold Trail | Uses Student Course Selection, Staff Information, Parent Information, Class Info Enhanced |

For each such district, you create a small YAML override file that inherits from `myedbc` and specifies only what differs.

---

## Step 1 — Collect the district's GDE files

Obtain a sample export from the district and note:

1. **Filenames** — which `.txt` files are present and do any differ from the standard names?
2. **Column names** — open each file and compare column headers against the base config.

Standard file set (from `myedbc_mapping.yaml`):

```
Student Demographic Information
Staff Information Enhanced
Student Schedule
Course Information
Emergency Contact Information
```

(Exact filenames vary by district and may be `.txt` or `.csv`.)

---

## Step 2 — Check what the base config expects

Run a dry-run against the district's files using the base config to see what breaks:

```bash
python -m src.main --sis myedbc \
  --input /path/to/district-gde-files \
  --output data/output \
  --dry-run
```

Examine the log for `WARNING` lines like:

```
WARNING - Primary source file 'StaffInformationEnhanced.txt' is empty for 'Staff'; skipping.
```

(The filename in the warning reflects what the config expects — if it mismatches the actual file, update the `source_files` in your override YAML.)

These tell you which files are named differently.

Also run with `--quality` after a successful (or partial) run to check column mapping issues.

---

## Step 3 — Create the override YAML

Create `config/mappings/sd99myedbc_mapping.yaml` (replace `sd99` with the district's SD number):

```yaml
# SD99 – Example District
# Inherits from standard MyEducation BC mapping.
# Differences: different staff filename only.
_base: myedbc
version: 1.0
sis: MyEducationBC

mappings:
  Staff:
    source_files:
      staff_info: "StaffInformation.txt"

  Classes:
    source_files:
      staff_info: "StaffInformation.txt"
```

Only include what differs. The `_base: myedbc` key triggers deep-merge inheritance — everything not listed here is inherited from `myedbc_mapping.yaml` unchanged.

### Overriding column names

If the district uses different column names (e.g., `"Surname"` instead of `"Last Name"`):

```yaml
mappings:
  Family:
    source_files:
      emergency_contact: "EmergencyContactInformation.txt"
    field_map:
      Last Name:
        column: "surname"    # district uses "surname" instead of "last name"
```

Remember: the extractor normalises column names to lowercase + stripped, so YAML values should be lowercase.

### Handling headerless files

Some districts export GDE files with no header row (SD40's Student Schedule is an example). Use the `headers:` key to inject column names:

```yaml
mappings:
  Classes:
    source_files:
      student_schedule: "SD40_StudentSchedule.csv"
    file_headers:
      student_schedule:
        - "Student Number"
        - "Course Code"
        - "Section"
        - "Teacher ID"
        - "School Number"
        # ... all columns in order
```

When `file_headers` is present for a source file role, the extractor uses these names instead of reading a header row. The values must match what the downstream field_map expects (after lowercase normalization).

### Overriding global config

If the district uses non-standard academic dates or homeroom grades:

```yaml
global_config:
  academic_start_month_day: "09-01"
  academic_end_month_day: "06-30"
```

### Scoping which grades get class rostering (`class_rostering_grades`)

By default **every** grade is rostered: `homeroom_grades` get a homeroom class,
and every remaining grade gets subject (timetable) classes from the schedule.
A district licensed for only part of its grade range can opt into a scope:

```yaml
global_config:
  # The COMPLETE set of grades that receive class rostering, in CEDS output
  # codes (IT/PR/PK/TK/KG/01…13/PS/UG/Other) — NOT raw MyEd values like "K"/"3".
  class_rostering_grades: ["07", "08", "09", "10", "11", "12"]
  homeroom_grades: ["07", "08", "09"]   # must be a SUBSET of the list above
```

- **homeroom classes** still follow `homeroom_grades`;
- **subject classes** are restricted to `class_rostering_grades − homeroom_grades`;
- the **blend rule below** narrows with them: a blended class is kept only if at
  least one of its pupils' grades is in that difference;
- a grade in **neither** set gets **no class and no enrollment at all**.

**The blend rule is general, not conditional on this key.** For EVERY district,
scoped or not, a blended class is emitted only when at least one of its pupils'
grades is on the timetable side — the configured scope when there is one, else
"every grade that is not a homeroom grade". A blend all of whose schedule rows
sit on the homeroom side could only ever be delivered with a teacher and no
students (each of those pupils is rostered through their homeroom instead), so
it is dropped, together with its teacher enrollment row. Setting
`class_rostering_grades` does not turn the rule ON; it makes the timetable side
narrower. Two things it deliberately does NOT do: it does not check whether
those pupils are ACTIVE (a blend whose in-scope pupils have all withdrawn still
ships studentless), and it does not change a blend's NAME, which is still built
from each section's most-common grade — so a surviving blend can name grades
other than its occupants'. Both are tracked residuals.

`class_rostering_grades: "homeroom"` is shorthand for "roster exactly the
homeroom grades" (an empty subject scope), so the grade list is written once and
the two keys cannot drift. That is SD83's shape: K-8 class rostering, while
grades 9-12 stay on `Students.csv` so their myBlueprint+ transcripts still work.

**`StudentSchedule.txt` and `ClassInformationEnh.txt` are genuinely optional
under this shorthand** (fixed 2026-08-17). With the subject scope empty, neither
file contributes any surviving class or enrollment — every blend gets
unconditionally suppressed and no timetable class is ever built. Before the fix,
omitting `StudentSchedule.txt` would also have deleted the district's K-8
**homeroom** enrollments, since `EnrollmentTransformer` used to gate its entire
output on that file being non-empty; homeroom enrollments never actually read
schedule data, so that was always a bug, not a rule to design around. A district
can still supply both files if it has them — the 9-12 rows are simply ignored —
but it no longer has to.

Points worth stating to the district before you ship it:

- **Students are NOT filtered by this key.** An unrostered grade still appears in
  `Students.csv` (and in the course feeds) with **no enrollment rows** — that is
  valid, intended output, and for SD83 it is the whole point.
- **`Staff.csv` is never filtered** — a teacher of an unrostered grade still ships.
- **Blank or unrecognised grades map to `UG`.** Today they land on the subject
  side and get a class; under a scope they get **nothing** unless you list `"UG"`
  explicitly. The run logs the count, but it is your call.
- **The first run after adopting it will trip the >20% drop anomaly** on Classes
  and Enrollments. That is the expected degradation, not a fault — say so in the
  rollout note so nobody "fixes" it.
- Getting it wrong fails **at config load, before any conversion runs**: a
  non-CEDS code, an empty list, the sentinel over an empty `homeroom_grades`, or a
  `homeroom_grades` that is not a subset are each rejected with a message naming
  the offending values. Verify with `make validate-config`.

### Scoping which grades of STUDENTS you send (`student_rostering_grades`)

`class_rostering_grades` above scopes *classes*; this key scopes the **students
themselves**. It is the outer boundary of the whole delivery — for a district
licensed for only part of its grade range, this is the key that matters.

```yaml
version: '1.11'          # QUOTED — see the version note below
global_config:
  # The COMPLETE set of grades whose students are sent, in CEDS output codes.
  student_rostering_grades: ["IT", "PR", "PK", "TK", "KG", "01", "02", "03",
                             "04", "05", "06", "07", "08"]
```

**No shipped district sets this today** — it exists for the first licensing
district that needs it. What it does:

- only students whose grade is listed reach `Students.csv`; the active-status
  filter still applies **on top** (the two are ANDed);
- **the excluded grades lose their guardians and their transcripts too**, not
  just their classes — `Family.csv` rows and `StudentCourses.csv` rows go with
  the students, because every student-bearing feed is filtered against the
  delivered roster. For a licensing district that is the point; it is the exact
  **opposite** of SD83's shape above, where 9-12 deliberately stay on the roster
  so their myBlueprint+ transcripts keep working. Read the two together and be
  sure which one you want;
- if you set this and say nothing about `class_rostering_grades`, class rostering
  automatically stays **inside** this boundary — the timetable side becomes
  `student_rostering_grades − homeroom_grades`, which narrows the general blend
  rule above with it — so you can never class-roster a grade whose students you
  are not delivering;
- `Staff.csv` is still never filtered — a teacher of an excluded grade ships.

Three things that will bite you if nobody says them first:

- **You almost certainly have to restate `homeroom_grades`.** Config inheritance
  REPLACES lists rather than merging them, so your config inherits base
  `myedbc`'s twelve homeroom codes (`IT`…`07`) whether you mention them or not —
  and the validated rule is `homeroom_grades` ⊆ `class_rostering_grades` ⊆
  `student_rostering_grades`. A K-8 list happens to contain all twelve, so it
  passes. The **mirror shape — SpacesEDU for grades 9-12 only — does not**, and
  fails at config load until you also state which homerooms you want:

  ```yaml
  global_config:
    homeroom_grades: []                                   # no homeroom classes
    student_rostering_grades: ["09", "10", "11", "12"]
  ```

  The error names both lists and which one to edit, so it is a five-second fix —
  but only if you were expecting it.

- **Declare `version: '1.11'`, quoted.** This key needs format version 1.11.
  Quote it: bare YAML `1.11` is read as the number 1.11 and rejected. An OLDER
  DistrictSync exe reading your config will log a warning and then **run anyway**
  — which for this key means delivering the grades you excluded. The version
  declaration makes the mismatch visible; it does not prevent it. If a district
  server runs a pinned older exe, upgrade it before shipping this config.

- **Blank or unrecognised grades map to `UG` and leave the delivery entirely.**
  Worse than under `class_rostering_grades`, where such a student only lost a
  class. List `"UG"` unless you are certain every student has a clean grade
  value. The run logs the kept/total count so you can check.

Also worth knowing: `CourseInfo.csv` is a course CATALOG, not student data, so it
is **not** narrowed — a K-8 district running myBlueprint+ will ship a catalog
containing courses no delivered student takes. Harmless, but expect the question.
And the first run after adopting the key trips the >20% drop anomaly on every
affected file — expected degradation, not a fault.

A list that matches **no** student in that night's export does not deliver a
broken roster: the run fails with nothing written and the previous output
untouched (exit code 1). That is deliberate — the alternative is shipping
enrolments for students who are not in `Students.csv`.

### School year naming convention (non-BC districts)

The pipeline internally uses **end-year semantics**: ``school_year = 2026``
means the academic year ending in 2026 (2025-2026). This matches the MyEd BC
"School Year" column convention.

For districts whose source files use **start-year semantics** instead — e.g.
Ontario or many US SIS exports where a bare ``2025`` means the academic year
**starting** in 2025 — set ``school_year_naming: start`` in the
``global_config``:

```yaml
global_config:
  school_year_naming: start  # bare 'YYYY' in the source = academic year STARTING in YYYY
```

The parser translates start-year values to end-year by adding 1 before use,
so all downstream behavior (class ID suffix, academic_start, academic_end)
remains consistent.

Range formats like ``2025/2026`` or ``2025-2026`` are unambiguous and ignore
this setting — the second year is always taken as the end. Default is
``end`` (BC / MyEd BC).

### School year fallback rollover

When no source file has a ``school year`` column, the pipeline falls back to
the system date. The rollover month-day controls when "today" should be
treated as belonging to the **next** academic year (rather than the current
one). Default ``07-25`` means anything from July 25 onwards rolls forward.
Districts that upload upcoming-year exports earlier can lower it:

```yaml
global_config:
  academic_year_rollover_month_day: "07-01"  # July onwards = next academic year
```

### Opting into CourseInfo / StudentCourses (myBlueprint+ tier)

The `CourseInfo` and `StudentCourses` entity templates live in the base
`myedbc_mapping.yaml`, but the base config does not enable them by default
— its `enabled_entities` lists only the 5 rostering entities. To produce
these CSVs, use (or inherit from) one of the myBlueprint+ tier configs:

| Config | What it produces |
|---|---|
| `mbp_all` | 5 rostering CSVs + `CourseInfo.csv` + `StudentCourses.csv` (full tier) |
| `mbp_core` | `Students.csv` + `CourseInfo.csv` + `StudentCourses.csv` only (minimal tier) |

Both are thin overrides that inherit MyEd BC file naming from `myedbc` and
just override `enabled_entities`:

```yaml
# config/mappings/mbp_all_mapping.yaml
_base: myedbc
sis: MyEducationBC
district_name: myBlueprint+ (full)

global_config:
  enabled_entities:
    - Students
    - Staff
    - Family
    - Classes
    - Enrollments
    - CourseInfo
    - StudentCourses
```

The MyEd BC exclusion patterns and course-code flavor suffixes are defined
in the base config and inherited automatically:

```yaml
# config/mappings/myedbc_mapping.yaml (base)
global_config:
  # Lowest grade included in the CourseInfo + StudentCourses CSVs. Default 10
  # (grades 10-12). Set to 8 or 9 to also include those grade levels — never
  # lower. The numeric early-grade exclusion regex is derived from this value,
  # so it is no longer listed as a literal pattern below.
  course_start_grade: 10
  excluded_course_code_patterns:
    - "^.{5}-K"      # kindergarten variants
    - "^X"           # X-prefix courses
    - "^ATT"         # attendance bookkeeping
  excluded_course_flavors: [HUB, HOL, DL, "---"]
```

`course_start_grade` is the editable knob for the senior-course grade floor
(set it in the district's mapping YAML when CourseInfo or StudentCourses is
enabled). MyEd BC encodes the grade in the course code, so
the transformer turns this value into an early-grade exclusion pattern
(`^.{5}0[0-9]` for 10, `^.{5}0[0-8]` for 9, `^.{5}0[0-7]` for 8).

### Combining district file naming with myBlueprint+ tier

For a real district that has both non-standard file naming AND wants the
myBlueprint+ CSVs, create a child config that inherits the district config
and just overrides `enabled_entities`:

```yaml
# config/mappings/sd48_mybplus_mapping.yaml
_base: sd48myedbc
district_name: SD48 + myBlueprint+

global_config:
  enabled_entities:
    - Students
    - Staff
    - Family
    - Classes
    - Enrollments
    - CourseInfo
    - StudentCourses
```

District mapping YAML configures the 5 standard entities by default. To opt a
district into the myBlueprint+ tier, use one of the tier configs above or add
`CourseInfo`/`StudentCourses` to the district's `enabled_entities`.

---

## Step 4 — Validate the new config

```bash
make validate-config
```

This runs `src/config/loader.py` against all YAML files in `config/mappings/`. If validation fails, the error message will include the specific field and the problem.

You can also validate directly:

```bash
python -c "
from src.config.loader import load_config
cfg = load_config('sd99myedbc')
print('OK:', cfg.sis, cfg.version)
"
```

---

## Step 5 — Add to the CI validation list

Open `Makefile` and add the new config to the `validate-config` target:

```makefile
validate-config:
	python -c "from src.config.loader import load_config; load_config('myedbc')"
	python -c "from src.config.loader import load_config; load_config('sd48myedbc')"
	python -c "from src.config.loader import load_config; load_config('sd51myedbc')"
	python -c "from src.config.loader import load_config; load_config('sd74myedbc')"
	python -c "from src.config.loader import load_config; load_config('sd99myedbc')"   # add this
	@echo "All configs valid."
```

Also add the config name to the `validate-config` step in `.github/workflows/ci.yml` so it runs in CI on every pull request:

```yaml
- name: Validate configs
  run: |
    python -c "from src.config.loader import load_config; load_config('myedbc')"
    python -c "from src.config.loader import load_config; load_config('sd99myedbc')"  # add this
```

---

## Step 6 — Add an E2E test

Add a test class to `tests/test_pipeline_e2e_districts.py`:

```python
class TestSD99Pipeline:
    """SD99 uses StaffInformation.txt instead of StaffInformationEnhanced.txt."""

    @pytest.fixture
    def sd99_input_dir(self, tmp_path):
        # Create minimal GDE files with correct SD99 naming
        demo_data = {
            "Student Number": ["1001"],
            "Legal Surname": ["Smith"],
            "Legal Given Name": ["Alice"],
            "Grade": ["10"],
            "School Year": ["2025"],
            "Enrolment Status": ["Active"],
        }
        pd.DataFrame(demo_data).to_csv(
            tmp_path / "StudentDemographicInformation.txt", index=False
        )

        staff_data = {
            "Staff ID": ["T01"],
            "Surname": ["Jones"],
            "Given Name": ["Bob"],
            "Teaching Staff": ["Y"],
            "School Number": ["99001"],
        }
        pd.DataFrame(staff_data).to_csv(
            tmp_path / "StaffInformation.txt", index=False   # SD99 file name
        )
        # ... create remaining required files ...
        return tmp_path

    def test_sd99_pipeline_completes(self, sd99_input_dir, tmp_path):
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        run_pipeline("sd99myedbc", str(sd99_input_dir), str(output_dir), dry_run=True)
```

---

## Step 7 — Test with real data

Once you have the district's actual GDE files:

```bash
python -m src.main --sis sd99myedbc \
  --input /path/to/real-gde-files \
  --output data/output \
  --dry-run
```

Then without `--dry-run` to verify the output CSVs, and with `--quality` to spot any mapping gaps.

---

## District config reference

| Config name | `_base` | Purpose |
|-------------|---------|---------|
| `myedbc` | (none — base) | Standard MyEdBC filenames; defines all 7 entity templates; enables the 5 rostering entities by default |
| `sd40myedbc` | `myedbc` | CSV files with SD-40_/SD40- prefix; Student Schedule is headerless (`file_headers:` used) |
| `sd48myedbc` | `myedbc` | Student Demographic Enhanced, Staff Information (non-enhanced) |
| `sd51myedbc` | `myedbc` | Contact SpacesEDU for file naming details |
| `sd54myedbc` | `myedbc` | Bulkley Valley — lowercase filenames; Staff non-Enhanced; Emergency Contact + Class Info Enhanced; ATT--AM/PM/Daily excluded |
| `sd60myedbc` | `myedbc` | Peace River North — Family `row_filters` (guardians-only); opt-in `cross_enrollment.collapse` home-school dedupe for dual-school students; ATT--AM/PM excluded |
| `sd74myedbc` | `myedbc` | Student Course Selection, Staff Information, Parent Information, Class Info Enhanced |
| `sd83myedbc` | `myedbc` | North Okanagan-Shuswap — full myBlueprint+ tier (7 entities); `homeroom_grades` through 08; `class_rostering_grades: "homeroom"` (K-8 class rostering only; 9-12 stay on the roster for their transcripts); `course_start_grade: 9`; Date of Birth withheld; standard file naming assumed (no real GDE samples yet) |
| `mbp_all` | `myedbc` | Tier override (full myBlueprint+) — enables CourseInfo + StudentCourses in addition to the 5 rostering CSVs |
| `mbp_core` | `myedbc` | Tier override (minimal myBlueprint+) — enables only Students + CourseInfo + StudentCourses |
