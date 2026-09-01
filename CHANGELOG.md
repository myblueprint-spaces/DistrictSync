# Changelog

All notable changes to DistrictSync are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Per-release download links and auto-generated commit notes live on the
[GitHub Releases](https://github.com/myblueprint-spaces/DistrictSync/releases) page.

## [Unreleased]

### Added

- **Seven phase-2 migration district configurations ship in the program:**
  SD10 (Arrow Lakes — Students + course feeds, generated student emails),
  SD27 (Cariboo-Chilcotin) and SD38 (Richmond) — full myBlueprint+ tier scoped
  to grades 8-12, SD67 (Okanagan Skaha), SD69 (Qualicum — course feeds from
  grade 9), SD71 (Comox Valley), and SD75 (Mission) — full myBlueprint+ tier.
  All assume standard MyEd BC file naming until real extracts are checked.
  SD27/SD38 are the first configurations to scope the student roster by grade
  (`student_rostering_grades`): students outside grades 8-12 don't reach any
  output CSV for those districts. Note for SD75: a district that created its
  own `sd75myedbc` mapping with the retired v2.x Mapping Editor still has that
  file in its per-user mappings folder, and it takes precedence over the new
  built-in one (the log names the shadowing file) — remove the local copy to
  use the shipped configuration.

### Fixed

- **myBlueprint+ transcript data was silently never imported.** `CourseInfo.csv`
  and `StudentCourses.csv` were delivered inside the rostering zip, and SpacesEDU
  imports nothing from `StudentCourses.csv` when it arrives that way — **without
  raising an error**, so the delivery looked healthy in DistrictSync and in the
  run history while no course data ever landed. Both files are now uploaded as
  standalone CSVs into the same remote folder as the zip, alongside
  `StudentAttendance.csv`, which already worked this way. Confirmed against the
  live importer in both shapes on 2026-08-27.

  **Affects myBlueprint+ districts only**, on any version up to and including
  v3.13.0. The five rostering CSVs (`Students`, `Staff`, `Family`, `Classes`,
  `Enrollments`) still ship together in `districtsync_<district>_<date>.zip` under
  the unchanged name, so **districts that produce only rostering CSVs see no
  change at all**. Affected districts should expect their first delivery after
  upgrading to import a full course history that had never arrived before.

- **Runs that never attempted a delivery no longer read as "Delivered".** A run
  that completed with data warnings but had no SFTP delivery configured into the
  nightly task showed "Delivered · N data warnings" in Run History and "the sync
  still delivered" on Home. Both now say "Completed" and that the files were
  written to the output folder — "Delivered" appears only when an upload
  actually succeeded.

- **Saving delivery settings while the nightly schedule was still being set up
  silently produced a task without delivery.** If you saved SFTP credentials in
  the short window while a schedule registration was still applying (its Windows
  permission prompt up), the save concluded there was no task to update, and the
  registration then completed without `--sftp` — the nightly built the roster
  but never uploaded it, while Settings showed delivery on. Both save notes now
  say the change isn't included yet and to save again once the schedule
  finishes, and a completed registration warns when settings changed while it
  was applying.

- **The data quality report no longer flags deliberately-blank columns.**
  Columns a district's configuration fixes to blank (a withheld Date of Birth,
  "Literacy Test Completed", CourseInfo's unused descriptor columns) were listed
  as "missing/empty" with 100%-missing warnings on every run, burying the real
  findings. Blank-by-design columns are now excluded from the missing-field
  check; duplicate and orphan checks are unchanged.

- **Course selections no longer ship without a course name.** Rows sourced from
  `StudentCourseSelection` looked their course's name up with a raw-code,
  exact-only, same-school match — so a course cataloged under a padded code or
  at a different school came through nameless (about a quarter of the real
  sample's transcript rows). The selection pass now resolves names through the
  same exact-then-7-character-prefix chain the history pass has always used;
  a name stays blank only when the course truly has no catalog entry.

- **BC letter and status marks are no longer counted as per-row data errors.**
  A district whose course history carries proficiency-scale marks (`PRF`, `DEV`,
  `EXT`, `EMG`), letter grades, or administrative statuses saw every such row
  counted as a "data warning" — tens of thousands per run on real data. These
  are recognized BC mark shapes now: logged once as counts, never flagged as
  errors. Only a value that reads as neither a number nor a letter/status code
  (data landed in the wrong column) still counts as a data error.

### Changed

- **Hyphen runs in course codes are treated as MyEd BC's fixed-width padding,
  not as a course "flavor".** Two visible fixes on real data: (1) distinct
  module courses that share a base code (`MADGE09---EX1`, `---EX2`, … — each a
  different ADST course in the catalog) are no longer collapsed into one
  transcript code, which removed thousands of duplicate `StudentCourses.csv`
  rows; (2) trailing padding is stripped everywhere (`MAPPR12---` →
  `MAPPR12`), so `CourseInfo.csv` carries clean codes, padded and unpadded
  rows of the same course de-duplicate, and transcript rows exact-match the
  catalog instead of relying on the 7-character-prefix fallback. Internal
  hyphen runs (`MSC--09`) are positional and are never touched. The `HUB` /
  `HOL` / `DL` delivery-mode flavors still truncate exactly as before.

- **Standing Granted (`SG`) and Transfer Standing (`TS`) marks now earn course
  credits on grade-10+ courses.** Both codes grant credit per the BC transcript
  legend; previously every non-numeric mark scored as not-passing, so `SG`/`TS`
  rows shipped with empty `Credits Earned` in `StudentCourses.csv`. Grade 9 and
  below stay non-credit, and a course code whose grade can't be read stays
  not-passing. Affects myBlueprint+ districts whose history carries these codes;
  a course selection matching an `SG`/`TS` history row is now deduplicated the
  same way as after a numeric pass.

- **A course-only configuration (`mbponly`) now delivers two standalone files and
  no zip**, since the archive is built only when a run produces at least one
  rostering CSV. Output CSVs themselves — columns, order, filenames and encodings
  — are untouched; only the delivery envelope changed (output contract `2.0.0`).

## [3.13.0] - 2026-08-18

Fixes a rostering bug that silently emptied `Enrollments.csv` for any district
whose export has no student timetable, plus five interface fixes found by a
hands-on walkthrough of the built program. **Output changes only for districts
with an empty or absent `StudentSchedule.txt`** — for everyone else the CSVs are
byte-identical.

**This release ran the certification pass.** The audit, product-gap review and
26-row manual QA walkthrough that `docs/claugentic-DECISIONS.md` (D-0037-6)
reserves were all completed against this build — the first release since 3.8.x
to do so, after three consecutive releases shipped ahead of it.

### Fixed

- **Homeroom enrollments no longer disappear when there is no student timetable.**
  If a district's export had an empty or missing `StudentSchedule.txt`,
  DistrictSync produced a completely empty `Enrollments.csv` — including the
  homeroom rows, which do not come from the timetable at all and should always
  have shipped. Districts rostering by homeroom only (SD83 is the first) were
  getting classes with no students in them. Co-teacher rows from
  `ClassInformationEnh.txt` were dropped by the same fault. All three kinds of
  enrollment are now built regardless of whether a timetable is present.

  Districts whose export *does* include a timetable are unaffected — the SD74
  reference output is byte-identical.

- **Convert no longer asks homeroom-only districts for files they don't need.**
  A district that rosters by homeroom only was warned that `StudentSchedule.txt`
  and `ClassInformationEnh.txt` were missing from its input folder, when neither
  contributes anything to its output. The files are still read when present.

- **Long messages stay inside their box.** Text in a status band ran past the
  coloured panel instead of wrapping onto a second line — most visibly on the
  delivery "Test connection" result and the "You're set up" summary. Every
  status band in the program wraps correctly now.

- **Continuing past the schedule step no longer breaks the schedule.** Pressing
  Continue while Windows was still asking permission abandoned the request
  half-finished. Worse, the step stayed marked "set up later" permanently — so
  going back and scheduling successfully still ended with a summary claiming the
  nightly sync was not set up. Continue is now unavailable until the permission
  prompt is answered, and a schedule confirmed as live clears the "later" mark.

- **The sync window fields say what they mean.** Renamed to "Sync starts (MM-DD)"
  and "Sync ends (MM-DD)", and their explanatory captions are no longer cut off
  mid-sentence. Under the old "Season starts/ends" labels an administrator could
  reasonably type the *shutdown* dates into fields that meant the *active*
  period, turning the nightly sync off for months with no warning.

- **A failed conversion now leaves a diagnostic trail.** The Convert screen
  deliberately shows a plain-language message rather than the raw error, but
  nothing was writing the technical detail to the log file either — so the
  screen's own "Open log folder" button led to a file with nothing useful in it.
  A conversion that fails because an output CSV is open in Excel is the case that
  exposed this.

### Added

- **Start over with a different address, part-way through setup.** The opening
  question ("who looks after this sync?") could only be answered once: after
  that, the only place to change the address was in Settings, which you cannot
  reach until setup is finished. A "Start over with a different address" link now
  sits above the setup steps and returns you to that first question. It clears
  the stored address and district number and nothing else — your other settings,
  including any recovery copies, are untouched.

### Changed

- **MyEducationBC is spelled as one word and listed first.** The general-purpose
  mapping was buried fourth in every district list, behind three myBlueprint+
  options that far fewer districts want.

- **Clearer message when we don't recognise your district.** Every place the
  program says it cannot match your email or district number now gives the same
  answer: try one of the default configurations, and contact
  `hello@spacesedu.com` if your data doesn't fit it. Previously three different
  screens gave three different answers, one of which implied a custom mapping was
  already being built.

## [3.12.0] - 2026-08-17

Adds SD83 (North Okanagan-Shuswap) as an eighth district config, plus two new
opt-in `global_config` keys that let a district license a grade subset for
class- and student-level rostering. Also fixes a studentless duplicate-class
bug that could affect every district. **No output change for any district
that doesn't set the new keys** — SD83 is the only shipped config that does.

**Released ahead of the certification pass by owner decision (2026-08-17).**
The audit + product-gap + QA-checklist walk that `docs/claugentic-DECISIONS.md`
(D-0037-6) reserves has **not** run against this build — the third consecutive
release to skip it; see that decision log for what this release is and is not
evidence of.

### Added

- **SD83 (North Okanagan-Shuswap) district config.** New `sd83myedbc` mapping opts
  into the full myBlueprint+ tier (rostering plus CourseInfo/StudentCourses),
  extends self-contained homeroom classes through grade 8, and starts
  course/transcript data at grade 9. Date of Birth is withheld from the output
  (the column stays, every value is blank). Standard MyEd BC file naming is
  assumed for now, pending real GDE samples from the district.
- **Opt-in grade-scoped rostering.** Two new district-config settings let a
  config restrict rostering to a licensed subset of grades:
  `class_rostering_grades` bounds which grades get class rostering (homeroom,
  timetable, and blended classes together); `student_rostering_grades` is the
  outer bound — which grades' students reach the output at all, narrowing
  Students, Family, Classes, Enrollments, and StudentCourses together. Both are
  absent by default, so every existing district's output is unchanged. SD83 is
  the first config to use one of them (`class_rostering_grades: "homeroom"`,
  shorthand for "classes only for the grades that get a homeroom" — grades 9-12
  still appear in `Students.csv` so their myBlueprint+ transcripts work, just
  without class enrollments).

### Changed

- **Empty duplicate classes are no longer sent.** DistrictSync could send SpacesEDU a
  blended class that had a teacher and no students at all. It happened when one teacher
  ran two split-grade sections at the same period and *every* pupil involved was in a
  grade that gets rostered through their homeroom — so the blended class it created
  could never contain anybody. Those pupils were, and still are, correctly rostered in
  their homeroom classes; the blended class beside them was a duplicate with nobody in
  it. It is no longer produced, and neither is its teacher enrollment row.

  This rule already applied to districts that had opted into grade-scoped class
  rostering. It now applies to every district.

  A blend that mixes a homeroom grade with a timetabled grade is **unaffected** — it
  still ships and still carries its timetabled pupils. Class names are unchanged.

  **What districts should expect on the first run after upgrading.** Class and
  enrollment counts drop by the number of these empty classes, once. That may trip
  DistrictSync's own anomaly check (it warns on a >20% drop), which shows up in three
  places:

  - **Convert** (manual run) — the run stops and asks an administrator to acknowledge
    the anomaly **before anything is written**. This is existing behaviour working
    correctly, but it means a manual conversion will pause until someone ticks the box.
  - **Home** — the nightly run's health verdict shows a WARNING once.
  - **Run History** — that run is listed with the anomaly as its reason.

  Scheduled runs still complete and still deliver; only the verdict wording changes.
  Exit codes are unchanged.

  Output contract `1.1.0` (row-set change, MINOR — no column, order, filename or
  encoding changed, and nothing the partner had confirmed is affected). See
  `docs/developer/output-contract.md` and `docs/partner/how-classes-work.md`.

## [3.11.0] - 2026-08-05

The nightly-schedule machinery no longer runs PowerShell. **No CSV output changes** —
the SD74 golden and every district contract test pass untouched; this release is
internals plus three interface fixes.

**Why it exists:** on 2026-08-04 Bitdefender's behaviour engine blocked DistrictSync
on a district machine. What it saw was the app launching `powershell.exe` with a
base64-encoded command, under an administrator prompt, to create a scheduled task —
a sequence that is legitimate here and also exactly what malware does to make itself
persistent. That whole sequence is gone.

### Changed

- **Scheduling talks to Windows directly.** Creating, reading, updating and removing
  the nightly task now use the Windows Task Scheduler programming interface in-process
  — the same one PowerShell's own commands drive — instead of launching PowerShell.
  DistrictSync starts **no child programs at all** for scheduling now, and both
  `powershell.exe` and `schtasks.exe` were removed from the list of Windows programs
  the app is permitted to launch.

  Every existing guarantee was carried across and re-checked: no catch-up run after a
  server was off, tasks still run on battery, the two-hour run limit, the
  stored-password logon that keeps the nightly working while you are logged off with
  network access for delivery, and the honest "we could not confirm" state that never
  claims a schedule is missing when the check itself failed.

- **The administrator prompt now names DistrictSync** instead of Windows PowerShell,
  because the app performs the privileged step itself. Your password still travels
  only inside an encrypted file that only your Windows account can open, and a prompt
  approved by a *different* administrator still safely refuses.

- **Checking the schedule is quicker and quieter.** That check runs almost every time
  you change screens; each one used to start a PowerShell process.

### Fixed

- **The window contents stay centred when you maximise.** They were centred within a
  fixed-width column that stopped growing, so a maximised window left everything
  hugging the left.
- **The email box no longer suggests a `.bc.ca` address.** The example presupposed a
  British Columbia domain, which is wrong for partners elsewhere; the field is now
  empty and the label carries the instruction.

### Known issues

- **Antivirus may still flag the download.** This release removes the *scheduling*
  behaviour that was flagged. The remaining pattern — a single-file program that
  unpacks itself into a temporary folder on launch, and extracts the window component
  into your user profile the first time it runs — is unchanged, and is what the coming
  installer removes. If the window ever fails to open after an antivirus block, delete
  `%USERPROFILE%\.flet\client\` and start the app again. **Your nightly scheduled sync
  is unaffected either way — it runs the command-line path and never opens a window.**
- **Keep the program somewhere permanent.** A single-file program registers the
  scheduled task against wherever it currently sits, so a copy left in `Downloads` (or
  anywhere a server's cleanup touches) can leave the nightly pointing at a file that is
  no longer there. The app warns when it detects this. The installer will make it moot.
- **Re-registering a schedule with the password box empty** reports that you should run
  as administrator, when what it should say is "enter your Windows password to keep this
  running unattended". Removing the schedule and creating it again with the password
  works. Unchanged from earlier releases.

### Notes for anyone reading the code

Three facts about the Windows interface were established by probing a live Task
Scheduler, because no documentation states them, and each would have been a silent
field defect: the real error status hides behind a generic wrapper; the "never ran"
placeholder date is `1999-11-30` and not the value the PowerShell commands report; and
the returned times are local wall-clock stamped misleadingly as UTC. The project's
continuous integration caught two further defects that the Windows development machine
structurally could not see.

**Not certified.** The audit and quality walk this project reserves before a release
have not been run against this build (see `docs/claugentic-DECISIONS.md`, 2026-08-05).
What *did* run: the full automated suite on three operating systems, and an owner walk
of the real scheduling paths on a real machine.

## [3.10.1] - 2026-08-04

Fixes a bug that made the **Continue button on the launch page do nothing**, found by
owner field-testing. Present since 3.9.0. **No CSV output changes.**

### Fixed

- **Clicking Continue on the launch page did nothing.** Clicking the button first
  moved focus off the email field, and the resulting blur rebuilt the card — including
  the button being pressed — between the mouse going down and coming back up. The
  press was delivered to a control that no longer existed, so nothing happened, every
  time. Pressing **Enter** always worked, because that path never blurs first.

  Blur handlers now update the inline error and the district-number note in place, and
  repaint only when the text actually changes, so a click is never disturbed.
- **The same bug on Home's "who looks after this sync" card** — its Save button lost
  clicks the same way, for the same reason. It was less visible only because that field
  is not auto-focused, so an admin who never clicked into it never triggered it.
- **The Continue button now re-checks its enabled state on blur**, not only on
  keystrokes, so a value that arrives without a change event (some autofill and paste
  paths) can no longer leave the button greyed out.

### Notes for anyone reading the code

The existing tests passed throughout, because they invoke `on_click` directly — which
skips focus, blur, and the frame the rebuild happened in. A scripted browser click
passed too, because a synthetic press and release land in one frame. Only a *held*
click reproduced it. The regression test added here pins the structural invariant a
unit test can hold: **a blur may not replace the controls its card is built from.**

## [3.10.0] - 2026-08-04

A focused follow-up to 3.9.0's front door, driven by owner field-testing of that
build. **No CSV output changes** — the SD74 golden and every district contract
test pass untouched; everything here is UI.

### Changed

- **District lists stay scoped to your district.** 3.9.0 shipped the scoping with
  a "Show all districts" row one click away on every list; that row is gone from
  all four surfaces (the setup wizard's District step, Settings → Folders &
  district, Convert, and Mapping). An admin whose address matches a district now
  sees that district's options and no other district's.

  What has *not* changed: nothing is withheld. Every mapping still ships inside
  the executable, an address that matches no district still shows all of them, and
  a broken or unclaimed mapping can still only ever *widen* a list — your own
  district can never disappear from the surface that edits it. The way to see
  every district again is to clear the stored address in **Settings → Who looks
  after this sync** (blank the field, Save); every list widens on the next visit.

  This narrows *visibility*, not access — the district domains it matches on are
  public, so typing one has never been a claim anybody checked.
- **The launch page is centred and asks one plain question.** It was the only
  surface in the app without a navigation rail and was still pinned to the left
  edge; it now sits in the middle of the window, the heading card and the form
  card share one width, and the field and its button run that full width. The
  explanation of how the email is matched, and the character counter under the
  field, are both gone — it asks for a work email and nothing else. The promise
  that the address stays on this computer is still made where it is being edited
  (Settings) and where the app interrupts you to ask for it (Home).

### Removed

- The `Show all districts` / `Showing all districts · Show only mine` list-scope
  row and its shared component, along with the internal `show_all` and
  `can_filter` plumbing behind it. Removed rather than disabled, so it cannot
  quietly return.

### Known issues

- **Some antivirus products flag the Windows exe as suspicious.** Bitdefender's
  Advanced Threat Control blocked it during field-testing of 3.9.0. The exe is an
  unsigned one-file PyInstaller build that unpacks itself into `%TEMP%` and, on
  first launch, extracts and starts the Flet desktop client from
  `%USERPROFILE%\.flet\` — behaviour that resembles what heuristic engines look
  for. If the client executable is quarantined, DistrictSync will fail to open a
  window on every later launch (the failure dialog names the log); deleting
  `%USERPROFILE%\.flet\client\` lets it re-extract from the executable, offline.
  **Your nightly scheduled sync is unaffected — it runs the command-line path and
  never opens a window.** Code signing and a non-self-extracting installer are the
  real fixes and are tracked in `docs/claugentic-ROADMAP.md`.

## [3.9.0] - 2026-07-31

Phase 1 of the front-door programme (plan 0038, nine slices, PRs #67–#75). The
first-run experience and the Home dashboard were both rewritten. **No CSV output
changes** — the SD74 golden and every district contract test pass untouched.

**Released ahead of the certification pass by owner decision (2026-07-31).** The
audit + product-gap + QA-checklist walk that `docs/claugentic-DECISIONS.md`
(D-0037-6) reserves for after Phase 2 has **not** run against this build; see that
decision log for what this release is and is not evidence of.

### Added

- **A launch page that asks who looks after this sync.** One work email, matched
  against the district's public staff domain, before the app opens. Every path
  leads into the app — a match, no match, a typo, skipping it entirely, or a crash
  on the page itself. The answer is changeable and clearable any time in Settings
  and echoed on Help; it is never sent anywhere.
- **District pickers scoped to your district.** Once an address is on file, the
  wizard, Settings, Convert and Mapping lists show just that district's options,
  with "Show all districts" one click away. A district is hidden only when some
  other district's list claims it, so a broken or unclaimed config only ever
  *widens* a list — your own district can never disappear from the surface that
  edits it.
- **Convert names the district this run will actually use.** A "This run: …" pill
  plus a one-click route to Mapping, shown only when the run's district differs
  from the saved one. A label, never a gate.
- **Home carries one roster-size number** on a healthy sync ("It included 4,812
  students"), so a suspiciously tiny sync cannot look fine. It names whatever the
  district actually produces — students, attendance rows or courses — and says
  nothing at all rather than guess when it cannot tell.
- **A quick-action strip on Home** (Convert now / Run History / Settings).

### Changed

- **Setup now happens on Home.** A new install opens straight into the wizard
  instead of a hero page pointing at another tab. The welcome line knows the
  difference between a brand-new install and one that has been running for months
  but never finished setup.
- **Home is slimmer after setup:** the metric-tile row is gone, replaced by one
  plain-language verdict, the size number, and the actions you would actually
  take. The "will it run again?" schedule card is unchanged.
- **The finish line saves before it hands you anywhere.** If saving fails, the
  summary stays on screen with an honest note and a retry, instead of silently
  bouncing you back to step 1.
- **Smaller downloads on Windows and macOS** — the packaged app drops an unused
  media engine (light Flet flavour).

### Fixed

- **An install that has synced for months is no longer greeted as new.** Run
  history has only been recorded since 3.5.0, so an upgrade from an earlier
  version arrives with an empty ledger; Home and Run History now say "No runs
  recorded yet" — a claim about the ledger — rather than "No sync has run yet".
- **No empty-state sentence names a nightly sync that was never set up.**
- **The roster-size number can no longer describe the wrong district.** Switching
  district in Mapping does not re-register the nightly task, and Convert can run a
  one-off district without saving it, so a stored run can predate the district
  now saved. The number is suppressed rather than computed from a mismatch.
- Attendance dates: the importer requires ISO `yyyy-MM-dd`, confirmed against the
  live importer (contract question Q1a). The `dd-MMM-yyyy` shape the published
  BC/Aspen document describes is **not** accepted.

### Documentation

- **`docs/developer/output-contract.md`** — a per-column contract for every file
  DistrictSync emits, with the honest provenance of each claim (confirmed against
  the live importer, or pending), a divergence register against the published
  partner docs, and three open owner questions each carrying the bench check that
  would settle it.

## [3.8.1] - 2026-07-27

A field-test patch. Owner testing of the packaged Windows exe surfaced a crash
the whole static gate suite is structurally blind to — the native folder
dialog — plus the first-mile gaps a brand-new district hits before the window
even opens. No CSV output changes; the SD74 golden and the district contract
tests pass untouched.

### Fixed

- **Clicking Browse no longer crashes the app when a previously saved folder
  is invalid.** A folder path stored with forward slashes (written by older
  releases) or a folder that no longer exists was handed straight to the
  Windows folder dialog, which rejects it (`0x80070057` / `0x80070002`) and
  took the whole session down with "The application encountered an error".
  Stored paths are now reduced to a provably-valid starting folder first — and
  when there isn't one, the dialog simply opens at the OS default. Any future
  dialog failure now degrades to "no folder chosen" instead of a crash.
- The packaging smoke script's failure diagnostic read the retired
  `~/.districtsync` log path; it now probes the real per-OS app-data location
  first.

### Added

- **Every launch now logs a one-line banner naming the running version and the
  data folder in use** — field forensics found the logs could not tell which
  exe version produced them.
- A committed pre-release QA checklist (`docs/developer/qa-checklist.md`) —
  a ~10-minute hands-on pass over the built exe (fresh profile, both Browse
  pickers, a fixture conversion, the upgrade-in-place path) — now a mandatory
  step before tagging. Exactly the scenarios CI cannot reach.
- The partner installation guide now walks through the Windows SmartScreen
  warning ("More info → Run anyway"), antivirus quarantine recovery, and sets
  the ~30-second first-launch expectation.

### Changed

- The product spec's host-key expectation now describes the fail-closed
  behaviour that actually shipped in 3.8.0 (an unpinned or unverifiable host
  refuses delivery; it is never accepted with just a warning).

## [3.8.0] - 2026-07-23

The partner self-serve release. A new **school-year sync window** means a
district can be set up once and left alone — the nightly sync runs during the
year, pauses over the summer, and picks up again every fall on its own, with
nothing to renew. Alongside it, a thorough standards audit (11 lenses across the
whole codebase, every finding independently re-checked before it was acted on)
plus a refreshed product spec drove a batch of security, privacy and
trustworthiness fixes. Nothing here changes the CSVs a district produces — the
SD74 golden and the 7-district output contract pass untouched throughout.

### Added

- **A school-year sync window — set it up once, and the nightly sync runs during
  the year and pauses over the summer, every year, on its own.** In the setup
  wizard's schedule step (and later in Settings) you can turn on a seasonal pause
  and pick the start and end dates — pre-filled from the district's school
  calendar (about two weeks before the year starts to a week or so after it
  ends), so the roster is in place for day one and the sync stops churning once
  school lets out, giving the SIS time to update. It recurs automatically every
  year with nothing to renew: the nightly task simply checks each night whether
  it's inside the window. Over the summer the home screen reads a calm "Paused
  for the summer — resumes Aug 11" (green, nothing wrong), and the "we expected a
  sync that didn't arrive" reminders correctly stay quiet while it's paused. Left
  off by default, so an install without a window keeps syncing year-round exactly
  as before.

### Security

- **Host-key pinning is now fail-closed, closing two bypasses.** The SFTP
  client no longer loads the machine's ordinary `~/.ssh/known_hosts` before
  applying the bundled pins — paramiko consults that store *first*, so a single
  user-writable line naming a SpacesEDU host silently defeated the pin added in
  3.7.0. And a missing or corrupt `config/known_hosts` no longer degrades to
  warn-and-accept: it refuses the connection. Either failure previously turned
  pinning off for all three hosts at once, invisibly, in a 2 a.m. run nobody
  watches. A password is never offered to an unverified server — the refusal
  happens during key exchange, before authentication.
- **A possible man-in-the-middle now reads as one.** A host-identity failure
  used to fall through to the generic "check the host, username, password and
  remote path, then try again" — inviting an admin to retype credentials at a
  possibly-impostor server. It now has two terminal categories of its own
  (identity *changed* vs identity *unverifiable*, which are different faults
  with different fixes), neither of which invites a retry, and neither of which
  leaks a host path, key blob or fingerprint.
- **Windows system binaries are invoked by absolute System32 path.** Schedule
  registration passes the district account's password to a PowerShell child via
  the environment; resolving `powershell`/`schtasks`/`icacls` by bare name let
  `CreateProcess` probe the calling executable's directory and the working
  directory first, so a planted binary in a group-writable install folder could
  have received that password. The elevation path already pinned the absolute
  path for exactly this reason — the rest of the scheduler now uses the same
  shared helper.

### Privacy

- **Raw student values can no longer reach the diagnostic log.** A failing
  field transform logged the offending source cell verbatim, and the
  unparseable-withdraw-date path logged up to ten raw values from whichever
  column the district config points at — one config edit away from a date of
  birth or a name, in the file a district would attach to a support ticket.
  Failures are now described by shape (type, length, character classes) instead
  of content, through a single reusable seam, with a test that scans for any
  future site trying to log a raw value again. Exception *messages* are treated
  as tainted too — a date parser echoes its input verbatim.

### Reliability

- **A run that produces nothing no longer reports success.** The write and
  deliver step was gated on there being outputs, so a night that produced zero
  files skipped saving, archiving and uploading, then logged "completed
  successfully" and stored a success record with every count at zero — while
  Task Scheduler showed green. The mirror case on the way in (no usable input)
  was already fail-loud; the unattended path now matches it.
- **A missing student export no longer ships orphan enrolments.** If the
  student file was empty but the timetable was fine, the roster was never
  published, the enrolment filter deliberately no-opped, the previous
  `Students.csv` was archived out of the upload, and SpacesEDU received classes
  and enrolments referencing students it had never heard of — the exact case
  the zero-orphan invariant exists to prevent.
- **Settings survive a crash — and survive a failed read.** `config.json` was
  rewritten in place with no temp file and no fsync, so a crash mid-write — or a
  read racing the truncate window — lost the district, both folders, the setup
  flag and every delivery setting, after which the nightly task kept running but
  stopped delivering. Writes now stage and atomically swap. A settings file that
  exists but cannot be read is distinguishable from one that is genuinely
  absent, so a configured admin is never told they are a new user. And a save
  that carries no setting you chose — the window-geometry save on app exit — can
  no longer overwrite settings it failed to read: the file is left byte-intact
  and self-heals on the next load. A save that *does* carry a setting you chose
  still replaces the file, but now copies the bytes it is replacing aside as
  `config.corrupt-<timestamp>.json` first, so nothing is lost without a recovery
  path. (Narrowing that second case further is tracked as a known residual.)
- **The manual "Convert now" path enforces the same delivery gate as the nightly
  run.** The roster-integrity refusal was wired into the scheduled and
  command-line paths but not the desktop one — so the path an admin uses
  precisely *when the nightly run looked wrong* still shipped enrolments for
  students that were never delivered, and recorded the night as a success. It
  now refuses before writing anything, leaving the last good output untouched.
- **"I've reviewed this — convert anyway" now applies only to the run it
  reviewed.** The acknowledgement carried no run identity, and the district
  picker stayed live while the warning was on screen, so an admin could review
  one district's shrink warning and have the approval apply to a different one.
- **A conversion's files can only ever be delivered under the district that built
  them.** The Deliver action re-read the district picker at the moment you
  clicked it, so changing district after a run — which the screen actively
  invites when it notices a mismatch — could send one district's student roster
  to SpacesEDU labelled as another's. Because most districts produce identically
  named files, that delivery would simply *succeed* under the wrong name.
  Changing district now withdraws the action and names which district built the
  files. Delivery is also no longer offered at all when the chosen district has
  nothing in the output folder to send, instead of failing with a "try again"
  message that could never succeed.
- **Only the files a run vouched for are delivered.** The upload zipped whatever
  `.csv` files happened to sit in the output folder, so a spreadsheet export or
  a backup CSV an admin parked there was uploaded to SpacesEDU. Delivery is now
  driven by an explicit manifest of what the run actually produced. Nothing is
  deleted — foreign files simply stay on disk.

### Documentation

- Reconciled six places where the docs described behavior the code does not
  have: the claim that three districts pin fixed academic dates (none do — the
  pins were removed in June), the first-run wizard's step order (District leads,
  then Folders), and the README's statement that log entries power Run History
  (it reads a durable run store). The README's screen list was also missing
  Home, the product's primary surface.

### Testing

- Pinned the derived academic Start/End dates end-to-end. The SD74 golden runs
  against a *frozen* config that still pins literal dates — deliberately, so the
  golden stays time-independent — which meant the auto-derived path every live
  district actually takes was asserted nowhere. Covered at two altitudes, with
  expected values read from the config rather than hardcoded.

## [3.7.0] - 2026-07-20

The pre-partner completion release: every open backlog item landed ahead of
district distribution — SFTP server-identity pinning (keys bundled, zero
setup), data-integrity hardening across the whole pipeline, the full 0035
polish batch, and a behavior-preserving internal refactor program — plus the
0034 trust batch (Mapping schedule honesty, deliver-from-disk, Settings Save
trustworthiness, and the false-green kill).

### Security

- **The SFTP upload now verifies the server's identity, not just its name.**
  Delivery checks the server's SSH host key against pinned keys in
  `config/known_hosts` (a per-district override in the DistrictSync app-data
  folder wins without a new release). A pinned-key mismatch hard-fails delivery
  with a clear "server identity changed" error — the man-in-the-middle case —
  and is never retried; hosts without a pinned key connect exactly as before,
  with a log warning pointing at the pinning file. Replaces the previous
  trust-on-first-use policy. (pre-partner batch W1a)
- **Transient delivery failures now retry.** A network blip during upload
  retries up to 3 attempts with 2s/4s backoff instead of failing the nightly
  run outright. Wrong-password and host-key failures are never retried
  (account-lockout / MITM safety); the exit-code contract is unchanged, and
  Setup's "Test connection" still answers immediately. A failed connect also
  no longer leaks the SSH client socket. (W1a)

### Added

- **The output-CSV contract is now test-pinned for ALL 7 launch district
  configs** (was 3): per-district fixtures mirror each district's real GDE
  header shape (synthetic rows) and exercise every quirk end-to-end — SD40's
  headerless schedule and ATT exclusions, SD54's status-column-less
  withdraw-date detection, SD60's guardian row-filters, sanitized learn60
  emails, home-school rostering, and cross-enrollment collapse. (pre-partner W2a)
- **Config version gate.** A mapping config whose major version differs from
  the supported range is rejected with an actionable error (an out-of-range
  config must not drive a student-data conversion); newer minor drift warns;
  and whenever a user-dir mapping file shadows a bundled one (the sanctioned
  hotfix path), the loader names both paths in the log — a stale override can
  no longer take effect invisibly. (W2c)
- **Missed-run warning on Home.** When the schedule read-back confirms a LIVE
  nightly task but no run has been recorded in the last 26 hours (and the run
  store is itself old enough that a run was genuinely expected — a day-one
  install is never falsely warned), Home shows "We expected a nightly sync that
  didn't arrive" with a route to Run History. A red failure always outranks
  this amber warning. (plan 0034 slice 4)
- **Run History shows where each run came from.** A new Source column reads
  "Nightly", "Manual", or "Command line" ("—" for older records), plus a muted
  "Different district: …" note when a run belongs to a district other than the
  active one. (plan 0034 slice 4)

### Changed

- **Trust-copy honesty pass (0035 polish).** Home and Run History claim only
  what was verified: "delivered to SpacesEDU" appears only when the upload
  actually succeeded; a local-only run reads "completed — files were written
  to your output folder"; "Your roster is syncing" is asserted only on a
  confirmed-LIVE schedule read-back (else "Your roster is up to date"); a
  failed sync is dated from its own record instead of "Last night's sync…".
  Fix routing is reason-aware: a failed delivery's button reads "Open
  Settings" and lands where the fix lives. (W3a)
- **Convert never dead-ends (0035 polish).** A fresh install leads with a
  routed "Finish setup first" card; error cards end with a concrete next step
  and a support path (raw PowerShell text demoted to a trailing "Details"
  clause); an amber note flags a per-run district pick that differs from the
  saved district; the whole form locks while a job runs — no dead clicks, no
  double-starts. (W3b)
- **The window remembers its size and position** (restored safely clamped to
  the current screen); the Setup attention dot refreshes immediately after
  scheduling or removing the nightly sync; Help gained an About block (version
  + copy buttons + release notes + prefilled PII-free support email) and every
  error card offers "Open log folder". Plainer language throughout: "Schedule
  nightly sync" / "Remove nightly sync". (W3c)
- **Artifact refactors (behavior-preserving; W4d).** One
  `MappingConfig.active_entities()` accessor serves every enabled-entities
  selection site; the Classes→Enrollments handoff is an explicit frozen
  `ClassArtifacts` bundle with a fail-loud ordering assertion; enrollment
  source builders return frames instead of mutating a shared list (row order
  byte-identical); dead helpers deleted and zip naming re-homed beside its
  only consumer.
- **Internal refactor wave (behavior-preserving; district output byte-identical
  by snapshot proof).** Field mappings apply through a typed Strategy with
  unknown `transform:` names rejected at config load with an actionable error;
  `BaseTransformer` slimmed by composition into focused helper modules; the
  blended-class detector is a plain service with a single-pass teacher index;
  duplicated grade-split/date-format/ID-normalization idioms collapsed to
  single sources; a config missing standard rostering entities now warns at
  load. (W4b2 + W4c)
- **Settings Save is now trustworthy about the nightly schedule.** Saving
  Settings can no longer silently convert an unattended nightly task (one
  registered with a Windows password, so it runs while signed out) into a
  logged-on-only one: the app remembers how the task was registered and pauses
  on an explicit choice — "Keep running when signed out — re-enter the Windows
  password" or "Continue — the sync will only run while signed in" (Cancel
  leaves the task untouched; the password itself is still never stored). The
  Save also compares against what was *actually registered* — so after
  switching districts in Mapping, opening Settings and pressing Save
  re-registers the task with the new district even with no field edits,
  exactly what the Mapping notice promises. An edited daily run time is saved
  even when no schedule is registered (it previously silently reverted), and
  the folders card's Save is now labelled "Save folders & district".
  (plan 0034 slice 3)
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

### Fixed

- **The Class "Name" mapping config now actually drives class naming.** The
  consumers read the spaced YAML authoring keys every mapping file emits, so
  the primary-teacher flag is live and districts with a renamed section column
  (SD60/SD74: "Section") now include the section letter — SD74-style names
  change from "Clark Music 10 2026" to "Clark Carol Music 10 (B) 2026"
  (partner-visible, made deliberately before partners depend on the names;
  SD74 golden regenerated — Classes.csv Name column only). StudentCourses
  source columns are now fully config-driven (the last Configurable-Columns
  debt), byte-identical for every bundled config. (pre-partner W4b1)
- **One platform-dispatch point for scheduling.** `src/scheduler` now exposes
  a `Scheduler` protocol with honest per-platform capability flags and a
  `get_scheduler()` factory; every UI caller goes through it instead of
  scattered platform branches (Windows security invariants untouched; cron
  register fails loud on a password it can't honor). Setup's delivery section
  finished the plain-language sweep: "Delivery to SpacesEDU", "Save delivery
  settings", "Couldn't connect to SpacesEDU"; the wizard finish banner's tone
  and words now always agree; classified schedule-error copy uses the
  "schedule the nightly sync" vocabulary; Convert's district-mismatch note no
  longer asserts a nightly sync that may not exist. (W4a + wave-3 panel)
- **Linux scheduling can no longer wipe other cron jobs.** A failed
  `crontab -l` read (e.g. permission denied) now aborts Register/Unregister
  with a loud error instead of rewriting the whole crontab from a blind read;
  classification is exit-code-first. Unregistering a missing entry reads as
  the calm "No schedule was registered — nothing changed". (pre-partner W2b)
- **Settings Save honesty, completed.** A Save whose schedule re-register is
  refused by the register flow's own gate (e.g. an invalid run time) no longer
  claims "updating the nightly schedule…" — both Save sites say the schedule
  wasn't updated and name the fix (`ReconcileOutcome.BLOCKED`). SFTP port
  typos now get "That port isn't a number" instead of the host-allowlist
  message. And registering the schedule, going Back, and enabling delivery
  before Finish gets an honest amber finish line instead of claiming tonight's
  run will deliver with a task baked without `--sftp`. (W2b)
- **Dev hygiene.** The logging fallback now rotates (5 MB × 3) instead of
  growing unbounded; the >50%-missing quality warning and its exactly-50%
  boundary are test-pinned; the flaky schedule-probe render-smoke test is
  deterministic; a new static guard fails CI on un-awaited async window calls;
  the throwaway Flet prototype spike is deleted. (W2d)
- **A vanishing roster file now raises the same alarm as a big drop.** An
  entity the district config produces that suddenly transforms to zero rows
  (or disappears entirely) fires the ANOMALY warning — on the CLI it warns and
  rides the run record; in the app it stops before writing until an admin
  acknowledges. An unreadable previous CSV warns loudly instead of silently
  skipping the check, and the expected-entity set derives from
  `enabled_entities` so another config's CSV sharing the folder never fires a
  false alarm. (pre-partner batch W1b)
- **Manual conversions archive stale entity CSVs** into `archive_<ts>/`
  exactly like scheduled runs — a stale file can no longer ride an SFTP zip
  after a manual convert. Two conversions landing in the same second can no
  longer collide on the atomic writer's staging/backup folders (unique
  suffixes; collisions fail loud), and a run that was hard-killed mid-commit
  is detected on the next run: the leftover backup moves to
  `archive_<ts>_recovered/` with a warning (nothing deleted), and abandoned
  staging folders older than 7 days are swept. (W1b)
- **Silent data-quality failures in the transformers now surface loudly**
  (never failing the run): a student-email template naming a missing column
  records per-row data errors instead of invisibly blanking every email; a
  non-blank, non-numeric StudentCourses mark (letter grades, "Pass") records a
  data error instead of silently nulling earned credits (scoring unchanged);
  mixed-vintage input sets trigger a school-year disagreement warning naming
  every year found; and the active-roster filter logs an aggregate
  dropped-rows warning per entity (counts only — never student ids). (W1c)
- **Killed the false silence on early failures.** The early-exit failures inside
  the pipeline (input folder missing, district config missing or invalid) now
  write a failed run record to both the diagnostic log and Run History before
  exiting — Task Scheduler's exit code and Run History can no longer disagree
  about whether the nightly sync failed. Bounded categories only in the store;
  the exit-code contract is unchanged. (plan 0034 slice 4)

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
