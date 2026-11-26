"""Plan 0055 L1 (DSYNC-92): the throwaway Azure domain check keeps its hard limits IN THE SCRIPT.

Owner decision 2026-11-23: one instance, minimal, only before a real release, CAD 5 per run at most. These pins make
loosening a limit a visible test edit rather than a quiet change to a script nobody re-reads.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent / "scripts" / "lab" / "azure_domain_check.ps1").read_text(
    encoding="utf8"
)


def _const(name: str) -> str:
    match = re.search(rf"^\${name}\s*=\s*'?([^'\s#]+)'?", SCRIPT, re.MULTILINE)
    assert match, name
    return match.group(1)


def test_the_limits_are_hard_coded():
    assert _const("VmSize") == "Standard_D2as_v5"
    assert _const("Priority") == "Spot"
    assert _const("DiskSku") == "Standard_LRS"
    assert int(_const("TimeBoxMinutes")) <= 180
    assert int(_const("CostCeilingCad")) <= 5
    assert _const("Prefix") == "dsynctest-"
    assert _const("ResourceGroup") == "customer-experience-ca"


def test_the_worst_case_fits_under_the_ceiling_at_the_on_demand_rate():
    worst = (
        (float(_const("WorstCaseVmCadPerHour")) + float(_const("DiskCadPerHour"))) * int(_const("TimeBoxMinutes")) / 60
    )
    assert worst <= int(_const("CostCeilingCad"))
    assert "exceeds the ceiling" in SCRIPT  # and the script refuses to start otherwise


def test_one_vm_spot_no_public_ip_no_inbound_and_an_azure_side_shutdown_backstop():
    assert SCRIPT.count("az vm create") == 1
    assert "--priority $Priority --eviction-policy Delete" in SCRIPT
    assert "--public-ip-address '\"\"'" in SCRIPT and "--nsg '\"\"'" in SCRIPT
    assert "az vm auto-shutdown" in SCRIPT


def test_everything_is_deleted_by_name_in_a_finally_and_never_the_resource_group():
    assert re.search(r"finally\s*\{[^}]*Remove-Lab", SCRIPT, re.DOTALL)
    assert "Remove-Lab | Out-Null   # anything a killed run left behind" in SCRIPT
    assert "az group delete" not in SCRIPT
    assert "starts_with(name, '$Prefix')" in SCRIPT


def test_every_wait_checks_the_deadline():
    assert "function Assert-InTime" in SCRIPT
    assert SCRIPT.count("Assert-InTime") >= 3


def test_the_caveat_is_in_every_result():
    assert "not a typical member server" in SCRIPT

