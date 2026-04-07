# Adding a Transformer

This guide walks through adding a transformer for a new entity type (e.g., "Courses").

---

## When you need a custom transformer

If the entity only needs YAML field mappings with no custom logic, `DefaultTransformer` handles it automatically — no code required. Add a custom transformer when you need:

- Custom join logic (multiple source files)
- Filtering rows (e.g., active-only)
- Derived columns (e.g., blended class detection, email generation)
- Deduplication

---

## Step 1 — Create the transformer class

Create `src/etl/transformers/courses.py`:

```python
from typing import Any

import pandas as pd

from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.context import TransformContext


class CourseTransformer(BaseTransformer):

    def transform(
        self,
        df: pd.DataFrame,
        mapping: dict[str, Any],
        context: TransformContext,
    ) -> pd.DataFrame:
        working = self.normalize_columns(df)
        field_map = mapping.get("field_map", {})

        result = pd.DataFrame()
        result = self.apply_field_map(working, result, field_map, "Courses", context)

        # Custom logic: drop rows with no course code
        if "Course ID" in result.columns:
            result = result[result["Course ID"].notna()]

        return result.reset_index(drop=True)
```

Key conventions:

- Call `self.normalize_columns(df)` first — this ensures all column lookups are lowercase.
- Work on a copy so you don't mutate the input DataFrame.
- Use `self.apply_field_map()` for standard field mappings, then add custom logic on top.
- Use `self.get_source_file(context, mapping["source_files"], "role_name")` to load additional source files.
- Return a DataFrame with `reset_index(drop=True)`.

---

## Step 2 — Register the transformer

Add it to the registry in `src/etl/transformers/registry.py`:

```python
from src.etl.transformers.courses import CourseTransformer   # add this

TRANSFORMER_REGISTRY: dict[str, BaseTransformer] = {
    "Students":    StudentTransformer(),
    "Staff":       StaffTransformer(),
    "Family":      FamilyTransformer(),
    "Classes":     ClassTransformer(),
    "Enrollments": EnrollmentTransformer(),
    "Courses":     CourseTransformer(),                       # add this
}
```

---

## Step 3 — Add the YAML mapping

Add an entry to `config/mappings/myedbc_mapping.yaml` under `mappings:`:

```yaml
mappings:
  # ... existing entities ...

  Courses:
    source_files:
      course_info: "CourseInformation.txt"
    field_map:
      Course ID:
        column: "course code"
      Course Name:
        column: "title"
      Grade Level:
        column: "grade"
        transform: grade_to_ceds
      School ID:
        value: "12345"
```

Add "Courses" to `global_config.entity_order` if order matters:

```yaml
global_config:
  entity_order:
    - Students
    - Staff
    - Family
    - Classes
    - Enrollments
    - Courses
```

---

## Step 4 — Write tests

Create `tests/test_transform_courses.py`:

```python
import pandas as pd
import pytest

from src.etl.transformers.courses import CourseTransformer
from src.etl.transformers.context import TransformContext


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "Course Code": ["SCI10", "MAT09"],
        "Title": ["Science 10", "Math 9"],
        "Grade": ["10", "9"],
    })


@pytest.fixture
def mapping():
    return {
        "source_files": {"course_info": "CourseInformation.txt"},
        "field_map": {
            "Course ID": "course code",
            "Course Name": "title",
            "Grade Level": {"column": "grade", "transform": "grade_to_ceds"},
        },
    }


@pytest.fixture
def context(sample_df):
    return TransformContext(
        raw_data={"CourseInformation.txt": sample_df},
        school_year=2025,
        academic_start="2025-08-25",
        academic_end="2026-07-25",
    )


def test_basic_transform(sample_df, mapping, context):
    result = CourseTransformer().transform(sample_df, mapping, context)
    assert "Course ID" in result.columns
    assert len(result) == 2


def test_empty_input(mapping, context):
    result = CourseTransformer().transform(pd.DataFrame(), mapping, context)
    assert result.empty


def test_grade_mapped_to_ceds(sample_df, mapping, context):
    result = CourseTransformer().transform(sample_df, mapping, context)
    assert result["Grade Level"].tolist() == ["10", "09"]
```

Run:

```bash
python -m pytest tests/test_transform_courses.py -v
```

---

## Using TransformContext for cross-entity data

If your transformer needs data from a previously-processed entity:

```python
# Access students output (populated after Students transformer runs)
if context.students_output is not None:
    active_ids = set(context.students_output["User ID"].dropna())
```

The context's `raw_data` dict contains all source files loaded from disk, keyed by filename (e.g., `"CourseInformation.txt"`).

---

## Accessing multiple source files

```python
def transform(self, df, mapping, context):
    working = self.normalize_columns(df)

    # Load a secondary source file
    staff_df = self.get_source_file(context, mapping["source_files"], "staff_info")
    staff_df = self.normalize_columns(staff_df)

    # Join them
    merged = working.merge(staff_df, on="some key", how="left")
    # ...
```

The `source_files` mapping in YAML uses role names (like `"staff_info"`, `"student_schedule"`) as keys. `get_source_file()` looks up the filename for that role and retrieves it from `context.raw_data`.
