"""The export affordance on Mapping's current-mapping card (plan 0044 S7 §7.1).

When a conversion looks wrong, support asks for the district's mapping — so a district that
authored its OWN mapping must be able to SEE the file: Mapping's CURRENT card carries
``MAPPING_EXPORT_LABEL`` at text tier, revealing the path, a copy of it and its FOLDER.

Every rule this file pins is an ABSENCE with its positive twin in the same class, because
each absence is exactly how the affordance would go quietly wrong:

* no door on a SHIPPED card — twinned against the user-authored card **in the same render**
  (one door, on the current card, however many cards are on screen);
* no door when the overlay file is NOT on disk (hand-deleted) or when ``overlay_path``
  RAISES — twinned against the mount that offers one, so "absent" never means "broken";
* "Open folder" receives the FOLDER, twinned with the assertion that it is NOT the file;
* the path reaches NO log record, twinned with the assertion that it really is on screen (a
  log sweep over a mechanism that never ran is a vacuous green).

Driven through the REAL ``build_mapping`` mount against a REAL overlay written into the
per-test ``isolated_user_profile``, because the door's whole precondition is a file on disk.

The tree helpers and the two banned-vocabulary sweeps are IMPORTED, never re-typed: a second
hand-written walker (or ban list) is one that drifts from the rule it is meant to enforce.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig, config_file_path
from src.config.authoring import OverlaySpec, overlay_path, write_overlay
from src.ui_flet import components, convert_output, mapping_catalog
from src.ui_flet.screens import mapping as mapping_screen
from src.ui_flet.screens.mapping import build_mapping

# Single-sourced sweeps (the launch page's authentication ban + the creator flow's
# vague-future ban) and the S6 gate file's tree helpers — imported so ONE walker and ONE of
# each ban list serve every Mapping assertion.
from tests.test_ui_flet_activation_gate import (
    UNMATCHED,
    _button,
    _button_labels,
    _filled,
    _install,
    _pick,
    _texts,
    _walk,
)
from tests.test_ui_flet_creator_flow import BANNED_COPY_WORDS
from tests.test_ui_flet_identity_page import _assert_no_banned_vocabulary

CUSTOM_ID = "sd93custom"
CUSTOM_NAME = "SD93 - Export Test"
SECOND_ID = "sd94custom"
SECOND_NAME = "SD94 - Export Twin"
SHIPPED_ID = "sd48myedbc"

#: Every constant the export adds, by NAME — so a rename fails loudly rather than dropping
#: out of the sweeps below (the S4 lesson: a sweep going quiet is worse than a red one).
S7_EXPORT_CONSTANTS = (
    "MAPPING_EXPORT_LABEL",
    "MAPPING_EXPORT_TITLE",
    "MAPPING_EXPORT_NOTE",
    "MAPPING_EXPORT_OPEN_LABEL",
)


# --------------------------------------------------------------------------- #
# Fixtures + helpers                                                           #
# --------------------------------------------------------------------------- #
@pytest.fixture
def page() -> MagicMock:
    return MagicMock()


def _write(sd_number: int, name: str) -> Path:
    """A REAL overlay in the isolated user mappings dir → ``origin == "user"``."""
    path = write_overlay(
        OverlaySpec(
            sd_number=sd_number,
            district_name=name,
            district_domains=(),
            base="myedbc",
        ),
        overwrite=True,
    )
    # The write landed after the autouse fixture's reset, so the memo must be dropped — the
    # app's own rule (``mapping_catalog.reset_catalog_cache``'s invalidation contract).
    mapping_catalog.reset_catalog_cache()
    return path


@pytest.fixture
def overlay(isolated_user_profile: Path) -> str:  # noqa: ARG001 - the isolation seam
    _write(93, CUSTOM_NAME)
    return CUSTOM_ID


def _mount(page: MagicMock, cfg: AppConfig) -> ft.Control:
    return build_mapping(page, app_config=cfg, on_navigate=lambda _dest: None)


def _export_triggers(tree: ft.Control) -> list[ft.Control]:
    """Every export TRIGGER CONTROL on screen — a LIST, deliberately.

    ``_button_labels`` returns a set, so a second door on a second card would collapse into
    the same label and "exactly one door" would be vacuously true (caught by mutating the
    pending card to carry one).
    """
    return [
        control
        for control in _walk(tree)
        if isinstance(control, (ft.TextButton, ft.OutlinedButton, ft.FilledButton))
        and control.content == mapping_screen.MAPPING_EXPORT_LABEL
    ]


def _door_labels(tree: ft.Control) -> list[str]:
    """The label of every export door on screen, in render order (one per control)."""
    return [str(control.content) for control in _export_triggers(tree)]


def _copy_buttons(tree: ft.Control) -> list[ft.IconButton]:
    return [
        control
        for control in _walk(tree)
        if isinstance(control, ft.IconButton) and control.icon == ft.Icons.CONTENT_COPY_ROUNDED
    ]


def _reveal(tree: ft.Control) -> None:
    _button(tree, mapping_screen.MAPPING_EXPORT_LABEL).on_click(None)


# --------------------------------------------------------------------------- #
# 1. Where the door is offered — and where it is not                           #
# --------------------------------------------------------------------------- #
class TestWhoGetsTheDoor:
    def test_a_user_authored_CURRENT_card_offers_it_at_TEXT_tier(self, page, tmp_path, overlay) -> None:
        cfg = _install(tmp_path, sis_type=overlay)

        tree = _mount(page, cfg)

        trigger = _button(tree, mapping_screen.MAPPING_EXPORT_LABEL)
        assert isinstance(trigger, ft.TextButton), "the export door is not at text tier"
        # The positive twin for the card itself: it really is the user-authored card.
        assert mapping_screen.CUSTOM_ORIGIN_NOTE in _texts(tree)

    def test_a_SHIPPED_current_card_offers_NOTHING(self, page, tmp_path, overlay) -> None:
        """The twin, on the SAME install: the overlay is on disk, it is just not the district
        in use — so an absent door can only be the origin rule, never a broken write."""
        cfg = _install(tmp_path, sis_type=SHIPPED_ID)

        tree = _mount(page, cfg)

        assert _door_labels(tree) == []
        assert mapping_screen.CUSTOM_ORIGIN_NOTE not in _texts(tree), "the shipped card claims a user origin"

    def test_the_ORIGIN_rule_is_load_bearing_not_just_the_file_check(self, page, monkeypatch, tmp_path) -> None:
        """The two preconditions are separately necessary.

        On a normal install a shipped id has no file in the user mappings dir either, so the
        test above cannot tell which rule suppressed the door. Here the path resolves to a
        file that DOES exist for every id, leaving ``origin == "user"`` as the only thing that
        can withhold the door from a mapping that is ours — mutating the origin check away
        makes this red while every other row stays green.
        """
        real = _write(93, CUSTOM_NAME)
        monkeypatch.setattr(mapping_screen, "overlay_path", lambda _sis: real)
        cfg = _install(tmp_path, sis_type=SHIPPED_ID)

        tree = _mount(page, cfg)

        assert real.is_file(), "the stat check would have suppressed the door on its own"
        assert _door_labels(tree) == []

    def test_exactly_ONE_door_renders_however_many_user_cards_are_on_screen(self, page, tmp_path, overlay) -> None:
        """You export the district this computer CONVERTS: the "Switch to" card gets no door,
        even when the row picked is a mapping authored here too — two paths on screen would be
        two answers to "which file?"."""
        _write(94, SECOND_NAME)
        cfg = _install(tmp_path, sis_type=overlay)
        tree = _mount(page, cfg)
        assert _door_labels(tree) == [mapping_screen.MAPPING_EXPORT_LABEL], "the mount does not offer one door"

        _pick(_dropdown_of(tree), SECOND_ID)

        # The pending card is now a SECOND user-authored mapping (its own name and change
        # door prove it rendered), and there is still exactly one export door: the current
        # card's.
        assert SECOND_NAME in _texts(tree), "the second user-authored card never rendered"
        assert mapping_screen.MAPPING_EDIT_LABEL in _button_labels(tree)
        assert _door_labels(tree) == [mapping_screen.MAPPING_EXPORT_LABEL]

    def test_a_hand_DELETED_overlay_offers_no_door_and_no_error(self, page, tmp_path, overlay) -> None:
        """TOTAL over the file: the memoised catalog still says "authored here", so the
        ``is_file()`` check is the only thing standing between an admin and a revealed path to
        a file that is not there. Mounted BEFORE the delete as the twin."""
        cfg = _install(tmp_path, sis_type=overlay)
        assert _door_labels(_mount(page, cfg)) == [mapping_screen.MAPPING_EXPORT_LABEL], "the twin never rendered"

        overlay_path(overlay).unlink()
        tree = _mount(page, cfg)  # the catalog memo is deliberately NOT reset — origin is still "user"

        assert _door_labels(tree) == []
        # ...and the surface is still the Mapping view, not its never-crash floor.
        assert "Switch mapping" in _texts(tree)

    def test_an_unresolvable_id_offers_no_door_and_no_error(self, page, monkeypatch, tmp_path, overlay) -> None:
        """``overlay_path`` VALIDATES the id and can raise. Any raise ⇒ nothing rendered."""
        cfg = _install(tmp_path, sis_type=overlay)
        assert _door_labels(_mount(page, cfg)) == [mapping_screen.MAPPING_EXPORT_LABEL], "the twin never rendered"

        def _boom(_sis: str) -> Path:
            raise ValueError("invalid sis type")

        monkeypatch.setattr(mapping_screen, "overlay_path", _boom)
        tree = _mount(page, cfg)

        assert _door_labels(tree) == []
        assert "Switch mapping" in _texts(tree)


def _dropdown_of(tree: ft.Control) -> ft.Dropdown:
    for control in _walk(tree):
        if isinstance(control, ft.Dropdown):
            return control
    raise AssertionError("no district Dropdown on the Mapping view")


# --------------------------------------------------------------------------- #
# 2. What the reveal shows                                                     #
# --------------------------------------------------------------------------- #
class TestTheRevealedBlock:
    def test_pressing_it_reveals_the_path_a_copy_control_and_Open_folder(self, page, tmp_path, overlay) -> None:
        cfg = _install(tmp_path, sis_type=overlay)
        tree = _mount(page, cfg)
        path = str(overlay_path(overlay))
        assert path not in _texts(tree), "the path is on screen before anyone asked for it"

        _reveal(tree)

        texts = _texts(tree)
        assert mapping_screen.MAPPING_EXPORT_TITLE in texts
        assert path in texts, "the reveal does not name the real overlay path"
        assert mapping_screen.MAPPING_EXPORT_NOTE in texts
        assert len(_copy_buttons(tree)) == 1, "the reveal carries no copy control"
        opener = _button(tree, mapping_screen.MAPPING_EXPORT_OPEN_LABEL)
        assert isinstance(opener, ft.OutlinedButton), "Open folder is not at secondary tier"
        # Progressive disclosure: the trigger SWAPPED for the block (no dead duplicate).
        assert _export_triggers(tree) == []

    def test_the_reveal_is_never_a_YAML_text_dump(self, page, tmp_path, overlay) -> None:
        """The decided shape: reveal the FILE, never its content — the block may not carry the
        overlay's own text (a config dumped on screen is unreadable and unsupportable)."""
        cfg = _install(tmp_path, sis_type=overlay)
        tree = _mount(page, cfg)
        body = overlay_path(overlay).read_text(encoding="utf-8")

        _reveal(tree)

        blob = "\n".join(_texts(tree))
        assert "authored_with" not in blob, "the overlay's YAML text is on screen"
        assert body not in blob

    def test_the_copy_control_carries_the_PATH(self, page, monkeypatch, tmp_path, overlay) -> None:
        """A locked-down server may have no other route to the value, so the one-click path
        must copy the path itself — never a label, never the district name."""
        copied: list[str] = []
        real = components.copy_button

        def _spy(page_arg, text, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
            copied.append(text)
            return real(page_arg, text, **kwargs)

        monkeypatch.setattr(components, "copy_button", _spy)
        cfg = _install(tmp_path, sis_type=overlay)
        tree = _mount(page, cfg)

        _reveal(tree)

        assert copied == [str(overlay_path(overlay))]

    def test_Open_folder_opens_the_FOLDER_never_the_file(self, page, monkeypatch, tmp_path, overlay) -> None:
        """Through ``convert_output.open_folder`` — the ONE per-OS dispatcher — and with the
        DIRECTORY: opening the file itself would hand a district's mapping to whatever the OS
        has registered for ``.yaml``."""
        opened: list[str] = []
        monkeypatch.setattr(convert_output, "open_folder", lambda path: opened.append(path) or True)
        cfg = _install(tmp_path, sis_type=overlay)
        tree = _mount(page, cfg)
        _reveal(tree)

        _button(tree, mapping_screen.MAPPING_EXPORT_OPEN_LABEL).on_click(None)

        target = overlay_path(overlay)
        assert opened == [str(target.parent)]
        assert opened != [str(target)], "the file was opened, not its folder"
        assert not opened[0].endswith(".yaml")

    def test_the_reveal_writes_NOTHING_to_config_json(self, page, tmp_path, overlay) -> None:
        """A disclosure toggle is not a district's setting: it lives in the view closure."""
        cfg = _install(tmp_path, sis_type=overlay)
        before = json.loads(config_file_path().read_text(encoding="utf-8"))
        tree = _mount(page, cfg)

        _reveal(tree)

        assert json.loads(config_file_path().read_text(encoding="utf-8")) == before


# --------------------------------------------------------------------------- #
# 3. ONE filled primary — before AND after the reveal                          #
# --------------------------------------------------------------------------- #
class TestOneFilledPrimarySurvivesTheReveal:
    def test_the_filled_list_is_unchanged_by_the_reveal(self, page, tmp_path, overlay) -> None:
        """Text trigger, secondary opener: the Switch card's Apply stays the screen's ONE
        filled primary in the revealed state too (S6's list equality, extended)."""
        cfg = _install(tmp_path, sis_type=overlay)
        tree = _mount(page, cfg)
        assert _filled(tree) == ["Use this mapping"]

        _reveal(tree)

        assert _filled(tree) == ["Use this mapping"]


# --------------------------------------------------------------------------- #
# 4. The copy sweeps + the log sweep                                           #
# --------------------------------------------------------------------------- #
class TestTheExportCopy:
    def _index(self) -> dict[str, str]:
        found = {name: getattr(mapping_screen, name) for name in S7_EXPORT_CONSTANTS}
        assert all(isinstance(value, str) and value for value in found.values()), found
        return found

    def test_every_named_constant_exists(self) -> None:
        """The falsification twin: an index that matched nothing would pass both sweeps."""
        assert len(self._index()) == len(S7_EXPORT_CONSTANTS) == 4

    @pytest.mark.parametrize("name", sorted(S7_EXPORT_CONSTANTS))
    def test_no_constant_carries_banned_identity_vocabulary(self, name) -> None:
        _assert_no_banned_vocabulary(self._index()[name], name)

    @pytest.mark.parametrize("name", sorted(S7_EXPORT_CONSTANTS))
    def test_no_constant_promises_a_vague_future(self, name) -> None:
        value = self._index()[name].lower()
        for probe in BANNED_COPY_WORDS:
            assert probe not in value, f"{name} promises {probe!r}"

    def test_the_note_says_what_the_file_is_without_inviting_a_hand_edit(self) -> None:
        """S6 deliberately left standing the promise that the app never rewrites a file an
        admin edited by hand; a note that suggested a text editor would retire it."""
        note = mapping_screen.MAPPING_EXPORT_NOTE.lower()

        assert "file" in note, "the positive twin: it does name the thing being shown"
        for invitation in ("edit", "editor", "change it", "open it in"):
            assert invitation not in note, f"the note invites {invitation!r}: {note!r}"

    def test_the_rendered_block_carries_neither_ban(self, page, tmp_path, overlay) -> None:
        cfg = _install(tmp_path, sis_type=overlay)
        tree = _mount(page, cfg)
        _reveal(tree)

        blob = "\n".join(_texts(tree))
        assert mapping_screen.MAPPING_EXPORT_TITLE in blob, "the positive twin: the block really rendered"
        _assert_no_banned_vocabulary(blob, "Mapping — the revealed export block")
        for probe in BANNED_COPY_WORDS:
            assert probe not in blob.lower(), f"the revealed block promises {probe!r}"


class TestThePathIsShownButNeverLogged:
    def test_no_log_record_carries_the_path(self, page, monkeypatch, caplog, tmp_path, overlay) -> None:
        """The path carries the OS account name. It is shown and copied by the admin, and
        interpolated into NO log line — including ``open_folder``'s dispatch."""
        opened: list[str] = []
        monkeypatch.setattr(convert_output, "open_folder", lambda path: opened.append(path) or True)
        cfg = _install(tmp_path, sis_type=overlay)
        target = overlay_path(overlay)

        with caplog.at_level(logging.DEBUG):
            caplog.clear()  # the overlay WRITE is setup, not the act under test
            tree = _mount(page, cfg)
            _reveal(tree)
            _button(tree, mapping_screen.MAPPING_EXPORT_OPEN_LABEL).on_click(None)

        # The positive control: the mechanism really ran (a log sweep over a reveal that never
        # happened is a vacuous green).
        assert str(target) in _texts(tree)
        assert opened == [str(target.parent)]
        for record in caplog.records:
            line = record.getMessage()
            assert str(target) not in line, f"a log record carries the mapping path: {line!r}"
            assert str(target.parent) not in line, f"a log record carries the mappings dir: {line!r}"

    def test_the_absent_door_reports_a_path_free_reason(self, page, monkeypatch, caplog, tmp_path, overlay) -> None:
        """The twin for the sweep above on the failure branch: a raise is logged (so the door
        is never silently missing for a reason nobody can find) with no path in the line."""
        cfg = _install(tmp_path, sis_type=overlay)
        target = overlay_path(overlay)

        def _boom(_sis: str) -> Path:
            raise ValueError("invalid sis type")

        monkeypatch.setattr(mapping_screen, "overlay_path", _boom)
        with caplog.at_level(logging.DEBUG):
            caplog.clear()
            tree = _mount(page, cfg)

        assert _door_labels(tree) == []
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "an unresolvable mapping file was not reported at all"
        for line in warnings:
            assert str(target) not in line
            assert str(target.parent) not in line
            assert UNMATCHED not in line
