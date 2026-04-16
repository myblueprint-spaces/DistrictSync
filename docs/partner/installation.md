# Partner Installation Guide

This guide walks you through installing and configuring DistrictSync on your school district's server. The entire process takes approximately 15–20 minutes.

---

## Prerequisites

Before you begin, ensure you have:

- [ ] A Windows Server (2016 or later) or Linux server
- [ ] Administrator / sudo access to the server
- [ ] The SFTP credentials provided by SpacesEDU (host, username, password)
- [ ] A directory where MyEdBC will place the GDE export files
- [ ] The GDE export scheduled in MyEdBC (contact your SIS administrator if not done)

---

## Step 1 — Download DistrictSync

1. Visit the [Releases page](https://github.com/myblueprint-spaces/DistrictSync/releases/latest)
2. Download the file for your platform:
   - **Windows:** `DistrictSync-windows.exe`
   - **Linux:** `DistrictSync-linux`

3. Put the file somewhere sensible. **The .exe can live anywhere** —
   Desktop, `C:\Program Files\DistrictSync\`, `/opt/districtsync/`, a USB
   stick, a shared network drive. Your settings, logs, and any
   custom mappings are written to your user home directory
   (see [Where does data live?](#where-does-data-live) below), not
   next to the .exe, so the exe location is purely your preference.

   Suggested layouts:

=== "Windows"
    ```
    C:\DistrictSync\
      DistrictSync-windows.exe
    ```

=== "Linux"
    ```bash
    sudo mkdir -p /opt/districtsync
    sudo mv DistrictSync-linux /opt/districtsync/DistrictSync
    sudo chmod +x /opt/districtsync/DistrictSync
    ```

### Where does data live?

All runtime state is stored in `~/.districtsync/` (your user home
directory). This works the same on every platform and survives
updates to the .exe:

| File | Purpose |
|------|---------|
| `~/.districtsync/config.json` | Wizard settings (input/output paths, SFTP host, schedule) |
| `~/.districtsync/etl_tool.log` | All ETL run history — wizard runs, scheduled runs, and CLI runs all write here |
| `~/.districtsync/mappings/*.yaml` | Any district mappings you create in the Mapping Editor (persists across exe upgrades) |
| OS credential store | SFTP password (Windows Credential Manager / macOS Keychain / Linux Secret Service) |

On Windows that path is
`C:\Users\<your-username>\.districtsync\`. You can back up or inspect
this folder at any time. Deleting it resets the tool to first-run
state.

---

## Step 2 — Run the Setup Wizard (Windows only)

!!! note "Linux / headless / Docker partners"
    On Linux or in a container, skip to
    [Step 3 — Headless configuration](#step-3-headless-configuration-linux-docker-no-browser)
    or see the dedicated [Headless & Docker SFTP Setup](headless-sftp-setup.md) guide.

1. Double-click `DistrictSync-windows.exe`
2. Your browser will open automatically at `http://localhost:8501`
3. Follow the 5-step Setup Wizard:

### Wizard Step 1 — File Paths

| Field | Example | Notes |
|-------|---------|-------|
| GDE Input Directory | `C:\DistrictSync\input` | Where MyEdBC places the GDE files |
| CSV Output Directory | `C:\DistrictSync\output` | Where CSVs will be written |

Click **Validate & Continue**.

### Wizard Step 2 — District Configuration

Select your district from the dropdown. If your district is not listed, contact SpacesEDU support.

### Wizard Step 3 — Schedule

Optionally enable a daily automated schedule. If you only need ad-hoc runs via the Convert page, you can leave this disabled.

If enabled, choose the daily run time. We recommend **03:00 AM** (3am) — after the overnight GDE export from MyEdBC has completed.

### Wizard Step 4 — SFTP Upload

Enter the SFTP credentials provided by SpacesEDU:

| Field | Example |
|-------|---------|
| SFTP Host | `sftp.spacesEDU.com` |
| Port | `22` |
| Username | (provided by SpacesEDU) |
| Password | (provided by SpacesEDU) |
| Remote Path | `/files` |

Click **Test Connection** to verify the credentials work.

### Wizard Step 5 — Save & Activate

Review your settings and click **Save & Activate Schedule** (or **Save Configuration** if no schedule was enabled).

If a schedule was enabled, DistrictSync will create a Windows Task Scheduler entry named `DistrictSync_Daily` that runs every day at the time you specified.

After saving, the Setup Wizard switches to a management dashboard where you can view, edit, or disable the schedule and SFTP settings at any time without re-running the wizard.

!!! tip "Custom district mapping?"
    Need a custom district mapping? Use the **Mapping Editor** page to create or modify mappings visually.

---

## Step 3 — Headless configuration (Linux / Docker / no browser)

On Linux servers or containers with no browser, use the CLI to
configure SFTP directly. No config-file hand-editing and no Python
one-liners needed.

```bash
# Interactive — prompts for each field (password input is hidden):
/opt/districtsync/DistrictSync --sftp-configure

# Or fully scripted with the password in an env var:
export DISTRICTSYNC_SFTP_PASSWORD='your-password-here'
/opt/districtsync/DistrictSync --sftp-configure \
  --sftp-host sftp.ca.spacesedu.com \
  --sftp-user your_username \
  --sftp-remote /files
unset DISTRICTSYNC_SFTP_PASSWORD

# Verify:
/opt/districtsync/DistrictSync --sftp-test
```

The host/port/user/remote path are written to `~/.districtsync/config.json`;
the password is stored in the OS credential store via the `keyring`
library (Linux Secret Service, macOS Keychain, or Windows Credential
Manager — depending on the platform).

For Docker, secrets-manager integration, `libsecret` backends, and
scripted password piping via stdin, see the dedicated guide:
**[Headless & Docker SFTP Setup](headless-sftp-setup.md)**.

### Add the crontab entry

```bash
# Run daily at 3:00 AM
(crontab -l 2>/dev/null; echo "0 3 * * * /opt/districtsync/DistrictSync --sis myedbc --input /data/gde/input --output /data/gde/output --sftp # DistrictSync managed entry") | crontab -
```

---

## Step 4 — Verify the setup

### Test a manual run

=== "Windows"
    Open Command Prompt as Administrator:
    ```cmd
    C:\DistrictSync\DistrictSync-windows.exe --sis myedbc --input C:\DistrictSync\input --output C:\DistrictSync\output --dry-run
    ```

=== "Linux"
    ```bash
    /opt/districtsync/DistrictSync --sis myedbc --input /data/gde/input --output /data/gde/output --dry-run
    ```

A successful dry run prints a summary like:

```
=== DRY RUN (no files written) ===
  Students: 1,842 rows
  Staff: 47 rows
  Family: 3,201 rows
  Classes: 284 rows
  Enrollments: 12,456 rows
```

### Verify Task Scheduler (Windows)

1. Open **Task Scheduler** (search in Start menu)
2. Look for **DistrictSync_Daily** in the task list
3. Right-click → **Run** to trigger a test run immediately

---

## Step 5 — Check the log

The ETL log is written to `etl_tool.log` in the current working directory at run time.

- **Windows:** Task Scheduler's **Start in** field controls this — set it to `C:\DistrictSync\` and the log appears there. The Setup Wizard sets this automatically.
- **Linux:** The log is written to whichever directory you run the command from (e.g. `/opt/districtsync/`).

=== "Windows"
    ```
    C:\DistrictSync\etl_tool.log
    ```

=== "Linux"
    ```bash
    tail -50 /opt/districtsync/etl_tool.log
    ```

A successful run ends with:

```
INFO - ETL process completed successfully.
INFO - Committed 5 output file(s) to C:\DistrictSync\output
INFO - SFTP upload complete: 5 file(s) uploaded
```

---

## What happens each day

1. **03:00 AM** — Task Scheduler / cron starts `DistrictSync`
2. Tool reads GDE files from the input directory
3. Transforms data into 5 CSV files
4. Checks for anomalies — if any entity's record count has dropped more than 20% compared to the previous run, a warning is logged
5. Writes all 5 CSVs atomically (all succeed or none are committed)
6. Zips all 5 CSVs into a single dated file (`districtsync_YYYY-MM-DD.zip`) and uploads to SpacesEDU via SFTP
7. Writes a detailed log entry to `etl_tool.log`

---

## District-specific notes

| District | Config name | Notes |
|----------|-------------|-------|
| Default (MyEdBC) | `myedbc` | Standard filenames |
| SD40 – New Westminster | `sd40myedbc` | CSV files with SD-40_/SD40- prefix. StudentSchedule has no headers (auto-injected via config). |
| SD48 – Sea to Sky | `sd48myedbc` | Uses `StudentDemographicEnhanced.txt`, `StaffInformation.txt` |
| SD51 – Boundary | `sd51myedbc` | Contact SpacesEDU for file naming |
| SD74 – Gold Trail | `sd74myedbc` | Uses `studentcourseselection.txt`, `StaffInformation.txt`, `ParentInformation.txt` |

---

## Getting help

If you encounter issues:

1. Check the [Troubleshooting Guide](troubleshooting.md)
2. Review `etl_tool.log` for error details
3. Contact SpacesEDU support with the log attached
