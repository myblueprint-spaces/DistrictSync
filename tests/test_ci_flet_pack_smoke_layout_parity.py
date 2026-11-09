"""The packed-exe smoke script's output-layout literals match ``src.etl.output_layout`` (DSYNC-57).

``scripts/ci_flet_pack_smoke.py`` runs against a FROZEN artifact and never imports ``src``, so
it re-spells the roster subfolder and the zip prefix. A rename in ``output_layout`` without this
pin would leave the smoke looking in the wrong place.
"""

from __future__ import annotations

import ci_flet_pack_smoke as smoke

from src.etl import output_layout


def test_rostering_subdir_matches() -> None:
    assert smoke._ROSTERING_SUBDIR == output_layout.ROSTERING_SUBDIR


def test_zip_prefix_matches() -> None:
    assert smoke._ACSV_ZIP_PREFIX == output_layout.ACSV_ZIP_PREFIX


def test_rostering_entities_are_the_zip_members() -> None:
    names = {f"{name}.csv" for name in smoke._ROSTERING_ENTITIES}
    assert not names & output_layout.STANDALONE_CSV_FILENAMES
    assert len(names) == 5
