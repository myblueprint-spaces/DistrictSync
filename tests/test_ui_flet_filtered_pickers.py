"""The four district pickers, actually scoped (plan 0038 S5) — the VIEW half.

The rule itself is pinned COUNTED in ``test_ui_flet_filtered_catalog.py``; this file asks the
question that file cannot: **does the rule reach the screen?** S3 shipped the domain rows and
S4a shipped the launch page, and through both slices every picker still rendered all eleven
configs — a filter is only real when a picker is short.

Per ``docs/claugentic-CHARTER.md`` → "Flet view-glue surfaces": these are per-STATE render
smokes over the real control trees, not a re-test of the rule. Four consumers, one question
each:

* ``setup`` wizard District step — is the matched district the only option, and pre-selected?
* ``setup`` Settings folders card — same list, and does the toggle swap it in place?
* ``convert`` — is the saved district still prefillable, and does the "This run" pill follow
  the PICK rather than the save?
* ``mapping`` — one catalog build per mount, and the current mapping never missing.

Every "the list is short" assertion carries its positive twin (the same surface, unmatched,
showing everything) — "only one row" is equally satisfied by a working filter and by a
catalog that failed to load.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig
from src.ui_flet import components
from src.ui_flet.mapping_catalog import SHOW_ALL_LABEL, SHOWING_ALL_LABEL
from src.ui_flet.screens.convert import build_convert
from src.ui_flet.screens.mapping import build_mapping
from src.ui_flet.screens.setup import build_setup
from src.ui_flet.verdict import Verdict

# An address at a REAL shipped staff domain — the live end-to-end input. Using a synthetic
# domain here would test the plumbing while proving nothing about the rows we ship.
SD48_ADMIN = "roster.admin@sd48.bc.ca"
UNMATCHED = "roster.admin@example.com"


@pytest.fixture
def page() -> MagicMock:
    return MagicMock()


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


def _dropdown(tree, label: str) -> ft.Dropdown:  # noqa: ANN001
    for control in _walk(tree):
        if isinstance(control, ft.Dropdown) and control.label == label:
            return control
    raise AssertionError(f"no Dropdown labelled {label!r}")


def _keys(dropdown: ft.Dropdown) -> list[str]:
    return [option.key for option in dropdown.options or []]


def _texts(dropdown: ft.Dropdown) -> list[str]:
    return [option.text for option in dropdown.options or []]


def _button(tree, content: str):  # noqa: ANN001, ANN202
    for control in _walk(tree):
        if getattr(control, "content", None) == content:
            return control
    return None


def _pick_event(value: str) -> MagicMock:
    """A Dropdown ``on_select`` event exposing ``e.control.value``.

    Mapping's handler reads the value off the EVENT; Convert's reads it off the closed-over
    control. Both are set at every call site below so neither shape can silently no-op.
    """
    evt = MagicMock()
    evt.control.value = value
    return evt


def _pills(tree) -> list[str]:  # noqa: ANN001
    """Every ``status_pill`` label in the tree (the pill's text rides inside a Row)."""
    return [
        c.value
        for c in _walk(tree)
        if isinstance(c, ft.Text) and isinstance(c.value, str) and c.value.startswith("This run:")
    ]


def _cfg(**over) -> AppConfig:  # noqa: ANN003
    base = {
        "input_dir": "/in",
        "output_dir": "/out",
        "sis_type": "sd48myedbc",
        "setup_completed": True,
        "identity_email": SD48_ADMIN,
    }
    base.update(over)
    return AppConfig(**base)


def _pin_config(monkeypatch: pytest.MonkeyPatch, cfg: AppConfig) -> AppConfig:
    """Screens load their own ``AppConfig`` — pin it, hermetically."""
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda _cls: cfg))
    return cfg


# --------------------------------------------------------------------------- #
# 1. The wizard District step — the launch page's promise, made literal        #
# --------------------------------------------------------------------------- #
class TestWizardDistrictStep:
    def test_a_matched_identity_scopes_the_District_options_on_FIRST_PAINT(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The load-bearing test of the whole slice.

        S4a's launch page says "That's SD48 — you'll confirm it on the next step." Until this
        lands, "the next step" was a dropdown of all eleven, and the sentence was true only in
        the weakest sense. Now the District step opens on SD48 alone.
        """
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type=""))

        tree = build_setup(page)

        assert _keys(_dropdown(tree, "District")) == ["sd48myedbc"]

    def test_the_matched_district_is_PRE_SELECTED_under_the_single_entry_rule(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """D9, re-scoped to the visible list: auto-select when there is no meaningful choice
        to make. It stays a PRE-SELECTION — the dropdown is right there, and "Show all
        districts" is one click below it."""
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type=""))

        assert _dropdown(build_setup(page), "District").value == "sd48myedbc"

    def test_an_UNMATCHED_admin_sees_every_district_and_none_pre_selected(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The positive twin for both tests above: without a match the step is exactly what it
        was before this slice — eleven options and an explicit "Choose your district"."""
        from src.config.loader import available_configs

        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type="", identity_email=UNMATCHED))

        dropdown = _dropdown(build_setup(page), "District")

        assert _keys(dropdown) == available_configs()
        assert len(_keys(dropdown)) == 11
        assert dropdown.value is None

    def test_no_identity_at_all_sees_every_district(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type="", identity_email=""))

        assert len(_keys(_dropdown(build_setup(page), "District"))) == 11

    def test_the_show_all_row_is_ABSENT_when_nothing_is_being_hidden(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """A courtesy about a short list would be nonsense over a complete one."""
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type="", identity_email=UNMATCHED))

        tree = build_setup(page)

        assert _button(tree, SHOW_ALL_LABEL) is None
        assert _button(tree, SHOWING_ALL_LABEL) is None

    def test_the_show_all_row_IS_present_when_the_list_is_short(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The positive twin of the absence above."""
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type=""))

        assert _button(build_setup(page), SHOW_ALL_LABEL) is not None

    def test_pressing_show_all_reveals_every_district_and_INVERTS_the_row(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """Nothing is ever withheld — one text-tier click brings the whole catalog back, and
        the row stays on screen offering the way BACK (keyed on `can_filter`, not `filtered`:
        keying it on the latter would strand the admin in the long list)."""
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type=""))
        root = build_setup(page)

        _button(root, SHOW_ALL_LABEL).on_click(None)

        assert len(_keys(_dropdown(root, "District"))) == 11
        assert _button(root, SHOWING_ALL_LABEL) is not None
        assert _button(root, SHOW_ALL_LABEL) is None

    def test_show_all_does_not_un_pick_the_district(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type=""))
        root = build_setup(page)

        _button(root, SHOW_ALL_LABEL).on_click(None)

        assert _dropdown(root, "District").value == "sd48myedbc"

    def test_show_all_writes_NOTHING_durable(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """Flag 5: per-session only. A flip-once-forever setting would permanently re-arm the
        wrong-district risk this feature exists to reduce.

        Asserted at the WRITE, not by scanning `vars(cfg)` for a "show_all" key — a leak does
        not have to introduce a new field to be a leak, and a key scan is blind to a toggle
        that scribbles on an EXISTING one (a falsification probe that reused
        `identity_prompt_dismissed` passed the key scan untouched). Spying on the two save
        paths covers every field at once, and the re-scoped second mount is the twin that
        proves the toggle did something at all.
        """
        cfg = _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type=""))
        writes: list[str] = []
        monkeypatch.setattr(AppConfig, "save", lambda _self: writes.append("save"))
        monkeypatch.setattr(AppConfig, "identity_save", lambda _self, **kw: writes.append("identity_save") or True)
        before = dict(vars(cfg))
        root = build_setup(page)
        writes.clear()  # the mount itself may legitimately persist; only the TOGGLE is under test

        _button(root, SHOW_ALL_LABEL).on_click(None)

        assert writes == [], f"toggling the list scope persisted something: {writes}"
        assert vars(cfg) == before, "the toggle mutated the shared AppConfig instance"
        assert len(_keys(_dropdown(build_setup(page), "District"))) == 1, "a new mount re-scopes"


# --------------------------------------------------------------------------- #
# 2. The Settings folders card — the same list, after setup                    #
# --------------------------------------------------------------------------- #
class TestSettingsFoldersCard:
    def test_the_settings_district_dropdown_is_scoped(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        _pin_config(monkeypatch, _cfg())

        assert _keys(_dropdown(build_setup(page), "District")) == ["sd48myedbc"]

    def test_an_unmatched_admin_keeps_the_full_settings_list(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        _pin_config(monkeypatch, _cfg(identity_email=UNMATCHED))

        assert len(_keys(_dropdown(build_setup(page), "District"))) == 11

    def test_the_saved_district_survives_a_match_that_EXCLUDES_it(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The G3 shape, on the picker that edits it: an install set up for SD74 whose admin
        writes from an SD48 address must still be able to SEE SD74 — otherwise the surface
        that changes a district cannot show the district in use."""
        _pin_config(monkeypatch, _cfg(sis_type="sd74myedbc"))

        keys = _keys(_dropdown(build_setup(page), "District"))

        assert keys == ["sd48myedbc", "sd74myedbc"]

    def test_toggling_show_all_swaps_the_options_IN_PLACE(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """In place, not by re-rendering the card — a rebuild would discard un-saved folder
        edits the admin is in the middle of making."""
        _pin_config(monkeypatch, _cfg())
        root = build_setup(page)
        dropdown = _dropdown(root, "District")

        _button(root, SHOW_ALL_LABEL).on_click(None)

        assert len(_keys(dropdown)) == 11, "the SAME control object gained the full option set"
        assert _dropdown(root, "District") is dropdown


# --------------------------------------------------------------------------- #
# 3. Convert — the prefill, the scope, and the "This run" pill                 #
# --------------------------------------------------------------------------- #
class TestConvertScreen:
    def test_the_district_dropdown_is_scoped(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        _pin_config(monkeypatch, _cfg())

        assert _keys(_dropdown(build_convert(page), "District")) == ["sd48myedbc"]

    def test_the_SAVED_district_still_prefills_under_a_filter(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The membership test moved from `available_configs()` to the VISIBLE ids, so this is
        the pin that scoping cannot silently demote a valid saved district to "unset" (which
        would disable Convert on an install that has been running for months)."""
        _pin_config(monkeypatch, _cfg(sis_type="sd74myedbc"))

        dropdown = _dropdown(build_convert(page), "District")

        assert dropdown.value == "sd74myedbc"
        assert "sd74myedbc" in _keys(dropdown)

    def test_a_saved_district_that_names_no_real_config_still_reads_as_unset(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The D9 guarantee survives the rewrite: a hand-edited `config.json` naming a district
        we do not ship must NOT prefill (and must not be fabricated into the allowlist)."""
        _pin_config(monkeypatch, _cfg(sis_type="sd99nonesuch"))

        dropdown = _dropdown(build_convert(page), "District")

        assert dropdown.value is None
        assert "sd99nonesuch" not in _keys(dropdown)

    def test_the_pill_is_ABSENT_when_the_pick_matches_the_saved_district(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        _pin_config(monkeypatch, _cfg())

        assert _pills(build_convert(page)) == []

    def test_the_pill_APPEARS_on_divergence_and_names_the_PICKED_district(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The P1 leftover, closed: before this the header asserted the SAVED district while
        the run would use the PICKED one."""
        _pin_config(monkeypatch, _cfg(identity_email=UNMATCHED))
        tree = build_convert(page)
        dropdown = _dropdown(tree, "District")
        dropdown.value = "sd74myedbc"
        dropdown.on_select(_pick_event("sd74myedbc"))

        pills = _pills(tree)

        assert len(pills) == 1
        assert "Gold Trail" in pills[0], pills
        assert "Sea to Sky" not in pills[0], "the pill must name the run's district, not the saved one"

    def test_the_pill_REPAINTS_back_to_absent_when_the_pick_realigns(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """A stale pill claiming a district the run will not use is worse than no pill."""
        _pin_config(monkeypatch, _cfg(identity_email=UNMATCHED))
        tree = build_convert(page)
        dropdown = _dropdown(tree, "District")

        dropdown.value = "sd74myedbc"
        dropdown.on_select(_pick_event("sd74myedbc"))
        assert _pills(tree), "positive twin — the pill really did appear first"

        dropdown.value = "sd48myedbc"
        dropdown.on_select(_pick_event("sd48myedbc"))

        assert _pills(tree) == []

    def test_the_pill_carries_the_route_to_Mapping(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """A per-run override is a question ("did you mean to?"), so it ships with the answer:
        the surface where a saved district is changed for good."""
        _pin_config(monkeypatch, _cfg(identity_email=UNMATCHED))
        hops: list[str] = []
        tree = build_convert(page, on_navigate=hops.append)
        dropdown = _dropdown(tree, "District")
        dropdown.value = "sd74myedbc"
        dropdown.on_select(_pick_event("sd74myedbc"))

        _button(tree, "Change mapping").on_click(None)

        assert hops == ["mapping"]

    def test_the_pill_is_WARNING_toned_and_never_colour_alone(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """It reuses ``components.status_pill``, whose tint/line/on-tint triple is the
        AA-gated one the verdict band uses and which always carries an icon beside the text."""
        _pin_config(monkeypatch, _cfg(identity_email=UNMATCHED))
        seen: list[Verdict] = []
        real = components.status_pill
        monkeypatch.setattr(components, "status_pill", lambda label, status: seen.append(status) or real(label, status))
        tree = build_convert(page)
        dropdown = _dropdown(tree, "District")
        dropdown.value = "sd74myedbc"
        dropdown.on_select(_pick_event("sd74myedbc"))

        assert Verdict.WARNING in seen

    def test_the_pill_is_not_a_GATE(self, page: MagicMock, monkeypatch, isolated_user_profile, tmp_path) -> None:
        """A label may never decide whether a conversion may run — the whole point of a
        per-run override is that it RUNS.

        Asserted on the real Convert BUTTON either side of the pick, not by re-checking the
        pure ``can_run_convert`` (which the pill obviously does not call — a probe that wired
        `convert_btn.disabled = True` straight into the pill's paint passed that version of
        this test untouched). The before/after comparison isolates the pill's effect from the
        input/output gates that legitimately own that flag.
        """
        _pin_config(monkeypatch, _cfg(identity_email=UNMATCHED, input_dir=str(tmp_path), output_dir=str(tmp_path)))
        tree = build_convert(page)
        convert_btn = _button(tree, "Convert now")
        assert convert_btn is not None
        before = convert_btn.disabled
        assert before is False, "the gates are open, so the pill's effect is actually visible"

        dropdown = _dropdown(tree, "District")
        dropdown.value = "sd74myedbc"
        dropdown.on_select(_pick_event("sd74myedbc"))

        assert _pills(tree), "the divergent state really is up"
        assert convert_btn.disabled is before, "the pill changed the run gate — it is a label, not a gate"

    def test_show_all_reveals_the_full_list_on_convert(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        _pin_config(monkeypatch, _cfg())
        tree = build_convert(page)

        _button(tree, SHOW_ALL_LABEL).on_click(None)

        assert len(_keys(_dropdown(tree, "District"))) == 11


# --------------------------------------------------------------------------- #
# 4. Mapping — one catalog build, and the current mapping never missing        #
# --------------------------------------------------------------------------- #
class TestMappingScreen:
    def test_the_switch_dropdown_is_scoped(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        assert _keys(_dropdown(build_mapping(page, app_config=_cfg()), "Roster mapping")) == ["sd48myedbc"]

    def test_the_current_mapping_is_ALWAYS_offerable(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """Mapping's job is switching AWAY from and BACK to a mapping. A list that could omit
        the one in use would make the revert path unreachable."""
        keys = _keys(_dropdown(build_mapping(page, app_config=_cfg(sis_type="mbp_core")), "Roster mapping"))

        assert "mbp_core" in keys
        assert keys == ["mbp_core", "sd48myedbc"]

    def test_the_mount_builds_the_catalog_ONCE(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """The pre-S5 surface parsed all eleven YAMLs TWICE per mount (the summaries dict and
        the dropdown options each called `list_configs()`). One memoised build now serves both,
        and this counts the parses to prove it rather than trusting the refactor."""
        from src.config import loader as loader_mod
        from src.ui_flet import mapping_catalog

        mapping_catalog.reset_catalog_cache()
        parsed: list[str] = []
        real = loader_mod.load_config
        monkeypatch.setattr(mapping_catalog, "load_config", lambda sis, cd=None: (parsed.append(sis), real(sis, cd))[1])

        build_mapping(page, app_config=_cfg())

        assert len(parsed) == len(set(parsed)), f"a config was parsed more than once: {parsed}"

    def test_show_all_reveals_the_full_list_on_mapping(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        tree = build_mapping(page, app_config=_cfg())

        _button(tree, SHOW_ALL_LABEL).on_click(None)

        assert len(_keys(_dropdown(tree, "Roster mapping"))) == 11

    def test_a_widened_pick_still_summarises(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """After a widen, picking a newly-visible config must produce its real output summary
        — not the degraded "we couldn't read this configuration" card."""
        tree = build_mapping(page, app_config=_cfg())
        _button(tree, SHOW_ALL_LABEL).on_click(None)
        dropdown = _dropdown(tree, "Roster mapping")
        dropdown.value = "mbp_core"
        dropdown.on_select(_pick_event("mbp_core"))

        texts = [c.value for c in _walk(tree) if isinstance(getattr(c, "value", None), str)]

        assert any("Produces:" in t and "Student courses" in t for t in texts), texts
        assert not any("couldn't read this configuration" in t for t in texts)


# --------------------------------------------------------------------------- #
# 5. Label disambiguation reaches the rendered rows                            #
# --------------------------------------------------------------------------- #
class TestLabelsOnScreen:
    def test_no_two_rendered_rows_read_identically(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """G13's shipped-set guarantee, re-asserted where the admin actually reads it: the
        FULL rendered option list of every picker."""
        cfg = _cfg(identity_email=UNMATCHED)
        _pin_config(monkeypatch, cfg)

        for dropdown in (
            _dropdown(build_setup(page), "District"),
            _dropdown(build_convert(page), "District"),
            _dropdown(build_mapping(page, app_config=cfg), "Roster mapping"),
        ):
            texts = _texts(dropdown)
            assert len(set(texts)) == len(texts), texts

    def test_the_two_sd51_rows_are_distinguishable(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """The collision that motivated the rule: `sd51attendance` inherits SD51's mappings and
        (before S3) its `district_name`. Both rows ride an SD51 admin's matched list, so this
        is exactly the population that would have been shown two identical options."""
        cfg = _cfg(sis_type="sd51myedbc", identity_email="roster.admin@sd51.bc.ca")
        _pin_config(monkeypatch, cfg)

        dropdown = _dropdown(build_convert(page), "District")

        assert set(_keys(dropdown)) == {"sd51myedbc", "sd51attendance"}
        assert len(set(_texts(dropdown))) == 2, _texts(dropdown)


# --------------------------------------------------------------------------- #
# 6. Fail-open reaches the screen                                             #
# --------------------------------------------------------------------------- #
class TestTheScreensNeverFailClosed:
    def test_a_raising_filter_still_paints_every_district(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The floor, driven through the real screen rather than the pure layer: if the
        scoping blows up, the admin loses a short list — never a district, and never Convert.
        """
        from src.ui_flet import mapping_catalog

        _pin_config(monkeypatch, _cfg())
        monkeypatch.setattr(
            mapping_catalog, "resolve_domain", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        dropdown = _dropdown(build_convert(page), "District")

        assert len(_keys(dropdown)) == 11
        assert dropdown.value == "sd48myedbc"

    def test_an_unreadable_profile_scopes_nothing(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """G2 composes with the filter: under UNREADABLE settings we never claim to know who
        the admin is, so the list stays complete."""
        from src.config.app_config import ConfigLoadState

        _pin_config(monkeypatch, _cfg(load_state=ConfigLoadState.UNREADABLE))

        assert len(_keys(_dropdown(build_convert(page), "District"))) == 11
