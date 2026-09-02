"""``config_editor`` — the creator's pure form, gate and stored-fact logic (plan 0044 S3).

Everything the district config creator DECIDES lives in ``src/ui_flet/config_editor.py``
rather than in the view, so it can be pinned headless. What is pinned here:

- **The grade chain by DERIVATION.** Every reachable form state is fed through the REAL
  ``GlobalConfig.check_rostering_grade_scopes`` (via ``build_overlay`` →
  ``validate_overlay``), including the two states that shipped configs actually use: the
  ``"homeroom"`` sentinel when every rostered grade is a homeroom grade, and
  ``homeroom == ()`` for a secondary-only district. No reachable state may produce a
  config the loader refuses.
- **Domains.** Prefill = identity ∪ the vendored table, presumptive ONLY when both are
  empty; an invalid entry is refused WITHOUT echoing the value (the likeliest bad paste
  is a personal address).
- **The hostile-value tables.** ``creator_verified`` and ``authored_with`` are
  hand-editable, so ``stored_verified_digest`` / ``verified_is_current`` /
  ``overlay_staleness`` are pinned malformed-in ⇒ safe-out, each with a positive twin
  (an absent fact must force another test run, never unlock one).
- **Boundedness.** ``humanize_config_error`` may never echo a message: a planted domain
  and a planted path are asserted ABSENT from every category it returns.

All writes land in the per-test isolated profile via the autouse ``isolated_user_profile``
fixture in ``tests/conftest.py``.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest
import yaml

from src.config.app_config import AppConfig
from src.config.authoring import (
    ALLOWED_BASES,
    CREATOR_ENTITIES,
    AuthoredWith,
    OverlaySpec,
    build_overlay,
    current_digest,
    derive_sis_id,
    write_overlay,
)
from src.config.loader import load_config, validate_overlay
from src.config.models import CLASS_ROSTERING_HOMEROOM_SENTINEL
from src.etl.pipeline import PipelineResult
from src.etl.transformers.grades import CEDS_GRADE_CODES, CEDS_MAPPING
from src.ui_flet.config_editor import (
    BASE_LABELS,
    CEDS_GRADE_ORDER,
    CONFIG_ERROR_DOMAIN,
    CONFIG_ERROR_GRADES,
    CONFIG_ERROR_MISSING_BASE,
    CONFIG_ERROR_OTHER,
    CONFIG_ERROR_UNREADABLE,
    FILE_LABELS,
    CreatorForm,
    GateOutcome,
    GateState,
    StalenessFact,
    base_label,
    derive_domains,
    distinct_source_files,
    file_form_rows,
    file_label,
    files_primary_action,
    gate_outcome_for,
    humanize_config_error,
    missing_files,
    overlay_staleness,
    renames_from_resolved,
    sd_number_from_text,
    seed_entities,
    split_domains,
    stored_verified_digest,
    validate_domains,
    verified_is_current,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "ui_flet" / "config_editor.py"

DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64


def _form(**overrides) -> CreatorForm:
    """An SD93 form with the identity fields answered."""
    fields = {"base": "myedbc", "sd_number": 93, "district_name": "SD93 - Editor Test", "domains": ("sd93.bc.ca",)}
    fields.update(overrides)
    return CreatorForm(**fields)  # type: ignore[arg-type]


def _cfg(**overrides) -> AppConfig:
    fields = {"input_dir": "/in", "output_dir": "/out", "sis_type": "sd93custom"}
    fields.update(overrides)
    return AppConfig(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Purity — the claim the module's docstring makes
# ---------------------------------------------------------------------------


class TestPurity:
    def test_the_module_imports_no_flet_and_no_io(self):
        """A pure decision layer that imported the toolkit could not be tested headless,
        and one that imported ``pathlib`` would be a step from doing I/O — the module's
        docstring claims BOTH halves ("no flet, no I/O"), so both are pinned."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        banned = ("flet", "pathlib")
        assert not [name for name in imported if name.split(".")[0] in banned]
        # The positive twin — the sweep really does see this module's imports.
        assert "src.config.authoring" in imported

    def test_an_explicit_empty_rostered_scope_is_refused_at_construction(self):
        """Reviewer finding (S3): `with_rostered` refused `()` but a direct construction did
        not, so `CreatorForm(rostered=(), homeroom=())` emitted `class_rostering_grades: []`
        — a config the loader refuses and `humanize_config_error` mislabels as a chain
        problem. Positive twin: `None` (inherit) and a one-grade scope both construct."""
        with pytest.raises(ValueError, match="at least one grade"):
            CreatorForm(base="myedbc", sd_number=93, district_name="T", rostered=(), homeroom=())
        assert CreatorForm(base="myedbc", sd_number=93, district_name="T").rostered is None
        form = CreatorForm(base="myedbc", sd_number=93, district_name="T", rostered=("08",), homeroom=())
        assert form.rostered == ("08",)


# ---------------------------------------------------------------------------
# Base labels
# ---------------------------------------------------------------------------


class TestBaseLabels:
    def test_every_allowed_base_has_a_plain_language_label(self):
        assert set(BASE_LABELS) == set(ALLOWED_BASES)

    def test_no_label_is_a_raw_config_id_and_none_repeat(self):
        labels = list(BASE_LABELS.values())
        assert len(set(labels)) == len(labels)  # no two picker rows may read identically
        for base, label in BASE_LABELS.items():
            assert base not in label
            assert label.strip() == label and label

    def test_base_label_falls_back_to_the_id_rather_than_blanking_the_row(self):
        assert base_label("mbponly") == BASE_LABELS["mbponly"]
        assert base_label("some_future_base") == "some_future_base"


# ---------------------------------------------------------------------------
# Field rules — what a raw text field's string MEANS (plan 0044 S3 review)
# ---------------------------------------------------------------------------


class TestSdNumberFromText:
    """The four-digit bound is SAFETY-relevant: the value becomes a filename stem and a
    ``--sis`` argument baked into a scheduled task."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("93", 93),
            (" 93 ", 93),  # a pasted value carries whitespace
            ("SD93", 93),  # the launch page's own spelling
            ("#48", 48),
            ("048", 48),  # leading zeros are not a different district
            ("9999", 9999),  # the bound is INCLUSIVE — a real 4-digit number is usable
            ("0", 0),  # representable; refused downstream by `derive_sis_id` (positive int)
        ],
    )
    def test_a_usable_district_number_is_read(self, text, expected):
        assert sd_number_from_text(text) == expected

    @pytest.mark.parametrize("text", ["", "   ", "not a number", "SD", "6045551234", "12345"])
    def test_anything_unusable_answers_None_rather_than_a_wrong_number(self, text):
        assert sd_number_from_text(text) is None

    def test_a_pasted_phone_number_can_never_author_a_config_id(self):
        """The bound's whole point, asserted as the consequence rather than the digit count:
        without it ``derive_sis_id`` would happily author ``sd6045551234custom``. The positive
        twin sits right beside it — the same call on a real district number DOES author one."""
        assert sd_number_from_text("6045551234") is None
        assert derive_sis_id(sd_number_from_text("93") or 0) == "sd93custom"


class TestSplitDomains:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("sd48.bc.ca", ("sd48.bc.ca",)),
            ("sd48.bc.ca, sd48.ca", ("sd48.bc.ca", "sd48.ca")),  # comma
            ("sd48.bc.ca; sd48.ca", ("sd48.bc.ca", "sd48.ca")),  # semicolon
            ("sd48.bc.ca sd48.ca", ("sd48.bc.ca", "sd48.ca")),  # bare whitespace
            ("  sd48.bc.ca  ", ("sd48.bc.ca",)),  # surrounding whitespace
            ("sd48.bc.ca,,  ,sd48.ca,", ("sd48.bc.ca", "sd48.ca")),  # blank entries dropped
            ("", ()),
            ("   ", ()),
            (",;, ", ()),
        ],
    )
    def test_the_split(self, text, expected):
        assert split_domains(text) == expected

    def test_duplicates_and_shape_are_left_to_the_boundary(self):
        """Deliberately NOT de-duplicated or shape-checked here: ``validate_domains`` is the
        ONE boundary that decides both, and splitting that decision in two is how a note that
        should say "that isn't a domain" ends up saying nothing."""
        assert split_domains("sd48.bc.ca, sd48.bc.ca") == ("sd48.bc.ca", "sd48.bc.ca")
        assert validate_domains(split_domains("sd48.bc.ca, sd48.bc.ca")) == ("sd48.bc.ca",)
        # …and an entry that is not a domain survives the split to be REFUSED at the boundary.
        assert split_domains("roster.admin@sd48.bc.ca") == ("roster.admin@sd48.bc.ca",)
        with pytest.raises(ValueError, match="bare lowercase domain"):
            validate_domains(split_domains("roster.admin@sd48.bc.ca"))


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


class TestDeriveDomains:
    def test_identity_leads_and_the_table_follows(self):
        assert derive_domains("someone.ca", 48) == ("someone.ca", "sd48.bc.ca")

    def test_a_domain_in_both_is_not_repeated(self):
        assert derive_domains("sd48.bc.ca", 48) == ("sd48.bc.ca",)

    def test_a_multi_domain_district_keeps_the_tables_order(self):
        """SD63 is one of the two multi-domain rows — order is the source CSV's."""
        assert derive_domains("", 63) == ("saanichschools.ca", "sides.ca", "sd63.bc.ca")

    def test_identity_alone_when_the_table_has_no_row(self):
        assert derive_domains("sd93.bc.ca", 900) == ("sd93.bc.ca",)

    def test_the_table_wins_over_the_conventional_guess(self):
        """SD93's real staff domain is nothing like ``sd93.bc.ca`` — the guess must not
        be offered when the vendored table actually knows the district."""
        assert derive_domains("", 93) == ("csf.bc.ca",)

    def test_the_presumptive_guess_ONLY_when_both_are_empty(self):
        assert derive_domains("", 900) == ("sd900.bc.ca",)
        # ...and it is never mixed IN beside a known domain, where it would look
        # equally authoritative (the twin for the row above).
        assert "sd48.bc.ca" in derive_domains("", 48)
        assert derive_domains("", 34) == ("abbyschools.ca",)

    @pytest.mark.parametrize("bad_sd", [0, -5, True, "48", None])
    def test_total_over_an_unusable_district_number(self, bad_sd):
        assert derive_domains("", bad_sd) == ()
        assert derive_domains("sd93.bc.ca", bad_sd) == ("sd93.bc.ca",)

    @pytest.mark.parametrize("bad_identity", ["", "  ", "someone@example.com", "SD93.BC.CA", None, 42])
    def test_an_unusable_identity_domain_is_dropped_not_prefilled(self, bad_identity):
        assert derive_domains(bad_identity, 48) == ("sd48.bc.ca",)


class TestValidateDomains:
    def test_keeps_valid_entries_de_duplicated_in_order(self):
        assert validate_domains(["sd93.bc.ca", "prn.bc.ca", "sd93.bc.ca"]) == ("sd93.bc.ca", "prn.bc.ca")

    def test_blank_rows_are_dropped_and_an_empty_list_is_legitimate(self):
        assert validate_domains(["sd93.bc.ca", "  ", ""]) == ("sd93.bc.ca",)
        assert validate_domains([]) == ()

    @pytest.mark.parametrize("bad", ["someone@example.com", "SD93.BC.CA", "sd93 .bc.ca", "http://sd93.bc.ca"])
    def test_an_invalid_entry_is_refused_WITHOUT_echoing_the_value(self, bad):
        with pytest.raises(ValueError) as caught:
            validate_domains(["sd93.bc.ca", bad])
        message = str(caught.value)
        assert bad not in message
        # ...and it still says enough to fix: which entry, and what shape is wanted.
        assert "2 of 2" in message and "sd48.bc.ca" in message

    def test_the_form_boundary_runs_the_same_check(self):
        with pytest.raises(ValueError):
            _form().with_domains(["someone@example.com"])
        assert _form().with_domains(["prn.bc.ca"]).domains == ("prn.bc.ca",)


# ---------------------------------------------------------------------------
# Entity seeding
# ---------------------------------------------------------------------------


class TestEntitySeeding:
    @pytest.mark.parametrize(
        "base,expected",
        [
            ("myedbc", ("Students", "Staff", "Family", "Classes", "Enrollments")),
            ("mbp_all", CREATOR_ENTITIES),
            ("mbp_core", ("Students", "CourseInfo", "StudentCourses")),
            ("mbponly", ("CourseInfo", "StudentCourses")),
        ],
    )
    def test_seeded_from_each_of_the_four_bases(self, base, expected):
        assert seed_entities(load_config(base)) == expected

    def test_student_attendance_is_absent_by_construction(self):
        assert "StudentAttendance" not in seed_entities(load_config("sd51attendance"))

    @pytest.mark.parametrize("base", ALLOWED_BASES)
    def test_an_UNTOUCHED_seeded_selection_emits_nothing(self, base):
        """Order matters: a re-ordered seed would emit the key on every overlay."""
        resolved = load_config(base)
        form = _form(base=base, entities=seed_entities(resolved))
        overlay = build_overlay(form.to_overlay_spec(), resolved_base=resolved)
        assert "global_config" not in overlay

    def test_a_CHANGED_selection_does_emit(self):
        """The twin — the silence above is minimality, not a dropped key."""
        resolved = load_config("myedbc")
        form = _form(entities=("Students", "Staff"))
        overlay = build_overlay(form.to_overlay_spec(), resolved_base=resolved)
        assert overlay["global_config"]["enabled_entities"] == ["Students", "Staff"]

    def test_an_empty_explicit_selection_is_refused(self):
        with pytest.raises(ValueError, match="at least one CSV"):
            _form().with_entities([])

    def test_an_unauthorable_entity_is_refused(self):
        with pytest.raises(ValueError, match="StudentAttendance"):
            _form().with_entities(["Students", "StudentAttendance"])

    def test_the_selection_is_canonically_ordered(self):
        assert _form().with_entities(["Enrollments", "Students"]).entities == ("Students", "Enrollments")

    def test_an_unseeded_form_inherits_the_bases_list(self):
        assert _form().to_overlay_spec().enabled_entities is None


# ---------------------------------------------------------------------------
# The CEDS vocabulary
# ---------------------------------------------------------------------------


class TestCedsVocabulary:
    def test_order_is_the_tables_own_de_duplicated_value_order(self):
        assert tuple(dict.fromkeys(CEDS_MAPPING.values())) == CEDS_GRADE_ORDER

    def test_it_is_exactly_the_valid_config_vocabulary(self):
        assert set(CEDS_GRADE_ORDER) == set(CEDS_GRADE_CODES)
        assert len(CEDS_GRADE_ORDER) == len(CEDS_GRADE_CODES)  # de-duplicated

    def test_it_reads_youngest_to_oldest(self):
        assert CEDS_GRADE_ORDER[:5] == ("IT", "PR", "PK", "TK", "KG")
        assert CEDS_GRADE_ORDER.index("01") < CEDS_GRADE_ORDER.index("12")

    def test_Other_keeps_its_mixed_case(self):
        assert "Other" in CEDS_GRADE_ORDER
        assert "other" not in CEDS_GRADE_ORDER
        assert _form().with_rostered(["Other"]).rostered == ("Other",)
        with pytest.raises(ValueError, match="CEDS grade code"):
            _form().with_rostered(["other"])


# ---------------------------------------------------------------------------
# The grade chain — through the REAL validator
# ---------------------------------------------------------------------------


def _resolved_scopes(form: CreatorForm):
    """Emit ``form``'s overlay and resolve it through the REAL loader + chain validator."""
    overlay = build_overlay(form.to_overlay_spec(), resolved_base=load_config(form.base))
    resolved = validate_overlay(overlay)
    scopes = resolved.global_config
    return scopes.homeroom_grades, scopes.class_rostering_grades, scopes.student_rostering_grades


class TestGradeDerivation:
    def test_unanswered_emits_nothing_and_inherits_every_scope(self):
        spec = _form().to_overlay_spec()
        assert (spec.homeroom_grades, spec.class_rostering_grades, spec.student_rostering_grades) == (None, None, None)
        homeroom, class_scope, student = _resolved_scopes(_form())
        assert homeroom == load_config("myedbc").global_config.homeroom_grades
        assert class_scope is None and student is None

    def test_a_subset_homeroom_derives_the_whole_chain(self):
        form = _form().with_rostered(["KG", "01", "08"]).with_homeroom(["KG", "01"])
        assert _resolved_scopes(form) == (["KG", "01"], ["KG", "01", "08"], ["KG", "01", "08"])

    def test_homeroom_EQUAL_to_rostered_derives_the_sentinel(self):
        form = _form().with_rostered(["KG", "01"]).with_homeroom(["KG", "01"])
        spec = form.to_overlay_spec()
        assert spec.class_rostering_grades == CLASS_ROSTERING_HOMEROOM_SENTINEL
        assert _resolved_scopes(form) == (["KG", "01"], CLASS_ROSTERING_HOMEROOM_SENTINEL, ["KG", "01"])

    def test_an_EMPTY_homeroom_is_a_real_answer_for_a_secondary_only_district(self):
        form = _form().with_rostered(["08", "09", "10", "11", "12"])
        assert form.homeroom == ()
        homeroom, class_scope, student = _resolved_scopes(form)
        assert homeroom == []
        assert class_scope == student == ["08", "09", "10", "11", "12"]

    def test_the_chain_companion_is_emitted_so_the_resolved_chain_is_self_contained(self):
        form = _form().with_rostered(["08", "09"])
        overlay = build_overlay(form.to_overlay_spec(), resolved_base=load_config("myedbc"))
        assert "homeroom_grades" in overlay["global_config"]

    @pytest.mark.parametrize(
        "rostered,homeroom",
        [
            (["KG"], ["KG"]),
            (["KG", "01", "02"], ["KG"]),
            (["08", "09", "10", "11", "12"], []),
            (["IT", "PR", "PK", "TK", "KG", "01", "02", "03", "04", "05", "06", "07"], ["KG", "01"]),
            (["Other", "UG"], []),
            (list(CEDS_GRADE_ORDER), list(CEDS_GRADE_ORDER)),
        ],
    )
    def test_every_reachable_answered_state_LOADS(self, rostered, homeroom):
        """The claim: `check_rostering_grade_scopes` can only ever CONFIRM the derivation."""
        form = _form().with_rostered(rostered).with_homeroom(homeroom)
        assert _resolved_scopes(form)  # no raise is the assertion

    def test_a_homeroom_grade_outside_rostered_is_unrepresentable(self):
        with pytest.raises(ValueError, match="not rostered grades"):
            _form().with_rostered(["KG"]).with_homeroom(["08"])

    def test_narrowing_rostered_narrows_homeroom_with_it(self):
        form = _form().with_rostered(["KG", "01", "08"]).with_homeroom(["KG", "01"])
        narrowed = form.with_rostered(["01", "08"])
        assert narrowed.homeroom == ("01",)
        assert _resolved_scopes(narrowed) == (["01"], ["01", "08"], ["01", "08"])

    def test_a_half_answered_chain_cannot_be_constructed(self):
        with pytest.raises(ValueError, match="both be answered"):
            CreatorForm(rostered=("08", "09"))
        with pytest.raises(ValueError, match="both be answered"):
            CreatorForm(homeroom=("KG",))

    def test_clearing_either_half_returns_the_whole_chain_to_inherited(self):
        answered = _form().with_rostered(["KG", "01"]).with_homeroom(["KG"])
        for cleared in (answered.with_rostered(None), answered.with_homeroom(None)):
            assert cleared.rostered is None and cleared.homeroom is None

    def test_an_empty_rostered_selection_is_refused(self):
        with pytest.raises(ValueError, match="at least one grade"):
            _form().with_rostered([])

    def test_the_form_is_frozen_so_a_step_cannot_half_mutate_it(self):
        form = _form()
        with pytest.raises(dataclasses.FrozenInstanceError):
            form.sd_number = 40  # type: ignore[misc]
        assert form.with_district(sd_number=40).sd_number == 40 and form.sd_number == 93


class TestToOverlaySpec:
    def test_it_carries_the_forms_identity_verbatim(self):
        spec = _form().to_overlay_spec()
        assert isinstance(spec, OverlaySpec)
        assert (spec.sd_number, spec.district_name, spec.district_domains, spec.base) == (
            93,
            "SD93 - Editor Test",
            ("sd93.bc.ca",),
            "myedbc",
        )

    def test_S3_emits_no_filename_renames_and_S4_can_pass_them(self):
        assert _form().to_overlay_spec().source_file_renames == {}
        spec = _form().to_overlay_spec(source_file_renames={"StudentSchedule.txt": "sched.txt"})
        assert spec.source_file_renames == {"StudentSchedule.txt": "sched.txt"}

    def test_an_invalid_base_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="base must be one of"):
            _form(base="sd40myedbc")
        with pytest.raises(ValueError, match="base must be one of"):
            _form().with_base("sd40myedbc")

    def test_with_base_switches_the_starting_point(self):
        assert _form().with_base("mbp_core").base == "mbp_core"

    def test_a_directly_constructed_unauthorable_entity_is_refused_too(self):
        """The `with_entities` guard is not the only door — construction checks as well."""
        with pytest.raises(ValueError, match="StudentAttendance"):
            _form(entities=("Students", "StudentAttendance"))


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class TestMissingFiles:
    def test_case_insensitive_matching_against_what_the_folder_holds(self):
        assert missing_files(["Students.txt", "StaffInformation.txt"], ["students.txt"]) == ("StaffInformation.txt",)

    def test_a_complete_folder_reports_none(self):
        """The twin — the list above is a real absence, not a spelling mismatch."""
        assert missing_files(["Students.txt", "StaffInformation.txt"], ["students.TXT", "staffinformation.txt"]) == ()

    def test_the_mappings_spelling_is_what_comes_back(self):
        assert missing_files(["Students.TXT"], []) == ("Students.TXT",)

    def test_expected_order_is_preserved_and_de_duplicated(self):
        assert missing_files(["b.txt", "a.txt", "b.txt"], []) == ("b.txt", "a.txt")

    def test_nothing_expected_reports_none(self):
        assert missing_files([], ["Students.txt"]) == ()


class TestGateOutcomeFor:
    def test_no_output_folder_REFUSES_whatever_else_is_true(self):
        outcome = gate_outcome_for(
            result=PipelineResult(entity_counts={"Students": 5}),
            error=None,
            output_dir_valid=False,
            expected_files=["Students.txt"],
            present_files=[],
        )
        assert outcome == GateOutcome(state=GateState.REFUSED_NO_OUTPUT_DIR)

    def test_nothing_run_yet(self):
        outcome = gate_outcome_for(
            result=None, error=None, output_dir_valid=True, expected_files=["Students.txt"], present_files=[]
        )
        assert outcome.state is GateState.NOT_RUN and outcome.counts == {} and outcome.note == ""

    def test_a_completed_run_PASSES_with_its_counts(self):
        outcome = gate_outcome_for(
            result=PipelineResult(entity_counts={"Students": 12, "Classes": 3}),
            error=None,
            output_dir_valid=True,
            expected_files=["Students.txt"],
            present_files=["Students.txt"],
        )
        assert outcome.state is GateState.PASSED
        assert outcome.counts == {"Students": 12, "Classes": 3}
        assert outcome.missing_files == () and outcome.note == ""

    def test_a_pass_still_reports_missing_files_without_downgrading_the_verdict(self):
        outcome = gate_outcome_for(
            result=PipelineResult(entity_counts={"Students": 12}),
            error=None,
            output_dir_valid=True,
            expected_files=["Students.txt", "StaffInformation.txt"],
            present_files=["Students.txt"],
        )
        assert outcome.state is GateState.PASSED
        assert outcome.missing_files == ("StaffInformation.txt",)

    def test_a_raised_run_FAILS_with_a_bounded_note(self):
        outcome = gate_outcome_for(
            result=None,
            error=FileNotFoundError("/home/admin/sd93custom_mapping.yaml"),
            output_dir_valid=True,
            expected_files=["Students.txt"],
            present_files=[],
        )
        assert outcome.state is GateState.FAILED
        assert outcome.note == CONFIG_ERROR_MISSING_BASE
        assert outcome.counts == {}
        assert outcome.missing_files == ("Students.txt",)

    def test_both_a_result_and_an_error_fails_loud(self):
        with pytest.raises(ValueError, match="exactly one outcome"):
            gate_outcome_for(
                result=PipelineResult(),
                error=RuntimeError("boom"),
                output_dir_valid=True,
                expected_files=[],
                present_files=[],
            )

    def test_RUNNING_is_the_views_transient_state_and_is_never_derived(self):
        derived = {
            gate_outcome_for(
                result=result,
                error=error,
                output_dir_valid=valid,
                expected_files=[],
                present_files=[],
            ).state
            for result, error, valid in [
                (None, None, True),
                (PipelineResult(), None, True),
                (None, RuntimeError("x"), True),
                (None, None, False),
            ]
        }
        assert GateState.RUNNING not in derived
        assert derived == {
            GateState.NOT_RUN,
            GateState.PASSED,
            GateState.FAILED,
            GateState.REFUSED_NO_OUTPUT_DIR,
        }


class TestHumanizeConfigError:
    #: Planted values that must never reach admin-facing copy — a domain (the likeliest
    #: bad paste is a personal address) and a filesystem path.
    PLANTED_DOMAIN = "someone.example.com"
    PLANTED_PATH = "/home/admin/Desktop/district secrets/config.yaml"

    def _planted(self, template: str) -> str:
        return template.format(domain=self.PLANTED_DOMAIN, path=self.PLANTED_PATH)

    def test_a_real_chain_violation_reads_as_a_grade_problem(self):
        overlay = {
            "_base": "myedbc",
            "district_name": "SD93",
            "district_domains": ["sd93.bc.ca"],
            "global_config": {"homeroom_grades": ["KG", "01"], "student_rostering_grades": ["08", "09"]},
        }
        with pytest.raises(ValueError) as caught:
            validate_overlay(overlay)
        assert humanize_config_error(caught.value) == CONFIG_ERROR_GRADES

    def test_a_real_bad_domain_reads_as_a_domain_problem(self):
        with pytest.raises(ValueError) as caught:
            OverlaySpec(
                sd_number=93,
                district_name="SD93",
                district_domains=(f"admin@{self.PLANTED_DOMAIN}",),
                base="myedbc",
            )
        assert humanize_config_error(caught.value) == CONFIG_ERROR_DOMAIN

    def test_a_real_missing_base_reads_as_a_missing_starting_point(self):
        with pytest.raises(FileNotFoundError) as caught:
            validate_overlay({"_base": "no_such_base", "district_name": "SD93", "district_domains": []})
        assert humanize_config_error(caught.value) == CONFIG_ERROR_MISSING_BASE

    @pytest.mark.parametrize(
        "exc,expected",
        [
            (FileNotFoundError("{path}"), CONFIG_ERROR_MISSING_BASE),
            (OSError("cannot read {path}"), CONFIG_ERROR_UNREADABLE),
            (PermissionError("{path}"), CONFIG_ERROR_UNREADABLE),
            (yaml.YAMLError("while scanning {path}"), CONFIG_ERROR_UNREADABLE),
            (ValueError("homeroom_grades must be a SUBSET of student_rostering_grades"), CONFIG_ERROR_GRADES),
            (ValueError("class_rostering_grades got the bare string 'k'"), CONFIG_ERROR_GRADES),
            (ValueError("district_domains entry 1 is not a bare lowercase domain"), CONFIG_ERROR_DOMAIN),
            (ValueError("something else entirely about {domain}"), CONFIG_ERROR_OTHER),
            (RuntimeError("boom {path}"), CONFIG_ERROR_OTHER),
            (SystemExit(1), CONFIG_ERROR_OTHER),
            (KeyError("{domain}"), CONFIG_ERROR_OTHER),
        ],
    )
    def test_the_category_table(self, exc, expected):
        planted = type(exc)(*(self._planted(arg) if isinstance(arg, str) else arg for arg in exc.args))
        assert humanize_config_error(planted) == expected

    def test_no_category_ever_echoes_a_planted_domain_or_path(self):
        for template, _ in [
            ("{path}", None),
            ("cannot read {path}", None),
            ("district_domains entry 1 of 1: {domain}", None),
            ("homeroom_grades not a subset — {domain} {path}", None),
            ("unclassifiable {domain} {path}", None),
        ]:
            note = humanize_config_error(ValueError(self._planted(template)))
            assert self.PLANTED_DOMAIN not in note
            assert self.PLANTED_PATH not in note
            assert "/" not in note.replace("myBlueprint+", "")
            # ...and it still SAYS something (the twin for the bans above).
            assert len(note) > 20

    def test_the_categories_are_a_bounded_set(self):
        categories = {
            CONFIG_ERROR_GRADES,
            CONFIG_ERROR_DOMAIN,
            CONFIG_ERROR_MISSING_BASE,
            CONFIG_ERROR_UNREADABLE,
            CONFIG_ERROR_OTHER,
        }
        assert len(categories) == 5


# ---------------------------------------------------------------------------
# The stored facts
# ---------------------------------------------------------------------------


class TestStoredVerifiedDigest:
    def test_a_well_formed_entry_is_returned(self):
        """The positive twin for the whole hostile table below."""
        assert stored_verified_digest(_cfg(creator_verified={"sd93custom": DIGEST}), "sd93custom") == DIGEST

    def test_an_unrecorded_id_reads_absent(self):
        assert stored_verified_digest(_cfg(creator_verified={"sd40custom": DIGEST}), "sd93custom") is None

    @pytest.mark.parametrize(
        "stored",
        [
            "not a dict",
            ["sd93custom"],
            None,
            42,
            {"sd93custom": DIGEST[:-1]},  # 63 chars
            {"sd93custom": DIGEST.upper()},
            {"sd93custom": {"nested": DIGEST}},
            {"sd93custom": 12345},
            {"sd93custom": None},
            {"sd93custom": ""},
            {"sd93custom": f" {DIGEST} "},
        ],
    )
    def test_a_malformed_map_reads_ABSENT_so_the_gate_re_fires(self, stored):
        assert stored_verified_digest(_cfg(creator_verified=stored), "sd93custom") is None

    @pytest.mark.parametrize("bad_id", ["../evil", "sd93custom/../x", "", "  ", "sd93-custom"])
    def test_an_id_that_is_not_a_config_id_reads_absent(self, bad_id):
        assert stored_verified_digest(_cfg(creator_verified={bad_id: DIGEST}), bad_id) is None

    def test_a_shipped_id_is_readable_too_because_activation_gates_on_ORIGIN(self):
        assert stored_verified_digest(_cfg(creator_verified={"sd40myedbc": DIGEST}), "sd40myedbc") == DIGEST

    @pytest.mark.parametrize("bad_id", [42, None, ["sd93custom"]])
    def test_a_non_string_id_reads_absent_rather_than_raising(self, bad_id):
        assert stored_verified_digest(_cfg(creator_verified={"sd93custom": DIGEST}), bad_id) is None

    def test_a_settings_object_without_the_field_reads_absent_rather_than_raising(self):
        class OldProfile:
            pass

        assert stored_verified_digest(OldProfile(), "sd93custom") is None  # type: ignore[arg-type]


class TestVerifiedIsCurrent:
    def test_matching_digests_are_current(self):
        assert verified_is_current(DIGEST, DIGEST) is True

    def test_two_UNKNOWNS_are_never_read_as_agreement(self):
        assert verified_is_current(None, None) is False

    @pytest.mark.parametrize(
        "stored,current",
        [(DIGEST, OTHER_DIGEST), (None, DIGEST), (DIGEST, None), ("", DIGEST), (DIGEST, "")],
    )
    def test_everything_else_closes_the_gate(self, stored, current):
        assert verified_is_current(stored, current) is False


class TestOverlayStaleness:
    def _authored(self, *, version="1.0.0", digest=DIGEST) -> AuthoredWith:
        return AuthoredWith(app_version=version, base="myedbc", base_digest=digest)

    def test_unknown_provenance_is_NOT_stale(self):
        assert overlay_staleness(None, running_version="2.0.0", current_base_digest=OTHER_DIGEST) == StalenessFact()

    def test_neither_flag_when_everything_matches(self):
        fact = overlay_staleness(self._authored(version="2.0.0"), running_version="2.0.0", current_base_digest=DIGEST)
        assert fact == StalenessFact(version_differs=False, base_changed=False)

    def test_both_flags_when_the_build_and_the_base_moved(self):
        fact = overlay_staleness(self._authored(), running_version="2.0.0", current_base_digest=OTHER_DIGEST)
        assert fact == StalenessFact(version_differs=True, base_changed=True)

    def test_only_the_version_moved(self):
        fact = overlay_staleness(self._authored(), running_version="2.0.0", current_base_digest=DIGEST)
        assert fact == StalenessFact(version_differs=True, base_changed=False)

    def test_only_the_base_moved(self):
        fact = overlay_staleness(self._authored(), running_version="1.0.0", current_base_digest=OTHER_DIGEST)
        assert fact == StalenessFact(version_differs=False, base_changed=True)

    def test_an_unloadable_base_is_not_reported_as_a_change(self):
        fact = overlay_staleness(self._authored(), running_version="1.0.0", current_base_digest=None)
        assert fact.base_changed is False

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_recorded_field_is_unknown_not_stale(self, blank):
        fact = overlay_staleness(
            AuthoredWith(app_version=blank.strip(), base="myedbc", base_digest=blank.strip()),
            running_version="2.0.0",
            current_base_digest=OTHER_DIGEST,
        )
        assert fact == StalenessFact()


# ---------------------------------------------------------------------------
# Acceptance (4), at this layer: staleness really re-closes the gate
# ---------------------------------------------------------------------------


class TestTheStoredFactAgainstTheRealConfig:
    """The two halves joined: what was recorded vs what ``load_config`` sees TODAY.

    The view's FILES step asks exactly this question, so it is pinned end to end here
    (over the real authoring layer and the real loader) rather than only over literals.
    """

    def _write_sd93(self) -> Path:
        return write_overlay(
            OverlaySpec(
                sd_number=93,
                district_name="SD93 - Gate Test",
                district_domains=("sd93.bc.ca",),
                base="myedbc",
            ),
            overwrite=True,
        )

    def test_an_untouched_config_stays_current(self):
        """The positive twin — the gate does not re-fire for no reason."""
        self._write_sd93()
        cfg = _cfg(creator_verified={"sd93custom": current_digest("sd93custom")})
        assert verified_is_current(stored_verified_digest(cfg, "sd93custom"), current_digest("sd93custom")) is True

    def test_a_hand_edited_overlay_re_closes_the_gate(self):
        path = self._write_sd93()
        cfg = _cfg(creator_verified={"sd93custom": current_digest("sd93custom")})
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["district_domains"] = ["sd93.bc.ca", "someone.ca"]
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        assert verified_is_current(stored_verified_digest(cfg, "sd93custom"), current_digest("sd93custom")) is False

    def test_a_malformed_stored_entry_re_closes_the_gate(self):
        self._write_sd93()
        cfg = _cfg(creator_verified={"sd93custom": "not-a-digest"})
        assert stored_verified_digest(cfg, "sd93custom") is None
        assert verified_is_current(stored_verified_digest(cfg, "sd93custom"), current_digest("sd93custom")) is False

    def test_an_unloadable_config_re_closes_the_gate_even_with_a_matching_record(self):
        path = self._write_sd93()
        digest = current_digest("sd93custom")
        cfg = _cfg(creator_verified={"sd93custom": digest})
        path.write_text("global_config: [broken\n", encoding="utf-8")
        assert current_digest("sd93custom") is None
        assert verified_is_current(stored_verified_digest(cfg, "sd93custom"), current_digest("sd93custom")) is False


# ---------------------------------------------------------------------------
# The filename form (plan 0044 S4): slots, rows, the resume inverse, tiering
# ---------------------------------------------------------------------------
#: The rows each starting point earns, in the order they must render. Hand-written on
#: purpose: a tuple derived from the same walk the code uses would pass whatever the walk
#: did, and this is the assertion that the ORDER is a decision (``CREATOR_ENTITIES``, then
#: first-seen per file) rather than ``advisory_expected_files``' ``list(set)``.
SLOT_ORDER: dict[str, tuple[str, ...]] = {
    "myedbc": (
        "StudentDemographicInformation.txt",
        "StaffInformationEnhanced.txt",
        "EmergencyContactInformation.txt",
        "StudentSchedule.txt",
        "CourseInformation.txt",
        "ClassInformationEnh.txt",
    ),
    "mbp_all": (
        "StudentDemographicInformation.txt",
        "StaffInformationEnhanced.txt",
        "EmergencyContactInformation.txt",
        "StudentSchedule.txt",
        "CourseInformation.txt",
        "ClassInformationEnh.txt",
        "StudentCourseHistory.txt",
        "StudentCourseSelection.txt",
    ),
    "mbp_core": (
        "StudentDemographicInformation.txt",
        "CourseInformation.txt",
        "StudentCourseHistory.txt",
        "StudentCourseSelection.txt",
    ),
    "mbponly": (
        "CourseInformation.txt",
        "StudentCourseHistory.txt",
        "StudentCourseSelection.txt",
    ),
}

#: The four renames the SD74 snapshot extract really needs (the same table
#: ``tests/test_config_authoring.py`` and ``tests/test_ui_flet_creator_flow.py`` use).
SD74_RENAMES = {
    "StaffInformationEnhanced.txt": "StaffInformation.txt",
    "EmergencyContactInformation.txt": "ParentInformation.txt",
    "StudentSchedule.txt": "studentcourseselection.txt",
    "ClassInformationEnh.txt": "ClassInfoEnhanced.txt",
}


def _slots_for(base: str):
    from src.etl.pipeline import advisory_expected_files

    resolved = load_config(base)
    return distinct_source_files(resolved, expected=advisory_expected_files(resolved))


class TestDistinctSourceFiles:
    @pytest.mark.parametrize("base", ALLOWED_BASES)
    def test_one_slot_per_file_the_config_reads_in_a_pinned_order(self, base):
        assert tuple(slot.original for slot in _slots_for(base)) == SLOT_ORDER[base]

    @pytest.mark.parametrize("base", ALLOWED_BASES)
    def test_the_order_is_the_walk_never_the_expected_list(self, base):
        """The twin for the order pin: ``advisory_expected_files`` returns ``list(set)``,
        whose order moves with ``PYTHONHASHSEED``. A shuffled ``expected`` may not move a
        single row — otherwise S4 would have inherited S3's instability."""
        resolved = load_config(base)
        shuffled = sorted(SLOT_ORDER[base], reverse=True)

        slots = distinct_source_files(resolved, expected=shuffled)

        assert tuple(slot.original for slot in slots) == SLOT_ORDER[base]

    def test_references_name_the_active_entity_role_sites_in_walk_order(self):
        by_name = {slot.original: slot for slot in _slots_for("myedbc")}

        assert by_name["StudentSchedule.txt"].references == (
            ("Classes", "student_schedule"),
            ("Enrollments", "student_schedule"),
        )
        # ONE row fixes three roles — the whole reason the map is keyed by FILE.
        assert by_name["StudentDemographicInformation.txt"].references == (
            ("Students", "student_demographic"),
            ("Classes", "student_demographic"),
            ("Enrollments", "student_demographic"),
        )

    @pytest.mark.parametrize("base", ["myedbc", "mbp_all"])
    def test_the_school_year_file_is_flagged_where_an_active_entity_reads_it(self, base):
        by_name = {slot.original: slot for slot in _slots_for(base)}

        assert by_name["StudentSchedule.txt"].names_school_year is True
        assert by_name["CourseInformation.txt"].names_school_year is False

    @pytest.mark.parametrize("base", ["mbp_core", "mbponly"])
    def test_a_school_year_file_no_active_entity_reads_gets_no_row_at_all(self, base):
        """``global_config.school_year_sources`` still names ``StudentSchedule.txt`` on
        both course-only tiers, but no ACTIVE entity reads it — ``extract_required_files``
        never loads it, so a row would offer a name that changes nothing."""
        resolved = load_config(base)
        assert "student_schedule" in resolved.global_config.school_year_sources

        assert "StudentSchedule.txt" not in {slot.original for slot in _slots_for(base)}

    def test_a_file_outside_expected_is_dropped(self):
        """The positive twin for the ``expected`` filter: it really is a filter."""
        resolved = load_config("myedbc")

        slots = distinct_source_files(resolved, expected=["StudentSchedule.txt"])

        assert tuple(slot.original for slot in slots) == ("StudentSchedule.txt",)


class TestFileLabels:
    def test_every_distinct_file_of_every_base_has_a_plain_language_name(self):
        every = {slot.original for base in ALLOWED_BASES for slot in _slots_for(base)}

        assert every, "the slot walk found nothing — the sweep below would be vacuous"
        assert every <= set(FILE_LABELS), f"no FILE_LABELS row for {sorted(every - set(FILE_LABELS))}"

    def test_the_labels_are_the_slots_labels(self):
        for slot in _slots_for("mbp_all"):
            assert slot.label == FILE_LABELS[slot.original]

    def test_an_unlabelled_filename_falls_back_to_itself(self):
        assert file_label("SomethingNew.txt") == "SomethingNew.txt"


class TestWithRename:
    @pytest.mark.parametrize("typed", ["", "   ", "StudentSchedule.txt", "  StudentSchedule.txt  "])
    def test_blank_or_the_standard_name_drops_the_entry(self, typed):
        """A "rename" to the standard name is not one, and the emission must stay minimal —
        an entry naming the base's own file would emit an override that only forks it."""
        form = _form().with_rename("StudentSchedule.txt", "sched.txt")
        assert form.renames == {"StudentSchedule.txt": "sched.txt"}

        assert form.with_rename("StudentSchedule.txt", typed).renames == {}

    def test_a_typed_name_is_stripped_and_kept(self):
        form = _form().with_rename("StudentSchedule.txt", "  studentcourseselection.txt \t")

        assert form.renames == {"StudentSchedule.txt": "studentcourseselection.txt"}

    @pytest.mark.parametrize(
        "bad",
        [
            "../StudentSchedule.txt",
            "sub/StudentSchedule.txt",
            "sub\\StudentSchedule.txt",
            "C:sched.txt",
            "sched.txt:stream",
            "sched<1>.txt",
            "sched\nule.txt",
            "CON.txt",
            "nul",
            "s" * 256,
        ],
    )
    def test_every_shape_the_filename_boundary_refuses_is_refused_here(self, bad):
        with pytest.raises(ValueError):
            _form().with_rename("StudentSchedule.txt", bad)

    def test_a_blank_original_raises(self):
        with pytest.raises(ValueError, match="base filename"):
            _form().with_rename("  ", "sched.txt")

    def test_an_unknown_original_is_not_rejected_here(self):
        """This module holds no resolved base; ``authoring._build_renames`` fails loud at
        write time, which is the one layer that can see what the base references."""
        assert _form().with_rename("NotAFile.txt", "sched.txt").renames == {"NotAFile.txt": "sched.txt"}

    def test_a_direct_construction_cannot_bypass_the_boundary(self):
        with pytest.raises(ValueError):
            _form(renames={"StudentSchedule.txt": "../escape.txt"})

    def test_a_direct_construction_with_a_blank_key_is_refused(self):
        with pytest.raises(ValueError, match="renames keys"):
            _form(renames={"": "sched.txt"})

    def test_the_renames_ride_the_overlay_spec(self):
        spec = _form().with_rename("StudentSchedule.txt", "sched.txt").to_overlay_spec()

        assert dict(spec.source_file_renames) == {"StudentSchedule.txt": "sched.txt"}

    def test_an_explicit_keyword_still_wins_including_an_empty_map(self):
        form = _form().with_rename("StudentSchedule.txt", "sched.txt")

        assert dict(
            form.to_overlay_spec(source_file_renames={"CourseInformation.txt": "c.txt"}).source_file_renames
        ) == {"CourseInformation.txt": "c.txt"}
        assert dict(form.to_overlay_spec(source_file_renames={}).source_file_renames) == {}


class TestFileFormRows:
    def test_the_effective_name_is_the_renamed_one_else_the_standard_one(self):
        slots = _slots_for("myedbc")

        rows = file_form_rows(slots, renames={"StudentSchedule.txt": "sched.txt"}, present=[])

        by_name = {row.slot.original: row for row in rows}
        assert by_name["StudentSchedule.txt"].effective == "sched.txt"
        assert by_name["CourseInformation.txt"].effective == "CourseInformation.txt"

    @pytest.mark.parametrize(
        ("renamed", "on_disk"),
        [
            ("studentcourseselection.txt", "StudentCourseSelection.txt"),
            ("StudentCourseSelection.txt", "studentcourseselection.txt"),
        ],
    )
    def test_presence_folds_case_in_both_directions(self, renamed, on_disk):
        """The extractor loads it either way, so a chip reading "missing" would be a false
        alarm on the very card an admin uses to decide whether their extract is complete."""
        rows = file_form_rows(_slots_for("myedbc"), renames={"StudentSchedule.txt": renamed}, present=[on_disk])

        assert {row.slot.original: row.present for row in rows}["StudentSchedule.txt"] is True

    def test_the_twin_a_genuinely_absent_file_reads_absent(self):
        rows = file_form_rows(
            _slots_for("myedbc"), renames={"StudentSchedule.txt": "sched.txt"}, present=["Something.txt"]
        )

        assert all(row.present is False for row in rows)

    def test_the_slot_is_carried_whole(self):
        """Composition, not duplication — a row cannot disagree with the file it describes."""
        slots = _slots_for("myedbc")

        rows = file_form_rows(slots, renames={}, present=[])

        assert [row.slot for row in rows] == list(slots)


class TestRenamesFromResolved:
    def _resolved_pair(self, renames: dict[str, str]):
        write_overlay(
            OverlaySpec(
                sd_number=93,
                district_name="SD93 - Resume Test",
                district_domains=("sd93.bc.ca",),
                base="myedbc",
                source_file_renames=renames,
            ),
            overwrite=True,
        )
        return load_config("myedbc"), load_config("sd93custom")

    def test_a_written_map_round_trips_out_of_the_resolved_config(self):
        base, current = self._resolved_pair(SD74_RENAMES)

        assert renames_from_resolved(base, current) == SD74_RENAMES

    def test_the_twin_an_unrenamed_config_answers_nothing(self):
        base, current = self._resolved_pair({})

        assert renames_from_resolved(base, current) == {}

    def test_a_config_compared_with_itself_answers_nothing(self):
        base = load_config("myedbc")

        assert renames_from_resolved(base, base) == {}

    def test_a_hand_edited_divergence_collapses_to_the_first_seen_name(self):
        """The honest repair: the rows on screen and the map the next Save writes are ONE
        value, so a config whose two sites disagree reads as its first-seen name (and the
        next Save writes that everywhere) rather than silently keeping both."""
        from src.config.authoring import overlay_path

        base, _current = self._resolved_pair({"StudentSchedule.txt": "sched.txt"})
        target = overlay_path("sd93custom")
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        raw["mappings"]["Enrollments"]["source_files"]["student_schedule"] = "other.txt"
        target.write_text(yaml.safe_dump(raw), encoding="utf-8")

        found = renames_from_resolved(base, load_config("sd93custom"))

        assert found == {"StudentSchedule.txt": "sched.txt"}, "Classes is the first site in the walk"


class TestFilesPrimaryAction:
    @pytest.mark.parametrize(
        ("unsaved", "passed", "already", "expected"),
        [
            (True, True, True, "save"),
            (True, True, False, "save"),
            (True, False, True, "save"),
            (True, False, False, "save"),
            (False, True, True, "none"),
            (False, False, True, "none"),
            (False, True, False, "confirm"),
            (False, False, False, "run"),
        ],
    )
    def test_all_eight_states(self, unsaved, passed, already, expected):
        assert files_primary_action(unsaved=unsaved, passed=passed, already=already) == expected

    def test_unsaved_always_wins(self):
        """A test conversion run against names the config on disk lacks reports on the
        WRONG files, so the run button may never be the primary while a change is pending."""
        for passed in (True, False):
            for already in (True, False):
                assert files_primary_action(unsaved=True, passed=passed, already=already) == "save"

    def test_at_most_one_action_is_ever_the_primary(self):
        """The property the "exactly ONE filled primary" rule rests on: the answer is a
        single value in every state, and only the active district's state has none."""
        answers = {
            files_primary_action(unsaved=u, passed=p, already=a)
            for u in (True, False)
            for p in (True, False)
            for a in (True, False)
        }
        assert answers == {"save", "run", "confirm", "none"}
