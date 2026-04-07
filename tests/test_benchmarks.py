"""Performance benchmarks for the core ETL pipeline.

These tests are excluded from the normal test run (-m 'not benchmark').
Run manually with: pytest tests/test_benchmarks.py -m benchmark --benchmark-only

Uses a synthetic 5,000-student dataset to detect regressions in
transformation throughput.
"""

import numpy as np
import pandas as pd
import pytest

from src.etl.transformer import DataTransformer


@pytest.fixture(scope="module")
def large_student_df():
    """5,000 synthetic student rows."""
    n = 5000
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "student number": [f"S{i:05d}" for i in range(n)],
        "legal first name": [f"FirstName{i}" for i in range(n)],
        "legal surname": [f"LastName{i}" for i in range(n)],
        "date of birth": ["2010-01-15"] * n,
        "grade": rng.choice(
            ["K", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"],
            n,
        ).tolist(),
        "school number": rng.choice(["100", "200", "300"], n).tolist(),
        "homeroom": [f"HR{i % 30}" for i in range(n)],
        "previous school number": [""] * n,
        "usual first name": [""] * n,
        "usual surname": [""] * n,
        "student email address": [f"student{i}@test.ca" for i in range(n)],
        "enrolment status": rng.choice(["Active", "Active", "Active", "PreReg"], n).tolist(),
        "teacher name": [f"Teacher{i % 50}" for i in range(n)],
        "teacher id": [f"T{i % 50:03d}" for i in range(n)],
    })


@pytest.fixture(scope="module")
def students_mapping():
    from pathlib import Path

    import yaml
    with open(Path("config/mappings/myedbc_mapping.yaml")) as f:
        return yaml.safe_load(f)["mappings"]["Students"]


@pytest.fixture(scope="module")
def global_config_module():
    from pathlib import Path

    import yaml
    with open(Path("config/mappings/myedbc_mapping.yaml")) as f:
        full = yaml.safe_load(f)
    return {**full.get("global_config", {}), "mappings": full.get("mappings", {})}


@pytest.mark.benchmark
def test_benchmark_student_transform(benchmark, large_student_df, students_mapping, global_config_module):
    """Benchmark: transform 5,000 student records."""
    raw_data = {"StudentDemographicInformation.txt": large_student_df}
    transformer = DataTransformer()
    transformer.set_school_year(2025)

    result = benchmark(
        transformer.transform,
        large_student_df,
        students_mapping,
        "Students",
        raw_data,
        global_config_module,
    )
    assert len(result) > 0, "Benchmark produced no output rows"
