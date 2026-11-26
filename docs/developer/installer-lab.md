# Installer lab runbook (plan 0055 L1, DSYNC-92)

The LOCAL lab that proves the machine-wide installer where a workstation cannot: a domain, a gMSA, a stored-password
service account, Windows Server with Desktop Experience and Server Core. It runs on VMware Workstation Pro on the
owner's PC and holds synthetic data only: the repo's test fixtures, never a district drop, and no real account, host or
district name.

**No Azure (owner, 2026-11-26: "im not authorised for azure spend").** No Azure resource is created for this plan; every row runs on the local VMware lab, the owner's PC or a GitHub runner. The one-instance Azure script was removed.

## Local first (owner decisions 2026-11-23 and 2026-11-26: "i got into broadcom i can download so that we cna do the local testing without spending money")

The whole matrix runs on the owner's PC (the VMs below) or a GitHub runner. The Azure lab built on 2026-11-23 was
deleted the same day, and there is no Azure fallback.

**The PC (measured 2026-11-23):** Windows 11 Home 26300, Core Ultra 9 185H (16 cores), 15.5 GB RAM, 98 GB free disk.
The Windows hypervisor is already running (WSL2 and Docker Desktop use it). No Hyper-V Manager (Home has none), and no
VirtualBox or VMware installed.

**What to install (the owner, once):**

1. **VMware Workstation Pro 26H1** (the owner chose VMware on 2026-11-23). Broadcom's current release; per its 26H1
   release notes it is free for commercial use and needs NO licence key, so leave the installer's key field blank. It is
   downloaded from the Broadcom support portal after signing in, which the owner does himself. It runs beside the
   Windows hypervisor (WSL2, Docker) through the Windows Hypervisor Platform: turn on the optional feature *Windows
   Hypervisor Platform* (Settings > System > Optional features > More Windows features), then restart.
2. **Windows Server 2022 evaluation ISO** (free, 180 days, Microsoft Evaluation Center; about 5 GB). One ISO gives both
   Server Core and Desktop Experience.
3. Nothing else: the MSI comes from the B1 rehearsal artifact, and the repo's fixtures are the data.

**Capacity.** RAM is the limit: a Server Core VM needs 2 GB, a Desktop Experience VM 4 GB. A domain pair (DC 2 GB + member
4 GB) fits while other heavy apps are closed; a third VM (Server Core member) runs only with the Desktop member stopped.
Disk: each VM has a 32 GB thin disk (the Server 2022 minimum; owner 2026-11-26), so the three can never pass 96 GB together.
Expected real use is about 12 GB per Core VM and 22 GB for Desktop, about 46 GB for all three plus the ISO; `-Action check`
prints both.

**Where each matrix row runs** (plan 0055 test matrix):

| Where | Rows | Why |
|---|---|---|
| The PC as it is | T21 (signatures), T22 (SmartScreen on a fresh download) | Read-only checks. Every other row changes machine state (Program Files, ProgramData, HKLM, Task Scheduler) on the owner's daily machine, so it runs in a VM even where the PC could. |
| One workgroup VM (Server 2022 Desktop, no domain) | T1, T2, T3 (local password service account), T7/T7b/T7c for local accounts, T8 (from a per-user v3.30 install), T9, T10-T12 (SYSTEM updater, once U2/U3 exist; a loopback feed), T15 (policy key set with `gpedit`/registry), T17, T18, T20, T23, T24, T25, T26 | Needs admin, Program Files, Task Scheduler and second local accounts, but no directory. A snapshot before each row makes them repeatable. |
| Plus a Server Core VM (workgroup) | T6 (console and WinRM from the Desktop VM; SSH optional) | Headless install, machine setup and scheduling. |
| A local domain (DC VM + member VM) | T4 (domain password account), T5 (gMSA + the DSYNC-33 schedule check), T7 for a gMSA principal | gMSA needs Active Directory with a KDS root key: a DC VM (Server Core, 2 GB) and a member VM joined to it, on a host-only network. |
| Not locally | T19's Intune half (needs a tenant); Group Policy software install can be done on the local domain | **Not proven before release:** no tenant and no Azure spend. Intune is documented from Microsoft's MSI guidance and confirmed by the first district that uses it. |

**Docker: a poor fit for the installer, and Windows 11 Home cannot run Windows containers at all.** Docker Desktop on this
PC runs Linux containers only. Even Windows containers would not help: they have no Task Scheduler and no interactive
desktop, an MSI installs into the container's own throwaway layer, and a container cannot be a domain member for a gMSA
the way a server is. So no matrix row runs in Docker. **Where Docker still helps:** (1) a local SFTP server container as
the delivery target for the migration rows (T7b's "delivery test before the nightly moves") instead of the real
SpacesEDU host; (2) a local HTTPS server for a test update feed when U1/U2 are built (the e2e flavour's loopback feed);
(3) running the Linux test suite exactly as CI does.

**Order.** Workgroup Desktop VM first (most rows, no domain), then the Server Core VM, then the domain pair for T4/T5.

**Hypervisor: VMware Workstation Pro (owner decision 2026-11-23, "okay vmware").** Everything after the manual installs is
`scripts/lab/local_lab.ps1` (its sizes and safety properties are pinned by `tests/test_lab_script.py`):

```powershell
.\scripts\lab\local_lab.ps1 -Action check                                                 # read-only: VMware, Hypervisor Platform, ISO, memory, disk; exit 1 = not ready
.\scripts\lab\local_lab.ps1 -Action create -IsoPath C:\Lab\iso\<evaluation ISO>.iso   # all three VMs, unattended, snapshot "clean"
.\scripts\lab\local_lab.ps1 -Action copy-in -Vm lab-desktop -HostPath .\msi-n.msi -GuestPath C:\lab\msi-n.msi
.\scripts\lab\local_lab.ps1 -Action run -Vm lab-desktop -Command "msiexec /i C:\lab\msi-n.msi /qn /l*v C:\lab\install.log"
.\scripts\lab\local_lab.ps1 -Action revert -Vm lab-desktop -Snapshot clean               # between rows
.\scripts\lab\local_lab.ps1 -Action domain                                                # DC + members + gMSA, snapshot "domain"
.\scripts\lab\local_lab.ps1 -Action destroy                                               # when the matrix is recorded
```

Each VM is installed from the evaluation ISO with an answer file the script writes to a small second ISO (Windows' own
IMAPI2; no download), installs VMware Tools at first logon, and is then driven only through VMware Tools
(`vmrun runProgramInGuest` and file copies): no network share, remote session or open port. The lab administrator
password is generated once into `C:\Lab\vms\lab-secret.txt` with an owner-only ACL and never printed.
Each VM: install from the ISO, take a clean snapshot, copy the MSI in (shared folder or a mounted ISO), run the row,
record the result on its ticket, revert to the snapshot.
