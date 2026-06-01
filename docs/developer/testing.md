# Testing

---

## Running tests

```bash
# All tests
python -m pytest tests/ -v

# With coverage (enforces 80% minimum)
python -m pytest tests/ --cov=src --cov-report=term-missing --cov-fail-under=80

# Single test file
python -m pytest tests/test_transform_students.py -v

# Single test by name
python -m pytest tests/ -k "test_active_only_filter" -v
```

---

## Test layout

| File | What it covers |
|------|----------------|
| `test_transform_students.py` | StudentTransformer — active filter, grade mapping, email generation |
| `test_transform_staff.py` | StaffTransformer — roster join, role mapping, deduplication |
| `test_transform_family.py` | FamilyTransformer — contact extraction, missing email handling |
| `test_transform_classes.py` | ClassTransformer — homeroom generation, subject class joins |
| `test_transform_enrollments.py` | EnrollmentTransformer — student + teacher enrollment rows |
| `test_blended_classes.py` | BlendedClassService — same teacher/time/grade detection |
| `test_grade_mapping.py` | `grade_to_ceds()` — all grade code variants |
| `test_email_generation.py` | `generate_student_email()` — format string substitution |
| `test_class_generation.py` | `generate_class_name()`, `generate_class_id()` |
| `test_enrollment_status.py` | Active/inactive/pre-reg status filtering |
| `test_role_mapping.py` | `map_role()` — Y/N → teacher/administrator |
| `test_school_year.py` | `determine_school_year()` — data-derived and date-fallback |
| `test_source_config.py` | `normalize_source_config()` — dict/list/list-of-dict formats |
| `test_extractor.py` | DataExtractor — encoding fallback, delimiter detection |
| `test_loader.py` | DataLoader — transactional write, atomic commit, rollback on failure |
| `test_config.py` | YAML loading, Pydantic validation, inheritance, cycle detection |
| `test_registry.py` | Registry — correct transformer returned, DefaultTransformer fallback |
| `test_quality_report.py` | DataQualityReport — missing fields, duplicates, orphan detection |
| `test_pipeline_e2e.py` | Full pipeline — all 5 entities, dry-run, diff, quality flags |
| `test_pipeline_e2e_districts.py` | SD48 + SD74 full pipeline with district-specific file naming |
| `test_cli.py` | CLI flags — `--dry-run`, `--diff`, `--quality`, transactional write |
| `test_benchmarks.py` | Performance benchmarks — excluded from CI, run manually |

---

## Conventions

### No file I/O in unit tests

Unit tests create DataFrames directly. They do not write or read `.txt` / `.csv` files. Use `tmp_path` (pytest built-in) only in integration / E2E tests.

```python
# Good
df = pd.DataFrame({"student number": ["1001"], "grade": ["10"]})
result = StudentTransformer().transform(df, mapping, context)

# Avoid in unit tests
df = pd.read_csv("tests/fixtures/students.txt")
```

### Fixtures in conftest.py

Shared fixtures (base mappings, standard DataFrames, a default `TransformContext`) live in `tests/conftest.py`. Import them by name in any test file without explicit imports — pytest discovers them automatically.

### School year mocking

`BaseTransformer.determine_school_year()` falls back to `datetime.now()` when no school year data is in the source files. Mock it in tests that need a deterministic year:

```python
from unittest.mock import patch

def test_school_year_fallback():
    with patch("src.etl.transformers.base.datetime") as mock_dt:
        mock_dt.now.return_value.year = 2025
        mock_dt.now.return_value.month = 9   # September → school year 2025
        year = BaseTransformer().determine_school_year({}, {})
    assert year == 2025
```

### TransformContext construction

```python
from src.etl.transformers.context import TransformContext

context = TransformContext(
    raw_data={"StudentSchedule.txt": schedule_df, "CourseInformation.txt": course_df},
    school_year=2025,
    academic_start="2025-08-25",
    academic_end="2026-07-25",
    students_output=None,   # set to a DataFrame if the entity under test needs it
)
```

### Testing config inheritance

```python
from src.config.loader import load_config
from pathlib import Path

def test_sd48_inherits_base(tmp_path):
    # Write minimal base config
    (tmp_path / "myedbc_mapping.yaml").write_text("...")
    (tmp_path / "sd48myedbc_mapping.yaml").write_text("_base: myedbc\n...")
    cfg = load_config("sd48myedbc", config_dir=tmp_path)
    assert cfg.sis == "MyEducationBC"
```

Always pass `config_dir=tmp_path` in config tests so they don't read from `config/mappings/`.

---

## Coverage configuration

`pyproject.toml` omits certain modules from coverage:

```toml
[tool.coverage.run]
omit = [
    "src/utils/logger.py",   # logging configuration only
    "src/ui/*",              # Streamlit UI — not unit-testable
    "src/ui/launcher.py",
]
```

---

## Benchmarks

`tests/test_benchmarks.py` uses `pytest-benchmark` with a 5,000-row synthetic dataset. These tests are excluded from CI via the `not benchmark` marker:

```toml
# pyproject.toml
[tool.pytest.ini_options]
addopts = "-m 'not benchmark'"
```

Run manually when profiling:

```bash
python -m pytest tests/test_benchmarks.py -v --benchmark-only
```

---

## CI matrix

CI runs on Python 3.9, 3.11, and 3.13 on `ubuntu-latest` (see `.github/workflows/ci.yml`). All tests must pass on all three versions before a PR can be merged.

CI also runs the following quality gates on each push:

| Step | Command |
|------|---------|
| Format check | `ruff format --check src/ tests/` |
| Type check | `mypy src/ --exclude 'src/ui'` (UI pages excluded) |
| Security scan | `bandit -r src/ -q` |
| Config validation | `make validate-config` (all district + tier YAML configs) |

!!! note "Testing district configs with non-standard filenames"
    When writing E2E tests for district configs that use non-standard filenames (e.g., SD40's CSV files with SD-40_ prefix), create fixture files in `tmp_path` using the exact filenames the district config expects. See `tests/test_pipeline_e2e_districts.py` for examples.
