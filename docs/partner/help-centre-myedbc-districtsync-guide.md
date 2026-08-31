<!--
DRAFT — for a human to review and paste into the SpacesEDU Help Centre.
Source article being updated: https://help.spacesedu.com/en-ca/article/myedbc-districtsync-guide-mx56qo/
Drafted: 2026-08-13
This file is NOT linked from anywhere and is not part of the built docs — it exists only
so a reviewer can compare it against the live article and copy the approved text across.
See "What changed from the published version" at the bottom for a reviewer-facing diff.
-->

# MyEdBC DistrictSync Guide

DistrictSync converts MyEducation BC General Data Extracts (GDEs) into the CSV format SpacesEDU and myBlueprint+ use to keep your roster up to date. It runs as a single program on a school district server — there's nothing to install separately, no database to set up, and no web address to visit.

This guide covers installing DistrictSync, running its setup, and using it day to day.

---

## Installing DistrictSync

1. Download the file for your platform from the [Releases page](https://github.com/myblueprint-spaces/DistrictSync/releases/latest):
   - **Windows:** `DistrictSync-windows.exe`
   - **Linux:** `DistrictSync-linux`
2. Put the file anywhere convenient — a folder like `C:\DistrictSync\`, `/opt/districtsync/`, even a USB stick. Your settings, logs and run history are saved to your Windows user profile, not next to the program file, so where you keep the program doesn't matter.

**On first launch, Windows may show a blue "Windows protected your PC" screen.** This is normal for a new release that isn't yet code-signed — it doesn't mean anything is wrong. Click **More info**, then **Run anyway**. The first launch can take up to about 30 seconds while Windows unpacks the program; wait rather than double-clicking again.

For servers with no display (headless Linux, Docker, Windows Server Core), see [Headless & Docker SFTP Setup](headless-sftp-setup.md) — SFTP delivery can be configured entirely from the command line.

---

## First launch: a desktop app, not a browser

Double-clicking the program opens **a native application window on the desktop** — nothing opens in a web browser, and there's no address to type in.

Down the left side of the window is a fixed navigation menu, always in the same order:

**Home · Convert · Run History · Setup · Mapping · Help**

### "Who looks after this sync?"

The very first thing you'll see is a short question: **"Who looks after this sync?"**, asking for one work email address. This isn't a login or an account — there's no password, and nothing is "unlocked" by answering. It exists purely so DistrictSync can recognize your district's email domain and pre-select the right district for you further into setup.

- If your address matches a district DistrictSync already supports, it tells you which one and lets you continue or correct it.
- If it doesn't recognize the domain, it says so calmly and lets you carry on — you'll pick your district yourself in a moment.
- If you're not the person who manages this — or would rather skip the question — there's a plain link to move on without answering ("I'm not the person who looks after this sync"). Nothing is saved either way.

You can add, change or remove this address later from **Setup → Settings**.

---

## Setup: a 5-step wizard

After the initial question, DistrictSync walks you through a five-step setup wizard on the **Setup** screen:

1. **Choose your district** — pick your district from a dropdown. If your email domain was recognized in the first step, your district may already be selected for you (a suggestion you can change, never a silent default) — otherwise nothing is pre-picked. If your district isn't listed, contact SpacesEDU support.
2. **Choose your folders** — the **input folder** where your MyEd BC GDE files land, and the **output folder** DistrictSync writes the converted CSV files to.
3. **Set up delivery** — enter the SFTP details SpacesEDU provided (host, username, password, remote path) and test the connection. This step is optional and can be set up later.
4. **Set a nightly schedule** — turn on an automatic daily run and pick a time (03:00 is a good default, after your overnight MyEd BC export finishes). This step is also optional — if you only plan to run conversions by hand from the **Convert** screen, you can skip it. This step also has an optional **seasonal pause**, so the sync can stop over summer break and resume on its own each fall — see the [FAQ](faq.md#general) for details.
5. **Finish** — an honest summary of what was actually set up (and what you skipped, so you know what's left). Finishing here is the one thing that marks setup complete.

**Turning on the nightly schedule shows one Windows permission prompt.** Registering a task that can run whether or not you're logged in needs administrator rights, so Windows asks you to approve that one step — click **Yes**. You don't need to run the whole program as administrator, and if you never turn on the schedule (ad-hoc runs only), you won't see this prompt at all.

Once you finish the wizard, the **Setup** screen (the rail item still says "Setup") turns into a flat **Settings** page where you can review or change your folders, district, schedule and delivery settings at any time, with a single **Save** that updates everything — including re-registering the nightly task if something that affects it changed.

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

Shows which pre-built district configuration is currently active and what it produces (which CSV files, from how many source files), and lets you switch to a different pre-built configuration — seeing what it would produce before you apply it. This is **not** a YAML editor: creating or adjusting a district's column mapping is done by the DistrictSync/SpacesEDU team, not in the app. Contact SpacesEDU support if your district needs a new or adjusted mapping.

### Help

Links out to the SpacesEDU Help Centre and a one-click "email support" button (with the version number and your district name pre-filled in the subject line, so support doesn't have to ask). Both the Help Centre link and the support address are also shown as plain, selectable text, in case the "open" buttons don't do anything on a locked-down server without a browser or mail client configured.

---

## Running DistrictSync from the command line

For servers with no desktop, or for scripted/scheduled runs, DistrictSync also works entirely from the command line — the same program file, run with arguments:

```
DistrictSync-windows.exe --sis myedbc --input C:\DistrictSync\input --output C:\DistrictSync\output
```

Add `--sftp` to also deliver the files to SpacesEDU afterward, or `--dry-run` to preview the row counts without writing any files. See [Headless & Docker SFTP Setup](headless-sftp-setup.md) for configuring SFTP credentials entirely by command line (no desktop app needed), and [Troubleshooting](troubleshooting.md) for what each exit code means.

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

Most districts use the standard 5-file rostering set. Some also use the myBlueprint+ tier, which adds the course files above. Your DistrictSync configuration (chosen on the Mapping screen, or during setup) decides which files your installation produces — your SpacesEDU/myBlueprint+ contact can tell you which tier your district is on.

For details on how homeroom, subject and blended classes are built, see [How Classes Work](how-classes-work.md).

---

## Getting help

If something isn't working as expected:

1. Check the **Home** screen first — it names the problem in plain language when something needs attention.
2. Check [Troubleshooting](troubleshooting.md) and the [FAQ](faq.md) for common issues.
3. Open **Help** in the app, or email hello@spacesedu.com — include the version number shown on the Help screen.

---

## What changed from the published version

This section is for the reviewer only — it is not part of the article and should not be published.

**Now wrong** — things in the current article that would actively mislead or misdirect a district admin today:

- **"Browser-based setup."** The app is a native desktop window. There is no browser, no localhost URL, and nothing to navigate to — double-clicking the executable opens the app directly.
- **Config stored at `~/.districtsync/`.** Settings, logs and run history now live in the OS's standard per-user app-data folder (`%LOCALAPPDATA%\DistrictSync` on Windows). `~/.districtsync` is only where an *older* install used to keep this — on upgrade, DistrictSync auto-migrates it once and leaves a `MOVED.txt` breadcrumb behind. (This is already correctly documented in `docs/partner/installation.md`; the help-centre article still describes the old location as current.)
- **"Manual execution via web UI 'Convert' page."** There is no web UI. The Convert screen is a page inside the native desktop app, not a browser tab.
- **The 5-step wizard order and content.** The article lists File Paths → District → Schedule → SFTP → Review, with SFTP requiring a password at that step and a plain "Validate & Continue" button. The real wizard order is **District → Folders → Delivery → Schedule → Finish**, Delivery and Schedule are both explicitly skippable ("set up later"), and the schedule step includes an optional seasonal-pause feature the old article doesn't mention at all.
- **"Mapping Editor allows non-standard filename adjustments without YAML editing."** There is no mapping editor. The Mapping screen only lets you review the active configuration and switch to a different pre-built one — it explicitly does not edit YAML or filenames. Creating or adjusting a mapping is done by the SpacesEDU/DistrictSync team, not in the app.
- **Logs at `~/.districtsync/etl_tool.log`.** Same path issue as above — it's now in the per-user app-data folder, and there is now also a separate `history.db` that the Run History screen reads from (the article doesn't mention Run History as a screen at all, or the run-history database).
- ~~**Support contact.**~~ **RESOLVED, and the app changed rather than the article** (owner decision 2026-08-13): `hello@spacesedu.com` — the address this article already used — is the correct contact. The app previously surfaced a different one, so `SUPPORT_EMAIL` (the Help screen, Home's "we don't have a mapping for your district yet" card) and the CLI's crash hint were all re-pointed to match. **No change needed here** — this row is kept only so a reviewer comparing the two versions doesn't re-open it.
- **"Zips everything into one upload."** No longer true as of the 2026-08-26 delivery change. Only the five rostering CSVs go into `districtsync_YYYY-MM-DD.zip`; `StudentAttendance.csv` and the two myBlueprint+ files (`CourseInfo.csv`, `StudentCourses.csv`) are uploaded as standalone CSVs beside it, because SpacesEDU ingests those three individually. A course-only or attendance-only configuration produces no zip at all. The zip's *name* is unchanged.
- **Three output tiers listed as "myedbc / mbp_all / mbp_core," with mbp_core described as 3 files exactly.** Still broadly right, but the article doesn't mention that individual districts (e.g. `sd40myedbc`, `sd48myedbc`, `sd51myedbc`, `sd54myedbc`, `sd60myedbc`, `sd74myedbc`, `sd83myedbc`) are separate configurations layered on the same 5/7-file tiers, and doesn't mention the six-screen navigation (Home, Convert, Run History, Setup, Mapping, Help) that replaced whatever page structure the Streamlit UI had.

**Stale but harmless** — still broadly true, just dated or under-specified:

- The overall day-to-day mechanics (GDE in, CSV out, SFTP upload, atomic writes, >20% anomaly warning) are unchanged and still accurate.
- The `districtsync_YYYY-MM-DD.zip` naming is unchanged (what goes *inside* it is not — see "Now wrong" above).
- The general concept of "5 files for standard rostering, 7 with myBlueprint+" is still correct, just needs the per-district config layer added.
- The Windows Task Scheduler entry name (`DistrictSync_Daily`) is still accurate.
- District-specific file-format notes (SD40's headerless CSVs, SD48's enhanced files, etc.) are still broadly accurate, though the current partner docs (`docs/partner/installation.md`) carry more detail and should probably be the source of truth going forward rather than duplicating district notes in the help-centre article.
