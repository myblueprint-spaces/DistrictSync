# Troubleshooting

## The tool runs but produces no output

**Possible causes:**

1. **Wrong district config** — The config expects different filenames than what's in the input directory.
   - Check the log for messages like `Primary source file 'X.txt' is empty`.
   - Try a different config (e.g., `sd48myedbc` instead of `myedbc`).

2. **GDE files not in the input directory** — Verify the files are present:
   ```
   C:\GDE2Acsv\input\
     your Student Demographic file     ← must be here
     your Student Schedule file
     your Staff Information file
     your Course Information file
     your Emergency Contact file
   ```

3. **All students are inactive** — Only `Active` enrollment status is exported.
   - Run with `--quality` to get a breakdown: `GDE2Acsv-windows.exe --sis myedbc --input ... --output ... --quality`

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

**"Access denied" or task shows "Last Run Result: 0x1"**
- The task must be configured to **Run whether user is logged on or not** and with **Highest Privileges**.
- Delete the task (`schtasks /Delete /F /TN GDE2Acsv_Daily`) and re-run the Setup Wizard as Administrator.

**Task runs but no log entry appears**
- Check the task's "Start In" directory — it must be set to the directory containing the `.exe`.
- In Task Scheduler, edit the task → Actions → set **Start in** to `C:\GDE2Acsv\`.

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

If the tool crashes mid-run, you may find an incomplete output directory. Since v1.4+, GDE2Acsv uses atomic (transactional) writes — all CSVs are staged in a temporary directory first and only committed together on success. A failed run leaves the previous output intact.

If you find a `.tmp_*` directory in your output folder, it means the tool was interrupted during a write. Delete it and re-run.

---

## SFTP Host Rejected

GDE2Acsv only allows SFTP uploads to SpacesEDU servers (sftp.ca.spacesedu.com, sftp.app.spacesedu.com, sftp.myblueprint.ca). If you see 'SFTP host not allowed', verify you're using the correct SpacesEDU host.

---

## Record Count Drop Warning

If a run produces significantly fewer records than the previous run (>20% decrease), a warning is logged. This usually means the GDE export was incomplete. Re-export the files from MyEdBC and run again.

---

## Wrong file extension (.csv vs .txt)

Some districts (e.g., SD40 – New Westminster) export GDE files as `.csv` instead of `.txt`. If the tool cannot find the expected file, check whether your district's files have a `.csv` extension and contact SpacesEDU to ensure the correct district config (e.g., `sd40myedbc`) is configured for your installation.

---

## Records missing after SpacesEDU import

If GDE2Acsv completed successfully but records are missing in SpacesEDU, the issue is on the import side — not GDE2Acsv. Common causes:

- **Email domain mismatch** — student or staff email doesn't match the district's configured domain in SpacesEDU.
- **Missing required field** — a record was skipped because a required field (User ID, Name, etc.) was blank.
- **Orphaned enrollment** — an enrollment references a Class ID or User ID that doesn't exist in the corresponding file.
- **Family without student** — a family record references a student not in `Students.csv`.

Check the **SpacesEDU import report** for details. See also [FAQ — What happens after upload](faq.md#what-happens-after-upload-spacesedu-import).

---

## Log location

| Platform | Log file |
|----------|----------|
| Windows | Same directory as the `.exe`, e.g. `C:\GDE2Acsv\etl_tool.log` — requires Task Scheduler's **Start in** to be set to that folder (see [Task Scheduler does not run the task](#task-scheduler-does-not-run-the-task)) |
| Linux | Current working directory when the command is run, e.g. `/opt/gde2acsv/etl_tool.log` |

The log rotates automatically at 5MB and keeps 3 backups.
