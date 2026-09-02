"""The creator flow's VIEW glue (plan 0044 S3) — the wizard's creator branch end to end.

The decisions themselves are COUNTED elsewhere: the form/gate/stored-fact rules in
``tests/test_ui_flet_config_editor.py``, the six-step shape in
``tests/test_ui_flet_setup_flow.py``, the write/digest/provenance in
``tests/test_config_authoring.py``, the three settings writes in
``tests/test_app_config_creator.py``. This file asks the question none of those can:
**does an admin whose district ships no mapping actually get one, and can they only
activate it after a test conversion that wrote nothing?**

Per ``docs/claugentic-CHARTER.md`` → "Flet view-glue surfaces": per-STATE render + wiring
tests over the REAL control tree (``build_setup`` → ``build_creator``), with the real
``write_overlay`` / ``delete_overlay`` / ``AppConfig`` writes landing in the per-test
profile ``isolated_user_profile`` (autouse) redirects. Every ABSENCE assertion carries its
positive twin in the same class — "no overlay was written", "``run_pipeline`` was never
called", "the D9 seed did not fire" and "no Run History row" are each equally satisfied by
a mechanism that does not work at all.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig
from src.config.authoring import (
    OverlaySpec,
    current_digest,
    overlay_path,
    write_overlay,
)
from src.history.store import read_run_records
from src.ui_flet import components
from src.ui_flet.config_editor import CEDS_GRADE_ORDER, CreatorForm
from src.ui_flet.job_runner import GateRefused, creator_gate_job
from src.ui_flet.screens import creator as creator_screen
from src.ui_flet.screens import setup as setup_screen
from src.ui_flet.screens.setup import build_setup
from src.utils.version import app_version

# Single-sourced from the S4a sweep — a second hand-typed banned-word list is a list that
# drifts, and the identification-is-not-authentication promise rests on it.
from tests.test_ui_flet_identity_page import (
    _assert_no_banned_vocabulary,
)

SNAPSHOT_INPUT = Path(__file__).parent / "snapshots" / "input"

#: The base filenames the SD74 snapshot extract renames (same table as
#: ``tests/test_config_authoring.py``) — what makes a REAL dry run over those inputs
#: produce rows for every entity rather than a folder full of missing files.
SD74_RENAMES = {
    "StaffInformationEnhanced.txt": "StaffInformation.txt",
    "EmergencyContactInformation.txt": "ParentInformation.txt",
    "StudentSchedule.txt": "studentcourseselection.txt",
    "ClassInformationEnh.txt": "ClassInfoEnhanced.txt",
}

#: An address at a district that ships NO mapping — the creator's whole population.
SD93_ADMIN = "roster.admin@sd93.bc.ca"
#: An address at a REAL shipped staff domain, so the D9 auto-seed has exactly one visible
#: district to fire on (the twin that proves the "seed did not fire" assertion is real).
SD48_ADMIN = "roster.admin@sd48.bc.ca"

#: Strings that would promise a LATER slice's behaviour (S4's filename form, S5's column
#: report, S6's editor). No rendered creator/Files string may contain one.
FORBIDDEN_PROMISES = ("column", "edit", "soon", "later", "rename")


# --------------------------------------------------------------------------- #
# Tree helpers                                                                 #
# --------------------------------------------------------------------------- #
def _walk(control):  # noqa: ANN001, ANN202 - an untyped Flet tree
    yield control
    children: list[object] = []
    ctrls = getattr(control, "controls", None)
    if isinstance(ctrls, list):
        children.extend(ctrls)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        children.append(content)
    for child in children:
        if isinstance(child, ft.Control):
            yield from _walk(child)


def _texts(tree) -> list[str]:  # noqa: ANN001
    """Every user-visible string in the tree (values, labels, helpers, hints, buttons)."""
    found: list[str] = []
    for control in _walk(tree):
        for attr in ("value", "label", "helper", "hint_text", "content", "tooltip"):
            item = getattr(control, attr, None)
            if isinstance(item, str):
                found.append(item)
    return found


def _blob(tree) -> str:  # noqa: ANN001
    return "\n".join(_texts(tree))


def _error_texts(tree) -> str:  # noqa: ANN001
    """Only the strings painted in the FAILED colour — the inline NOTE, not the whole surface.

    The identity page's rule, for the same reason: a text field keeps whatever the admin typed
    (that is what a field is for); what must never carry the value is the MESSAGE, because a
    caller that logs it would leak it.
    """
    from src.ui_flet import tokens

    return "\n".join(
        control.value
        for control in _walk(tree)
        if isinstance(control, ft.Text)
        and isinstance(control.value, str)
        and control.color == tokens.color_status_failed
    )


def _button(tree, label: str):  # noqa: ANN001, ANN202
    for control in _walk(tree):
        if isinstance(control, (ft.FilledButton, ft.OutlinedButton, ft.TextButton)) and control.content == label:
            return control
    raise AssertionError(f"no button labelled {label!r}; found {sorted(_button_labels(tree))}")


def _button_labels(tree) -> set[str]:  # noqa: ANN001
    return {
        control.content
        for control in _walk(tree)
        if isinstance(control, (ft.FilledButton, ft.OutlinedButton, ft.TextButton)) and isinstance(control.content, str)
    }


def _has_button(tree, label: str) -> bool:  # noqa: ANN001
    return label in _button_labels(tree)


def _field(tree, label: str) -> ft.TextField:  # noqa: ANN001
    for control in _walk(tree):
        if isinstance(control, ft.TextField) and control.label == label:
            return control
    raise AssertionError(f"no TextField labelled {label!r}")


def _dropdown(tree, label: str) -> ft.Dropdown:  # noqa: ANN001
    for control in _walk(tree):
        if isinstance(control, ft.Dropdown) and control.label == label:
            return control
    raise AssertionError(f"no Dropdown labelled {label!r}")


def _dropdowns(tree) -> list[ft.Dropdown]:  # noqa: ANN001
    return [c for c in _walk(tree) if isinstance(c, ft.Dropdown)]


def _checkbox(tree, label: str) -> ft.Checkbox:  # noqa: ANN001
    for control in _walk(tree):
        if isinstance(control, ft.Checkbox) and control.label == label:
            return control
    raise AssertionError(f"no Checkbox labelled {label!r}")


def _checkboxes(tree, label: str) -> list[ft.Checkbox]:  # noqa: ANN001
    """EVERY checkbox carrying ``label``, in tree order.

    The grades card asks the same vocabulary twice — the rostered row, then the homeroom
    subset row — so a grade code legitimately labels two boxes and ``_checkbox`` (first
    match) can only ever speak for the first of them.
    """
    return [c for c in _walk(tree) if isinstance(c, ft.Checkbox) and c.label == label]


def _rostered_row(tree) -> list[ft.Checkbox]:  # noqa: ANN001
    """The grades card's FIRST row — one box per CEDS code, in vocabulary order."""
    return [_checkboxes(tree, code)[0] for code in CEDS_GRADE_ORDER]


def _event(value: object) -> MagicMock:
    """A control event exposing ``e.control.value`` (Dropdown/TextField/Checkbox all read it)."""
    evt = MagicMock()
    evt.control.value = value
    return evt


def _type(field: ft.TextField, value: str) -> None:
    """Type into a text field the way Flet does: set the value, then fire ``on_change``."""
    field.value = value
    if field.on_change is not None:
        field.on_change(_event(value))


def _tick(box: ft.Checkbox, value: bool) -> None:
    box.value = value
    if box.on_change is not None:
        box.on_change(_event(value))


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def page() -> MagicMock:
    """A permissive stub page (no worker threads run — the gate tests opt in below)."""
    return MagicMock()


def _driving_page() -> MagicMock:
    """A page that runs off-thread workers inline and executes marshalled coroutines.

    The same stub ``tests/test_ui_flet_setup_sftp.py`` uses: ``JobRunner.run`` dispatches via
    ``page.run_thread`` and delivers via ``page.run_task``, so both have to be real for the
    gate's result rendering to be exercised at all.
    """
    import asyncio

    page = MagicMock()
    page.run_thread = lambda fn: fn()
    page.run_task = lambda coro, *args: asyncio.run(coro(*args))
    return page


def _cfg(**over) -> AppConfig:  # noqa: ANN003
    base = {
        "input_dir": "",
        "output_dir": "",
        "sis_type": "",
        "setup_completed": False,
        "identity_email": SD93_ADMIN,
        "identity_sd_number": "93",
    }
    base.update(over)
    return AppConfig(**base)  # type: ignore[arg-type]


def _pin(monkeypatch: pytest.MonkeyPatch, cfg: AppConfig) -> AppConfig:
    """``build_setup`` loads its own ``AppConfig`` — pin it, hermetically."""
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda _cls: cfg))
    return cfg


def _spy_reset(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Spy on the catalog invalidation the UI caller owes after every write/delete (S2's rule)."""
    calls: list[int] = []
    monkeypatch.setattr(creator_screen, "reset_catalog_cache", lambda: calls.append(1))
    return calls


def _valid_folders(tmp_path: Path) -> dict[str, str]:
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    return {"input_dir": str(in_dir), "output_dir": str(tmp_path / "out")}


def _write_sd93(*, renames: dict[str, str] | None = None, name: str = "SD93 - Creator Test") -> Path:
    return write_overlay(
        OverlaySpec(
            sd_number=93,
            district_name=name,
            district_domains=("sd93.bc.ca",),
            base="myedbc",
            source_file_renames=renames or {},
        ),
        overwrite=True,
    )


def _open_creator(page: MagicMock, monkeypatch: pytest.MonkeyPatch, cfg: AppConfig) -> ft.Control:
    """Mount the wizard on its District step and press "Set up my district"."""
    _pin(monkeypatch, cfg)
    root = build_setup(page)
    _button(root, setup_screen.CREATOR_ENTRY_LABEL).on_click(None)
    return root


def _fill_and_continue(root: ft.Control, *, sd: str = "93", name: str = "Sunny Ridge") -> None:
    _type(_field(root, creator_screen.CREATOR_SD_FIELD_LABEL), sd)
    _type(_field(root, creator_screen.CREATOR_NAME_FIELD_LABEL), name)
    _button(root, creator_screen.CREATOR_CONTINUE_LABEL).on_click(None)


# --------------------------------------------------------------------------- #
# 1. The branch renders, and it is a DOOR — not a replacement                  #
# --------------------------------------------------------------------------- #
class TestTheCreatorBranchRenders:
    def test_the_standard_District_step_offers_the_creator_door_at_text_tier(self, page, monkeypatch) -> None:
        """The entry point is TEXT tier so the step keeps its ONE filled primary (Continue)."""
        _pin(monkeypatch, _cfg())
        root = build_setup(page)

        door = _button(root, setup_screen.CREATOR_ENTRY_LABEL)

        assert isinstance(door, ft.TextButton), "the creator door must not compete with Continue"
        assert _dropdowns(root), "the standard district picker is still the primary answer"

    def test_pressing_it_renders_the_four_forms_and_no_district_picker(self, page, monkeypatch) -> None:
        root = _open_creator(page, monkeypatch, _cfg())

        blob = _blob(root)
        assert _dropdown(root, creator_screen.CREATOR_START_FIELD_LABEL) is not None
        assert _field(root, creator_screen.CREATOR_SD_FIELD_LABEL).value == "93", "prefilled from the launch page"
        assert _field(root, creator_screen.CREATOR_NAME_FIELD_LABEL) is not None
        # The stored identity domain LEADS (it is the one value we know is real for this
        # install); the vendored table's rows for SD93 follow. Both are prefills, correctable
        # in the field they land in.
        assert _field(root, creator_screen.CREATOR_DOMAINS_FIELD_LABEL).value.startswith("sd93.bc.ca")
        for entity in ("Students", "Families", "Student courses"):
            assert _checkbox(root, entity) is not None
        assert _checkbox(root, creator_screen.CREATOR_GRADES_INHERIT_LABEL).value is True, "grades inherit by default"
        assert creator_screen.CREATOR_START_PROMPT in blob
        assert not [d for d in _dropdowns(root) if d.label == "District"], "the creator surface replaces the picker"
        assert _has_button(root, creator_screen.CREATOR_DISCARD_LABEL), "never a dead end"

    def test_exactly_one_filled_primary_on_the_creator_forms(self, page, monkeypatch) -> None:
        root = _open_creator(page, monkeypatch, _cfg())

        filled = [c for c in _walk(root) if isinstance(c, ft.FilledButton)]

        assert [c.content for c in filled] == [creator_screen.CREATOR_CONTINUE_LABEL]

    def test_the_grades_form_opens_on_a_full_roster_with_the_homeroom_half_seeded(self, page, monkeypatch) -> None:
        """S3b's card. Un-ticking "use my starting point's grades" must open a VALID chain
        (``homeroom ⊆ rostered`` before the admin touches anything) that narrows NOTHING: the
        rostered row opens on the WHOLE vocabulary, and only the homeroom subset is seeded from
        the starting point's own list. Seeding the rostered row from that list instead
        de-rostered grades 8-12 the moment the question was opened."""
        root = _open_creator(page, monkeypatch, _cfg())

        _tick(_checkbox(root, creator_screen.CREATOR_GRADES_INHERIT_LABEL), False)

        blob = _blob(root)
        assert creator_screen.CREATOR_GRADES_PROMPT in blob
        assert creator_screen.CREATOR_HOMEROOM_PROMPT in blob
        # Two rows, same vocabulary: [rostered, homeroom].
        assert [c.value for c in _checkboxes(root, "KG")] == [True, True], "a homeroom grade, rostered"
        assert [c.value for c in _checkboxes(root, "12")] == [True, False], "rostered, but no homeroom"
        assert all(box.value is True for box in _rostered_row(root)), "opening the question narrowed the roster"

    def test_the_shipped_recommendation_only_appears_for_a_district_we_ship(self, page, monkeypatch) -> None:
        """The owner's decision (open question 1): a shipped mapping is RECOMMENDED, never enforced."""
        root = _open_creator(page, monkeypatch, _cfg(identity_email=SD48_ADMIN, identity_sd_number="48"))
        assert creator_screen.creator_shipped_note("48") in _blob(root)

        # The twin: SD93 ships nothing, so there is nothing to point that admin at.
        other = _open_creator(MagicMock(), monkeypatch, _cfg())
        assert creator_screen.creator_shipped_note("93") not in _blob(other)


# --------------------------------------------------------------------------- #
# 2. Continue IS the write                                                     #
# --------------------------------------------------------------------------- #
class TestContinueIsTheWrite:
    def test_continue_writes_the_overlay_stores_the_token_invalidates_and_advances(
        self, page, monkeypatch, tmp_path
    ) -> None:
        cfg = _cfg(**_valid_folders(tmp_path))
        resets = _spy_reset(monkeypatch)
        root = _open_creator(page, monkeypatch, cfg)

        _fill_and_continue(root)

        assert overlay_path("sd93custom").exists(), "no mapping file reached the profile"
        assert cfg.creator_pending_sis == "sd93custom", "the resume token was not stored"
        assert resets == [1], "the picker catalog was not invalidated after the write"
        assert cfg.sis_type == "", "the write must NEVER touch the district this install converts"
        assert "Step 2 of 6" in _texts(root), "the creator walk did not advance to Folders"

    def test_a_refused_load_back_writes_nothing_and_does_not_advance(self, page, monkeypatch, tmp_path) -> None:
        """The twin. ``write_overlay`` load-backs before any bytes land, so a config that could
        not load leaves the profile untouched — and the step must stay put behind a BOUNDED note
        that never echoes the exception (Pydantic quotes values; a domain is one)."""
        cfg = _cfg(**_valid_folders(tmp_path))
        resets = _spy_reset(monkeypatch)

        def _refuse(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
            raise ValueError("homeroom grades ['09'] are not rostering grades: roster.admin@sd93.bc.ca")

        monkeypatch.setattr(creator_screen, "write_overlay", _refuse)
        root = _open_creator(page, monkeypatch, cfg)

        _fill_and_continue(root)

        assert not overlay_path("sd93custom").exists()
        assert cfg.creator_pending_sis == ""
        assert resets == [], "nothing was written, so nothing may be invalidated"
        note = _error_texts(root)
        assert creator_screen.CREATOR_WRITE_FAILED_NOTE in note
        assert "Step 1 of 6" in _texts(root), "a failed write must not walk the admin forward"
        assert "sd93.bc.ca" not in note, "the note echoed the exception's value"

    @pytest.mark.parametrize(
        ("sd", "name", "expected"),
        [
            ("", "Sunny Ridge", "CREATOR_SD_INVALID_NOTE"),
            ("93", "   ", "CREATOR_NAME_REQUIRED_NOTE"),
        ],
    )
    def test_a_field_problem_answers_with_its_own_note_and_never_attempts_a_write(
        self, page, monkeypatch, sd, name, expected
    ) -> None:
        cfg = _cfg()
        wrote: list[int] = []
        monkeypatch.setattr(creator_screen, "write_overlay", lambda *_a, **_kw: wrote.append(1))
        root = _open_creator(page, monkeypatch, cfg)

        _fill_and_continue(root, sd=sd, name=name)

        assert wrote == []
        assert getattr(creator_screen, expected) in _error_texts(root)

    def test_an_invalid_domain_is_reported_without_echoing_it(self, page, monkeypatch) -> None:
        root = _open_creator(page, monkeypatch, _cfg())
        _type(_field(root, creator_screen.CREATOR_DOMAINS_FIELD_LABEL), "roster.admin@sd93.bc.ca")

        _fill_and_continue(root)

        note = _error_texts(root)
        assert creator_screen.CREATOR_DOMAIN_INVALID_NOTE in note
        assert "roster.admin" not in note, "the likeliest bad paste is a personal address"
        assert not overlay_path("sd93custom").exists()

    def test_unticking_every_file_is_refused_rather_than_silently_inheriting(self, page, monkeypatch) -> None:
        """An empty tick list LOOKS like "produce nothing" and would EMIT "inherit" — the two
        readings disagree, so the form refuses instead of picking one."""
        root = _open_creator(page, monkeypatch, _cfg())
        for entity in ("Students", "Staff", "Families", "Classes", "Enrollments"):
            _tick(_checkbox(root, entity), False)

        _fill_and_continue(root)

        assert creator_screen.CREATOR_ENTITIES_EMPTY_NOTE in _error_texts(root)
        assert not overlay_path("sd93custom").exists()

    def test_opening_the_grades_question_writes_no_narrowing_of_its_own(self, page, monkeypatch, tmp_path) -> None:
        """Merely OPENING the grades question must de-roster NOBODY.

        The S3 review's first blocking finding: the question seeded its rostered row from the
        starting point's HOMEROOM list, so on ``myedbc`` an admin who un-ticked "use my
        starting point's grades" and pressed Continue — without touching a single grade — wrote
        a roster of ``IT…07`` and lost every grade 8-12 student silently. What is written must
        be behaviourally identical to inheriting until the admin narrows something.
        """
        from src.config.loader import load_config
        from src.config.models import CLASS_ROSTERING_HOMEROOM_SENTINEL

        cfg = _cfg(**_valid_folders(tmp_path))
        root = _open_creator(page, monkeypatch, cfg)

        _tick(_checkbox(root, creator_screen.CREATOR_GRADES_INHERIT_LABEL), False)
        _fill_and_continue(root)

        assert overlay_path("sd93custom").exists(), "the write did not happen"
        resolved = load_config("sd93custom").global_config
        assert set(resolved.student_rostering_grades or ()) == set(CEDS_GRADE_ORDER), (
            "opening the question dropped grades from the roster"
        )
        assert resolved.class_rostering_grades != CLASS_ROSTERING_HOMEROOM_SENTINEL, (
            "the homeroom sentinel would confine class rostering to K-7"
        )
        assert set(resolved.class_rostering_grades or ()) == set(CEDS_GRADE_ORDER)
        # …and the homeroom half is the starting point's own list, so the chain is unchanged.
        assert list(resolved.homeroom_grades) == list(load_config("myedbc").global_config.homeroom_grades)

    def test_the_twin_unticking_one_grade_DOES_narrow_the_written_roster(self, page, monkeypatch, tmp_path) -> None:
        """Without this twin the row above passes just as well on a form that cannot narrow."""
        from src.config.loader import load_config

        cfg = _cfg(**_valid_folders(tmp_path))
        root = _open_creator(page, monkeypatch, cfg)

        _tick(_checkbox(root, creator_screen.CREATOR_GRADES_INHERIT_LABEL), False)
        _tick(_checkboxes(root, "08")[0], False)  # the ROSTERED row's grade 8
        _fill_and_continue(root)

        resolved = load_config("sd93custom").global_config
        assert set(resolved.student_rostering_grades or ()) == set(CEDS_GRADE_ORDER) - {"08"}

    def test_a_corrected_district_number_leaves_exactly_one_overlay_behind(self, page, monkeypatch, tmp_path) -> None:
        """The S3 review's second blocking finding: a mistyped number left an ORPHAN.

        ``93`` → Continue → Back → ``94`` → Continue used to write ``sd94custom`` while leaving
        ``sd93custom`` on disk: both rode every picker, Discard only ever knew the pending one,
        and "Nothing was kept, and your district list is back as it was" became untrue.
        """
        from src.config.loader import available_configs

        cfg = _cfg(**_valid_folders(tmp_path))
        root = _open_creator(page, monkeypatch, cfg)

        _fill_and_continue(root, sd="93")
        _button(root, "Back").on_click(None)  # back to the creator District step
        _fill_and_continue(root, sd="94")

        assert overlay_path("sd94custom").exists(), "the corrected district was not written"
        assert not overlay_path("sd93custom").exists(), "the superseded overlay was left behind"
        assert [sis for sis in available_configs() if sis.endswith("custom")] == ["sd94custom"]
        assert cfg.creator_pending_sis == "sd94custom"

    def test_the_twin_re_pressing_continue_on_the_same_number_deletes_nothing(
        self, page, monkeypatch, tmp_path
    ) -> None:
        """The twin: the tidy-up is keyed on a CHANGED id, so an edit that keeps the number
        (a corrected name, another domain) must never delete the district's own mapping."""
        cfg = _cfg(**_valid_folders(tmp_path))
        root = _open_creator(page, monkeypatch, cfg)
        _fill_and_continue(root, sd="93")
        _button(root, "Back").on_click(None)
        deletes: list[str] = []
        monkeypatch.setattr(creator_screen, "delete_overlay", lambda sis: deletes.append(sis))

        _fill_and_continue(root, sd="93", name="Sunny Ridge East")

        assert deletes == [], "an unchanged district number must not delete anything"
        assert overlay_path("sd93custom").exists()
        assert cfg.creator_pending_sis == "sd93custom"

    def test_a_refused_resume_token_still_advances_and_says_what_was_lost(self, page, monkeypatch, tmp_path) -> None:
        """The file is on disk and the step is re-visitable, so only resume convenience was lost."""
        cfg = _cfg(**_valid_folders(tmp_path))
        monkeypatch.setattr(AppConfig, "creator_save", lambda _self, **_kw: False)
        root = _open_creator(page, monkeypatch, cfg)

        _fill_and_continue(root)

        assert overlay_path("sd93custom").exists()
        assert "Step 2 of 6" in _texts(root), "a refused advisory write must not trap the admin"
        assert creator_screen.CREATOR_RESUME_REFUSED_NOTE in _blob(root)


# --------------------------------------------------------------------------- #
# 3. Resume — and the token that self-heals                                    #
# --------------------------------------------------------------------------- #
class TestResume:
    def test_a_pending_token_lands_in_creator_mode_at_the_right_step(self, page, monkeypatch, tmp_path) -> None:
        _write_sd93(name="Sunny Ridge")
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        _pin(monkeypatch, cfg)

        root = build_setup(page)

        texts = _texts(root)
        # DISTRICT is satisfied by the pending overlay and FOLDERS validate, so the untested
        # gate step is where the work actually stopped.
        assert setup_screen.FILES_STEP_TITLE in texts
        assert "Step 3 of 6" in texts
        assert _has_button(root, creator_screen.GATE_RUN_LABEL)

    def test_the_form_is_rehydrated_from_the_overlay_on_disk(self, page, monkeypatch) -> None:
        _write_sd93(name="Sunny Ridge")
        cfg = _cfg(creator_pending_sis="sd93custom")  # blank folders → resume lands on FOLDERS
        _pin(monkeypatch, cfg)
        root = build_setup(page)

        _button(root, "Back").on_click(None)  # back to the creator District step

        assert _field(root, creator_screen.CREATOR_NAME_FIELD_LABEL).value == "Sunny Ridge"
        assert _field(root, creator_screen.CREATOR_SD_FIELD_LABEL).value == "93"

    def test_a_token_with_no_mapping_file_self_heals_to_the_standard_walk(self, page, monkeypatch) -> None:
        """The twin of the row above: a token is only half the fact, and the missing half must
        not open a six-step flow around a district that does not exist."""
        cfg = _cfg(creator_pending_sis="sd93custom")  # nothing written
        _pin(monkeypatch, cfg)

        root = build_setup(page)

        assert cfg.creator_pending_sis == "", "the stale token was not cleared"
        assert "Step 1 of 5" in _texts(root), "the standard walk did not resume"
        assert _dropdown(root, "District") is not None


# --------------------------------------------------------------------------- #
# 4. Discard                                                                   #
# --------------------------------------------------------------------------- #
class TestDiscard:
    def test_discard_deletes_clears_invalidates_and_says_so(self, page, monkeypatch, tmp_path) -> None:
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        resets = _spy_reset(monkeypatch)
        _pin(monkeypatch, cfg)
        root = build_setup(page)

        _button(root, creator_screen.CREATOR_DISCARD_LABEL).on_click(None)

        assert not overlay_path("sd93custom").exists(), "the overlay was left behind"
        assert cfg.creator_pending_sis == ""
        assert resets == [1], "a deleted district must leave every picker"
        texts = _texts(root)
        assert setup_screen.CREATOR_DISCARDED_NOTE in texts
        assert "Step 1 of 5" in texts, "the standard walk is back"
        assert _dropdown(root, "District") is not None

    def test_a_discard_with_nothing_written_yet_is_idempotent_success(self, page, monkeypatch) -> None:
        """The positive twin for the deletion above: pressing discard BEFORE the first write
        clears the surface without an error (``delete_overlay``'s ``False`` is not a failure)."""
        cfg = _cfg()
        root = _open_creator(page, monkeypatch, cfg)

        _button(root, creator_screen.CREATOR_DISCARD_LABEL).on_click(None)

        assert setup_screen.CREATOR_DISCARDED_NOTE in _texts(root)
        assert _dropdown(root, "District") is not None


# --------------------------------------------------------------------------- #
# 5. The D9 auto-seed must not answer the question the admin is mid-way through #
# --------------------------------------------------------------------------- #
class TestTheAutoSeedAndAPendingCreator:
    def test_the_seed_does_NOT_fire_while_a_creator_setup_is_pending(self, page, monkeypatch) -> None:
        """Obligation #8. The seed would satisfy the District step with a BUNDLED district and
        resume the admin past the step they are half-way through."""
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", identity_email=SD48_ADMIN, identity_sd_number="48")
        _pin(monkeypatch, cfg)

        root = build_setup(page)

        assert cfg.sis_type == "", "the seed pre-selected a bundled district into a creator flow"
        assert "We've picked" not in _blob(root)
        assert not [d for d in _dropdowns(root) if d.label == "District"]

    def test_the_twin_with_no_token_still_seeds_the_single_visible_district(self, page, monkeypatch) -> None:
        """Without the twin, the row above passes just as well on a seed that never works."""
        _pin(monkeypatch, _cfg(identity_email=SD48_ADMIN, identity_sd_number="48"))

        root = build_setup(page)

        assert _dropdown(root, "District").value == "sd48myedbc"
        assert "We've picked" in _blob(root)


# --------------------------------------------------------------------------- #
# 6. The gate's refusal (the job itself — testable headless)                    #
# --------------------------------------------------------------------------- #
class TestTheGateRefusesWithoutAUsableOutputFolder:
    def _spy_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
        from src.etl import pipeline as pipeline_mod

        calls: list[dict] = []

        def _fake(sis_type, input_path, output_path, **kwargs):  # noqa: ANN001, ANN202
            calls.append({"sis": sis_type, "input": input_path, "output": output_path, **kwargs})
            return pipeline_mod.PipelineResult(entity_counts={"Students": 3})

        monkeypatch.setattr(pipeline_mod, "run_pipeline", _fake)
        return calls

    @pytest.mark.parametrize("output_dir", ["", "   ", "/definitely/not/a/parent/out"])
    def test_a_blank_or_unusable_output_folder_refuses_before_the_pipeline_is_touched(
        self, monkeypatch, output_dir
    ) -> None:
        calls = self._spy_pipeline(monkeypatch)

        with pytest.raises(GateRefused):
            creator_gate_job("sd93custom", input_dir=str(SNAPSHOT_INPUT), output_dir=output_dir)

        assert calls == [], "a test conversion ran without a folder to write to"

    def test_the_twin_with_a_validated_folder_runs_exactly_once_as_a_DRY_run(self, monkeypatch, tmp_path) -> None:
        calls = self._spy_pipeline(monkeypatch)

        result = creator_gate_job("sd93custom", input_dir=str(SNAPSHOT_INPUT), output_dir=str(tmp_path / "out"))

        assert len(calls) == 1
        assert calls[0]["dry_run"] is True, "the gate must never write CSVs"
        assert calls[0]["sis"] == "sd93custom"
        assert result.entity_counts == {"Students": 3}

    def test_the_view_renders_the_bounded_refusal_rather_than_a_raw_error(self, monkeypatch, tmp_path) -> None:
        """The wizard's step order makes this state hard to reach (Folders precedes Your files),
        which is precisely why the refusal is asserted at the seam every host shares: a
        ``GateRefused`` from the worker paints ONE bounded sentence, never an exception."""
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))

        def _refuse(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
            raise GateRefused("A test conversion needs an output folder; none is set.")

        monkeypatch.setattr(creator_screen, "creator_gate_job", _refuse)
        _pin(monkeypatch, cfg)
        root = build_setup(_driving_page())

        _button(root, creator_screen.GATE_RUN_LABEL).on_click(None)

        blob = _blob(root)
        assert creator_screen.GATE_REFUSED_NO_OUTPUT_NOTE in blob
        assert "none is set" not in blob, "the raw exception text reached the screen"
        assert not _has_button(root, creator_screen.GATE_CONFIRM_LABEL), "a refused test cannot offer activation"


# --------------------------------------------------------------------------- #
# 7. A gate run leaves NO Run History row — and DOES leave the diagnostic line  #
# --------------------------------------------------------------------------- #
class TestTheGateRunIsInvisibleToRunHistory:
    def test_no_store_row_while_the_diagnostic_line_IS_logged(self, monkeypatch, tmp_path, caplog) -> None:
        """ONE test, both halves (the no-vacuous-greens twin).

        Drives the REAL ``run_pipeline(dry_run=True)`` over the real SD74 snapshot inputs
        through the REAL gate button, with an ``sd93custom`` overlay whose renames match those
        files. "No Run History row" on its own is satisfied by a run that never happened; the
        ``__DISTRICTSYNC_RUN__`` line is the proof that one did.
        """
        _write_sd93(renames=SD74_RENAMES)
        cfg = _cfg(
            creator_pending_sis="sd93custom",
            input_dir=str(SNAPSHOT_INPUT),
            output_dir=str(tmp_path / "out"),
        )
        _pin(monkeypatch, cfg)
        page = _driving_page()
        root = build_setup(page)
        assert setup_screen.FILES_STEP_TITLE in _texts(root), "the gate step is where an untested district resumes"

        with caplog.at_level(logging.INFO):
            _button(root, creator_screen.GATE_RUN_LABEL).on_click(None)

        blob = _blob(root)
        assert creator_screen.GATE_PASSED_HEADLINE in blob, "the dry run did not complete"
        # ``metric_tile`` upper-cases its caption, so the count row reads "STUDENTS" over "100".
        assert "STUDENTS" in blob, "the per-entity counts did not render"
        # Half 1 — nothing durable: no store row (and the DB was never even created).
        assert read_run_records() == [], "a test conversion appeared in Run History"
        # Half 2 — the diagnostic parity that proves half 1 is not vacuous.
        assert any("__DISTRICTSYNC_RUN__" in record.getMessage() for record in caplog.records)
        # …and no CSVs: a dry run writes nothing at all.
        assert list((tmp_path / "out").glob("*.csv")) == []


# --------------------------------------------------------------------------- #
# 8. Activation — one save, and a hand edit re-closes the gate                  #
# --------------------------------------------------------------------------- #
class _PassingGate:
    """A stand-in for the worker: a dry run that "passed" with plausible counts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, sis_id: str, *, input_dir: str, output_dir: str):  # noqa: ANN204
        from src.etl.pipeline import PipelineResult

        self.calls.append((sis_id, input_dir, output_dir))
        return PipelineResult(entity_counts={"Students": 12, "Classes": 4})


def _wizard_at_the_gate(monkeypatch: pytest.MonkeyPatch, cfg: AppConfig) -> tuple[ft.Control, _PassingGate]:
    """Mount the creator walk on its gate step with a stubbed (passing) test conversion."""
    gate = _PassingGate()
    monkeypatch.setattr(creator_screen, "creator_gate_job", gate)
    _pin(monkeypatch, cfg)
    root = build_setup(_driving_page())
    assert setup_screen.FILES_STEP_TITLE in _texts(root)
    return root, gate


class TestActivation:
    def test_confirm_writes_the_district_clears_the_token_and_records_the_digest_in_ONE_save(
        self, monkeypatch, tmp_path
    ) -> None:
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        resets = _spy_reset(monkeypatch)
        root, gate = _wizard_at_the_gate(monkeypatch, cfg)

        _button(root, creator_screen.GATE_RUN_LABEL).on_click(None)
        assert gate.calls and creator_screen.GATE_PASSED_HEADLINE in _blob(root)
        saves: list[int] = []
        real_save = AppConfig.save
        monkeypatch.setattr(AppConfig, "save", lambda self: (saves.append(1), real_save(self))[1])

        _button(root, creator_screen.GATE_CONFIRM_LABEL).on_click(None)

        assert cfg.sis_type == "sd93custom"
        assert cfg.creator_pending_sis == ""
        assert cfg.creator_verified["sd93custom"] == current_digest("sd93custom")
        assert saves == [1], f"activation must be ONE atomic save, got {len(saves)}"
        assert resets == [1], "the newly active district must reach every picker"
        assert "Step 4 of 6" in _texts(root), "activation did not advance the walk"

    def test_the_confirm_only_appears_once_a_test_has_passed(self, monkeypatch, tmp_path) -> None:
        """The twin: before a run there is nothing to confirm, and the run button is the ONE
        filled primary; after it passes, the confirm takes that tier and the run demotes."""
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        root, _gate = _wizard_at_the_gate(monkeypatch, cfg)

        assert not _has_button(root, creator_screen.GATE_CONFIRM_LABEL)
        assert [c.content for c in _walk(root) if isinstance(c, ft.FilledButton)] == [creator_screen.GATE_RUN_LABEL]

        _button(root, creator_screen.GATE_RUN_LABEL).on_click(None)

        assert [c.content for c in _walk(root) if isinstance(c, ft.FilledButton)] == [
            creator_screen.GATE_CONFIRM_LABEL
        ], "exactly one filled primary, and it is the confirm"
        assert _has_button(root, creator_screen.GATE_RERUN_LABEL)

    def test_a_refused_activation_keeps_the_admin_on_the_step(self, monkeypatch, tmp_path) -> None:
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        monkeypatch.setattr(AppConfig, "activate_creator_config", lambda _self, **_kw: False)
        root, _gate = _wizard_at_the_gate(monkeypatch, cfg)
        _button(root, creator_screen.GATE_RUN_LABEL).on_click(None)

        _button(root, creator_screen.GATE_CONFIRM_LABEL).on_click(None)

        assert cfg.sis_type == "", "a refused save must not leave a district applied"
        texts = _texts(root)
        assert creator_screen.CREATOR_ACTIVATE_FAILED_NOTE in texts
        assert setup_screen.FILES_STEP_TITLE in texts, "a failure must never advance silently"

    def test_a_hand_edit_of_the_overlay_re_closes_the_gate(self, monkeypatch, tmp_path) -> None:
        """Staleness (acceptance 4): the recorded fact is keyed on the RESOLVED config, so an
        edit anyone makes by hand — vendor, admin or support — asks for another test run."""
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        root, _gate = _wizard_at_the_gate(monkeypatch, cfg)
        _button(root, creator_screen.GATE_RUN_LABEL).on_click(None)
        _button(root, creator_screen.GATE_CONFIRM_LABEL).on_click(None)
        assert creator_screen.creator_gate_current(cfg, "sd93custom") is True, "the positive half"

        path = overlay_path("sd93custom")
        path.write_text(path.read_text(encoding="utf-8").replace("SD93 - Creator Test", "SD93 - Edited"), "utf-8")

        assert creator_screen.creator_gate_current(cfg, "sd93custom") is False
        # …and the re-closed gate is where a re-mount lands the admin.
        _pin(monkeypatch, cfg)
        cfg.creator_pending_sis = "sd93custom"  # a resumed setup for the same district
        assert setup_screen.FILES_STEP_TITLE in _texts(build_setup(MagicMock()))


class TestTheStalenessNote:
    """Why another test run is being asked for — on the Files step and nowhere else.

    The owner's decision (open question 2): S3 surfaces it HERE, where the re-test is being
    asked for. Home answers "did the roster sync?", and a config that still converts is not
    a fault.
    """

    def test_an_overlay_written_by_a_different_build_says_so(self, monkeypatch, tmp_path) -> None:
        _write_sd93()
        path = overlay_path("sd93custom")
        path.write_text(
            path.read_text(encoding="utf-8").replace(f"app_version: {app_version()}", "app_version: 0.0.1-old"),
            "utf-8",
        )
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        _pin(monkeypatch, cfg)

        assert creator_screen.GATE_STALE_VERSION_NOTE in _texts(build_setup(MagicMock()))

    def test_the_twin_an_overlay_this_build_wrote_says_nothing(self, monkeypatch, tmp_path) -> None:
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        _pin(monkeypatch, cfg)

        texts = _texts(build_setup(MagicMock()))

        assert creator_screen.GATE_STALE_VERSION_NOTE not in texts
        assert creator_screen.GATE_STALE_BASE_NOTE not in texts, "unknown provenance is never staleness"


# --------------------------------------------------------------------------- #
# 9. The finish line is unreachable until the district is genuinely active      #
# --------------------------------------------------------------------------- #
class TestTheFinishLineIsGatedOnActivation:
    def test_the_gate_step_blocks_the_walk_until_a_test_has_passed(self, monkeypatch, tmp_path) -> None:
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        root, _gate = _wizard_at_the_gate(monkeypatch, cfg)

        assert _button(root, "Continue").disabled is True, "an untested district walked on"

        _button(root, creator_screen.GATE_RUN_LABEL).on_click(None)
        _button(root, creator_screen.GATE_CONFIRM_LABEL).on_click(None)
        _button(root, "Back").on_click(None)  # back to the (now passed) gate step

        assert _button(root, "Continue").disabled is False

    def test_a_tested_but_never_activated_district_reaches_finish_DISABLED_with_a_reason(
        self, monkeypatch, tmp_path
    ) -> None:
        """The honest handling of ``derive_flow``'s one asymmetric state: every other step
        satisfied (the digest is recorded and current) while ``sis_type`` still points nowhere.
        The Finish button stays shut — never a silent flip — and says what is missing."""
        _write_sd93()
        cfg = _cfg(
            creator_pending_sis="sd93custom",
            creator_verified={"sd93custom": current_digest("sd93custom") or ""},
            **_valid_folders(tmp_path),
        )
        _pin(monkeypatch, cfg)
        root = build_setup(MagicMock())  # resume: Delivery (every earlier step satisfied)

        _button(root, "Set up later").on_click(None)  # defer delivery
        _button(root, "Set up later").on_click(None)  # defer the schedule → Finish

        texts = _texts(root)
        assert "Step 6 of 6" in texts
        assert setup_screen.CREATOR_FINISH_NEEDS_GATE_NOTE in texts
        assert _button(root, "Finish setup").disabled is True
        assert cfg.setup_completed is False

    def test_the_twin_once_activated_the_finish_line_opens_and_finishes(self, monkeypatch, tmp_path) -> None:
        """Acceptance 1 end to end, INCLUDING its last clause: the finish line is pressed and
        the four facts are read back from DISK, not from the instance the wizard was holding —
        an in-memory assertion would pass on a save that never landed."""
        _write_sd93()
        real_load = AppConfig.load  # captured BEFORE the pin, so the read-back is the real one
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        root, _gate = _wizard_at_the_gate(monkeypatch, cfg)
        _button(root, creator_screen.GATE_RUN_LABEL).on_click(None)
        _button(root, creator_screen.GATE_CONFIRM_LABEL).on_click(None)  # → Delivery

        _button(root, "Set up later").on_click(None)  # defer delivery
        _button(root, "Set up later").on_click(None)  # defer the schedule → Finish

        texts = _texts(root)
        assert "Step 6 of 6" in texts
        assert setup_screen.CREATOR_FINISH_NEEDS_GATE_NOTE not in texts
        assert _button(root, "Finish setup").disabled is False

        _button(root, "Finish setup").on_click(None)

        fresh = real_load()
        assert fresh.setup_completed is True, "the finish line did not record completion on disk"
        assert fresh.sis_type == "sd93custom"
        assert fresh.creator_pending_sis == "", "the resume token outlived the setup"
        assert fresh.creator_verified["sd93custom"] == current_digest("sd93custom")


# --------------------------------------------------------------------------- #
# 10. Copy: identification-not-authentication, and no promise of a later slice  #
# --------------------------------------------------------------------------- #
def _creator_copy_constants() -> dict[str, str]:
    """Every creator/gate copy constant on the module, by name (never a hand-typed list)."""
    prefixes = ("CREATOR_", "FILES_", "GATE_")
    found = {
        name: value
        for name, value in vars(creator_screen).items()
        if name.startswith(prefixes) and isinstance(value, str)
    }
    found["creator_shipped_note"] = creator_screen.creator_shipped_note("48")
    return found


class TestTheCopy:
    def test_the_constant_index_is_not_empty(self) -> None:
        """The falsification twin for the two sweeps below: a name-derived index that matched
        nothing would pass every assertion in this class."""
        assert len(_creator_copy_constants()) >= 25

    @pytest.mark.parametrize("name", sorted(_creator_copy_constants()))
    def test_no_constant_carries_banned_identity_vocabulary(self, name) -> None:
        """0038's promise, extended: "verify"/"verified" IS banned, so every gate string says
        test / test run / checked."""
        _assert_no_banned_vocabulary(_creator_copy_constants()[name], name)

    @pytest.mark.parametrize("name", sorted(_creator_copy_constants()))
    def test_no_constant_promises_a_later_slice(self, name) -> None:
        lowered = _creator_copy_constants()[name].lower()
        for probe in FORBIDDEN_PROMISES:
            assert probe not in lowered, f"{name} promises {probe!r}, which S3 does not ship"

    def test_the_rendered_creator_forms_carry_neither(self, page, monkeypatch) -> None:
        root = _open_creator(page, monkeypatch, _cfg())
        _tick(_checkbox(root, creator_screen.CREATOR_GRADES_INHERIT_LABEL), False)  # the grades card too

        blob = _blob(root)

        _assert_no_banned_vocabulary(blob, "the rendered creator forms")
        for probe in FORBIDDEN_PROMISES:
            assert probe not in blob.lower(), f"the creator forms promise {probe!r}"

    def test_the_rendered_gate_step_carries_neither_before_or_after_a_run(self, monkeypatch, tmp_path) -> None:
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        root, _gate = _wizard_at_the_gate(monkeypatch, cfg)

        before = _blob(root)
        _button(root, creator_screen.GATE_RUN_LABEL).on_click(None)
        after = _blob(root)

        for where, blob in (("before the test run", before), ("after the test run", after)):
            _assert_no_banned_vocabulary(blob, f"the rendered gate step {where}")
            for probe in FORBIDDEN_PROMISES:
                assert probe not in blob.lower(), f"the gate step {where} promises {probe!r}"

    def test_the_files_step_title_is_single_sourced(self) -> None:
        """The step-title dict and the constant must not drift (one title, one source)."""
        from src.ui_flet.setup_flow import SetupStep

        assert setup_screen._STEP_TITLES[SetupStep.FILES] == setup_screen.FILES_STEP_TITLE


# --------------------------------------------------------------------------- #
# 11. The host seam (§3.5) — one surface, two hosts                             #
# --------------------------------------------------------------------------- #
class TestTheHostSeam:
    def test_the_wizard_passes_exactly_the_named_callbacks(self, page, monkeypatch) -> None:
        """S6's Mapping surface is the second host; naming the callbacks now is what stops it
        becoming a second wizard."""
        seen: list[dict] = []
        real = setup_screen.build_creator

        def _spy(page_arg, **kwargs):  # noqa: ANN001, ANN003, ANN202
            seen.append(kwargs)
            return real(page_arg, **kwargs)

        monkeypatch.setattr(setup_screen, "build_creator", _spy)

        _open_creator(page, monkeypatch, _cfg())

        assert seen, "the creator branch did not route through the host seam"
        assert set(seen[-1]) == {"cfg", "sis_id", "form", "on_written", "on_activated", "on_discarded", "stage"}
        for name in ("on_written", "on_activated", "on_discarded"):
            assert callable(seen[-1][name]), name
        assert seen[-1]["stage"] == "forms"

    def test_build_creator_renders_identically_from_a_second_host(self, page, monkeypatch) -> None:
        """The ``_finish`` two-mount equivalence precedent: a seam whose two hosts render
        different surfaces is two surfaces. The wizard's own surface is captured AT the seam
        (the spy's return value), so the comparison is the creator control itself rather than
        the step chrome wrapped around it."""
        cfg = _cfg()
        built: list[ft.Control] = []
        real = setup_screen.build_creator

        def _spy(page_arg, **kwargs):  # noqa: ANN001, ANN003, ANN202
            control = real(page_arg, **kwargs)
            built.append(control)
            return control

        monkeypatch.setattr(setup_screen, "build_creator", _spy)
        _open_creator(page, monkeypatch, cfg)

        standalone = real(
            MagicMock(),
            cfg=cfg,
            sis_id="",
            form=setup_screen.creator_form_for_new(cfg),
            on_written=lambda *_a: None,
            on_activated=lambda: None,
            on_discarded=lambda: None,
        )

        assert built, "the wizard did not build through the seam"
        assert _texts(standalone) == _texts(built[-1])

    def test_the_gate_stage_is_the_same_seam(self, monkeypatch, tmp_path) -> None:
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        seen: list[dict] = []
        real = setup_screen.build_creator

        def _spy(page_arg, **kwargs):  # noqa: ANN001, ANN003, ANN202
            seen.append(kwargs)
            return real(page_arg, **kwargs)

        monkeypatch.setattr(setup_screen, "build_creator", _spy)
        _pin(monkeypatch, cfg)

        build_setup(MagicMock())

        assert seen[-1]["stage"] == "files"
        assert seen[-1]["sis_id"] == "sd93custom"
        assert isinstance(seen[-1]["form"], CreatorForm)


# --------------------------------------------------------------------------- #
# 12. Mount smoke — no creator surface may fall to the ErrorCard floor          #
# --------------------------------------------------------------------------- #
class TestTheMountsRender:
    def _assert_no_floor(self, monkeypatch: pytest.MonkeyPatch, build) -> ft.Control:  # noqa: ANN001
        real = components.ErrorCard
        floor: dict[str, object] = {"obj": None}

        def _spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            floor["obj"] = real(*args, **kwargs)
            return floor["obj"]

        monkeypatch.setattr(components, "ErrorCard", _spy)
        out = build()
        assert isinstance(out, ft.Control)
        assert floor["obj"] is None, "a creator surface fell to its ErrorCard floor"
        return out

    def test_the_creator_forms_mount(self, page, monkeypatch) -> None:
        self._assert_no_floor(monkeypatch, lambda: _open_creator(page, monkeypatch, _cfg()))

    def test_the_gate_step_mounts(self, monkeypatch, tmp_path) -> None:
        _write_sd93()
        cfg = _cfg(creator_pending_sis="sd93custom", **_valid_folders(tmp_path))
        _pin(monkeypatch, cfg)
        self._assert_no_floor(monkeypatch, lambda: build_setup(MagicMock()))
