# Troubleshooting

## The tool runs but produces no output

**Possible causes:**

1. **Wrong district config** — The config expects different filenames than what's in the input directory.
   - Check the log for messages like `Primary source file 'X.txt' is empty`.
   - Try a different config (e.g., `sd48myedbc` instead of `myedbc`).

2. **GDE files not in the input directory** — Verify the files are present:
   ```
   C:\GDE2Acsv\input\
     StudentDemographicInformation.txt  ← must be here
     StudentSchedule.txt
     StaffInformationEnhanced.txt
     CourseInformation.txt
     EmergencyContactInformation.txt
   ```

3. **All students are inactive** — Only `Active` enrollment status is exported.
   - Run with `--quality` to get a breakdown: `GDE2Acsv.exe --sis myedbc --input ... --output ... --quality`

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
WARNING - Could not decode file StudentDemographic.txt with utf-8, trying latin1
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

## Log location

| Platform | Log file |
|----------|----------|
| Windows | Same directory as the `.exe`, e.g. `C:\GDE2Acsv\etl_tool.log` |
| Linux | `~/.gde2acsv/etl_tool.log` or current directory |

The log rotates automatically at 5MB and keeps 3 backups.
