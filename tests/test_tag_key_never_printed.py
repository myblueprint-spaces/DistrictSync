"""The account-tag key never leaves ``src/utils/account_tag.py`` (plan 0055 N1 follow-up).

The key is this computer's ``MachineGuid``. A tag plus the key plus a short list of likely account names could be
reversed, so the key itself must never reach ``--diagnose``, a log, Run History or a support report: only the TAG does.
Pinned two ways: no other module reads the key at all (a source scan with a positive twin), and the ``--diagnose``
report, run with a distinctive key in place, prints the tag and not the key.
"""

from __future__ import annotations

import io
from pathlib import Path

from src.utils import account_tag, diagnostics

SRC = Path(__file__).resolve().parent.parent / "src"
KEY_READERS = ("_read_machine_guid", "tag_key", "MachineGuid", "MACHINE_KEY_VALUE", "MACHINE_KEY_PATH")


def _readers(root: Path) -> list[str]:
    hits = []
    for source in root.rglob("*.py"):
        if source.name == "account_tag.py" and source.parent.name == "utils":
            continue
        text = source.read_text(encoding="utf-8")
        if any(name in text for name in KEY_READERS):
            hits.append(source.name)
    return hits


def test_only_the_tag_module_reads_the_key():
    assert _readers(SRC) == []


def test_positive_twin_the_scan_finds_a_planted_reader(tmp_path):
    (tmp_path / "support_bundle.py").write_text("from src.utils.account_tag import tag_key\n", encoding="utf-8")
    assert _readers(tmp_path) == ["support_bundle.py"]


def test_the_diagnose_report_prints_the_tag_never_the_key(monkeypatch):
    guid = "7f3c2b9e-0d4a-4e1f-9c8b-key-never-printed"
    monkeypatch.setattr(account_tag, "tag_key", lambda *, create=True: guid.encode())
    monkeypatch.setattr(account_tag, "process_account", lambda: "CORP\\jane")
    out = io.StringIO()
    assert diagnostics.run_diagnose(out) == 0
    report = out.getvalue()
    tag = account_tag.account_tag("CORP\\jane", key=guid.encode())
    assert tag and tag in report  # positive twin: the report really ran with this key
    assert guid not in report and guid.upper() not in report
