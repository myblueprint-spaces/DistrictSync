"""The first-run machine setup window — a PRE-SHELL surface (plan 0055 M2b, DSYNC-106).

A machine-wide (MSI) install that has not been set up refuses a per-user profile, so ``launcher.main`` shows this window
INSTEAD of the shell, before the legacy migration, the pin and the log sink. It asks the one question the MSI cannot
(owner decision O2: how DistrictSync handles new versions, default ``notify``, O1) and runs the SAME ladder as
``--machine-setup`` (:func:`src.scheduler.machine_setup.run`) behind one UAC prompt. Once the computer uses the shared
profile it re-launches the app, which then boots on that profile through its normal path (a second ``ft.run`` in one
process is unproven, the reason ``grant_window`` re-launches too).

Modelled on :mod:`src.ui_flet.grant_window`: thin view glue, two REQUIRED seams (``request`` and ``reexec``) so no test
can reach a real UAC prompt, and the setup runs on the UI thread because the UAC prompt is system-modal anyway.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable

import flet as ft

from src.scheduler.machine_setup import NOT_HANDED_OVER, WINDOW_NOT_SET_UP, MachineSetupResult
from src.ui_flet import components, tokens
from src.update import policy
from src.update.setting import MODE_LABELS

WINDOW_TITLE = "Set up DistrictSync on this computer"
HEADLINE = "One step before DistrictSync can run"
DETAIL = (
    "DistrictSync is installed for everyone on this computer. Setting it up keeps its settings, run history and "
    "delivery password in one shared folder, C:\\ProgramData\\DistrictSync, which the account that runs the nightly "
    "sync can use too."
)
ADMIN_NOTE = "Windows asks an administrator to allow this once."
UPDATES_LABEL = "New versions of DistrictSync"
UPDATE_CHOICES: list[tuple[str, str]] = [(mode, MODE_LABELS[mode]) for mode in (policy.NOTIFY, policy.ON, policy.OFF)]
UPDATES_NOTE = "Your IT team can also set this with Group Policy, which wins over this choice."
PRIMARY_LABEL = "Set up this computer"
RETRY_LABEL = "Try again"
CLOSE_LABEL = "Close"
FAILED_HEADLINE = "This computer is not set up yet"
WAITING_LINE = "Setting up this computer. Answer the Windows permission prompt if it appears."

_WINDOW_WIDTH = 600
_WINDOW_HEIGHT = 620


def build_machine_setup_body(
    page: ft.Page,
    *,
    request: Callable[[str], MachineSetupResult],
    reexec: Callable[[], None],
) -> ft.Control:
    """The window body: the ask, and whatever the attempt turns it into. Never raises (``ErrorCard`` floor)."""
    host = ft.Container(expand=True)
    chosen = {"mode": policy.DEFAULT_UPDATE_MODE}

    async def _close(_e: object | None = None) -> None:
        # ``page.window.destroy()`` is a coroutine on flet 0.85.3; ``os._exit(1)`` is the floor that keeps the window
        # closeable if that fails (an ordinary close returns through the launcher).
        try:
            await page.window.destroy()
        except Exception:  # noqa: BLE001 - the window must always be closeable
            os._exit(1)

    def _choose(value: str) -> None:
        chosen["mode"] = value

    async def _attempt(_e: object | None = None) -> None:
        # The setup waits on a UAC prompt and an elevated child: it runs OFF the event loop, behind a waiting line, so
        # the window stays alive and a second click cannot queue a second setup.
        host.content = _frame(components.inflight_row(WAITING_LINE), _close)
        page.update()
        # Flet swallows an exception in a handler: without this catch a surprise is a button that does nothing, on the
        # one screen standing between this admin and the app.
        try:
            result = await asyncio.to_thread(request, chosen["mode"])
        except Exception as exc:  # noqa: BLE001 - a dead button is the one outcome this screen cannot have
            result = MachineSetupResult(1, (f"The machine setup could not run ({type(exc).__name__}).",), False)
        if result.handed_over:
            reexec()  # the shared profile is in use: the app starts on it
            return
        lines = [line for line in result.lines if line != NOT_HANDED_OVER]
        host.content = _frame(
            components.ErrorCard(
                FAILED_HEADLINE,
                " ".join([*lines, WINDOW_NOT_SET_UP]),
                action=components.primary_button(RETRY_LABEL, _attempt),
                log_folder=False,  # the default button would pin, and create, a per-user profile
            ),
            _close,
        )
        page.update()

    try:
        host.content = _frame(_ask_card(_attempt, _choose), _close)
        return host
    except Exception as exc:  # noqa: BLE001 - the never-crash floor (design system)
        return components.ErrorCard(
            "DistrictSync couldn't show this window",
            f"Nothing on this computer was changed. ({type(exc).__name__})",
            log_folder=False,
        )


def _ask_card(on_attempt: Callable[[object | None], None], on_choose: Callable[[str], None]) -> ft.Control:
    """The opening state: what this does, the update question, and the ONE filled primary."""
    return components.card(
        ft.Column(
            spacing=tokens.space_md,
            controls=[
                ft.Text(HEADLINE, size=tokens.type_section, weight=ft.FontWeight.W_700, color=tokens.color_text),
                ft.Text(DETAIL, size=tokens.type_body, color=tokens.color_text),
                ft.Text(UPDATES_LABEL, size=tokens.type_body, weight=ft.FontWeight.W_600, color=tokens.color_text),
                components.choice_group(UPDATE_CHOICES, value=policy.DEFAULT_UPDATE_MODE, on_choose=on_choose),
                ft.Text(UPDATES_NOTE, size=tokens.type_caption, color=tokens.color_muted),
                ft.Text(ADMIN_NOTE, size=tokens.type_body, color=tokens.color_muted),
                components.primary_button(PRIMARY_LABEL, on_attempt),
            ],
        )
    )


def _frame(body: ft.Control, on_close: Callable[[object | None], None]) -> ft.Control:
    """One card on the content wash, with the text-tier way out beneath it."""
    return ft.Container(
        bgcolor=tokens.color_content_wash,
        padding=ft.Padding(left=tokens.space_xl, top=tokens.space_xl, right=tokens.space_xl, bottom=tokens.space_xl),
        expand=True,
        content=ft.Column(
            spacing=tokens.space_lg,
            scroll=ft.ScrollMode.AUTO,
            controls=[components.page_header(WINDOW_TITLE), body, components.text_button(CLOSE_LABEL, on_close)],
        ),
    )


def _reexec() -> None:  # pragma: no cover - ends the process
    # DSYNC-117: the shared relaunch (an argv LIST; ``os.execv`` split a ``Program Files`` path).
    from src.ui_flet.grant_window import relaunch

    relaunch()


def show_machine_setup_window() -> bool:  # pragma: no cover - ft.run glue
    """Render the window. ``False`` means it could not be shown; the launcher then shows its plain dialog."""
    try:
        from src.scheduler.machine_setup import run
        from src.utils.logger import get_logger
        from src.utils.paths import handshake_dir

        # The window's own log, so "see the log" is true. It lives in the per-user handshake folder, the one declared
        # exception (the UAC handshake files and MOVED.txt live there too); no profile is pinned.
        log_dir = handshake_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        get_logger(__name__, log_file=log_dir / "etl_tool.log")

        def _page(page: ft.Page) -> None:
            page.title = WINDOW_TITLE
            page.window.width = _WINDOW_WIDTH
            page.window.height = _WINDOW_HEIGHT
            page.bgcolor = tokens.color_content_wash
            page.add(build_machine_setup_body(page, request=run, reexec=_reexec))

        ft.run(_page)
        return True
    except Exception:  # noqa: BLE001 - the launcher's dialog is the fallback
        print("DistrictSync could not show the machine setup window.", file=sys.stderr)
        return False
