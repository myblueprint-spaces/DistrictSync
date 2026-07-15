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

All runtime state is stored in the standard per-user application-data
folder for your operating system — **not** next to the .exe — so moving
or re-downloading the program never loses your settings or history:

| Platform | Data folder |
|----------|-------------|
| Windows | `C:\Users\<username>\AppData\Local\DistrictSync\` |
| macOS | `~/Library/Application Support/DistrictSync/` |
| Linux | `$XDG_DATA_HOME/DistrictSync/` (default `~/.local/share/DistrictSync/`) |

Inside that folder:

| File | Purpose |
|------|---------|
| `config.json` | Wizard settings (input/output paths, SFTP host, schedule) |
| `history.db` | Run-history database — every wizard, scheduled, and CLI run is recorded here; the **Run History** surface reads it |
| `etl_tool.log` | Diagnostic log (rotates at 5 MB, keeps 3 backups) — detailed messages for troubleshooting |
| `mappings/*.yaml` | Any custom district mapping YAML provided by the DistrictSync team (persists across exe upgrades) |
| OS credential store | SFTP password (Windows Credential Manager / macOS Keychain / Linux Secret Service) |

You can back up or inspect this folder at any time. Deleting it resets
the tool to first-run state.

!!! note "Upgrading from an older version?"
    Earlier releases stored this data in a `.districtsync` folder in your
    home directory (e.g. `C:\Users\<username>\.districtsync\`). The first
    time you run a newer version, DistrictSync copies your settings, logs,
    and history into the new location above and leaves a small `MOVED.txt`
    note in the old folder pointing at the new one. The move is safe and
    one-time — if anything prevents it, the tool simply keeps using the old
    folder (you are never left half-moved).

---

## Step 2 — Run the Setup Wizard (Windows only)

!!! note "Linux / headless / Docker partners"
    On Linux or in a container, skip to
    [Step 3 — Headless configuration](#step-3-headless-configuration-linux-docker-no-browser)
    or see the dedicated [Headless & Docker SFTP Setup](headless-sftp-setup.md) guide.

!!! note "Enabling a schedule? Expect one Windows permission prompt"
    When you turn on the daily automated schedule (Step 4 below), DistrictSync shows **one Windows permission prompt (UAC)** as it registers the task — creating a task that runs **unattended** (whether or not you are logged on) needs administrator rights, and DistrictSync requests them just for that one step. Click **Yes** on the prompt. You do **not** need to run the whole app as administrator, and ad-hoc-only use (the Convert surface, no schedule) shows no prompt at all.

1. Double-click `DistrictSync-windows.exe`
2. The DistrictSync desktop window opens directly — no browser involved
3. Go to the **Setup** surface and follow its 5 steps (the Schedule and Delivery steps are optional — skip either and set it up later):

### Wizard Step 1 — File Paths

| Field | Example | Notes |
|-------|---------|-------|
| GDE Input Directory | `C:\DistrictSync\input` | Where MyEdBC places the GDE files |
| CSV Output Directory | `C:\DistrictSync\output` | Where CSVs will be written |

Click **Validate & Continue**.

### Wizard Step 2 — District Configuration

Select your district from the dropdown. If your district is not listed, contact SpacesEDU support.

### Wizard Step 3 — SFTP Upload (Delivery)

Enter the SFTP credentials provided by SpacesEDU:

| Field | Example |
|-------|---------|
| SFTP Host | `sftp.spacesEDU.com` |
| Port | `22` |
| Username | (provided by SpacesEDU) |
| Password | (provided by SpacesEDU) |
| Remote Path | `/files` |

Click **Test Connection** to verify the credentials work.

### Wizard Step 4 — Schedule

Optionally enable a daily automated schedule. If you only need ad-hoc runs via the Convert page, you can leave this disabled.

If enabled, choose the daily run time. We recommend **03:00 AM** (3am) — after the overnight GDE export from MyEdBC has completed.

!!! note "Unattended runs need administrator rights — granted via the permission prompt"
    An unattended task runs whether or not you're logged on, which needs administrator rights. DistrictSync requests them with **one Windows permission prompt** as it registers the task — click **Yes**. If you decline, nothing is changed and you can try again. You'll also enter your **Windows account password** so the task can sign in and run while you're logged off.

### Wizard Step 5 — Finish

The wizard shows an honest summary of what it actually checked — for example *"we tested the connection to `<host>` as `<user>` just now and it worked"*, or, if you skipped a step, what's left to set up later. If a schedule was enabled, DistrictSync creates a Windows Task Scheduler entry named `DistrictSync_Daily` that runs every day at the time you specified.

Once you finish the wizard, the **Setup** surface graduates into a flat **Settings** page (the rail label stays "Setup") where you can review, edit, or remove the schedule and SFTP settings at any time without re-running the wizard. It leads with the schedule card and has a single **Save** that re-registers the task whenever a setting baked into it changes.

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

The host/port/user/remote path are written to `config.json` in DistrictSync's
per-user data folder (see [Where does data live?](#where-does-data-live) above);
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

The diagnostic log is always written to `etl_tool.log` in DistrictSync's
per-user data folder (see [Where does data live?](#where-does-data-live) above) —
regardless of where the `.exe` lives or which working directory a scheduled task
runs from. It rotates automatically at 5 MB and keeps 3 backups.

=== "Windows"
    ```
    C:\Users\<username>\AppData\Local\DistrictSync\etl_tool.log
    ```

=== "Linux"
    ```bash
    tail -50 ~/.local/share/DistrictSync/etl_tool.log
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
| SD60 – Peace River North | `sd60myedbc` | Guardians-only family import; dual-school students rostered under home school; student emails generated as `firstname+lastname+admission-year@learn60.ca`; `Active No Primary` excluded |
| SD74 – Gold Trail | `sd74myedbc` | Uses `studentcourseselection.txt`, `StaffInformation.txt`, `ParentInformation.txt` |

---

## Getting help

If you encounter issues:

1. Check the [Troubleshooting Guide](troubleshooting.md)
2. Review `etl_tool.log` for error details
3. Contact SpacesEDU support with the log attached
