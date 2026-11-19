# Runbook: the hosted SD74 server (installer build)

myBlueprint runs DistrictSync on its own Windows server for SD74. SD74 delivers raw MyEducation BC General Data Extracts (GDE) to us by SFTP; this server converts them nightly and delivers the Advanced CSV to SpacesEDU. This page is how that server is installed from the **public release page, exactly as a district would** (first done for v3.30.0, DSYNC-87). It holds no host names, account names, passwords or district data; keep those in the team's password manager and server notes.

## Before you start

- A Windows account on the server that will own DistrictSync **and** run the nightly. Use one account for both: the installer, the settings, the run history and the SFTP delivery password all live under that account (see [Where things live](#where-things-live)). Sign in as that account for every step below.
- The SFTP landing folder where SD74's GDE files arrive (the input folder), and an empty folder for the output.
- The SpacesEDU SFTP delivery details for SD74 (from the password manager).
- If the server ran the single-file `DistrictSync-windows.exe` before, note its scheduled task; you will replace it (step 5).

## 1. Download

From the release page, as a customer would:

- Release page: `https://github.com/myblueprint-spaces/DistrictSync/releases`
- This version: `https://github.com/myblueprint-spaces/DistrictSync/releases/download/v3.30.0/DistrictSync-Setup.exe`
- Always the newest: `https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-Setup.exe`

Check it before running it, in PowerShell:

```powershell
Get-AuthenticodeSignature .\DistrictSync-Setup.exe | Format-List Status, SignerCertificate, TimeStamperCertificate
Get-FileHash .\DistrictSync-Setup.exe -Algorithm SHA256
```

`Status` must be `Valid`, the signer `CN=myBlueprint Corp.`, and `TimeStamperCertificate` present. The hash must match the `DistrictSync-Setup.exe` line of the release's `SHA256SUMS.txt`. If Windows SmartScreen shows a warning on a brand-new version, record it on the release ticket (plan 0054 keeps the cautious wording until a result is recorded).

## 2. Install

Double-click `DistrictSync-Setup.exe` (or run `.\DistrictSync-Setup.exe --silent`). It needs no administrator rights and installs for the signed-in account only:

- program: `%LOCALAPPDATA%\myBlueprint.DistrictSync\current\DistrictSync.exe`
- a Start menu entry, **DistrictSync**
- `%LOCALAPPDATA%\myBlueprint.DistrictSync\Update.exe` (the installer's own tool, used for uninstall)

The app opens once after install. Never pass DistrictSync arguments through `Setup.exe` (it does not forward them; plan 0054 [RG-D]); install first, then run the installed exe.

## 3. Configure (in the app)

Open DistrictSync from the Start menu and complete Setup:

1. **District:** the bundled SD74 mapping (`sd74myedbc`). Do not create a custom mapping for SD74.
2. **Input folder:** the SFTP landing folder for SD74's GDE files. The SD74 mapping reads these exact file names (case-insensitive): `StudentDemographicInformation.txt`, `StaffInformation.txt`, `ParentInformation.txt`, `ClassInfoEnhanced.txt`, `CourseInformation.txt`, `studentcourseselection.txt` (all six, as of v3.30.0). A night with any of them missing or empty stops the run and sends nothing (exit 1, `incomplete_input`); that is deliberate (failure policy §2 layer 3a).
3. **Output folder:** a separate folder, never the landing folder.
4. **Delivery (SFTP):** the SpacesEDU details for SD74. The password goes to Windows Credential Manager for this account only.

Then run one conversion from **Convert** and read the result before scheduling. From a terminal the same check is:

```powershell
& "$env:LOCALAPPDATA\myBlueprint.DistrictSync\current\DistrictSync.exe" --sis sd74myedbc --input "<landing folder>" --output "<output folder>" --dry-run
```

## 4. Schedule

Use the app's **Setup → Schedule** step (not raw `schtasks`): it registers the nightly for the installed exe, refuses an account with no password, and records which account the task runs as. On a server where nobody stays signed in, choose to run the task **whether or not the account is signed in** and give this same account's password; Windows stores it in Task Scheduler, DistrictSync never does. Keep the run time after SD74's SFTP drop normally lands.

Since DSYNC-66 the nightly for an installed copy always runs at the **limited** run level, even when "highest privileges" is asked for, because the program sits in a folder the account itself can write to. DistrictSync needs no elevation to convert and deliver.

Do not run the nightly as a different account (a service account or gMSA) with this per-user install: that account cannot use this account's settings and password, and the shared machine profile it would need turns the update check off. That set-up is the [service-account guide](../partner/headless-sftp-setup.md#running-the-nightly-sync-as-a-service-account) and is a different install.

Check it: Task Scheduler shows **DistrictSync_Daily**; right-click → **Run** once and read Run History.

## 5. Replacing an older single-file install

If this server ran `DistrictSync-windows.exe` from a folder: settings, history and the SFTP password are in the same per-account profile and are used as they are. Remove the old schedule in the old app (or Task Scheduler), schedule again from the installed app (step 4) so the task points at the installed exe, then delete the old exe.

## Where things live

| What | Where |
|---|---|
| Program | `%LOCALAPPDATA%\myBlueprint.DistrictSync\current\` |
| Settings | `%LOCALAPPDATA%\DistrictSync\config.json` |
| Run history | `%LOCALAPPDATA%\DistrictSync\history.db` (the app's **Run History**) |
| Log | `%LOCALAPPDATA%\DistrictSync\etl_tool.log` (5 MB, 3 backups) |
| Update-check record | `%LOCALAPPDATA%\DistrictSync\updater\` |
| SFTP password | Windows Credential Manager, this account only |
| Output | the output folder: `acsv_sd74_<date>.zip` (what is delivered), roster CSVs in `csv\`, earlier files in `archive_<timestamp>\` (never deleted) |

`DistrictSync.exe --diagnose` prints a support report (scope, folders, schedule). It holds no passwords but names accounts and folders: treat it like a log.

## Updates on this server

**v3.30.0 does not update itself.** The installed build ships with no update feed and no trusted key (plan 0054 R15 exception, DECISIONS 2026-11-19), so it makes no update connection at all; Help reads "Updates are not available yet." To move to a newer version:

1. Download that release's `DistrictSync-Setup.exe` and check it (step 1).
2. Pick a time well clear of the nightly, and close DistrictSync.
3. Run the new `DistrictSync-Setup.exe` as the same account. It installs over the existing copy in the same folder, so settings, history and the password (all in the profile, not the program folder) are untouched and the task still points at `current\DistrictSync.exe`. The first time this is done, confirm `--version` and Task Scheduler afterwards and record the result on the release ticket: the upgrade-over-an-existing-install path has not been run on a real machine yet.

Once a release ships with updates switched on (plan 0054 S8), this server needs that **one** manual upgrade; from then on it updates itself after a scheduled run, never while a conversion is running, and rolls back a version that fails to start. To keep a server on a fixed version after that, set `DISTRICTSYNC_UPDATES=off` for the account (or the package's off-switch marker file in the `updater` folder); `update_enabled` in `config.json` is the app's own switch and has no screen yet. See `docs/developer/updates.md`.

## Uninstall

1. In DistrictSync, **Setup → Schedule → Remove** (so no task is left pointing at a missing program).
2. Windows **Settings → Apps → DistrictSync → Uninstall**, or `& "$env:LOCALAPPDATA\myBlueprint.DistrictSync\Update.exe" uninstall --silent`.

Uninstall removes the program only. Settings, history, the log and the saved password stay (`%LOCALAPPDATA%\DistrictSync\` and Credential Manager); delete them by hand only when retiring the server, and keep the output folder as long as SD74's records policy requires.

## If something goes wrong

- A failed night: **Run History** names what failed; the log has the detail. A missing or empty SD74 file stops the night with nothing sent, and SpacesEDU keeps the previous import.
- Rolling back to an older version: uninstall, then install the older release's `DistrictSync-Setup.exe` by its tag URL. Settings and history are kept.
- Known gap (DSYNC-86): a teacher MyEd lists on a class **without** the Primary Teacher flag is not enrolled in that class yet. On the 2026-11-13 SD74 drop that was 46 teacher rows on 43 classes.
