"""Convert loads the config's file set, not the input folder's contents (plan 0051 Slice 1).

Convert used to read EVERY ``.csv``/``.txt`` in the picked folder and parse the lot,
while the CLI read only ``extract_required_files(config)``. A district that pointed
Convert at its raw MyEd BC export folder therefore had its run decided by extracts
DistrictSync never reads: SD67's died on an empty ``AccidentInformation.txt`` (a
district with no accident records exports the file empty, and an empty file cannot be
parsed by any encoding/delimiter) while their nightly over the same folder succeeded.

These pin the parity, not just the symptom. Reading through ``load_data`` also brings
the disk path's case-insensitive resolution and its case-COLLISION raise to Convert,
so three of the four divergences close in one substitution.

Runs under the autouse isolation fixture, so AppConfig + the run store land in a
per-test tmp profile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.app_config import AppConfig
from src.config.loader import load_config
from src.etl.extractor import DataExtractor, ExtractionError
from src.etl.pipeline import extract_required_files
from src.ui_flet.convert_result import ConvertStatus
from src.ui_flet.screens.convert import convert_job
from tests.test_pipeline_run_store import _write_myedbc_input

#: A real MyEd BC extract that no config references. A district with nothing to report
#: exports it EMPTY, which is the shape that cannot be parsed.
UNREFERENCED_EMPTY = "AccidentInformation.txt"


@pytest.fixture()
def gde_input(tmp_path: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    _write_myedbc_input(d)
    return d


@pytest.fixture()
def gde_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


def _configure(gde_input: Path, gde_output: Path) -> None:
    AppConfig(input_dir=str(gde_input), output_dir=str(gde_output), sis_type="myedbc").save()


class TestUnreferencedFilesCannotDecideARun:
    def test_an_empty_unreferenced_extract_no_longer_fails_the_run(self, gde_input: Path, gde_output: Path) -> None:
        """The reported SD67 fault, reduced. Raised ExtractionError before plan 0051."""
        _configure(gde_input, gde_output)
        (gde_input / UNREFERENCED_EMPTY).write_bytes(b"")

        result = convert_job("myedbc", str(gde_input))

        assert result.status is ConvertStatus.DELIVERED
        assert result.entity_counts.get("Students", 0) > 0

    def test_an_unreferenced_file_is_never_opened(self, gde_input: Path) -> None:
        """The positive twin of the test above — assert on the FILE SET, not on survival.

        Without this, the test above would keep passing for the wrong reason the moment
        an empty file stops raising (plan 0051 Slice 2): a run that survives because the
        stranger parsed to an empty frame is not the behaviour being pinned. What must
        hold is that the config's set is what gets read.
        """
        required = extract_required_files(load_config("myedbc"))

        assert UNREFERENCED_EMPTY not in required

        (gde_input / UNREFERENCED_EMPTY).write_bytes(b"")
        loaded = DataExtractor(str(gde_input)).load_data(required)

        assert UNREFERENCED_EMPTY not in loaded
        assert set(loaded) == set(required)

    def test_a_blank_line_only_unreferenced_file_is_also_ignored(self, gde_input: Path, gde_output: Path) -> None:
        """A second unparseable shape, so the pin is not keyed to zero bytes alone.

        Deliberately NOT random bytes: those PARSE (latin1 never fails, and the python
        engine reads them as one column), so a "garbage content" test would be green
        today and prove nothing. Empty and blank-line-only are the two shapes that
        actually defeat delimiter detection — verified, not assumed.
        """
        _configure(gde_input, gde_output)
        (gde_input / "ConductIncident.txt").write_bytes(b"\n")

        assert convert_job("myedbc", str(gde_input)).status is ConvertStatus.DELIVERED


class TestConvertAndCLIResolveFilenamesIdentically:
    def test_a_case_mismatched_source_file_resolves(self, gde_input: Path, gde_output: Path) -> None:
        """The disk path resolves case-insensitively; the old bytes path did not.

        Before plan 0051 this reached the delivery-integrity gate as INCOMPLETE_ROSTER —
        a true statement about the symptom that named nothing about the cause, on a
        folder the nightly converted correctly.
        """
        _configure(gde_input, gde_output)
        (gde_input / "StudentDemographicInformation.txt").rename(gde_input / "studentdemographicinformation.txt")

        result = convert_job("myedbc", str(gde_input))

        assert result.status is ConvertStatus.DELIVERED
        assert result.entity_counts.get("Students", 0) > 0

    def test_a_case_collision_fails_loudly_rather_than_guessing(self, gde_input: Path, gde_output: Path) -> None:
        """Two spellings and a config naming NEITHER exactly: loading the wrong one
        would convert the wrong roster, so the extractor refuses. New to Convert —
        the bytes path keyed by the on-disk name and would have picked one silently.
        """
        _configure(gde_input, gde_output)
        body = (gde_input / "StudentDemographicInformation.txt").read_bytes()
        (gde_input / "StudentDemographicInformation.txt").unlink()
        (gde_input / "studentdemographicinformation.txt").write_bytes(body)
        (gde_input / "STUDENTDEMOGRAPHICINFORMATION.TXT").write_bytes(body)
        # Same probe as `test_extractor`'s collision test: a case-INSENSITIVE filesystem
        # (Windows, default macOS) makes the second write an overwrite, so the collision
        # cannot be built there at all. Count real DIRECTORY ENTRIES — not a glob, which is
        # itself case-insensitive on Windows and would report two matches for one file.
        demographics = [p for p in gde_input.iterdir() if p.name.lower() == "studentdemographicinformation.txt"]
        if len(demographics) < 2:  # pragma: no cover - platform-dependent
            pytest.skip("case-insensitive filesystem: the second write replaced the first")

        with pytest.raises(ExtractionError, match="match 'StudentDemographicInformation.txt'"):
            convert_job("myedbc", str(gde_input))


class TestNoUsableInputStaysReachable:
    def test_an_empty_folder_still_reports_no_input(self, tmp_path: Path, gde_output: Path) -> None:
        """``load_data`` inserts an EMPTY frame per absent file where the old bytes path
        omitted the key, so Convert's ``not raw_data`` test would never fire again and
        NO_INPUT would be unreachable. The emptiness check has to look at the FRAMES."""
        empty_dir = tmp_path / "empty_input"
        empty_dir.mkdir()
        _configure(empty_dir, gde_output)

        assert convert_job("myedbc", str(empty_dir)).status is ConvertStatus.NO_INPUT

    def test_a_partial_folder_still_runs(self, gde_input: Path, gde_output: Path) -> None:
        """The twin: only a folder with NOTHING usable is NO_INPUT. A district missing one
        optional extract must still convert — a per-entity skip-on-empty is legitimate."""
        _configure(gde_input, gde_output)
        (gde_input / "EmergencyContactInformation.txt").unlink()

        assert convert_job("myedbc", str(gde_input)).status is not ConvertStatus.NO_INPUT


class TestSchoolYearSourcesAreNotSilentlyNarrowed:
    """`extract_required_files` may EXCLUDE a config's school-year source file.

    Its docstring is explicit: ``school_year_sources`` are included only when an enabled
    entity ALSO names them, and otherwise ``determine_school_year`` falls back to the
    calendar-date heuristic. Four shipped configs are in exactly that state, so reading
    the config's set rather than the folder's moves Convert from source-derived to
    calendar-derived for them — Convert now MATCHES the CLI, which has always done this,
    but it is a real behaviour change and not the "risk is nil" the plan first claimed.

    It is harmless today only because none of those configs' ACTIVE entities uses a
    year-derived field. That is an accident of their ``enabled_entities``, not a
    construction, and it is what this test pins: enabling ``Classes`` on one of these
    tiers would silently re-key every Class ID via ``append_year_to_id``.
    """

    #: Configs whose `school_year_sources` file is NOT in their required set.
    NARROWED = ("mbp_core", "mbponly", "sd38myedbc", "sd51attendance")

    @pytest.mark.parametrize("config_name", NARROWED)
    def test_the_narrowed_configs_are_still_exactly_these(self, config_name: str) -> None:
        cfg = load_config(config_name)
        required = set(extract_required_files(cfg))
        sources = set(cfg.global_config.school_year_sources.values())

        assert sources - required, (
            f"{config_name} no longer relies on the calendar fallback — it is no longer at "
            "risk, so drop it from NARROWED and from the plan 0051 note."
        )

    @pytest.mark.parametrize("config_name", NARROWED)
    def test_no_active_entity_depends_on_the_school_year(self, config_name: str) -> None:
        """The guard. Fails the moment one of these tiers enables a year-derived field."""
        cfg = load_config(config_name)
        mappings = cfg.to_raw_dict()["mappings"]

        offenders = [
            f"{entity}.{column}"
            for entity in cfg.active_entities()
            for column, spec in mappings[entity]["field_map"].items()
            if isinstance(spec, dict) and (spec.get("use_academic_year") or spec.get("append_year_to_id"))
        ]

        assert not offenders, (
            f"{config_name} now has year-derived field(s) {offenders} while its school-year "
            "source file sits outside `extract_required_files` — so it would silently take the "
            "calendar fallback. Add the source file to the required set before enabling these."
        )


class TestAnUnreadableSourceFileFailsLoudly:
    def test_an_unreadable_required_file_raises_rather_than_being_skipped(
        self, gde_input: Path, gde_output: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A locked/unreadable input file now STOPS the run instead of vanishing from it.

        The retired folder read swallowed a per-file ``OSError`` and carried on, so an
        export held open by Excel or an AV scanner produced a run that reported success
        having silently dropped that entity — a wrong roster delivered quietly, which is
        the failure mode this product fails loud to avoid. ``load_data`` does not guard the
        read, so Convert now behaves as the CLI always has. Accepted deliberately (plan
        0051 review item 3): the admin gets Convert's error card rather than a clean
        result that is missing people.
        """
        _configure(gde_input, gde_output)
        locked = gde_input / "StudentDemographicInformation.txt"
        real_read_bytes = Path.read_bytes

        def _deny(self: Path, *a: object, **k: object) -> bytes:
            if self.name == locked.name:
                raise PermissionError(13, "The process cannot access the file")
            return real_read_bytes(self, *a, **k)

        monkeypatch.setattr(Path, "read_bytes", _deny)

        with pytest.raises(PermissionError):
            convert_job("myedbc", str(gde_input))
