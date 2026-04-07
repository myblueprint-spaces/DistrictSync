"""Data quality report generator.

Produces a summary of the ETL output highlighting potential issues:
- Missing/empty required fields
- Duplicate records
- Orphaned enrollments (class or user not found)
- Grade distribution
- Record counts per entity
"""

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class EntityReport:
    """Quality metrics for a single output entity."""

    name: str
    row_count: int = 0
    duplicate_count: int = 0
    missing_fields: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DataQualityReport:
    """Full quality report across all entities."""

    entities: dict[str, EntityReport] = field(default_factory=dict)
    cross_entity_warnings: list[str] = field(default_factory=list)

    def analyze(self, outputs: dict[str, pd.DataFrame]) -> "DataQualityReport":
        """Run all quality checks on the pipeline outputs."""
        for name, df in outputs.items():
            report = EntityReport(name=name, row_count=len(df))
            self._check_missing_fields(report, df)
            self._check_duplicates(report, df, name)
            self.entities[name] = report

        self._check_orphaned_enrollments(outputs)
        self._check_grade_distribution(outputs)
        return self

    def _check_missing_fields(self, report: EntityReport, df: pd.DataFrame) -> None:
        """Flag columns where values are missing or empty."""
        for col in df.columns:
            null_count = df[col].isna().sum()
            empty_count = (df[col].astype(str).str.strip() == "").sum()
            total_missing = int(null_count + empty_count)
            if total_missing > 0:
                report.missing_fields[col] = total_missing
                pct = total_missing / len(df) * 100 if len(df) > 0 else 0
                if pct > 50:
                    report.warnings.append(f"{col}: {pct:.0f}% missing ({total_missing}/{len(df)})")

    def _check_duplicates(self, report: EntityReport, df: pd.DataFrame, name: str) -> None:
        """Check for duplicate records based on entity-specific keys.

        Known entities use predefined key columns. Unknown entities fall back
        to a heuristic: any column ending with ' ID' or named 'Course Code'.
        """
        key_map = {
            "Students": ["User ID"],
            "Staff": ["User ID"],
            "Family": ["Student User ID", "Email"],
            "Classes": ["Class ID"],
            "Enrollments": ["Class ID", "User ID", "Role"],
        }
        keys = key_map.get(name)
        if keys is None:
            # Heuristic for unknown entities: columns ending with " ID" or " Code"
            keys = [c for c in df.columns if c.endswith(" ID") or c.endswith(" Code")]
        if keys and all(k in df.columns for k in keys):
            dupes = df.duplicated(subset=keys, keep=False).sum()
            report.duplicate_count = int(dupes)
            if dupes > 0:
                report.warnings.append(f"{dupes} duplicate rows on {keys}")

    def _check_orphaned_enrollments(self, outputs: dict[str, pd.DataFrame]) -> None:
        """Check for enrollments referencing non-existent classes or users."""
        enrollments = outputs.get("Enrollments")
        if enrollments is None:
            return

        classes = outputs.get("Classes")
        if classes is not None and "Class ID" in enrollments.columns and "Class ID" in classes.columns:
            class_ids = set(classes["Class ID"].dropna())
            enrolled_classes = set(enrollments["Class ID"].dropna())
            orphaned = enrolled_classes - class_ids
            if orphaned:
                self.cross_entity_warnings.append(f"{len(orphaned)} enrollment class IDs not found in Classes output")

        students = outputs.get("Students")
        staff = outputs.get("Staff")
        if "User ID" in enrollments.columns:
            known_users: set[str] = set()
            if students is not None and "User ID" in students.columns:
                known_users.update(students["User ID"].dropna().astype(str).tolist())  # type: ignore[arg-type]
            if staff is not None and "User ID" in staff.columns:
                known_users.update(staff["User ID"].dropna().astype(str).tolist())  # type: ignore[arg-type]
            if known_users:
                enrolled_users = set(enrollments["User ID"].dropna().astype(str))
                orphaned = enrolled_users - known_users
                if orphaned:
                    self.cross_entity_warnings.append(
                        f"{len(orphaned)} enrollment user IDs not found in Students/Staff output"
                    )

    def _check_grade_distribution(self, outputs: dict[str, pd.DataFrame]) -> None:
        """Report grade distribution in Students for visibility."""
        students = outputs.get("Students")
        if students is None or "Grade" not in students.columns:
            return
        dist = students["Grade"].value_counts().to_dict()
        grades_with_one = [g for g, c in dist.items() if c == 1]
        if grades_with_one:
            self.cross_entity_warnings.append(
                f"Grades with only 1 student: {', '.join(str(g) for g in grades_with_one)}"
            )

    def to_text(self) -> str:
        """Render the report as a human-readable text string."""
        lines = ["=" * 60, "DATA QUALITY REPORT", "=" * 60, ""]

        for name, report in self.entities.items():
            lines.append(f"--- {name} ---")
            lines.append(f"  Rows: {report.row_count}")
            if report.duplicate_count:
                lines.append(f"  Duplicates: {report.duplicate_count}")
            if report.missing_fields:
                lines.append("  Missing/empty fields:")
                for col, count in sorted(report.missing_fields.items(), key=lambda x: -x[1]):
                    lines.append(f"    {col}: {count}")
            if report.warnings:
                lines.append("  Warnings:")
                for w in report.warnings:
                    lines.append(f"    ! {w}")
            lines.append("")

        if self.cross_entity_warnings:
            lines.append("--- Cross-Entity Checks ---")
            for w in self.cross_entity_warnings:
                lines.append(f"  ! {w}")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)
