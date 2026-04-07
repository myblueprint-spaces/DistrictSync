"""Canonical column name constants for GDE source files.

Centralising these string literals prevents subtle bugs from typos and
makes district-specific overrides easy to manage in one place.
"""

# Student/Staff shared
SCHOOL_NUMBER = "school number"
TEACHER_NAME = "teacher name"

# Schedule / timetable
MASTER_TIMETABLE_ID = "master timetable id"

# Staff roster
STAFF_SOURCEID = "staff sourceid"

# Course
COURSE_CODE = "course code"
DISTRICT_COURSE_CODE = "district course code"
COURSE_TITLE = "title"

# Commonly-joined columns
LAST_NAME = "last name"
