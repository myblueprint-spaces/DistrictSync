# Changelog

All notable changes to DistrictSync are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Per-release download links and auto-generated commit notes live on the
[GitHub Releases](https://github.com/myblueprint-spaces/DistrictSync/releases) page.

## [Unreleased]

### Fixed
- `--version` now reports the real release version. The build stamps the git tag into the executable (`src/_version.py`); previously the frozen exe reported `dev` because it couldn't read installed package metadata.

## [3.3.1] - 2026-06-25

Fixes the unattended Windows scheduling regression that blocked district rollout,
and makes its failures legible.

### Fixed

- **Unattended Windows scheduling ("Access is denied").** Registering the daily
  task to run *whether or not the user is logged on* failed — even when elevated —
  after v3.3.0 moved registration to `schtasks /Create /XML` (the credentials in
  the XML broke the run-as handoff). Registration now uses PowerShell
  `Register-ScheduledTask` with an explicit `Password`-logon principal, restoring
  unattended scheduling. The one-time schedule setup must be run **as
  administrator** (creating an unattended task requires elevation).

### Changed

- **Readable scheduler errors + elevation-aware diagnostics.** A failed schedule
  registration now shows a clean one-line message instead of a raw PowerShell
  CLIXML blob, and the wizard no longer tells an already-elevated user to "run as
  administrator" — it distinguishes a missing-elevation, a rejected credential
  (Windows account password vs Windows Hello PIN / Microsoft-Account password),
  and a too-old Windows.

### Security

- The scheduled-task run-as password is no longer placed on the process command
  line. It is passed to PowerShell only through a child-process environment
  variable, never logged and never written to disk.

## [3.3.0] - 2026-06-24

Adds the SpacesEDU **StudentAttendance** export, unifies the CLI and web-UI
conversion engines, and hardens output writes. The v3.2.0 **PreReg** default is
also restored — review the "Fixed" note before rolling out to a district.

### Added

- **SpacesEDU StudentAttendance export.** New `StudentAttendance.csv` output
  (first enabled for SD51) with a configurable Absence Date format (ISO
  `YYYY-MM-DD` by default).
- **Active-status resolution logging.** The students transformer now logs which
  signal — the enrolment status column vs the withdraw date — resolved each
  student's active status, making roster decisions easier to diagnose.
- Setup Wizard Step 1 now has a 📁 Browse button beside the input/output
  directory fields that opens the native folder picker (the text box still
  accepts manual entry/paste).

### Changed

- **Unified conversion engine.** The CLI (`python -m src.main`) and the Streamlit
  web UI now run conversion through one shared engine, locking byte-for-byte
  parity between their output CSVs.
- **Fail-loud field transforms + honest run status.** A failing field transform
  now blanks only the affected cell (or column) and records the error to a
  per-run `data_errors` summary surfaced in Run History and the Convert page,
  instead of silently dropping rows. A run with no usable required input now
  exits non-zero.
- **Stale entity CSVs are archived, not deleted.** Output-directory CSVs not
  produced by the current run are moved into `archive_<ts>/` (non-destructive,
  and excluded from SFTP upload) rather than removed.

### Fixed

- **PreReg students are included by default again.** Restores the default
  `active_values` to `["Active", "PreReg"]` — the Advanced CSV spec's expected
  `EnrollStatus` values. v3.2.0 had narrowed the default to `["Active"]`, which
  silently dropped pre-registered students from `Students.csv`, `Classes.csv`,
  and `Enrollments.csv` — a breaking change against the spec. The fix lives in
  code (`BaseTransformer.DEFAULT_ACTIVE_VALUES`); districts can still opt PreReg
  out — or add statuses such as `Active No Primary` — via
  `EnrollStatus.active_values`. The withdraw-date logic is unchanged (status wins
  when present; the date is only a fallback for rows with no status value).
- **Atomic `save_all` commit.** Output files are committed with a
  backup-and-restore step so a mid-commit failure rolls back and the output
  directory is never left torn.
- Windows scheduled-task registration now uses Task Scheduler XML instead of an
  inline `/TR` command, removing schtasks' 261-character limit (which blocked
  source-mode scheduling and very long install paths) and the brittle
  `cmd /c "cd /d …"` quoting.
- `StudentAttendance.csv` is written without the UTF-8 BOM.
- Setup Wizard folder picker keeps working when the window manager does not
  support `-topmost`.
- Streamlit no longer logs noisy Arrow tracebacks for display columns that mix
  numbers with a string sentinel (coerced to a uniform string).

## [3.2.0] - 2026-06-15

Config-driven active-student filtering. This release changes the **default**
roster contents, so review the "Changed" notes before rolling out to a district.

### Changed

- **Active-student roster is now config-driven.** A student is included only
  when their enrolment status is in `active_values` (default `["Active"]`).
  Districts can override per-config via `EnrollStatus.active_values`.
- **PreReg (and other non-active) students are now excluded by default**, matching
  the partner FAQ. Previously some non-active statuses could appear in `Students.csv`.
- **Enrolment status value now wins over the withdraw date.** The previous hard
  withdraw-date override was dropped — a student whose status is active is kept
  even if a stale past withdraw date is present.

### Fixed

- **Zero-orphan enrollments.** Homeroom + subject student enrollment rows and
  auto-generated homeroom classes are filtered to the active roster, so no row in
  `Enrollments.csv` or `Classes.csv` references a student missing from
  `Students.csv`. Teacher enrollments are not filtered.

### Internal

- Extracted `TransformContext.get_demo_student_col()` to de-duplicate the
  student-id-column resolution shared by the Classes and Enrollments transformers.

## [3.1.1] - 2026-06-05

- Unattended scheduled-task + SFTP hardening (run-as account/password, redacted
  logging, host allowlist). See the GitHub release for details.

## [3.1.0] - 2026-06-04

- See the GitHub release for details.

## [3.0.0] - 2026-04-16

- myBlueprint+ output tiers (`CourseInfo`, `StudentCourses`) and `enabled_entities`
  output targeting. See the GitHub release for details.

[3.3.0]: https://github.com/myblueprint-spaces/DistrictSync/releases/tag/v3.3.0
[3.2.0]: https://github.com/myblueprint-spaces/DistrictSync/releases/tag/v3.2.0
[3.1.1]: https://github.com/myblueprint-spaces/DistrictSync/releases/tag/v3.1.1
[3.1.0]: https://github.com/myblueprint-spaces/DistrictSync/releases/tag/v3.1.0
[3.0.0]: https://github.com/myblueprint-spaces/DistrictSync/releases/tag/v3.0.0
