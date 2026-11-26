"""Plan 0055 U1 (DSYNC-97): the Group Policy templates set exactly the key, value and modes the app reads.

The ADMX is the district IT surface for the policy; if it drifted from ``src/update/policy.py`` a policy set in the editor
would be read as malformed (and so ``off``) or not read at all.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from src.update import policy

_ROOT = Path(__file__).resolve().parent.parent / "deploy" / "group-policy"
_NS = {"p": "http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions"}


def test_the_admx_writes_the_key_and_value_the_app_reads():
    tree = ET.parse(_ROOT / "DistrictSync.admx")
    (rule,) = tree.getroot().findall(".//p:policy", _NS)
    assert rule.get("class") == "Machine"
    assert rule.get("key").casefold() == policy.POLICY_KEY_PATH.casefold()  # registry paths ignore case
    (enum,) = rule.findall(".//p:enum", _NS)
    assert enum.get("valueName") == policy.POLICY_VALUE_NAME
    values = [item.find("p:value/p:string", _NS).text for item in enum.findall("p:item", _NS)]
    assert tuple(values) == policy.UPDATE_MODES


def test_the_adml_carries_every_string_the_admx_names_and_defaults_to_notify():
    admx = (_ROOT / "DistrictSync.admx").read_text(encoding="utf8")
    adml = ET.parse(_ROOT / "en-US" / "DistrictSync.adml").getroot()
    ids = {s.get("id") for s in adml.findall(".//p:string", _NS)}
    import re

    for name in re.findall(r"\$\(string\.([A-Za-z_]+)\)", admx):
        assert name in ids, name
    (dropdown,) = adml.findall(".//p:dropdownList", _NS)
    assert policy.UPDATE_MODES[int(dropdown.get("defaultItem"))] == policy.DEFAULT_UPDATE_MODE


def test_disabled_in_group_policy_means_off():
    """Without it, Disabled deletes the value and reads as Not configured (notify). It writes the string "off" to the
    enum's own value through a disabledList; a policy-level valueName would also write a REG_DWORD there."""
    tree = ET.parse(_ROOT / "DistrictSync.admx")
    (rule,) = tree.getroot().findall(".//p:policy", _NS)
    assert rule.get("valueName") is None  # no second writer of the enum's value
    assert rule.find("p:disabledValue", _NS) is None
    (item,) = rule.findall("p:disabledList/p:item", _NS)
    assert item.get("key").casefold() == policy.POLICY_KEY_PATH.casefold()
    assert item.get("valueName") == policy.POLICY_VALUE_NAME
    assert item.find("p:value/p:string", _NS).text == policy.OFF


def test_the_policy_children_follow_the_admx_schema_order():
    """The ADMX schema is a sequence; a loader can reject an out-of-order file, and ET.parse would not notice."""
    order = [
        "parentCategory",
        "supportedOn",
        "enabledValue",
        "disabledValue",
        "enabledList",
        "disabledList",
        "elements",
    ]
    tree = ET.parse(_ROOT / "DistrictSync.admx")
    (rule,) = tree.getroot().findall(".//p:policy", _NS)
    seen = [child.tag.split("}")[1] for child in rule]
    assert seen == sorted(seen, key=order.index)
