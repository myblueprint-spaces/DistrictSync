# Frequently Asked Questions

## General

**Q: How often does the tool run?**

Once per day at the time configured in the Setup Wizard (default: 3:00 AM). This is controlled by Windows Task Scheduler (Windows) or cron (Linux/macOS).

**Q: What happens if the GDE files are not present at run time?**

The tool logs a warning for each missing file and skips the affected entity. For example, if the Course Information GDE file is missing, Classes and Enrollments will be skipped. The run is still considered complete; other entities are processed normally.

**Q: Can I run it manually?**

Yes, at any time:
```cmd
DistrictSync-windows.exe --sis myedbc --input C:\DistrictSync\input --output C:\DistrictSync\output
```

Add `--sftp` to also upload after generating:
```cmd
DistrictSync-windows.exe --sis myedbc --input ... --output ... --sftp
```

**Q: Can I preview the output without writing files?**

Yes — use the `--dry-run` flag. It prints a summary of how many rows each entity would produce, without writing any files or uploading anything.

---

## Data questions

**Q: Why are some students missing from the output?**

Only students with `Enrolment Status = Active` are included. Students with status `PreReg`, `Inactive`, or with a past withdrawal date are excluded. Run `--quality` to see a breakdown.

**Q: What does "blended class" mean?**

A blended class is detected when the same teacher teaches multiple sections at the same time slot but with students from different grade levels. DistrictSync automatically merges these into a single class record for SpacesEDU. The class is named after the teacher, course titles, and grade range (e.g., "Reed - Science 3 / Science 4 (03/04) 2025"). See [How Classes Work](how-classes-work.md) for full details on class types.

**Q: Why does the grade show as "01" instead of "1"?**

DistrictSync maps all grade codes to the CEDS (Common Education Data Standards) format:

| MyEdBC grade | CEDS output |
|-------------|-------------|
| K | KG |
| 1 | 01 |
| 2 | 02 |
| … | … |
| 12 | 12 |

This is required by the SpacesEDU import format.

---

**Q: Do I have to set up a schedule?**

No. The schedule is optional. You can use the **Convert** page in the web UI to run ad-hoc conversions: upload GDE files, convert, and download the CSVs directly in the browser. The schedule is only needed for unattended daily runs.

**Q: How are files uploaded via SFTP?**

All 5 output CSVs are zipped into a single dated file (e.g., `districtsync_2026-04-08.zip`) and uploaded as one file. This applies to both scheduled runs and ad-hoc uploads from the Convert page.

**Q: Can I change the schedule or SFTP settings after setup?**

Yes. Open the **Setup Wizard** page — if you've already completed setup, it shows a management dashboard where you can edit the schedule time, disable the schedule, edit SFTP settings, or disable SFTP. You don't need to re-run the full wizard.

---

## What happens after upload (SpacesEDU import)

DistrictSync generates the CSV files and uploads them. **SpacesEDU** then imports them. The following describes SpacesEDU's import behavior — not DistrictSync's.

**Q: In what order does SpacesEDU process the files?**

1. `Students.csv` — creates or updates student accounts
2. `Staff.csv` — creates or updates staff accounts
3. `Family.csv` — links family/guardian records to students
4. `Classes.csv` — creates or updates class records
5. `Enrollments.csv` — enrolls students and teachers into classes

This order ensures dependencies are met (e.g., students exist before enrollments are created).

**Q: How does SpacesEDU match existing users?**

SpacesEDU matches incoming records against the database by **User ID** or **email**. If a match is found, the existing account is updated rather than duplicated.

**Q: What happens to students or staff no longer in the file?**

Users that no longer appear in `Students.csv` or `Staff.csv` (by User ID, Role, and School ID) are marked **Inactive** in SpacesEDU. They are not deleted.

**Q: What happens to enrollments no longer in the file?**

Students and teachers are **unenrolled** from a class if they no longer appear in `Enrollments.csv` for that class. Existing classes are preserved and new enrollments are added.

**Q: When does SpacesEDU skip a record during import?**

SpacesEDU validates each record and skips it (with a flag in the import report) when:

| Issue | Action |
|-------|--------|
| Missing required field (User ID, Name, etc.) | Skip record |
| Field format doesn't match (e.g., invalid grade) | Skip record |
| Email doesn't match the district's email domain | Skip record |
| Student ID in Family.csv not found in Students.csv | Skip record |
| Class ID in Enrollments.csv not found in Classes.csv | Skip record |
| User ID in Enrollments.csv not found in Students/Staff | Skip record |
| Invalid file format (missing headers, wrong columns) | Skip entire file |

Check the **SpacesEDU import report** after each import to see which records were skipped and why.

---

## Technical questions

**Q: Do I need to install Python or anything else?**

No. The `.exe` file is a self-contained executable that includes Python, all libraries, and the configuration files. Nothing else needs to be installed.

**Q: Where are SFTP credentials stored?**

Credentials are stored in the Windows Credential Manager (Windows) or the equivalent OS keychain — never in a plain text file. The configuration file (`~/.districtsync/config.json`) stores only non-sensitive settings like host and port.

**Q: Can I run this on multiple districts from the same server?**

Yes, by creating separate scheduled tasks with different `--sis`, `--input`, and `--output` arguments. Contact SpacesEDU for multi-district setup guidance.

**Q: How do I update to a newer version?**

1. Download the new `.exe` from the [Releases page](https://github.com/myblueprint-spaces/DistrictSync/releases/latest)
2. Replace the existing `.exe` in `C:\DistrictSync\`
3. The scheduled task continues to work automatically — no reconfiguration needed

**Q: Is there a web-based UI?**

Yes — double-clicking `DistrictSync-windows.exe` (with no arguments) opens a browser-based UI at `http://localhost:8501`. It includes five pages: Setup Wizard, Convert, Run History, Mapping Editor, and Help & Docs. When run with `--sis`/`--input`/`--output` arguments (e.g. from Task Scheduler), it runs headlessly without opening a browser.

**Q: Can I customize field mappings without editing YAML?**

Yes. The **Mapping Editor** page in the web UI provides a step-by-step wizard for creating or modifying district configurations. It auto-detects column names from your files and saves a properly formatted YAML config.

**Q: Why is my SFTP host being rejected?**

For security, SFTP uploads are restricted to SpacesEDU servers only (sftp.ca.spacesedu.com, sftp.app.spacesedu.com, sftp.myblueprint.ca). Contact SpacesEDU support if you need a different host.

**Q: What does the record count drop warning mean?**

After each run, DistrictSync compares output against the previous run. If any entity (Students, Classes, etc.) dropped by more than 20%, a warning is logged. This usually means the GDE export was partial or corrupted — re-export from MyEdBC.

**Q: Where does DistrictSync store my settings, logs, and custom mappings?**

All runtime state is written to `~/.districtsync/` — which is
`C:\Users\<username>\.districtsync\` on Windows, `/home/<user>/.districtsync/` on Linux,
and `/Users/<user>/.districtsync/` on macOS. Specifically:

- `config.json` — wizard settings (input/output paths, SFTP host, schedule time).
- `etl_tool.log` — history of every run (wizard, scheduled, CLI). The Run History page reads this file.
- `mappings/*.yaml` — any district mapping you create in the Mapping Editor. These override the built-in configs if the file name matches.

Your SFTP password is stored in the OS credential manager (Windows Credential Manager / macOS Keychain / Linux Secret Service), never on disk in plain text.

You can back up `~/.districtsync/` to preserve your setup, or delete it to reset the tool to a fresh-install state. The `.exe` itself can live anywhere (Desktop, Program Files, USB stick) — it doesn't store anything next to itself.

**Q: I see a warning about `pkg_resources is deprecated` when I run the .exe from a terminal. Is that a problem?**

No. That warning comes from the Python packaging machinery that PyInstaller embeds in the binary — not from DistrictSync itself. It prints once at startup and is harmless. Partners who double-click the .exe never see it because there's no terminal window. It will go away once PyInstaller upgrades its internal bootstrap code.
