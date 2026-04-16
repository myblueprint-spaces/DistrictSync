# Headless & Docker SFTP Setup

If your district server has no browser (headless Linux, a locked-down
Windows Server Core, or a container), you can configure SpacesEDU SFTP
upload entirely from the command line — no Streamlit wizard required.

DistrictSync ships three CLI subcommands for credential management:

| Command | Purpose |
|---------|---------|
| `DistrictSync --sftp-configure` | Save host/port/user/remote path + store password in the OS credential store |
| `DistrictSync --sftp-test` | Verify the stored credentials by opening an SFTP session and listing the remote path |
| `DistrictSync --sftp-show` | Print the current SFTP configuration (never prints the password) |

The password is stored in the OS credential store via the cross-platform
[`keyring`](https://pypi.org/project/keyring/) library — Windows
Credential Manager, macOS Keychain, or Linux Secret Service (GNOME
Keyring / KWallet / libsecret). **The password is never written to disk
in plaintext.**

---

## Option 1 — Interactive prompt

Run with `--sftp-configure` and no other flags. The tool prompts for
each field and hides the password:

```bash
DistrictSync --sftp-configure
```

Example session:

```text
SpacesEDU SFTP setup — press Ctrl+C to cancel.
Allowed hosts: sftp.app.spacesedu.com, sftp.ca.spacesedu.com, sftp.myblueprint.ca
Host [sftp.ca.spacesedu.com]:
Port [22]:
Username []: district_x
Remote path [/files]:
SFTP password:
SFTP configured: district_x@sftp.ca.spacesedu.com:22/files
Password saved to the OS credential store.
Run 'DistrictSync --sftp-test' to verify the connection.
```

Then verify:

```bash
DistrictSync --sftp-test
# → Connection to sftp.ca.spacesedu.com:22 successful.
```

---

## Option 2 — Headless / scripted (env var)

Pass every field as a flag and supply the password through the
`DISTRICTSYNC_SFTP_PASSWORD` environment variable. The command never
prompts.

```bash
export DISTRICTSYNC_SFTP_PASSWORD='your-password-here'
DistrictSync --sftp-configure \
  --sftp-host sftp.ca.spacesedu.com \
  --sftp-user district_x \
  --sftp-remote /files
unset DISTRICTSYNC_SFTP_PASSWORD
```

This is the right pattern for shell scripts, Ansible/Chef runbooks, and
configuration-management tools.

---

## Option 3 — Headless (stdin)

Pipe the password through stdin with `--sftp-password-stdin`. Useful
when the password lives in a secrets file:

```bash
cat /run/secrets/sftp_password | DistrictSync --sftp-configure \
  --sftp-host sftp.ca.spacesedu.com \
  --sftp-user district_x \
  --sftp-remote /files \
  --sftp-password-stdin
```

---

## Daily ETL + upload

Once configured, daily runs just add `--sftp`:

```bash
DistrictSync --sis myedbc \
  --input /data/gde/input \
  --output /data/gde/output \
  --sftp
```

The CLI reads the saved host/port/user/remote path from
`~/.districtsync/config.json`, retrieves the password from the OS keyring,
zips the output CSVs to `districtsync_<sis>_<YYYY-MM-DD>.zip`, and uploads.

---

## Docker

Containers need three things to make `keyring` work:

1. A keyring backend installed (or an alternative — see "No keyring
   backend" below).
2. The password supplied at container startup (never baked into the
   image).
3. Persistence of `~/.districtsync/config.json` so settings survive restarts.

### Dockerfile

```dockerfile
FROM python:3.11-slim

# System deps: libsecret for the keyring backend, plus dbus for the session.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsecret-1-0 \
    dbus \
    && rm -rf /var/lib/apt/lists/*

# Either install the published binary...
# ADD https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-linux /usr/local/bin/DistrictSync
# RUN chmod +x /usr/local/bin/DistrictSync

# ...or install from source:
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY config ./config
ENV PYTHONPATH=/app
ENTRYPOINT ["python", "-m", "src.main"]
```

### docker-compose.yml

```yaml
services:
  districtsync:
    build: .
    volumes:
      - districtsync_config:/root/.districtsync   # persists ~/.districtsync
      - ./input:/data/input               # GDE export drop
      - ./output:/data/output             # generated CSVs
    environment:
      - DISTRICTSYNC_SFTP_PASSWORD=${SFTP_PASSWORD}
    command: >
      --sis myedbc
      --input /data/input
      --output /data/output
      --sftp

volumes:
  districtsync_config:
```

### One-time config inside the container

```bash
# Populate the secret password from the host shell, not the image.
export SFTP_PASSWORD='your-password-here'

# First-time setup — runs, stores credentials, exits.
docker compose run --rm districtsync \
  --sftp-configure \
  --sftp-host sftp.ca.spacesedu.com \
  --sftp-user district_x \
  --sftp-remote /files

# Verify.
docker compose run --rm districtsync --sftp-test

# Daily runs can now proceed on schedule.
```

### No keyring backend (container / minimal Linux)

If your image has no `libsecret`/GNOME Keyring and you can't install
one, use `keyrings.alt` which stores credentials in an encrypted file
inside the container's `~/.districtsync` volume:

```bash
pip install keyrings.alt
```

Then on first run, `keyring` will auto-select the file-based backend.
This is less secure than a native OS keychain but is suitable for
single-tenant container deployments where the volume is private.

---

## Cron / Task Scheduler

Schedule the daily run just like any other command — no special
handling is required because the password lives in the keyring:

=== "Linux crontab"
    ```cron
    0 3 * * * /opt/districtsync/DistrictSync --sis myedbc --input /data/gde/input --output /data/gde/output --sftp
    ```

=== "Windows Task Scheduler"
    ```cmd
    schtasks /Create /SC DAILY /ST 03:00 /TN DistrictSync_Daily ^
      /TR "C:\DistrictSync\DistrictSync-windows.exe --sis myedbc --input C:\DistrictSync\input --output C:\DistrictSync\output --sftp"
    ```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No module named 'keyring'` | Install the released `.exe` (deps are bundled) or `pip install -r requirements.txt` |
| `No SFTP password found` | Run `DistrictSync --sftp-configure` again — the keyring entry is missing |
| `SFTP host 'X' is not allowed` | Only the SpacesEDU SFTP hosts are accepted; contact support for the correct host |
| `Connection failed: Authentication failed` | Re-run `--sftp-configure`; the stored password is wrong or has been rotated |
| `No recommended backend was available` (Linux) | Install `libsecret-1-0` + `dbus`, or `pip install keyrings.alt` |
| `--sftp-password-stdin` hangs | stdin must be piped; don't run interactively with that flag |

See [Troubleshooting](troubleshooting.md) for non-SFTP issues.
