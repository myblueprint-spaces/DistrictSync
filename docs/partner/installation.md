# Partner Installation Guide

This guide walks you through installing and configuring GDE2Acsv on your school district's server. The entire process takes approximately 15–20 minutes.

---

## Prerequisites

Before you begin, ensure you have:

- [ ] A Windows Server (2016 or later) or Linux server
- [ ] Administrator / sudo access to the server
- [ ] The SFTP credentials provided by SpacesEDU (host, username, password)
- [ ] A directory where MyEdBC will place the GDE export files
- [ ] The GDE export scheduled in MyEdBC (contact your SIS administrator if not done)

---

## Step 1 — Download GDE2Acsv

1. Visit the [Releases page](https://github.com/myblueprint/GDE2Acsv/releases/latest)
2. Download the file for your platform:
   - **Windows:** `GDE2Acsv-windows.exe`
   - **Linux:** `GDE2Acsv-linux`

3. Create a dedicated folder and place the file there:

=== "Windows"
    ```
    C:\GDE2Acsv\
      GDE2Acsv-windows.exe
    ```

=== "Linux"
    ```bash
    sudo mkdir -p /opt/gde2acsv
    sudo mv GDE2Acsv-linux /opt/gde2acsv/GDE2Acsv
    sudo chmod +x /opt/gde2acsv/GDE2Acsv
    ```

---

## Step 2 — Run the Setup Wizard (Windows only)

!!! note "Linux partners"
    On Linux, skip to [Step 3 — Manual configuration](#step-3-manual-configuration-linux).

1. Double-click `GDE2Acsv-windows.exe`
2. Your browser will open automatically at `http://localhost:8501`
3. Follow the 5-step Setup Wizard:

### Wizard Step 1 — File Paths

| Field | Example | Notes |
|-------|---------|-------|
| GDE Input Directory | `C:\GDE2Acsv\input` | Where MyEdBC places the GDE files |
| CSV Output Directory | `C:\GDE2Acsv\output` | Where CSVs will be written |

Click **Validate & Continue**.

### Wizard Step 2 — District Configuration

Select your district from the dropdown. If your district is not listed, contact SpacesEDU support.

### Wizard Step 3 — Schedule

Choose the daily run time. We recommend **03:00 AM** (3am) — after the overnight GDE export from MyEdBC has completed.

### Wizard Step 4 — SFTP Upload

Enter the SFTP credentials provided by SpacesEDU:

| Field | Example |
|-------|---------|
| SFTP Host | `sftp.spacesEDU.com` |
| Port | `22` |
| Username | (provided by SpacesEDU) |
| Password | (provided by SpacesEDU) |
| Remote Path | `/upload` |

Click **Test Connection** to verify the credentials work.

### Wizard Step 5 — Activate

Review your settings and click **Save & Activate Schedule**.

GDE2Acsv will create a Windows Task Scheduler entry named `GDE2Acsv_Daily` that runs every day at the time you specified.

---

## Step 3 — Manual Configuration (Linux)

On Linux, create a configuration file and set up a crontab entry manually.

### Create the config directory

```bash
mkdir -p ~/.gde2acsv
```

### Create the config file

```bash
cat > ~/.gde2acsv/config.json << 'EOF'
{
  "input_dir": "/data/gde/input",
  "output_dir": "/data/gde/output",
  "sis_type": "myedbc",
  "schedule_time": "03:00",
  "schedule_task_name": "GDE2Acsv_Daily",
  "schedule_registered": false,
  "sftp_enabled": true,
  "sftp_host": "sftp.spacesEDU.com",
  "sftp_port": 22,
  "sftp_username": "your_username",
  "sftp_remote_path": "/upload"
}
EOF
```

### Store the SFTP password

```bash
python3 -c "
import keyring
keyring.set_password('GDE2Acsv_SFTP', 'your_username', 'your_password')
print('Password stored.')
"
```

### Add the crontab entry

```bash
# Run daily at 3:00 AM
(crontab -l 2>/dev/null; echo "0 3 * * * /opt/gde2acsv/GDE2Acsv --sis myedbc --input /data/gde/input --output /data/gde/output --sftp # GDE2Acsv managed entry") | crontab -
```

---

## Step 4 — Verify the setup

### Test a manual run

=== "Windows"
    Open Command Prompt as Administrator:
    ```cmd
    C:\GDE2Acsv\GDE2Acsv-windows.exe --sis myedbc --input C:\GDE2Acsv\input --output C:\GDE2Acsv\output --dry-run
    ```

=== "Linux"
    ```bash
    /opt/gde2acsv/GDE2Acsv --sis myedbc --input /data/gde/input --output /data/gde/output --dry-run
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
2. Look for **GDE2Acsv_Daily** in the task list
3. Right-click → **Run** to trigger a test run immediately

---

## Step 5 — Check the log

The ETL log is written to `etl_tool.log` in the same directory as the executable (or `~/.gde2acsv/etl_tool.log` on Linux).

=== "Windows"
    ```
    C:\GDE2Acsv\etl_tool.log
    ```

=== "Linux"
    ```bash
    tail -50 ~/.gde2acsv/etl_tool.log
    ```

A successful run ends with:

```
INFO - ETL process completed successfully.
INFO - Committed 5 output file(s) to C:\GDE2Acsv\output
INFO - SFTP upload complete: 5 file(s) uploaded
```

---

## What happens each day

1. **03:00 AM** — Task Scheduler / cron starts `GDE2Acsv`
2. Tool reads GDE `.txt` files from the input directory
3. Transforms data into 5 CSV files
4. Writes all 5 CSVs atomically (all succeed or none are committed)
5. Uploads all 5 CSVs to SpacesEDU via SFTP
6. Writes a detailed log entry to `etl_tool.log`

---

## District-specific notes

| District | Config name | Notes |
|----------|-------------|-------|
| Default (MyEdBC) | `myedbc` | Standard filenames |
| SD48 – Sea to Sky | `sd48myedbc` | Uses `StudentDemographicEnhanced.txt`, `StaffInformation.txt` |
| SD51 – Boundary | `sd51myedbc` | Contact SpacesEDU for file naming |
| SD74 – Gold Trail | `sd74myedbc` | Uses `studentcourseselection.txt`, `StaffInformation.txt`, `ParentInformation.txt` |

---

## Getting help

If you encounter issues:

1. Check the [Troubleshooting Guide](troubleshooting.md)
2. Review `etl_tool.log` for error details
3. Contact SpacesEDU support with the log attached
