"""Tests for the self-service overlay authoring layer (plan 0044 slice 1).

``src/config/authoring.py`` is the engine behind the district config editor: it
emits a THIN ``_base:`` overlay into the user ``mappings/`` dir that the real
loader, CLI and pipeline can already run. What is pinned here:

- **Emission goldens** — the whole dict, for a full overlay and for an
  all-defaults one (acceptance (2): the all-defaults resolved config is
  ``to_raw_dict``-equal to its base).
- **The chain-companion rule** — a narrower grade scope drags ``homeroom_grades``
  out with it, because ``_deep_merge`` REPLACES lists.
- **Rename propagation** — one filename change reaches every entity role AND
  ``global_config.school_year_sources``, with the no-divergence invariant enforced.
- **Nothing invalid or torn reaches disk** — a failed load-back writes nothing
  (with its positive write twin), and an ``os.replace`` failure leaves neither a
  target nor staging litter.
- **Acceptance (1), end to end and IN-PROCESS** — an overlay whose renames
  reproduce the SD74 snapshot's filenames drives ``run_pipeline`` over the real
  snapshot inputs with no missing-file complaint, while the same overlay WITHOUT
  the renames does complain (the positive twin that proves the propagation is what
  made the files load).

All writes land in the per-test isolated profile via the autouse
``isolated_user_profile`` fixture in ``tests/conftest.py`` — the real
``~/.local/share/DistrictSync`` is never touched.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
import yaml

from src.config.authoring import (
    ALLOWED_BASES,
    CREATOR_ENTITIES,
    OverlaySpec,
    build_overlay,
    delete_overlay,
    derive_sis_id,
    is_custom_sis_id,
    overlay_path,
    write_overlay,
)
from src.config.loader import available_configs, load_config, validate_overlay
from src.etl.pipeline import run_pipeline
from src.utils.paths import user_mappings_dir

SNAPSHOT_INPUT = Path(__file__).parent / "snapshots" / "input"

#: The base filenames the SD74 snapshot extract renames (see
#: ``config/mappings/sd74myedbc_mapping.yaml`` + ``tests/snapshots/input/``).
#: ``StudentDemographicInformation.txt`` and ``CourseInformation.txt`` keep the
#: standard names, so they are deliberately absent.
SD74_RENAMES = {
    "StaffInformationEnhanced.txt": "StaffInformation.txt",
    "EmergencyContactInformation.txt": "ParentInformation.txt",
    "StudentSchedule.txt": "studentcourseselection.txt",
    "ClassInformationEnh.txt": "ClassInfoEnhanced.txt",
}

#: The base ``myedbc`` homeroom list, restated so the chain-companion assertions
#: read as intent rather than as "whatever the base says".
BASE_HOMEROOM = ["IT", "PR", "PK", "TK", "KG", "01", "02", "03", "04", "05", "06", "07"]


@pytest.fixture
def base_config():
    """The real bundled ``myedbc`` config, resolved and validated."""
    return load_config("myedbc")


def _spec(**overrides) -> OverlaySpec:
    """An SD93 spec with the mandatory identity fields filled in."""
    fields = {
        "sd_number": 93,
        "district_name": "SD93 - Authoring Test",
        "district_domains": ("sd93.bc.ca",),
        "base": "myedbc",
    }
    fields.update(overrides)
    return OverlaySpec(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Id derivation + shape predicate
# ---------------------------------------------------------------------------


class TestSisId:
    def test_derive_sis_id_shape_and_charset(self):
        assert derive_sis_id(93) == "sd93custom"
        # The whole point of routing through validate_sis_type: the result must be
        # safe as a filename stem AND as a --sis argument.
        assert derive_sis_id(8).isalnum()

    @pytest.mark.parametrize("bad", [0, -1, True])
    def test_derive_sis_id_rejects_non_positive_and_bool(self, bad):
        # `True` is an int subclass — unguarded it would author "sd1custom".
        with pytest.raises(ValueError, match="positive int"):
            derive_sis_id(bad)

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("sd48custom", True),
            ("sd048custom", True),
            ("sd93custom", True),
            ("sd40myedbc", False),
            ("custom", False),
            ("sd48customx", False),
            ("sdcustom", False),
            ("SD48CUSTOM", False),
            (None, False),
            (48, False),
        ],
    )
    def test_is_custom_sis_id(self, value, expected):
        assert is_custom_sis_id(value) is expected


# ---------------------------------------------------------------------------
# Spec boundary validation
# ---------------------------------------------------------------------------


class TestSpecValidation:
    @pytest.mark.parametrize("bad_target", ["../x.txt", "a/b.txt", "", " x.txt", "x.txt ", ".", "..", "d\\x.txt"])
    def test_rename_target_must_be_a_bare_filename(self, bad_target):
        # This value is joined onto the admin's input dir by the extractor, so it is
        # a path boundary, not a formatting nicety.
        with pytest.raises(ValueError):
            _spec(source_file_renames={"StudentSchedule.txt": bad_target})

    def test_a_bare_filename_target_is_accepted(self):
        # Positive twin for the boundary above.
        spec = _spec(source_file_renames={"StudentSchedule.txt": "sched.txt"})
        assert spec.source_file_renames == {"StudentSchedule.txt": "sched.txt"}

    def test_blank_district_name_is_refused(self):
        with pytest.raises(ValueError, match="district_name"):
            _spec(district_name="   ")

    def test_bad_domain_is_refused_without_echoing_the_value(self):
        leaked = "Someone.Person@sd93.bc.ca"
        with pytest.raises(ValueError) as excinfo:
            _spec(district_domains=(leaked,))
        assert leaked not in str(excinfo.value)
        assert "not quoted" in str(excinfo.value)

    def test_base_outside_the_allowlist_is_refused(self):
        with pytest.raises(ValueError, match="base must be one of"):
            _spec(base="sd74myedbc")

    def test_build_overlay_re_checks_the_base_allowlist(self, base_config):
        # Defence in depth: the frozen spec cannot normally carry a bad base, so
        # bypass __post_init__ the way only a bug could.
        spec = _spec()
        object.__setattr__(spec, "base", "sd74myedbc")
        with pytest.raises(ValueError, match="base must be one of"):
            build_overlay(spec, resolved_base=base_config)

    def test_empty_enabled_entities_is_refused(self):
        with pytest.raises(ValueError, match="at least one entity"):
            _spec(enabled_entities=())

    def test_unauthorable_entity_is_refused(self):
        # StudentAttendance stays vendor-authored (plan non-goal).
        assert "StudentAttendance" not in CREATOR_ENTITIES
        with pytest.raises(ValueError, match="StudentAttendance"):
            _spec(enabled_entities=("Students", "StudentAttendance"))


# ---------------------------------------------------------------------------
# Emission goldens
# ---------------------------------------------------------------------------


class TestEmission:
    def test_full_overlay_golden(self, base_config):
        spec = _spec(
            enabled_entities=("Students", "Staff", "Classes", "Enrollments"),
            homeroom_grades=("KG", "01", "02"),
            class_rostering_grades=("KG", "01", "02", "08"),
            student_rostering_grades=("KG", "01", "02", "08", "09"),
            source_file_renames={"StudentSchedule.txt": "sched.txt"},
        )
        assert build_overlay(spec, resolved_base=base_config) == {
            "_base": "myedbc",
            "sis": "sd93custom",
            "district_name": "SD93 - Authoring Test",
            "district_domains": ["sd93.bc.ca"],
            "global_config": {
                "enabled_entities": ["Students", "Staff", "Classes", "Enrollments"],
                "homeroom_grades": ["KG", "01", "02"],
                "class_rostering_grades": ["KG", "01", "02", "08"],
                "student_rostering_grades": ["KG", "01", "02", "08", "09"],
                "school_year_sources": {"student_schedule": "sched.txt"},
            },
            "mappings": {
                "Classes": {"source_files": {"student_schedule": "sched.txt"}},
                "Enrollments": {"source_files": {"student_schedule": "sched.txt"}},
            },
        }

    def test_root_keys_are_emitted_in_the_declared_order(self, base_config):
        spec = _spec(source_file_renames={"StudentSchedule.txt": "sched.txt"})
        assert list(build_overlay(spec, resolved_base=base_config)) == [
            "_base",
            "sis",
            "district_name",
            "district_domains",
            "global_config",
            "mappings",
        ]

    def test_all_defaults_emits_only_the_identity_keys(self, base_config):
        assert build_overlay(_spec(), resolved_base=base_config) == {
            "_base": "myedbc",
            "sis": "sd93custom",
            "district_name": "SD93 - Authoring Test",
            "district_domains": ["sd93.bc.ca"],
        }

    def test_empty_domains_are_emitted_explicitly(self, base_config):
        # An explicit empty list is the honest "this district claims no staff
        # domain" statement; omitting the key would be indistinguishable from
        # "we never asked".
        overlay = build_overlay(_spec(district_domains=()), resolved_base=base_config)
        assert overlay["district_domains"] == []

    def test_acceptance_2_all_defaults_resolves_byte_equal_to_its_base(self, base_config):
        """An all-defaults overlay changes NOTHING the ETL can see (acceptance (2))."""
        overlay = build_overlay(_spec(), resolved_base=base_config)
        resolved = validate_overlay(overlay)
        assert resolved.to_raw_dict() == base_config.to_raw_dict()
        # ...and the three presentation keys DO differ — the positive half, without
        # which the equality above would be vacuously satisfiable by an empty overlay.
        assert resolved.sis == "sd93custom" != base_config.sis
        assert resolved.district_name != base_config.district_name
        assert resolved.district_domains == ["sd93.bc.ca"] != base_config.district_domains

    def test_no_version_key_is_ever_emitted(self, base_config):
        spec = _spec(student_rostering_grades=("08", "09"), homeroom_grades=())
        overlay = build_overlay(spec, resolved_base=base_config)
        assert "version" not in overlay
        # ...and the resolved config still HAS a version (inherited), so the gate ran.
        assert validate_overlay(overlay).version == base_config.version


class TestGradeChainCompanion:
    def test_student_scope_alone_drags_homeroom_out(self, base_config):
        """`_deep_merge` REPLACES lists, so the chain's lower bound must be pinned."""
        overlay = build_overlay(_spec(student_rostering_grades=("08", "09")), resolved_base=base_config)
        assert overlay["global_config"] == {
            "homeroom_grades": BASE_HOMEROOM,
            "student_rostering_grades": ["08", "09"],
        }

    def test_class_scope_alone_drags_homeroom_out(self, base_config):
        overlay = build_overlay(
            _spec(
                class_rostering_grades=("KG", "01", "02", "03", "04", "05", "06", "07", "IT", "PR", "PK", "TK", "08")
            ),
            resolved_base=base_config,
        )
        assert overlay["global_config"]["homeroom_grades"] == BASE_HOMEROOM

    def test_spec_homeroom_wins_over_the_inherited_list(self, base_config):
        overlay = build_overlay(
            _spec(homeroom_grades=(), student_rostering_grades=("08", "09")),
            resolved_base=base_config,
        )
        assert overlay["global_config"] == {
            "homeroom_grades": [],
            "student_rostering_grades": ["08", "09"],
        }
        # A secondary-only district is only VALID with the empty homeroom — the
        # companion rule pins the chain, it does not rescue a bad one.
        assert validate_overlay(overlay).global_config.homeroom_grades == []

    def test_homeroom_alone_does_not_force_the_other_two(self, base_config):
        overlay = build_overlay(_spec(homeroom_grades=("KG", "01")), resolved_base=base_config)
        assert overlay["global_config"] == {"homeroom_grades": ["KG", "01"]}

    def test_a_spec_equal_to_the_base_emits_no_global_config(self, base_config):
        overlay = build_overlay(
            _spec(
                enabled_entities=tuple(base_config.global_config.enabled_entities),
                homeroom_grades=tuple(base_config.global_config.homeroom_grades),
            ),
            resolved_base=base_config,
        )
        assert "global_config" not in overlay

    def test_homeroom_sentinel_passes_through_as_the_string(self, base_config):
        overlay = build_overlay(_spec(class_rostering_grades="homeroom"), resolved_base=base_config)
        assert overlay["global_config"]["class_rostering_grades"] == "homeroom"
        assert overlay["global_config"]["homeroom_grades"] == BASE_HOMEROOM
        # It survives the loader too (the ETL branches on the sentinel, not on a list).
        assert validate_overlay(overlay).global_config.class_rostering_grades == "homeroom"


class TestRenamePropagation:
    def test_one_rename_reaches_every_role_and_the_year_source(self, base_config):
        """`StudentSchedule.txt` is named by Classes, Enrollments AND school_year_sources.

        The year source is the load-bearing one: its silent fallback to the date
        heuristic would move every ``append_year_to_id`` Class ID.
        """
        overlay = build_overlay(
            _spec(source_file_renames={"StudentSchedule.txt": "sched.txt"}),
            resolved_base=base_config,
        )
        assert overlay["global_config"] == {"school_year_sources": {"student_schedule": "sched.txt"}}
        assert overlay["mappings"] == {
            "Classes": {"source_files": {"student_schedule": "sched.txt"}},
            "Enrollments": {"source_files": {"student_schedule": "sched.txt"}},
        }
        # ONLY the changed role is emitted — deep merge merges dict keys, so the
        # untouched roles must stay inherited (a restated role would fork the base).
        assert set(overlay["mappings"]["Classes"]["source_files"]) == {"student_schedule"}
        resolved = validate_overlay(overlay)
        assert resolved.mappings["Classes"].source_files["course_info"] == "CourseInformation.txt"
        assert resolved.mappings["Classes"].source_files["student_schedule"] == "sched.txt"

    def test_rename_reaches_a_DISABLED_entity_too(self, base_config):
        """A stale reference in a disabled entity is a trap the moment it is enabled."""
        assert "StudentCourses" not in base_config.active_entities()
        overlay = build_overlay(
            _spec(source_file_renames={"CourseInformation.txt": "courses.txt"}),
            resolved_base=base_config,
        )
        assert overlay["mappings"]["StudentCourses"]["source_files"] == {"course_info": "courses.txt"}
        assert overlay["mappings"]["CourseInfo"]["source_files"] == {"course_info": "courses.txt"}

    def test_unknown_original_fails_loud(self, base_config):
        # A typo would otherwise silently no-op and surface as a missing file at 2 a.m.
        with pytest.raises(ValueError, match="never references"):
            build_overlay(
                _spec(source_file_renames={"StudentSchdule.txt": "sched.txt"}),
                resolved_base=base_config,
            )

    def test_two_originals_onto_one_target_fails_loud(self, base_config):
        with pytest.raises(ValueError, match="onto one filename"):
            build_overlay(
                _spec(
                    source_file_renames={
                        "StudentSchedule.txt": "everything.txt",
                        "CourseInformation.txt": "everything.txt",
                    }
                ),
                resolved_base=base_config,
            )

    def test_no_divergence_invariant_catches_partial_propagation(self, base_config, monkeypatch):
        """The invariant is checked against the EMITTED overlay, not the rename map.

        Simulate the bug it exists for — an emission that misses one of the sites
        naming a renamed file — and prove it raises rather than shipping a config
        whose entities disagree about which file they read.
        """
        import src.config.authoring as authoring

        real = authoring._build_renames

        def _lossy(spec, sites):
            entity_overrides, year_sources = real(spec, sites)
            entity_overrides.pop("Enrollments", None)
            return entity_overrides, year_sources

        monkeypatch.setattr(authoring, "_build_renames", _lossy)
        with pytest.raises(ValueError, match="diverges on source file"):
            build_overlay(
                _spec(source_file_renames={"StudentSchedule.txt": "sched.txt"}),
                resolved_base=base_config,
            )

    def test_no_renames_emits_no_mappings_block(self, base_config):
        assert "mappings" not in build_overlay(_spec(), resolved_base=base_config)


# ---------------------------------------------------------------------------
# write_overlay / delete_overlay
# ---------------------------------------------------------------------------


class TestWriteOverlay:
    def test_writes_into_the_user_mappings_dir_and_loads_back(self):
        path = write_overlay(_spec(), overwrite=False)
        assert path == user_mappings_dir() / "sd93custom_mapping.yaml"
        assert path.parent == user_mappings_dir()
        config = load_config("sd93custom")
        assert config.sis == "sd93custom"
        assert config.district_domains == ["sd93.bc.ca"]
        assert "sd93custom" in available_configs()

    def test_the_written_text_carries_a_header_and_no_version_line(self):
        path = write_overlay(_spec(), overwrite=False)
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# Generated by DistrictSync's self-service district setup.")
        assert not any(line.startswith("version:") for line in text.splitlines())
        # ...and the resolved config still version-gates cleanly (the inherited pin).
        assert load_config("sd93custom").version == load_config("myedbc").version

    @pytest.mark.parametrize("base", ALLOWED_BASES)
    def test_round_trip_against_every_allowed_base(self, base):
        spec = _spec(base=base)
        path = write_overlay(spec, overwrite=False)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["_base"] == base
        config = load_config("sd93custom")
        assert config.sis == derive_sis_id(93) == "sd93custom"
        assert config.district_domains == ["sd93.bc.ca"]
        assert config.district_name == "SD93 - Authoring Test"
        # The overlay inherits the base's entity selection unchanged.
        assert config.global_config.enabled_entities == load_config(base).global_config.enabled_entities
        assert "sd93custom" in available_configs()

    def test_overwrite_false_refuses_an_existing_file_and_leaves_it_intact(self):
        first = write_overlay(_spec(district_name="First name"), overwrite=False)
        before = first.read_bytes()
        with pytest.raises(FileExistsError, match="sd93custom"):
            write_overlay(_spec(district_name="Second name"), overwrite=False)
        assert first.read_bytes() == before

    def test_overwrite_true_replaces_the_file(self):
        first = write_overlay(_spec(district_name="First name"), overwrite=False)
        before = first.read_bytes()
        second = write_overlay(_spec(district_name="Second name"), overwrite=True)
        assert second == first
        assert second.read_bytes() != before
        assert load_config("sd93custom").district_name == "Second name"

    def test_a_failed_load_back_writes_nothing(self):
        """A secondary-only scope over the INHERITED K-7 homeroom is a chain violation."""
        with pytest.raises(ValueError, match="homeroom_grades"):
            write_overlay(_spec(student_rostering_grades=("08", "09")), overwrite=False)
        assert list(user_mappings_dir().iterdir()) == []

    def test_a_valid_spec_DOES_write_there(self):
        """Positive twin for the assertion above (no vacuous greens)."""
        write_overlay(_spec(homeroom_grades=(), student_rostering_grades=("08", "09")), overwrite=False)
        assert [p.name for p in user_mappings_dir().iterdir()] == ["sd93custom_mapping.yaml"]

    def test_an_unauthorable_enabled_entities_never_reaches_disk(self):
        with pytest.raises(ValueError):
            write_overlay(_spec(enabled_entities=("Nope",)), overwrite=False)
        assert list(user_mappings_dir().iterdir()) == []

    def test_a_torn_write_leaves_no_target_and_no_staging_litter(self, monkeypatch):
        import src.config.authoring as authoring

        def _boom(src, dst):
            raise OSError("simulated promote failure")

        monkeypatch.setattr(authoring.os, "replace", _boom)
        with pytest.raises(OSError, match="simulated promote failure"):
            write_overlay(_spec(), overwrite=False)
        assert list(user_mappings_dir().iterdir()) == []

    def test_a_torn_overwrite_leaves_the_original_byte_intact(self, monkeypatch):
        import src.config.authoring as authoring

        path = write_overlay(_spec(district_name="Original name"), overwrite=False)
        before = path.read_bytes()

        def _boom(src, dst):
            raise OSError("simulated promote failure")

        monkeypatch.setattr(authoring.os, "replace", _boom)
        with pytest.raises(OSError, match="simulated promote failure"):
            write_overlay(_spec(district_name="Replacement name"), overwrite=True)
        assert path.read_bytes() == before
        assert [p.name for p in user_mappings_dir().iterdir()] == ["sd93custom_mapping.yaml"]

    def test_the_promote_really_is_os_replace(self, monkeypatch):
        """Positive twin for the two crash sims: they patch the call the write USES."""
        import src.config.authoring as authoring

        calls: list[tuple[str, str]] = []
        real = os.replace

        def _spy(src, dst):
            calls.append((str(src), str(dst)))
            real(src, dst)

        monkeypatch.setattr(authoring.os, "replace", _spy)
        path = write_overlay(_spec(), overwrite=False)
        assert calls and calls[-1][1] == str(path)

    def test_logs_the_id_and_counts_but_never_the_name_or_a_domain(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.config.authoring"):
            write_overlay(
                _spec(source_file_renames={"StudentSchedule.txt": "sched.txt"}),
                overwrite=False,
            )
        messages = [record.getMessage() for record in caplog.records]
        assert any("sd93custom" in message for message in messages)
        assert not any("sd93.bc.ca" in message or "Authoring Test" in message for message in messages)


class TestDeleteOverlay:
    def test_deletes_a_self_service_overlay(self):
        path = write_overlay(_spec(), overwrite=False)
        assert delete_overlay("sd93custom") is True
        assert not path.exists()

    def test_returns_false_when_absent(self):
        assert delete_overlay("sd93custom") is False

    def test_refuses_a_non_custom_id(self):
        # A user-dir `sd40myedbc` is a hand-placed HOTFIX override of a shipped
        # config — deleting it here would silently revert a district mid-season.
        (user_mappings_dir() / "sd40myedbc_mapping.yaml").write_text("_base: myedbc\n", encoding="utf-8")
        with pytest.raises(ValueError, match="not a self-service config id"):
            delete_overlay("sd40myedbc")
        assert (user_mappings_dir() / "sd40myedbc_mapping.yaml").exists()

    @pytest.mark.parametrize("bad", ["../evil", "../../etc/passwd", "sd93custom/../x", "sd93custom.yaml"])
    def test_refuses_a_traversal_shaped_id(self, bad):
        with pytest.raises(ValueError):
            delete_overlay(bad)

    def test_overlay_path_validates_the_id(self):
        assert overlay_path("sd93custom").name == "sd93custom_mapping.yaml"
        with pytest.raises(ValueError, match="Invalid SIS type"):
            overlay_path("../evil")


# ---------------------------------------------------------------------------
# Acceptance (1) — end to end, in process, against the real SD74 snapshot inputs
# ---------------------------------------------------------------------------


def _missing_file_complaints(records, filenames: set[str]) -> list[str]:
    """Extractor 'File not found' + pipeline 'all source files are empty' lines.

    Filtered to the ``filenames`` of interest, so an unrelated entity's legitimate
    skip can never make this assertion vacuous.
    """
    hits = []
    for record in records:
        message = record.getMessage()
        if "File not found" not in message and "are empty for" not in message:
            continue
        if any(name in message for name in filenames):
            hits.append(message)
    return hits


class TestEndToEndAgainstSnapshotInputs:
    """A self-service overlay whose renames match the SD74 extract's real filenames."""

    def test_renamed_overlay_runs_the_pipeline_over_the_snapshot_inputs(self, tmp_path, caplog):
        write_overlay(
            _spec(district_name="SD93 - Snapshot filenames", source_file_renames=SD74_RENAMES),
            overwrite=False,
        )
        with caplog.at_level(logging.WARNING):
            result = run_pipeline(
                "sd93custom",
                str(SNAPSHOT_INPUT),
                str(tmp_path / "out"),
                dry_run=True,
            )
        assert result.entity_counts["Students"] > 0
        # Every renamed file loaded — no missing-file or all-sources-empty complaint.
        assert _missing_file_complaints(caplog.records, set(SD74_RENAMES.values())) == []
        assert _missing_file_complaints(caplog.records, set(SD74_RENAMES)) == []
        # The renamed sources actually produced their entities (the propagation is
        # what made Classes/Enrollments/Staff/Family runnable at all).
        for entity in ("Staff", "Family", "Classes", "Enrollments"):
            assert result.entity_counts[entity] > 0, entity

    def test_the_same_overlay_WITHOUT_renames_cannot_find_those_files(self, tmp_path, caplog):
        """The positive twin: the renames are what made the run above work."""
        write_overlay(_spec(district_name="SD93 - Standard filenames"), overwrite=False)
        with caplog.at_level(logging.WARNING):
            result = run_pipeline(
                "sd93custom",
                str(SNAPSHOT_INPUT),
                str(tmp_path / "out"),
                dry_run=True,
            )
        complaints = _missing_file_complaints(caplog.records, set(SD74_RENAMES))
        assert complaints, "expected missing-file complaints for the un-renamed sources"
        for original in SD74_RENAMES:
            assert any(original in message for message in complaints), original

        # Students reads StudentDemographicInformation.txt, which is NOT renamed —
        # so the difference between the two runs is exactly the renamed roles.
        assert result.entity_counts["Students"] > 0
        # Staff and Family have no source but a renamed one, so they vanish entirely.
        for entity in ("Staff", "Family"):
            assert result.entity_counts.get(entity, 0) == 0, entity

        # Classes/Enrollments do NOT vanish — the homeroom half is generated from the
        # (un-renamed) demographic file — so the honest claim is that they SHRINK.
        # Quantify it against the renamed run rather than a hard-coded number.
        assert delete_overlay("sd93custom") is True
        write_overlay(
            _spec(district_name="SD93 - Snapshot filenames", source_file_renames=SD74_RENAMES),
            overwrite=False,
        )
        renamed = run_pipeline("sd93custom", str(SNAPSHOT_INPUT), str(tmp_path / "out2"), dry_run=True)
        for entity in ("Classes", "Enrollments"):
            assert result.entity_counts.get(entity, 0) < renamed.entity_counts[entity], entity


# ---------------------------------------------------------------------------
# The search_dirs test seam
# ---------------------------------------------------------------------------


class TestSearchDirsSeam:
    """``write_overlay(search_dirs=…)`` must resolve the BASE from the injected pair.

    Non-vacuous by construction: the injected ``myedbc`` declares a homeroom list the
    real bundled one does not, and minimality is what makes the difference visible —
    a spec matching the INJECTED base emits nothing, while the same spec against the
    REAL base emits the key.
    """

    @pytest.fixture
    def injected_pair(self, tmp_path):
        from src.config.loader import bundle_mappings_dir

        injected_user_dir = tmp_path / "injected_user_mappings"
        injected_user_dir.mkdir()
        raw = yaml.safe_load((bundle_mappings_dir() / "myedbc_mapping.yaml").read_text(encoding="utf-8"))
        raw["global_config"]["homeroom_grades"] = ["KG"]
        (injected_user_dir / "myedbc_mapping.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
        return [injected_user_dir, bundle_mappings_dir()]

    def test_the_injected_base_decides_minimality(self, injected_pair):
        path = write_overlay(_spec(homeroom_grades=("KG",)), overwrite=False, search_dirs=injected_pair)
        # The overlay still lands in the REAL user mappings dir — search_dirs is a
        # RESOLUTION seam, never a write-target seam.
        assert path.parent == user_mappings_dir()
        assert "global_config" not in yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_the_real_base_would_have_emitted_it(self):
        """The twin — without the seam, the same spec DOES emit the key."""
        path = write_overlay(_spec(homeroom_grades=("KG",)), overwrite=False)
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["global_config"] == {"homeroom_grades": ["KG"]}

    def test_an_unknown_base_in_the_injected_pair_fails_loud(self, tmp_path):
        empty = tmp_path / "empty_a"
        also_empty = tmp_path / "empty_b"
        empty.mkdir()
        also_empty.mkdir()
        with pytest.raises(FileNotFoundError, match="myedbc_mapping.yaml"):
            write_overlay(_spec(), overwrite=False, search_dirs=[empty, also_empty])
        assert list(user_mappings_dir().iterdir()) == []


class TestBaseSentinelComparison:
    """A base that ALREADY carries the sentinel makes restating it a no-op.

    Covers the sentinel branch of the base-side comparison: minimality must judge
    ``"homeroom"`` against ``"homeroom"``, not against a list.
    """

    def test_restating_the_base_sentinel_emits_nothing(self, base_config):
        base_config.global_config.class_rostering_grades = "homeroom"
        overlay = build_overlay(_spec(class_rostering_grades="homeroom"), resolved_base=base_config)
        assert "global_config" not in overlay

    def test_a_list_over_a_base_sentinel_still_emits(self, base_config):
        base_config.global_config.class_rostering_grades = "homeroom"
        overlay = build_overlay(
            _spec(class_rostering_grades=tuple(BASE_HOMEROOM) + ("08",)),
            resolved_base=base_config,
        )
        assert overlay["global_config"]["class_rostering_grades"] == [*BASE_HOMEROOM, "08"]


class TestRenameFilenameTypes:
    @pytest.mark.parametrize("bad_target", [None, 7, "x\x00.txt"])
    def test_non_string_and_nul_targets_are_refused(self, bad_target):
        with pytest.raises(ValueError):
            _spec(source_file_renames={"StudentSchedule.txt": bad_target})

    @pytest.mark.parametrize("bad_original", ["", "   ", None])
    def test_blank_or_non_string_originals_are_refused(self, bad_original):
        with pytest.raises(ValueError, match="source_file_renames keys"):
            _spec(source_file_renames={bad_original: "sched.txt"})


class TestDeleteRefusesToLeaveTheMappingsDir:
    def test_a_symlinked_overlay_pointing_outside_is_refused(self, tmp_path):
        """Defence in depth: the id is clean, but the FILE resolves elsewhere.

        ``validate_sis_type`` already excludes separators and dots, so the only way a
        target can leave the mappings dir is a symlink — and following one into an
        unlink would delete a file the admin never put there.
        """
        outside = tmp_path / "outside.yaml"
        outside.write_text("_base: myedbc\n", encoding="utf-8")
        link = user_mappings_dir() / "sd93custom_mapping.yaml"
        link.symlink_to(outside)
        with pytest.raises(ValueError, match="outside the user mappings dir"):
            delete_overlay("sd93custom")
        assert outside.exists()
        assert link.is_symlink()

    def test_a_real_file_in_the_mappings_dir_is_deleted(self):
        """Positive twin — the refusal above is about the symlink, not about the id."""
        write_overlay(_spec(), overwrite=False)
        assert delete_overlay("sd93custom") is True
