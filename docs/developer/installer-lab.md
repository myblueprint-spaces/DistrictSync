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
Each VM: install from the ISO, take a clean snapshot, copy the MSI in (shared folder or a mounted ISO), run the row,
record the result on its ticket, revert to the snapshot.

## Azure fallback (paused)

## Cost (named before anything is created)

Prices: Azure retail, canadacentral, pay-as-you-go, Windows licence included, read 2026-11-23.

| VM | Role | Size | CAD/hour |
|---|---|---|---|
| `dc1` | domain controller, Server 2022 Core | Standard_B2s | 0.0764 |
| `mem1` | member, Server 2022 with Desktop Experience (window smokes) | Standard_B2ms | 0.1418 |
| `core1` | member, Server 2022 Core | Standard_B2s | 0.0764 |

All three running: CAD 0.29 per hour, **about CAD 215 a month if never stopped**, plus disks (3 × 127 GB Standard SSD,
about CAD 30 a month) and three Standard public IPs (about CAD 11 a month). **As planned** (auto-shutdown every night,
about 40 hours of matrix work, deleted after): **about CAD 45 in total.** A stopped (deallocated) VM costs only its
disk and IP.

## Shape

- Resource group `customer-experience-ca` (canadacentral, shared). Every resource is named `dsynclab-*` and tagged
  `purpose=districtsync-installer-lab`, `ticket=DSYNC-92`, `delete-after=<date>`.
- One VNet `dsynclab-vnet`, `10.55.0.0/16`, subnet `10.55.1.0/24`. The domain controller has the static address `10.55.1.4` and the VNet's
  DNS points at it once it is promoted.
- **No inbound access.** Each VM has a Standard public IP only for OUTBOUND traffic (downloading the MSI from a GitHub
  run artifact, and the update feed later). Standard IPs deny inbound unless a rule allows it, and no rule is added.
  Every command runs through `az vm run-command invoke` (the Azure agent). For the window smokes on `mem1`, use Azure
  Bastion Developer (free) if the region offers it; otherwise add ONE NSG rule allowing RDP from the operator's current
  address for the session, and delete it afterwards.
- Domain `lab.dsync.test` (IANA-reserved `.test`). Accounts: a lab administrator, the gMSA `svc_dsync$`, the
  stored-password service account `svc_dsyncpw`, and a standard user `lab_user` (T20).
- Passwords are generated on the operator's machine for this session, passed to `az` from a variable, and never
  written to a file, a log, the repo or a ticket. Losing them is harmless: the lab is deleted A password passed inside a `run-command` script is visible in that VM's run-command history to anyone with access to the resource group; acceptable for a throwaway lab, and one more reason it is deleted.

## Create

PowerShell, signed in with `az login`, from any folder:

```powershell
$rg = "customer-experience-ca"; $loc = "canadacentral"
$tags = "purpose=districtsync-installer-lab", "ticket=DSYNC-92", "delete-after=$((Get-Date).AddDays(14).ToString('yyyy-MM-dd'))"
$adminUser = "labadmin"
$pw = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ }) + "!a1"

az network vnet create -g $rg -n dsynclab-vnet --address-prefixes 10.55.0.0/16 --subnet-name lab --subnet-prefixes 10.55.1.0/24 --tags $tags
$core = "MicrosoftWindowsServer:WindowsServer:2022-datacenter-azure-edition-core:latest"
$desk = "MicrosoftWindowsServer:WindowsServer:2022-datacenter-azure-edition:latest"
foreach ($vm in @(@("dsynclab-dc1", $core, "Standard_B2s", "10.55.1.4"), @("dsynclab-mem1", $desk, "Standard_B2ms", ""), @("dsynclab-core1", $core, "Standard_B2s", ""))) {
  $ipArgs = if ($vm[3]) { @("--private-ip-address", $vm[3]) } else { @() }
  az vm create -g $rg -n $vm[0] --image $vm[1] --size $vm[2] --vnet-name dsynclab-vnet --subnet lab @ipArgs `
    --public-ip-sku Standard --nsg '""' --admin-username $adminUser --admin-password $pw --tags $tags --only-show-errors
  az vm auto-shutdown -g $rg -n $vm[0] --time 0300   # 03:00 UTC = evening in Canada
}
```

Promote the domain controller, point the VNet at it, and join the members:

```powershell
az vm run-command invoke -g $rg -n dsynclab-dc1 --command-id RunPowerShellScript --scripts `
  "Install-WindowsFeature AD-Domain-Services -IncludeManagementTools; Install-ADDSForest -DomainName lab.dsync.test -SafeModeAdministratorPassword (ConvertTo-SecureString '$pw' -AsPlainText -Force) -Force -NoRebootOnCompletion; Restart-Computer -Force"
az network vnet update -g $rg -n dsynclab-vnet --dns-servers 10.55.1.4
foreach ($m in "dsynclab-mem1", "dsynclab-core1") {
  az vm restart -g $rg -n $m
  az vm run-command invoke -g $rg -n $m --command-id RunPowerShellScript --scripts `
    "`$c = New-Object PSCredential('LAB\$adminUser', (ConvertTo-SecureString '$pw' -AsPlainText -Force)); Add-Computer -DomainName lab.dsync.test -Credential `$c -Restart -Force"
}
```

Accounts, the gMSA and the stored-password service account (on `dc1`), then install the gMSA on each member:

```powershell
az vm run-command invoke -g $rg -n dsynclab-dc1 --command-id RunPowerShellScript --scripts @"
Add-KdsRootKey -EffectiveTime ((Get-Date).AddHours(-10))
New-ADGroup -Name DSyncHosts -GroupScope Global
Add-ADGroupMember DSyncHosts -Members 'dsynclab-mem1$','dsynclab-core1$'
New-ADServiceAccount -Name svc_dsync -DNSHostName svc_dsync.lab.dsync.test -PrincipalsAllowedToRetrieveManagedPassword DSyncHosts
New-ADUser -Name svc_dsyncpw -AccountPassword (ConvertTo-SecureString '$pw' -AsPlainText -Force) -Enabled `$true -PasswordNeverExpires `$true
New-ADUser -Name lab_user -AccountPassword (ConvertTo-SecureString '$pw' -AsPlainText -Force) -Enabled `$true
"@
foreach ($m in "dsynclab-mem1", "dsynclab-core1") {
  az vm restart -g $rg -n $m   # picks up the DSyncHosts membership
  az vm run-command invoke -g $rg -n $m --command-id RunPowerShellScript --scripts `
    "Install-WindowsFeature RSAT-AD-PowerShell; Install-ADServiceAccount svc_dsync; Test-ADServiceAccount svc_dsync"
}
```

The 'Log on as a batch job' right is granted per test, not up front: T5 runs once WITHOUT it (the DSYNC-33 schedule
check must report it) and once with it (`ntrights`-equivalent through `secedit` in the matrix script).

## Get the MSI onto the members

The MSI is the 1-day artifact `DistrictSync-windows-msi-rehearsal` of a `release.yml` dispatch (plan 0055 B1). GitHub
answers an artifact download with a short-lived signed URL; fetch it on the operator's machine and hand it straight to
the VM:

```powershell
$run = "<rehearsal run id>"
$id = gh api "repos/myblueprint-spaces/DistrictSync/actions/runs/$run/artifacts" --jq '.artifacts[] | select(.name=="DistrictSync-windows-msi-rehearsal") | .id'
$url = (gh api -i "repos/myblueprint-spaces/DistrictSync/actions/artifacts/$id/zip" | Select-String '^Location: (.*)$').Matches[0].Groups[1].Value.Trim()
foreach ($m in "dsynclab-mem1", "dsynclab-core1") {
  az vm run-command invoke -g $rg -n $m --command-id RunPowerShellScript --scripts `
    "New-Item -ItemType Directory -Force C:\lab | Out-Null; Invoke-WebRequest '$url' -OutFile C:\lab\msi.zip; Expand-Archive C:\lab\msi.zip C:\lab -Force; Get-ChildItem C:\lab"
}
```

The URL expires within minutes and grants nothing else; it is never written to a file or a ticket.

## Run the matrix

Each row of plan 0055's test matrix that is marked **L** is run here and its result (pass, fail, or the measured
behaviour) recorded on the slice's ticket and, for L1's own questions, appended to plan 0055 under "L1 findings".
Commands run through `az vm run-command invoke`; a row that needs the window runs on `mem1` over Bastion or RDP.
Every run sets `DISTRICTSYNC_DATA_DIR` only where the row says so (most rows exercise the real machine profile, which is
the point of the lab).

## Stop, start, delete

The lab shares its resource group with production resources, so every command names the lab's resources explicitly:

```powershell
$rg = "customer-experience-ca"
$vms = az vm list -g $rg --query "[?starts_with(name, 'dsynclab-')].id" -o tsv
az vm deallocate --ids $vms   # idle: disks and IPs only
az vm start --ids $vms
# Delete (when the matrix is recorded): the VMs, then everything else carrying the lab tag, then check nothing is left.
az vm delete --ids $vms --yes --force-deletion none
$rest = az resource list --tag purpose=districtsync-installer-lab --query "[?starts_with(name, 'dsynclab-')].id" -o tsv
az resource delete --ids $rest   # NICs, disks, IPs, then the VNet (rerun once if a dependency was still attached)
az resource list --tag purpose=districtsync-installer-lab -o table   # must be empty
```

Never `az group delete` here: the resource group holds production services. Deleting the lab is the last step of L1
and of R1's matrix run; the ticket records the date it was deleted.
