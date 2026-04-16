# How Classes Work

DistrictSync generates three types of classes in `Classes.csv`. Understanding how each type is detected helps you verify the output matches your district's expectations.

---

## Class types

### Subject classes

The primary class type. Each row in the Student Schedule GDE file represents a student enrolled in a course section. DistrictSync joins the schedule with Course Information and Staff Information to build a class record for each unique section.

A subject class is identified by its **Master Timetable ID** — the unique section identifier assigned by MyEdBC. The class name is composed from the teacher name, course title, section letter, and school year (e.g., "Reed - Science 7 A 2025").

### Homeroom classes

Auto-generated for elementary grades. The district config specifies which grades get homerooms via `homeroom_grades` in `global_config`:

```yaml
global_config:
  homeroom_grades: ["KG", "01", "02", "03", "04", "05", "06", "07"]
```

For each student in a homeroom grade, DistrictSync creates a homeroom class based on the student's school and homeroom assignment from the Student Demographic file. Homeroom classes use an ID format of `HR_<school>_<homeroom>_<year>`.

Grades not listed in `homeroom_grades` do not get homeroom classes.

### Blended classes

Detected automatically when the **same teacher** teaches **multiple course sections** at the **same time slot** (same period, day, and semester) with students from **two or more grade levels**.

This is common in small or rural schools where, for example, a teacher runs a combined Grade 1/2 class. Instead of creating separate class records that would split the roster in SpacesEDU, DistrictSync merges them into a single blended class.

**How detection works:**

1. Class sections are grouped by teacher + time slot (school, teacher ID, semester, day, period)
2. Each group is checked for multiple unique Master Timetable IDs
3. If those sections have students from 2+ different grade levels, the group is identified as a blended class
4. All original sections are mapped to a single blended class ID
5. The blended class name includes the teacher, course titles, and grade range (e.g., "Reed - Science 3 / Science 4 (03/04) 2025")

**Grade assignment:** Each section's grade is determined by the most common grade among its enrolled students (from the Student Schedule data).

---

## Data sources by class type

| Class type | Primary data source | Additional sources |
|-----------|--------------------|--------------------|
| Subject | Student Schedule | Course Information, Staff Information |
| Homeroom | Student Demographic | — |
| Blended | Student Schedule + Class Information | Course Information, Staff Information |

!!! note "Districts without Enhanced Class Information"
    Some districts (e.g., SD40) have a non-enhanced Class Information file that lacks the `Teacher ID` and `Master Timetable ID` columns needed for blended detection. In these cases, DistrictSync falls back to the Student Schedule data (deduplicated to one row per section) to perform the same detection. The results are equivalent.

---

## Enrollments

Each class type generates corresponding enrollment records in `Enrollments.csv`:

- **Subject classes** — one student enrollment row per student-section pair, plus one teacher enrollment per section
- **Homeroom classes** — one student enrollment per student in a homeroom grade, plus one teacher enrollment per homeroom
- **Blended classes** — student enrollments follow the blended class ID; teacher enrollments are created for all teachers associated with the blended group

Enrollments are deduplicated on `Class ID + User ID + Role` to prevent duplicates when a student or teacher appears in multiple source rows for the same section.

---

## Verifying the output

After a run, check `Classes.csv` for expected class types:

- **Class IDs starting with `HR_`** are homeroom classes
- **Class IDs starting with `BLENDED_`** are blended classes
- **All other Class IDs** are subject classes (typically the Master Timetable ID with year suffix)

Use the `--quality` flag or the Convert page's quality report to check for orphaned enrollments (enrollment rows referencing a class ID not in `Classes.csv`).
