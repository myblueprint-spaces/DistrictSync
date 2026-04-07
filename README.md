# GDE2Acsv

Converts **MyEducation BC General Data Extracts (GDEs)** into **SpacesEDU Advanced CSV** format for school districts in British Columbia.

Distributed as single-file executables for district servers. Runs daily via Windows Task Scheduler or cron, uploads output CSVs to SpacesEDU via SFTP automatically.

## Download

| Platform | File | Notes |
|----------|------|-------|
| **Windows** | [GDE2Acsv-windows.exe](https://github.com/myblueprint/GDE2Acsv/releases/latest/download/GDE2Acsv-windows.exe) | Double-click to open Setup Wizard, or use in Task Scheduler |
| **Linux** | [GDE2Acsv-linux](https://github.com/myblueprint/GDE2Acsv/releases/latest/download/GDE2Acsv-linux) | `chmod +x` before first run |
| **macOS** | [GDE2Acsv-macos](https://github.com/myblueprint/GDE2Acsv/releases/latest/download/GDE2Acsv-macos) | Allow in System Settings > Privacy & Security |

See the **[Partner Installation Guide](docs/partner/installation.md)** for step-by-step setup with screenshots.

## Quick Start

### 1. Place GDE files

Copy these files from your MyEducation BC system into `data/input/`:

| File | Description |
|------|-------------|
| `StudentDemographicInformation.txt` | Student records (demographics, grade, homeroom) |
| `StudentSchedule.txt` | Course enrolments per student |
| `StaffInformationEnhanced.txt` | Staff records with teaching flag |
| `EmergencyContactInformation.txt` | Parent/guardian contacts |
| `CourseInformation.txt` | Course catalog |
| `ClassInformationEnh.txt` | Class timetable (optional, for blended class detection) |

### 2. Run the tool

**Windows (PowerShell):**
```powershell
.\GDE2Acsv.exe --sis myedbc --input data\input --output data\output
```

**Linux:**
```bash
./GDE2Acsv --sis myedbc --input data/input --output data/output
```

### 3. Upload output

Five CSV files are generated in `data/output/`:

- `Students.csv` — Active students with CEDS grade codes
- `Staff.csv` — Teachers and administrators
- `Family.csv` — Parent/guardian contacts
- `Classes.csv` — Homeroom + subject + blended classes
- `Enrollments.csv` — Student and teacher class enrolments

Upload these to your district's SpacesEDU SFTP folder.

## District-Specific Configurations

Use the `--sis` flag to select a district-specific mapping:

| Flag | District |
|------|----------|
| `myedbc` | Standard MyEducation BC (default) |
| `sd48myedbc` | SD48 (Sea to Sky) |
| `sd51myedbc` | SD51 (Boundary) |
| `sd74myedbc` | SD74 (Gold Trail) |

District configs customize source filenames, field mappings, email formats, and academic dates as needed.

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

### Dry run

Preview row counts and columns before writing anything:

```bash
.\GDE2Acsv.exe --sis myedbc --input data\input --output data\output --dry-run
```

### Diff mode

See what changed compared to the last run:

```bash
.\GDE2Acsv.exe --sis myedbc --input data\input --output data\output --diff
```

### Quality report

Check for missing fields, duplicate records, and orphaned enrolments:

```bash
.\GDE2Acsv.exe --sis myedbc --input data\input --output data\output --quality
```

## Web UI (Ad-Hoc Conversions)

For one-off conversions without the command line, a browser-based interface is available.

**Requirements:** Python 3.9+ with `streamlit` installed.

```bash
pip install streamlit
streamlit run src/ui/app.py
```

The web interface lets you:
1. Select a district configuration from a dropdown
2. Upload GDE `.txt` files via drag-and-drop
3. Preview output tables in the browser
4. Download individual CSVs or a ZIP archive of all outputs

This is useful for ad-hoc conversions, verifying data before automating, or districts that prefer a visual interface.

## Configuration

All field mappings live in YAML files under `config/mappings/`. Key options:

**Student email generation** — Set a template in the Students field_map:
```yaml
"Email Address":
  format: "{student number}@sd51.bc.ca"
```

**Homeroom grades** — Control which grades get homeroom classes:
```yaml
homeroom_grades: ["KG", "01", "02", "03", "04", "05", "06", "07"]
```

**Fixed academic dates** — Override auto-calculated dates:
```yaml
"Start Date":
  value: "2025-08-25"
  use_academic_year: false
```

**Config inheritance** — District configs can inherit from a base and override only differences:
```yaml
_base: myedbc
sis: district42
mappings:
  Students:
    source_files:
      student_demographic: "CustomDemo.txt"
```

## Logging

Debug logs are written to `etl_tool.log` (append mode). Console shows WARNING+ only. If something goes wrong, check this log file for details on file loading, record counts, blended class detection, homeroom generation, and active student filtering.

## Support

Contact Shan Peiris (shan.peiris@myblueprint.ca) with:
- A zipped copy of your `data/input` folder
- The `etl_tool.log` file
