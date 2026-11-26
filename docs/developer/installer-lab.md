# Installer lab runbook (plan 0055 L1, DSYNC-92)

The throwaway Azure lab that proves the machine-wide installer where a workstation cannot: a domain, a gMSA, a
stored-password service account, Windows Server with Desktop Experience and Server Core. Owner decision 2026-11-23
(plan 0055 O4): the lab runs under the owner's Azure login, small, tagged, shut down when idle, and deleted when the
matrix is done. The owner's login can create resources in `customer-experience-ca` but not new resource groups, so the
lab lives there, beside production resources: every lab resource is named `dsynclab-*` and tagged, sits in its own
VNet with no peering, and is deleted BY NAME, never by deleting the resource group. It holds synthetic data only: the repo's test fixtures, never a district
drop, and no real account, host or district name.

## Testing approach (owner decision 2026-11-26, plan 0055 round 3)

The installer is proven by the CI rehearsals (`release.yml` `installer-msi`: sign, install to Program Files, CLI and window
smokes, upgrade, uninstall) and, once every slice is coded, by ONE throwaway Azure VM for the rows CI cannot reach (a gMSA,
a domain password account, Server Core). The local VMware lab planned on 2026-11-23 was dropped (no Broadcom sign-up path).

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
