"""The verified-fact check at BOTH refusal sites (plan 0044 S6 §6.1).

Mapping's "Use this mapping" and the Settings folders card's "Save folders & district" are
two of the four writers of ``AppConfig.sis_type``. For a mapping authored on THIS computer
each now consults the pure ``config_editor.activation_allowed`` BEFORE any write, so no app
surface can switch this install onto a district it set up itself and never tested.

**One file for two screens, deliberately.** They are ONE rule, and a file per screen is how
two behaviours drift: the twin pairs here (a shipped row applies/saves · a user row whose
digest is current applies/saves) sit beside the refusals they make meaningful, and the
absence assertions — "``AppConfig.save`` was never called", "``reconcile()`` never ran",
"the bytes on disk are unchanged" — each have their positive twin in the same class.

Driven through the REAL ``build_mapping`` / ``build_setup`` mounts against a REAL overlay and
a REAL ``config.json`` in the per-test ``isolated_user_profile``, because a mocked save cannot
fail — or refuse — the way a real one can.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig, config_file_path
from src.config.authoring import OverlaySpec, current_digest, write_overlay
from src.ui_flet import mapping_catalog
from src.ui_flet.screens import mapping as mapping_screen
from src.ui_flet.screens import setup as setup_screen
from src.ui_flet.screens.mapping import build_mapping
from src.ui_flet.screens.setup import build_setup

#: An address at no shipped district's domain, so every district is offered and the pickers
#: are not scoped away from the row a test is about to choose.
UNMATCHED = "roster.admin@example.net"
CUSTOM_ID = "sd93custom"
CUSTOM_NAME = "SD93 - Gate Test"
SHIPPED_ID = "sd48myedbc"
OTHER_SHIPPED_ID = "sd74myedbc"
STALE_DIGEST = "b" * 64


# --------------------------------------------------------------------------- #
# Tree helpers                                                                 #
# --------------------------------------------------------------------------- #
def _walk(control):  # noqa: ANN001, ANN202 - an untyped Flet tree
    yield control
    children: list[object] = []
    kids = getattr(control, "controls", None)
    if isinstance(kids, list):
        children.extend(kids)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        children.append(content)
    for child in children:
        if isinstance(child, ft.Control):
            yield from _walk(child)


def _texts(tree) -> list[str]:  # noqa: ANN001
    found: list[str] = []
    for control in _walk(tree):
        for attr in ("value", "label", "helper", "hint_text", "content", "tooltip"):
            item = getattr(control, attr, None)
            if isinstance(item, str):
                found.append(item)
    return found


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


def _dropdown(tree, label: str) -> ft.Dropdown:  # noqa: ANN001
    for control in _walk(tree):
        if isinstance(control, ft.Dropdown) and control.label == label:
            return control
    raise AssertionError(f"no Dropdown labelled {label!r}")


def _pick(dropdown: ft.Dropdown, value: str) -> None:
    """Choose an option the way Flet does: set the value, then fire ``on_select``."""
    keys = [option.key for option in dropdown.options or []]
    assert value in keys, f"{value!r} is not offered; found {keys}"
    dropdown.value = value
    if dropdown.on_select is not None:
        event = MagicMock()
        event.control.value = value
        dropdown.on_select(event)


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def page() -> MagicMock:
    return MagicMock()


@pytest.fixture
def overlay(monkeypatch: pytest.MonkeyPatch, isolated_user_profile: Path) -> str:  # noqa: ARG001
    """A REAL ``sd93custom`` overlay in the isolated user mappings dir → ``origin == "user"``."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    write_overlay(
        OverlaySpec(
            sd_number=93,
            district_name=CUSTOM_NAME,
            district_domains=(),
            base="myedbc",
        ),
        overwrite=True,
    )
    mapping_catalog.reset_catalog_cache()  # the write landed after the autouse fixture's reset
    return CUSTOM_ID


def _folders(tmp_path: Path) -> dict[str, str]:
    in_dir = tmp_path / "in"
    in_dir.mkdir(exist_ok=True)
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    return {"input_dir": str(in_dir), "output_dir": str(out_dir)}


def _install(tmp_path: Path, *, sis_type: str = SHIPPED_ID, verified: dict[str, str] | None = None) -> AppConfig:
    """A configured install ON DISK, then loaded back — the state both Saves start from."""
    AppConfig(
        setup_completed=True,
        sis_type=sis_type,
        identity_email=UNMATCHED,
        creator_verified=dict(verified or {}),
        **_folders(tmp_path),
    ).save()
    return AppConfig.load()


def _on_disk() -> dict:
    return json.loads(config_file_path().read_text(encoding="utf-8"))


def _spy_save(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every ``AppConfig.save`` — and CALL THROUGH, so the twins really persist."""
    calls: list[str] = []
    real = AppConfig.save

    def _spy(self: AppConfig) -> None:
        calls.append(self.sis_type)
        real(self)

    monkeypatch.setattr(AppConfig, "save", _spy)
    return calls


# --------------------------------------------------------------------------- #
# 1. Mapping's Apply                                                           #
# --------------------------------------------------------------------------- #
class TestMappingApply:
    def _mount(self, page: MagicMock, cfg: AppConfig) -> ft.Control:
        return build_mapping(page, app_config=cfg, on_navigate=lambda _dest: None)

    def test_a_stale_user_row_is_REFUSED_and_nothing_is_written(self, page, monkeypatch, tmp_path, overlay) -> None:
        cfg = _install(tmp_path, verified={overlay: STALE_DIGEST})
        saves = _spy_save(monkeypatch)
        tree = self._mount(page, cfg)

        _pick(_dropdown(tree, "Roster mapping"), overlay)
        _button(tree, "Use this mapping").on_click(None)

        assert mapping_screen.MAPPING_NEEDS_TEST_NOTE in _texts(tree)
        assert mapping_screen.MAPPING_NEEDS_TEST_HEADLINE in _texts(tree)
        assert saves == [], "a refused Apply reached AppConfig.save"
        assert _on_disk()["sis_type"] == SHIPPED_ID, "the district on disk changed anyway"
        # The fix is one press away, at secondary tier (the screen's filled primary is Apply).
        door = _button(tree, mapping_screen.MAPPING_EDIT_LABEL)
        assert isinstance(door, ft.OutlinedButton)
        # ...and nothing claims the switch happened.
        assert not any(text.startswith("Now using") for text in _texts(tree))

    def test_the_refusals_change_door_opens_the_panel_on_the_TEST(self, page, monkeypatch, tmp_path, overlay) -> None:
        """The refusal says "run one" — so its door must land on the surface that runs it,
        not back at the four questions."""
        cfg = _install(tmp_path, verified={overlay: STALE_DIGEST})
        _spy_save(monkeypatch)
        tree = self._mount(page, cfg)
        _pick(_dropdown(tree, "Roster mapping"), overlay)
        _button(tree, "Use this mapping").on_click(None)

        _button(tree, mapping_screen.MAPPING_EDIT_LABEL).on_click(None)

        labels = _button_labels(tree)
        assert "Run a test conversion" in labels, sorted(labels)
        assert "Use this mapping" not in labels, "the view's controls are still in the tree"

    def test_a_SHIPPED_row_applies(self, page, monkeypatch, tmp_path, overlay) -> None:
        """The twin, on the same install (the overlay exists, it is just not the row picked) —
        so the refusal above cannot pass merely because Apply is broken."""
        cfg = _install(tmp_path, verified={overlay: STALE_DIGEST})
        saves = _spy_save(monkeypatch)
        tree = self._mount(page, cfg)

        _pick(_dropdown(tree, "Roster mapping"), OTHER_SHIPPED_ID)
        _button(tree, "Use this mapping").on_click(None)

        assert saves == [OTHER_SHIPPED_ID]
        assert _on_disk()["sis_type"] == OTHER_SHIPPED_ID
        assert mapping_screen.MAPPING_NEEDS_TEST_NOTE not in _texts(tree)
        assert any(text.startswith("Now using") for text in _texts(tree))

    def test_a_user_row_whose_digest_is_CURRENT_applies(self, page, monkeypatch, tmp_path, overlay) -> None:
        """The other twin: the check gates on the TESTED FACT, never on provenance alone."""
        live = current_digest(overlay)
        assert live is not None, "the overlay does not resolve — the row is vacuous"
        cfg = _install(tmp_path, verified={overlay: live})
        saves = _spy_save(monkeypatch)
        tree = self._mount(page, cfg)

        _pick(_dropdown(tree, "Roster mapping"), overlay)
        _button(tree, "Use this mapping").on_click(None)

        assert saves == [overlay]
        assert _on_disk()["sis_type"] == overlay
        assert mapping_screen.MAPPING_NEEDS_TEST_NOTE not in _texts(tree)

    def test_the_refusal_names_no_district_no_path_and_no_digest(self, tmp_path) -> None:
        """Privacy: the note is STRUCTURAL — it explains the rule, never the row."""
        note = mapping_screen.MAPPING_NEEDS_TEST_NOTE

        assert "test conversion" in note, "the positive twin: it does name the act that fixes it"
        assert mapping_screen.MAPPING_EDIT_LABEL in note, "it does not name the button beside it"
        for leak in (CUSTOM_NAME, CUSTOM_ID, "93", str(tmp_path), STALE_DIGEST, "sd93"):
            assert leak not in note, f"the refusal echoes {leak!r}"


# --------------------------------------------------------------------------- #
# 2. The Settings folders card's Save                                          #
# --------------------------------------------------------------------------- #
class TestFoldersSave:
    def _spy_reconcile(self, monkeypatch: pytest.MonkeyPatch) -> list[int]:
        """Spy the pure decision the shared ``_reconcile`` calls — no call, no reconcile."""
        calls: list[int] = []
        real = setup_screen.schedule_reconcile

        def _spy(**kwargs):  # noqa: ANN003, ANN202
            calls.append(1)
            return real(**kwargs)

        monkeypatch.setattr(setup_screen, "schedule_reconcile", _spy)
        return calls

    def _mount(self, page: MagicMock, *, routed: bool = True) -> tuple[ft.Control, list[str]]:
        hops: list[str] = []
        tree = build_setup(page, on_navigate=(hops.append if routed else None))
        return tree, hops

    def test_a_stale_user_district_is_REFUSED_writing_and_reconciling_NOTHING(
        self, page, monkeypatch, tmp_path, overlay
    ) -> None:
        _install(tmp_path, verified={overlay: STALE_DIGEST})
        saves = _spy_save(monkeypatch)
        reconciles = self._spy_reconcile(monkeypatch)
        tree, hops = self._mount(page)

        _pick(_dropdown(tree, "District"), overlay)
        _button(tree, "Save folders & district").on_click(None)

        assert setup_screen.FOLDERS_NEEDS_TEST_NOTE in _texts(tree)
        assert saves == [], "a refused Save reached AppConfig.save"
        assert reconciles == [], "a refused Save still reconciled the nightly task"
        assert _on_disk()["sis_type"] == SHIPPED_ID
        assert "Saved." not in _texts(tree), "a refusal beside a Saved. line is the worst of both"
        # The hop is present and routes to Mapping, where the test lives.
        _button(tree, setup_screen.FOLDERS_NEEDS_TEST_LINK_LABEL).on_click(None)
        assert hops == ["mapping"]

    def test_the_note_says_BOTH_facts_including_the_un_re_registered_nightly(self) -> None:
        note = setup_screen.FOLDERS_NEEDS_TEST_NOTE

        assert "Nothing was saved" in note
        assert "nightly schedule was not updated" in note, "the second fact is the one discovered a night later"
        assert "Mapping" in note, "the note does not say where the test lives"

    def test_without_on_navigate_the_note_renders_with_NO_button(self, page, monkeypatch, tmp_path, overlay) -> None:
        """Never a dead affordance — and never a dead end either: the rail still has Mapping."""
        _install(tmp_path, verified={overlay: STALE_DIGEST})
        _spy_save(monkeypatch)
        self._spy_reconcile(monkeypatch)
        tree, _hops = self._mount(page, routed=False)

        _pick(_dropdown(tree, "District"), overlay)
        _button(tree, "Save folders & district").on_click(None)

        assert setup_screen.FOLDERS_NEEDS_TEST_NOTE in _texts(tree), "the positive twin: it IS refused"
        assert setup_screen.FOLDERS_NEEDS_TEST_LINK_LABEL not in _button_labels(tree)

    def test_a_SHIPPED_district_saves_and_reconciles(self, page, monkeypatch, tmp_path, overlay) -> None:
        """The twin. Without it, "nothing was saved" proves nothing about the gate."""
        _install(tmp_path, verified={overlay: STALE_DIGEST})
        saves = _spy_save(monkeypatch)
        reconciles = self._spy_reconcile(monkeypatch)
        tree, _hops = self._mount(page)

        _pick(_dropdown(tree, "District"), OTHER_SHIPPED_ID)
        _button(tree, "Save folders & district").on_click(None)

        assert saves == [OTHER_SHIPPED_ID]
        assert reconciles == [1]
        assert _on_disk()["sis_type"] == OTHER_SHIPPED_ID
        assert setup_screen.FOLDERS_NEEDS_TEST_NOTE not in _texts(tree)

    def test_a_user_district_whose_digest_is_CURRENT_saves_and_reconciles(
        self, page, monkeypatch, tmp_path, overlay
    ) -> None:
        live = current_digest(overlay)
        assert live is not None
        _install(tmp_path, verified={overlay: live})
        saves = _spy_save(monkeypatch)
        reconciles = self._spy_reconcile(monkeypatch)
        tree, _hops = self._mount(page)

        _pick(_dropdown(tree, "District"), overlay)
        _button(tree, "Save folders & district").on_click(None)

        assert saves == [overlay]
        assert reconciles == [1]
        assert _on_disk()["sis_type"] == overlay
        assert setup_screen.FOLDERS_NEEDS_TEST_NOTE not in _texts(tree)

    def test_the_folder_values_on_disk_survive_a_refusal(self, page, monkeypatch, tmp_path, overlay) -> None:
        """The refusal is ALL-or-nothing: not a partial save that keeps the folders and drops
        the district, because "Saved." would then be a lie about the field that matters."""
        _install(tmp_path, verified={overlay: STALE_DIGEST})
        before = _on_disk()
        _spy_save(monkeypatch)
        self._spy_reconcile(monkeypatch)
        tree, _hops = self._mount(page)
        picker = next(control for control in _walk(tree) if type(control).__name__ == "PickerField")
        moved = tmp_path / "moved-in"
        moved.mkdir()

        picker._on_change(str(moved), picker._validator(str(moved)))
        _pick(_dropdown(tree, "District"), overlay)
        _button(tree, "Save folders & district").on_click(None)

        after = _on_disk()
        assert after["input_dir"] == before["input_dir"], "the folder change landed on a refused Save"
        assert after["sis_type"] == before["sis_type"]

    def test_a_refusal_is_CLEARED_by_the_next_Save_that_lands(self, page, monkeypatch, tmp_path, overlay) -> None:
        """A stale refusal under a successful Save would say nothing was saved about a save."""
        _install(tmp_path, verified={overlay: STALE_DIGEST})
        _spy_save(monkeypatch)
        self._spy_reconcile(monkeypatch)
        tree, _hops = self._mount(page)
        _pick(_dropdown(tree, "District"), overlay)
        _button(tree, "Save folders & district").on_click(None)
        assert setup_screen.FOLDERS_NEEDS_TEST_NOTE in _texts(tree)

        _pick(_dropdown(tree, "District"), OTHER_SHIPPED_ID)
        _button(tree, "Save folders & district").on_click(None)

        assert setup_screen.FOLDERS_NEEDS_TEST_NOTE not in _texts(tree)

    def test_the_note_names_no_district_no_path_and_no_digest(self, tmp_path) -> None:
        note = setup_screen.FOLDERS_NEEDS_TEST_NOTE
        for leak in (CUSTOM_NAME, CUSTOM_ID, str(tmp_path), STALE_DIGEST, "sd93"):
            assert leak not in note, f"the refusal echoes {leak!r}"
