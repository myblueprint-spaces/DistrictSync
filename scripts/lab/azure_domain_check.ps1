<#
.SYNOPSIS
  Plan 0055 (DSYNC-92): ONE throwaway Azure VM that proves the domain-only rows of the installer matrix, then deletes itself.

.DESCRIPTION
  Owner decision 2026-11-23: "just 1 instance quickly test the requirement and end ... keep it minimal for the required
  test and do only when necessary before a real release". Everything else runs on local VMs (docs/developer/installer-lab.md).

  One Windows Server 2022 Core VM promotes ITSELF to a domain controller and runs the domain rows on itself:
    T4  a domain password service account runs the installed DistrictSync from Program Files as a scheduled task;
    T5  a gMSA does the same (KDS root key, the DC itself allowed to retrieve the password).
  CAVEAT, stated in every result file: a domain controller is not a typical member server (no local accounts, DC
  policies apply). A pass here proves the account kinds and the Program Files ACL; it does not prove member-server policy.

  HARD LIMITS (constants below; the script refuses to run if they are changed to something it cannot keep):
    * one VM, Spot, Standard_D2as_v5 (B-series cannot run on Spot), Standard HDD, NO public IP, no inbound rule;
    * a 180-minute time box: every wait checks the deadline, and an Azure auto-shutdown fires at the deadline even if
      this machine dies;
    * a CAD 5 cost ceiling per run, checked against the hard-coded rates before anything is created;
    * every resource is named dsynctest-* in customer-experience-ca and deleted BY NAME in a finally block, on success
      and on failure; the next run first deletes anything a killed run left behind. The resource group is never deleted.

.PARAMETER MsiUrl
  A short-lived download URL for the signed MSI (the B1 rehearsal artifact). It is passed to the VM and never written to disk.

.PARAMETER ResultsDir
  Where the results JSON and the VM's transcript are saved.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$MsiUrl,
    [Parameter(Mandatory = $true)][string]$ResultsDir
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------------------------------------------------------------- hard limits (pinned by tests/test_lab_script.py)
$ResourceGroup = 'customer-experience-ca'
$Location = 'canadacentral'
$Prefix = 'dsynctest-'
$VmSize = 'Standard_D2as_v5'
$Priority = 'Spot'
$DiskSku = 'Standard_LRS'          # Standard HDD
$TimeBoxMinutes = 180
$CostCeilingCad = 5
# CAD per hour, Azure retail canadacentral, read 2026-11-23: Spot Windows D2as_v5 0.0512; on-demand (if Spot is evicted
# and the run is retried by hand) 0.2; an S10 Standard HDD about 0.008. The ceiling check uses the ON-DEMAND rate, so
# it holds even if Spot pricing rises to the on-demand cap (max-price -1).
$WorstCaseVmCadPerHour = 0.20
$DiskCadPerHour = 0.008
# -----------------------------------------------------------------------------------------------------------------

$worstCase = ($WorstCaseVmCadPerHour + $DiskCadPerHour) * ($TimeBoxMinutes / 60)
if ($worstCase -gt $CostCeilingCad) { throw "worst-case cost CAD $worstCase exceeds the ceiling CAD $CostCeilingCad - refusing" }
if ($TimeBoxMinutes -gt 180) { throw "the time box may not exceed 180 minutes" }

$started = Get-Date
$deadline = $started.AddMinutes($TimeBoxMinutes)
$vm = "${Prefix}dc"
$vnet = "${Prefix}vnet"
$tags = @('purpose=districtsync-installer-lab', 'ticket=DSYNC-92', "delete-after=$($deadline.ToUniversalTime().ToString('yyyy-MM-ddTHH:mmZ'))")
New-Item -ItemType Directory -Force $ResultsDir | Out-Null
$results = [ordered]@{
    started = $started.ToString('o'); vm_size = $VmSize; priority = $Priority; time_box_minutes = $TimeBoxMinutes
    cost_ceiling_cad = $CostCeilingCad; worst_case_cad = [math]::Round($worstCase, 2)
    caveat = 'Run on a domain controller, which is not a typical member server: no local accounts, DC policies apply.'
    rows = [ordered]@{}
}
$pw = (-join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })) + '!a1'  # this run only

function Say($m) { Write-Output "[$((Get-Date).ToString('HH:mm:ss'))] $m" }
function Assert-InTime { if ((Get-Date) -gt $deadline) { throw "time box of $TimeBoxMinutes minutes reached" } }
function Invoke-Vm([string]$script) {
    Assert-InTime
    $out = az vm run-command invoke -g $ResourceGroup -n $vm --command-id RunPowerShellScript --scripts $script --query "value[].message" -o tsv
    if ($LASTEXITCODE -ne 0) { throw "run-command failed" }
    return ($out -join "`n")
}
function Wait-Vm([string]$check, [string]$expect, [int]$tries = 30) {
    for ($i = 0; $i -lt $tries; $i++) {
        Assert-InTime
        try { if ((Invoke-Vm $check) -match $expect) { return } } catch { }
        Start-Sleep -Seconds 20
    }
    throw "timed out waiting for: $expect"
}
function Remove-Lab {
    Say 'deleting every dsynctest-* resource by name'
    $vmIds = az vm list -g $ResourceGroup --query "[?starts_with(name, '$Prefix')].id" -o tsv
    if ($vmIds) { az vm delete --ids $vmIds --yes -o none }
    foreach ($type in 'Microsoft.DevTestLab/schedules', 'Microsoft.Network/networkInterfaces', 'Microsoft.Compute/disks', 'Microsoft.Network/publicIPAddresses', 'Microsoft.Network/networkSecurityGroups', 'Microsoft.Network/virtualNetworks') {
        $ids = az resource list -g $ResourceGroup --resource-type $type --query "[?contains(name, '$($Prefix.TrimEnd('-'))')].id" -o tsv
        if ($ids) { az resource delete --ids $ids -o none }
    }
    $left = az resource list -g $ResourceGroup --query "[?contains(name, '$($Prefix.TrimEnd('-'))')].name" -o tsv
    if ($left) { Write-Warning "STILL PRESENT, delete by hand: $($left -join ', ')" } else { Say 'nothing dsynctest-* remains' }
    return [bool]$left
}

Say "worst case CAD $([math]::Round($worstCase, 2)) of ceiling CAD $CostCeilingCad; deadline $($deadline.ToString('HH:mm'))"
Remove-Lab | Out-Null   # anything a killed run left behind
try {
    Say 'network (no public IP; default outbound only, for the MSI and the fixtures download)'
    az network vnet create -g $ResourceGroup -n $vnet --address-prefixes 10.56.0.0/24 --tags $tags -o none
    az network vnet subnet create -g $ResourceGroup --vnet-name $vnet -n lab --address-prefixes 10.56.0.0/24 --default-outbound true -o none
    if ($LASTEXITCODE -ne 0) { throw 'network create failed' }

    Say "VM $vm ($Priority $VmSize, Standard HDD, no public IP)"
    az vm create -g $ResourceGroup -n $vm --image 'MicrosoftWindowsServer:WindowsServer:2022-datacenter-azure-edition-core:latest' `
        --size $VmSize --priority $Priority --eviction-policy Delete --max-price -1 --storage-sku $DiskSku `
        --os-disk-delete-option Delete --nic-delete-option Delete --vnet-name $vnet --subnet lab --private-ip-address 10.56.0.4 `
        --public-ip-address '""' --nsg '""' --admin-username labadmin --admin-password $pw --tags $tags --only-show-errors -o none
    if ($LASTEXITCODE -ne 0) { throw 'VM create failed (Spot capacity?)' }
    # Backstop if THIS machine dies: Azure stops the VM at the deadline (the disk, pennies, waits for the next run's cleanup).
    az vm auto-shutdown -g $ResourceGroup -n $vm --time $deadline.ToUniversalTime().ToString('HHmm') -o none

    Say 'promote to a domain controller'
    Invoke-Vm "Install-WindowsFeature AD-Domain-Services | Out-Null; Install-ADDSForest -DomainName lab.dsync.test -DomainNetbiosName LAB -SafeModeAdministratorPassword (ConvertTo-SecureString '$pw' -AsPlainText -Force) -Force -NoRebootOnCompletion | Out-Null; 'ok'" | Out-Null
    az vm restart -g $ResourceGroup -n $vm -o none
    Wait-Vm 'try { (Get-ADDomain).DNSRoot } catch { "no" }' 'lab\.dsync\.test'

    Say 'accounts: gMSA + password service account; batch-logon through Performance Log Users (a DC default holder of the right)'
    $accounts = Invoke-Vm @"
Add-KdsRootKey -EffectiveTime ((Get-Date).AddHours(-10)) | Out-Null
`$dc = Get-ADComputer `$env:COMPUTERNAME
New-ADServiceAccount -Name svc_dsync -DNSHostName svc_dsync.lab.dsync.test -PrincipalsAllowedToRetrieveManagedPassword `$dc
New-ADUser -Name svc_dsyncpw -AccountPassword (ConvertTo-SecureString '$pw' -AsPlainText -Force) -Enabled `$true -PasswordNeverExpires `$true
Add-ADGroupMember 'Performance Log Users' -Members (Get-ADServiceAccount svc_dsync), (Get-ADUser svc_dsyncpw)
Install-ADServiceAccount svc_dsync; 'gmsa test: ' + (Test-ADServiceAccount svc_dsync)
"@
    $results.rows['setup'] = $accounts

    Say 'install the MSI machine-wide and fetch the synthetic fixtures'
    $results.rows['install'] = Invoke-Vm @"
New-Item -ItemType Directory -Force C:\lab | Out-Null
Invoke-WebRequest '$MsiUrl' -OutFile C:\lab\msi.zip; Expand-Archive C:\lab\msi.zip C:\lab -Force
`$p = Start-Process msiexec.exe -ArgumentList '/i C:\lab\msi-n.msi /qn /l*v C:\lab\install.log' -Wait -PassThru
Invoke-WebRequest 'https://codeload.github.com/myblueprint-spaces/DistrictSync/zip/refs/heads/main' -OutFile C:\lab\repo.zip
Expand-Archive C:\lab\repo.zip C:\lab -Force
'msiexec exit ' + `$p.ExitCode + '; exe present ' + (Test-Path 'C:\Program Files\myBlueprint.DistrictSync\current\DistrictSync.exe')
"@

    foreach ($row in @(@('T4_domain_password_account', 'LAB\svc_dsyncpw', 'Password'), @('T5_gmsa', 'LAB\svc_dsync$', 'gMSA'))) {
        Say "row $($row[0])"
        # A gMSA is registered with LogonType Password and NO password (the directory holds it); a password account with its password.
        $register = if ($row[2] -eq 'gMSA') {
            "Register-ScheduledTask -TaskName `$n -Action `$a -Principal (New-ScheduledTaskPrincipal -UserId '$($row[1])' -LogonType Password) -Force"
        } else {
            "Register-ScheduledTask -TaskName `$n -Action `$a -User '$($row[1])' -Password '$pw' -Force"
        }
        $results.rows[$row[0]] = Invoke-Vm @"
`$n = 'DSyncLab_$($row[0])'; `$out = 'C:\lab\out_$($row[0])'; `$prof = 'C:\lab\profile_$($row[0])'
New-Item -ItemType Directory -Force `$out, `$prof | Out-Null
icacls `$out /grant '$($row[1]):(OI)(CI)M' | Out-Null; icacls `$prof /grant '$($row[1]):(OI)(CI)M' | Out-Null
`$fixtures = (Get-ChildItem C:\lab -Directory -Filter 'DistrictSync-*' | Select-Object -First 1).FullName + '\tests\snapshots\input'
icacls C:\lab /grant '$($row[1]):(OI)(CI)RX' | Out-Null
`$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c set DISTRICTSYNC_DATA_DIR=`$prof&& `"C:\Program Files\myBlueprint.DistrictSync\current\DistrictSync.exe`" --sis sd74myedbc --input `"`$fixtures`" --output `$out --dry-run > `$out\console.txt 2>&1"
$register | Out-Null
Start-ScheduledTask -TaskName `$n
for (`$i = 0; `$i -lt 60 -and (Get-ScheduledTask -TaskName `$n).State -eq 'Running'; `$i++) { Start-Sleep 5 }
`$info = Get-ScheduledTaskInfo -TaskName `$n
'last result 0x{0:X8}; console: {1}' -f `$info.LastTaskResult, ((Get-Content `$out\console.txt -ErrorAction SilentlyContinue | Select-Object -Last 5) -join ' | ')
Unregister-ScheduledTask -TaskName `$n -Confirm:`$false
"@
    }
    $results['outcome'] = 'completed'
}
catch {
    $results['outcome'] = "failed: $($_.Exception.Message)"
    Write-Warning $results['outcome']
}
finally {
    $results['leftovers'] = Remove-Lab
    $results['finished'] = (Get-Date).ToString('o')
    $results['minutes'] = [math]::Round(((Get-Date) - $started).TotalMinutes, 1)
    $file = Join-Path $ResultsDir ("azure-domain-check-{0}.json" -f $started.ToString('yyyyMMdd-HHmm'))
    $results | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $file
    Say "results: $file"
}
