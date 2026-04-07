"""Help & Documentation — in-app reference for how GDE2Acsv works.

Covers output format, data processing logic, file handling, configuration,
quality checks, CLI features, and troubleshooting — all in plain language.
"""

import sys
from pathlib import Path

import streamlit as st

_root = Path(__file__).parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.ui.brand import header, inject_brand_css  # noqa: E402

st.set_page_config(page_title="Help — GDE2Acsv", page_icon="❓", layout="wide")
inject_brand_css()
header("Help & Documentation", "How GDE2Acsv works and what to expect")

# ---------------------------------------------------------------------------
tab_output, tab_logic, tab_files, tab_config, tab_quality, tab_cli, tab_trouble = st.tabs([
    "Output Format",
    "How It Works",
    "File Handling",
    "Configuration",
    "Quality Checks",
    "CLI & Automation",
    "Troubleshooting",
])

# ===================================================================
# TAB 1 — Output Format
# ===================================================================
with tab_output:
    st.subheader("Output CSV Files")
    st.markdown(
        "GDE2Acsv produces 5 CSV files in the SpacesEDU Advanced CSV import format. "
        "Files are UTF-8 encoded with a BOM header so they open correctly in Excel."
    )

    st.markdown("#### Students.csv")
    st.markdown("""
| Column | Description |
|---|---|
| User ID | Unique student identifier (typically Student Number) |
| Student Number | Student's official SIS number |
| First Name | Legal or preferred first name |
| Last Name | Legal or preferred last name |
| Date of Birth | Student's date of birth |
| Grade | Grade level in CEDS format (KG, 01, 02, ... 12) |
| SchoolCode | School number the student attends |
| Homeroom | Homeroom assignment (if applicable) |
| PreRegSchoolCode | Previous school number (if transferred) |
| Preferred First Name | Usual/preferred first name (if different from legal) |
| Preferred Last Name | Usual/preferred last name |
| Community Hours | Community service hours (often blank) |
| Literacy Test Completed | Literacy test status (often blank) |
| Email Address | Student email (from file, generated from a pattern, or blank) |
""")

    st.markdown("#### Staff.csv")
    st.markdown("""
| Column | Description |
|---|---|
| User ID | Unique staff identifier (typically Teacher ID) |
| First Name | Staff member's first name |
| Last Name | Staff member's last name |
| Email | Staff email address |
| Role | `teacher` or `administrator` (based on teaching staff flag: Y = teacher, anything else = administrator) |
| School ID | School number where the staff member works |
""")

    st.markdown("#### Family.csv")
    st.markdown("""
| Column | Description |
|---|---|
| First Name | Parent/guardian first name |
| Last Name | Parent/guardian last name |
| Email | Contact email address |
| Student User ID | Student number (links contact to their student) |
""")

    st.markdown("#### Classes.csv")
    st.markdown("""
| Column | Description |
|---|---|
| Class ID | Unique identifier: Master Timetable ID + school year (e.g., `MST123_2025`). Blended classes use a `BLENDED_` prefix. |
| Name | Class name (e.g., "Smith - Math 10 (A) 2025"). Truncated to 100 characters if too long. |
| Grade | Grade level. Left blank for blended classes (which span multiple grades). |
| School ID | School number |
| Start Date | Academic year start date (auto-calculated or fixed) |
| End Date | Academic year end date (auto-calculated or fixed) |
""")

    st.markdown("#### Enrollments.csv")
    st.markdown("""
| Column | Description |
|---|---|
| Class ID | Class the person is enrolled in |
| User ID | Student or teacher ID |
| Role | `student` or `teacher` |
| School ID | School number |

Enrollments are deduplicated: if the same person appears in the same class with the same role
multiple times, only one record is kept.
""")

# ===================================================================
# TAB 2 — How It Works
# ===================================================================
with tab_logic:
    st.subheader("How GDE2Acsv Processes Your Data")

    # --- Active Students ---
    st.markdown("#### Active Students Only")
    st.markdown("""
Only **active** students are included in the output. The tool determines status in two ways:

**Method 1: Enrollment Status column**
If your data has an "Enrollment Status" column, students marked as **"Active"** or **"PreReg"**
(pre-registered) are included. All other statuses (Withdrawn, Inactive, etc.) are excluded.

**Method 2: Withdrawal Date column**
If there's no status column, the tool looks at the withdrawal date:
- Empty/blank date = **Active**
- Future date = **Active** (hasn't withdrawn yet)
- Past or today's date = **Inactive**

The tool tries 4 date formats: `15-Jan-2025`, `2025-01-15`, `01/15/2025`, `15/01/2025`.
If a date can't be parsed in any format, the student is marked inactive and a warning is logged
with sample unparseable dates.

This is why you may see fewer students in the output than in your SIS.
""")

    # --- School Year ---
    st.markdown("---")
    st.markdown("#### School Year Detection")
    st.markdown("""
The school year is detected automatically from a "School Year" column in your Student Schedule file.
If that column isn't found, the tool defaults to:
- **Current year** if the current month is August or later
- **Previous year** if the current month is before August

The school year is used to generate Class IDs (e.g., `MST123_2025`) and to calculate
academic start/end dates.
""")

    # --- Homeroom Classes ---
    st.markdown("---")
    st.markdown("#### Homeroom Classes")
    st.markdown("""
**What they are:** In elementary schools, students typically stay in one classroom with one teacher
who teaches multiple subjects. This is the "homeroom" model.

**How it works:**
- You configure which grade levels use homerooms (e.g., Kindergarten through Grade 7)
- For students in those grades, the tool creates **one homeroom class** per unique
  school + homeroom + teacher combination
- Both the students AND the teacher get enrolled into that single class
- The class name format is: `"TeacherName - Homeroom HomeRoom (Year)"`
- If the teacher name is missing, the class is named `"Unassigned Homeroom (Year)"`

**Example:** Mrs. Johnson teaches Homeroom 3A at Hillcrest Elementary with 25 students:
- 1 class: "Johnson - Homeroom 3A 2025"
- 25 student enrollments + 1 teacher enrollment = 26 records
""")

    # --- Subject Classes ---
    st.markdown("---")
    st.markdown("#### Subject Classes")
    st.markdown("""
**What they are:** In secondary schools, students move between classrooms for different subjects.

**How it works:**
- For students in grades NOT configured as homeroom grades, the tool creates classes from
  the Student Schedule data
- Course Information and Staff Information are joined to build class names
- Each unique course section becomes a class
- Class names are formatted as: `"TeacherLastName - CourseTitle (Section) Year"`
- Names exceeding 100 characters are truncated at a word boundary with "..."
- If a course title is missing from the Course Information file, it shows as "Unknown Course"

**District Course Code handling:** If your schedule file has a "District Course Code" column
but not "Course Code", it's automatically used for course matching.
""")

    # --- Blended Classes ---
    st.markdown("---")
    st.markdown("#### Blended Classes")
    st.markdown("""
**What they are:** When the same teacher teaches students from **multiple grade levels**
at the same time in the same classroom.

**Detection logic:** The tool groups class sections by school + teacher + term + semester +
day + period. If a group has 2+ sections with students from **2+ different grades**, it's
blended.

**What happens when blended:**
- All sections are merged into one class with a `BLENDED_` ID prefix
- The class name shows all course titles and grade range: "Garcia - English 3 / English 4 (10/11) 2025"
- The Grade field is left **blank** (since it spans multiple grades)
- ALL teachers in the blend get enrolled (not just the primary teacher)
- If teacher/course data is incomplete, the fallback name is "Blended Class GradeRange Year"

**Requirements:** Blended detection needs the **Class Information (Enhanced)** file with
teacher ID and schedule columns (term, semester, day, period). Without this file, blending
is skipped.
""")

    # --- Enrollments ---
    st.markdown("---")
    st.markdown("#### Enrollment Records")
    st.markdown("""
Enrollments link **people** to **classes**. The tool creates three types:

**1. Student Homeroom Enrollments** — For students in homeroom grades, one enrollment per
student per homeroom class.

**2. Student Subject Enrollments** — For students in non-homeroom grades, one enrollment per
course in the student's schedule.

**3. Teacher Enrollments** — Teachers are enrolled in the classes they teach. For blended
classes, ALL teachers in the blend are enrolled.

**Automatic cleanup:**
- Enrollments are deduplicated on Class ID + User ID + Role
- Teachers with invalid IDs (blank, null, or the text "nan") are automatically removed
  from enrollments but may still appear in the Staff output
""")

    # --- Email Generation ---
    st.markdown("---")
    st.markdown("#### Email Address Generation")
    st.markdown("""
Student emails can be configured in three ways:

1. **Read from a column** — Use the email address directly from your data file
2. **Generate from a pattern** — Build emails using a template, e.g., `{student number}@sd40.bc.ca`
   - Column names in braces are replaced with the student's data (case-insensitive)
   - If a referenced column doesn't exist, the email is left blank
3. **Leave blank** — No email is included

Pattern examples: `{legal first name}.{legal surname}@district.ca` or `{student number}@sd40.bc.ca`
""")

    # --- Grade Format ---
    st.markdown("---")
    st.markdown("#### Grade Format (CEDS)")
    st.markdown("""
Grade values are automatically converted to the **CEDS** (Common Education Data Standards) format:

| Your Data | CEDS | | Your Data | CEDS | | Your Data | CEDS |
|---|---|---|---|---|---|---|---|
| K, KF, EL | KG | | 4 | 04 | | 8 | 08 |
| 1 | 01 | | 5 | 05 | | 9 | 09 |
| 2 | 02 | | 6 | 06 | | 10 | 10 |
| 3 | 03 | | 7 | 07 | | 11, 12 | 11, 12 |

Unrecognized values become **UG** (Ungraded). If your grades show as UG, check the original
values in your source file.
""")

    # --- Invalid ID Handling ---
    st.markdown("---")
    st.markdown("#### Invalid ID Handling")
    st.markdown("""
Records with invalid IDs are automatically removed from the output:
- **Blank or empty** IDs
- **Null** values
- The literal text **"nan"** (which some SIS systems export instead of blanks)

This affects teacher enrollments most commonly — teachers without valid IDs are dropped from
enrollment records but remain in the Staff output.
""")

# ===================================================================
# TAB 3 — File Handling
# ===================================================================
with tab_files:
    st.subheader("How Files Are Processed")

    st.markdown("#### Encoding & Delimiter Detection")
    st.markdown("""
GDE2Acsv automatically handles different file formats:

**Encoding:** Tries UTF-8 first, then Latin-1, then CP1252. The first encoding that works is used.
If all three fail, the file cannot be processed — try re-exporting from your SIS as UTF-8.

**Delimiter:** Tries comma, then tab, then auto-detect. Most GDE files use comma or tab delimiters.

**Output:** CSV files are written in UTF-8 with a BOM (Byte Order Mark) header so they open
correctly in Microsoft Excel without garbled characters.
""")

    st.markdown("---")
    st.markdown("#### Headerless Files")
    st.markdown("""
Some districts receive GDE files without column headers (the first row is data, not column names).

GDE2Acsv handles this through the mapping configuration: you specify the column names in order,
and they're applied when the file is loaded. The Mapping Editor wizard can help detect headerless
files and set up the headers.

If your Student Schedule file has no headers, the standard column order is:
School Year, School Number, Student Number, PEN, Grade, Homeroom, Course School Number,
Course Code, District Course Code, Course Title, Short Name, Period, Day, Semester,
Section Letter, Master Timetable ID, Teacher ID, Teacher Name, Primary Teacher, Enrolment Status
""")

    st.markdown("---")
    st.markdown("#### Atomic Writes (All-or-Nothing)")
    st.markdown("""
When writing output files, GDE2Acsv uses a safety mechanism:

1. All CSV files are first written to a temporary staging directory
2. Only after **every file** writes successfully are they moved to the output directory
3. If any file fails to write, the staging directory is cleaned up and your **existing output
   remains untouched**

This means you'll never end up with a partial set of output files — either all 5 succeed or
none are changed.
""")

    st.markdown("---")
    st.markdown("#### Staff Roster Merge")
    st.markdown("""
If your district has a separate staff roster file (in addition to the standard Staff Information),
you can configure it as a secondary source. The tool will merge the two files on Teacher ID,
allowing you to pull staff IDs from one file and other details from another.
""")

# ===================================================================
# TAB 4 — Configuration
# ===================================================================
with tab_config:
    st.subheader("Configuration System")

    st.markdown("#### Config Inheritance")
    st.markdown("""
District mapping configs can **inherit** from a base config using the `_base` setting.
This means your district config only needs to specify what's **different** from the default.

For example, if SD48 only changes 4 file names from the standard MyEdBC config, the SD48 config
is just ~20 lines — not a full copy of the 100-line base. When the base config is updated
(new fields, bug fixes), your district automatically inherits those changes.

The Mapping Editor wizard handles this automatically when you create a new district config.
""")

    st.markdown("---")
    st.markdown("#### Academic Calendar Dates")
    st.markdown("""
Class start and end dates can be set in two ways:

1. **Automatic** (default) — Dates are calculated from the detected school year using the
   month-day patterns you configure (default: August 25 start, July 25 end). If the school
   year is 2025, dates become 2025-08-25 and 2026-07-25.

2. **Fixed** — You specify exact dates (e.g., 2025-08-25 and 2026-07-25). This is useful
   when your district's calendar doesn't follow the standard pattern.

Each entity (Classes, Enrollments) can override the global setting.
""")

    st.markdown("---")
    st.markdown("#### Anomaly Detection")
    st.markdown("""
After each run, GDE2Acsv compares the new output against the previous run's files. If any
entity's record count **drops by more than 20%**, a warning is logged.

This catches common issues like:
- A partial or corrupted GDE export
- An accidental filter that excluded too many students
- Missing source files

The warnings appear in the Run History page and the log file.
""")

# ===================================================================
# TAB 5 — Quality Checks
# ===================================================================
with tab_quality:
    st.subheader("Data Quality Report")
    st.markdown(
        "After conversion, GDE2Acsv runs quality checks to flag potential data issues. "
        "View the report on the Convert page or use `--quality` on the command line."
    )

    st.markdown("#### What's Checked")

    st.markdown("""
**Missing or Empty Fields**
- Counts how many records have blank values in each column
- Flags columns where more than 50% of values are missing
- Helps catch mapping errors (e.g., "the email column name was wrong")

**Duplicate Records**
- Checks for duplicates on key fields:
  - Students: User ID
  - Staff: User ID
  - Family: Student User ID + Email
  - Classes: Class ID
  - Enrollments: Class ID + User ID + Role
- Duplicate students may indicate the same person enrolled at multiple schools

**Orphaned Enrollments**
- Checks that every enrollment references a class that exists in the Classes output
- Checks that every enrollment references a person in Students or Staff
- Orphaned records mean something went wrong in class generation or student filtering

**Grade Distribution**
- Shows how many students are in each grade
- Flags grades with only 1 student (potential data error)
""")

# ===================================================================
# TAB 6 — CLI & Automation
# ===================================================================
with tab_cli:
    st.subheader("Command Line & Automation")

    st.markdown("#### Running from the Command Line")
    st.markdown("""
```
GDE2Acsv --sis myedbc --input /path/to/gde/files --output /path/to/output
```

**Flags:**
| Flag | What it does |
|---|---|
| `--sis myedbc` | Which district mapping to use (required) |
| `--input /path` | Directory containing your GDE source files (required) |
| `--output /path` | Where to write output CSVs (default: `data/output`) |
| `--dry-run` | Preview what would be produced without writing any files |
| `--diff` | Compare new output against existing files (shows row count changes, added/removed columns) |
| `--quality` | Generate a data quality report after conversion |
| `--sftp` | Upload output CSVs via SFTP after a successful run |
""")

    st.markdown("---")
    st.markdown("#### Scheduled Automation")
    st.markdown("""
The Setup Wizard configures a daily scheduled task that runs GDE2Acsv automatically:

- **Windows:** Creates a Windows Task Scheduler entry
- **Linux/macOS:** Adds a crontab entry

The scheduled task runs at your configured time (default: 3:00 AM), processes the GDE files,
writes output CSVs, and optionally uploads them via SFTP.
""")

    st.markdown("---")
    st.markdown("#### Run History Logging")
    st.markdown("""
After each run (success or failure), GDE2Acsv writes a structured log entry containing:
- Timestamp and duration
- Success or failure status
- Row counts for each entity (Students, Staff, Family, Classes, Enrollments)
- SFTP upload result
- Any error messages or anomaly warnings

These entries power the **Run History** page and are stored in the `etl_tool.log` file.
""")

# ===================================================================
# TAB 7 — Troubleshooting
# ===================================================================
with tab_trouble:
    st.subheader("Troubleshooting")

    st.markdown("#### First-Run Issues")
    st.markdown("""
**"I double-clicked the .exe and got a black terminal that closed"**
- The CLI exe requires command-line flags. Use the Setup Wizard instead:
  right-click the exe, look for a "Setup" or "UI" option, or run
  `GDE2Acsv.exe --help` from a terminal.

**"The browser didn't open"**
- Try opening `http://localhost:8501` manually in your browser.
- Check if another application is using port 8501.

**"Access denied when saving the schedule"**
- Right-click the application and select "Run as administrator."
- Windows Task Scheduler requires elevated permissions to create tasks.

**"The district dropdown is empty"**
- The `config/mappings/` directory may not be found. Ensure the application
  is run from its installation directory.
""")

    st.markdown("---")
    st.markdown("#### Data Issues")
    st.markdown("""
**"Some students are missing from the output"**
- Only active students (and pre-registered students) are included
- Check the enrollment status or withdrawal date in your source data
- Use `--quality` or the Convert page to see how many were filtered
- If withdrawal dates can't be parsed, those students are marked inactive

**"Classes or enrollments are empty"**
- Check that your Student Schedule file has the expected columns
- If your file has no headers, configure them in the Mapping Editor
- Verify the mapping's source file names match your actual filenames

**"Blended classes aren't being detected"**
- Blended detection requires the Class Information (Enhanced) file
- The file must have Teacher ID, Term, Semester, Day, and Period columns
- There must be 2+ grade levels in the same time slot for blending to trigger

**"Class names show 'Unknown Course'"**
- The Course Information file may be missing or the course code didn't match
- Check that Course Code values match between Student Schedule and Course Information

**"Teacher enrollments are missing"**
- Teachers with blank, null, or "nan" IDs are automatically removed from enrollments
- Check the Teacher ID column in your schedule file for empty values

**"Encoding errors or garbled characters"**
- The tool tries UTF-8, Latin-1, and CP1252 encodings automatically
- If your files use a different encoding, re-export from your SIS as UTF-8

**"Grades show as 'UG' (Ungraded)"**
- The grade value doesn't match any known format
- Check original values — should be numbers (1-12), K, KG, EL, etc.
""")

    st.markdown("---")
    st.markdown("#### Getting Help")
    st.markdown("Contact **support@myBlueprint.ca** for assistance.")

st.divider()
st.caption("SpacesEDU by myBlueprint · GDE2Acsv · support@myBlueprint.ca")
