# Changelog

All notable changes to DistrictSync are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Per-release download links and auto-generated commit notes live on the
[GitHub Releases](https://github.com/myblueprint-spaces/DistrictSync/releases) page.

## [Unreleased]

### Changed

- **Delivering to SpacesEDU now sends the files already on disk — never a
  rebuild.** Every "Deliver to SpacesEDU" action — the post-build deliver, the
  failed-delivery retry, and a new standalone "Deliver the files in your output
  folder" card on Convert — uploads the already-committed output CSVs straight
  from your output folder. Delivering never re-runs the conversion, so what you
  reviewed is exactly what ships, and a delivery can no longer silently
  re-acknowledge a large roster-drop warning (the old rebuild-with-auto-ack
  path is removed). The deliver confirmation shows labelled Server / Folder
  facts plus an honest freshness line ("Files last built …") derived from the
  files on disk; deliveries record in Run History as "Delivered saved files"
  (or "Delivery failed") without pretending to be builds, and Home's tiles
  keep showing the delivered build's real counts. (plan 0034 slice 2)
- **Mapping is now honest about the nightly schedule when you switch districts.**
  The post-Apply confirmation no longer claims "your folders and schedule are
  unchanged" — it says "Your folders are unchanged." and, when a registered
  nightly schedule exists (or can't be confirmed but is expected), shows a
  warning that the schedule still uses (or may still use) the old district,
  with an "Open Settings" button that routes to the Settings Save/re-register
  flow. Schedule truth comes from the real off-thread Windows read-back —
  never asserted from the saved setting alone. (plan 0034 slice 1)

## [3.6.0] - 2026-07-15

The professional-grade desktop release: the "Branded Professional" design system
across the whole app, no more console-window flashes, and delivery you can trust.

### Added

- **A formal DistrictSync design system.** The app now follows one documented
  design language — navy navigation rail with the myBlueprint mark on the window
  title bar, calm white content, one clear primary action per screen, and
  status told through tinted verdict banners with plain words (never colour
  alone). Every colour pairing is contrast-checked for accessibility as part of
  the test suite. (#54)
- **Pick the daily run time from a clock.** The schedule's run-time field now
  opens a time picker; typing a time still works. (#53)
- **App version visible in the app.** The navigation rail shows the running
  version, so support conversations can start with facts. (#54)

### Changed

- **No more flashing console windows.** Clicking around the app no longer pops
  brief black command windows — every background Windows check (schedule
  read-back, registration, elevation) now runs fully hidden. (#53)
- **Setup order matches how you think.** The first-run wizard now leads with
  your District, then Folders, then Delivery, then Schedule; the Settings page
  puts Folders & District on top. (#53)
- **"Test connection" now tests what delivery actually needs.** Signing in is
  the test; an upload-only SpacesEDU account that refuses folder *listing* is
  reported as success-with-a-note instead of a false failure — so Test and a
  real delivery can no longer contradict each other. (#53)
- **Every screen opens with a slim page header** instead of a large decorative
  banner; the health verdict is the first and loudest thing on Home. (#54)

### Fixed

- **Convert's "Deliver to SpacesEDU" no longer appears without a stored
  credential.** If delivery is configured but no password is saved on the
  Windows account, a calm note routes you to Setup instead of offering an
  upload that would fail. (#53)
- **An empty output folder can never report "delivered".** Attempting a
  delivery with no CSVs now fails loudly instead of silently claiming success.
  (#53)

## [3.5.0] - 2026-07-15

The Flet UI trust & professionalism redesign (plan 0029) plus SD60 email
standardization (plan 0030).

### Added

- **Guided 5-step Setup wizard.** First-run setup is now a stepped wizard —
  Folders → District → Delivery → Schedule → a finish screen that states what it
  actually checked (e.g. "we tested the connection to `<host>` just now and it
  worked"). The Schedule and Delivery steps are optional and can be set up later.
  Once finished, Setup becomes a flat **Settings** page with a single Save.
- **Durable run-history database.** Run history now lives in a dedicated
  `history.db` in the app-data folder instead of being parsed back out of the
  diagnostic log. Manual (Convert), scheduled, and CLI runs are all recorded and
  tagged by how they were triggered; the log stays for diagnostics only.
- **Schedule read-back.** DistrictSync now reads the real Windows scheduled task
  back and reports it honestly — registered and next-run time when it can confirm
  it, "not scheduled" only when it's genuinely absent, and "couldn't confirm right
  now" rather than guessing. An **Unregister** action removes the schedule.
- **District-configurable generated student emails.** A district config can now
  build the student login email from a template that optionally strips punctuation
  from names (`sanitize`) and derives a date part — e.g. a 2-digit year — from a
  source date column (`derived_dates`). Opt-in per district; every other district's
  email output is byte-for-byte unchanged.

### Changed

- **Turning on the nightly schedule now uses one Windows permission prompt (UAC)**
  for that step only, instead of requiring the whole app to be launched as
  administrator. The app itself never runs elevated.
- **App data moved to the standard per-OS location.** Settings, logs, and run
  history now live in `%LOCALAPPDATA%\DistrictSync` (Windows) /
  `~/Library/Application Support/DistrictSync` (macOS) /
  `~/.local/share/DistrictSync` (Linux). Existing installs are migrated
  automatically on first run, with a `MOVED.txt` note left in the old
  `~/.districtsync` folder (nothing is deleted).
- **Honest, verified status throughout.** The SFTP test now names exactly what it
  checked and when (and no longer writes a typed password to the credential store
  before testing); Convert names the output folder, offers "Open folder", and
  refuses to run without an explicit district; the navigation order is now fixed;
  the window/taskbar/exe show the myBlueprint brand icon; Exit closes the app and
  Enter submits forms.
- **SD60 (Peace River North): student emails standardized.** Every active student
  now gets a generated `firstname+lastname+admission-year@learn60.ca` login
  (previously the file's raw address across 70+ domains — many not deliverable to
  SpacesEDU); students are rostered under their **Home School Number**; and
  `Active No Primary` enrolments are excluded. Note for the district: this
  standardizes ~59% of students onto a new login address (see the SpacesEDU
  onboarding notes).

### Fixed

- **Stale in-app state.** Switching district, completing setup, and other changes
  now reflect immediately across Home, Run History, Mapping, and Help without a
  restart; the schedule status shown in the app is read back from Windows rather
  than trusted from a saved flag.
- **Run history starts fresh with this update.** Earlier history existed only in
  the diagnostic log (which mixed real runs with internal test entries), so it is
  **not** carried over; Run History fills in again from your next conversion. Your
  previous `etl_tool.log` is left untouched.

### Security

- The per-operation schedule-elevation handshake passes the Windows password to
  the elevated step through an encrypted (DPAPI, current-user-scoped) channel that
  fails closed if the prompt is approved under a different account, and never logs
  it or writes it to disk in plain text. Stored run history carries only a bounded
  error category — detailed error text stays in the local diagnostic log.

## [3.4.0] - 2026-07-08

The Flet 1.0 desktop rebuild (plan 0013) — Streamlit removed — plus the SD60
district config.

### Added

- **SD60 (Peace River North) district config.** New `sd60myedbc` mapping —
  guardians-only family import and an opt-in cross-enrollment collapse that
  rosters dual-school students once under their home school. (#46)

### Changed

- **Flet is now the only UI; the public executable is the Flet-default build.**
  Launching `DistrictSync` with no arguments opens the native Flet desktop app
  (double-click on Windows); running it with `--sis`/`--input`/`--output` uses the
  CLI, byte-for-byte unchanged. The GitHub Release now ships one Flet-default exe
  per OS (Windows/Linux/macOS) plus `SHA256SUMS.txt` — a single binary that is
  both the UI and the CLI. (#45)

### Removed

- **The Streamlit web UI (`src/ui/`) and the `streamlit` dependency.** The Flet
  desktop UI (`src/ui_flet/`) fully replaces it; the browser-based Streamlit app,
  its Playwright smoke tests, and the separate Streamlit release executables are
  gone. The ETL/CLI core is unchanged. (#45)

### Fixed

- **The built executable now reports the real release from `--version`.** Each
  release build stamps the pushed git tag into a bundled `src/_version.py`, which
  `app_version()` reads first — a frozen PyInstaller build ships no package
  metadata, so the previous `importlib`-only lookup always reported `dev`. The UI
  and the CLI now share the one `app_version()` lookup (tag → package metadata →
  `dev`). Preserves the fix from PR #42 through the Flet packaging rework.

## [3.3.3] - 2026-06-25

### Changed

- **Graceful shutdown.** Idle watchdog, Exit controls, and a single-instance
  guard for the desktop app. (#43)

## [3.3.2] - 2026-06-25

### Fixed

- **Report the real `--version` in built executables.** (#42)

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
