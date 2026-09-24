"""``src/ui_flet/failure_copy.py`` — the ONE copy source for failures and outcomes (plan 0053 S3).

What is pinned here, and why each pin has a twin:

* **Totality.** ``FAILED_CATEGORY_COPY`` covers every ``RunErrorCategory`` except ``NONE`` (which
  raises); ``outcome_sentence`` covers every valid ``(OutcomeKind, OutcomeReason)``;
  ``OUTCOME_TIER`` covers every ``OutcomeKind``. Each is derived from the ENUM, so a new member
  without copy is RED.
* **Bounded surfacing (P9).** No copy string carries an interpolation slot, a filename, a path,
  an unknown entity key or exception text — swept with a sentinel that would be visible anywhere.
* **The misdirection this replaced.** Only ``NO_INPUT`` and ``INPUT_UNREADABLE`` mention the input
  folder — asserted in both directions (the two that must, do).
* **One copy source.** Home, Run History and Convert's card render the SAME category detail, and
  the closing tail has ONE meaning everywhere — records are built the way the pipeline builds them
  (``build_run_record``), never in a shape no producer writes.
* **No permissive default.** The tail's and the outcome sentence's bools are keyword-only with no
  default (signature-pinned, with a doctored twin proving the check can fail).
* **Doc parity.** The PARTIAL strings ``docs/claugentic-PRODUCT.md`` quotes are computed from source.
* **The import cycle the entity-map move avoided.** ``failure_copy`` imports nothing from
  ``home_status``, and ``home_status`` defines no entity label map — AST-pinned with doctored twins.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.config.app_config import AppConfig
from src.etl.errors import (
    ConfigLoadError,
    EtlError,
    GuardKind,
    NoUsableInputError,
    RunErrorCategory,
    SourceSchemaError,
)
from src.etl.extractor import ExtractionError
from src.etl.outcomes import (
    ENTITY_CRITICALITY,
    VALID_REASONS,
    EntityOutcome,
    OutcomeKind,
    OutcomeReason,
    failed_entities,
)
from src.etl.pipeline import DeliveryIntegrityError, OutputWriteError, build_run_record
from src.ui_flet import failure_copy, home_status, humanize
from src.ui_flet.failure_copy import (
    FAILED_CATEGORY_COPY,
    FALLBACK_CATEGORY,
    NOTHING_SAVED_TAIL,
    NOTHING_SENT_TAIL,
    OUTCOME_TIER,
    UNKNOWN_ENTITY_PHRASE,
    data_warnings_clause,
    entity_phrase,
    error_card_copy,
    failed_copy,
    failed_copy_for,
    outcome_sentence,
    partial_copy,
)
from src.ui_flet.home_status import derive_home_status
from src.ui_flet.run_history import derive_history_banner, to_run_row
from src.ui_flet.verdict import Verdict

SENTINEL = r"SENTINEL_PII C:\secret"
_SRC = Path(__file__).resolve().parents[1] / "src" / "ui_flet"

_FAILURE_CATEGORIES = [c for c in RunErrorCategory if c is not RunErrorCategory.NONE]
_INPUT_FOLDER_CATEGORIES = {RunErrorCategory.NO_INPUT, RunErrorCategory.INPUT_UNREADABLE}
_VALID_PAIRS = [(kind, reason) for kind, reasons in VALID_REASONS.items() for reason in sorted(reasons)]


def _outcome(entity: str, kind: OutcomeKind, reason: OutcomeReason) -> EntityOutcome:
    return EntityOutcome(entity, kind, reason, 5 if kind is OutcomeKind.BUILT else 0)


def _every_copy_string() -> list[str]:
    """Every string this module can render, for every closed input (both bools, known + unknown)."""
    out: list[str] = []
    for category in _FAILURE_CATEGORIES:
        for requested in (True, False):
            out.extend(failed_copy(category, delivery_requested=requested))
    for kind, reason in _VALID_PAIRS:
        for entity in ("Family", SENTINEL):
            for delivered in (True, False):
                out.append(outcome_sentence(_outcome(entity, kind, reason), delivered=delivered))
    for failed in (
        [_outcome("Family", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN)],
        [_outcome(SENTINEL, OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR)],
        [
            _outcome("Family", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN),
            _outcome("CourseInfo", OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR),
            _outcome(SENTINEL, OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR),
        ],
        # Every key unknown: the branch where nothing named precedes the count.
        [
            _outcome(SENTINEL, OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR),
            _outcome(SENTINEL + "_2", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN),
        ],
    ):
        for delivered in (True, False):
            out.extend(partial_copy(failed, delivered=delivered))
    out.append(data_warnings_clause(3))
    return out


# --------------------------------------------------------------------------- #
# Entity phrases                                                               #
# --------------------------------------------------------------------------- #
class TestEntityPhrase:
    def test_a_known_entity_reads_as_its_countable_plural(self) -> None:
        assert entity_phrase("Family") == "family contacts"
        assert entity_phrase("StudentAttendance") == "attendance rows"

    @pytest.mark.parametrize("entity", sorted(ENTITY_CRITICALITY))
    def test_every_registry_entity_has_an_authored_phrase(self, entity: str) -> None:
        # A shipped entity reading "one of your files" would hide WHICH file from the admin.
        assert entity_phrase(entity) == humanize.SIZE_NOUNS[entity][1]
        assert entity_phrase(entity) != UNKNOWN_ENTITY_PHRASE

    @pytest.mark.parametrize("entity", [SENTINEL, "", "family", None, 42])
    def test_anything_else_is_the_generic_phrase_never_the_raw_key(self, entity: object) -> None:
        assert entity_phrase(entity) == UNKNOWN_ENTITY_PHRASE


# --------------------------------------------------------------------------- #
# Outcome sentences + tier                                                     #
# --------------------------------------------------------------------------- #
class TestOutcomeSentence:
    def test_the_templates_cover_exactly_the_valid_pairs(self) -> None:
        # Both directions: no valid pair without copy, and no copy for a pair that cannot exist.
        assert set(failure_copy._OUTCOME_TEMPLATES) == set(_VALID_PAIRS)
        assert len(_VALID_PAIRS) >= 7  # non-vacuity: the enum really has these pairs

    @pytest.mark.parametrize(("kind", "reason"), _VALID_PAIRS, ids=lambda v: getattr(v, "value", str(v)))
    @pytest.mark.parametrize("delivered", [True, False])
    def test_total_over_every_valid_pair(self, kind: OutcomeKind, reason: OutcomeReason, delivered: bool) -> None:
        for entity in ("Family", SENTINEL):
            sentence = outcome_sentence(_outcome(entity, kind, reason), delivered=delivered)
            assert sentence and sentence[0].isupper() and sentence.endswith(".")
            assert "{" not in sentence and "}" not in sentence

    def test_the_family_missing_column_sentence(self) -> None:
        outcome = _outcome("Family", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN)
        assert outcome_sentence(outcome, delivered=True) == (
            "Family contacts were left out of this sync: their export file is missing a column this "
            "district's mapping needs — often because a different report was saved under the same "
            "name. Everything else was delivered. Re-export that file and the next sync picks it up "
            "automatically."
        )
        assert "Everything else completed." in outcome_sentence(outcome, delivered=False)

    def test_an_unknown_entity_is_worded_in_the_singular(self) -> None:
        sentence = outcome_sentence(
            _outcome(SENTINEL, OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN), delivered=True
        )
        assert sentence.startswith("One of your files was left out of this sync: its export file")


class TestOutcomeTier:
    def test_total_over_every_kind(self) -> None:
        assert set(OUTCOME_TIER) == set(OutcomeKind)

    def test_the_warning_tier_is_exactly_what_failed_entities_selects(self) -> None:
        # S3's PARTIAL predicate (``failed_entities``) and the tier must agree kind for kind, so
        # S8 widening one without the other is RED.
        for kind, reason in _VALID_PAIRS:
            outcome = _outcome("Family", kind, reason)
            selected = bool(failed_entities([outcome]))
            assert selected == (OUTCOME_TIER[kind] is Verdict.WARNING), kind
        assert any(OUTCOME_TIER[kind] is Verdict.WARNING for kind in OutcomeKind)  # non-vacuity


class TestPartialCopy:
    def test_one_entity_delivered(self) -> None:
        failed = [_outcome("Family", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN)]
        headline, detail = partial_copy(failed, delivered=True)
        assert headline == "Your roster synced without family contacts"
        assert detail == outcome_sentence(failed[0], delivered=True)

    def test_one_entity_not_delivered(self) -> None:
        failed = [_outcome("Family", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN)]
        headline, _detail = partial_copy(failed, delivered=False)
        assert headline == "Your sync completed without family contacts"

    def test_several_entities_are_counted_and_named_without_echoing_an_unknown_key(self) -> None:
        failed = [
            _outcome("Family", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN),
            _outcome("CourseInfo", OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR),
            _outcome(SENTINEL, OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR),
        ]
        headline, detail = partial_copy(failed, delivered=True)
        assert headline == "Your roster synced without 3 of your files"
        assert detail.startswith("Family contacts, courses and 1 other file were left out of this sync.")
        assert "SENTINEL" not in detail

    def test_several_entities_all_unknown_are_counted_without_a_dangling_other(self) -> None:
        # A hand-dropped YAML's inventions: none is echoed, and "other" would refer to nothing.
        failed = [
            _outcome(SENTINEL, OutcomeKind.FAILED, OutcomeReason.TRANSFORM_ERROR),
            _outcome(SENTINEL + "_2", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN),
        ]
        headline, detail = partial_copy(failed, delivered=True)
        assert headline == "Your roster synced without 2 of your files"
        assert detail == (
            "2 of your files were left out of this sync. Everything else was delivered. Re-export those "
            "files and the next sync picks them up automatically — if it keeps happening, the Help page "
            "has our support contact."
        )
        assert "other" not in detail
        assert "SENTINEL" not in headline + detail

    def test_nothing_left_out_is_a_caller_bug(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            partial_copy([], delivered=True)


class TestDataWarningsClause:
    def test_none_is_empty_so_callers_append_unconditionally(self) -> None:
        assert data_warnings_clause(0) == ""

    def test_counts_are_worded_and_grouped(self) -> None:
        assert data_warnings_clause(1).startswith("There was also 1 data warning:")
        assert data_warnings_clause(1234).startswith("There were also 1,234 data warnings:")


# --------------------------------------------------------------------------- #
# Failure categories                                                           #
# --------------------------------------------------------------------------- #
class TestFailedCategoryCopy:
    def test_total_over_every_category_except_none(self) -> None:
        assert set(FAILED_CATEGORY_COPY) == set(_FAILURE_CATEGORIES)
        assert len(_FAILURE_CATEGORIES) >= 9  # non-vacuity: incl. S1's two new members

    def test_none_raises_a_completed_run_has_no_failure_copy(self) -> None:
        with pytest.raises(ValueError, match="completed run"):
            failed_copy(RunErrorCategory.NONE, delivery_requested=False)

    @pytest.mark.parametrize("category", _FAILURE_CATEGORIES, ids=lambda c: c.value)
    def test_no_interpolation_slot_anywhere_in_the_table(self, category: RunErrorCategory) -> None:
        headline, detail = FAILED_CATEGORY_COPY[category]
        assert headline and detail
        assert "{" not in headline + detail and "}" not in headline + detail

    @pytest.mark.parametrize("category", _FAILURE_CATEGORIES, ids=lambda c: c.value)
    def test_only_the_two_input_categories_mention_the_input_folder(self, category: RunErrorCategory) -> None:
        for requested in (True, False):
            _headline, detail = failed_copy(category, delivery_requested=requested)
            assert ("input folder" in detail.lower()) is (category in _INPUT_FOLDER_CATEGORIES), category

    def test_the_tail_is_chosen_by_the_delivery_bool(self) -> None:
        _h, sent = failed_copy(RunErrorCategory.SOURCE_SCHEMA, delivery_requested=True)
        _h, saved = failed_copy(RunErrorCategory.SOURCE_SCHEMA, delivery_requested=False)
        assert sent.endswith(NOTHING_SENT_TAIL) and NOTHING_SAVED_TAIL not in sent
        assert saved.endswith(NOTHING_SAVED_TAIL) and NOTHING_SENT_TAIL not in saved

    def test_the_tails_make_no_claim_about_what_spacesedu_holds(self) -> None:
        for tail in (NOTHING_SENT_TAIL, NOTHING_SAVED_TAIL):
            for claim in ("keeps", "still has", "last good", "untouched", "not changed"):
                assert claim not in tail


class TestFailedCopyFor:
    @pytest.mark.parametrize("value", [None, "", "a_future_category", "none", 7, ["config"]])
    def test_anything_unreadable_or_impossible_is_the_generic_copy(self, value: object) -> None:
        assert failed_copy_for(value, delivery_requested=False) == failed_copy(
            FALLBACK_CATEGORY, delivery_requested=False
        )

    @pytest.mark.parametrize("category", _FAILURE_CATEGORIES, ids=lambda c: c.value)
    def test_a_stored_value_reads_its_own_category(self, category: RunErrorCategory) -> None:
        # The positive twin: the fallback above is not the ONLY answer.
        assert failed_copy_for(category.value, delivery_requested=True) == failed_copy(
            category, delivery_requested=True
        )


class TestErrorCardCopy:
    """Convert's crash card is the exception's category, decided by TYPE (never its text)."""

    @pytest.mark.parametrize(
        ("exc", "category"),
        [
            (
                SourceSchemaError(SENTINEL, entity="Family", columns=("Guardian",), guard=GuardKind.PII_SCOPE),
                RunErrorCategory.SOURCE_SCHEMA,
            ),
            (NoUsableInputError(SENTINEL), RunErrorCategory.NO_INPUT),
            (ExtractionError(SENTINEL), RunErrorCategory.INPUT_UNREADABLE),
            (ConfigLoadError(SENTINEL), RunErrorCategory.CONFIG),
            (FileNotFoundError(SENTINEL), RunErrorCategory.CONFIG),
            (OutputWriteError(SENTINEL), RunErrorCategory.OUTPUT),
            (DeliveryIntegrityError(SENTINEL, category=RunErrorCategory.NO_OUTPUT), RunErrorCategory.NO_OUTPUT),
            (
                DeliveryIntegrityError(SENTINEL, category=RunErrorCategory.INCOMPLETE_ROSTER),
                RunErrorCategory.INCOMPLETE_ROSTER,
            ),
            (ValueError(SENTINEL), RunErrorCategory.DATA),
            (KeyError(SENTINEL), RunErrorCategory.UNKNOWN),
            (RuntimeError(SENTINEL), RunErrorCategory.UNKNOWN),
        ],
        ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else v.value,
    )
    def test_each_typed_exception_maps_to_its_categorys_copy(
        self, exc: BaseException, category: RunErrorCategory
    ) -> None:
        for requested in (True, False):
            card = error_card_copy(exc, delivery_requested=requested)
            assert card == failed_copy(category, delivery_requested=requested)
            assert "SENTINEL" not in card[0] + card[1]

    def test_a_none_category_exception_still_gets_a_card(self) -> None:
        # Total: an EtlError that claims "none" is a programming error, never a crash of the card.
        card = error_card_copy(EtlError("x", category=RunErrorCategory.NONE), delivery_requested=False)
        assert card == failed_copy(FALLBACK_CATEGORY, delivery_requested=False)

    @pytest.mark.parametrize(
        "exc",
        [
            SourceSchemaError("x", entity="Family", columns=("Guardian",), guard=GuardKind.JOIN_KEY),
            ConfigLoadError("x"),
            ValueError("x"),
        ],
        ids=["source_schema", "config", "data"],
    )
    def test_schema_config_and_data_cards_never_point_at_the_input_folder(self, exc: BaseException) -> None:
        _headline, detail = error_card_copy(exc, delivery_requested=False)
        assert "input folder" not in detail.lower()


# --------------------------------------------------------------------------- #
# Bounded surfacing (P9)                                                       #
# --------------------------------------------------------------------------- #
class TestNothingUnboundedReachesCopy:
    def test_the_sweep_is_not_vacuous(self) -> None:
        strings = _every_copy_string()
        assert len(strings) > 60  # 9 categories x 2 bools x 2 strings alone is 36
        assert any("family contacts" in s.lower() for s in strings)  # the entity path IS exercised

    def test_no_string_carries_the_sentinel_a_path_or_a_slot(self) -> None:
        for text in _every_copy_string():
            assert "SENTINEL" not in text
            assert "secret" not in text
            assert ":\\" not in text
            assert "{" not in text and "}" not in text


# --------------------------------------------------------------------------- #
# One copy source across Home, Run History and Convert                         #
# --------------------------------------------------------------------------- #
_NOW = datetime(2026, 7, 4, 8, 0, 0)
_CFG = AppConfig(input_dir="/in", output_dir="/out", sis_type="sd48myedbc", schedule_registered=True)


def _pipeline_failed_record(category: RunErrorCategory, *, attempted: bool = False, ok: bool = False) -> dict:
    """A failed record built by the SAME builder the pipeline's failure sink uses.

    The defaults are the shape that sink really writes for every pre-upload failure:
    ``sftp_attempted`` is set only after the committed write, so it is False even for a nightly
    with delivery configured. ``attempted``/``ok`` exist for the post-write windows only.
    """
    record = build_run_record(
        status="failed",
        elapsed=1.0,
        entity_counts={},
        sftp_attempted=attempted,
        sftp_ok=ok,
        source="scheduled",
        sis_type="sd48myedbc",
        error_category=category,
        entity_outcomes=None,
        timestamp=(_NOW - timedelta(hours=5)).isoformat(timespec="seconds"),
    )
    record["error"] = SENTINEL  # the log-only free text, planted to prove no surface reads it
    return record


def _surfaces(record: dict) -> tuple[str, str]:
    home = derive_home_status([record], _CFG, now=_NOW)
    banner = derive_history_banner([record], _CFG, now=_NOW)
    assert home.verdict is banner.verdict is Verdict.FAILED
    return home.detail, banner.detail


class TestTheThreeSurfacesWordAFailureIdentically:
    @pytest.mark.parametrize("category", _FAILURE_CATEGORIES, ids=lambda c: c.value)
    def test_the_realistic_failed_record_and_the_convert_card_share_the_whole_detail(
        self, category: RunErrorCategory
    ) -> None:
        # No delivery requested on Convert == the record the pipeline writes for a pre-upload
        # failure: both end "Nothing new was saved..." -- the same words, tail included.
        _headline, detail = failed_copy(category, delivery_requested=False)
        home, banner = _surfaces(_pipeline_failed_record(category))
        card = error_card_copy(EtlError("x", category=category), delivery_requested=False)

        assert home == f"The sync that ran 5 hours ago didn't finish. {detail}"
        assert banner == f"The most recent run didn't finish. {detail}"
        assert card[1] == detail
        for text in (home, banner, *card):
            assert "SENTINEL" not in text

    @pytest.mark.parametrize("category", _FAILURE_CATEGORIES, ids=lambda c: c.value)
    def test_the_category_sentence_is_identical_whatever_each_surface_can_prove_about_delivery(
        self, category: RunErrorCategory
    ) -> None:
        # With delivery requested, Convert's card may say "nothing was sent" (it knows the request);
        # the record cannot, so it keeps the saved tail. The CATEGORY words never differ.
        category_detail = FAILED_CATEGORY_COPY[category][1]
        home, banner = _surfaces(_pipeline_failed_record(category))
        card = error_card_copy(EtlError("x", category=category), delivery_requested=True)
        for text in (home, banner, card[1]):
            assert category_detail in text
        assert card[1].endswith(NOTHING_SENT_TAIL)
        assert home.endswith(NOTHING_SAVED_TAIL) and banner.endswith(NOTHING_SAVED_TAIL)

    def test_a_record_proving_the_upload_failed_says_nothing_was_sent_on_both_record_surfaces(self) -> None:
        # The one record shape that proves "not sent": an attempt that did not succeed.
        home, banner = _surfaces(_pipeline_failed_record(RunErrorCategory.UNKNOWN, attempted=True, ok=False))
        assert home.endswith(NOTHING_SENT_TAIL) and banner.endswith(NOTHING_SENT_TAIL)

    def test_twin_a_record_whose_upload_succeeded_never_says_nothing_was_sent(self) -> None:
        # The post-upload window (``--quality``/``--diff`` raising after ``_sftp_upload``): the files
        # WERE sent. The doctored shape the old parity fixture used would have claimed otherwise.
        home, banner = _surfaces(_pipeline_failed_record(RunErrorCategory.UNKNOWN, attempted=True, ok=True))
        assert NOTHING_SENT_TAIL not in home and NOTHING_SENT_TAIL not in banner


# --------------------------------------------------------------------------- #
# No permissive default on the bools that pick a claim                         #
# --------------------------------------------------------------------------- #
_CLAIM_BOOLS: list[tuple[Callable[..., object], str]] = [
    (failed_copy, "delivery_requested"),
    (failed_copy_for, "delivery_requested"),
    (error_card_copy, "delivery_requested"),
    (outcome_sentence, "delivered"),
    (partial_copy, "delivered"),
]


def _is_required_keyword_only(fn: Callable[..., object], name: str) -> bool:
    param = inspect.signature(fn).parameters[name]
    return param.kind is inspect.Parameter.KEYWORD_ONLY and param.default is inspect.Parameter.empty


class TestClaimBoolsHaveNoDefault:
    @pytest.mark.parametrize(("fn", "name"), _CLAIM_BOOLS, ids=[f"{fn.__name__}-{name}" for fn, name in _CLAIM_BOOLS])
    def test_required_keyword_only(self, fn: Callable[..., object], name: str) -> None:
        # Each bool picks a claim about what did (not) happen; a default would let a caller make the
        # wrong one silently.
        assert _is_required_keyword_only(fn, name)

    def test_doctored_a_defaulted_or_positional_parameter_is_caught(self) -> None:
        def defaulted(category: RunErrorCategory, *, delivery_requested: bool = False) -> None:
            del category, delivery_requested

        def positional(category: RunErrorCategory, delivery_requested: bool) -> None:
            del category, delivery_requested

        assert not _is_required_keyword_only(defaulted, "delivery_requested")
        assert not _is_required_keyword_only(positional, "delivery_requested")


# --------------------------------------------------------------------------- #
# The PARTIAL strings docs/claugentic-PRODUCT.md quotes                        #
# --------------------------------------------------------------------------- #
_PRODUCT_DOC = Path(__file__).resolve().parents[1] / "docs" / "claugentic-PRODUCT.md"


def _product_doc_quotes() -> list[str]:
    """The two PARTIAL strings PRODUCT.md quotes, COMPUTED from source (never copied)."""
    headline, _detail = partial_copy(
        [_outcome("Family", OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN)], delivered=True
    )
    outcomes = [
        _outcome(name, OutcomeKind.FAILED, OutcomeReason.MISSING_SOURCE_COLUMN)
        if name == "Family"
        else _outcome(name, OutcomeKind.BUILT, OutcomeReason.NONE)
        for name in ("Students", "Staff", "Family", "Classes", "Enrollments")
    ]
    record = build_run_record(
        status="success",
        elapsed=1.0,
        entity_counts={"Students": 10},
        sftp_attempted=True,
        sftp_ok=True,
        source="scheduled",
        sis_type="sd48myedbc",
        error_category=RunErrorCategory.NONE,
        entity_outcomes=outcomes,
        timestamp=(_NOW - timedelta(hours=5)).isoformat(timespec="seconds"),
    )
    row = to_run_row(record, prior_build=None, now=_NOW)
    return [headline, row.status_label]


class TestProductDocQuotesThePartialStrings:
    def test_each_quoted_string_is_computed_and_present_verbatim(self) -> None:
        quotes = _product_doc_quotes()
        assert quotes == ["Your roster synced without family contacts", "Delivered · 1 file skipped"]
        doc = _PRODUCT_DOC.read_text(encoding="utf-8")
        for quote in quotes:
            assert f'"{quote}"' in doc, quote

    def test_doctored_a_reworded_doc_is_red(self) -> None:
        doc = _PRODUCT_DOC.read_text(encoding="utf-8").replace("1 file skipped", "1 file left out")
        assert not all(f'"{quote}"' in doc for quote in _product_doc_quotes())


# --------------------------------------------------------------------------- #
# The import cycle the entity-map move exists to avoid                         #
# --------------------------------------------------------------------------- #
_LABEL_MAPS = {"ENTITY_LABELS", "SIZE_NOUNS"}


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _defined_label_maps(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        names.update(t.id for t in targets if isinstance(t, ast.Name) and t.id in _LABEL_MAPS)
    return names


class TestNoImportCycle:
    def _source(self, name: str) -> str:
        return (_SRC / name).read_text(encoding="utf-8")

    def test_failure_copy_imports_nothing_from_home_status(self) -> None:
        imported = _imported_modules(self._source("failure_copy.py"))
        assert "src.ui_flet.humanize" in imported  # non-vacuity: the walker sees real imports
        assert not any(m.endswith("home_status") for m in imported)

    def test_doctored_a_home_status_import_is_red(self) -> None:
        doctored = self._source("failure_copy.py") + "\nfrom src.ui_flet.home_status import is_stale\n"
        assert any(m.endswith("home_status") for m in _imported_modules(doctored))

    def test_home_status_defines_no_label_map_of_its_own(self) -> None:
        assert _defined_label_maps(self._source("home_status.py")) == set()
        assert _defined_label_maps(self._source("humanize.py")) == _LABEL_MAPS  # the maps DO live somewhere
        assert home_status.SIZE_NOUNS is humanize.SIZE_NOUNS  # re-imported, not copied

    def test_doctored_a_redefined_map_is_red(self) -> None:
        doctored = self._source("home_status.py") + "\nSIZE_NOUNS: dict[str, tuple[str, str]] = {}\n"
        assert _defined_label_maps(doctored) == {"SIZE_NOUNS"}
