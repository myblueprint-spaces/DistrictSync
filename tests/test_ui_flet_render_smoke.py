"""Render-smoke: every Flet screen's control tree must CONSTRUCT on the pinned flet
(0.85.3) without raising and without falling to its never-crash ErrorCard floor.

Why this exists: the screen views are coverage-omitted (Flet view glue) and the
manual GUI check was historically deferred to a human, so flet-0.85.3 API-drift
bugs shipped latent and undetected:
  - ``ft.Dropdown(on_change=...)``   -> TypeError (Dropdown's event is ``on_select``)
  - ``ft.TextField(helper_text=...)`` -> TypeError (the field is ``helper``)
Either one crashes the FIRST screen an admin opens (Setup). Unit tests never
instantiated the views, so the gates were green. This test instantiates each
screen against a stub Page so that entire class of render crash fails CI.

A stub Page (plain MagicMock) is enough: build_* functions construct controls and
close over ``page`` for event handlers — they don't need a live session at build
time. ``page.add``/``update``/``window.*`` are no-ops on the mock.
"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig, config_file_path
from src.ui_flet import components, tokens
from src.ui_flet.screens.convert import build_convert
from src.ui_flet.screens.help import build_help
from src.ui_flet.screens.home import build_home
from src.ui_flet.screens.mapping import build_mapping
from src.ui_flet.screens.onboarding import build_onboarding
from src.ui_flet.screens.run_history import build_run_history
from src.ui_flet.screens.setup import build_setup
from src.ui_flet.setup_flow import DowngradeInterrupt, TaskArgs, task_args_to_persisted


@pytest.fixture
def stub_page() -> MagicMock:
    """A permissive stub Page — any attr/method access returns a child mock no-op."""
    return MagicMock()


@pytest.fixture
def app_cfg() -> AppConfig:
    """Default (unconfigured) config — hermetic, independent of ~/.districtsync."""
    return AppConfig()


@pytest.fixture(autouse=True)
def _hermetic_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_setup/build_convert load AppConfig internally — pin it to defaults so
    the smoke never reads (or depends on) the developer's real ~/.districtsync."""
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cls()))


def _assert_renders(build_callable, monkeypatch: pytest.MonkeyPatch) -> ft.Control:
    """Build the screen; fail if it raises OR returns its ErrorCard floor.

    Each screen's floor does ``return components.ErrorCard(...)``. If the build's
    return value IS that ErrorCard, the screen swallowed a real render bug — a false
    pass by return-type alone. Spy on ErrorCard to catch that precisely.
    """
    real_errorcard = components.ErrorCard
    floor: dict[str, object] = {"obj": None}

    def spy(*args, **kwargs):
        obj = real_errorcard(*args, **kwargs)
        floor["obj"] = obj
        return obj

    monkeypatch.setattr(components, "ErrorCard", spy)
    out = build_callable()
    assert isinstance(out, ft.Control), f"build returned {type(out).__name__}, not a Control"
    assert out is not floor["obj"], "screen fell to its ErrorCard floor — a masked render bug"
    return out


def _settings_config(**over):
    """A completed config → ``build_setup`` renders SETTINGS mode (the flat scroll).

    The wizard reuses the same schedule/SFTP section builders, so the flat-scroll tests pin
    the shared behaviour via Settings mode (all sections present at the top level)."""
    from src.config.app_config import AppConfig

    base = {"input_dir": "/in", "output_dir": "/out", "sis_type": "myedbc", "setup_completed": True}
    base.update(over)
    return AppConfig(**base)


def _settings_tree(stub_page, monkeypatch, **over):
    """Build ``build_setup`` in Settings mode (a completed config), overriding the hermetic load."""
    from src.config.app_config import AppConfig

    cfg = _settings_config(**over)
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    return build_setup(stub_page)


class TestScreensRender:
    def test_setup_wizard_folders_step(self, stub_page, monkeypatch):
        # A fresh (unconfigured) config renders the wizard's first (Folders) step.
        _assert_renders(lambda: build_setup(stub_page), monkeypatch)

    def test_setup_settings_mode(self, stub_page, monkeypatch):
        # A completed config renders the flat Settings scroll without crashing.
        tree = _settings_tree(stub_page, monkeypatch)
        values = [getattr(c, "value", None) for c in _iter_controls(tree)]
        assert "Settings" in values, "Settings mode must render the 'Settings' title"
        # 2026-07-15 user decision: folders/district FIRST (what/where), then schedule (when), then delivery.
        order = [v for v in values if v in ("Daily schedule", "SFTP delivery (SpacesEDU)", "Folders & district")]
        assert order == ["Folders & district", "Daily schedule", "SFTP delivery (SpacesEDU)"]

    def test_home(self, stub_page, app_cfg, monkeypatch):
        _assert_renders(
            lambda: build_home(stub_page, app_config=app_cfg, on_navigate=lambda _d: None),
            monkeypatch,
        )

    def test_convert(self, stub_page, monkeypatch):
        _assert_renders(lambda: build_convert(stub_page), monkeypatch)

    def test_convert_blocks_without_output_dir(self, stub_page, monkeypatch):
        # D9/D10: the default (unconfigured) config has no district and no output folder, so the
        # form shows the routed blocked caption, disables Convert, and shows the district placeholder.
        tree = _assert_renders(lambda: build_convert(stub_page), monkeypatch)
        assert _has_text_containing(tree, "Set your output folder in Settings first")
        assert _button_by_content(tree, "Convert now").disabled is True
        dropdown = _find(tree, ft.Dropdown)[0]
        assert dropdown.value is None  # no configs[0] fallback (D9)
        assert dropdown.hint_text == "Choose your district"

    def test_convert_names_output_folder_and_prefills_saved_district(self, tmp_path, stub_page, monkeypatch):
        # D10 pre-run visibility + D9 prefill: a saved district + output folder → the form names
        # where files go and prefills the saved district (never an alphabetical guess).
        in_dir = tmp_path / "in"
        in_dir.mkdir()
        out_dir = tmp_path / "out"
        cfg = AppConfig(input_dir=str(in_dir), output_dir=str(out_dir), sis_type="myedbc")
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        tree = _assert_renders(lambda: build_convert(stub_page), monkeypatch)
        assert _has_text_containing(tree, "Files will be written to")
        assert _has_text_containing(tree, str(out_dir))
        assert _find(tree, ft.Dropdown)[0].value == "myedbc"

    def _delivery_ready_config(self, tmp_path, monkeypatch, *, with_csv: bool = True) -> AppConfig:
        """A configured install with delivery set up (+ optionally a committed CSV on disk)."""
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        if with_csv:
            (out_dir / "Students.csv").write_text("id\n1\n")
        cfg = AppConfig(
            input_dir=str(tmp_path),
            output_dir=str(out_dir),
            sis_type="myedbc",
            sftp_enabled=True,
            sftp_host="sftp.ca.spacesedu.com",
            sftp_username="district_x",
            sftp_remote_path="/upload",
        )
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        return cfg

    def test_convert_standalone_deliver_card_when_ready(self, tmp_path, stub_page, monkeypatch):
        # 0034 Slice 2: delivery configured + credential readable + CSVs on disk → the standalone
        # card renders, freshness-labelled, with a SECONDARY deliver button (one filled primary).
        import src.ui_flet.screens.convert as convert_mod

        self._delivery_ready_config(tmp_path, monkeypatch)
        monkeypatch.setattr(convert_mod, "_sftp_credential_present", lambda _cfg: True)
        tree = _assert_renders(lambda: build_convert(stub_page), monkeypatch)
        assert _has_text_containing(tree, "Deliver the files in your output folder")
        assert _has_text_containing(tree, "Files last built")
        deliver_btn = _button_by_content(tree, "Deliver to SpacesEDU")
        assert isinstance(deliver_btn, ft.OutlinedButton)  # secondary tier — Convert now keeps the fill
        # The new card must not add a second filled action ("Convert now" holds the screen's fill).
        assert not any(getattr(b, "content", None) == "Deliver to SpacesEDU" for b in _find(tree, ft.FilledButton))

    def test_convert_standalone_deliver_hidden_when_unconfigured(self, stub_page, monkeypatch):
        # The default (unconfigured) install: no delivery setup, no CSVs → no deliver affordance.
        tree = _assert_renders(lambda: build_convert(stub_page), monkeypatch)
        assert not _has_text_containing(tree, "Deliver the files in your output folder")
        assert not _has_text_containing(tree, "Delivery isn't ready on this account")

    def test_convert_standalone_deliver_hidden_without_csvs(self, tmp_path, stub_page, monkeypatch):
        # Delivery configured but nothing on disk to send → the affordance hides (never a dead button).
        import src.ui_flet.screens.convert as convert_mod

        self._delivery_ready_config(tmp_path, monkeypatch, with_csv=False)
        monkeypatch.setattr(convert_mod, "_sftp_credential_present", lambda _cfg: True)
        tree = _assert_renders(lambda: build_convert(stub_page), monkeypatch)
        assert not _has_text_containing(tree, "Deliver the files in your output folder")

    def test_convert_standalone_deliver_not_ready_without_credential(self, tmp_path, stub_page, monkeypatch):
        # Configured + files on disk but no stored password → the calm route-to-Setup card.
        import src.ui_flet.screens.convert as convert_mod

        self._delivery_ready_config(tmp_path, monkeypatch)
        monkeypatch.setattr(convert_mod, "_sftp_credential_present", lambda _cfg: False)
        tree = _assert_renders(lambda: build_convert(stub_page), monkeypatch)
        assert _has_text_containing(tree, "Delivery isn't ready on this account")
        assert not _has_text_containing(tree, "Deliver the files in your output folder")

    def test_home_with_refresh(self, stub_page, monkeypatch):
        # The Refresh affordance (D1) must render on the dashboard branch without
        # crashing — a configured+scheduled config is required to reach that branch
        # (the default unconfigured config renders onboarding, which has no Refresh).
        configured = AppConfig(
            input_dir="in",
            output_dir="out",
            sis_type="myedbc",
            schedule_registered=True,
        )
        tree = _assert_renders(
            lambda: build_home(
                stub_page,
                app_config=configured,
                on_navigate=lambda _d: None,
                on_refresh=lambda: None,
            ),
            monkeypatch,
        )
        # The button label is a plain string on FilledButton.content (see components._filled_button).
        assert any(getattr(c, "content", None) == "Refresh" for c in _iter_controls(tree)), (
            "dashboard branch must render the Refresh affordance"
        )

    def test_home_is_verdict_first(self, stub_page, monkeypatch):
        # Direction B (0033 Slice 2): the dashboard leads with the "Home" page_header title, then
        # the verdict band is the FIRST content element (index 1) — a toned status tint, before any
        # metric tile. Robust to run-store contents: every verdict paints one of the three tints.
        configured = AppConfig(input_dir="in", output_dir="out", sis_type="myedbc", schedule_registered=True)
        tree = _assert_renders(
            lambda: build_home(stub_page, app_config=configured, on_navigate=lambda _d: None),
            monkeypatch,
        )
        assert _has_text(tree, "Home"), "the Home page_header title must render"
        verdict_tints = {
            tokens.color_status_healthy_tint,
            tokens.color_status_warning_tint,
            tokens.color_status_failed_tint,
        }
        assert getattr(tree.controls[1], "bgcolor", None) in verdict_tints, (
            "the verdict band must be the first content element after the page header"
        )

    def test_run_history(self, stub_page, app_cfg, monkeypatch):
        _assert_renders(lambda: build_run_history(stub_page, app_config=app_cfg), monkeypatch)

    def test_run_history_with_refresh(self, stub_page, app_cfg, monkeypatch):
        # The Refresh affordance (D1) must render without crashing.
        _assert_renders(
            lambda: build_run_history(stub_page, app_config=app_cfg, on_refresh=lambda: None),
            monkeypatch,
        )

    def test_mapping(self, stub_page, app_cfg, monkeypatch):
        _assert_renders(lambda: build_mapping(stub_page, app_config=app_cfg), monkeypatch)

    def test_help(self, stub_page, app_cfg, monkeypatch):
        _assert_renders(lambda: build_help(stub_page, app_config=app_cfg), monkeypatch)

    def test_onboarding(self, stub_page, monkeypatch):
        _assert_renders(
            lambda: build_onboarding(stub_page, sis_type="myedbc", on_start_setup=lambda: None),
            monkeypatch,
        )

    def test_design_demo(self, monkeypatch):
        # The DISTRICTSYNC_UI_DEMO override target (Help slot).
        _assert_renders(components.build_design_demo, monkeypatch)


def _iter_controls(control):
    """Depth-first walk of a flet control tree (``.controls`` list + a single ``.content`` child)."""
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
            yield from _iter_controls(child)


def _find(control, cls):
    """All controls of type ``cls`` in the tree rooted at ``control`` (depth-first order)."""
    return [c for c in _iter_controls(control) if isinstance(c, cls)]


def _has_text(tree, exact) -> bool:
    """Whether any control in the tree carries the exact string as its ``value``."""
    return any(getattr(c, "value", None) == exact for c in _iter_controls(tree))


def _has_text_containing(tree, substring) -> bool:
    """Whether any control's ``value`` contains ``substring``."""
    return any(substring in (getattr(c, "value", None) or "") for c in _iter_controls(tree))


def _pick_event(value):
    """A stub Dropdown ``on_select`` event exposing ``e.control.value`` (the handler's read)."""
    evt = MagicMock()
    evt.control.value = value
    return evt


def test_mapping_post_apply_rerenders_and_allows_revert(stub_page, monkeypatch):
    """D1 acceptance: Apply re-renders the surface in place AND a switch can be reverted.

    Drives the real (coverage-omitted) handlers through the built control tree: the Apply gate
    starts disabled (no-op), enables on picking a different config, disables again right after
    Apply (now the persisted current), and — the load-bearing fix — RE-ENABLES when the previous
    mapping is re-selected (reverting was impossible before, since the gate compared against the
    frozen mount instance). ``AppConfig.save`` is stubbed so the interaction never touches the
    real profile (the existing per-file hermetic ``load`` patch covers the reads).
    """
    app_cfg = AppConfig(sis_type="myedbc")  # explicit persisted current (D9 flipped the default to "")
    original = app_cfg.sis_type
    target = "mbp_core"  # a real bundled config, different from the current
    assert original != target
    # `_on_apply` loads + saves AppConfig — keep the save off the real ~/.districtsync.
    monkeypatch.setattr(AppConfig, "save", lambda self: None)

    surface = _assert_renders(lambda: build_mapping(stub_page, app_config=app_cfg), monkeypatch)

    dropdowns = _find(surface, ft.Dropdown)
    assert dropdowns, "the Mapping surface exposes a district dropdown"
    dropdown = dropdowns[0]
    apply_btn = next(b for b in _find(surface, ft.FilledButton) if b.content == "Use this mapping")

    # Initially pending == persisted == original → Apply disabled (a no-op switch).
    assert apply_btn.disabled is True

    # Pick a different, loadable config → Apply enables.
    dropdown.on_select(_pick_event(target))
    assert apply_btn.disabled is False

    # Apply → the switch becomes the persisted current → Apply disables again (no re-apply).
    apply_btn.on_click(None)
    assert apply_btn.disabled is True

    # 0034 Slice 1 honesty: the confirmation claims folders only — never "schedule ... unchanged"
    # (an unchanged registered task still carries the OLD district). With no registered-schedule
    # hint (the hermetic default config) there is nothing to warn about either.
    assert _has_text(surface, "Your folders are unchanged.")
    assert not _has_text_containing(surface, "schedule are unchanged")
    assert not _has_text_containing(surface, "may still use")
    assert not any(getattr(c, "content", None) == "Open Settings" for c in _iter_controls(surface))

    # THE fix: re-selecting the previous mapping is applyable — a switch can be reverted in place.
    dropdown.on_select(_pick_event(original))
    assert apply_btn.disabled is False


def _mapping_apply(surface, target="mbp_core"):
    """Drive the Mapping surface's real handlers: pick ``target``, then Apply."""
    dropdown = _find(surface, ft.Dropdown)[0]
    apply_btn = next(b for b in _find(surface, ft.FilledButton) if b.content == "Use this mapping")
    dropdown.on_select(_pick_event(target))
    apply_btn.on_click(None)
    return dropdown, apply_btn


def _scheduled_mapping_cfg(monkeypatch):
    """A config whose hint says a nightly schedule is registered (the stale-task hazard case)."""
    cfg = AppConfig(sis_type="myedbc", schedule_registered=True)
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    return cfg


def test_mapping_apply_with_registered_hint_warns_hedged_and_routes_to_settings(stub_page, monkeypatch):
    """0034 Slice 1: Apply with a registered-schedule hint paints the HEDGED notice immediately.

    Before any probe returns, the record-based paint must already warn — honestly hedged ("may
    still use", never asserted from the hint alone) — and the "Open Settings" affordance must
    route via ``on_navigate("setup")`` (the Settings Save owns the actual re-registration).
    """
    cfg = _scheduled_mapping_cfg(monkeypatch)
    routed: list[str] = []
    surface = _assert_renders(
        lambda: build_mapping(stub_page, app_config=cfg, on_navigate=routed.append),
        monkeypatch,
    )

    _mapping_apply(surface)

    assert _has_text_containing(surface, "may still use")
    assert _has_text(surface, "Your folders are unchanged.")
    open_settings = next(c for c in _iter_controls(surface) if getattr(c, "content", None) == "Open Settings")
    open_settings.on_click(None)
    assert routed == ["setup"]


def test_mapping_apply_without_on_navigate_warns_without_routing_button(stub_page, monkeypatch):
    """Defensive default: no ``on_navigate`` → the notice still renders, minus the button (no crash)."""
    cfg = _scheduled_mapping_cfg(monkeypatch)
    surface = _assert_renders(lambda: build_mapping(stub_page, app_config=cfg), monkeypatch)

    _mapping_apply(surface)

    assert _has_text_containing(surface, "may still use")
    assert not any(getattr(c, "content", None) == "Open Settings" for c in _iter_controls(surface))


def test_mapping_apply_live_probe_upgrades_to_assertive_notice(monkeypatch):
    """Paint-then-refine (Home's pattern): a LIVE read-back upgrades the hedge to an assertion.

    The off-thread worker is driven inline (``_driving_page``) with ``probe_schedule`` stubbed
    LIVE and the win32 gate pinned, so the marshalled refine runs deterministically on any OS:
    the banner must now say the schedule STILL USES the old district (no more "may").
    """
    import src.ui_flet.screens.mapping as mapping_mod
    from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus

    monkeypatch.setattr(mapping_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        "src.ui_flet.schedule_probe.probe_schedule",
        lambda *a, **k: ScheduleStatus(state=ScheduleState.LIVE, headline="", detail=""),
    )
    cfg = _scheduled_mapping_cfg(monkeypatch)

    captured: list = []
    page = _driving_page(captured)
    surface = build_mapping(page, app_config=cfg, on_navigate=lambda _d: None)

    _mapping_apply(surface)

    assert len(captured) == 1, "Apply must marshal exactly one probe refine"
    coro_fn, _args = captured[0]
    asyncio.run(coro_fn())

    assert _has_text_containing(surface, "still uses")
    assert not _has_text_containing(surface, "may still use")


def test_mapping_stale_probe_never_resurrects_a_cleared_banner(monkeypatch):
    """A fresh pick clears the confirmation; a probe still in flight must NOT repaint it."""
    import src.ui_flet.screens.mapping as mapping_mod
    from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus

    monkeypatch.setattr(mapping_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        "src.ui_flet.schedule_probe.probe_schedule",
        lambda *a, **k: ScheduleStatus(state=ScheduleState.LIVE, headline="", detail=""),
    )
    cfg = _scheduled_mapping_cfg(monkeypatch)

    captured: list = []
    page = _driving_page(captured)
    surface = build_mapping(page, app_config=cfg, on_navigate=lambda _d: None)

    dropdown, _apply_btn = _mapping_apply(surface)
    dropdown.on_select(_pick_event("myedbc"))  # a fresh pick clears the banner slot

    coro_fn, _args = captured[0]
    asyncio.run(coro_fn())  # the (now stale) refine arrives after the pick

    assert not _has_text_containing(surface, "still use")  # neither hedged nor assertive resurrected


def _textfield_by_label(tree, label):
    """The first ``ft.TextField`` whose label EXACTLY equals ``label`` (or None)."""
    return next((f for f in _find(tree, ft.TextField) if (f.label or "") == label), None)


# The five always-present Setup text fields Enter must submit (the Windows-password
# field is Windows-only, so it is not asserted here — it renders + wires only on win32).
_ENTER_SUBMIT_LABELS = [
    "Daily run time (24-hour, HH:MM)",  # → Register
    "Username",  # → Save SFTP
    "Remote path",  # → Save SFTP
    "Port",  # → Save SFTP
    "Password",  # → Save SFTP (exact "Password"; the Windows field is "Windows account password")
]


def test_setup_textfields_wire_enter_to_submit(stub_page, monkeypatch):
    """Slice 2: the run-time + 4 SFTP text fields fire their action on Enter (``on_submit``).

    on_submit bypasses a disabled button, which is why the handlers re-check the gate; here
    we assert the wiring is present (a callable) so Enter behaves like clicking the button.
    Exercised via Settings mode, where the schedule + SFTP sections share the flat scroll.
    """
    tree = _settings_tree(stub_page, monkeypatch)
    for label in _ENTER_SUBMIT_LABELS:
        field = _textfield_by_label(tree, label)
        assert field is not None, f"expected a Setup TextField labelled {label!r}"
        assert callable(field.on_submit), f"{label!r} must wire on_submit (Enter-to-submit)"


def test_setup_enter_respects_gate_when_config_incomplete(stub_page, monkeypatch):
    """Enter on the run-time field with an incomplete config is a silent no-op.

    A Settings-mode config with no folders is incomplete, so ``can_register_schedule`` is False;
    firing ``on_submit`` must return without raising and without registering anything —
    Enter can never bypass the gate the disabled Register button enforces.
    """
    # setup_completed=True → Settings mode; blank folders → is_complete() False → gate closed.
    tree = _settings_tree(stub_page, monkeypatch, input_dir="", output_dir="")
    run_time = _textfield_by_label(tree, "Daily run time (24-hour, HH:MM)")
    assert run_time is not None
    assert run_time.on_submit(None) is None  # no-op: gate closed, no raise


def test_nav_rail_builds_and_exposes_rail_handle():
    """D7 render-smoke: build_nav constructs the fixed-order rail WITHOUT raising and hands
    the shell the rail handle (so it can sync the highlight on programmatic navigation).

    The rail is coverage-omitted view glue; this mount-smoke catches a flet-0.85.3 API
    drift in the rail the same way TestScreensRender guards the screens.
    """
    from src.ui_flet import nav, nav_rail
    from src.utils.version import app_version

    ordered = nav.ordered_destinations(nav.nav_model(AppConfig()))
    view, rail = nav_rail.build_nav(
        ordered=ordered,
        selected_id="setup",
        on_select=lambda _id: None,
        on_exit=lambda *_a: None,
    )
    assert isinstance(view, ft.Control)
    assert isinstance(rail, ft.NavigationRail)
    # Initial highlight is the fixed-order index of selected_id (single-sourced mapping).
    assert rail.selected_index == nav.selected_index_for("setup", ordered)
    # One rail destination per ordered entry — fixed order, nothing dropped.
    assert len(rail.destinations) == len(ordered)
    # Direction B navy rail (0033 Slice 2): navy bg + the app-version foot line in the trailing.
    assert rail.bgcolor == tokens.color_rail_bg
    assert _has_text_containing(rail.trailing, f"v{app_version()}"), "the rail must show the version caption"


def test_nav_rail_renders_setup_attention_badge():
    """D4/D7 render-smoke: an ``attention_ids`` set puts a badge on that rail destination only.

    The shell raises this badge on Setup when the off-thread schedule read-back reports a
    missing/contradicted schedule; here we prove the rail renders it without a flet-API crash.
    """
    from src.ui_flet import nav, nav_rail

    ordered = nav.ordered_destinations(nav.nav_model(AppConfig()))
    _view, rail = nav_rail.build_nav(
        ordered=ordered,
        selected_id="home",
        on_select=lambda _id: None,
        on_exit=lambda *_a: None,
        attention_ids=frozenset({"setup"}),
    )
    setup_idx = nav.selected_index_for("setup", ordered)
    home_idx = nav.selected_index_for("home", ordered)
    assert rail.destinations[setup_idx].badge is not None  # Setup badged
    assert rail.destinations[home_idx].badge is None  # nothing else badged


def test_setup_renders_unregister_affordance(stub_page, monkeypatch):
    """Slice 5: the schedule section exposes an Unregister affordance (cross-platform).

    Exercised via Settings mode, where the schedule section renders at the top level.
    """
    tree = _settings_tree(stub_page, monkeypatch)
    assert any(getattr(c, "content", None) == "Unregister schedule" for c in _iter_controls(tree)), (
        "the schedule section must render an Unregister button"
    )


def _driving_page(captured):
    """A page stub that runs off-thread workers synchronously and captures ``run_task`` calls."""
    page = MagicMock()
    page.run_thread = lambda fn: fn()  # run the worker body inline
    page.run_task = lambda coro, *args: captured.append((coro, args))
    return page


def _button_by_content(tree, content):
    return next(c for c in _iter_controls(tree) if getattr(c, "content", None) == content)


def _complete_config(monkeypatch):
    from src.config.app_config import AppConfig

    # setup_completed=True → Settings mode, so the schedule section (Register/Unregister) renders
    # at the top level; the register/unregister flow is shared verbatim with the wizard step.
    cfg = AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc", setup_completed=True)
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    # Keep the on-mount + post-outcome readout probe from firing a real PowerShell subprocess.
    from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus

    benign = ScheduleStatus(state=ScheduleState.UNKNOWN, headline="", detail="")
    monkeypatch.setattr("src.ui_flet.schedule_probe.probe_schedule", lambda *a, **k: benign)
    return cfg


def test_register_worker_survives_crash(monkeypatch):
    """D5 crash-net: an off-thread register worker that RAISES must still marshal a calm
    result (spinner + buttons released) instead of stranding the UI forever."""
    from src.ui_flet.screens.setup import _WORKER_ERROR_REGISTER

    _complete_config(monkeypatch)

    def _boom(*a, **k):
        raise OSError("injected worker crash")

    # Patch both platform entry points so the worker crashes on Windows AND Linux CI.
    monkeypatch.setattr("src.scheduler.windows.register_task", _boom)
    monkeypatch.setattr("src.scheduler.linux.register_cron", _boom)

    captured: list = []
    tree = build_setup(_driving_page(captured))
    captured.clear()  # discard the on-mount readout probe marshal
    _button_by_content(tree, "Register schedule").on_click(None)

    assert len(captured) == 1, "the crashed worker must marshal exactly one result (no strand)"
    _coro, args = captured[0]
    assert args == (False, _WORKER_ERROR_REGISTER)


def test_unregister_worker_survives_crash(monkeypatch):
    """D5 crash-net: same guarantee for the unregister worker."""
    from src.ui_flet.screens.setup import _WORKER_ERROR_UNREGISTER

    _complete_config(monkeypatch)

    def _boom(*a, **k):
        raise OSError("injected worker crash")

    monkeypatch.setattr("src.scheduler.windows.delete_task", _boom)
    monkeypatch.setattr("src.scheduler.linux.delete_cron", _boom)

    captured: list = []
    tree = build_setup(_driving_page(captured))
    captured.clear()
    _button_by_content(tree, "Unregister schedule").on_click(None)

    assert len(captured) == 1, "the crashed worker must marshal exactly one result (no strand)"
    _coro, args = captured[0]
    assert args == (False, _WORKER_ERROR_UNREGISTER)


class TestWizardStepsRender:
    """Slice 8 render-smoke: EVERY wizard step + finish → the transition cue mounts without crashing.

    ``build_setup`` renders only the resume step, so folders/district steps are reached by pointing
    real state at them, and the schedule → delivery → finish → cue path is driven via the actual
    forward buttons (the wizard mutates the root Column in place, so the same ``tree`` is re-read).
    """

    def _wizard_tree(self, stub_page, monkeypatch, cfg):
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
        return build_setup(stub_page)

    def test_district_step_renders_with_placeholder(self, stub_page, monkeypatch):
        # 2026-07-15 reorder: District is now step 1 — a fresh config lands here with the placeholder.
        tree = self._wizard_tree(stub_page, monkeypatch, AppConfig())  # unconfigured → District (step 1)
        assert _has_text(tree, "Step 1 of 5")
        # #4: the wizard opens with ONE orientation line (now on the District step, not a cold dropdown).
        assert _has_text_containing(tree, "keeps your MyEd BC roster flowing to SpacesEDU")
        dropdown = _find(tree, ft.Dropdown)[0]
        assert dropdown.hint_text == "Choose your district"  # D9 placeholder, no pre-selection
        assert dropdown.value is None

    def test_folders_step_renders_gated(self, stub_page, monkeypatch):
        # District chosen but folders invalid → the wizard lands on the Folders step (now step 2).
        cfg = AppConfig(sis_type="myedbc")  # district chosen, blank/invalid folders
        tree = self._wizard_tree(stub_page, monkeypatch, cfg)
        assert _has_text(tree, "Step 2 of 5")
        cont = _button_by_content(tree, "Continue")
        assert cont.disabled is True  # folders invalid → Continue gated (Enter can't bypass)

    def test_delivery_schedule_finish_and_transition_cue(self, tmp_path, stub_page, monkeypatch):
        in_dir = tmp_path / "in"
        in_dir.mkdir()
        cfg = AppConfig(input_dir=str(in_dir), output_dir=str(tmp_path / "out"), sis_type="myedbc")
        tree = self._wizard_tree(stub_page, monkeypatch, cfg)  # folders + district → Delivery (F1 order)

        # Delivery step (step 3, skippable) reuses the SFTP section — configured BEFORE the task is baked.
        assert _has_text(tree, "Step 3 of 5")
        assert _textfield_by_label(tree, "Username") is not None
        _button_by_content(tree, "Set up later").on_click(None)  # defer delivery → Schedule

        # Schedule step (step 4, skippable) reuses the register/unregister section.
        assert _has_text(tree, "Step 4 of 5")
        assert any(getattr(c, "content", None) == "Register schedule" for c in _iter_controls(tree))
        _button_by_content(tree, "Set up later").on_click(None)  # defer schedule → Finish

        # Finish step: neutral step title (#5) + the schedule-skipped honest headline (#1a) + copy.
        assert _has_text(tree, "Step 5 of 5")
        assert _has_text(tree, "Finish")  # #5: neutral step title, banner owns the peak
        assert _has_text(tree, "You're set up — nightly sync not scheduled yet")  # #1a adaptive headline
        assert _has_text_containing(tree, "Run conversions from the Convert tab")
        assert any(getattr(c, "content", None) == "Finish setup" for c in _iter_controls(tree))

        # Confirming graduates the surface to Settings with the one-time transition cue.
        _button_by_content(tree, "Finish setup").on_click(None)
        assert cfg.setup_completed is True
        assert _has_text(tree, "Settings")
        assert _has_text_containing(tree, "this is now your Settings page")

    def test_live_schedule_finish_consumes_next_run(self, tmp_path, stub_page, monkeypatch):
        """#7: the finish BODY consumes a LIVE read-back's next_run_display ("Tonight at HH:MM").

        Cross-platform: the schedule section is stubbed to fire ``on_status`` with a LIVE status
        synchronously on build (no PowerShell probe / no is_windows dependency), so the wizard's
        on_status→finish-copy wiring is exercised on every OS.
        """
        import src.ui_flet.screens.setup as setup_mod
        from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus

        live = ScheduleStatus(state=ScheduleState.LIVE, headline="", detail="", next_run_display="3:00 AM")

        def _stub_schedule(page, config, *, on_status=None, on_registered=None):
            if on_status is not None:
                on_status(live)  # deliver a LIVE read-back the moment the Schedule step builds
            return ft.Text("schedule"), setup_mod._ScheduleHandle(
                trigger_register=lambda: None,
                run_time_value=lambda: "03:00",
                persist_run_time=lambda: False,
            )

        monkeypatch.setattr(setup_mod, "_build_schedule_section", _stub_schedule)

        in_dir = tmp_path / "in"
        in_dir.mkdir()
        cfg = AppConfig(input_dir=str(in_dir), output_dir=str(tmp_path / "out"), sis_type="myedbc")
        tree = self._wizard_tree(stub_page, monkeypatch, cfg)  # folders + district → Delivery (F1 order)

        _button_by_content(tree, "Set up later").on_click(None)  # defer delivery → Schedule (stub fires LIVE)
        _button_by_content(tree, "Continue").on_click(None)  # schedule addressed (LIVE) → Finish

        assert _has_text_containing(tree, "Tonight at 3:00 AM")  # LIVE next-run consumed by the finish body


def test_settings_save_reconciles_reregistration_when_scheduled(tmp_path, stub_page, monkeypatch):
    """D8: changing a task-baked field in Settings re-registers the live schedule (same flow).

    A completed + scheduled config, whose output folder is then edited, must drive the schedule
    section's register flow on Save (``task_args_changed`` True). The register trigger is spied.
    """
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"
    new_out = tmp_path / "new_out"
    cfg = AppConfig(
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        sis_type="myedbc",
        setup_completed=True,
        schedule_registered=True,
    )
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(AppConfig, "save", lambda self: None)

    triggered = {"count": 0}
    # Spy the schedule section's register trigger via the handle the reconcile drives.
    import src.ui_flet.screens.setup as setup_mod
    from src.ui_flet.picker_field import PickerField

    real_build_schedule = setup_mod._build_schedule_section

    def _spy_build(page, config, **kw):
        card, handle = real_build_schedule(page, config, **kw)
        handle.trigger_register = lambda: triggered.__setitem__("count", triggered["count"] + 1)
        return card, handle

    monkeypatch.setattr(setup_mod, "_build_schedule_section", _spy_build)

    tree = build_setup(stub_page)
    # 0034 S3-c scope-accurate relabel: the folders card's Save names what it saves.
    save_btn = _button_by_content(tree, "Save folders & district")

    # The folders card holds two PickerFields; simulate a pick on the second (output).
    output_picker = [c for c in _iter_controls(tree) if isinstance(c, PickerField)][1]
    output_picker.value = str(new_out)
    output_picker._on_change(str(new_out), output_picker._validator(str(new_out)))
    save_btn.on_click(None)

    assert triggered["count"] == 1, "editing the output folder must re-register the live schedule"


# --------------------------------------------------------------------------- #
# F1 composition regressions — the sftp flag must reach the registered action.  #
# --------------------------------------------------------------------------- #
def _dropdown_by_label(tree, label):
    return next(d for d in _find(tree, ft.Dropdown) if (d.label or "") == label)


def _benign_probe(monkeypatch):
    from src.ui_flet.schedule_status import ScheduleState, ScheduleStatus

    monkeypatch.setattr(
        "src.ui_flet.schedule_probe.probe_schedule",
        lambda *a, **k: ScheduleStatus(state=ScheduleState.UNKNOWN, headline="", detail=""),
    )


def _capture_register_sftp(monkeypatch) -> dict:
    """Mock both register entry points to record the ``sftp`` flag baked into the action."""
    recorded: dict = {}

    def _fake_register(**kwargs):
        recorded["sftp"] = kwargs.get("sftp")
        return True, "ok"

    def _fake_cron(exe, sis, inp, out, run_time, *, sftp=False):
        recorded["sftp"] = sftp
        return True, "ok"

    monkeypatch.setattr("src.scheduler.windows.register_task", _fake_register)
    monkeypatch.setattr("src.scheduler.linux.register_cron", _fake_cron)
    return recorded


def _fill_sftp(tree):
    _dropdown_by_label(tree, "SFTP host (SpacesEDU)").value = "sftp.ca.spacesedu.com"
    _textfield_by_label(tree, "Username").value = "district_x"
    _textfield_by_label(tree, "Remote path").value = "/files"
    _textfield_by_label(tree, "Password").value = "pw"


def test_wizard_natural_order_bakes_sftp_into_registered_task(tmp_path, monkeypatch):
    """F1(a): a natural in-order wizard walk that configures Delivery BEFORE the Schedule step
    registers a task WITH --sftp (delivery precedes schedule, so cfg.sftp_enabled is set at bake)."""
    from src.sftp.uploader import SFTPUploader

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    cfg = AppConfig(input_dir=str(in_dir), output_dir=str(tmp_path / "out"), sis_type="myedbc")
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    monkeypatch.setattr(SFTPUploader, "store_password", lambda self, pw: None)
    monkeypatch.setattr(SFTPUploader, "get_stored_password", lambda self: "pw")
    _benign_probe(monkeypatch)
    recorded = _capture_register_sftp(monkeypatch)

    captured: list = []
    tree = build_setup(_driving_page(captured))  # folders + district done → Delivery (step 3, F1 order)

    _fill_sftp(tree)
    _button_by_content(tree, "Save SFTP credentials").on_click(None)  # cfg.sftp_enabled = True
    assert cfg.sftp_enabled is True

    _button_by_content(tree, "Continue").on_click(None)  # delivery addressed → Schedule (step 4)
    _button_by_content(tree, "Register schedule").on_click(None)  # bakes the task

    assert recorded.get("sftp") is True, "the task registered after Delivery must carry --sftp"


def test_settings_enabling_sftp_reregisters_task_with_sftp(monkeypatch, tmp_path):
    """F1(b): Settings — a registered schedule + newly enabled SFTP + Save re-registers WITH --sftp."""
    from src.sftp.uploader import SFTPUploader

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    cfg = AppConfig(
        input_dir=str(in_dir),
        output_dir=str(tmp_path / "out"),
        sis_type="myedbc",
        setup_completed=True,
        schedule_registered=True,
        sftp_enabled=False,
        schedule_time="03:00",
    )
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    monkeypatch.setattr(SFTPUploader, "store_password", lambda self, pw: None)
    monkeypatch.setattr(SFTPUploader, "get_stored_password", lambda self: "pw")
    _benign_probe(monkeypatch)
    recorded = _capture_register_sftp(monkeypatch)

    captured: list = []
    tree = build_setup(_driving_page(captured))  # Settings mode (completed config)

    _fill_sftp(tree)
    _button_by_content(tree, "Save SFTP credentials").on_click(None)  # enable SFTP → reconcile re-register

    assert cfg.sftp_enabled is True
    assert recorded.get("sftp") is True, "enabling SFTP on a scheduled install must re-register with --sftp"


# --------------------------------------------------------------------------- #
# 0034 Slice 3 — Settings Save trustworthiness (drives the REAL setup handlers). #
# --------------------------------------------------------------------------- #
def _settings_dirs(tmp_path):
    """A real (existing) input dir + a structurally-valid output dir for the Save gate."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    return in_dir, tmp_path / "out"


def _record_register(monkeypatch) -> dict:
    """Record every register call (both platform entry points) + the kwargs that matter."""
    recorded: dict = {"called": 0}

    def _fake_register(**kwargs):
        recorded["called"] += 1
        recorded["run_as_password"] = kwargs.get("run_as_password")
        recorded["sis_type"] = kwargs.get("sis_type")
        return True, "ok"

    def _fake_cron(exe, sis, inp, out, run_time, *, sftp=False):
        recorded["called"] += 1
        recorded["run_as_password"] = None  # cron has no logon-type concept
        recorded["sis_type"] = sis
        return True, "ok"

    monkeypatch.setattr("src.scheduler.windows.register_task", _fake_register)
    monkeypatch.setattr("src.scheduler.linux.register_cron", _fake_cron)
    return recorded


def _registered_settings_cfg(tmp_path, monkeypatch, *, unattended, old_sis="myedbc", current_sis="mbp_core"):
    """A Settings-mode config whose LIVE task was registered with ``old_sis`` (the durable record)
    while the config now carries ``current_sis`` — the post-Mapping-switch / post-restart state."""
    in_dir, out_dir = _settings_dirs(tmp_path)
    old_args = task_args_to_persisted(
        TaskArgs.of(
            input_dir=str(in_dir),
            output_dir=str(out_dir),
            sis_type=old_sis,
            sftp_enabled=False,
            run_time="03:00",
        )
    )
    cfg = AppConfig(
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        sis_type=current_sis,
        setup_completed=True,
        schedule_registered=True,
        schedule_time="03:00",
        schedule_unattended=unattended,
        schedule_task_args=old_args,
    )
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    _benign_probe(monkeypatch)
    return cfg


def _spy_trigger(monkeypatch) -> dict:
    """Spy the schedule handle's reconcile trigger (the seam the Settings Save drives)."""
    import src.ui_flet.screens.setup as setup_mod

    triggered = {"count": 0}
    real_build = setup_mod._build_schedule_section

    def _spy_build(page, config, **kw):
        card, handle = real_build(page, config, **kw)
        handle.trigger_register = lambda: triggered.__setitem__("count", triggered["count"] + 1)
        return card, handle

    monkeypatch.setattr(setup_mod, "_build_schedule_section", _spy_build)
    return triggered


def test_settings_run_time_edit_survives_save_with_no_schedule_registered(tmp_path, stub_page, monkeypatch):
    """S3-b acceptance: an edited run time persists on Save + survives an AppConfig reload
    (read back from the on-disk config.json) even when NO schedule is registered."""
    in_dir, out_dir = _settings_dirs(tmp_path)
    cfg = AppConfig(
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        sis_type="myedbc",
        setup_completed=True,
        schedule_registered=False,
    )
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    # save() is REAL — it writes to the per-test isolated app-data dir (conftest seam).

    tree = build_setup(stub_page)
    _textfield_by_label(tree, "Daily run time (24-hour, HH:MM)").value = "04:30"
    _button_by_content(tree, "Save folders & district").on_click(None)

    assert cfg.schedule_time == "04:30"
    on_disk = json.loads(config_file_path().read_text(encoding="utf-8"))
    assert on_disk["schedule_time"] == "04:30"  # survives a reload — it's on disk
    assert on_disk["schedule_registered"] is False  # persisting the time registered nothing


def test_settings_save_with_invalid_run_time_edit_persists_nothing_and_paints_the_inline_error(
    tmp_path, stub_page, monkeypatch
):
    """S3-b: an invalid run-time edit persists NOTHING and surfaces the existing inline error."""
    in_dir, out_dir = _settings_dirs(tmp_path)
    cfg = AppConfig(
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        sis_type="myedbc",
        setup_completed=True,
        schedule_registered=False,
    )
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))

    tree = build_setup(stub_page)
    _textfield_by_label(tree, "Daily run time (24-hour, HH:MM)").value = "25:99"
    _button_by_content(tree, "Save folders & district").on_click(None)

    assert cfg.schedule_time == "03:00"  # untouched
    assert json.loads(config_file_path().read_text(encoding="utf-8"))["schedule_time"] == "03:00"
    assert _has_text(tree, "That run time isn't valid")  # the register flow's exact inline error


def test_no_edit_save_after_mapping_switch_reregisters_from_the_persisted_args(tmp_path, stub_page, monkeypatch):
    """S3-d acceptance: after a Mapping district switch (config saved elsewhere), a Settings Save
    with NO field edits must still re-register — the reconcile compares against the durable
    last-REGISTERED args, not a mount-time snapshot of the already-switched config."""
    _registered_settings_cfg(tmp_path, monkeypatch, unattended=False)
    triggered = _spy_trigger(monkeypatch)

    tree = build_setup(stub_page)
    _button_by_content(tree, "Save folders & district").on_click(None)  # NO edits

    assert triggered["count"] == 1, "a no-edit Save must re-register the task with the new district"


def test_no_edit_save_without_a_persisted_record_falls_back_to_the_mount_snapshot(tmp_path, stub_page, monkeypatch):
    """S3-d back-compat: an install that registered before the record existed keeps the old
    (mount-snapshot) reconcile — a no-edit Save does not re-register (documented limitation
    until its next successful register writes the record)."""
    cfg = _registered_settings_cfg(tmp_path, monkeypatch, unattended=False)
    cfg.schedule_task_args = None  # pre-record install
    triggered = _spy_trigger(monkeypatch)

    tree = build_setup(stub_page)
    _button_by_content(tree, "Save folders & district").on_click(None)

    assert triggered["count"] == 0


def test_save_reregister_of_an_unattended_task_without_a_password_interrupts(tmp_path, monkeypatch):
    """S3-a acceptance: a reconcile re-register that WOULD downgrade an unattended task pauses on
    the explicit-choice dialog — no register fires until the admin chooses."""
    _registered_settings_cfg(tmp_path, monkeypatch, unattended=True)
    recorded = _record_register(monkeypatch)

    captured: list = []
    page = _driving_page(captured)
    tree = build_setup(page)
    _button_by_content(tree, "Save folders & district").on_click(None)

    assert recorded["called"] == 0, "no silent downgrade-register may fire"
    dialog = page.show_dialog.call_args[0][0]
    assert isinstance(dialog, ft.AlertDialog)
    labels = [getattr(b, "content", None) for b in dialog.actions]
    interrupt = DowngradeInterrupt()
    assert interrupt.keep_unattended_label in labels
    assert interrupt.signed_in_only_label in labels
    assert interrupt.cancel_label in labels


def _open_downgrade_dialog(tmp_path, monkeypatch):
    """Drive a Settings Save into the downgrade dialog; return (cfg, recorded, page, tree, dialog)."""
    cfg = _registered_settings_cfg(tmp_path, monkeypatch, unattended=True)
    recorded = _record_register(monkeypatch)
    captured: list = []
    page = _driving_page(captured)
    tree = build_setup(page)
    captured.clear()  # discard the on-mount readout-probe marshal
    _button_by_content(tree, "Save folders & district").on_click(None)
    dialog = page.show_dialog.call_args[0][0]
    return cfg, recorded, captured, page, tree, dialog


def _dialog_action(dialog, label):
    return next(b for b in dialog.actions if getattr(b, "content", None) == label)


def test_downgrade_choice_signed_in_only_reregisters_and_updates_the_facts_honestly(tmp_path, monkeypatch):
    """S3-a: choosing "signed in only" registers with a BLANK password (logged-on-only) and the
    persisted facts update honestly (unattended=False; args now carry the new district)."""
    cfg, recorded, captured, _page, _tree, dialog = _open_downgrade_dialog(tmp_path, monkeypatch)

    _dialog_action(dialog, DowngradeInterrupt().signed_in_only_label).on_click(None)

    assert recorded["called"] == 1
    assert recorded["run_as_password"] is None  # blank password → Interactive / logged-on-only
    coro, args = captured[-1]
    asyncio.run(coro(*args))  # the register worker's marshalled _apply_result
    assert cfg.schedule_registered is True
    assert cfg.schedule_unattended is False  # the flag now tells the truth
    assert cfg.schedule_task_args is not None
    assert cfg.schedule_task_args["sis_type"] == "mbp_core"  # baseline = what was just registered


def test_downgrade_choice_cancel_leaves_the_task_and_the_facts_untouched(tmp_path, monkeypatch):
    """S3-a: Cancel = no change — nothing registers, the durable record keeps the old task's
    truth, and the schedule section records honestly that the schedule was not updated."""
    cfg, recorded, _captured, _page, tree, dialog = _open_downgrade_dialog(tmp_path, monkeypatch)

    _dialog_action(dialog, DowngradeInterrupt().cancel_label).on_click(None)

    assert recorded["called"] == 0
    assert cfg.schedule_unattended is True  # record untouched — the task is still unattended
    assert cfg.schedule_task_args["sis_type"] == "myedbc"  # still the old task's args
    assert _has_text(tree, DowngradeInterrupt().cancelled_headline)


def test_downgrade_choice_keep_routes_to_the_password_flow_and_a_later_save_reprompts(tmp_path, monkeypatch):
    """S3-a: choosing "keep unattended" never collects the password in the dialog — it routes to
    the existing schedule-section field flow; and because the task is untouched, a later Save
    re-prompts (the baseline still holds the old args)."""
    cfg, recorded, _captured, page, tree, dialog = _open_downgrade_dialog(tmp_path, monkeypatch)

    _dialog_action(dialog, DowngradeInterrupt().keep_unattended_label).on_click(None)

    assert recorded["called"] == 0  # nothing registered yet — the admin still holds the choice
    assert cfg.schedule_unattended is True
    assert _has_text(tree, DowngradeInterrupt().keep_next_headline)

    _button_by_content(tree, "Save folders & district").on_click(None)  # the promise stays live
    assert page.show_dialog.call_count == 2, "an unresolved downgrade must re-prompt on the next Save"


def test_reconcile_without_the_unattended_fact_never_shows_the_dialog(tmp_path, monkeypatch):
    """S3-a scope: a re-register of a logged-on-only task can't downgrade anything — it proceeds
    directly (and S3-d end-to-end: the record updates to the new district on success)."""
    cfg = _registered_settings_cfg(tmp_path, monkeypatch, unattended=False)
    recorded = _record_register(monkeypatch)

    captured: list = []
    page = _driving_page(captured)
    tree = build_setup(page)
    captured.clear()
    _button_by_content(tree, "Save folders & district").on_click(None)

    assert page.show_dialog.call_count == 0
    assert recorded["called"] == 1
    assert recorded["sis_type"] == "mbp_core"  # the task now carries the NEW district
    coro, args = captured[-1]
    asyncio.run(coro(*args))
    assert cfg.schedule_task_args["sis_type"] == "mbp_core"


def test_sftp_save_reconcile_is_also_guarded_against_the_silent_downgrade(tmp_path, monkeypatch):
    """S3-a: the SECOND reconcile route (Save SFTP credentials → on_saved) converges on the same
    guarded trigger — enabling delivery on an unattended install interrupts too, never downgrades."""
    from src.sftp.uploader import SFTPUploader

    cfg = _registered_settings_cfg(tmp_path, monkeypatch, unattended=True, current_sis="myedbc")
    monkeypatch.setattr(SFTPUploader, "store_password", lambda self, pw: None)
    monkeypatch.setattr(SFTPUploader, "get_stored_password", lambda self: "pw")
    recorded = _record_register(monkeypatch)

    captured: list = []
    page = _driving_page(captured)
    tree = build_setup(page)
    _fill_sftp(tree)
    _button_by_content(tree, "Save SFTP credentials").on_click(None)  # flips sftp_enabled → changed

    assert cfg.sftp_enabled is True
    assert recorded["called"] == 0, "the SFTP-save reconcile must not silently downgrade either"
    assert isinstance(page.show_dialog.call_args[0][0], ft.AlertDialog)


def test_register_without_a_password_persists_the_registration_facts(tmp_path, monkeypatch):
    """S3-a/d: EVERY confirmed register (here the plain button, shared verbatim with the wizard's
    Schedule step) records the two durable facts — unattended=False and the exact task args."""
    in_dir, out_dir = _settings_dirs(tmp_path)
    cfg = AppConfig(
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        sis_type="myedbc",
        setup_completed=True,
        schedule_registered=False,
    )
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    _benign_probe(monkeypatch)
    recorded = _record_register(monkeypatch)

    captured: list = []
    tree = build_setup(_driving_page(captured))
    captured.clear()
    _button_by_content(tree, "Register schedule").on_click(None)

    assert recorded["called"] == 1
    coro, args = captured[-1]
    asyncio.run(coro(*args))
    assert cfg.schedule_registered is True
    assert cfg.schedule_unattended is False  # no password → honestly NOT unattended
    assert cfg.schedule_task_args == {
        "input_dir": str(in_dir),
        "output_dir": str(out_dir),
        "sis_type": "myedbc",
        "sftp_enabled": False,
        "run_time": "03:00",
    }


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows password field renders only on win32")
def test_register_with_a_password_persists_the_unattended_fact(tmp_path, monkeypatch):
    """S3-a: a password-backed register records unattended=True (the fact the downgrade guard
    reads on the next reconcile). The password itself is never persisted (I1/I3 unchanged)."""
    in_dir, out_dir = _settings_dirs(tmp_path)
    cfg = AppConfig(
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        sis_type="myedbc",
        setup_completed=True,
        schedule_registered=False,
    )
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    _benign_probe(monkeypatch)
    recorded = _record_register(monkeypatch)

    captured: list = []
    tree = build_setup(_driving_page(captured))
    _textfield_by_label(tree, "Windows account password").value = "hunter2"
    captured.clear()
    _button_by_content(tree, "Register schedule").on_click(None)

    assert recorded["run_as_password"] == "hunter2"  # the ONLY sink — register_task
    coro, args = captured[-1]
    asyncio.run(coro(*args))
    assert cfg.schedule_unattended is True
    assert cfg.schedule_task_args is not None
    assert "hunter2" not in json.dumps(cfg.schedule_task_args)  # never persisted anywhere


def test_unregister_clears_the_registration_facts(tmp_path, monkeypatch):
    """S3: a confirmed unregister clears BOTH durable facts — no task, no record."""
    cfg = _registered_settings_cfg(tmp_path, monkeypatch, unattended=True)
    monkeypatch.setattr("src.scheduler.windows.delete_task", lambda name: (True, "ok"))
    monkeypatch.setattr("src.scheduler.linux.delete_cron", lambda: (True, "ok"))

    captured: list = []
    tree = build_setup(_driving_page(captured))
    captured.clear()
    _button_by_content(tree, "Unregister schedule").on_click(None)

    coro, args = captured[-1]
    asyncio.run(coro(*args))
    assert cfg.schedule_registered is False
    assert cfg.schedule_unattended is False
    assert cfg.schedule_task_args is None


def test_convert_output_folder_row_renders_open_folder():
    """D10 render-smoke: the post-run output-folder row constructs with an "Open folder" button.

    The row is only reached inside ``_render_result`` after a committed run, so mount it directly
    to catch a flet-0.85.3 API drift the same way the screen smokes guard the full builds.
    """
    from src.ui_flet.screens.convert import _output_folder_row

    row = _output_folder_row(r"C:\Users\admin\output")
    assert isinstance(row, ft.Control)
    assert any(getattr(c, "content", None) == "Open folder" for c in _iter_controls(row))
    assert _has_text_containing(row, r"C:\Users\admin\output")


def test_no_ft_dropdown_uses_on_change():
    """Guard the specific trap: ft.Dropdown has NO on_change on 0.85.3 (use on_select).

    A static check so a re-introduced ``ft.Dropdown(on_change=...)`` or
    ``some_dropdown.on_change =`` fails even if a new screen isn't added above.
    """
    import ast
    import glob
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders: list[str] = []
    for path in glob.glob(os.path.join(root, "src", "ui_flet", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            # ft.Dropdown(on_change=...)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Dropdown"
                and any(kw.arg == "on_change" for kw in node.keywords)
            ):
                offenders.append(f"{os.path.relpath(path, root)}:{node.lineno} ft.Dropdown(on_change=)")
    assert not offenders, "ft.Dropdown has no on_change on flet 0.85.3 — use on_select: " + "; ".join(offenders)
