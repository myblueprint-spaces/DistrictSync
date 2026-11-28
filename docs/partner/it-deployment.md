# Installing DistrictSync for the whole computer (IT deployment guide)

This guide is for the district IT person who installs DistrictSync on a server. It covers the machine-wide
installer (`DistrictSync.msi`): what it installs, the one setup step an administrator does once, how the nightly
sync is scheduled, how updates are controlled, and how to remove it.

> **Status.** This guide describes the machine-wide installer for the release that first ships it: `DistrictSync.msi`
> and the Group Policy templates are attached to that release. Until then they are not on the releases page.

**Who uses this installer.** New districts. A district already running the single-file `DistrictSync-windows.exe`
or the per-user `DistrictSync-Setup.exe` does not have to change anything; move only if you want to (see
[Moving an existing copy](#moving-an-existing-copy)).

---

## What the installer does

- Installs DistrictSync for **every account on the computer** into
  `C:\Program Files\myBlueprint.DistrictSync\` (the program itself is `current\DistrictSync.exe`). Only
  administrators can change that folder.
- Adds a Start menu entry.
- Installs nothing else: no service, no scheduled task, no settings. Those come from the setup step below.

DistrictSync's settings, run history and saved delivery password live in **one shared folder,
`C:\ProgramData\DistrictSync`**, which the account that runs the nightly sync (a service account or a managed
service account) can also use. DistrictSync writes **no Windows account name** into its logs, log file names, run
history or the registry; it tells accounts apart with a short code instead, and its log lines are filtered for the
account it runs as and for profile folders.

---

## Step 1 — Install

Interactive: run `DistrictSync.msi` and follow the prompts (Windows asks for administrator permission).

Scripted (no prompts), from an administrator command prompt:

```
msiexec /i DistrictSync.msi /qn /l*v "%TEMP%\DistrictSync-install.log"
```

The log file is Windows Installer's own record of the install; keep it if anything goes wrong.

---

## Step 2 — Set the computer up (once, by an administrator)

The first time an administrator opens DistrictSync after installing, it shows one step,
**Set up DistrictSync on this computer**, before anything else. It asks one question, how DistrictSync handles new
versions:

| Choice | What it does |
|---|---|
| **Tell me when a newer version is available (recommended)** | Once the update service is live, DistrictSync checks the signed list of versions after the nightly sync and says when a newer one exists. It downloads and installs nothing. |
| **Install new signed versions automatically** | Planned: as above, and new signed versions installed automatically, never while a conversion is running. |
| **Never check for new versions** | DistrictSync makes no update connection at all. |

**Today, update checks are not live yet:** whatever you choose, DistrictSync makes no update connection until a later
release switches the update service on. Choose now what you want then; it applies from that release.

Then **Set up this computer** and allow the Windows permission prompt. DistrictSync creates the shared folder,
moves the administrator's own DistrictSync settings into it if there are any, and starts.

On a server with no desktop (Server Core) or for a scripted install, run instead:

```
"C:\Program Files\myBlueprint.DistrictSync\current\DistrictSync.exe" --machine-setup --updates notify
```

(`--updates off`, `notify` or `on`.) It prints what it did and exits 0 when this run has set the computer up (or changed the update mode).

Until this step has run, DistrictSync does nothing else on this computer: the window shows only the setup step,
and every command except `--machine-setup`, `--version`, `--help`, `--diagnose` and `--migrate` stops with:

> DistrictSync is installed for the whole computer but has not been set up yet. An administrator runs
> DistrictSync.exe --machine-setup --updates notify (or opens DistrictSync once), then this command works.

---

## Step 3 — District, folders and delivery

In the DistrictSync window, the setup walks through the district, the MyEdBC extract folder, the output folder and
**Delivery to SpacesEDU**. Without a desktop, after Step 2 and before scheduling, save the district and the two
folders with:

```
DistrictSync.exe --configure --sis <district> --input <folder> --output <folder>
```

`<district>` is the district's mapping name (for example `sd48myedbc`), `<folder>` a full path. It applies the
window's rules: the extract folder must already exist, the output folder's parent must exist (DistrictSync creates
the output folder itself at the first run), and when it changes the district to a mapping added on this computer,
that mapping must first have passed a test conversion. The test conversion is run from the **Mapping** screen of
DistrictSync's window, so a mapping authored locally has to be tested on a computer with a desktop before a server
without one can switch to it. It also refuses, with nothing saved, when DistrictSync's settings file cannot be read.
When saved it prints a confirmation, plus a reminder to run `--schedule-register` again if a nightly is already
scheduled, and exits 0; when refused it prints why and exits 1. Run it as the administrator who set the computer up,
or from an administrator command prompt.

Delivery is set with `--sftp-configure` (see the [headless guide](headless-sftp-setup.md)); run it from the installed
program.

---

## Step 4 — Schedule the nightly sync

From the window: **Settings › Schedule**. Without a desktop:

```
DistrictSync.exe --schedule-register --time 02:00 --principal gmsa:DISTRICT\svc_dsync$
DistrictSync.exe --schedule-register --time 02:00 --principal password:DISTRICT\svc_dsync
DistrictSync.exe --schedule-register --time 02:00
```

The first runs the nightly as a managed service account (see [Managed service accounts](managed-service-accounts.md)
for what your IT team prepares first), the second as an account with a stored password (the password comes from
the `DISTRICTSYNC_TASK_PASSWORD` environment variable, `--task-password-stdin`, or a prompt, never the command line),
and the third as the signed-in account, which runs only while that account is signed in. `--wake` wakes the computer
to run it. `--schedule-remove` removes the nightly.

---

## Updates and Group Policy

The choice from Step 2 can be changed later in **Settings › New versions of DistrictSync** by an administrator
running DistrictSync, or with `--machine-setup --updates <mode>`.

**Group Policy wins over both.** The templates ship with the release that ships the installer (`DistrictSync.admx`
and `en-US\DistrictSync.adml`; until then they are in the source repository under `deploy/group-policy/`);
copy them to `C:\Windows\PolicyDefinitions` (or your domain's Central Store). The policy is
**Computer Configuration › Administrative Templates › DistrictSync › Automatic updates**:

- **Enabled** with Off, Notify or On sets the mode, and DistrictSync cannot change it.
- **Disabled** is the same as Off.
- **Not configured** leaves the choice to the computer's own setting.

Automatic installing on a machine-wide install is not built yet: until then **On** behaves like **Notify** (and,
until the update service is live, both make no connection). As designed, before installing anything DistrictSync
will check that the version list is signed, that the download matches it, and that the program files carry
myBlueprint Corp.'s code signature; installing will be done by a Windows task running as the computer's system
account, from a folder only administrators can change.

**Upgrading by hand:** run the newer `DistrictSync.msi`. **Always run the newest MSI, never an older one:** an
older MSI run over a newer DistrictSync puts the older version back.

---

## Moving an existing copy

To see what moving an existing copy involves, run the installed program with:

```
DistrictSync.exe --migrate
```

It changes nothing. It reports whether the nightly still runs the old copy, whether a copy installed for one Windows
account is also here, whether this computer uses the shared folder and whether delivery's password is in it, with
the next step.

To move the nightly, from an administrator command prompt and at least 30 minutes away from the nightly's run time:

```
DistrictSync.exe --migrate --apply
```

Set the computer up first (Step 2). It does not move a nightly that is running or switched off in Task Scheduler,
and the nightly's extract folder, its output folder's parent and its district settings must all be reachable from the
installed program first. The move then:

1. reads the nightly's own facts from Task Scheduler: its run time, account, wake setting, district and folders;
2. if the nightly delivers to SpacesEDU, makes sure delivery's settings and password are in the shared folder and
   tests delivery from the installed program. If they are missing (for example the service account saved its own
   password), give them the way `--sftp-configure` takes them: `--sftp-host`, `--sftp-user`, `--sftp-remote`, and
   the password from `DISTRICTSYNC_SFTP_PASSWORD`, `--sftp-password-stdin` or a prompt. The nightly is not touched
   until that test passes;
3. for an account with a stored password, asks for that password (`DISTRICTSYNC_TASK_PASSWORD`,
   `--task-password-stdin` or a prompt, never the command line);
4. behind one Windows permission prompt: checks that Windows can start the installed program as the nightly's
   account, removes the old nightly, and schedules it again with the same time, account and settings from the
   installed program. If scheduling it again fails, the old nightly is registered again with the same time,
   account, program and command line (DistrictSync's own run level and limits apply). There is never a second
   nightly.

When DistrictSync's settings do not record which kind of account the nightly uses (a nightly made by hand with
`schtasks`), add `--principal password:DISTRICT\svc_dsync` or `--principal gmsa:DISTRICT\svc_dsync$`, naming the
account it runs as.

Old copies: a copy installed for your own Windows account (`DistrictSync-Setup.exe`) is removed once the nightly runs
the installed program; one installed for another account is reported with the command to run as that account. The
one-file `DistrictSync-windows.exe` is never deleted, because your own scripts may use it; delete it yourself.

It prints a report (Found, Moved, Left, Next, and a **Way back** line with the old nightly's time, account kind and
wake setting), also written to the log without account names, and exits 0 only when the nightly runs the installed
program and delivery passed its test (or the nightly does not deliver), 1 otherwise. **The way back:** schedule the
nightly again at the reported time for the same account, from the old copy's Setup › Schedule (or from the installed
copy with the `--schedule-register` line the report prints). If something stops part-way, see
[Troubleshooting](troubleshooting.md#moving-the-nightly-stopped-part-way---migrate---apply).

---

## Checking an install

```
DistrictSync.exe --diagnose
```

prints a support report, including an **Installation** block: the install folder, the running version and the MSI's
own version (they will differ once automatic updates exist), whether a copy for one account is also present, the updater's
folder and its last line, and the last update check. Send this report to your support contact when something is
wrong. It carries no passwords; it does name Windows accounts and folders.

Logs are in `C:\ProgramData\DistrictSync\runs\`: the nightly's is `etl_tool-scheduled-<code>.log`, a person's
`etl_tool-interactive-<code>.log`.

---

## Removing DistrictSync

1. **Remove the nightly first:** Settings › Schedule › Remove, or `DistrictSync.exe --schedule-remove`. A schedule
   left behind would point at a program that is no longer there.
2. Uninstall **DistrictSync** from Windows Settings › Apps (or `msiexec /x <the DistrictSync.msi you last installed> /qn`).

`C:\ProgramData\DistrictSync` (settings, run history, the delivery password) is kept, so a reinstall picks up where it
left off. Delete that folder yourself if the computer is being retired.
