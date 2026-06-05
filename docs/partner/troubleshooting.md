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
- If the task still doesn't run after a reboot, the Windows password entered at setup was likely incorrect. A wrong password causes `schtasks` to report an error in the wizard — if you dismissed the error, re-run the Setup Wizard to re-register the task with the correct password.
- If you left the password blank during setup, the task was registered for **logged-on-only** operation (the wizard warns you at that point). Enter the password to enable unattended runs.

**"Access denied" or task shows "Last Run Result: 0x1"**
- Re-run the Setup Wizard and enter your Windows account password on the schedule step. This re-registers the task with `/RU <user> /RP <password> /RL HIGHEST` so it runs unattended.

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

## Log location

Every ETL run — wizard, scheduled task, and CLI — writes to a single
persistent log file in your user home directory, regardless of where
the `.exe` lives or what working directory the task runs from:

| Platform | Log file |
|----------|----------|
| Windows | `C:\Users\<username>\.districtsync\etl_tool.log` |
| Linux   | `/home/<username>/.districtsync/etl_tool.log` |
| macOS   | `/Users/<username>/.districtsync/etl_tool.log` |

The Run History page in the web UI reads from this same path and
displays the runs in a sortable table. The log rotates automatically
at 5 MB and keeps 3 backups (`etl_tool.log.1`, `.2`, `.3`).
