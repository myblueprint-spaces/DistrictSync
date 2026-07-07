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
| `~/.districtsync/mappings/*.yaml` | Any custom district mapping YAML provided by the DistrictSync team (persists across exe upgrades) |
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

!!! warning "Enabling a schedule? Launch as administrator"
    If you will turn on the daily automated schedule (Step 3 below), **right-click `DistrictSync-windows.exe` → "Run as administrator"** instead of double-clicking. Creating a task that runs **unattended** (whether or not you are logged on) requires administrator rights — without them the schedule step fails with *"Access is denied."* This is a one-time setup requirement; the scheduled task itself runs on its own afterward. Ad-hoc-only use (the Convert surface, no schedule) does **not** need administrator rights.

1. Double-click `DistrictSync-windows.exe` — or **right-click → "Run as administrator"** if you will enable a schedule
2. The DistrictSync desktop window opens directly — no browser involved
3. Go to the **Setup** surface and follow its 5 steps:

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

!!! note "Unattended runs need administrator rights"
    Activating a schedule needs the wizard launched **as administrator** (see Step 2). If you launched it normally, this step reports *"Access is denied — run as administrator"*; close the wizard, relaunch it elevated, and try again.

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
    Need a custom district mapping? The **Mapping** surface lets you review and switch between pre-built configs, but creating or editing a mapping is not done in the app — contact SpacesEDU support and the DistrictSync team will provide the YAML config for your district.

---

## Step 3 — Headless configuration (Linux / Docker / no browser)

On Linux servers or containers where the desktop app can't run, use the CLI to
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
INFO - Committed output file(s) to C:\DistrictSync\output
INFO - SFTP upload complete
```

The number of output files depends on which config you're using — see the [Output CSVs](#output-csvs) section below.

---

## What happens each day

1. **03:00 AM** — Task Scheduler / cron starts `DistrictSync`
2. Tool reads GDE files from the input directory
3. Transforms data into the CSV files enabled by your config (5 for standard rostering, 7 with myBlueprint+, or a subset)
4. Checks for anomalies — if any entity's record count has dropped more than 20% compared to the previous run, a warning is logged
5. Writes all enabled CSVs atomically (all succeed or none are committed)
6. Zips them into a single dated file (`districtsync_YYYY-MM-DD.zip`) and uploads to SpacesEDU via SFTP
7. Writes a detailed log entry to `etl_tool.log`

### Output CSVs

| Config | CSVs produced |
|---|---|
| `myedbc` (and inheriting district configs `sd40myedbc`, `sd48myedbc`, …) | `Students.csv`, `Staff.csv`, `Family.csv`, `Classes.csv`, `Enrollments.csv` |
| `mbp_all` | All 5 above + `CourseInfo.csv` + `StudentCourses.csv` (full myBlueprint+ tier) |
| `mbp_core` | `Students.csv`, `CourseInfo.csv`, `StudentCourses.csv` only (minimal myBlueprint+ tier) |

---

## District-specific notes

| District | Config name | Notes |
|----------|-------------|-------|
| Default (MyEdBC) | `myedbc` | Standard filenames |
| SD40 – New Westminster | `sd40myedbc` | CSV files with SD-40_/SD40- prefix. StudentSchedule has no headers (auto-injected via config). |
| SD48 – Sea to Sky | `sd48myedbc` | Uses `StudentDemographicEnhanced.txt`, `StaffInformation.txt` |
| SD51 – Boundary | `sd51myedbc` | Contact SpacesEDU for file naming |
| SD60 – Peace River North | `sd60myedbc` | Guardians-only family import; dual-school students rostered under home school |
| SD74 – Gold Trail | `sd74myedbc` | Uses `studentcourseselection.txt`, `StaffInformation.txt`, `ParentInformation.txt` |

---

## Getting help

If you encounter issues:

1. Check the [Troubleshooting Guide](troubleshooting.md)
2. Review `etl_tool.log` for error details
3. Contact SpacesEDU support with the log attached
