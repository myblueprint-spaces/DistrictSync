"""The verified-fact check at EVERY refusal site (plan 0044 S6 §6.1 + its Stage-7 review).

Mapping's "Use this mapping", the Settings folders card's "Save folders & district" and the
wizard's STANDARD District step Continue all write ``AppConfig.sis_type``. For a mapping
authored on THIS computer each consults the pure ``config_editor.activation_allowed`` BEFORE
any write, so no app surface can switch this install onto a district it set up itself and
never tested.

**One file for one rule, deliberately** — not a file per screen, which is how two
behaviours drift. Every twin pair (a shipped row applies/saves/advances · a user row whose
digest is current applies/saves/advances) sits beside the refusal it makes meaningful, and
every absence assertion — "``AppConfig.save`` was never called", "``reconcile()`` never
ran", "the bytes on disk are unchanged", "the walk did not move on" — has its positive twin
in the same class.

Three more rules of the same shape live here for the same reason (S6 review):

* the hosted panel's promoted **Done** REFUSES while a file name on screen is not in the
  config on disk — S4's BLOCKING 2 recurring on the new host, with the wizard's fix;
* the card's **provenance note** may not survive the change door that fixes it;
* the two screens' gate copy faces the launch page's banned-vocabulary sweep and the
  creator flow's vague-future sweep, both IMPORTED — neither screen is reached by any
  rendered-tree sweep.

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
from src.config.authoring import OverlaySpec, current_digest, overlay_path, write_overlay
from src.ui_flet import mapping_catalog
from src.ui_flet.screens import creator as creator_screen
from src.ui_flet.screens import mapping as mapping_screen
from src.ui_flet.screens import setup as setup_screen
from src.ui_flet.screens.mapping import build_mapping
from src.ui_flet.screens.setup import build_setup

# Single-sourced sweeps: the launch page's banned authentication vocabulary and the creator
# flow's vague-future ban. Imported so both screens' gate copy faces ONE list of each.
from tests.test_ui_flet_creator_flow import BANNED_COPY_WORDS
from tests.test_ui_flet_identity_page import _assert_no_banned_vocabulary

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


def _filled(tree) -> list[str]:  # noqa: ANN001
    """Every FILLED button in the tree, in render order — asserted as list equality.

    ``components.primary_button`` is the only factory that builds an ``ft.FilledButton``, so
    this IS the "exactly one filled primary per screen" rule, read off the render.
    """
    return [
        control.content
        for control in _walk(tree)
        if isinstance(control, ft.FilledButton) and isinstance(control.content, str)
    ]


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


def _install(
    tmp_path: Path,
    *,
    sis_type: str = SHIPPED_ID,
    verified: dict[str, str] | None = None,
    pending_sis: str = "",
) -> AppConfig:
    """A configured install ON DISK, then loaded back — the state both Saves start from.

    ``pending_sis`` stores the creator's resume token, which is what puts Mapping's
    ``MAPPING_RESUME_LABEL`` door on the view — the one route that opens the hosted panel
    straight on the FILE NAMES for a district that is already active and current.
    """
    AppConfig(
        setup_completed=True,
        sis_type=sis_type,
        identity_email=UNMATCHED,
        creator_verified=dict(verified or {}),
        creator_pending_sis=pending_sis,
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

    def test_a_FOLDER_only_edit_on_a_STALE_ACTIVE_user_district_still_SAVES(
        self, page, monkeypatch, tmp_path, overlay
    ) -> None:
        """Only a district CHANGE is gated (plan 0044 S6 review, SHOULD 2).

        The district here is already the one this install converts, so saving a folder path
        activates nothing — it is what the nightly runs either way. Refusing the repair would
        prevent nothing and block the fix, and this Save is the only way to correct a folder.
        Its twin is ``test_a_stale_user_district_is_REFUSED_...`` above: switching TO the same
        stale district is refused in the same file.
        """
        _install(tmp_path, sis_type=overlay, verified={overlay: STALE_DIGEST})
        saves = _spy_save(monkeypatch)
        reconciles = self._spy_reconcile(monkeypatch)
        tree, _hops = self._mount(page)
        picker = next(control for control in _walk(tree) if type(control).__name__ == "PickerField")
        moved = tmp_path / "moved-in"
        moved.mkdir()

        picker._on_change(str(moved), picker._validator(str(moved)))
        _button(tree, "Save folders & district").on_click(None)

        assert setup_screen.FOLDERS_NEEDS_TEST_NOTE not in _texts(tree), "a folder-only edit was refused"
        assert saves == [overlay]
        assert reconciles == [1]
        assert _on_disk()["input_dir"] == str(moved), "the folder fix did not land"
        assert _on_disk()["sis_type"] == overlay

    def test_a_digest_recorded_AFTER_this_mount_is_read_by_the_Save(self, page, monkeypatch, tmp_path, overlay) -> None:
        """The check reads a FRESH ``AppConfig`` (plan 0044 S6 review, SHOULD 3).

        The test conversion that records the digest runs in Mapping's panel, through its own
        instance — so an admin who saves the district here right after passing it there must
        not be refused by a snapshot taken at mount. Mapping's Apply already reloads; this is
        the same fix at the second site.
        """
        _install(tmp_path, verified={})  # nothing tested yet, and the shipped row is active
        tree, _hops = self._mount(page)
        _pick(_dropdown(tree, "District"), overlay)
        # ...the admin hops to Mapping, passes the test conversion there, and comes back: the
        # digest reaches disk through a DIFFERENT AppConfig instance.
        live = current_digest(overlay)
        assert live is not None
        assert AppConfig.load().creator_save(creator_verified={overlay: live}), "the fixture write was refused"
        saves = _spy_save(monkeypatch)
        reconciles = self._spy_reconcile(monkeypatch)

        _button(tree, "Save folders & district").on_click(None)

        assert setup_screen.FOLDERS_NEEDS_TEST_NOTE not in _texts(tree), "the Save read a stale snapshot"
        assert saves == [overlay]
        assert reconciles == [1]
        assert _on_disk()["sis_type"] == overlay

    def test_the_note_names_no_district_no_path_and_no_digest(self, tmp_path) -> None:
        note = setup_screen.FOLDERS_NEEDS_TEST_NOTE
        for leak in (CUSTOM_NAME, CUSTOM_ID, str(tmp_path), STALE_DIGEST, "sd93"):
            assert leak not in note, f"the refusal echoes {leak!r}"


# --------------------------------------------------------------------------- #
# 3. The wizard's STANDARD District step (S6 review, BLOCKING 2)               #
# --------------------------------------------------------------------------- #
class TestWizardDistrictStep:
    """The FIFTH writer of ``sis_type``: the standard walk's District step Continue.

    The wizard's dropdown is the SAME ``_district_catalog`` build every other picker uses, so
    it offers a district set up on this computer like any other row — and this Continue is
    what would carry an untested one to a finish line that registers it as a nightly task.
    """

    def _unfinished(self, tmp_path: Path, *, verified: dict[str, str] | None = None) -> None:
        """A genuinely unfinished install: no district chosen, setup not completed."""
        AppConfig(
            setup_completed=False,
            sis_type="",
            identity_email=UNMATCHED,
            creator_verified=dict(verified or {}),
            **_folders(tmp_path),
        ).save()

    def _mount(self, page: MagicMock, *, routed: bool = True) -> tuple[ft.Control, list[str]]:
        hops: list[str] = []
        tree = build_setup(page, on_navigate=(hops.append if routed else None))
        assert "Step 1 of 5" in _texts(tree), "the wizard did not open on the District step"
        return tree, hops

    def test_a_stale_user_row_is_REFUSED_writing_and_advancing_NOTHING(
        self, page, monkeypatch, tmp_path, overlay
    ) -> None:
        self._unfinished(tmp_path, verified={overlay: STALE_DIGEST})
        saves = _spy_save(monkeypatch)
        tree, hops = self._mount(page)

        _pick(_dropdown(tree, "District"), overlay)
        _button(tree, "Continue").on_click(None)

        assert setup_screen.WIZARD_DISTRICT_NEEDS_TEST_NOTE in _texts(tree)
        assert saves == [], "a refused Continue reached AppConfig.save"
        assert _on_disk()["sis_type"] == "", "the district on disk was written anyway"
        # ...and the walk did not move on: the step that was refused is still the one showing.
        assert "Step 1 of 5" in _texts(tree)
        assert _dropdown(tree, "District").value == overlay, "the pick did not survive the refusal"
        # The route is present and lands on Mapping, where the test conversion lives.
        _button(tree, setup_screen.FOLDERS_NEEDS_TEST_LINK_LABEL).on_click(None)
        assert hops == ["mapping"]

    def test_without_on_navigate_the_note_renders_with_NO_button(self, page, monkeypatch, tmp_path, overlay) -> None:
        """Home's wizard host passes no route — a note alone, never a dead affordance."""
        self._unfinished(tmp_path, verified={overlay: STALE_DIGEST})
        _spy_save(monkeypatch)
        tree, _hops = self._mount(page, routed=False)

        _pick(_dropdown(tree, "District"), overlay)
        _button(tree, "Continue").on_click(None)

        assert setup_screen.WIZARD_DISTRICT_NEEDS_TEST_NOTE in _texts(tree), "the positive twin: it IS refused"
        assert setup_screen.FOLDERS_NEEDS_TEST_LINK_LABEL not in _button_labels(tree)

    def test_the_refusal_is_CLEARED_by_the_next_pick(self, page, monkeypatch, tmp_path, overlay) -> None:
        """It named the district that was picked when Continue was pressed — leaving it over a
        NEW pick would report a fault about a district it was never about."""
        self._unfinished(tmp_path, verified={overlay: STALE_DIGEST})
        _spy_save(monkeypatch)
        tree, _hops = self._mount(page)
        _pick(_dropdown(tree, "District"), overlay)
        _button(tree, "Continue").on_click(None)
        assert setup_screen.WIZARD_DISTRICT_NEEDS_TEST_NOTE in _texts(tree)

        _pick(_dropdown(tree, "District"), OTHER_SHIPPED_ID)

        assert setup_screen.WIZARD_DISTRICT_NEEDS_TEST_NOTE not in _texts(tree)

    def test_a_SHIPPED_row_advances_and_PERSISTS(self, page, monkeypatch, tmp_path, overlay) -> None:
        """The twin, on the same install (the overlay exists — it is just not the row picked)."""
        self._unfinished(tmp_path, verified={overlay: STALE_DIGEST})
        saves = _spy_save(monkeypatch)
        tree, _hops = self._mount(page)

        _pick(_dropdown(tree, "District"), OTHER_SHIPPED_ID)
        _button(tree, "Continue").on_click(None)

        assert saves == [OTHER_SHIPPED_ID]
        assert _on_disk()["sis_type"] == OTHER_SHIPPED_ID
        assert "Step 2 of 5" in _texts(tree), "the wizard did not move on"
        assert setup_screen.WIZARD_DISTRICT_NEEDS_TEST_NOTE not in _texts(tree)

    def test_a_user_row_whose_digest_is_CURRENT_advances_and_PERSISTS(
        self, page, monkeypatch, tmp_path, overlay
    ) -> None:
        """The other twin: the gate is the TESTED FACT, never provenance alone."""
        live = current_digest(overlay)
        assert live is not None, "the overlay does not resolve — the row is vacuous"
        self._unfinished(tmp_path, verified={overlay: live})
        saves = _spy_save(monkeypatch)
        tree, _hops = self._mount(page)

        _pick(_dropdown(tree, "District"), overlay)
        _button(tree, "Continue").on_click(None)

        assert saves == [overlay]
        assert _on_disk()["sis_type"] == overlay
        assert "Step 2 of 5" in _texts(tree)
        assert setup_screen.WIZARD_DISTRICT_NEEDS_TEST_NOTE not in _texts(tree)

    def test_the_note_says_what_did_not_happen_and_where_the_fix_is(self, tmp_path) -> None:
        note = setup_screen.WIZARD_DISTRICT_NEEDS_TEST_NOTE

        assert "Nothing was saved" in note
        assert "test conversion" in note, "the positive twin: it names the act that fixes it"
        assert "Mapping" in note, "the note does not say where the test lives"
        for leak in (CUSTOM_NAME, CUSTOM_ID, str(tmp_path), STALE_DIGEST, "sd93"):
            assert leak not in note, f"the refusal echoes {leak!r}"


# --------------------------------------------------------------------------- #
# 4. Mapping's hosted panel: the promoted "Done" (S6 review, BLOCKING 1)       #
# --------------------------------------------------------------------------- #
class TestMappingPanelDone:
    """The host's way-back may not carry a pending file name out of the panel.

    On a district that is active AND current the panel's Back control promotes to the filled
    ``MAPPING_PANEL_DONE_LABEL`` — the host's stand-in for the wizard footer's Continue. The
    rows re-render themselves when a name is picked, so that control was built before the
    pick and is still painting the filled tier: pressing it would return to the view with the
    pending rename discarded, and the write it skipped is the only record of it. This is S4's
    BLOCKING 2 recurring on the new host, and it takes the wizard's fix — REFUSE and
    re-render, so one press makes the surface read its own truth.
    """

    ROW_FILE = "MyStaff.txt"

    def _panel(self, page: MagicMock, tmp_path: Path, overlay: str) -> ft.Control:
        """Mount Mapping on a CURRENT, ACTIVE user district and open the panel on FILES."""
        live = current_digest(overlay)
        assert live is not None, "the overlay does not resolve — every assertion below is vacuous"
        cfg = _install(tmp_path, sis_type=overlay, verified={overlay: live}, pending_sis=overlay)
        # A file in the input folder, so the rows have a REAL name to offer (the row list is
        # the folder's contents plus "use the standard name").
        (Path(cfg.input_dir) / self.ROW_FILE).write_text("x", encoding="utf-8")
        tree = build_mapping(page, app_config=cfg, on_navigate=lambda _dest: None)
        _button(tree, mapping_screen.MAPPING_RESUME_LABEL).on_click(None)
        assert creator_screen.FILES_NAMES_TITLE in _texts(tree), "the panel did not open on the file names"
        return tree

    def _row_label(self, tree: ft.Control) -> str:
        """The label of the first FILE-NAME row's dropdown (a standard source file name)."""
        for control in _walk(tree):
            if isinstance(control, ft.Dropdown) and control.label not in {"Roster mapping", "District"}:
                assert isinstance(control.label, str)
                return control.label
        raise AssertionError("no file-name row rendered on the panel")

    def test_Done_REFUSES_while_a_file_name_is_pending_and_keeps_the_pick(
        self, page, monkeypatch, tmp_path, overlay
    ) -> None:
        tree = self._panel(page, tmp_path, overlay)
        assert _filled(tree) == [mapping_screen.MAPPING_PANEL_DONE_LABEL], "the promotion did not happen"
        row = self._row_label(tree)
        _pick(_dropdown(tree, row), self.ROW_FILE)
        # Spied around the PRESS alone: the install this state needed is itself a save.
        saves = _spy_save(monkeypatch)

        _button(tree, mapping_screen.MAPPING_PANEL_DONE_LABEL).on_click(None)

        # (a) the panel is still on screen — the view's controls never came back.
        assert creator_screen.FILES_NAMES_TITLE in _texts(tree)
        assert "Use this mapping" not in _button_labels(tree)
        # (b) the pending pick SURVIVED the refusing re-render (the host owns that map).
        assert _dropdown(tree, row).value == self.ROW_FILE
        # (c) ...and the surface now reads its own truth: ONE filled primary, and it is the
        #     Save that writes what is pending.
        assert _filled(tree) == [creator_screen.FILES_SAVE_LABEL]
        assert creator_screen.FILES_UNSAVED_NOTE in _texts(tree)
        assert mapping_screen.MAPPING_PANEL_BACK_LABEL in _button_labels(tree), "the way back is still offered"
        assert saves == [], "a refused Done wrote settings"

    def test_Done_RETURNS_to_the_view_when_nothing_is_pending(self, page, tmp_path, overlay) -> None:
        """The twin. Without it, "Done refused" would pass on a Done that never works."""
        tree = self._panel(page, tmp_path, overlay)

        _button(tree, mapping_screen.MAPPING_PANEL_DONE_LABEL).on_click(None)

        assert _filled(tree) == ["Use this mapping"], "the Mapping view is not what came back"
        assert creator_screen.FILES_NAMES_TITLE not in _texts(tree)

    def test_after_the_SAVE_the_way_back_returns_to_the_view(self, page, tmp_path, overlay) -> None:
        """The other twin: a pending name is a state the admin can LEAVE, by saving it.

        The Save re-closes this district's gate (the config it just wrote is not the one the
        recorded test ran), so the promotion correctly retires and the body's test conversion
        takes the primary tier — and the text-tier Back still leaves the panel.
        """
        tree = self._panel(page, tmp_path, overlay)
        row = self._row_label(tree)
        _pick(_dropdown(tree, row), self.ROW_FILE)

        _button(tree, creator_screen.FILES_SAVE_LABEL).on_click(None)

        assert _filled(tree) == [creator_screen.GATE_RUN_LABEL], "the tiers did not follow the save"
        assert creator_screen.FILES_UNSAVED_NOTE not in _texts(tree)
        assert mapping_screen.MAPPING_PANEL_DONE_LABEL not in _button_labels(tree)

        _button(tree, mapping_screen.MAPPING_PANEL_BACK_LABEL).on_click(None)

        assert _filled(tree) == ["Use this mapping"]


# --------------------------------------------------------------------------- #
# 5. Mapping's provenance note must not survive its own fix (S6 review, SHOULD 1)
# --------------------------------------------------------------------------- #
class TestMappingProvenanceNote:
    """``MAPPING_STALE_VERSION_NOTE`` says "change its setup" — so after the change door has,
    it may not still be on the card.

    The notes are I/O (a YAML read plus a base digest), so they are memoised per view build;
    the memo is what would have outlived the very door it points at.
    """

    def _authored_with_an_older_build(self, sis: str) -> None:
        """Rewrite the overlay's provenance block to name a build that is not this one."""
        import yaml

        path = overlay_path(sis)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["authored_with"]["app_version"] = "0.0.1"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        mapping_catalog.reset_catalog_cache()

    def _mount(self, page: MagicMock, tmp_path: Path, overlay: str) -> ft.Control:
        self._authored_with_an_older_build(overlay)
        cfg = _install(tmp_path, sis_type=overlay, verified={overlay: STALE_DIGEST})
        tree = build_mapping(page, app_config=cfg, on_navigate=lambda _dest: None)
        assert mapping_screen.MAPPING_STALE_VERSION_NOTE in _texts(tree), "the note under test never rendered"
        return tree

    def test_the_note_is_GONE_once_the_change_door_has_re_written_the_overlay(self, page, tmp_path, overlay) -> None:
        tree = self._mount(page, tmp_path, overlay)

        _button(tree, mapping_screen.MAPPING_EDIT_LABEL).on_click(None)
        _button(tree, "Continue").on_click(None)  # the creator's District Continue IS the write
        _button(tree, mapping_screen.MAPPING_PANEL_BACK_LABEL).on_click(None)

        assert mapping_screen.MAPPING_STALE_VERSION_NOTE not in _texts(tree), (
            "the provenance note survived the fix it asked for"
        )
        assert "Current mapping" in _texts(tree), "the Mapping view is not what came back"

    def test_the_note_is_STILL_shown_when_nothing_was_re_written(self, page, tmp_path, overlay) -> None:
        """The twin: clearing the memo may not clear the FACT. Open the door, change nothing,
        come back — the overlay still names another build, so the note still belongs."""
        tree = self._mount(page, tmp_path, overlay)

        _button(tree, mapping_screen.MAPPING_EDIT_LABEL).on_click(None)
        _button(tree, mapping_screen.MAPPING_PANEL_BACK_LABEL).on_click(None)

        assert mapping_screen.MAPPING_STALE_VERSION_NOTE in _texts(tree)


# --------------------------------------------------------------------------- #
# 6. The copy sweep over both screens' gate strings (S6 review, NOTE 1)        #
# --------------------------------------------------------------------------- #
#: The sweeps that already cover the creator surface and the launch page, applied here to the
#: copy that renders on MAPPING and SETUP — neither of which any rendered-tree sweep reaches.
#: Imported, never re-typed: a second hand-written banned-word list is a list that drifts, and
#: the identification-is-not-authentication promise (0038) rests on there being one.
def _mapping_gate_copy() -> dict[str, str]:
    """Every ``MAPPING_*`` string constant plus the route label, by NAME.

    Collected reflectively so a constant added later faces the sweep without anyone
    remembering to list it — the failure mode a hand-written tuple has.
    """
    return {
        name: value
        for name, value in vars(mapping_screen).items()
        if isinstance(value, str) and (name.startswith("MAPPING_") or name == "OPEN_SETTINGS_LABEL")
    }


def _setup_gate_copy() -> dict[str, str]:
    """The Setup surface's two verified-fact refusals + their shared route label."""
    return {
        name: value
        for name, value in vars(setup_screen).items()
        if isinstance(value, str) and name.startswith(("FOLDERS_NEEDS_TEST", "WIZARD_DISTRICT_NEEDS_TEST"))
    }


class TestTheGateCopy:
    def test_the_sweep_actually_collected_the_strings_it_claims_to(self) -> None:
        """The positive twin for two reflective collectors: an empty sweep is a vacuous green."""
        mapping_copy = _mapping_gate_copy()
        setup_copy = _setup_gate_copy()

        for expected in (
            "MAPPING_CREATE_LABEL",
            "MAPPING_EDIT_LABEL",
            "MAPPING_RESUME_LABEL",
            "MAPPING_PANEL_BACK_LABEL",
            "MAPPING_PANEL_DONE_LABEL",
            "MAPPING_NEEDS_TEST_HEADLINE",
            "MAPPING_NEEDS_TEST_NOTE",
            "MAPPING_PANEL_NEEDS_OUTPUT_HEADLINE",
            "MAPPING_PANEL_NEEDS_OUTPUT_NOTE",
            "MAPPING_STALE_VERSION_NOTE",
            "MAPPING_STALE_BASE_NOTE",
            "MAPPING_PANEL_FLOOR_HEADLINE",
            "MAPPING_PANEL_FLOOR_DETAIL",
            "OPEN_SETTINGS_LABEL",
        ):
            assert expected in mapping_copy, f"{expected} is not being swept"
        for expected in (
            "FOLDERS_NEEDS_TEST_NOTE",
            "FOLDERS_NEEDS_TEST_LINK_LABEL",
            "WIZARD_DISTRICT_NEEDS_TEST_NOTE",
        ):
            assert expected in setup_copy, f"{expected} is not being swept"

    def test_no_gate_string_uses_authentication_vocabulary(self) -> None:
        """Identification is never authentication (0038): a district that has not passed a
        test conversion is UNTESTED, never unverified, unauthorized or locked."""
        for source, copy in (("mapping.py", _mapping_gate_copy()), ("setup.py", _setup_gate_copy())):
            for name, value in copy.items():
                _assert_no_banned_vocabulary(value, f"{source}:{name}")

    def test_no_gate_string_promises_an_unscheduled_future(self) -> None:
        """The vague-future ban the creator's copy already faces (``BANNED_COPY_WORDS``)."""
        for source, copy in (("mapping.py", _mapping_gate_copy()), ("setup.py", _setup_gate_copy())):
            for name, value in copy.items():
                lowered = value.lower()
                for probe in BANNED_COPY_WORDS:
                    assert probe not in lowered, f"{source}:{name} promises {probe!r}: {value!r}"
