# Adding a District Config

This guide explains how to add support for a new school district that exports MyEdBC GDE files with non-standard filenames or column names.

---

## Background

Most districts export GDE files with the standard naming (`StudentDemographicInformation.txt`, `StaffInformationEnhanced.txt`, etc.) and identical column names. For those districts, the base `myedbc` config works as-is.

Some districts have differences:

| District | Difference |
|----------|-----------|
| SD48 – Sea to Sky | Uses `StudentDemographicEnhanced.txt` and `StaffInformation.txt` |
| SD74 – Gold Trail | Uses `studentcourseselection.txt`, `StaffInformation.txt`, `ParentInformation.txt`, `ClassInfoEnhanced.txt` |

For each such district, you create a small YAML override file that inherits from `myedbc` and specifies only what differs.

---

## Step 1 — Collect the district's GDE files

Obtain a sample export from the district and note:

1. **Filenames** — which `.txt` files are present and do any differ from the standard names?
2. **Column names** — open each file and compare column headers against the base config.

Standard file set (from `myedbc_mapping.yaml`):

```
StudentDemographicInformation.txt
StaffInformationEnhanced.txt
StudentSchedule.txt
CourseInformation.txt
EmergencyContactInformation.txt
```

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

### Overriding global config

If the district uses non-standard academic dates or homeroom grades:

```yaml
global_config:
  academic_start_month_day: "09-01"
  academic_end_month_day: "06-30"
```

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

| Config name | `_base` | Key differences |
|-------------|---------|----------------|
| `myedbc` | (none — base) | Standard MyEdBC filenames |
| `sd48myedbc` | `myedbc` | `StudentDemographicEnhanced.txt`, `StaffInformation.txt` |
| `sd51myedbc` | `myedbc` | Contact SpacesEDU for file naming details |
| `sd74myedbc` | `myedbc` | `studentcourseselection.txt`, `StaffInformation.txt`, `ParentInformation.txt`, `ClassInfoEnhanced.txt` |
