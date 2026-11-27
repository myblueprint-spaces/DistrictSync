"""Plan 0055 L1 (DSYNC-92): the local VMware lab script keeps its shape and its limits IN THE SCRIPT."""

from __future__ import annotations

import re
from pathlib import Path

LOCAL = (Path(__file__).resolve().parent.parent / "scripts" / "lab" / "local_lab.ps1").read_text(encoding="utf8")


def test_the_local_lab_is_three_small_vms_from_one_evaluation_iso():
    """The sizes the owner was told (docs/developer/installer-lab.md): 2/4/2 GB, 32 GB thin each (the Server 2022
    minimum, owner 2026-11-26: keep the disks small), 2 vCPU each."""
    for name, memory in (("lab-dc", 2048), ("lab-desktop", 4096), ("lab-core", 2048)):
        assert re.search(
            rf"'{name}'\s*=\s*@\{{ Image = \d; MemoryMB = {memory}; DiskGB = 32; Cpus = 2; UsedGB = \d+ \}}", LOCAL
        ), name
    assert "expected real use" in LOCAL  # the check never reads as "the disks get filled"


def test_the_local_lab_opens_no_remote_session_and_keeps_its_secret_owner_only():
    assert "runProgramInGuest" in LOCAL  # guest commands through VMware Tools
    for remote in ("Enter-PSSession", "Invoke-Command -ComputerName", "New-PSSession", "net use"):
        assert remote not in LOCAL, remote
    assert "/inheritance:r /grant:r" in LOCAL


def test_the_local_lab_snapshots_clean_and_domain_states_and_can_destroy_itself():
    assert "'snapshot', (Get-Vmx $name), 'clean'" in LOCAL
    assert "'snapshot', (Get-Vmx $member), 'domain'" in LOCAL
    assert "'deleteVM'" in LOCAL


def test_the_local_lab_check_is_read_only_and_names_what_is_missing():
    """-Action check detects VMware (registry InstallPath, then the default folders) and reports, changing nothing."""
    body = LOCAL[LOCAL.index("function Test-LabHost") : LOCAL.index("function Initialize-LabDir")]
    assert "InstallPath" in LOCAL and "VersionInfo.ProductVersion" in body
    for needed in ("vmrun.exe", "vmware-vdiskmanager.exe", "windows.iso", "HypervisorPlatform", "evaluation ISO"):
        assert needed in body, needed
    for writer in (
        "Set-",
        "New-Item",
        "Remove-",
        "Invoke-Vmrun",
        "Enable-WindowsOptionalFeature",
        "icacls",
        "Get-LabSecret",
    ):
        assert writer not in body, writer
    assert "'check' { Test-LabHost; exit $script:CheckExitCode }" in LOCAL


def test_there_is_no_azure_lab_script():
    """Owner, 2026-11-26: "im not authorised for azure spend". Nothing in scripts/lab may create Azure resources."""
    lab = Path(__file__).resolve().parent.parent / "scripts" / "lab"
    scripts = list(lab.glob("*.ps1"))
    assert [p.name for p in scripts] == ["local_lab.ps1"]  # positive twin: the scan sees the lab folder
    for script in scripts:
        assert "az vm create" not in script.read_text(encoding="utf8")


def test_the_lab_folder_is_owner_only_and_a_failed_icacls_stops_the_script():
    """The secret and the answer ISOs (which carry the same password) live in one owner-only folder. A failed icacls does
    not throw in PowerShell, so the exit code is checked; an earlier version wrote files NAMED after the password."""
    body = LOCAL[LOCAL.index("function Initialize-LabDir") : LOCAL.index("function Get-LabSecret")]
    assert "/inheritance:r /grant:r" in body and ':(OI)(CI)F"' in body
    assert "if ($LASTEXITCODE -ne 0) { throw" in body
    assert LOCAL.index("Initialize-LabDir\n") < LOCAL.index("Set-Content -NoNewline -Path $file")


def test_guest_commands_need_no_signed_in_session_and_never_carry_the_password():
    assert "'-interactive'" not in LOCAL  # no session exists after a reboot
    domain = LOCAL[LOCAL.index("function Initialize-LabDomain") : LOCAL.index("$targets = ")]
    assert "'$pw'" not in domain and "Get-LabSecret" not in domain  # the guest reads it from $GuestSecret
    assert "Remove-GuestSecret" in domain
    # every guest step names the marker that proves it finished
    for marker in ("'promoted'", "'static'", "'joined'", "'accounts done'", "'gmsa ok'"):
        assert domain.count(marker) >= 2, marker  # produced by the guest and expected by the host


def test_the_hypervisor_check_does_not_depend_on_the_hyper_v_feature():
    body = LOCAL[LOCAL.index("function Test-LabHost") : LOCAL.index("function Initialize-LabDir")]
    assert "HypervisorPresent" in body and "Microsoft-Hyper-V" not in body
    assert "Test-Path -PathType Leaf $iso" in body
