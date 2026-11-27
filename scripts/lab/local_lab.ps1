<#
.SYNOPSIS
  Plan 0055 L1 (DSYNC-92): the LOCAL installer lab on VMware Workstation Pro (owner decision 2026-11-23: "okay vmware").

.DESCRIPTION
  Three throwaway Windows Server 2022 evaluation VMs, built unattended from the one evaluation ISO:

    lab-dc       Server Core, 2 vCPU, 2 GB, 32 GB thin   domain controller (gMSA and domain-account rows only)
    lab-desktop  Desktop Experience, 2 vCPU, 4 GB, 32 GB thin most matrix rows: workgroup first, domain member later
    lab-core     Server Core, 2 vCPU, 2 GB, 32 GB thin     headless and Server Core rows

  What the owner does by hand (docs/developer/installer-lab.md): install VMware Workstation Pro, turn on Windows
  Hypervisor Platform, download the Server 2022 evaluation ISO. Everything else is this script:

    .\local_lab.ps1 -Action check [-IsoPath C:\Lab\iso\<eval>.iso]                # read-only: what is ready, what is missing
    .\local_lab.ps1 -Action create   -IsoPath C:\Lab\iso\<eval>.iso [-Vm lab-desktop]   # build (unattended) and snapshot "clean"
    .\local_lab.ps1 -Action start|stop -Vm lab-desktop
    .\local_lab.ps1 -Action snapshot|revert -Vm lab-desktop -Snapshot clean
    .\local_lab.ps1 -Action run -Vm lab-desktop -Command "Get-ComputerInfo | Select OsName"
    .\local_lab.ps1 -Action copy-in -Vm lab-desktop -HostPath .\msi-n.msi -GuestPath C:\lab\msi-n.msi
    .\local_lab.ps1 -Action domain                                                 # promote lab-dc, join the members, gMSA
    .\local_lab.ps1 -Action destroy [-Vm lab-core]                                 # delete the VM(s) and their files

  Guest commands go through VMware Tools (vmrun runProgramInGuest), installed by the answer file's specialize pass, so no
  network share or remote session is opened. Synthetic data only. The lab administrator password is generated once,
  kept in <LabDir>\lab-secret.txt (owner-only ACL), and never printed; the lab is throwaway.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('check', 'create', 'start', 'stop', 'snapshot', 'revert', 'run', 'copy-in', 'domain', 'destroy', 'status')]
    [string]$Action,
    [string]$IsoPath,
    [ValidateSet('lab-dc', 'lab-desktop', 'lab-core')][string]$Vm,
    [string]$Snapshot = 'clean',
    [string]$Command,
    [string]$HostPath,
    [string]$GuestPath,
    [string]$LabDir = 'C:\Lab\vms'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# -------------------------------------------------------------------------- the lab (pinned by tests/test_lab_script.py)
$Vms = [ordered]@{
    # DiskGB: 32 is the Windows Server 2022 minimum (owner 2026-11-26: keep the disks small). The disk is thin: it is a
    # ceiling, not a reservation. UsedGB is the expected real use after install, Tools and a snapshot or two.
    'lab-dc'      = @{ Image = 1; MemoryMB = 2048; DiskGB = 32; Cpus = 2; UsedGB = 12 }   # index 1: Standard, Server Core
    'lab-desktop' = @{ Image = 2; MemoryMB = 4096; DiskGB = 32; Cpus = 2; UsedGB = 22 }   # index 2: Standard, Desktop Experience
    'lab-core'    = @{ Image = 1; MemoryMB = 2048; DiskGB = 32; Cpus = 2; UsedGB = 12 }
}
$Domain = 'lab.dsync.test'
$Netbios = 'LAB'
$GuestOs = 'windows2019srvnext-64'   # VMware's guest id for Server 2022
# -----------------------------------------------------------------------------------------------------------------------

function Say($m) { Write-Output "[$((Get-Date).ToString('HH:mm:ss'))] $m" }

function Find-VMwareDir {
    # The installer records its folder in the registry; the two default folders are the fallback. $null when not found.
    $candidates = @()
    foreach ($key in 'HKLM:\SOFTWARE\WOW6432Node\VMware, Inc.\VMware Workstation', 'HKLM:\SOFTWARE\VMware, Inc.\VMware Workstation') {
        $props = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue   # StrictMode: never dot into a missing key
        if ($props -and $props.PSObject.Properties['InstallPath']) { $candidates += $props.InstallPath }
    }
    $candidates += "${env:ProgramFiles(x86)}\VMware\VMware Workstation", "$env:ProgramFiles\VMware\VMware Workstation"
    foreach ($dir in $candidates) {
        if ($dir -and (Test-Path (Join-Path $dir 'vmrun.exe'))) { return $dir.TrimEnd('\') }
    }
    return $null
}

function Get-VMwareDir {
    $dir = Find-VMwareDir
    if (-not $dir) { throw 'VMware Workstation Pro is not installed (vmrun.exe not found). Run -Action check; see docs/developer/installer-lab.md.' }
    return $dir
}

function Test-LabHost {
    # Read-only: says what is ready and what is still missing. Changes nothing. Exit code 0 = ready to create, 1 = not yet.
    $missing = @()
    $dir = Find-VMwareDir
    if ($dir) {
        $exe = Join-Path $dir 'vmware.exe'
        $version = if (Test-Path $exe) { (Get-Item $exe).VersionInfo.ProductVersion } else { 'unknown' }
        Say "ok      VMware Workstation $version at $dir"
        foreach ($tool in 'vmrun.exe', 'vmware-vdiskmanager.exe', 'windows.iso') {
            if (Test-Path (Join-Path $dir $tool)) { Say "ok      $tool" } else { Say "MISSING $tool in $dir"; $missing += $tool }
        }
    } else {
        Say 'MISSING VMware Workstation Pro (no vmrun.exe in the registered or default folder)'
        $missing += 'VMware Workstation Pro'
    }
    # The Windows hypervisor runs under WSL2, Docker, Memory Integrity and Hyper-V alike (Windows 11 Home has no Hyper-V
    # feature but can still run it); whenever it runs, VMware needs Windows Hypervisor Platform.
    $whp = Get-CimInstance Win32_OptionalFeature -Filter "Name='HypervisorPlatform'" -ErrorAction SilentlyContinue
    $hypervisorRunning = [bool](Get-CimInstance Win32_ComputerSystem).HypervisorPresent
    if ($whp -and $whp.InstallState -eq 1) {
        Say 'ok      Windows Hypervisor Platform is on'
    } elseif ($hypervisorRunning) {
        Say 'MISSING Windows Hypervisor Platform is off while the Windows hypervisor runs: VMware needs it (turn it on, restart)'
        $missing += 'Windows Hypervisor Platform'
    } else {
        Say 'ok      the Windows hypervisor is not running; VMware uses its own (Windows Hypervisor Platform not needed)'
    }
    $iso = $IsoPath
    if (-not $iso) {
        $found = @(Get-ChildItem 'C:\Lab\iso\*.iso' -ErrorAction SilentlyContinue)
        if ($found.Count -gt 0) { $iso = $found[0].FullName }
    }
    if ($iso -and (Test-Path -PathType Leaf $iso)) {
        Say "ok      evaluation ISO $iso ($([math]::Round((Get-Item $iso).Length / 1GB, 1)) GB)"
    } else {
        Say 'MISSING the Windows Server 2022 evaluation ISO (none at -IsoPath or in C:\Lab\iso\)'
        $missing += 'evaluation ISO'
    }
    $ramGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
    $needRam = [math]::Ceiling((($Vms.Values | ForEach-Object { $_.MemoryMB }) | Measure-Object -Sum).Sum / 1024) + 4
    if ($ramGb -ge $needRam) { Say "ok      $ramGb GB memory (all three VMs at once need about $needRam GB)" }
    else { Say "WARN    $ramGb GB memory: run the VMs one or two at a time (all three need about $needRam GB)" }
    $root = [IO.Path]::GetPathRoot($LabDir)
    $freeGb = [math]::Round((Get-PSDrive -Name $root.Substring(0, 1)).Free / 1GB)
    $needDisk = (($Vms.Values | ForEach-Object { $_.DiskGB }) | Measure-Object -Sum).Sum
    $expectDisk = (($Vms.Values | ForEach-Object { $_.UsedGB }) | Measure-Object -Sum).Sum
    $usage = "expected real use about $expectDisk GB; the thin disks can never pass $needDisk GB together"
    if ($freeGb -ge $needDisk) { Say "ok      $freeGb GB free on $root ($usage)" }
    elseif ($freeGb -ge $expectDisk) { Say "ok      $freeGb GB free on $root ($usage; enough for the expected use)" }
    else { Say "WARN    $freeGb GB free on $root, less than the expected use ($usage)" }
    # Say writes to the output stream, so the verdict goes in a script variable, not a return value.
    $script:CheckExitCode = if ($missing.Count -eq 0) { 0 } else { 1 }
    if ($missing.Count -eq 0) { Say 'ready: .\local_lab.ps1 -Action create -IsoPath <the ISO above>' }
    else { Say "not ready; still missing: $($missing -join ', ')" }
}

function Initialize-LabDir {
    # The WHOLE lab folder is owner-only before anything is written in it: the secret, and the answer ISOs that carry
    # the same password, inherit it. Checked, because a failed icacls does not throw.
    New-Item -ItemType Directory -Force $LabDir | Out-Null
    icacls $LabDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "${env:USERDOMAIN}\${env:USERNAME}:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "could not make $LabDir owner-only (icacls exit $LASTEXITCODE); nothing was written" }
}

function Get-LabSecret {
    Initialize-LabDir
    $file = Join-Path $LabDir 'lab-secret.txt'
    if (-not (Test-Path $file)) {
        $pw = (-join ((48..57) + (65..90) + (97..122) | Get-Random -Count 20 | ForEach-Object { [char]$_ })) + '!a1'
        Set-Content -NoNewline -Path $file -Value $pw
    }
    return (Get-Content -Raw $file)
}

function Get-Vmx([string]$name) { Join-Path $LabDir "$name\$name.vmx" }

function Invoke-Vmrun([string[]]$arguments) {
    $vmrun = Join-Path (Get-VMwareDir) 'vmrun.exe'
    $out = & $vmrun -T ws @arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "vmrun $($arguments[0]) failed: $out" }
    return $out
}

function Invoke-Guest([string]$name, [string]$script, [string]$expect = '') {
    # Runs PowerShell in the guest as the lab administrator through VMware Tools (no signed-in session needed); output
    # comes back via a temp file. With -expect, the output must contain that marker or the step throws: guest PowerShell
    # exits 0 even when a command in it failed. Guest scripts never contain the password: they read it from
    # $GuestSecret (Send-GuestSecret), so an error that quotes the failing line cannot print it.
    $pw = Get-LabSecret
    $vmx = Get-Vmx $name
    $remoteOut = 'C:\Windows\Temp\lab-out.txt'
    $wrapped = "& { $script } *> $remoteOut"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($wrapped))
    Invoke-Vmrun @('-gu', 'Administrator', '-gp', $pw, 'runProgramInGuest', $vmx, 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', "-NoProfile -EncodedCommand $encoded") | Out-Null
    $local = Join-Path $env:TEMP "lab-out-$name.txt"
    Invoke-Vmrun @('-gu', 'Administrator', '-gp', $pw, 'CopyFileFromGuestToHost', $vmx, $remoteOut, $local) | Out-Null
    $out = Get-Content -Raw $local
    Remove-Item -Force $local
    if ($expect -and ($out -notmatch [regex]::Escape($expect))) { throw "$name : the guest step did not finish ('$expect' missing)" }
    return $out
}

$GuestSecret = 'C:\Windows\Temp\lab-secret.txt'
function Send-GuestSecret([string]$name) {
    Invoke-Vmrun @('-gu', 'Administrator', '-gp', (Get-LabSecret), 'CopyFileFromHostToGuest', (Get-Vmx $name), (Join-Path $LabDir 'lab-secret.txt'), $GuestSecret) | Out-Null
}
function Remove-GuestSecret([string]$name) { Invoke-Guest $name "Remove-Item -Force '$GuestSecret'; 'gone'" 'gone' | Out-Null }

function Wait-Tools([string]$name, [int]$minutes = 60) {
    $deadline = (Get-Date).AddMinutes($minutes)
    while ((Get-Date) -lt $deadline) {
        # A real guest operation, not checkToolsState: Workstation 26 with Tools 13 keeps reporting 'installed'
        # while guest operations already work.
        try { Invoke-Vmrun @('-gu', 'Administrator', '-gp', (Get-LabSecret), 'listProcessesInGuest', (Get-Vmx $name)) | Out-Null; return } catch { }
        Start-Sleep -Seconds 20
    }
    throw "$name did not finish installing within $minutes minutes"
}

function New-AnswerIso([string]$name, [int]$imageIndex, [string]$pw, [string]$isoOut) {
    $xml = @"
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <SetupUILanguage><UILanguage>en-US</UILanguage></SetupUILanguage>
      <InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <DiskConfiguration><Disk wcm:action="add"><DiskID>0</DiskID><WillWipeDisk>true</WillWipeDisk>
        <CreatePartitions><CreatePartition wcm:action="add"><Order>1</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition></CreatePartitions>
        <ModifyPartitions><ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Active>true</Active><Format>NTFS</Format><Letter>C</Letter></ModifyPartition></ModifyPartitions>
      </Disk></DiskConfiguration>
      <ImageInstall><OSImage>
        <InstallFrom><MetaData wcm:action="add"><Key>/IMAGE/INDEX</Key><Value>$imageIndex</Value></MetaData></InstallFrom>
        <InstallTo><DiskID>0</DiskID><PartitionID>1</PartitionID></InstallTo>
      </OSImage></ImageInstall>
      <UserData><AcceptEula>true</AcceptEula></UserData>
    </component>
  </settings>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <ComputerName>$name</ComputerName><TimeZone>Pacific Standard Time</TimeZone>
    </component>
    <!-- VMware Tools installs here, as SYSTEM: oobeSystem FirstLogonCommands never run on Server Core (no Explorer).
         Newer Tools ISOs ship setup.exe; older ones setup64.exe. -->
    <component name="Microsoft-Windows-Deployment" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <RunSynchronous><RunSynchronousCommand wcm:action="add"><Order>1</Order>
        <Path>powershell -NoProfile -Command "`$d = (Get-Volume | Where-Object FileSystemLabel -eq 'VMware Tools').DriveLetter; foreach (`$f in 'setup64.exe','setup.exe') { `$s = `$d + ':\' + `$f; if (`$d -and (Test-Path `$s)) { Start-Process `$s -ArgumentList '/s /v\`"/qn REBOOT=R\`"' -Wait; break } }"</Path>
        <Description>VMware Tools</Description></RunSynchronousCommand></RunSynchronous>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <UserAccounts><AdministratorPassword><Value>$pw</Value><PlainText>true</PlainText></AdministratorPassword></UserAccounts>
      <AutoLogon><Enabled>true</Enabled><Username>Administrator</Username><Password><Value>$pw</Value><PlainText>true</PlainText></Password><LogonCount>1</LogonCount></AutoLogon>
      <OOBE><HideEULAPage>true</HideEULAPage><SkipMachineOOBE>true</SkipMachineOOBE><ProtectYourPC>3</ProtectYourPC></OOBE>
    </component>
  </settings>
</unattend>
"@
    # An ISO holding autounattend.xml at its root (Windows Setup reads it from any drive), built with Windows' own IMAPI2.
    $stage = Join-Path $env:TEMP "lab-answer-$name"
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $stage | Out-Null
    Set-Content -Encoding UTF8 (Join-Path $stage 'autounattend.xml') $xml
    $image = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
    $image.FileSystemsToCreate = 3   # ISO9660 + Joliet
    $image.VolumeName = 'ANSWER'
    $image.Root.AddTree($stage, $false)
    $stream = $image.CreateResultImage().ImageStream
    if (-not ('LabIsoWriter' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class LabIsoWriter {
    public static void Write(object comStream, string path) {
        IStream stream = (IStream)comStream;
        byte[] buffer = new byte[65536];
        IntPtr read = Marshal.AllocHGlobal(sizeof(int));
        try {
            using (FileStream file = File.Create(path)) {
                while (true) {
                    stream.Read(buffer, buffer.Length, read);
                    int n = Marshal.ReadInt32(read);
                    if (n <= 0) break;
                    file.Write(buffer, 0, n);
                }
            }
        } finally { Marshal.FreeHGlobal(read); }
    }
}
'@
    }
    [LabIsoWriter]::Write($stream, $isoOut)
    Remove-Item -Recurse -Force $stage
}

function New-LabVm([string]$name) {
    if (-not $IsoPath -or -not (Test-Path $IsoPath)) { throw '-IsoPath must point at the Windows Server 2022 evaluation ISO.' }
    $spec = $Vms[$name]
    $dir = Join-Path $LabDir $name
    if (Test-Path (Get-Vmx $name)) { Say "$name already exists; skipping"; return }
    New-Item -ItemType Directory -Force $dir | Out-Null
    $vmware = Get-VMwareDir
    Say "$name : disk $($spec.DiskGB) GB (thin), $($spec.MemoryMB) MB, $($spec.Cpus) vCPU"
    & (Join-Path $vmware 'vmware-vdiskmanager.exe') -c -s "$($spec.DiskGB)GB" -a lsilogic -t 0 (Join-Path $dir "$name.vmdk") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "vdiskmanager failed for $name" }
    $answer = Join-Path $dir 'answer.iso'
    New-AnswerIso $name $spec.Image (Get-LabSecret) $answer
    @"
.encoding = "UTF-8"
config.version = "8"
virtualHW.version = "21"
displayName = "$name"
guestOS = "$GuestOs"
firmware = "bios"
memsize = "$($spec.MemoryMB)"
numvcpus = "$($spec.Cpus)"
pciBridge0.present = "TRUE"
pciBridge4.present = "TRUE"
pciBridge4.virtualDev = "pcieRootPort"
pciBridge4.functions = "8"
pciBridge5.present = "TRUE"
pciBridge5.virtualDev = "pcieRootPort"
pciBridge5.functions = "8"
pciBridge6.present = "TRUE"
pciBridge6.virtualDev = "pcieRootPort"
pciBridge6.functions = "8"
pciBridge7.present = "TRUE"
pciBridge7.virtualDev = "pcieRootPort"
pciBridge7.functions = "8"
scsi0.present = "TRUE"
scsi0.virtualDev = "lsisas1068"
scsi0:0.present = "TRUE"
scsi0:0.fileName = "$name.vmdk"
sata0.present = "TRUE"
sata0:0.present = "TRUE"
sata0:0.deviceType = "cdrom-image"
sata0:0.fileName = "$IsoPath"
sata0:1.present = "TRUE"
sata0:1.deviceType = "cdrom-image"
sata0:1.fileName = "$answer"
sata0:2.present = "TRUE"
sata0:2.deviceType = "cdrom-image"
sata0:2.fileName = "$(Join-Path $vmware 'windows.iso')"
ethernet0.present = "TRUE"
ethernet0.connectionType = "nat"
ethernet0.virtualDev = "e1000e"
ethernet0.addressType = "generated"
bios.bootOrder = "hdd,cdrom"
tools.syncTime = "TRUE"
"@ | Set-Content -Encoding UTF8 (Get-Vmx $name)
    Invoke-Vmrun @('start', (Get-Vmx $name), 'nogui') | Out-Null
    Say "$name : installing Windows unattended (about 15-25 minutes)"
    Wait-Tools $name
    Invoke-Vmrun @('snapshot', (Get-Vmx $name), 'clean') | Out-Null
    Say "$name : ready, snapshot 'clean' taken"
}

function Initialize-LabDomain {
    $read = "`$pw = ConvertTo-SecureString (Get-Content -Raw '$GuestSecret') -AsPlainText -Force"
    foreach ($vm in 'lab-dc', 'lab-desktop', 'lab-core') { Send-GuestSecret $vm }
    Say 'promote lab-dc'
    Invoke-Guest 'lab-dc' "$read; Install-WindowsFeature AD-Domain-Services | Out-Null; Install-ADDSForest -DomainName $Domain -DomainNetbiosName $Netbios -SafeModeAdministratorPassword `$pw -Force -NoRebootOnCompletion -ErrorAction Stop | Out-Null; 'promoted'" 'promoted' | Out-Null
    Invoke-Vmrun @('reset', (Get-Vmx 'lab-dc'), 'soft') | Out-Null
    Wait-Tools 'lab-dc'
    $dcIp = (Invoke-Vmrun @('getGuestIPAddress', (Get-Vmx 'lab-dc'), '-wait')).Trim()
    Say "lab-dc is $dcIp; make it static and point the members at it"
    Invoke-Guest 'lab-dc' "`$i = Get-NetIPConfiguration | Where-Object IPv4DefaultGateway | Select-Object -First 1; `$a = `$i.IPv4Address | Select-Object -First 1; `$gw = `$i.IPv4DefaultGateway.NextHop; Set-NetIPInterface -InterfaceIndex `$i.InterfaceIndex -Dhcp Disabled; Remove-NetIPAddress -InterfaceIndex `$i.InterfaceIndex -AddressFamily IPv4 -Confirm:`$false -ErrorAction SilentlyContinue; Remove-NetRoute -InterfaceIndex `$i.InterfaceIndex -DestinationPrefix 0.0.0.0/0 -Confirm:`$false -ErrorAction SilentlyContinue; New-NetIPAddress -InterfaceIndex `$i.InterfaceIndex -IPAddress $dcIp -PrefixLength `$a.PrefixLength -DefaultGateway `$gw -ErrorAction Stop | Out-Null; Set-DnsClientServerAddress -InterfaceIndex `$i.InterfaceIndex -ServerAddresses 127.0.0.1; 'static'" 'static' | Out-Null
    foreach ($member in 'lab-desktop', 'lab-core') {
        Say "join $member"
        Invoke-Guest $member "$read; `$i = Get-NetIPConfiguration | Where-Object IPv4DefaultGateway | Select-Object -First 1; Set-DnsClientServerAddress -InterfaceIndex `$i.InterfaceIndex -ServerAddresses $dcIp; `$c = New-Object PSCredential('$Netbios\Administrator', `$pw); Add-Computer -DomainName $Domain -Credential `$c -Force -ErrorAction Stop; 'joined'" 'joined' | Out-Null
        Invoke-Vmrun @('reset', (Get-Vmx $member), 'soft') | Out-Null
        Wait-Tools $member
    }
    Say 'gMSA and the stored-password service account'
    Invoke-Guest 'lab-dc' @"
$read
`$ErrorActionPreference = 'Stop'
Add-KdsRootKey -EffectiveTime ((Get-Date).AddHours(-10)) | Out-Null
New-ADGroup -Name DSyncHosts -GroupScope Global
Add-ADGroupMember DSyncHosts -Members 'lab-desktop`$','lab-core`$'
New-ADServiceAccount -Name svc_dsync -DNSHostName svc_dsync.$Domain -PrincipalsAllowedToRetrieveManagedPassword DSyncHosts
New-ADUser -Name svc_dsyncpw -AccountPassword `$pw -Enabled `$true -PasswordNeverExpires `$true
New-ADUser -Name lab_user -AccountPassword `$pw -Enabled `$true
'accounts done'
"@ 'accounts done' | Out-Null
    foreach ($member in 'lab-desktop', 'lab-core') {
        Invoke-Vmrun @('reset', (Get-Vmx $member), 'soft') | Out-Null   # picks up DSyncHosts
        Wait-Tools $member
        Invoke-Guest $member "Install-WindowsFeature RSAT-AD-PowerShell | Out-Null; Install-ADServiceAccount svc_dsync -ErrorAction Stop; if (Test-ADServiceAccount svc_dsync) { 'gmsa ok' }" 'gmsa ok' | Out-Null
        Say "$member : gMSA svc_dsync installed and tested"
    }
    foreach ($vm in 'lab-dc', 'lab-desktop', 'lab-core') { Remove-GuestSecret $vm }
    foreach ($member in 'lab-desktop', 'lab-core') { Invoke-Vmrun @('snapshot', (Get-Vmx $member), 'domain') | Out-Null }
    Say "domain ready; snapshot 'domain' taken on the members"
}

$targets = if ($Vm) { @($Vm) } else { @($Vms.Keys) }
switch ($Action) {
    'check' { Test-LabHost; exit $script:CheckExitCode }
    'create' { foreach ($t in $targets) { New-LabVm $t } }
    'start' { foreach ($t in $targets) { Invoke-Vmrun @('start', (Get-Vmx $t), 'nogui') | Out-Null; Say "$t started" } }
    'stop' { foreach ($t in $targets) { Invoke-Vmrun @('stop', (Get-Vmx $t), 'soft') | Out-Null; Say "$t stopped" } }
    'snapshot' { foreach ($t in $targets) { Invoke-Vmrun @('snapshot', (Get-Vmx $t), $Snapshot) | Out-Null; Say "$t snapshot '$Snapshot'" } }
    'revert' { foreach ($t in $targets) { Invoke-Vmrun @('revertToSnapshot', (Get-Vmx $t), $Snapshot) | Out-Null; Say "$t reverted to '$Snapshot'" } }
    'run' { if (-not $Vm -or -not $Command) { throw '-Vm and -Command are required' }; Invoke-Guest $Vm $Command }
    'copy-in' {
        if (-not $Vm -or -not $HostPath -or -not $GuestPath) { throw '-Vm, -HostPath and -GuestPath are required' }
        Invoke-Vmrun @('-gu', 'Administrator', '-gp', (Get-LabSecret), 'CopyFileFromHostToGuest', (Get-Vmx $Vm), (Resolve-Path $HostPath).Path, $GuestPath) | Out-Null
        Say "copied to $Vm : $GuestPath"
    }
    'domain' { Initialize-LabDomain }
    'status' { Invoke-Vmrun @('list') }
    'destroy' {
        foreach ($t in $targets) {
            $vmx = Get-Vmx $t
            if (Test-Path $vmx) {
                try { Invoke-Vmrun @('stop', $vmx, 'hard') | Out-Null } catch { }
                Invoke-Vmrun @('deleteVM', $vmx) | Out-Null
                Remove-Item -Recurse -Force (Join-Path $LabDir $t) -ErrorAction SilentlyContinue
                Say "$t deleted"
            }
        }
    }
}
