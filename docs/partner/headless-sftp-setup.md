# Headless & Docker SFTP Setup

If your district server has no display (headless Linux, a locked-down
Windows Server Core, or a container), you can configure SpacesEDU SFTP
upload entirely from the command line — no desktop UI required.

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

!!! note "On Windows, run these from a terminal — not by double-clicking"
    The Windows `.exe` is a windowed application (double-clicking opens the
    desktop app, not a console). When you launch it from Command Prompt or
    PowerShell **with** arguments it attaches to that window, so the prompts and
    confirmations below appear there as shown. Launched with no terminal attached
    — a scheduled task, a service, or a double-click — it prints nothing and
    cannot prompt; use the flag-driven or stdin forms (Options 2 and 3) in that
    case, or configure SFTP from the desktop app's Setup screen.

    Because the app is windowed, your shell returns to the prompt immediately.
    That is cosmetic for the non-interactive forms; for the interactive prompt in
    Option 1, run it as `start /wait DistrictSync --sftp-configure` so the shell
    stays with the session.

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

The CLI reads the saved host/port/user/remote path from `config.json` in
DistrictSync's per-user data folder (on Linux, `~/.local/share/DistrictSync`),
retrieves the password from the OS keyring, zips the rostering CSVs to
`districtsync_<sis>_<YYYY-MM-DD>.zip`, and uploads that zip plus any
`StudentAttendance.csv` / `CourseInfo.csv` / `StudentCourses.csv` as
standalone files.

---

## Docker

Containers need three things to make `keyring` work:

1. A keyring backend installed (or an alternative — see "No keyring
   backend" below).
2. The password supplied at container startup (never baked into the
   image).
3. Persistence of DistrictSync's data folder (`~/.local/share/DistrictSync`,
   i.e. `/root/.local/share/DistrictSync` for the `root` user) so settings
   survive restarts.

### Dockerfile

```dockerfile
FROM python:3.13-slim

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
      - districtsync_config:/root/.local/share/DistrictSync   # persists config, logs, run history
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
inside the container's DistrictSync data-folder volume:

```bash
pip install keyrings.alt
```

Then on first run, `keyring` will auto-select the file-based backend.
This is less secure than a native OS keychain but is suitable for
single-tenant container deployments where the volume is private.

---

## Cron / Task Scheduler

The saved password lives in the OS keyring, and the keyring is **per Windows
account** — Windows Credential Manager has no cross-user scope. So the rule
is: **no special handling is required only when the scheduled task runs as
the same account that ran `--sftp-configure`.** If the task's run-as account
is different from that — most commonly a service account — that account
needs its own keyring entry, seeded by running `--sftp-configure` as it,
once. See [Running the nightly sync as a service
account](#running-the-nightly-sync-as-a-service-account) below for the full
procedure.

=== "Linux crontab"
    ```cron
    0 3 * * * /opt/districtsync/DistrictSync --sis myedbc --input /data/gde/input --output /data/gde/output --sftp
    ```

=== "Windows Task Scheduler"
    On a machine **with a desktop**, use DistrictSync's own Setup → Schedule
    step instead of `schtasks` directly — it refuses to register a different
    account with no password, reports what Windows returns in plain language,
    and records which account the task runs as so the app's own run checks stay
    honest afterwards. (Windows itself validates the account and password, in
    both paths.) Raw
    `schtasks` below is for **headless Windows Server Core only**, where
    there's no desktop to run the wizard on.

    Registered as whoever runs this command:

    ```cmd
    schtasks /Create /SC DAILY /ST 03:00 /TN DistrictSync_Daily ^
      /TR "C:\DistrictSync\DistrictSync-windows.exe --sis myedbc --input C:\DistrictSync\input --output C:\DistrictSync\output --sftp"
    ```

    To register it against a specific account instead — a service account,
    most commonly — add `/RU` and `/RP`:

    ```cmd
    schtasks /Create /SC DAILY /ST 03:00 /TN DistrictSync_Daily ^
      /RU DOMAIN\SVC_DistrictSync /RP "that account's password" ^
      /TR "C:\DistrictSync\DistrictSync-windows.exe --sis myedbc --input C:\DistrictSync\input --output C:\DistrictSync\output --sftp"
    ```

    None of DistrictSync's own checks apply to a raw `schtasks` registration —
    the blank-password refusal, the plain-language error mapping and the record
    of which account the task runs as all live in the app's Schedule step. It's
    also subject to
    the same Windows security policy that can block DistrictSync's own
    scheduler from saving a task password: **"Network access: Do not allow
    storage of passwords and credentials for network authentication"**
    (`HKLM\SYSTEM\CurrentControlSet\Control\Lsa\DisableDomainCreds`). See
    [Troubleshooting](troubleshooting.md#task-scheduler-does-not-run-the-task)
    for what that looks like and what to do about it.

---

## Running the nightly sync as a service account

Two districts (SD54, SD60) asked for this independently, in August and
September 2026: a service account survives its owner leaving, rotating a
password, or being disabled — a personal login does not. DistrictSync's own
Setup → Schedule step (the Windows desktop app) is the supported way to set
it up: type the account name and its password there. This section explains
what that step needs from the account and from Windows, and what to expect
once it's running.

This covers a **named account with a password** — a domain service account
or a local admin account. It does not cover `SYSTEM`, a group-managed
service account (gMSA), `LOCAL SERVICE` or `NETWORK SERVICE`; none of those
are supported today (see the project ROADMAP).

### The one-time `--sftp-configure` step — necessary, not optional

Windows Credential Manager has no cross-user scope: `keyring`'s Windows
backend stores the SFTP password with `CRED_PERSIST_ENTERPRISE`, and
Microsoft documents that persistence level as readable only "for this user
on other computers" — never by a *different* account on the same machine
(**Documented**, see the Windows `CREDENTIALW` reference). So a password
stored under the admin's own login is structurally invisible to a task
running as a service account. The only way that account gets its own
readable credential is to run `--sftp-configure` as it, once:

```cmd
runas /user:<account> "C:\DistrictSync\DistrictSync-windows.exe --sftp-configure"
```

Use the **full path** — `runas` does not resolve the working directory you
started from. The shipped Windows build is a *windowed* executable and this
prompt is interactive, so it needs a console it can attach to; if you get no
prompt, use the non-interactive form described above instead — the
`DISTRICTSYNC_SFTP_PASSWORD` environment variable or `--sftp-password-stdin`,
run under the same `runas` session.

Use `.\SVC_DistrictSync` for a local account or `DOMAIN\SVC_DistrictSync`
for a domain one — both forms validate wherever DistrictSync accepts a
run-as account. `runas` performs an **interactive** logon, so the account
needs the right to log on locally for this one step, even though the
nightly task itself only ever needs the batch-logon right (below) — if a
"Deny log on locally" policy applies to the account, ask your IT team to
lift it for this one step.

This step does double duty: it's also Microsoft's own recommended way
around a documented Task Scheduler quirk. A batch-logon task's user profile
loads *asynchronously*, and on that task's very first run it "may not be
fully loaded" — `%USERPROFILE%` can resolve to a default profile rather than
the account's own (Microsoft KB 2968540 — **Documented**). Running an
interactive `runas` first forces the account's profile to exist before the
nightly task ever touches it. Verify afterward with
`DistrictSync-windows.exe --sftp-test`, run as the same account.

### Domain accounts vs. local accounts

Both are supported. DistrictSync's run-as account validation accepts a bare
local name, `.\name`, or `DOMAIN\name`. (A UPN, `user@domain`, is **not**
accepted — see the ROADMAP.)

- **Local account.** The `runas` step above is everything this account
  needs.
- **Domain account** (SD54's case). The first time that account's DPAPI
  master key is used on a given machine, Windows creates it and backs it up
  to a reachable, *writable* domain controller. If that backup fails, the
  key is never created, and Credential Manager itself refuses to open —
  Windows code `0x80090345` (Microsoft KB 3205778 — **Documented**). Run the
  `runas` step while this machine has a working connection to a domain
  controller — not, for example, mid-reconnect over a VPN — and simply retry
  once connectivity is confirmed if it fails the first time. This is not a
  DistrictSync fault, and DistrictSync cannot detect or work around it.

**What backs this guidance, and where it stops.** DistrictSync measured, on
2026-09-16, that a batch-logon task *can* read back a credential its own
account wrote interactively — the mechanism this whole procedure depends on
does work. That measurement, though, was taken on a standalone, non-domain
Windows host, using a test account that was *already* interactively signed
in — its profile already warm, exactly the condition the `runas` step above
exists to create ahead of time. It's evidence the mechanism works, not a
guarantee for a domain-joined server with roaming profiles under group
policy, which was not — and could not be — tested on that host. Treat the
domain guidance above as a documented risk to plan around, not something
DistrictSync has reproduced.

### Rights the account needs

- **"Log on as a batch job."** Any unattended (password) registration needs
  this, service account or not — a freshly created service account is the
  account most likely to be missing it.
- **Modify, never Full Control, on the input and output folders.** Every
  unattended task still registers with the highest available privileges on
  this account — dropping that could break a district whose output folder
  needs an elevated token to write to. The compensating control is scoping
  what the account can do: grant Modify on those two folders, and never add
  the account to Administrators.

### Switching an already-scheduled task onto a service account

DistrictSync does not repoint a live task's account in place. If a nightly
sync is already scheduled — under a personal login or another account —
switch it from Setup → Schedule: choose **Remove nightly sync**, then type
the new account and its password and choose **Schedule nightly sync** again.
That costs two Windows permission prompts instead of one, and for the moment
between those two steps there is no nightly sync registered at all. If the
second step is declined, times out, or otherwise fails after the first one
succeeded, the previous schedule is already gone — Windows never returns a
task's stored password, so there is nothing to restore, and DistrictSync
reports the schedule as missing rather than implying one still exists. The
account and password typed into the form stay there, so retrying is one
click. Do this interactively; it can't be scripted or run unattended.

### Where things live afterward, and why Run History goes quiet

Once the nightly runs as the service account, its `config.json`,
`history.db` (run history) and `etl_tool.log` all live under **that
account's own** per-user data folder — not the admin's (on Windows,
`%LOCALAPPDATA%\DistrictSync` resolved for the service account). The
admin's own Run History and Home dashboard will show a permanent gap once
the switch happens; DistrictSync reports that as an absence, not a fault,
and uses Windows' own record of the task's last run instead. An IT team
running a service account should expect to look at *that* account's own
DistrictSync folder for logs and run history, not the admin's.

If a seasonal pause (Setup → Schedule) is turned on, it stops applying once
a service account runs the nightly. The pause setting is saved to the
admin's own account's `config.json`; the nightly process, though, loads the
settings of whichever account it runs *as* — and a service account has no
DistrictSync profile of its own carrying that setting, so the window is
simply never enforced there, and the sync keeps running straight through
the break it was meant to cover. DistrictSync's Settings screen says so
plainly once both a window and a foreign account are in effect; the one
remedy that works today is removing the nightly schedule for the break.

### Rotation

If the service account's password is rotated — by policy or by hand — the
*stored* Task Scheduler credential goes stale silently: the task starts
failing to run (or, if the Windows policy named above is in force, may fail
to be *re-registered* the next time Settings is saved), with no proactive
alert. After a rotation, re-enter the new Windows password at the Schedule
step to re-register the task.

**These are two separate secrets — do not confuse them.** The Windows account
password is held by Task Scheduler for the task; the SpacesEDU delivery
password saved by `--sftp-configure` is a different secret, stored in the
account's own Credential Manager and keyed by the SFTP user name. Rotating the
**Windows** password does not change the delivery password and does not
require `--sftp-configure` to be run again. Re-run `--sftp-configure` only when
the **SpacesEDU** password itself changes.

If registering the task fails, see
[Troubleshooting](troubleshooting.md#task-scheduler-does-not-run-the-task)
for what the message means and what to do.

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
