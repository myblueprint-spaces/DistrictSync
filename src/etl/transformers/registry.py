"""Transformer registry — maps entity names to their transformer instances."""

import logging
from typing import Any

import pandas as pd

from src.etl.transformers.base import BaseTransformer
from src.etl.transformers.classes import ClassTransformer
from src.etl.transformers.context import TransformContext
from src.etl.transformers.course_info import CourseInfoTransformer
from src.etl.transformers.enrollments import EnrollmentTransformer
from src.etl.transformers.family import FamilyTransformer
from src.etl.transformers.staff import StaffTransformer
from src.etl.transformers.student_attendance import StudentAttendanceTransformer
from src.etl.transformers.student_courses import StudentCoursesTransformer
from src.etl.transformers.students import StudentTransformer

logger = logging.getLogger(__name__)


class DefaultTransformer(BaseTransformer):
    """Generic transformer for entities that only need field mapping.

    Any entity defined in YAML config that doesn't require custom logic
    (joins, blended class detection, etc.) works automatically via this class.
    """

    def __init__(self, entity_name: str):
        self._entity_name = entity_name

    def transform(self, df: pd.DataFrame, mapping: dict[str, Any], context: TransformContext) -> pd.DataFrame:
        working = self.normalize_columns(df)
        result = pd.DataFrame()
        field_map = mapping.get("field_map", {})
        return self.apply_field_map(working, result, field_map, self._entity_name, context)


TRANSFORMER_REGISTRY: dict[str, BaseTransformer] = {
    "Students": StudentTransformer(),
    "Staff": StaffTransformer(),
    "Family": FamilyTransformer(),
    "Classes": ClassTransformer(),
    "Enrollments": EnrollmentTransformer(),
    "CourseInfo": CourseInfoTransformer(),
    "StudentCourses": StudentCoursesTransformer(),
    "StudentAttendance": StudentAttendanceTransformer(),
}


def source_column_roles(entity_name: str) -> frozenset[str]:
    """The ``source_columns`` ROLES the transformer for ``entity_name`` reads (plan 0053 S12).

    Read off the transformer class (``BaseTransformer.SOURCE_COLUMN_ROLES``), so the
    config loader judges a role against exactly what the read sites resolve. An entity
    with no registered transformer runs through :class:`DefaultTransformer`, which reads
    no ``source_columns`` block — every role there is unknown. No log line (unlike
    :func:`get_transformer`): this is asked at config load, not at run time.
    """
    transformer = TRANSFORMER_REGISTRY.get(entity_name)
    return transformer.SOURCE_COLUMN_ROLES if transformer is not None else DefaultTransformer.SOURCE_COLUMN_ROLES


def get_transformer(entity_name: str) -> BaseTransformer:
    transformer = TRANSFORMER_REGISTRY.get(entity_name)
    if transformer is None:
        logger.info(f"No registered transformer for '{entity_name}'; using DefaultTransformer")
        return DefaultTransformer(entity_name)
    return transformer
