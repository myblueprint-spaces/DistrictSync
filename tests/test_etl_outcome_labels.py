"""Config-declared labels on an outcome (plan 0053 S7, owner decision D4).

``docs/developer/failure-policy.md`` §8 (P9): a file or column may be NAMED in the record and in
the copy only when it is config-DECLARED — a member of the resolved config's own vocabulary — and
label-shaped (printable, trimmed, at most 120 characters, no newline, neither email- nor
path-shaped). Every refusal below has its accepting twin, so no absence is vacuous:

* :func:`safe_label` — membership twins, shape twins, the substring trap;
* :func:`derive_labels` — the single-file rule (one own file named; five never; a cross-file
  entity never) and "a file only beside a column";
* :class:`EntityOutcome` — labels only on ``missing_source_column``, a file only with a column;
* :func:`apply_labels` / :meth:`OutcomeLedger.record_failure` — FAILED labels come from the
  error's own config-spelling columns (never ``str(exc)``), EMPTY labels from ``missing_mapped``;
* the record round trip — labels written only when non-empty (every other entry byte-identical),
  and the TOTAL reader drops junk without dropping the entry;
* :func:`preflight.label_vocabulary_by_entity` over the real bundled configs.
"""

from __future__ import annotations

import json

import pytest

from src.config.loader import available_configs, load_config
from src.etl.errors import GuardKind, SourceSchemaError
from src.etl.outcomes import (
    FILE_LABEL_KEY,
    LABELS_KEY,
    MAX_LABEL_LENGTH,
    OUTCOMES_RECORD_KEY,
    EntityOutcome,
    LabelVocabulary,
    OutcomeKind,
    OutcomeLedger,
    OutcomeReason,
    apply_labels,
    derive_labels,
    outcomes_from_record,
    outcomes_to_record,
    safe_label,
)
from src.etl.preflight import ObservationScope, label_vocabulary_by_entity, observation_scope

_GUARDIAN = "Parent Auth / Guardian"
_EMAIL = "Email Address"
_CONTACTS = "EmergencyContactInformation.txt"
_SENTINEL = "SENTINEL_PII C:\\secret"

# Family's shape in the Unity config: one own source file, columns in config spelling.
_FAMILY_VOCAB = LabelVocabulary(
    columns=frozenset({"First Name", "Last Name", _EMAIL, "Student Number", _GUARDIAN}),
    files=(_CONTACTS,),
    reads_own_files=True,
)
# Classes' shape: five configured source files.
_CLASSES_VOCAB = LabelVocabulary(
    columns=frozenset({"Grade", "Course Title"}),
    files=(
        "StudentSchedule.txt",
        "CourseInformation.txt",
        "StaffInformationEnhanced.txt",
        "StudentDemographicInformation.txt",
        "ClassInformationEnh.txt",
    ),
    reads_own_files=True,
)


def _schema_error(entity: str, *columns: str) -> SourceSchemaError:
    return SourceSchemaError(_SENTINEL, entity=entity, columns=columns, guard=GuardKind.PII_SCOPE)


# --------------------------------------------------------------------------- #
# safe_label — the ONE membership + shape check                                 #
# --------------------------------------------------------------------------- #
class TestSafeLabel:
    def test_a_declared_label_is_kept(self) -> None:
        assert safe_label(_GUARDIAN, vocabulary=_FAMILY_VOCAB.columns) == _GUARDIAN

    @pytest.mark.parametrize(
        "text",
        [
            "parent auth / guardian",  # an OBSERVED (lower-cased) header, not the config's spelling
            "Guardian",  # a fragment of a declared name
            "Zzsentinelpupilname",  # a header the config never declared
        ],
    )
    def test_the_twin_anything_not_declared_is_dropped(self, text: str) -> None:
        assert safe_label(text, vocabulary=_FAMILY_VOCAB.columns) is None

    @pytest.mark.parametrize(
        "text",
        [
            "Parent\nGuardian",  # a newline
            "Parent\tGuardian",  # any other control character
            " Parent Auth / Guardian",  # untrimmed
            "",
            "x" * (MAX_LABEL_LENGTH + 1),
            "guardian@example.org",  # email-shaped
            "C:\\Users\\admin\\contacts.txt",  # path-shaped (backslash / drive letter)
            "C:/exports/contacts.txt",
            "/srv/exports/contacts.txt",
            "~/contacts.txt",
            "https://example.org/contacts",
            "folder\\contacts.txt",
        ],
    )
    def test_a_badly_shaped_label_is_dropped_EVEN_WHEN_the_config_declares_it(self, text: str) -> None:
        assert safe_label(text, vocabulary=frozenset({text})) is None

    def test_the_twin_the_length_cap_is_inclusive_and_a_mid_name_slash_is_legal(self) -> None:
        longest = "x" * MAX_LABEL_LENGTH
        assert safe_label(longest, vocabulary=frozenset({longest})) == longest
        assert safe_label(_GUARDIAN, vocabulary=frozenset({_GUARDIAN})) == _GUARDIAN

    @pytest.mark.parametrize("text", [None, 42, ("Email Address",), b"Email Address"])
    def test_a_non_str_is_dropped(self, text: object) -> None:
        assert safe_label(text, vocabulary=frozenset({"Email Address"})) is None

    def test_a_str_vocabulary_is_refused_since_in_would_match_a_substring(self) -> None:
        with pytest.raises(TypeError, match="not a str"):
            safe_label("Guardian", vocabulary=_GUARDIAN)
        # The trap it closes: substring membership would have let a fragment through.
        assert "Guardian" in _GUARDIAN


# --------------------------------------------------------------------------- #
# derive_labels — the single-file rule                                          #
# --------------------------------------------------------------------------- #
class TestSingleFileRule:
    def test_one_own_source_file_is_named_beside_the_column(self) -> None:
        assert derive_labels([_GUARDIAN], _FAMILY_VOCAB) == (_CONTACTS, (_GUARDIAN,))

    def test_the_twin_a_five_file_entity_never_names_a_file(self) -> None:
        assert derive_labels(["Grade"], _CLASSES_VOCAB) == ("", ("Grade",))

    def test_the_twin_a_cross_file_entity_never_names_its_file(self) -> None:
        cross = LabelVocabulary(columns=frozenset({"Teacher Id"}), files=("OnlyOne.txt",), reads_own_files=False)
        assert derive_labels(["Teacher Id"], cross) == ("", ("Teacher Id",))

    def test_no_surviving_column_names_nothing_not_even_the_file(self) -> None:
        # The column may live in ANOTHER export (a source_columns read); the entity's own file
        # must not be named beside a column the vocabulary does not know.
        assert derive_labels(["Home School Number"], _FAMILY_VOCAB) == ("", ())
        assert derive_labels([], _FAMILY_VOCAB) == ("", ())

    def test_an_unusable_file_name_is_dropped_and_the_column_kept(self) -> None:
        vocab = LabelVocabulary(columns=frozenset({_EMAIL}), files=("C:\\exports\\x.txt",), reads_own_files=True)
        assert derive_labels([_EMAIL], vocab) == ("", (_EMAIL,))

    @pytest.mark.parametrize(
        "filename",
        ["exports/contacts.txt", "../x/contacts.txt", "C:x.txt", "contacts.txt:stream", ".", ".."],
    )
    def test_a_declared_file_that_is_not_a_bare_filename_is_never_named(self, filename: str) -> None:
        # `source_files` is unvalidated: a relative path is DECLARED here, so membership alone
        # would pass it. In a file name `/` is always a separator (S7-PQ-1). The twin is
        # test_one_own_source_file_is_named_beside_the_column.
        vocab = LabelVocabulary(columns=frozenset({_EMAIL}), files=(filename,), reads_own_files=True)
        assert derive_labels([_EMAIL], vocab) == ("", (_EMAIL,))

    def test_duplicates_are_dropped_and_order_kept_undeclared_ones_filtered(self) -> None:
        assert derive_labels([_GUARDIAN, "nope", _EMAIL, _GUARDIAN], _FAMILY_VOCAB) == (
            _CONTACTS,
            (_GUARDIAN, _EMAIL),
        )


# --------------------------------------------------------------------------- #
# EntityOutcome — labels only where a producer puts them                        #
# --------------------------------------------------------------------------- #
def _labelled(kind: OutcomeKind = OutcomeKind.FAILED, **kw: object) -> EntityOutcome:
    fields: dict[str, object] = {"labels": (_GUARDIAN,), "file_label": _CONTACTS, **kw}
    reason = fields.pop("reason", OutcomeReason.MISSING_SOURCE_COLUMN)
    return EntityOutcome("Family", kind, reason, 0, (), **fields)  # type: ignore[arg-type]


class TestEntityOutcomeLabelInvariants:
    @pytest.mark.parametrize("kind", [OutcomeKind.FAILED, OutcomeKind.EMPTY])
    def test_a_missing_source_column_outcome_may_carry_labels(self, kind: OutcomeKind) -> None:
        outcome = _labelled(kind)
        assert outcome.labels == (_GUARDIAN,) and outcome.file_label == _CONTACTS

    def test_the_twin_labels_on_any_other_reason_are_refused(self) -> None:
        with pytest.raises(ValueError, match="only a missing_source_column"):
            _labelled(reason=OutcomeReason.TRANSFORM_ERROR)

    def test_a_file_without_a_column_is_refused(self) -> None:
        with pytest.raises(ValueError, match="only beside the column"):
            _labelled(labels=())

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("labels", ("Parent\nGuardian",)),
            ("labels", ("x" * (MAX_LABEL_LENGTH + 1),)),
            ("labels", ("guardian@example.org",)),
            ("labels", (_GUARDIAN, _GUARDIAN)),
            ("file_label", "C:\\exports\\x.txt"),
            ("file_label", "x" * (MAX_LABEL_LENGTH + 1)),
            ("file_label", "exports/contacts.txt"),
            ("file_label", "C:x.txt"),
        ],
    )
    def test_an_unshaped_or_duplicated_label_is_refused(self, field: str, value: object) -> None:
        with pytest.raises(ValueError):
            _labelled(**{field: value})

    @pytest.mark.parametrize(("field", "value"), [("labels", [_GUARDIAN]), ("file_label", None)])
    def test_a_wrongly_typed_label_is_refused(self, field: str, value: object) -> None:
        with pytest.raises(TypeError):
            _labelled(**{field: value})

    def test_the_default_is_no_labels(self) -> None:
        outcome = EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN)
        assert outcome.labels == () and outcome.file_label == ""


# --------------------------------------------------------------------------- #
# apply_labels — the ONE producer                                               #
# --------------------------------------------------------------------------- #
class TestApplyLabels:
    def test_failed_takes_the_errors_columns_never_the_observation(self) -> None:
        failed = EntityOutcome(
            "Family", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN, 0, (_EMAIL, _GUARDIAN)
        )
        labelled = apply_labels(failed, _FAMILY_VOCAB, error_columns=(_GUARDIAN,))
        assert labelled.labels == (_GUARDIAN,) and labelled.file_label == _CONTACTS
        assert labelled.missing_mapped == (_EMAIL, _GUARDIAN)  # the observation is untouched

    def test_empty_takes_missing_mapped(self) -> None:
        empty = EntityOutcome("Family", OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN, 0, (_EMAIL,))
        labelled = apply_labels(empty, _FAMILY_VOCAB, error_columns=("ignored",))
        assert labelled.labels == (_EMAIL,) and labelled.file_label == _CONTACTS

    @pytest.mark.parametrize(
        "outcome",
        [
            EntityOutcome.failed("Family", OutcomeReason.TRANSFORM_ERROR),
            EntityOutcome("Family", OutcomeKind.BUILT, OutcomeReason.NONE, 3, (_EMAIL,)),
            EntityOutcome("Family", OutcomeKind.EMPTY, OutcomeReason.NO_ROWS_AFTER_TRANSFORM, 0),
        ],
    )
    def test_the_twin_any_other_reason_is_never_labelled(self, outcome: EntityOutcome) -> None:
        assert apply_labels(outcome, _FAMILY_VOCAB, error_columns=(_EMAIL,)) is outcome

    def test_no_vocabulary_names_nothing(self) -> None:
        failed = EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN)
        assert apply_labels(failed, None, error_columns=(_GUARDIAN,)) is failed

    def test_an_outcome_already_labelled_is_never_relabelled(self) -> None:
        labelled = EntityOutcome(
            "Family", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN, 0, (), (_GUARDIAN,), _CONTACTS
        )
        assert apply_labels(labelled, _FAMILY_VOCAB, error_columns=(_EMAIL,)) is labelled

    def test_nothing_surviving_leaves_the_outcome_unchanged(self) -> None:
        failed = EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN)
        assert apply_labels(failed, _FAMILY_VOCAB, error_columns=("undeclared",)) is failed


# --------------------------------------------------------------------------- #
# The ledger: record_failure / note_label_vocabulary                            #
# --------------------------------------------------------------------------- #
class TestLedgerLabels:
    def test_a_schema_error_on_a_single_file_entity_names_the_file_and_the_column(self) -> None:
        ledger = OutcomeLedger(["Family"])
        ledger.note_label_vocabulary("Family", _FAMILY_VOCAB)
        reason = ledger.record_failure("Family", _schema_error("Family", _GUARDIAN))
        assert reason is OutcomeReason.MISSING_SOURCE_COLUMN
        (outcome,) = ledger.complete()
        assert (outcome.file_label, outcome.labels) == (_CONTACTS, (_GUARDIAN,))
        # The exception's MESSAGE (a sentinel here) never reaches the outcome.
        assert "SENTINEL" not in json.dumps(outcomes_to_record(ledger.complete()))

    def test_the_twin_without_a_vocabulary_nothing_is_named(self) -> None:
        ledger = OutcomeLedger(["Family"])
        ledger.record_failure("Family", _schema_error("Family", _GUARDIAN))
        assert ledger.complete() == (EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN),)

    def test_a_five_file_entity_names_the_column_only(self) -> None:
        ledger = OutcomeLedger(["Classes"])
        ledger.note_label_vocabulary("Classes", _CLASSES_VOCAB)
        ledger.record_failure("Classes", _schema_error("Classes", "Grade"))
        (outcome,) = ledger.complete()
        assert (outcome.file_label, outcome.labels) == ("", ("Grade",))

    def test_an_error_attributed_to_another_entity_names_nothing(self) -> None:
        ledger = OutcomeLedger(["Family"])
        ledger.note_label_vocabulary("Family", _FAMILY_VOCAB)
        ledger.record_failure("Family", _schema_error("Students", _GUARDIAN))
        (outcome,) = ledger.complete()
        assert outcome.labels == () and outcome.file_label == ""

    def test_an_untyped_error_is_a_transform_error_and_names_nothing(self) -> None:
        ledger = OutcomeLedger(["Family"])
        ledger.note_label_vocabulary("Family", _FAMILY_VOCAB)
        assert ledger.record_failure("Family", ValueError(_GUARDIAN)) is OutcomeReason.TRANSFORM_ERROR
        assert ledger.complete() == (EntityOutcome.failed("Family", OutcomeReason.TRANSFORM_ERROR),)

    def test_an_empty_outcome_is_labelled_from_the_held_observation(self) -> None:
        ledger = OutcomeLedger(["Family"])
        ledger.note_label_vocabulary("Family", _FAMILY_VOCAB)
        ledger.note_missing_mapped("Family", [_EMAIL])
        ledger.record(EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM))
        (outcome,) = ledger.complete()
        assert outcome.reason is OutcomeReason.MISSING_SOURCE_COLUMN
        assert (outcome.file_label, outcome.labels) == (_CONTACTS, (_EMAIL,))

    def test_note_label_vocabulary_refuses_caller_bugs(self) -> None:
        ledger = OutcomeLedger(["Family", "Students"])
        with pytest.raises(ValueError, match="not an entity"):
            ledger.note_label_vocabulary("Classes", _FAMILY_VOCAB)
        with pytest.raises(TypeError, match="LabelVocabulary"):
            ledger.note_label_vocabulary("Family", {"columns": [_EMAIL]})  # type: ignore[arg-type]
        ledger.note_label_vocabulary("Family", _FAMILY_VOCAB)
        with pytest.raises(ValueError, match="already has a label vocabulary"):
            ledger.note_label_vocabulary("Family", _FAMILY_VOCAB)
        ledger.record(EntityOutcome.built("Students", 3))
        with pytest.raises(ValueError, match="already has an outcome"):
            ledger.note_label_vocabulary("Students", _FAMILY_VOCAB)

    @pytest.mark.parametrize(
        "vocabulary",
        [
            LabelVocabulary(columns=_EMAIL, files=(_CONTACTS,), reads_own_files=True),  # type: ignore[arg-type]
            LabelVocabulary(columns=None, files=(_CONTACTS,), reads_own_files=True),  # type: ignore[arg-type]
            LabelVocabulary(columns=frozenset({_EMAIL, 7}), files=(_CONTACTS,), reads_own_files=True),  # type: ignore[arg-type]
            LabelVocabulary(columns=frozenset({_EMAIL}), files=_CONTACTS, reads_own_files=True),  # type: ignore[arg-type]
            LabelVocabulary(columns=frozenset({_EMAIL}), files=(_CONTACTS, None), reads_own_files=True),  # type: ignore[arg-type]
            LabelVocabulary(columns=frozenset({_EMAIL}), files=(_CONTACTS,), reads_own_files="yes"),  # type: ignore[arg-type]
        ],
    )
    def test_a_malformed_vocabulary_is_refused_at_note_time_and_costs_only_the_labels(
        self, vocabulary: LabelVocabulary
    ) -> None:
        # Labelling runs inside record()/record_failure() — the delivery path. A malformed
        # vocabulary must fail HERE, where the pipeline's guard turns it into "no labels",
        # never later from record() (S7-BEH-1).
        ledger = OutcomeLedger(["Family", "Students"])
        with pytest.raises(TypeError, match="vocabulary"):
            ledger.note_label_vocabulary("Family", vocabulary)
        with pytest.raises(TypeError, match="vocabulary"):
            ledger.note_label_vocabulary("Students", vocabulary)
        ledger.note_missing_mapped("Family", [_EMAIL])
        ledger.record(EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM))
        ledger.record_failure("Students", _schema_error("Students", _EMAIL))
        family, students = ledger.complete()
        assert family.reason is OutcomeReason.MISSING_SOURCE_COLUMN and family.labels == ()
        assert students.reason is OutcomeReason.MISSING_SOURCE_COLUMN and students.labels == ()

    def test_the_twin_a_well_formed_vocabulary_still_labels(self) -> None:
        ledger = OutcomeLedger(["Family"])
        ledger.note_label_vocabulary("Family", LabelVocabulary(frozenset({_EMAIL}), (_CONTACTS,), True))
        ledger.note_missing_mapped("Family", [_EMAIL])
        ledger.record(EntityOutcome.empty("Family", OutcomeReason.NO_ROWS_AFTER_TRANSFORM))
        (family,) = ledger.complete()
        assert (family.file_label, family.labels) == (_CONTACTS, (_EMAIL,))

    def test_record_failure_refuses_like_record(self) -> None:
        ledger = OutcomeLedger(["Family"])
        with pytest.raises(ValueError, match="not an entity"):
            ledger.record_failure("Classes", ValueError("x"))
        ledger.record_failure("Family", ValueError("x"))
        with pytest.raises(ValueError, match="already has an outcome"):
            ledger.record_failure("Family", ValueError("x"))


# --------------------------------------------------------------------------- #
# The record: written only when non-empty; the reader is TOTAL                  #
# --------------------------------------------------------------------------- #
class TestLabelsRoundTrip:
    def test_labels_round_trip(self) -> None:
        column_only = EntityOutcome(
            "CourseInfo", OutcomeKind.EMPTY, OutcomeReason.MISSING_SOURCE_COLUMN, 0, ("Title",), ("Title",)
        )
        outcomes = (EntityOutcome.built("Students", 3), _labelled(), column_only)
        record = {OUTCOMES_RECORD_KEY: json.loads(json.dumps(outcomes_to_record(outcomes)))}
        assert outcomes_from_record(record) == outcomes

    def test_an_unlabelled_entry_is_byte_identical_to_before(self) -> None:
        entry = outcomes_to_record([EntityOutcome.failed("Family", OutcomeReason.MISSING_SOURCE_COLUMN)])["Family"]
        assert entry == {"kind": "failed", "reason": "missing_source_column", "rows": 0}

    def test_the_twin_a_labelled_entry_carries_both_keys_and_a_column_only_one_carries_one(self) -> None:
        entry = outcomes_to_record([_labelled()])["Family"]
        assert entry[LABELS_KEY] == [_GUARDIAN] and entry[FILE_LABEL_KEY] == _CONTACTS
        column_only = outcomes_to_record([_labelled(file_label="")])["Family"]
        assert column_only[LABELS_KEY] == [_GUARDIAN] and FILE_LABEL_KEY not in column_only

    @staticmethod
    def _read(**extra: object) -> EntityOutcome:
        entry = {"kind": "failed", "reason": "missing_source_column", "rows": 0, **extra}
        (outcome,) = outcomes_from_record({OUTCOMES_RECORD_KEY: {"Family": entry}})
        return outcome

    @pytest.mark.parametrize(
        "labels",
        [
            "Parent Auth / Guardian",  # a bare str, not a list
            ["Parent\nGuardian"],
            ["x" * (MAX_LABEL_LENGTH + 1)],
            ["guardian@example.org"],
            ["C:\\secret\\file.txt"],
            [_GUARDIAN, _GUARDIAN],
            [42],
        ],
    )
    def test_junk_labels_read_as_none_without_costing_the_entry(self, labels: object) -> None:
        outcome = self._read(labels=labels, file_label=_CONTACTS)
        assert (outcome.kind, outcome.reason) == (OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN)
        assert outcome.labels == () and outcome.file_label == ""

    def test_the_twin_good_labels_are_read(self) -> None:
        outcome = self._read(labels=[_GUARDIAN], file_label=_CONTACTS)
        assert (outcome.labels, outcome.file_label) == ((_GUARDIAN,), _CONTACTS)

    @pytest.mark.parametrize(
        "file_label", ["C:\\secret", "a\nb", 7, "x" * (MAX_LABEL_LENGTH + 1), "exports/contacts.txt", ".."]
    )
    def test_a_junk_file_label_alone_is_dropped_and_the_columns_kept(self, file_label: object) -> None:
        outcome = self._read(labels=[_GUARDIAN], file_label=file_label)
        assert (outcome.labels, outcome.file_label) == ((_GUARDIAN,), "")

    def test_a_file_label_without_columns_is_dropped(self) -> None:
        assert self._read(file_label=_CONTACTS).file_label == ""

    def test_labels_on_a_reason_that_names_nothing_are_dropped_not_the_entry(self) -> None:
        entry = {"kind": "failed", "reason": "transform_error", "rows": 0, "labels": [_GUARDIAN]}
        (outcome,) = outcomes_from_record({OUTCOMES_RECORD_KEY: {"Family": entry}})
        assert outcome.reason is OutcomeReason.TRANSFORM_ERROR and outcome.labels == ()

    def test_an_unknown_kind_names_nothing(self) -> None:
        entry = {"kind": "from_the_future", "reason": "missing_source_column", "rows": 0, "labels": [_GUARDIAN]}
        (outcome,) = outcomes_from_record({OUTCOMES_RECORD_KEY: {"Family": entry}})
        assert outcome.kind is OutcomeKind.FAILED and outcome.labels == ()


# --------------------------------------------------------------------------- #
# The vocabulary, derived from the real bundled configs                         #
# --------------------------------------------------------------------------- #
class TestLabelVocabularyFromTheRealConfigs:
    def test_unity_family_is_one_file_with_the_guardian_column_in_config_spelling(self) -> None:
        family = label_vocabulary_by_entity(load_config("unitychristianmyedbc"))["Family"]
        assert family.files == (_CONTACTS,) and family.reads_own_files
        assert {_GUARDIAN, _EMAIL} <= family.columns
        assert "parent auth / guardian" not in family.columns  # never lower-cased

    def test_classes_declares_five_files_so_it_can_never_name_one(self) -> None:
        classes = label_vocabulary_by_entity(load_config("myedbc"))["Classes"]
        assert len(classes.files) == 5
        some_column = next(iter(classes.columns))
        assert derive_labels([some_column], classes) == ("", (some_column,))

    def test_enrollments_reads_across_files_so_it_can_never_name_one(self) -> None:
        assert observation_scope("Enrollments") is ObservationScope.ALL_FILES
        assert not label_vocabulary_by_entity(load_config("myedbc"))["Enrollments"].reads_own_files

    def test_attendance_placeholders_are_not_columns_but_its_band_columns_are(self) -> None:
        """Its field_map values are placeholders (NO_CLAIM) and never labels; its REAL reads — the
        ``global_config.attendance`` band columns, all on its own files — are (0053 S13b, owner
        ruling 2026-09-26: an absent required band column is named)."""
        config = load_config("sd51attendance")
        attendance = label_vocabulary_by_entity(config)["StudentAttendance"]
        band = config.global_config.attendance
        configured = {str(v).strip() for b in ("daily", "period") for k, v in band[b].items() if k.endswith("_col")}
        assert attendance.columns == configured and "authorized am" in attendance.columns
        assert not ({"School Number", "Absence Date", "Student Number"} & attendance.columns)  # the placeholders
        assert attendance.reads_own_files
        # Two band files, so the single-file rule still names no file beside the column.
        assert len(attendance.files) == 2
        assert derive_labels(["authorized am"], attendance) == ("", ("authorized am",))

    def test_a_malformed_attendance_band_contributes_nothing_and_never_raises(self) -> None:
        """``_attendance_band_columns`` is TOTAL: ``global_config.attendance`` is a free
        ``dict[str, Any]``, so a user overlay can carry a non-dict band or a non-str ``_col``
        value. Those drop out; the well-formed columns still come through (the positive twin)."""
        config = load_config("sd51attendance")
        band = config.global_config.attendance
        band["period"] = ["x"]
        band["daily"] = {**band["daily"], "daily_authorized_col": 7}
        expected = {v.strip() for k, v in band["daily"].items() if k.endswith("_col") and isinstance(v, str)} - {""}
        assert expected  # non-vacuous: some well-formed daily columns survive

        attendance = label_vocabulary_by_entity(config)["StudentAttendance"]

        assert attendance.columns == expected
        assert "7" not in attendance.columns and "x" not in attendance.columns

    def test_the_attendance_vocabulary_suffix_covers_every_key_the_transformer_reads(self) -> None:
        """``preflight`` finds band columns by the ``_col`` suffix; the transformer reads exactly
        ``_DAILY_KEYS`` / ``_PERIOD_KEYS`` — pinned so the two cannot drift (and non-vacuous)."""
        from src.etl.preflight import ATTENDANCE_COLUMN_KEY_SUFFIX
        from src.etl.transformers.student_attendance import _DAILY_KEYS, _PERIOD_KEYS

        assert _DAILY_KEYS and _PERIOD_KEYS
        assert all(key.endswith(ATTENDANCE_COLUMN_KEY_SUFFIX) for key in (*_DAILY_KEYS, *_PERIOD_KEYS))
        assert not "category_map".endswith(ATTENDANCE_COLUMN_KEY_SUFFIX)  # the twin: a knob is not a column

    def test_source_columns_are_never_label_vocabulary_but_field_map_and_row_filters_are(self) -> None:
        # No bundled config declares `source_columns` today, so the synthetic config that exercises
        # every field-map variant (plus row_filters and source_columns) stands in.
        from src.etl.preflight import ExpectationOrigin, expected_columns
        from tests.test_etl_preflight import _synthetic_config

        config = _synthetic_config()
        auxiliary = {
            item.source_column
            for item in expected_columns(config)
            if item.origin is ExpectationOrigin.SOURCE_COLUMN and item.entity == "Students"
        }
        assert auxiliary == {"Course Code Full"}, "non-vacuity: the config really declares source_columns"
        vocab = label_vocabulary_by_entity(config)["Students"]
        assert not (auxiliary & vocab.columns)
        assert {"Student Number", "Parent Auth / Guardian"} <= vocab.columns  # field_map + row_filters
        # Config spelling, trimmed, never lower-cased: Staff maps "  Legal Surname  ".
        assert "Legal Surname" in label_vocabulary_by_entity(config)["Staff"].columns

    def test_only_active_entities_get_a_vocabulary(self) -> None:
        assert set(label_vocabulary_by_entity(load_config("mbponly"))) == {"CourseInfo", "StudentCourses"}

    @pytest.mark.parametrize("sis_type", available_configs())
    def test_every_bundled_label_passes_safe_label(self, sis_type: str) -> None:
        # The shape rule never suppresses a real config name: every declared label is nameable.
        vocabularies = label_vocabulary_by_entity(load_config(sis_type))
        assert vocabularies, "non-vacuity: every config has at least one active entity"
        for vocab in vocabularies.values():
            for label in (*vocab.columns, *vocab.files):
                assert safe_label(label, vocabulary=frozenset({label})) == label

    def test_the_derivation_is_total(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.etl import preflight

        def _boom(_config: object) -> None:
            raise RuntimeError("derivation bug")

        monkeypatch.setattr(preflight, "expected_columns", _boom)
        assert label_vocabulary_by_entity(load_config("myedbc")) == {}
