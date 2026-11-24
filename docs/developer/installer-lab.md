# Installer lab runbook (plan 0055 L1, DSYNC-92)

The throwaway Azure lab that proves the machine-wide installer where a workstation cannot: a domain, a gMSA, a
stored-password service account, Windows Server with Desktop Experience and Server Core. Owner decision 2026-11-23
(plan 0055 O4): the lab runs under the owner's Azure login, small, tagged, shut down when idle, and deleted when the
matrix is done. The owner's login can create resources in `customer-experience-ca` but not new resource groups, so the
lab lives there, beside production resources: every lab resource is named `dsynclab-*` and tagged, sits in its own
VNet with no peering, and is deleted BY NAME, never by deleting the resource group. It holds synthetic data only: the repo's test fixtures, never a district
drop, and no real account, host or district name.

## Local first (owner decision 2026-11-23: "i prefer to do the test locally using local vm and docker first")

The matrix runs on the owner's PC first; the Azure lab below is PAUSED (its three VMs deallocated 2026-11-23, nothing
deleted) and is the fallback for what cannot run locally.

**The PC (measured 2026-11-23):** Windows 11 Home 26300, Core Ultra 9 185H (16 cores), 15.5 GB RAM, 98 GB free disk.
The Windows hypervisor is already running (WSL2 and Docker Desktop use it). No Hyper-V Manager (Home has none), and no
VirtualBox or VMware installed.

**What to install (the owner, once):**

1. **VMware Workstation Pro 17** (free for personal and commercial use; download from Broadcom with a free account), OR
   **VirtualBox 7.1**. Both run beside the Windows hypervisor through the Windows Hypervisor Platform; VMware is the
   faster of the two in that mode. Turn on the optional feature *Windows Hypervisor Platform* (Settings > System >
   Optional features > More Windows features), then restart.
2. **Windows Server 2022 evaluation ISO** (free, 180 days, Microsoft Evaluation Center; about 5 GB). One ISO gives both
   Server Core and Desktop Experience.
3. Nothing else: the MSI comes from the B1 rehearsal artifact, and the repo's fixtures are the data.

**Capacity.** RAM is the limit: a Server Core VM needs 2 GB, a Desktop Experience VM 4 GB. A domain pair (DC 2 GB + member
4 GB) fits while other heavy apps are closed; a third VM (Server Core member) runs only with the Desktop member stopped.
Disk: about 15 GB per Core VM and 25 GB for Desktop, so about 55 GB for all three plus the ISO, or less with linked clones.

**Where each matrix row runs** (plan 0055 test matrix):

| Where | Rows | Why |
|---|---|---|
| The PC as it is | T21 (signatures), T22 (SmartScreen on a fresh download) | Read-only checks. Every other row changes machine state (Program Files, ProgramData, HKLM, Task Scheduler) on the owner's daily machine, so it runs in a VM even where the PC could. |
| One workgroup VM (Server 2022 Desktop, no domain) | T1, T2, T3 (local password service account), T7/T7b/T7c for local accounts, T8 (from a per-user v3.30 install), T9, T10-T12 (SYSTEM updater, once U2/U3 exist; a loopback feed), T15 (policy key set with `gpedit`/registry), T17, T18, T20, T23, T24, T25, T26 | Needs admin, Program Files, Task Scheduler and second local accounts, but no directory. A snapshot before each row makes them repeatable. |
| Plus a Server Core VM (workgroup) | T6 (console and WinRM from the Desktop VM; SSH optional) | Headless install, machine setup and scheduling. |
| A local domain (DC VM + member VM) | T4 (domain password account), T5 (gMSA + the DSYNC-33 schedule check), T7 for a gMSA principal | gMSA needs Active Directory with a KDS root key: a DC VM (Server Core, 2 GB) and a member VM joined to it, on a host-only network. |
| Not locally | T19's Intune half (needs a tenant); Group Policy software install can be done on the local domain | Left to Azure or a district. |

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
password is generated once into `C:\Labms\lab-secret.txt` with an owner-only ACL and never printed.
Each VM: install from the ISO, take a clean snapshot, copy the MSI in (shared folder or a mounted ISO), run the row,
record the result on its ticket, revert to the snapshot.

## Azure: one throwaway instance, only before a real release

Owner decision 2026-11-23: "just 1 instance quickly test the requirement and end ... cad 1-5 is fine ... keep it minimal
for the required test and do only when necessary before a real release". The three-VM lab built earlier that day was
deleted the same day (every `dsynclab-*` resource, by name; the resource group listed empty of them afterwards).

**What it covers:** only the rows no local VM can, and only if the local domain pair has not already covered them:
T4 (a domain password service account) and T5 (a gMSA) running the installed DistrictSync from Program Files as a
scheduled task. **Caveat, in every result file:** the VM promotes ITSELF to a domain controller and runs the rows on
itself, and a domain controller is not a typical member server (no local accounts, domain-controller policies apply). A
pass proves the account kinds and the Program Files permissions, not member-server policy.

**How:** one script, `scripts/lab/azure_domain_check.ps1`, run from the operator's PowerShell after `az login`:

```powershell
.\scripts\lab\azure_domain_check.ps1 -MsiUrl '<short-lived URL of the B1 rehearsal artifact>' -ResultsDir .\lab-results
```

Its limits are constants in the script, pinned by `tests/test_lab_script.py`, not just written here:

| Limit | Value |
|---|---|
| Instances | one VM, `dsynctest-dc`, Windows Server 2022 Core |
| Size and pricing | `Standard_D2as_v5` on **Spot** (CAD 0.051/h; B-series cannot run on Spot, and an on-demand B2s costs more at CAD 0.076/h), eviction deletes it |
| Disk | Standard HDD |
| Network | no public IP, no inbound rule; the subnet keeps default outbound access only to download the MSI and the fixtures; commands go through `az vm run-command` |
| Time box | 180 minutes: every wait checks the deadline, and an Azure auto-shutdown at the deadline stops the VM even if the operator's machine dies |
| Cost ceiling | CAD 5 per run, checked before anything is created against the ON-DEMAND rate (worst case about CAD 0.62) |
| Clean-up | everything `dsynctest-*` deleted by name in a `finally`, on success and on failure, and first thing on the next run; the resource group is never deleted |

The results (the rows' task results and console tails, the timings, any leftover) are saved as JSON in `-ResultsDir`
and copied to the release ticket. A run that leaves anything behind says so in the JSON and on the console.
