# DistrictSync

Converts **MyEducation BC General Data Extracts (GDEs)** into **SpacesEDU Advanced CSV** format for school districts in British Columbia.

Distributed as single-file executables for district servers. Runs daily via Windows Task Scheduler or cron, uploads output CSVs to SpacesEDU via SFTP automatically.

## Download

| Platform | File | Notes |
|----------|------|-------|
| **Windows** | [DistrictSync-windows.exe](https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-windows.exe) | Double-click to open Setup Wizard, or use in Task Scheduler |
| **Linux** | [DistrictSync-linux](https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-linux) | `chmod +x` before first run |
| **macOS** | [DistrictSync-macos](https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-macos) | Allow in System Settings > Privacy & Security |

For setup basics, see the **[SpacesEDU Help Centre article](https://help.spacesedu.com/en-ca/article/mx56qo)**. The `docs/` directory has the complete documentation — installation, headless/Docker SFTP, how it works, FAQ, troubleshooting, and developer guides.

## What It Does

Reads the standard GDE export files from MyEducation BC and produces the CSV files required by SpacesEDU / myBlueprint+. The 5 standard rostering files are always produced; the two myBlueprint+ course files are produced when enabled in the district config:

| Input (MyEdBC GDE) | Output (SpacesEDU / myBlueprint+) |
|---|---|
| Student Demographic | `Students.csv` |
| Staff Information – Enhanced | `Staff.csv` |
| Emergency Contact Information | `Family.csv` |
| Student Schedule + Course Information | `Classes.csv` |
| Student Schedule + Class Information – Enhanced | `Enrollments.csv` |
| Course Information | `CourseInfo.csv` *(myBlueprint+)* |
| Student Course History + Selection + Course Information | `StudentCourses.csv` *(myBlueprint+)* |

The myBlueprint+ course files (`CourseInfo.csv`, `StudentCourses.csv`) include senior courses (grades 10–12) by default. Lower the start grade to **8** or **9** per district by setting `course_start_grade` in the district's mapping config.

File names vary by district — each district's mapping config specifies its actual filenames and formats.

## Quick Start

```bash
# Windows
.\DistrictSync.exe --sis myedbc --input data\input --output data\output

# Linux
./DistrictSync --sis myedbc --input data/input --output data/output
```

## District Configurations

Use `--sis` to select a district-specific mapping:

| Flag | District |
|------|----------|
| `myedbc` | Standard MyEducation BC (default) |
| `sd40myedbc` | SD40 (New Westminster) |
| `sd48myedbc` | SD48 (Sea to Sky) |
| `sd51myedbc` | SD51 (Boundary) |
| `sd54myedbc` | SD54 (Bulkley Valley) |
| `sd74myedbc` | SD74 (Gold Trail) |

For districts feeding **myBlueprint+** course data, three tier configs select which CSVs to emit (all inherit `_base: myedbc`):

| Flag | Emits |
|------|-------|
| `mbp_all` | All 7 (5 rostering + CourseInfo + StudentCourses) |
| `mbp_core` | Students + CourseInfo + StudentCourses |
| `mbponly` | CourseInfo + StudentCourses only |

New district configs are created by hand in `config/mappings/`. Configs support `_base` inheritance — override only what differs from the default. The desktop app's **Mapping** screen reviews and switches between the built-in configs.

## CLI Options

| Flag | Description |
|------|-------------|
| `--sis` | SIS type / district config (required) |
| `--input` | Path to input GDE files (required) |
| `--output` | Output path for CSV files (default: `data/output`) |
| `--dry-run` | Preview what would be generated without writing files |
| `--diff` | Compare new output against existing CSV files |
| `--quality` | Print a data quality report after conversion |
| `--sftp` | Upload output CSVs via SFTP after a successful run |

## Desktop UI

A native desktop interface (Flet) for setup, ad-hoc conversions, and monitoring. The packaged executable **opens the desktop app automatically** when launched without arguments (double-click on Windows) — no Python or extra install required. Running the same executable with CLI arguments (`--sis`/`--input`/`--output`) uses the CLI instead.

To run from source:

```bash
pip install -r requirements.txt
python -m src.main
```

**Screens:**
- **Setup** — Configure paths, schedule, and SFTP upload
- **Convert** — Pick GDE files and run a conversion on demand
- **Run History** — View the log of automated daily runs
- **Mapping** — Review and switch the active district data configuration
- **Help** — Links to the Help Centre and support contact

## Key Features

- **Homeroom + subject + blended classes** — Automatically generates the right class types based on grade configuration
- **Active student filtering** — Only includes active and pre-registered students
- **CEDS grade mapping** — Converts grade values to standard format (K → KG, 1 → 01, etc.)
- **Anomaly detection** — Warns if any entity drops by >20% compared to the previous run
- **Atomic writes** — Output files are staged and committed as a set (all-or-nothing)
- **Config inheritance** — District configs inherit from a base and override only differences
- **Structured run logging** — JSON log entries power the Run History page
- **SFTP host allowlist** — Uploads restricted to SpacesEDU servers only
- **Quality reports** — Checks for missing fields, duplicates, and orphaned enrollments

## Logging

Debug logs written to `etl_tool.log`. Console shows WARNING+ only. Structured `__DISTRICTSYNC_RUN__` JSON entries are written after each run for the Run History page.

## Support

Contact **hello@spacesedu.com** with:
- A zipped copy of your `data/input/` folder
- The `etl_tool.log` file
