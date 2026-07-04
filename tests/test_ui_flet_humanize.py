"""Unit tests for the pure ``humanize.friendly_district_name`` helper (DS-2, COUNTED).

Pins the TOTAL contract of the sole IA-2 humanization helper: a trust surface must
never show a raw config id when a friendly ``district_name`` exists, and must never
crash / go blank on a bad, empty, or unknown config. The synthetic-``config_dir``
fixtures (empty ``district_name`` + malformed YAML) exercise branches no bundled
config can reach — the ``iff non-empty`` clause and the ``except → raw id`` path.

Uses the ``config_dir`` seam (passed straight through to ``loader.load_config``,
whose own ``config_dir`` arg overrides the ``~/.districtsync`` search dirs), so these
are hermetic — no home dependency, no real-config coupling for the fixture branches.
"""

from __future__ import annotations

from pathlib import Path

from src.ui_flet.humanize import friendly_district_name

# The real bundled configs — used only for the "known district" and "unknown id"
# cases (their district_name is asserted structurally, not by hardcoded string).
BUNDLED_MAPPINGS = Path(__file__).resolve().parent.parent / "config" / "mappings"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# A minimal standalone (no `_base`) valid MappingConfig — version + sis + one entity.
_MINIMAL_VALID = """\
version: "1.0"
sis: MyEducationBC
district_name: "{name}"
mappings:
  Students:
    source_files:
      student_demographic: StudentDemographicInformation.txt
    field_map: {{}}
"""


class TestFriendlyDistrictName:
    def test_known_district_returns_friendly_name_not_id(self) -> None:
        # A real bundled config carries a non-empty district_name; the helper must
        # return the human name, never the raw id. Asserted structurally (robust to
        # config-copy edits) — the product invariant is "not the id, and non-empty".
        result = friendly_district_name("sd40myedbc", config_dir=BUNDLED_MAPPINGS)
        assert result and result != "sd40myedbc"

    def test_empty_district_name_falls_back_to_raw_id(self, tmp_path: Path) -> None:
        # REQUIRED synthetic fixture: no bundled config has an empty district_name,
        # so the `iff non-empty` clause is unreachable without this.
        _write(tmp_path / "faux_mapping.yaml", _MINIMAL_VALID.format(name=""))
        assert friendly_district_name("faux", config_dir=tmp_path) == "faux"

    def test_whitespace_district_name_falls_back_to_raw_id(self, tmp_path: Path) -> None:
        # A whitespace-only district_name strips to empty → raw id (same clause).
        _write(tmp_path / "faux_mapping.yaml", _MINIMAL_VALID.format(name="   "))
        assert friendly_district_name("faux", config_dir=tmp_path) == "faux"

    def test_broken_yaml_falls_back_to_raw_id(self, tmp_path: Path) -> None:
        # REQUIRED synthetic fixture: malformed YAML → the `except` path is genuinely
        # hit (falls back to the raw id, never raises, never blanks).
        _write(tmp_path / "broken_mapping.yaml", "district_name: [unterminated\n  : : :")
        assert friendly_district_name("broken", config_dir=tmp_path) == "broken"

    def test_unknown_id_returns_raw_id(self, tmp_path: Path) -> None:
        # No mapping file for the id → FileNotFoundError caught → input unchanged.
        assert friendly_district_name("no_such_district", config_dir=tmp_path) == "no_such_district"

    def test_empty_string_returns_empty(self) -> None:
        assert friendly_district_name("") == ""

    def test_whitespace_input_returns_empty(self) -> None:
        assert friendly_district_name("   ") == ""

    def test_known_district_never_surfaces_the_raw_id(self) -> None:
        # Product invariant across every bundled config: when a friendly name exists
        # (all 9 bundled configs carry one), the helper never returns the raw id.
        for sis_id in ("sd40myedbc", "sd48myedbc", "sd51myedbc", "sd74myedbc"):
            result = friendly_district_name(sis_id, config_dir=BUNDLED_MAPPINGS)
            assert result and result != sis_id
