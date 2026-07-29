"""The shell's BOOT ORDER — and the launch gate that now sits inside it (plan 0038 S4a).

Two halves, written in this order on purpose:

1. **Characterisation** (``TestBootInvariantsSurviveTheRefactor``) — the properties the
   pre-S4a ``shell.main`` already had, written and observed GREEN against the OLD shell
   BEFORE the ``build_app_body``/``root_host`` refactor, so the refactor has something to
   break. A refactor whose only evidence is a green end state proves nothing about what it
   preserved.
2. **The gate** — the launch page mounting between geometry and the app body, and the
   floor that guarantees identity can never fail closed.

``shell.py`` is coverage-omitted view glue, but these are the load-bearing lifecycle facts
(the PLAT-0 zero-orphan close and the never-trap floor), so they are asserted directly.

A plain ``MagicMock`` page is enough: ``main`` constructs controls and closes over ``page``
for handlers — no live session is needed at build time. Every test runs under
``isolated_user_profile`` because ``main`` calls ``AppConfig.load()``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

from src.config.app_config import AppConfig
from src.ui_flet import shell
from src.ui_flet.screens import home
from tests.test_app_config_identity import V38X_CONFIG_JSON


@pytest.fixture
def page() -> MagicMock:
    """A stub page whose ``window``/``add``/``update`` are recording no-ops."""
    return MagicMock()


def _write_config(profile: Path, **values: object) -> None:
    """Plant a settings file on disk (the fixture's dir is created lazily on first use)."""
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "config.json").write_text(json.dumps(values), encoding="utf-8")


def _iter_controls(control):  # noqa: ANN001, ANN202 - a test walker over an untyped tree
    yield control
    for attr in ("controls", "content", "actions"):
        child = getattr(control, attr, None)
        if child is None:
            continue
        for item in child if isinstance(child, list) else [child]:
            if isinstance(item, ft.Control):
                yield from _iter_controls(item)


def _added_root(page: MagicMock) -> ft.Control:
    """The ONE control handed to ``page.add`` — the root host."""
    assert page.add.call_count == 1, f"page.add called {page.add.call_count} times, expected exactly 1"
    return page.add.call_args[0][0]


def _texts(control) -> list[str]:  # noqa: ANN001 - untyped Flet tree
    return [c.value for c in _iter_controls(control) if isinstance(getattr(c, "value", None), str)]


def _shows_launch_page(page: MagicMock) -> bool:
    """Is the LAUNCH PAGE mounted? Decided STRUCTURALLY, never by its headline.

    S4b gave Home's identity card the same headline the launch page uses ("Who looks after
    this sync?") on purpose — one fact, one wording — which makes a text-only proxy
    ambiguous: it now matches BOTH the surface we are checking for and the card that
    proves we are past it.

    The positive half is the ESCAPE, not "Continue": the setup wizard labels a button
    "Continue" too, so that half would be doing nothing and ``not has_rail`` would silently
    be carrying the whole predicate — the same weak-proxy shape this helper exists to fix.
    ``SKIP_LABEL`` ("I'm not the person who looks after this sync") is on every launch-page
    state and on no other surface in the app.
    """
    root = _added_root(page)
    controls = list(_iter_controls(root))
    has_rail = any(isinstance(c, ft.NavigationRail) for c in controls)
    has_escape = any(getattr(c, "content", None) == shell.identity.SKIP_LABEL for c in controls)
    return has_escape and not has_rail


def _has_rail(page: MagicMock) -> bool:
    """The app body's structural marker — the positive twin of every launch-page absence."""
    return any(isinstance(c, ft.NavigationRail) for c in _iter_controls(_added_root(page)))


def _button_labelled(control, label: str):  # noqa: ANN001, ANN202 - untyped Flet tree
    """Find a button by its LABEL — which on flet 0.85.3 is ``content``, never ``text``."""
    buttons = [c for c in _iter_controls(control) if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton))]
    for candidate in buttons:
        if candidate.content == label:
            return candidate
    raise AssertionError(f"no button labelled {label!r}; found: {[b.content for b in buttons]}")


# --------------------------------------------------------------------------- #
# 1. Characterisation — properties the pre-refactor shell already had          #
# --------------------------------------------------------------------------- #
class TestBootInvariantsSurviveTheRefactor:
    """Observed green on the OLD shell before ``build_app_body`` existed."""

    def test_page_add_is_called_exactly_once(self, page: MagicMock, isolated_user_profile: Path) -> None:
        """One root host. Two ``page.add`` calls would stack two trees on the page."""
        _write_config(
            isolated_user_profile, setup_completed=True, input_dir="/in", output_dir="/out", sis_type="myedbc"
        )

        shell.main(page)

        assert isinstance(_added_root(page), ft.Control)

    def test_the_window_close_handler_is_bound(self, page: MagicMock, isolated_user_profile: Path) -> None:
        """PLAT-0: the OS close event must reach ``_close_window`` (zero orphans)."""
        _write_config(
            isolated_user_profile, setup_completed=True, input_dir="/in", output_dir="/out", sis_type="myedbc"
        )

        shell.main(page)

        assert not isinstance(page.window.on_event, MagicMock), "window.on_event was never bound to a real handler"
        assert page.window.prevent_close is False

    def test_the_disconnect_handler_exits_the_host_process(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PLAT-0's other half: a disconnected view must not orphan the python host."""
        _write_config(
            isolated_user_profile, setup_completed=True, input_dir="/in", output_dir="/out", sis_type="myedbc"
        )
        exits: list[int] = []
        monkeypatch.setattr(shell.os, "_exit", lambda code: exits.append(code))

        shell.main(page)
        assert not isinstance(page.on_disconnect, MagicMock), "on_disconnect was never bound"
        page.on_disconnect(MagicMock())

        assert exits == [0]

    def test_a_configured_install_mounts_the_rail_and_a_screen(
        self, page: MagicMock, isolated_user_profile: Path
    ) -> None:
        """The app body itself: the fixed-order rail plus a rendered surface."""
        _write_config(
            isolated_user_profile, setup_completed=True, input_dir="/in", output_dir="/out", sis_type="myedbc"
        )

        shell.main(page)

        root = _added_root(page)
        rails = [c for c in _iter_controls(root) if isinstance(c, ft.NavigationRail)]
        assert len(rails) == 1, "expected exactly one NavigationRail in the app body"
        assert len(rails[0].destinations) == 6

    def test_the_first_paint_actually_renders_the_initial_destination(
        self, page: MagicMock, isolated_user_profile: Path
    ) -> None:
        """The content pane is POPULATED on the first paint, not merely present.

        Closes a characterisation hole the refactor made easy to fall into: deleting the
        `render_by_id(initial_id)` call at the tail of `build_app_body` left the whole
        shell-touching suite green while shipping an empty content pane — a rail beside a
        blank rectangle. Every other test walks the tree and finds the RAIL, which exists
        either way.
        """
        _write_config(
            isolated_user_profile, setup_completed=True, input_dir="/in", output_dir="/out", sis_type="myedbc"
        )

        shell.main(page)

        # A completed install launches on Home, whose page header titles the surface.
        assert "Home" in _texts(_added_root(page)), "the content host is empty on the first paint"


# --------------------------------------------------------------------------- #
# 2. The launch gate                                                           #
# --------------------------------------------------------------------------- #
FRESH = {}  # no config.json at all — the archetypal first launch


class TestTheLaunchGate:
    def test_a_fresh_install_lands_on_the_launch_page_not_the_app_body(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[AppConfig] = []
        monkeypatch.setattr(shell, "build_app_body", lambda p, cfg: built.append(cfg) or ft.Text("body"))

        shell.main(page)

        assert built == [], "the app body was built while the launch page should be showing"
        assert shell.identity.HERO_HEADLINE in _texts(_added_root(page))
        assert _shows_launch_page(page), "the positive twin of every '_shows_launch_page is False' below"

    def test_the_proxy_label_is_the_ONE_that_actually_discriminates(
        self, page: MagicMock, isolated_user_profile: Path
    ) -> None:
        """Why ``_shows_launch_page`` reads the ESCAPE and not "Continue".

        The setup wizard's footer button is also labelled "Continue", so a proxy built on
        that label contributes nothing and ``not has_rail`` silently carries the whole
        predicate — the weak-proxy shape that made the headline check ambiguous in the
        first place. This asserts the discriminating property directly, so it stays true
        (or goes red) as either surface's copy changes.
        """
        from src.ui_flet.screens.setup import build_setup

        wizard_labels = {getattr(c, "content", None) for c in _iter_controls(build_setup(MagicMock()))}
        shell.main(page)
        launch_labels = {getattr(c, "content", None) for c in _iter_controls(_added_root(page))}

        assert shell.identity.SKIP_LABEL in launch_labels
        assert shell.identity.SKIP_LABEL not in wizard_labels, "the escape label is no longer unique to the gate"
        assert shell.identity.CONTINUE_LABEL in launch_labels
        assert shell.identity.CONTINUE_LABEL in wizard_labels, "'Continue' would discriminate after all — recheck"

    def test_the_close_handlers_are_bound_BEFORE_the_launch_page_is_built(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hoist, asserted as an ORDER not as an end state.

        Closing the window at the launch page must orphan nothing — and there is no rail
        and no Exit button on that page, so the title-bar close is the ONLY exit from it.
        If the handlers were still bound after the gate (their pre-S4a position), that
        close would be unhandled for exactly as long as the page is up.
        """
        seen: dict[str, bool] = {}

        def _spy(*args: object, **kwargs: object) -> ft.Control:
            seen["window"] = not isinstance(page.window.on_event, MagicMock)
            seen["disconnect"] = not isinstance(page.on_disconnect, MagicMock)
            return ft.Text("launch page")

        monkeypatch.setattr(shell.identity, "build_identity", _spy)

        shell.main(page)

        assert seen == {"window": True, "disconnect": True}

    def test_a_configured_install_never_sees_the_launch_page(
        self, page: MagicMock, isolated_user_profile: Path
    ) -> None:
        """An upgrading install boots straight to the app (Journey 4's gate half).

        The full render half — dashboard, no wizard, asked exactly once — lives in
        ``TestJourney4UpgradeInPlace`` below, driven from the literal v3.8.x settings file.
        """
        _write_config(
            isolated_user_profile,
            input_dir="C:/gde/in",
            output_dir="C:/gde/out",
            sis_type="sd74myedbc",
            schedule_registered=True,
            schedule_time="03:00",
            setup_completed=True,
            sftp_enabled=True,
            sftp_host="sftp.ca.spacesedu.com",
        )

        shell.main(page)

        assert not _shows_launch_page(page)
        assert _has_rail(page)

    def test_an_unreadable_profile_never_sees_the_launch_page(
        self, page: MagicMock, isolated_user_profile: Path
    ) -> None:
        """G2 — we could not persist the answer, so we must not ask for it."""
        isolated_user_profile.mkdir(parents=True, exist_ok=True)
        (isolated_user_profile / "config.json").write_bytes(b'{"input_dir": "C:/in", ')

        shell.main(page)

        assert not _shows_launch_page(page)
        # The positive half: something real mounted. Without it, "no launch page" and "no
        # card" are both satisfied by a blank page — which is exactly the failure a gate
        # that crashed silently would produce.
        assert _has_rail(page), "the app body never mounted; the absences below are vacuous"
        # ...and G2 holds on Home too: an unreadable profile gets no card either (S4b).
        assert home.IDENTITY_CARD_HEADLINE not in _texts(_added_root(page))

    def test_the_completed_install_boot_reads_the_config_once_for_the_body(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The non-gated path hands the app body the config the shell already loaded."""
        _write_config(
            isolated_user_profile, setup_completed=True, input_dir="/in", output_dir="/out", sis_type="myedbc"
        )
        seen: list[AppConfig] = []
        monkeypatch.setattr(shell, "build_app_body", lambda p, cfg: seen.append(cfg) or ft.Text("body"))

        shell.main(page)

        assert [c.sis_type for c in seen] == ["myedbc"]


# --------------------------------------------------------------------------- #
# 2b. Journey 4, the RENDER half — an upgrading install, end to end (S4b)      #
# --------------------------------------------------------------------------- #
class TestJourney4UpgradeInPlace:
    """A shipped v3.8.x install boots into the app and is asked ONCE, on Home.

    Driven from the LITERAL settings file such an install has on disk (single-sourced with
    the config-layer half in ``tests/test_app_config_identity.py``), through the REAL boot
    — gate, shell, app body, Home — because the promise being pinned is a composition:
    no launch page in front of a working sync, no wizard, no re-ask once it is answered or
    dismissed, and one small card under the verdict.
    """

    @staticmethod
    def _plant(profile: Path, **identity: object) -> None:
        profile.mkdir(parents=True, exist_ok=True)
        values = json.loads(V38X_CONFIG_JSON)
        values.update(identity)
        (profile / "config.json").write_text(json.dumps(values), encoding="utf-8")

    @staticmethod
    def _boot(page: MagicMock) -> list[str]:
        shell.main(page)
        return _texts(_added_root(page))

    def test_it_lands_on_the_dashboard_and_is_asked_once_on_home(
        self, page: MagicMock, isolated_user_profile: Path
    ) -> None:
        self._plant(isolated_user_profile)

        texts = self._boot(page)

        assert not _shows_launch_page(page), "a working install was stopped at a launch page"
        assert "Welcome to DistrictSync" not in texts, "a configured install was shown the onboarding hero"
        assert _has_rail(page)
        assert "Home" in texts, "the dashboard did not paint"
        assert home.IDENTITY_CARD_HEADLINE in texts, "the upgrading install was never asked"

    def test_a_dismissed_profile_is_never_asked_again(self, page: MagicMock, isolated_user_profile: Path) -> None:
        self._plant(isolated_user_profile, identity_prompt_dismissed=True)

        texts = self._boot(page)

        assert _has_rail(page) and "Home" in texts, "the dashboard did not paint; the absence below is vacuous"
        assert home.IDENTITY_CARD_HEADLINE not in texts

    def test_an_install_that_already_answered_is_never_asked_again(
        self, page: MagicMock, isolated_user_profile: Path
    ) -> None:
        self._plant(isolated_user_profile, identity_email="admin@sd48.bc.ca")

        texts = self._boot(page)

        assert _has_rail(page) and "Home" in texts, "the dashboard did not paint; the absence below is vacuous"
        assert home.IDENTITY_CARD_HEADLINE not in texts


# --------------------------------------------------------------------------- #
# 3. Persist-then-enter, and entering exactly once                             #
# --------------------------------------------------------------------------- #
class TestEnteringTheApp:
    def _drive_to_entry(self, page: MagicMock, monkeypatch: pytest.MonkeyPatch, address: str) -> list[AppConfig]:
        seen: list[AppConfig] = []
        monkeypatch.setattr(shell, "build_app_body", lambda p, cfg: seen.append(cfg) or ft.Text("body"))

        shell.main(page)

        root = _added_root(page)
        field = next(c for c in _iter_controls(root) if isinstance(c, ft.TextField))
        field.value = address
        _button_labelled(root, shell.identity.CONTINUE_LABEL).on_click(None)
        _button_labelled(page.add.call_args[0][0], shell.identity.GET_STARTED_LABEL).on_click(None)
        return seen

    def test_the_app_body_sees_the_persisted_identity_on_the_FIRST_paint(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Persist, THEN enter with a FRESH load — so the first session is scoped correctly.

        Entering first and persisting afterwards would hand the app body the PRE-identity
        instance, leaving S5's lists unfiltered for the whole first session.
        """
        seen = self._drive_to_entry(page, monkeypatch, "admin@sd48.bc.ca")

        assert [c.identity_email for c in seen] == ["admin@sd48.bc.ca"]
        stored = json.loads((isolated_user_profile / "config.json").read_text(encoding="utf-8"))
        assert stored["identity_email"] == "admin@sd48.bc.ca"

    def test_a_failed_persist_still_enters(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Advisory metadata may never trap an admin in front of their own sync."""

        def _boom(self: AppConfig) -> None:
            raise OSError("the profile is read-only")

        monkeypatch.setattr(AppConfig, "save", _boom)

        seen = self._drive_to_entry(page, monkeypatch, "admin@sd48.bc.ca")

        assert len(seen) == 1, "a persist failure must still open the app"
        assert seen[0].identity_email == "", "nothing was written, so the fresh load carries nothing"

    def test_entering_twice_builds_the_app_body_once(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A double-click on Get started must not stack a second app body on the host."""
        seen: list[AppConfig] = []
        monkeypatch.setattr(shell, "build_app_body", lambda p, cfg: seen.append(cfg) or ft.Text("body"))

        shell.main(page)
        root = _added_root(page)
        next(c for c in _iter_controls(root) if isinstance(c, ft.TextField)).value = "admin@sd48.bc.ca"
        _button_labelled(root, shell.identity.CONTINUE_LABEL).on_click(None)
        get_started = _button_labelled(page.add.call_args[0][0], shell.identity.GET_STARTED_LABEL)
        get_started.on_click(None)
        get_started.on_click(None)

        assert len(seen) == 1

    def test_the_escape_enters_and_stores_nothing(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """G7 (flag 1): the person at the console who is not the admin is never trapped."""
        seen: list[AppConfig] = []
        monkeypatch.setattr(shell, "build_app_body", lambda p, cfg: seen.append(cfg) or ft.Text("body"))

        shell.main(page)
        root = _added_root(page)
        next(c for c in _iter_controls(root) if isinstance(c, ft.TextField)).value = "admin@sd48.bc.ca"
        _button_labelled(root, shell.identity.SKIP_LABEL).on_click(None)

        assert len(seen) == 1, "the escape must enter the app"
        assert seen[0].identity_email == ""
        assert not (isolated_user_profile / "config.json").exists(), "the escape wrote a settings file"


# --------------------------------------------------------------------------- #
# 4. The identity-layer FLOOR — it can never fail closed                       #
# --------------------------------------------------------------------------- #
class TestTheIdentityFloor:
    def test_a_raise_in_the_page_build_still_mounts_the_app(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*args: object, **kwargs: object) -> ft.Control:
            raise RuntimeError("the launch page exploded")

        monkeypatch.setattr(shell.identity, "build_identity", _boom)
        seen: list[AppConfig] = []
        monkeypatch.setattr(shell, "build_app_body", lambda p, cfg: seen.append(cfg) or ft.Text("body"))

        shell.main(page)

        assert len(seen) == 1, "a crash in the launch page must land in the app, unfiltered"

    def test_a_raise_in_the_gate_predicate_still_mounts_the_app(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(_cfg: AppConfig) -> bool:
            raise RuntimeError("the predicate exploded")

        monkeypatch.setattr(shell, "needs_identity", _boom)
        seen: list[AppConfig] = []
        monkeypatch.setattr(shell, "build_app_body", lambda p, cfg: seen.append(cfg) or ft.Text("body"))

        shell.main(page)

        assert len(seen) == 1

    def test_a_raise_during_resolution_still_mounts_the_app(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The handler-level half of the floor: a raise mid-answer opens the app anyway."""
        monkeypatch.setattr(shell.identity, "matched_state", MagicMock(side_effect=RuntimeError("resolution exploded")))
        seen: list[AppConfig] = []
        monkeypatch.setattr(shell, "build_app_body", lambda p, cfg: seen.append(cfg) or ft.Text("body"))

        shell.main(page)
        root = _added_root(page)
        next(c for c in _iter_controls(root) if isinstance(c, ft.TextField)).value = "admin@sd48.bc.ca"
        _button_labelled(root, shell.identity.CONTINUE_LABEL).on_click(None)

        assert len(seen) == 1

    def test_a_raise_in_the_APP_BODY_is_NOT_floored(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The boundary of the floor, pinned deliberately — it covers IDENTITY, not the app.

        Aimed at ungated space: it would be easy (and wrong) to widen the ``except`` around
        the gate until it swallowed a broken app body too. Then a crash on the way in would
        paint an EMPTY window with no rail, no error and no log the admin can be pointed
        at, instead of the launcher's early-failure dialog. Identity failures degrade;
        app-body failures must stay loud.
        """

        def _boom(_page: ft.Page, _cfg: AppConfig) -> ft.Control:
            raise RuntimeError("the app body exploded")

        monkeypatch.setattr(shell, "build_app_body", _boom)
        shell.main(page)
        root = _added_root(page)
        next(c for c in _iter_controls(root) if isinstance(c, ft.TextField)).value = "admin@sd48.bc.ca"
        _button_labelled(root, shell.identity.CONTINUE_LABEL).on_click(None)

        # Entering is the one act on this page that is NOT guarded. Swallowing it would
        # leave a launch page whose Get started button quietly does nothing.
        get_started = _button_labelled(page.add.call_args[0][0], shell.identity.GET_STARTED_LABEL)
        with pytest.raises(RuntimeError, match="the app body exploded"):
            get_started.on_click(None)

        # And the SECOND press must raise too. A latch armed before the body was built
        # would swallow this one — the page still mounted, the button now inert forever,
        # which is a trap dressed as a working screen.
        with pytest.raises(RuntimeError, match="the app body exploded"):
            get_started.on_click(None)

    def test_a_raise_in_the_app_body_is_not_floored_on_the_ESCAPE_path_either(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same boundary on the other exit — the escape enters without persisting."""

        def _boom(_page: ft.Page, _cfg: AppConfig) -> ft.Control:
            raise RuntimeError("the app body exploded")

        monkeypatch.setattr(shell, "build_app_body", _boom)
        shell.main(page)
        skip = _button_labelled(_added_root(page), shell.identity.SKIP_LABEL)

        with pytest.raises(RuntimeError, match="the app body exploded"):
            skip.on_click(None)
        # The second press repeats — the latch was never armed by the failed attempt.
        with pytest.raises(RuntimeError, match="the app body exploded"):
            skip.on_click(None)

    def test_a_TRANSIENT_app_body_failure_leaves_the_page_usable(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The point of latching on SUCCESS: one bad press must not disable the page.

        A locked profile or a probe that raised once is transient by nature. If the latch
        armed before the body was built, the admin would be left in front of a launch page
        whose every affordance had become a silent no-op.
        """
        attempts: list[int] = []

        def _flaky(_page: ft.Page, cfg: AppConfig) -> ft.Control:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("a transient failure")
            return ft.Text("body")

        monkeypatch.setattr(shell, "build_app_body", _flaky)
        shell.main(page)
        skip = _button_labelled(_added_root(page), shell.identity.SKIP_LABEL)

        with pytest.raises(RuntimeError, match="a transient failure"):
            skip.on_click(None)
        skip.on_click(None)  # the retry succeeds

        assert len(attempts) == 2

    def test_the_floor_probes_are_not_vacuous(
        self, page: MagicMock, isolated_user_profile: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POSITIVE twin: with nothing injected, the SAME drive does NOT enter the app.

        Without this, every floor test above would also pass if the gate had silently
        stopped showing the launch page at all.
        """
        seen: list[AppConfig] = []
        monkeypatch.setattr(shell, "build_app_body", lambda p, cfg: seen.append(cfg) or ft.Text("body"))

        shell.main(page)
        root = _added_root(page)
        next(c for c in _iter_controls(root) if isinstance(c, ft.TextField)).value = "admin@sd48.bc.ca"
        _button_labelled(root, shell.identity.CONTINUE_LABEL).on_click(None)

        assert seen == [], "resolution alone must not enter the app — the admin confirms"
