# Troubleshooting

## The tool runs but produces no output

**Possible causes:**

1. **Wrong district config** — The config expects different filenames than what's in the input directory.
   - Check the log for messages like `Primary source file 'X.txt' is empty`.
   - Try a different config (e.g., `sd48myedbc` instead of `myedbc`).

2. **GDE files not in the input directory** — Verify the files are present:
   ```
   C:\DistrictSync\input\
     your Student Demographic file     ← must be here
     your Student Schedule file
     your Staff Information file
     your Course Information file
     your Emergency Contact file
   ```

3. **All students are inactive** — Only `Active` enrollment status is exported.
   - Run with `--quality` to get a breakdown: `DistrictSync-windows.exe --sis myedbc --input ... --output ... --quality`

---

## Family contacts weren't included

**What you see.** Home reads **"Your roster synced without family contacts"** — or **"Your sync completed without family contacts"** when the run was not set to deliver — and the run's row in Run History reads **"Delivered · 1 file skipped"** (or **"Completed · 1 file skipped"**). When the warning says the export is missing a column, it names that column — and, because family contacts are read from one export file, that file too — exactly as your district's mapping spells them (for example the parent/guardian column of `EmergencyContactInformation.txt`). `Family.csv` is not in your output folder, and any previous one has been moved into an `archive_<date>` subfolder there, so it cannot be sent by mistake.

**What it means.** Your emergency-contact export arrived, but DistrictSync could not build family contacts from it. The usual cause is a different MyEd BC report saved under the usual filename — for example the basic *Emergency Contact Information* report instead of the **Enhanced** one, which carries the email and parent/guardian columns your mapping reads. DistrictSync never sends contacts it cannot filter, so it leaves the whole file out rather than send them. Everything else — students, staff, classes and enrollments — was built and delivered as normal, and the run still ends with exit code **0**. What SpacesEDU does with family links from an earlier delivery while `Family.csv` is missing is pending confirmation.

**What to do.** Re-export the Enhanced emergency-contact report under the same filename. The next sync picks it up automatically — there is nothing to change in DistrictSync — and the warning clears once family contacts are built again; until then it shows every night. For the detailed cause, open `etl_tool.log` and search for `ENTITY NOT BUILT`: that line names the file that was left out and the kind of problem. The course and attendance exports behave the same way — a problem with one of them leaves only that file out.

---

## SFTP upload fails

**Error: "No password found"**
- The OS credential store was cleared (e.g., after a password change or server reinstall).
- Re-enter credentials via the Setup Wizard → Step 4.

**Error: "Connection failed: Connection refused"**
- Verify the SFTP host and port are correct.
- Check that the server firewall allows outbound connections on port 22.

**Error: "Authentication failed"**
- The username or password is incorrect.
- Contact SpacesEDU to confirm your credentials.

---

## Task Scheduler does not run the task

**First — is a seasonal pause switched on?**
- If you enabled the **seasonal pause** (Setup → Schedule), DistrictSync intentionally does nothing outside your school-year window. The task still wakes each night; it checks the date and exits cleanly.
- Confirm by opening DistrictSync: Home reads a green *"Paused for the summer — resumes &lt;date&gt;"*. That is the pause working, not a fault, and it will resume on its own on that date.
- If you need a one-off run while paused, use the **Convert** page — manual conversions always run, regardless of the window.

**Task does not run after a reboot / server restart**
- The Setup Wizard (schedule step) automatically registers the task to **run whether the user is logged on or not** with **Highest Privileges**, using the Windows account password you enter during setup.
- If the task still doesn't run after a reboot, the Windows password entered at setup was likely incorrect. A wrong password is reported in the wizard as **"Windows rejected the user name or password"** — re-run the schedule step with the correct password.
- If you left the password blank during setup, the task was registered for **logged-on-only** operation (the wizard warns you at that point). Enter the password to enable unattended runs.

**The Windows permission prompt (UAC) when activating the schedule**
- Registering an unattended task (runs whether or not you are logged on, with highest privileges) requires administrator rights. DistrictSync requests them with **one Windows permission prompt** as it registers the task — click **Yes**. You do **not** need to launch the whole app as administrator.
- **"You declined the Windows permission prompt — nothing was changed."** The prompt was answered **No** / **Cancel**. Re-run the schedule step and click **Yes**.
- **"The elevated step couldn't read the request DistrictSync prepared under your account"** — that can happen when the administrator credentials typed at the prompt belong to a different account. Sign in to the computer with an administrator account and run DistrictSync from there, or use the no-password (logged-on-only) schedule.
- **"Something on this computer is likely blocking the schedule change even after the permission prompt was approved"** — in our testing a wrong password reports a different message, not this one. Ask your IT team, quoting the Windows code the message shows and the `etl_tool.log` line containing `[HRESULT ` (see [*Where DistrictSync stores its data*](#where-districtsync-stores-its-data-config-logs-run-history)).
- **"Windows is missing the nightly task's own saved account details"** — Windows has no saved account information for the nightly task, and this is *not* about the password you typed. If a nightly schedule is listed, choose **Remove nightly sync**, then **Schedule nightly sync** again. If it keeps failing, the Help page in DistrictSync has our support contact.
- **"This copy of DistrictSync can't reach Windows Task Scheduler"** — this build cannot talk to the Windows Task Scheduler service, so the nightly sync cannot be scheduled from this computer at all. Conversions still run from the **Convert** page; the Help page in DistrictSync has our support contact.

**"Windows would not save the password for the nightly task" (Windows code `0x80070520`)**
- DistrictSync asked Windows to store the nightly task's password and Windows refused. Microsoft documents this when the security setting **"Network access: Do not allow storage of passwords and credentials for network authentication"** is switched on — a security-hardening setting, not a misconfiguration. It is set in Local Security Policy (Security Options) and recorded in the registry at `HKLM\SYSTEM\CurrentControlSet\Control\Lsa\DisableDomainCreds`.
- **DistrictSync cannot work around this policy.** There are three options: ask your IT team to lift the setting for this computer; schedule without the Windows password, which runs only while someone is signed in and **will not run after a reboot with no one signed in**; or run each conversion by hand from the **Convert** page.
- Do **not** tick **"Do not store password"** in Task Scheduler as a workaround. Microsoft documents tasks registered that way as having no access to network resources or to encrypted files; DistrictSync never registers a task that way and cannot support one — nightly delivery to SpacesEDU may stop working.
- There is a fourth option if your directory can supply a **managed service account (gMSA)**: nothing is stored on the computer, so this policy does not block it. Your IT team has three things to put in place first, and scheduling the nightly sync as any account other than your own also moves this computer's settings into a shared folder that this version cannot move back — both are covered in *[Managed service accounts (gMSA) — what your IT team needs to do](managed-service-accounts.md)*.

**"Windows would not schedule the task as that managed service account" (Windows code `0x80070534`)**
- Only reachable with **This is a managed service account (gMSA)** ticked in **Setup → Daily schedule**. Windows did not accept the account: either the directory does not know the name, or this computer is not set up to use it. DistrictSync cannot tell which, so it shows the three prerequisites rather than guessing at one.
- **Retrying changes nothing** — all three prerequisites are your directory administrator's to check. They are listed, with a worked example, in *[Managed service accounts (gMSA) — what your IT team needs to do](managed-service-accounts.md)*; hand them that page.
- If the name itself is wrong, fix it in **Windows account for the nightly task** — it must be `DOMAIN\name$`, including the trailing `$`.
- If your directory administrator confirms all three prerequisites are in place and Windows still refuses the account, tell us: the Help page in DistrictSync has our support contact, and the Windows code shown on screen is what we need.

**Task shows a non-zero "Last Run Result" (e.g. code 3 / 0x3)**
- Exit code 3 means the ETL conversion **succeeded** and the output files were written, but the **SFTP delivery to SpacesEDU failed**. The CSV files are intact in your output folder.
- Open the **Run History** page in the DistrictSync wizard or check `etl_tool.log` in your DistrictSync data folder (see [*Where DistrictSync stores its data*](#where-districtsync-stores-its-data-config-logs-run-history)) for an `ERROR` line beginning `SFTP upload FAILED —` to find the cause (network, credentials, host).
- Re-run `--sftp-test` (or Setup Wizard → Step 4) to verify your SFTP credentials are still valid.

**Task runs but nothing happens**
- Open the **Run History** page in the DistrictSync app. Scheduled, manual,
  and CLI runs are all recorded in the run-history database (`history.db`) in
  your DistrictSync data folder, so a run that started should appear there,
  tagged by how it was triggered.
- For the *why* behind a failed run, open the diagnostic log (`etl_tool.log`
  in the same folder) and look for `Pipeline failed:` lines. Run History tells
  you *that* a run failed; the log carries the detailed cause.
- The task's **Start in** field does not matter — both `history.db` and the
  log always live in your DistrictSync data folder regardless of the working
  directory (see
  [*Where DistrictSync stores its data*](#where-districtsync-stores-its-data-config-logs-run-history)).

---

## I ran the .exe from a terminal and saw nothing

On Windows the released `.exe` is a **windowed** application, so double-clicking
it never flashes a black console box. The trade-off is that it only prints when
there is a terminal to print to:

| How you launched it | Do you see output? |
|---|---|
| Command Prompt / PowerShell, **with** `--sis`/`--input`/… | **Yes** — it attaches to that window |
| Double-click | No (it opens the app window instead) |
| Task Scheduler / a service | No — nothing is attached to it |
| Output redirected (`… > out.txt`) | Yes — the redirect is honoured |

Because the app is windowed, your shell does **not** wait for it: the prompt
returns immediately and the output appears after it. Use `start /wait` if you
want the prompt to wait for the run to finish.

When nothing is printed, the run still leaves two signals: the diagnostic log
(`etl_tool.log` in your DistrictSync data folder) and the **exit code** below.
On Linux and macOS output goes to the terminal as usual.

---

## Exit codes

Every run ends with one of four codes. Task Scheduler shows it as **Last Run
Result**; in Command Prompt read it with `echo %ERRORLEVEL%`, in PowerShell with
`$LASTEXITCODE`.

| Code | Meaning | What to do |
|------|---------|-----------|
| **0** | Success — the conversion completed (and any requested SFTP delivery succeeded). This includes a run that left out a file whose export had a problem — family contacts, courses, student courses or attendance; Home and Run History show that as a warning. | Nothing — or, if Home shows a warning, see [*Family contacts weren't included*](#family-contacts-werent-included). |
| **1** | The run did **not** complete: bad input folder, unreadable district config, no usable input files, a student, staff, class or enrollment export with a problem (for example a missing column the mapping needs), or a run that produced no output / lost its student roster. **Nothing was written** — your previous output folder is untouched. | Check `etl_tool.log` for the `Pipeline failed:` line, and confirm the GDE export actually landed in the input folder. |
| **2** | The command line itself was wrong — a missing/unknown flag, more than one `--sftp-…` subcommand, or `--sftp-password-stdin` with nothing piped in. | Fix the command; nothing was run. |
| **3** | The conversion **succeeded and the CSVs were written**, but the SFTP delivery to SpacesEDU failed. | See the *Last Run Result* notes above — the files are intact in your output folder. |

A code **1** and a code **3** mean very different things: `1` means no files were
produced, `3` means the files exist locally but SpacesEDU did not receive them.

---

## Encoding errors in log

```
WARNING - Could not decode your Student Demographic file with utf-8, trying latin1
```

This is normal. The tool automatically tries UTF-8, Latin-1, and CP1252 in sequence. If all three fail, check that the file is a valid text file (not corrupt or binary).

---

## "Mapping file not found" error

```
ERROR - Mapping file not found: config/mappings/myedbc_mapping.yaml
```

The tool cannot find its configuration files. This happens when:
- The executable was moved without the `config/` directory.
- The tool is invoked from a different working directory.

**Fix:** The config is embedded in the executable (PyInstaller bundle). If you see this error from the bundled `.exe`, contact SpacesEDU — it may indicate a corrupted download.

---

## Partial output files

If the tool crashes mid-run, you may find an incomplete output directory. Since v1.4+, DistrictSync uses atomic (transactional) writes — all CSVs are staged in a temporary directory first and only committed together on success. A failed run leaves the previous output intact.

If you find a `.tmp_*` directory in your output folder, it means the tool was interrupted during a write. Delete it and re-run.

---

## SFTP Host Rejected

DistrictSync only allows SFTP uploads to SpacesEDU servers (sftp.ca.spacesedu.com, sftp.app.spacesedu.com, sftp.myblueprint.ca). If you see 'SFTP host not allowed', verify you're using the correct SpacesEDU host.

---

## Record Count Drop Warning

If a run produces significantly fewer records than the previous run (>20% decrease), a warning is logged. This usually means the GDE export was incomplete. Re-export the files from MyEdBC and run again.

---

## Wrong file extension (.csv vs .txt)

Some districts (e.g., SD40 – New Westminster) export GDE files as `.csv` instead of `.txt`. If the tool cannot find the expected file, check whether your district's files have a `.csv` extension and contact SpacesEDU to ensure the correct district config (e.g., `sd40myedbc`) is configured for your installation.

---

## Records missing after SpacesEDU import

If DistrictSync completed successfully but records are missing in SpacesEDU, the issue is on the import side — not DistrictSync. Common causes:

- **Email domain mismatch** — student or staff email doesn't match the district's configured domain in SpacesEDU.
- **Missing required field** — a record was skipped because a required field (User ID, Name, etc.) was blank.
- **Orphaned enrollment** — an enrollment references a Class ID or User ID that doesn't exist in the corresponding file.
- **Family without student** — a family record references a student not in `Students.csv`.

Check the **SpacesEDU import report** for details. See also [FAQ — What happens after upload](faq.md#what-happens-after-upload-spacesedu-import).

---

## A student appears in the wrong class

A student shows up in an old or incorrect class in SpacesEDU even though MyEdBC was updated.

**The fix depends on the student's grade**, because different GDE files drive placement for different grades:

| Student's grade | Class placement comes from | So the field to fix is in… |
|-----------------|----------------------------|----------------------------|
| K–7 (homeroom grades) | **Student Demographic** — the `Homeroom` and `Teacher name` columns | the Student Demographic GDE |
| 8–12 | **Student Schedule** — the course-section rows | the Student Schedule GDE |

DistrictSync rebuilds every class and enrollment **from scratch on each run** — it never remembers a previous run. So if the output still places a student in the wrong class, the **current** input file named above still contains that placement. The fix is always the same shape: correct it in MyEdBC → re-export that GDE → re-run.

**Homeroom-grade students (K–7) are the common surprise.** Their class is named `<Homeroom> - <Teacher name> (<year>)` (e.g. `10 Eng6/7 - Bali-Kainth, P. (2026)`), taken **straight from the Student Demographic `Homeroom` and `Teacher name` columns** — *not* from who teaches their courses. If a student was moved to a new homeroom but still appears in the old one, the **`Homeroom` field on their demographic record was never updated in MyEdBC**. Moving them in the schedule (their course teachers) will *not* fix it — and re-exporting only the Student Schedule will *not* fix it. Update the homeroom assignment in MyEdBC, then re-export the Student Demographic file.

> **A stale GDE cannot be auto-detected.** DistrictSync faithfully transforms whatever it is handed; it has no way to know an export is out of date. The only built-in guard is the >20 % record-drop warning (above), which won't catch scattered individual changes. Keeping exports current — especially the Student Schedule at **semester rollover**, when secondary timetables change wholesale — is an operational responsibility.

---

## A student isn't getting the expected homeroom class

Homeroom classes are only created for grades listed in the config's `homeroom_grades` (compared after CEDS normalization). DistrictSync maps several MyEdBC grade codes onto a homeroom grade automatically — for example `KF` and `EL` both normalize to `KG`.

**Any grade code the tool doesn't recognize falls through to `UG` (ungraded)** and is treated as a non-homeroom grade, so that student is routed through the *subject-class* (schedule) path and gets no homeroom. If a district introduces a new grade code and its students unexpectedly have no homeroom, the code is likely missing from the grade map — contact SpacesEDU to have it added.

---

## Where DistrictSync stores its data (config, logs, run history)

DistrictSync keeps all of its data — your saved settings (`config.json`), the
diagnostic log (`etl_tool.log`), and the run-history database — in the standard
per-user application-data folder for your operating system. Nothing is ever
written next to the `.exe`, so moving or re-downloading the program never loses
your settings or history:

| Platform | Data folder |
|----------|-------------|
| Windows | `C:\Users\<username>\AppData\Local\DistrictSync\` |
| macOS   | `~/Library/Application Support/DistrictSync/` |
| Linux   | `$XDG_DATA_HOME/DistrictSync/` (default `~/.local/share/DistrictSync/`) |

Two things track your runs, side by side in that folder:

- **`history.db`** — the run-history database the **Run History** surface reads.
  Every run (wizard, scheduled task, and CLI) is recorded here, tagged by how it
  was triggered.
- **`etl_tool.log`** — the diagnostic log: the detailed, human-readable messages
  for troubleshooting a specific run. It rotates automatically at 5 MB and keeps
  3 backups (`etl_tool.log.1`, `.2`, `.3`).

Both are written regardless of where the `.exe` lives or what working directory
the task runs from.

> **Run History looks empty after updating?** This version records history in
> `history.db` rather than parsing it back out of the log, so history starts
> fresh with this update — earlier log-derived runs aren't carried over (the old
> log mixed real runs with internal test entries). Run History fills in again
> from your next conversion; your previous `etl_tool.log` is untouched.

**Upgrading from an older version?** Earlier releases stored this data in a
`.districtsync` folder in your home directory (e.g. `C:\Users\<username>\.districtsync\`).
The first time you run a newer version, DistrictSync automatically copies your
settings, logs, and history into the new location above and leaves a small
`MOVED.txt` note in the old folder pointing at the new one. The move is safe and
one-time: if anything prevents it, DistrictSync simply keeps using the old folder
(you are never left half-moved). Once you have confirmed everything still works,
the old `.districtsync` folder is safe to delete.
