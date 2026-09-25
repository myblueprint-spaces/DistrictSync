<!--
DRAFT — for a human to review and paste into the SpacesEDU Help Centre.
Source article being updated: https://help.spacesedu.com/en-ca/article/myedbc-districtsync-guide-mx56qo/
Drafted: 2026-08-13 · Revised: 2026-09-03 (self-service district setup, the signed Windows
build, the macOS .dmg, a required-input-files section, delivery contents) · Revised: 2026-09-14
(the MyEd BC reports named as REPORTS and the Enhanced variants called out, Class Information
Enhanced's second job, case-insensitive filename matching, the attendance source files; then the
owner's two wording tweaks and the email-addresses block) · Revised: 2026-09-23 (plan 0053 S0:
the missing-file bullet restated to what the code does today, and the false "either Emergency
Contact variant works" claim corrected — the basic report lacks the email and guardian columns) ·
Revised: 2026-09-24 (plan 0053 S4: a problem with the family-contact, course or attendance
export now leaves only that file out, so the missing-file bullet and the Enhanced-report
consequence were restated).
This file is NOT linked from anywhere and is not part of the built docs — it exists only
so a reviewer can compare it against the live article and copy the approved text across.

Scope principle (owner, 2026-09-03): this article is the PUBLIC, HIGH-LEVEL reference —
installing, configuring, the MyEd BC files it needs, and what it produces. Detailed
per-screen how-to and troubleshooting belong to the in-app Help surface (see the ROADMAP's
"In-app Help becomes the partner docs surface" plan), not here. It links to no in-repo doc,
because none is reachable from the Help Centre.

The button labels quoted in the self-service section are pinned to the app by
tests/test_creator_doc_copy_parity.py — if the app renames one, that test stays red until
this file is updated to match.

See "What changed from the published version" at the bottom for a reviewer-facing diff.
-->

# MyEdBC DistrictSync Guide

DistrictSync converts MyEducation BC General Data Extracts (GDEs) into the CSV format SpacesEDU and myBlueprint+ use to keep your roster up to date. It runs as a single program on a school district server — there's nothing to install separately, no database to set up, and no web address to visit.

This guide covers installing DistrictSync, running its setup, the MyEd BC files it needs, and what it produces.

---

## Installing DistrictSync

1. Download the file for your platform from the [Releases page](https://github.com/myblueprint-spaces/DistrictSync/releases/latest):
   - **Windows:** `DistrictSync-windows.exe`
   - **macOS:** `DistrictSync-macos.dmg` — open it and drag **DistrictSync** into **Applications**. The first time you open it, allow it under **System Settings › Privacy & Security**.
   - **Linux:** `DistrictSync-linux` — run `chmod +x` on it before the first run.
   - A command-line-only macOS build (`DistrictSync-macos`) is also published for Macs with no desktop.
2. Put the file anywhere convenient — a folder like `C:\DistrictSync\`, `/opt/districtsync/`, even a USB stick. Your settings, logs and run history are saved to your user profile, not next to the program file, so where you keep the program doesn't matter.

**The Windows download is code-signed.** Windows should name **myBlueprint Corp.** as the publisher rather than warning about an unknown one. You can confirm this yourself before running it: right-click the `.exe`, choose **Properties**, and open the **Digital Signatures** tab.

You may still see a blue **"Windows protected your PC"** screen for a while after a new version comes out. SmartScreen builds trust from how widely a specific file has been downloaded, which takes time even for a signed app — it doesn't mean anything is wrong. Click **More info**, check that the publisher reads **myBlueprint Corp.**, then click **Run anyway**. If the publisher shows as *unknown*, stop and contact us — a signed release should never show that. The first launch can take up to about 30 seconds while Windows unpacks the program; wait rather than double-clicking again.

The macOS and Linux downloads are not yet signed, so those platforms show their usual first-run prompts for an unsigned app.

For servers with no display (headless Linux, Docker, Windows Server Core), see *Headless configuration* below — SFTP delivery can be configured entirely from the command line.

---

## First launch: a desktop app, not a browser

Double-clicking the program opens **a native application window**, with a navigation menu along the left-hand side.

**Home · Convert · Run History · Setup · Mapping · Help**

### "Who looks after this sync?"

The very first thing you'll see is a short question: **"Who looks after this sync?"**, asking for one work email address. This isn't a login or an account — there's no password, and nothing is "unlocked" by answering. It exists purely so DistrictSync can recognize your district's email domain and pre-select the right district for you further into setup.

- If your address matches a district DistrictSync already supports, it tells you which one and lets you continue or correct it.
- If it doesn't recognize the domain, it says so calmly and lets you carry on — you'll pick your district yourself in a moment. You can also give your district number here: if DistrictSync ships a mapping for it, that district is offered to you; if not, you'll be able to set your district up yourself (see *Setting up a district that isn't listed yet*).
- If you're not the person who manages this — or would rather skip the question — there's a plain link to move on without answering ("I'm not the person who looks after this sync"). Nothing is saved either way.

You can add, change or remove this address later from **Setup → Settings**.

---

## Setup: a 5-step wizard

After the initial question, DistrictSync walks you through a five-step setup wizard on the **Setup** screen:

1. **Choose your district** — pick your district from a dropdown. If your email domain was recognized in the first step, your district may already be selected for you (a suggestion you can change, never a silent default) — otherwise nothing is pre-picked. If your district isn't listed, press **Set up my district** to build its mapping yourself in a few short questions (see the next section), or contact SpacesEDU support to have one built for you.
2. **Choose your folders** — the **input folder** where your MyEd BC GDE files land, and the **output folder** DistrictSync writes the converted CSV files to.
3. **Set up delivery** — enter the SFTP details SpacesEDU provided (host, username, password, remote path) and test the connection. This step is optional and can be set up later.
4. **Set a nightly schedule** — turn on an automatic daily run and pick a time (03:00 is a good default, after your overnight MyEd BC export finishes). This step is also optional — if you only plan to run conversions by hand from the **Convert** screen, you can skip it. This step also has an optional **seasonal pause**: pick a start and end date once, and the sync stops over the summer break and resumes on its own each fall, every year, with nothing to renew. While paused, Home says so in green — it isn't a warning.
5. **Finish** — a summary of what was actually set up (and what you skipped, if anything, so you know what's left). Finishing here is the one thing that marks setup complete.

If you set up your own district in step 1, the wizard gains one extra step, **Your files**, between Folders and Delivery — described in the next section.

**Turning on the nightly schedule shows one Windows permission prompt.** Registering a task that can run whether or not you're logged in needs administrator rights, so Windows asks you to approve that one step — click **Yes**. You don't need to run the whole program as administrator, and if you never turn on the schedule (ad-hoc runs only), you won't see this prompt at all.

Once you finish the wizard, the **Setup** screen (the rail item still says "Setup") turns into a flat **Settings** page where you can review or change your folders, district, schedule and delivery settings at any time, with a single **Save** that updates everything — including re-registering the nightly task if something that affects it changed.

---

## Setting up a district that isn't listed yet

DistrictSync ships ready-made mappings for many BC districts. If yours isn't one of them, you can set it up yourself, in the app, without waiting for a new release. The MyEd BC extract layout is the same from district to district; what differs is which files your district receives, what they're called, and which grades you roster — and those are the questions this setup asks.

**Two ways in:**

- During first-run setup, on the *Choose your district* step: **Set up my district**.
- On an installation that's already running, on the **Mapping** screen: **Set up a district that isn't listed**.

**Four short questions:**

1. **Your starting point** — the standard MyEd BC mapping closest to what your district sends: standard rostering (the five roster files), full myBlueprint+ (roster plus course files), core myBlueprint+ (students plus course files), or course files only. Everything you don't change is inherited from it — including fixes we ship to it in future versions.
2. **Your district** — district number, name and staff email domains. Typing the number fills in the name and domain for you; correct anything that's wrong. Nothing here is sent anywhere — it names your setup in the district list and lets the launch question recognize your colleagues' addresses.
3. **Which files to produce** — the CSV files DistrictSync should produce. Your starting point's usual set is pre-ticked.
4. **Which grades** — which grades are rostered at all, and which of those get one homeroom class instead of their timetable classes (typically the elementary grades). Or simply keep your starting point's grades.

**Then, "Your files":** one row for each MyEd BC file the mapping expects. If your district's file has a different name, pick it from the list of what's in your input folder or type it, then press **Save these file names**. Files the app can't see in the folder are listed plainly — a test carries on without them, and whatever they feed comes out empty.

Press **Run a test conversion**. It reads your real input folder, writes nothing and sends nothing, and shows how many rows each file would hold. If a column the mapping expects isn't in any of your files (usually a renamed header), it's listed under a heading that says the test still passed. When the counts look right, press **Save district mapping** — from then on this computer converts your district. Only after that can you continue to Delivery and Schedule.

**Afterwards:**

- Your district appears in every district list, marked **Added on this computer**.
- On **Mapping**, its card offers **Edit mapping** (which re-opens the same questions and *Your files*) and **Show mapping file**. Your whole mapping is one small text file, `sd<number>custom_mapping.yaml`, in the `mappings` folder inside DistrictSync's data folder in your user profile. Support may ask you for it. It survives upgrades.
- After you edit it, or after an update changes the standard mapping it builds on, DistrictSync asks for the test conversion again before the mapping can be re-activated or saved as your district. Nothing is changed behind your back.
- Discarding a mapping asks first, and is refused while it's the one this computer converts with — switch to another mapping first.

**What it doesn't do:** it doesn't change column names. If your district pre-processes its MyEd BC export and the column headers differ, the test conversion tells you which columns are missing — contact SpacesEDU support for a mapping built to your files. Having a mapping built for you remains available to every district.

---

## The six screens

### Home

A plain-language health check: is the sync working? A green banner means everything's fine, with the roster size for a quick sanity check. If something needs attention — a missing schedule, a failed run — Home names the problem in plain language and gives you a button to fix it. If setup hasn't been finished yet, Home shows the setup wizard itself instead of a dashboard.

### Convert

Run a conversion by hand at any time — separate from any nightly schedule. Choose your district and input folder, click **Convert now**, and DistrictSync builds the CSV files into your configured output folder. If SFTP delivery is set up, you can send the files to SpacesEDU from here too, either right after a conversion or on their own from files already on disk. If a run's record counts drop sharply compared to the last one, Convert pauses and asks you to confirm before writing anything.

### Run History

A read-only list of past runs — nightly, manual, and command-line — newest first, with plain-language status (not raw error text) and whether each run was delivered to SpacesEDU.

### Setup

Covered above: the first-run wizard, then the ongoing Settings page for folders, district, schedule and delivery.

### Mapping

Shows which district mapping is active and what it produces (which CSV files, from how many source files), and lets you switch to another — seeing what it would produce before you apply it. This is also where you set up a district that isn't listed and, for a mapping added on this computer, edit it, run its test conversion, and find its mapping file. The ready-made mappings that ship with DistrictSync can't be edited in the app — contact SpacesEDU support if one of those needs a change.

### Help

Links out to the SpacesEDU Help Centre and a one-click "email support" button (with the version number and your district name pre-filled in the subject line, so support doesn't have to ask). Both the Help Centre link and the support address are also shown as plain, selectable text, in case the "open" buttons don't do anything on a locked-down server without a browser or mail client configured. If you've told DistrictSync who looks after the sync, that address is shown here too — it stays on this computer and isn't sent anywhere.

---

## The MyEd BC files DistrictSync needs

DistrictSync reads the standard MyEducation BC General Data Extract (GDE) reports, dropped into your input folder as `.txt` files (tab- or comma-separated; `.csv` works too). Which ones you need depends on which output files your district produces.

**Ask your MyEd BC administrator for these six reports** — the **Enhanced** variant wherever one exists, because the Enhanced reports carry columns DistrictSync relies on (see *Why the Enhanced reports* below):

| MyEd BC report to request | Filename the standard mapping looks for | Feeds |
|---|---|---|
| Student Demographic Information Enhanced | `StudentDemographicInformation.txt` | Students, homeroom classes, enrollments |
| Staff Information Enhanced | `StaffInformationEnhanced.txt` | Staff, Classes |
| Emergency Contact Information Enhanced | `EmergencyContactInformation.txt` | Family |
| Student Schedule | `StudentSchedule.txt` | Classes, Enrollments |
| Course Information | `CourseInformation.txt` | Classes; CourseInfo and StudentCourses (myBlueprint+) |
| Class Information Enhanced | `ClassInformationEnh.txt` | Classes (blended, multi-grade) and Enrollments (co-teachers) |

Three more, only for districts on the myBlueprint+ tier or syncing attendance:

| MyEd BC report to request | Filename the standard mapping looks for | Feeds |
|---|---|---|
| Student Course History | `StudentCourseHistory.txt` | StudentCourses (myBlueprint+) |
| Student Course Selection | `StudentCourseSelection.txt` | StudentCourses (myBlueprint+) |
| Student Daily Absences / Student Period Absences Enhanced | set per district — there is no standard default | StudentAttendance (by arrangement) |

- **Attendance is arranged individually.** Unlike the rest, the attendance exports have no standard filename — districts that sync attendance receive them under their own names, set in that district's mapping, and the period band should be the **Enhanced** export where one exists. Talk to your SpacesEDU contact before turning attendance on.
- **Which of them your district needs.** **Standard rostering** needs the first five, with Class Information Enhanced strongly recommended (see below). **Full myBlueprint+** adds the two course-history files. **Core myBlueprint+** needs the demographic file plus Course Information and the two course-history files.
- **The filename is your district's, not ours.** The names above are the plain MyEd BC spellings the standard mapping looks for. Many districts receive the same reports under other names — `StaffInformation.txt`, `studentcourseselection.txt`, and, very commonly for the Enhanced reports, `StudentDemographicEnhanced.txt` and `EmergencyContactInformationEnhanced.txt`. That is normal and nothing needs renaming: a ready-made district mapping already knows your district's names, and a mapping you set up yourself lets you set them on the *Your files* step.
- **Spelling matters, capitalization doesn't.** DistrictSync finds your files whatever their case, so `studentschedule.txt` and `StudentSchedule.txt` both work. It won't guess at a genuinely different name — and if the name in your mapping isn't in the folder and two files there match it apart from case, the run stops and names them both rather than picking one.
- **What a missing or changed file does depends on the file.** An output is left out of that night's delivery when *every* file it reads is missing or empty (or every row is filtered out); the other outputs are still delivered, and DistrictSync shows a warning every night the output stays out — except for attendance rows, which are absent on any night without absences. When a file is present but a problem stops an output from being built — typically a different report saved under the usual filename, missing a column this district's mapping needs (a guardian flag, a grade, the home school) — what happens depends on that output. For students, staff, classes or enrollments the whole run stops: nothing new is sent to SpacesEDU that night, so it keeps the last successful sync, and those files stay in your output folder. For family contacts, courses, student courses or attendance, only that file is left out: everything else is delivered, and DistrictSync shows a warning every night until a correct export arrives (what SpacesEDU does with records linked by an earlier delivery of that file is pending confirmation). The whole run also stops when the student export produces no students, when no required file is usable, or when a file cannot be read. Other missing columns (typically a different report saved under the usual filename) are handled differently from column to column today: some stop the run; others leave rows out or leave a field blank; a few send more people instead — with no staff status column, for example, staff who have left are not filtered out and are sent along with current staff. Beyond a file left out, Home warns only if a file shrank by more than a fifth, so a quieter-than-usual night is also worth checking in Run History; contact support if the counts look wrong.
- **Why the Enhanced reports.** *Student Demographic Information Enhanced* carries an enrollment-status column; the basic report doesn't, and without one DistrictSync falls back to the withdrawal date to decide who is still active — so former students with no withdrawal date can slip through as active. *Class Information Enhanced* carries the **Primary Teacher** column, which is how teachers who never appear in the student timetable (co-taught and modular programs) get their class enrollments. With the basic report instead, blended classes are still detected, but those co-teachers are simply absent from `Enrollments.csv`; with no Class Information file at all, blended classes are not detected either. *Emergency Contact Information Enhanced* carries the **Email Address** column the standard mapping reads for each contact (and, in the Enhanced report, **Parent Auth / Guardian**, which a district mapping may filter on). The basic report has neither: with the standard mapping every contact then has a blank email, so none is sent and no `Family.csv` is produced — with a warning on screen every night until the Enhanced report is back; with a mapping that filters on the guardian column, `Family.csv` is left out of each night's delivery — the rest of the roster still syncs, with a warning on screen — until the Enhanced report is back. Request the Enhanced variant, and keep requesting the same report under the same filename.

**Check the email addresses before your first sync.** Email is the one field in these extracts SpacesEDU cannot work around, and a blank or mistyped one fails quietly — the run succeeds, the counts look plausible, and the person simply never appears. It is worth a look in MyEd BC now rather than a puzzled email in October.

- **Family contacts are the strict case.** A contact with no email address is not written to `Family.csv` at all, because SpacesEDU has no way to import one. So if guardian emails are blank in MyEd BC, sit in a field your extract doesn't include, or are entered on the wrong contact, those families are missing from SpacesEDU and nothing about the run will look wrong. A guardian's address is personal — it can't be derived from anything — so correcting it in MyEd BC is the only fix. The run log records how many contacts were left out, as a count only, which is the number to watch after your first sync.
- **Students are the forgiving case.** A student with no email address is still sent and still appears on the roster; they just can't be invited by email until one is added.
- **No student emails in MyEd BC? They can often be generated.** Where your students' addresses follow a predictable pattern — `{student number}@yourdistrict.ca`, or a first-name/surname combination — your mapping can build them from columns you already export, so the field doesn't have to be populated in MyEd BC at all. Several shipped district mappings already work this way. It has to be set up in the mapping rather than in the app, so email SpacesEDU support with the pattern your district uses and we'll build it into your configuration. The same trick can't rescue family contacts — there is no pattern for a guardian's personal address.

---

## What gets produced

Every configuration produces some or all of these CSV files, depending on which are enabled for your district:

| File | What it contains |
|---|---|
| `Students.csv` | Active student roster |
| `Staff.csv` | Teacher and staff roster |
| `Family.csv` | Parent/guardian contact links |
| `Classes.csv` | Homeroom, subject and blended classes |
| `Enrollments.csv` | Student and teacher class enrollments |
| `CourseInfo.csv` *(myBlueprint+ only)* | Course catalog |
| `StudentCourses.csv` *(myBlueprint+ only)* | Per-student course history |
| `StudentAttendance.csv` *(by arrangement)* | Daily or period absences, for districts that sync attendance |

Most districts use the standard 5-file rostering set. Some also use the myBlueprint+ tier, which adds the two course files. Your district mapping (chosen during setup, or on the Mapping screen) decides which files your installation produces — your SpacesEDU/myBlueprint+ contact can tell you which tier your district is on.

**How classes are built.** Subject classes come from the timetable, one per section. For the grades your mapping marks as homeroom grades (typically elementary), students get one homeroom class from their demographic record instead of timetable classes. Where one teacher teaches two or more grades in the same slot, those sections are merged into a single blended class.

**A few rules worth knowing.** Only Active and PreReg students are sent; Inactive students — and, where a district has chosen to, grades outside its rostering range — are left out, along with their classes and enrollments. Family contacts with no email address are left out and students with no email address are still sent; both counts appear in the run log (see *Check the email addresses before your first sync*, above). Class names are capped at 100 characters. The files are written all-or-nothing — a run that fails partway never leaves a half-written set behind.

**Delivery.** When SFTP delivery is on, the five rostering CSVs are zipped into one dated file, `districtsync_YYYY-MM-DD.zip`, and `CourseInfo.csv`, `StudentCourses.csv` and `StudentAttendance.csv` are uploaded beside it as standalone files, because SpacesEDU imports those three individually. A course-only or attendance-only configuration produces no zip. Uploads can only go to SpacesEDU's own servers.

---

## Running DistrictSync from the command line

For servers with no desktop, or for scripted/scheduled runs, DistrictSync also works entirely from the command line — the same program file, run with arguments:

```
DistrictSync-windows.exe --sis myedbc --input C:\DistrictSync\input --output C:\DistrictSync\output
```

Replace `myedbc` with your district's mapping name — for example `sd48myedbc` for a shipped district mapping, or `sd<number>custom` for one you set up yourself (its mapping file's name without `_mapping.yaml`) — and the folders as needed. Add `--sftp` to also deliver the files to SpacesEDU afterward, or `--dry-run` to preview the row counts without writing any files. See *Headless configuration* below for configuring SFTP credentials entirely by command line (no desktop app needed), and *Troubleshooting* for what each exit code means.

---

## Getting help

If something isn't working as expected:

1. Check the **Home** screen first — it names the problem in plain language when something needs attention.
2. Check *Troubleshooting* below for common issues.
3. Open **Help** in the app, or email hello@spacesedu.com — include the version number shown on the Help screen, and if you set up your own district, the mapping file from **Show mapping file**.

---

## What changed from the published version

This section is for the reviewer only — it is not part of the article and should not be published.

**Baseline.** The live article was re-read on 2026-09-03 and again on 2026-09-14 — it is
unchanged, so nothing below has been overtaken. It already carries the 2026-08-13 body (native desktop app, the launch question, the five wizard steps in the right order, the seasonal pause, the six screens, `hello@spacesedu.com`), followed by the older tail sections (*Headless configuration (Linux)*, *Step 3 — Place your GDE files*, *Step 4 — Run the tool*, *Output and SFTP upload*, *Configurations*, *Field mapping reference*, *Automating GDE downloads from MyEdBC SFTP*, *Troubleshooting*). The rows below are against THAT.

**Now wrong** — things in the live article that would actively mislead a district admin today:

- **"This is not a YAML editor: creating or adjusting a district's column mapping is done by the DistrictSync/SpacesEDU team, not in the app."** Half of that is now false. Since this release a district can set up its OWN mapping in the app — starting point, identity, which files, which grades, its own file names — and edit it later from Mapping. What stays vendor-only is the *column* layer (header names). The new *Setting up a district that isn't listed yet* section and the rewritten *Mapping* subsection carry this; the wizard's step 1 and the launch question's no-match bullet gain the pointer.
- **"This is normal for a new release that isn't yet code-signed."** The Windows download has been signed as **myBlueprint Corp.** since v3.16.0 (2026-09-02). The paragraph is replaced with the signed-publisher version (check Digital Signatures; SmartScreen may still warn for a while; an *unknown* publisher is now a reason to stop). macOS/Linux stay unsigned and the article now says so.
- **"DistrictSync automatically zips all output CSVs into a single dated archive."** Wrong since the 2026-08-26 delivery change: only the five rostering CSVs go into the zip; `CourseInfo.csv`, `StudentCourses.csv` and `StudentAttendance.csv` are uploaded standalone beside it, and a course-only or attendance-only configuration produces no zip. The *Delivery* paragraph under *What gets produced* is the replacement — the *Output and SFTP upload* tail section should be trimmed to its manual-upload sentence or dropped.
- **Download list is Windows + Linux only.** macOS ships as `DistrictSync-macos.dmg` since v3.15.0 (drag to Applications; allow in Privacy & Security); the bare `DistrictSync-macos` is now the headless-only build. Both added to *Installing DistrictSync*.
- **"If your district uses different filenames, advise SpacesEDU Support and we can generate a custom configuration for you"** (*Step 3 — Place your GDE files*). Still an option, no longer the only one: file names are now set by the district on the *Your files* step. Superseded by *The MyEd BC files DistrictSync needs*.
- **The live GDE list names five files and omits Class Information Enhanced entirely** (*Step 3 — Place your GDE files*: demographic, staff, emergency contact, schedule, course information). So a district following the live article never requests the report that carries **Primary Teacher** — and co-taught and modular-program teachers are then missing from `Enrollments.csv` with nothing on screen to say why. It is the single most consequential omission on the page. Fixed by the six-report table in *The MyEd BC files DistrictSync needs*. (Also worth eyeballing on the live page: the emergency-contact filename renders as `Emergency ContactInformation.txt`, with a space — possibly just a line wrap, but check.)
- **The live list gives filenames only, and none of them says "Enhanced" except the staff one.** The reports to ask a MyEd BC administrator for are *Student Demographic Information Enhanced*, *Staff Information Enhanced*, *Emergency Contact Information Enhanced*, *Student Schedule*, *Course Information* and *Class Information Enhanced* — and the first, third and sixth commonly arrive under filenames the standard mapping does NOT look for (`StudentDemographicEnhanced.txt`, `EmergencyContactInformationEnhanced.txt`). The new table gives the report name and the standard mapping's filename as separate columns, with a bullet saying the difference is normal and where it is reconciled (*Your files*, or a ready-made district mapping).

**Sections to replace or fold** — the older tail, one by one:

- ***Step 3 — Place your GDE files*** → replaced by *The MyEd BC files DistrictSync needs* (same content, plus Class Information Enhanced and the two attendance files, the MyEd BC report names, the case-insensitive matching rule, missing-file behaviour and *Why the Enhanced reports*).
- ***Configurations*** → superseded by *The MyEd BC files DistrictSync needs* (the per-tier required-files lists) + *What gets produced* (the per-tier outputs). The three-tier list also omits that individual districts have their own mappings (twenty ship with the program today, plus any a district adds itself) — the article no longer enumerates them; the Mapping screen does.
- ***Step 4 — Run the tool*** → folded into *Running DistrictSync from the command line* ("Replace `myedbc` with your district's mapping name").
- ***Output and SFTP upload*** → see the "zips all output CSVs" row above.
- ***Headless configuration (Linux)*** → keep; add that the macOS command-line build (`DistrictSync-macos`) takes the same flags.
- ***Field mapping reference*** → keep as-is (out of this revision's scope). One behavioural note worth a line there: Family rows with no email address are no longer emitted (v3.14.0), and students with no email are still emitted.
- ***Automating GDE downloads from MyEdBC SFTP*** → keep as-is.
- ***Troubleshooting*** → keep; the exit-code table is still correct. Worth one new row: a district's own mapping can stop working after an app update that changes the standard mapping it builds on — that night shows as a failed run in Run History with a configuration reason, and Mapping's card says the mapping needs its test conversion run again (**Edit mapping** → **Run a test conversion**).

**New in this draft, not in the live article:**

- *Setting up a district that isn't listed yet* — the whole section.
- *The MyEd BC files DistrictSync needs* — the required-input-files section the article's scope principle calls for (public, high-level: which reports to request, what they feed, how names are reconciled). Since 2026-09-14 it also carries: the MyEd BC **report** names beside the filenames; *Why the Enhanced reports*, which gives the consequence of each non-Enhanced variant rather than just preferring them; Class Information Enhanced's second job (co-teacher enrollments, not only blended-class detection); and the two attendance source files, which had no row at all even though `StudentAttendance.csv` has one under *What gets produced*.
- *Check the email addresses before your first sync* — a new block at the foot of *The MyEd BC files DistrictSync needs*, at the owner's request. Three facts a district cannot get from the live article today: a Family row with no email is not written at all (v3.14.0), so blank guardian emails in MyEd BC are invisible data loss; a student with no email still ships; and where student addresses follow a district pattern they can be GENERATED by the mapping instead of exported — eight shipped mappings already do (sd10/40/51/54/60/74/75 and unitychristian), which is why the block routes that request to support rather than to the in-app self-service setup, whose four questions cannot express an email template. The corresponding sentence under *A few rules worth knowing* was trimmed to a cross-reference so the page does not state the same rule twice.
- **Capitalization no longer has to match** (shipped 2026-08-04). The draft previously said "Filenames must match exactly", which has been wrong for six weeks: DistrictSync matches a configured name against the folder ignoring case, an exact match always wins, and only a genuine case collision on a name that is otherwise absent stops the run — which it does loudly, naming both files. Replaced by the *Spelling matters, capitalization doesn't* bullet.
- Under *What gets produced*: the `StudentAttendance.csv` row, the three-sentence *How classes are built*, the *A few rules worth knowing* paragraph (active/PreReg filter, rostering-range scoping, the no-email Family/Student rules, the 100-character cap, all-or-nothing writes), and the *Delivery* paragraph.
- The in-repo relative links (`headless-sftp-setup.md`, `faq.md`, `troubleshooting.md`, `how-classes-work.md`) are GONE — they were dead on the Help Centre. Each now names the live article's own section (*Headless configuration*, *Troubleshooting*) or carries the fact inline (the seasonal pause, how classes are built).

**Stale but harmless** — still broadly true, just dated or under-specified:

- The overall day-to-day mechanics (GDE in, CSV out, SFTP upload, atomic writes, >20% anomaly warning) are unchanged and still accurate.
- The `districtsync_YYYY-MM-DD.zip` naming is unchanged (what goes *inside* it is not — see above).
- The Windows Task Scheduler entry name (`DistrictSync_Daily`) is still accurate.
- The article's two named district file-format notes (SD40's headerless CSVs, SD48's enhanced files) are still accurate but are the only two of twenty; the bundled mappings carry the rest and the article no longer needs to.
- ~~**Support contact.**~~ RESOLVED 2026-08-13 (the app was re-pointed to `hello@spacesedu.com`, which the article already used). Kept only so a reviewer comparing versions doesn't re-open it.
