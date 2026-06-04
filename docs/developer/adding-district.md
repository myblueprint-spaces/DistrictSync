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
(it is also exposed in the Mapping Editor's Calendar step when CourseInfo or
StudentCourses is enabled). MyEd BC encodes the grade in the course code, so
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

The Mapping Editor wizard currently configures the 5 standard entities only.
To opt a district into the myBlueprint+ tier, use one of the tier configs
above or edit YAML directly.

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
| `sd74myedbc` | `myedbc` | Student Course Selection, Staff Information, Parent Information, Class Info Enhanced |
| `mbp_all` | `myedbc` | Tier override (full myBlueprint+) — enables CourseInfo + StudentCourses in addition to the 5 rostering CSVs |
| `mbp_core` | `myedbc` | Tier override (minimal myBlueprint+) — enables only Students + CourseInfo + StudentCourses |
