"""The four district pickers, actually scoped (plan 0038 S5) — the VIEW half.

The rule itself is pinned COUNTED in ``test_ui_flet_filtered_catalog.py``; this file asks the
question that file cannot: **does the rule reach the screen?** S3 shipped the domain rows and
S4a shipped the launch page, and through both slices every picker still rendered all eleven
configs — a filter is only real when a picker is short.

Per ``docs/claugentic-CHARTER.md`` → "Flet view-glue surfaces": these are per-STATE render
smokes over the real control trees, not a re-test of the rule. Four consumers, one question
each:

* ``setup`` wizard District step — is the matched district the only option, and pre-selected?
* ``setup`` Settings folders card — same list, and no way to widen it?
* ``convert`` — is the saved district still prefillable, and does the "This run" pill follow
  the PICK rather than the save?
* ``mapping`` — one catalog build per mount, and the current mapping never missing.

Every "the list is short" assertion carries its positive twin (the same surface, unmatched,
showing everything) — "only one row" is equally satisfied by a working filter and by a
catalog that failed to load.

**2026-08-04 — the "Show all districts" row retired from all four surfaces** (owner decision).
The per-surface toggle tests that lived here are replaced by ``_assert_no_widen_affordance``,
asserted on every one of the four; the escape now lives at the INPUT (clear the stored address
in Settings), which ``test_ui_flet_filtered_catalog.py`` pins.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig
from src.ui_flet import components
from src.ui_flet.screens.convert import build_convert
from src.ui_flet.screens.mapping import build_mapping
from src.ui_flet.screens.setup import build_setup
from src.ui_flet.verdict import Verdict

# An address at a REAL shipped staff domain — the live end-to-end input. Using a synthetic
# domain here would test the plumbing while proving nothing about the rows we ship.
SD48_ADMIN = "roster.admin@sd48.bc.ca"
UNMATCHED = "roster.admin@example.com"
# SD51 ships TWO tiers behind one staff domain — the only shipped shape where a matched admin
# still has a real switch to make, which is what the post-Apply revert test needs.
SD51_ADMIN = "roster.admin@sd51.bc.ca"


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


def _assert_no_widen_affordance(tree, where: str) -> None:  # noqa: ANN001
    """No control on this surface offers to widen the district list (2026-08-04).

    Matched on the RENDERED TEXT, not on a retired label constant: a row re-added under new
    wording is the regression this guards, and a constant-based check could not see it. Both
    the old wordings ("Show all districts…" / "Showing all districts · Show only mine") are
    caught by the "show all"/"show only" probes.
    """
    blob = " ".join(
        str(value)
        for control in _walk(tree)
        for value in (getattr(control, "value", None), getattr(control, "content", None))
        if isinstance(value, str)
    ).lower()
    for probe in ("show all", "show only", "all districts", "every district"):
        assert probe not in blob, f"{where} still offers to widen the list ({probe!r})"


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

        **Scoped honestly to the path it covers.** The auto-SEED fires only when `ws["sis"]`
        is empty, i.e. on an install with no district saved — which is the population the
        launch page actually addresses (the page never shows once setup is complete). On a
        RESUMED wizard that already has a saved district, the list is still scoped but the
        value comes from the config, not from the domain; the launch-page promise holds there
        in the weaker "you'll see it on the next step" sense. Both are covered below.
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

    def test_the_step_ACKNOWLEDGES_a_choice_it_made_for_the_admin(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """A pre-selection nobody is told about is a silent default.

        The default caption INSTRUCTS a pick ("Pick the district whose…"); rendering that over
        a value we have already chosen reads as though nothing happened and quietly hides that
        we chose it. The acknowledging form names the district, says where the guess came from
        (a public email domain — not a lookup of the person), and offers the correction in the
        same breath.
        """
        from src.ui_flet.screens import setup as setup_screen

        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type=""))

        text = " ".join(c.value for c in _walk(build_setup(page)) if isinstance(getattr(c, "value", None), str))

        assert "We've picked" in text
        assert "Sea to Sky" in text
        assert "change it if that's wrong" in text
        assert setup_screen.DISTRICT_PICK_PROMPT not in text, "the instruction to pick is superseded"

    def test_an_UNSEEDED_step_keeps_the_plain_instruction(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The positive twin: with nothing auto-chosen, the caption instructs as it always did."""
        from src.ui_flet.screens import setup as setup_screen

        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type="", identity_email=UNMATCHED))

        text = " ".join(c.value for c in _walk(build_setup(page)) if isinstance(getattr(c, "value", None), str))

        assert setup_screen.DISTRICT_PICK_PROMPT in text
        assert "We've picked" not in text

    def test_an_explicit_pick_retires_the_acknowledgement(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """Once the admin chooses, the caption must stop crediting us with the choice.

        The admin re-picks from the dropdown the seed had already set — which is the shape
        this takes on a scoped list, and the one that matters: the caption retires because a
        CHOICE was made, not because the value changed.
        """
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type=""))
        root = build_setup(page)
        dropdown = _dropdown(root, "District")
        assert dropdown.value == "sd48myedbc", "the seed fired, so there is a claim to retire"
        dropdown.on_select(_pick_event("sd48myedbc"))

        text = " ".join(c.value for c in _walk(root) if isinstance(getattr(c, "value", None), str))

        assert "We've picked" not in text

    def test_a_SAVED_district_never_triggers_the_auto_seed_claim(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The weaker half of the first-paint promise, pinned so the stronger claim stays scoped.

        The auto-seed fires ONLY when nothing is saved. With a district already saved, the
        wizard's resume finds the District step satisfied and opens on Folders — so the admin
        does not see the District step on this launch at all, and we must certainly not claim
        to have picked anything for them. The launch-page line holds here only in the weaker
        "your district is already set" sense, which is exactly right: this population chose it.
        """
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type="sd48myedbc"))

        text = " ".join(c.value for c in _walk(build_setup(page)) if isinstance(getattr(c, "value", None), str))

        assert "We've picked" not in text
        assert "Step 2 of 5" in text, "resume moved past the satisfied District step, as it always has"

    def test_a_matched_SEVERAL_list_shows_both_and_auto_selects_NEITHER(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """SD51 ships two tiers behind one staff domain — the LIVE matched-several case.

        Two visible options is a real choice, so D9's rule ("auto-select only when there is no
        meaningful choice to make") must decline, and the placeholder must prompt an explicit
        pick. Getting this wrong would silently commit an SD51 admin to whichever tier sorted
        first — and the two produce different files.
        """
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type="", identity_email="roster.admin@sd51.bc.ca"))

        dropdown = _dropdown(build_setup(page), "District")

        assert set(_keys(dropdown)) == {"sd51myedbc", "sd51attendance"}
        assert dropdown.value is None, "two real options must never be auto-selected"

    def test_an_UNMATCHED_admin_sees_every_district_and_none_pre_selected(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The positive twin for both tests above: without a match the step is exactly what it
        was before this slice — eleven options and an explicit "Choose your district"."""
        from src.config.loader import available_configs

        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type="", identity_email=UNMATCHED))

        dropdown = _dropdown(build_setup(page), "District")

        assert _keys(dropdown) == available_configs()
        assert len(_keys(dropdown)) == 12
        assert dropdown.value is None

    def test_no_identity_at_all_sees_every_district(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type="", identity_email=""))

        assert len(_keys(_dropdown(build_setup(page), "District"))) == 12

    def test_the_wizard_step_offers_no_way_to_widen_the_list(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """2026-08-04: the District step shows the matched district and offers no escape.

        Asserted on the RENDERED TEXT rather than on a label constant, because the constant
        is gone — a re-added row under any wording still says "show all", and a test that
        could only recognise the retired string would miss it.
        """
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type=""))
        root = build_setup(page)

        assert _keys(_dropdown(root, "District")) == ["sd48myedbc"]
        _assert_no_widen_affordance(root, "the wizard District step")

    def test_the_unmatched_wizard_step_is_the_positive_twin(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """Nothing is withheld — an admin we cannot place still gets all eleven, with no row
        to press. That is what makes the absence above a scoping decision and not a lockout."""
        _pin_config(monkeypatch, _cfg(setup_completed=False, sis_type="", identity_email=UNMATCHED))
        root = build_setup(page)

        assert len(_keys(_dropdown(root, "District"))) == 12
        _assert_no_widen_affordance(root, "the unmatched wizard District step")


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

        assert len(_keys(_dropdown(build_setup(page), "District"))) == 12

    def test_the_saved_district_survives_a_match_that_EXCLUDES_it(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The G3 shape, on the picker that edits it: an install set up for SD74 whose admin
        writes from an SD48 address must still be able to SEE SD74 — otherwise the surface
        that changes a district cannot show the district in use."""
        _pin_config(monkeypatch, _cfg(sis_type="sd74myedbc"))

        keys = _keys(_dropdown(build_setup(page), "District"))

        assert keys == ["sd48myedbc", "sd74myedbc"]

    def test_settings_offers_no_way_to_widen_the_list(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The surface an admin lands on right after finishing the wizard — if the row
        survived anywhere it would be here, one screen later, undoing the whole decision."""
        _pin_config(monkeypatch, _cfg())
        root = build_setup(page)

        assert _keys(_dropdown(root, "District")) == ["sd48myedbc"]
        _assert_no_widen_affordance(root, "the Settings folders card")


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

    def test_convert_offers_no_way_to_widen_the_list(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """Per-SURFACE, not through the setup helper: each screen wired its own toggle, so
        each screen has to be asked separately whether it still does."""
        _pin_config(monkeypatch, _cfg())
        tree = build_convert(page)

        assert _keys(_dropdown(tree, "District")) == ["sd48myedbc"]
        _assert_no_widen_affordance(tree, "Convert")

    def test_an_unmatched_convert_still_shows_everything(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        _pin_config(monkeypatch, _cfg(identity_email=UNMATCHED))
        tree = build_convert(page)

        assert len(_keys(_dropdown(tree, "District"))) == 12
        _assert_no_widen_affordance(tree, "unmatched Convert")


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
        and this counts the parses to prove it rather than trusting the refactor.

        **What the counter actually sees, stated so it is not over-read:** it patches
        `mapping_catalog.load_config`, so it observes the CATALOG's parses only.
        `friendly_district_name` imports the loader inside its own body and is invisible to
        this seam, so this is not a whole-mount I/O census. It IS a faithful guard on the
        defect it exists for — both the retired double `list_configs()` and the eager
        `setdefault(..., summarize_config(...))` went through exactly this seam, and either
        one re-appearing surfaces as a duplicate id below.
        """
        from src.config import loader as loader_mod
        from src.ui_flet import mapping_catalog

        mapping_catalog.reset_catalog_cache()
        parsed: list[str] = []
        real = loader_mod.load_config
        monkeypatch.setattr(mapping_catalog, "load_config", lambda sis, cd=None: (parsed.append(sis), real(sis, cd))[1])

        build_mapping(page, app_config=_cfg())

        # The positive twin: without it a counter that saw NOTHING (a renamed seam, a patch
        # that missed) passes the uniqueness check trivially — `[] == set([])`.
        assert len(parsed) == 12, f"the parse counter saw nothing like a full catalog: {parsed}"
        assert len(parsed) == len(set(parsed)), f"a config was parsed more than once: {parsed}"

    def test_mapping_offers_no_way_to_widen_the_list(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """The surface whose whole job is switching mapping — the one where keeping the row
        would have been most defensible, and so the clearest statement of the decision.

        A matched admin can still switch BETWEEN their own district's tiers and can always
        switch back (the current mapping rides every list); reaching another district's
        mapping means clearing the stored address in Settings.
        """
        tree = build_mapping(page, app_config=_cfg())

        assert _keys(_dropdown(tree, "Roster mapping")) == ["sd48myedbc"]
        _assert_no_widen_affordance(tree, "Mapping")

        unfiltered = build_mapping(page, app_config=_cfg(identity_email=UNMATCHED))
        assert len(_keys(_dropdown(unfiltered, "Roster mapping"))) == 12
        _assert_no_widen_affordance(unfiltered, "unmatched Mapping")

    def test_an_APPLIED_mapping_survives_in_its_own_picker(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """The revert path, on the switch that a scoped admin can still make.

        SD51 is the live matched-several district: its rostering and attendance tiers share
        one staff domain, so an SD51 admin sees both and can switch between them. Applying
        one must never remove EITHER from the dropdown that applied it — a switch you cannot
        undo without restarting the app is the failure this guards.

        (The pre-2026-08-04 version of this test drove a widen → pick → Apply → narrow round
        trip to catch `_catalog()` re-deriving `saved_sis` from the STALE mount instance. That
        re-derivation was the show-all toggle's; with the toggle gone the option list is built
        once per mount and cannot go stale mid-visit, so the sequence is no longer expressible.
        What remains testable is the invariant it existed to protect, which is this.)
        """
        from src.config.app_config import AppConfig as _AC

        cfg = _cfg(sis_type="sd51myedbc", identity_email=SD51_ADMIN)
        monkeypatch.setattr(
            _AC, "load", classmethod(lambda _cls: _cfg(sis_type="sd51myedbc", identity_email=SD51_ADMIN))
        )
        monkeypatch.setattr(_AC, "save", lambda _self: None)
        tree = build_mapping(page, app_config=cfg)
        dropdown = _dropdown(tree, "Roster mapping")
        assert set(_keys(dropdown)) == {"sd51myedbc", "sd51attendance"}, "the switch is a real choice"

        dropdown.value = "sd51attendance"
        dropdown.on_select(_pick_event("sd51attendance"))
        _button(tree, "Use this mapping").on_click(None)

        keys = _keys(_dropdown(tree, "Roster mapping"))
        assert "sd51attendance" in keys, "the mapping we just applied vanished from its own picker"
        assert "sd51myedbc" in keys, "...and so did the one to revert to"

    def test_NO_saved_district_reads_as_unanswered_not_as_a_FAULT(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """Reachable from Convert's "Change mapping", which fires exactly when nothing is saved.

        A blank `sis_type` used to fall through `summarize_config("")` into the DEGRADED
        summary and paint "We couldn't read this configuration — it may need attention." over
        an empty name: a failure report about a district nobody ever chose, on the surface
        whose job is to let them choose one.
        """
        from src.ui_flet.screens import mapping as mapping_screen

        tree = build_mapping(page, app_config=_cfg(sis_type=""))
        texts = [c.value for c in _walk(tree) if isinstance(getattr(c, "value", None), str)]

        assert mapping_screen.NO_DISTRICT_TITLE in texts
        assert not any("couldn't read this configuration" in t for t in texts)

    def test_the_failure_card_STILL_fires_for_a_real_fault(
        self, page: MagicMock, monkeypatch, isolated_user_profile
    ) -> None:
        """Positive twin — the empty state must not have swallowed the genuine degraded case."""
        tree = build_mapping(page, app_config=_cfg(sis_type="sd99nonesuch"))
        texts = [c.value for c in _walk(tree) if isinstance(getattr(c, "value", None), str)]

        assert any("couldn't read this configuration" in t for t in texts)

    def test_picking_another_config_still_summarises(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """Picking a config OTHER than the mounted one must produce its real output summary —
        not the degraded "we couldn't read this configuration" card. Driven from an unmatched
        admin, who is the population that still sees the whole catalog."""
        tree = build_mapping(page, app_config=_cfg(identity_email=UNMATCHED))
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

        assert len(_keys(dropdown)) == 12
        assert dropdown.value == "sd48myedbc"

    def test_an_unreadable_profile_scopes_nothing(self, page: MagicMock, monkeypatch, isolated_user_profile) -> None:
        """G2 composes with the filter: under UNREADABLE settings we never claim to know who
        the admin is, so the list stays complete."""
        from src.config.app_config import ConfigLoadState

        _pin_config(monkeypatch, _cfg(load_state=ConfigLoadState.UNREADABLE))

        assert len(_keys(_dropdown(build_convert(page), "District"))) == 12
