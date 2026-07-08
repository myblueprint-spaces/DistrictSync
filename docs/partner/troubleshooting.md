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

**Task does not run after a reboot / server restart**
- The Setup Wizard (schedule step) automatically registers the task to **run whether the user is logged on or not** with **Highest Privileges**, using the Windows account password you enter during setup.
- If the task still doesn't run after a reboot, the Windows password entered at setup was likely incorrect. A wrong password causes Windows to report an error in the wizard — if you dismissed the error, re-run the Setup Wizard to re-register the task with the correct password.
- If you left the password blank during setup, the task was registered for **logged-on-only** operation (the wizard warns you at that point). Enter the password to enable unattended runs.

**"Access is denied" when activating the schedule**
- **Most common cause: the wizard was not run as administrator.** Creating an unattended task (runs whether or not you are logged on, with highest privileges) requires administrator rights. Close the wizard, **right-click `DistrictSync-windows.exe` → "Run as administrator"**, and re-run the schedule step.
- **If you are already running as administrator and still see "Access is denied":** the Windows password was likely rejected. Enter your **Windows account password** — not your Windows Hello PIN, and for a **Microsoft Account** your microsoft.com password — on the schedule step. If it still fails, the account may not be permitted to **"Log on as a batch job"** (check with your IT administrator).

**Task shows a non-zero "Last Run Result" (e.g. code 3 / 0x3)**
- Exit code 3 means the ETL conversion **succeeded** and the output files were written, but the **SFTP delivery to SpacesEDU failed**. The CSV files are intact in your output folder.
- Open the **Run History** page in the DistrictSync wizard or check `~/.districtsync/etl_tool.log` for an `ERROR` line beginning `SFTP upload FAILED —` to find the cause (network, credentials, host).
- Re-run `--sftp-test` (or Setup Wizard → Step 4) to verify your SFTP credentials are still valid.

**Task runs but nothing happens**
- Open the Run History page in the DistrictSync wizard. Scheduled runs
  write to the same `~/.districtsync/etl_tool.log` as manual runs, so
  they should appear there.
- If there's a run but it failed, look for `Pipeline failed:` lines
  in the log for the cause.
- The task's **Start in** field does not matter — logs always go to
  `~/.districtsync/` regardless of the working directory.

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

## Log location

Every ETL run — wizard, scheduled task, and CLI — writes to a single
persistent log file in your user home directory, regardless of where
the `.exe` lives or what working directory the task runs from:

| Platform | Log file |
|----------|----------|
| Windows | `C:\Users\<username>\.districtsync\etl_tool.log` |
| Linux   | `/home/<username>/.districtsync/etl_tool.log` |
| macOS   | `/Users/<username>/.districtsync/etl_tool.log` |

The Run History surface in the app reads from this same path and
displays the runs in a sortable table. The log rotates automatically
at 5 MB and keeps 3 backups (`etl_tool.log.1`, `.2`, `.3`).
