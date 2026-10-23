# Managed service accounts (gMSA) — what your IT team needs to do

DistrictSync can schedule its nightly sync as a **group managed service account (gMSA)**. Windows
gets the account's credential from your directory, so nothing is stored on this computer and there
is no password for anybody to type, keep or rotate.

**Read "What DistrictSync will do" before you start.** Scheduling the nightly sync as any account
other than your own — a managed service account included — also moves this computer's DistrictSync
settings into a shared folder, and **this version cannot move them back**. DistrictSync shows you
exactly what will move and asks you to confirm first, but that part can succeed even if the gMSA
registration then fails — so a failed attempt still leaves this computer changed.

This page is written to be handed to whoever administers your Active Directory. It is one page on
purpose.

---

## Why a district would want this

DistrictSync's nightly sync can run in three ways:

| How it runs | What Windows stores | Runs when nobody is signed in? |
| --- | --- | --- |
| As the signed-in account, logged-on-only | nothing | **No** |
| As any account, with its password | the account's password, on this computer | Yes |
| **As a managed service account** | **nothing — the directory holds the credential** | Yes |

The middle row is what most districts use today. Two things push a district to the third row:

- **A hardening policy blocks the middle row.** If the security setting
  *"Network access: Do not allow storage of passwords and credentials for network authentication"* is switched on,
  Windows refuses to save the task's password and the nightly sync cannot run unattended at all.
  DistrictSync reports this as **"Windows would not save the password for the nightly task"** with
  Windows code `0x80070520`. A managed service account is the one unattended option that policy
  does not block, because nothing is stored on the computer.
- **Password rotation.** A service account's password changes and the scheduled task silently
  stops working. A gMSA's password is rotated by the directory and the task keeps running.

---

## The three prerequisites

All three must be true **before** DistrictSync is asked to schedule the task. DistrictSync cannot
check any of them: it validates the *shape* of the account name and nothing more. Windows answers
**some** of the real questions when the task is registered — a name the directory does not know is
refused there — but **not all of them**: a missing 'Log on as a batch job' right, or a computer that
is not allowed to retrieve the account's password, can register without any error and only fail at
3 AM, when the task first tries to run. If registration succeeded but the nightly never seems to
run, see *Registered, but the nightly never runs* below.

1. **This computer is listed in the account's PrincipalsAllowedToRetrieveManagedPassword.**
   Without it, this computer cannot retrieve the account's password from the directory.

2. **Install-ADServiceAccount has been run for the account on this computer.** This is the
   local-side half of the same arrangement, run once per computer.

3. **The account is granted the 'Log on as a batch job' right on this computer.** Task Scheduler
   will not run a task as an account that does not hold it.

*(DistrictSync shows this same list on screen, and in the failure message below, from a single
definition in its source — so the three you read here are the three it asks for.)*

A worked example, for the record — substitute your own names:

```powershell
# On a domain controller (or any machine with the AD PowerShell module):
New-ADServiceAccount -Name svc_districtsync -DNSHostName svc_districtsync.example.local `
    -PrincipalsAllowedToRetrieveManagedPassword "DISTRICTSERVER$"

# On the computer that will run DistrictSync, as an administrator:
Install-ADServiceAccount -Identity svc_districtsync
Test-ADServiceAccount  -Identity svc_districtsync      # must return True
```

"Log on as a batch job" is granted in **Local Security Policy → Local Policies → User Rights
Assignment**, or by Group Policy for the OU the computer sits in.

---

## What to enter in DistrictSync

1. Open DistrictSync and go to **Setup**. (The option is on the **Setup** screen of a configured
   install only — it is deliberately not offered during first-run setup.)
2. In **Daily schedule**, type the account into **Windows account for the nightly task**, in the
   form `DOMAIN\svc_districtsync$`. The trailing `$` is required — it is what makes the name a
   managed service account.
3. Tick **This is a managed service account (gMSA)**.
4. The Windows password box disappears. That is correct: there is nothing for you to type.
5. Choose **Schedule nightly sync** and approve the Windows permission prompt.

---

## What DistrictSync will do

- **Register one scheduled task** that runs daily at your chosen time, as the account you named,
  with highest privileges, whether or not anybody is signed in.
- **Move this computer's DistrictSync settings into a shared folder**, `C:\ProgramData\DistrictSync`,
  so the service account can read them when it runs. That covers the district mapping, the input
  and output folders, the SpacesEDU delivery password and the run history. DistrictSync creates
  that folder with its permissions set in one step, owned by Administrators, with inheritance
  stripped; the service account is granted **read/execute** at the folder root and **modify** on
  the `runs` subfolder only. DistrictSync shows you exactly what will move and asks you to confirm
  before any of it happens.
- **Ask for permission once.** Registering an unattended task requires administrator rights.
  DistrictSync raises a single Windows permission prompt and the application itself never runs
  elevated.

## What DistrictSync does not do yet

- **Removing the nightly sync does not yet take the account's access away.** Choosing **Remove
  nightly sync** deletes the scheduled task. The permissions that were granted on
  `C:\ProgramData\DistrictSync` stay. The mechanism to revoke them exists and is deliberately
  ordered — it would run only after Windows confirms the task is gone — but nothing in this
  version calls it, so treat revocation as a manual step for now: if the account should not keep
  access to that folder, remove it with `icacls` or the folder's Security tab.

## What DistrictSync cannot undo

- **The move to a shared folder is permanent in this version.** "Remove nightly sync" removes the
  scheduled task; it does **not** move the settings back to a per-Windows-account folder. Restoring
  that today means an administrator removing `C:\ProgramData\DistrictSync` by hand. Un-provisioning
  is on our roadmap and is not in this release.
- **After the move, any administrator of this computer can open DistrictSync and change these
  settings** — and an account that is not an administrator cannot open it at all. That is a real
  change in who can do what, which is why DistrictSync says so in the confirmation before you
  agree to it.
- **DistrictSync never grants, revokes or checks anything in the directory.** All three
  prerequisites above are yours; DistrictSync only asks Windows to register a task.

---

## If it does not work

DistrictSync shows the cause on screen with a Windows code. Two are worth knowing about in
advance:

- **"Windows would not schedule the task as that managed service account"** (Windows code
  `0x80070534`) — Windows did not accept the account. Either the directory does not know the name,
  or this computer is not set up to use it. DistrictSync cannot tell which from where it sits, so
  it shows the three prerequisites rather than guessing at one. Pressing **Schedule nightly sync**
  again on its own changes nothing — check the three above first.
- **"Windows would not save the password for the nightly task"** (Windows code `0x80070520`) — the
  storage policy described at the top of this page. Please tell us if you see this **with** the
  gMSA option ticked: a managed service account stores nothing on this computer, so that policy
  should not apply to it.

For anything else, DistrictSync's log file carries one line per failure with the Windows code in
it — the failure card on screen has an **Open log folder** button, and
*[Where DistrictSync stores its data](troubleshooting.md#where-districtsync-stores-its-data-config-logs-run-history)*
says where that folder is for both kinds of install. The **Help** screen has our support contact.
Please include the code.

---

## Registered, but the nightly never runs

The task registered without an error, Windows says it fired, but no run appears in Run History and
nothing was delivered. This is the case where Windows could not **start** DistrictSync as the
account — so DistrictSync itself never got a chance to write anything.

**1. Read the result Windows recorded.** Windows keeps a result code for the task's last run, and
DistrictSync shows it:

- On **Home**, the warning reads **"Windows reported a problem starting or running the nightly sync"**
  and names the code.
- On **Setup**, under **Daily schedule**, a line starting **"Windows last ran this task"** gives the
  time and the code.
- **Copy details** (on the Home warning and on Run History) copies the task name, Windows' last run
  time, the result code and the next run — no account names and no folder paths — ready to paste to
  your IT team or to us.

**2. What the code means.**

| Code | What Windows is saying | What to check |
| --- | --- | --- |
| `0x8007052E` | It could not sign in the account — name or password rejected | For a gMSA this usually means prerequisite 1 or 2 is missing: this computer is not allowed to retrieve the password, or `Install-ADServiceAccount` / `Test-ADServiceAccount` fails on this computer |
| `0x80070569` | The account does not have the logon type the task needs | Prerequisite 3 — 'Log on as a batch job' |
| `0x80070532` | The account's password has expired | Not expected for a gMSA; for an ordinary account, re-register with its new password |
| `0x80070005` | Access denied | The account's folder permissions (step 3 below) |
| `0x80070002` | The program the task starts was not found | DistrictSync was moved, renamed or deleted after the task was registered — re-register from the copy you use now |
| `0x8007010B` | The task's start-in folder is not usable | The folder DistrictSync lives in is missing or the account cannot open it |
| `0x00041301` | The task is running right now | Nothing — check again once it finishes |

Any other code: DistrictSync shows it as-is rather than guessing. Send it to us.

**3. The three prerequisites, plus folder access.** Re-check the three prerequisites above. Then
check that the account can reach every folder the nightly touches:

- **read and execute** on DistrictSync's program file **and the folder it is in**. The standard
  `C:\Program Files` location usually allows this already; a copy kept in somebody's own profile
  (Desktop, Downloads) usually does not;
- **read** on the input folder where the MyEd BC extract files land, and **write** on the output
  folder. Network shares need these permissions for the account on the share as well.

**4. Turn on Task Scheduler history** (it is off by default). In **Task Scheduler**, select **Task
Scheduler Library** and choose **Enable All Tasks History** in the Actions pane, or run, as an
administrator:

```powershell
wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true
```

The next run's **History** tab then shows each step Windows took and where it stopped.

**5. Where the logs are.** On an install whose settings were moved to the shared folder, each account
that runs DistrictSync writes its **own** log in `C:\ProgramData\DistrictSync\runs\`, named
`etl_tool-<account>.log` — the account name in lower case, with `\` replaced by `_` and the trailing
`$` dropped (for example `etl_tool-example_svc_districtsync.log`). The **Open log folder** button
opens that folder. **If there is no log for the service account at all, the nightly never got as
far as starting DistrictSync** — the cause is one of the Windows-side checks above, not DistrictSync.
