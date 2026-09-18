"""The auto-grant window — a PRE-SHELL surface, not a screen behind the rail (0049 S-1b-ii.2).

D5 put this at ``screens/grant_access.py``. **That mount point does not exist at the moment
of failure:** ``launcher.main`` calls ``pin_data_dir()`` inside its try, *before*
``ft.run(shell.main, …)``, so an ``INACCESSIBLE`` refusal lands in the ``except`` — the shell
is never entered, and it could not survive being entered anyway (``AppConfig.load()``
re-resolves ``user_data_dir()`` and re-raises). So this renders from the launcher's refusal
path, where the machine-scope causes already live, as a single-purpose window of its own.

View glue, deliberately thin: every string and every branch is in the pure
:mod:`src.ui_flet.grant_access`, and the elevated round trip is
:func:`src.scheduler.provision_session.request_access`. What is left here is assembly, one
state swap, and the two seams — ``request`` and ``reexec`` — which are REQUIRED arguments
with no defaults, so a test can never accidentally reach the real UAC prompt and the one
caller has to say what it means.

**The grant runs synchronously on the UI thread.** The UAC prompt is system-modal and sits
above this window regardless, so a worker thread would buy nothing here and would add
cross-thread control updates to a boot path — the last place to add a class of bug.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

import flet as ft

from src.scheduler.provision_session import GrantOutcome, request_access
from src.ui_flet import components, grant_access, tokens

# The window is sized for one card of copy — the same order of magnitude as the launcher's
# own boot-error window, and never the shell's geometry (there is no AppConfig to read it
# from in this state).
_WINDOW_WIDTH = 560
_WINDOW_HEIGHT = 480


def build_grant_body(
    page: ft.Page,
    *,
    request: Callable[[], GrantOutcome],
    reexec: Callable[[], None],
) -> ft.Control:
    """Build the window body: the ask, and whatever the attempt turns it into.

    ``request`` performs the elevated round trip and returns a typed
    :class:`~src.scheduler.provision_session.GrantOutcome`; ``reexec`` re-launches the
    process and does not return. Both are injected because this function is the only thing
    in the module a test can drive, and because a defaulted ``request`` would put a live UAC
    prompt one accident away from every render smoke.

    Never raises: an ``ErrorCard`` floor stands in for a render failure, because this window
    IS the app for the admin who sees it — falling back to a traceback would leave them with
    nothing at all.
    """
    host = ft.Container(expand=True)

    async def _close(_e: object | None = None) -> None:
        # ``page.window.destroy()`` is a COROUTINE on the pinned flet (0.85.3): a bare
        # synchronous call is an un-awaited no-op — the window simply does not close, with
        # no exception and no log (the 0029 Exit-button class, static-banned by
        # ``test_no_unawaited_async_window_calls``). Hence the async handler and the
        # ``await``, exactly as ``shell._close_window`` and the launcher's boot dialog do.
        # ``os._exit(1)`` is the floor so this window can always be closed, and 1 rather
        # than 0 because closing it without a grant means the app did not start.
        try:
            await page.window.destroy()
        except Exception:  # noqa: BLE001 - the window must always be closeable
            os._exit(1)

    def _attempt(_e: object | None = None) -> None:
        # ``request_access`` never raises by contract, and this catch is not a second
        # opinion about that: Flet SWALLOWS an exception raised inside an event handler, so
        # the cost of being wrong is a primary button that does nothing at all, forever, on
        # the one screen standing between this admin and the app. Any surprise lands on the
        # honest "could not ask" copy instead.
        try:
            outcome = request()
        except Exception:  # noqa: BLE001 - a dead button is the one outcome this screen cannot have
            outcome = GrantOutcome.UNAVAILABLE
        if outcome is GrantOutcome.GRANTED:
            reexec()
            return
        host.content = _failed(outcome)
        page.update()

    def _failed(outcome: GrantOutcome) -> ft.Control:
        copy = grant_access.failure_copy(outcome)
        if copy is None:  # pragma: no cover - GRANTED never reaches here (it re-execs)
            copy = grant_access.failure_copy(GrantOutcome.UNAVAILABLE)
            assert copy is not None  # nosec B101 - UNAVAILABLE is a table entry, not an inference
        return _frame(
            components.ErrorCard(
                copy.headline,
                copy.detail,
                action=components.primary_button(copy.primary_label, _attempt),
            ),
            _close,
        )

    try:
        host.content = _frame(_ask_card(_attempt), _close)
        return host
    except Exception as exc:  # noqa: BLE001 - the never-crash floor (design system)
        return components.ErrorCard(
            "DistrictSync couldn't show this window",
            f"Nothing on this computer was changed. ({type(exc).__name__})",
        )


def _ask_card(on_attempt: Callable[[object | None], None]) -> ft.Control:
    """The opening state: what happened, what it costs, and the ONE filled primary."""
    return components.card(
        ft.Column(
            spacing=tokens.space_md,
            controls=[
                ft.Text(
                    grant_access.ASK_HEADLINE,
                    size=tokens.type_section,
                    weight=ft.FontWeight.W_700,
                    color=tokens.color_text,
                ),
                ft.Text(grant_access.ASK_DETAIL, size=tokens.type_body, color=tokens.color_text),
                ft.Text(grant_access.ASK_ADMIN_NOTE, size=tokens.type_body, color=tokens.color_muted),
                ft.Text(grant_access.ASK_REASSURANCE, size=tokens.type_caption, color=tokens.color_muted),
                components.primary_button(grant_access.ASK_PRIMARY_LABEL, on_attempt),
            ],
        )
    )


def _frame(body: ft.Control, on_close: Callable[[object | None], None]) -> ft.Control:
    """One card of copy on the content wash, with the text-tier way out beneath it."""
    return ft.Container(
        bgcolor=tokens.color_content_wash,
        padding=ft.Padding(left=tokens.space_xl, top=tokens.space_xl, right=tokens.space_xl, bottom=tokens.space_xl),
        expand=True,
        content=ft.Column(
            spacing=tokens.space_lg,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                components.page_header(grant_access.WINDOW_TITLE),
                body,
                components.text_button(grant_access.CLOSE_LABEL, on_close),
            ],
        ),
    )


def _reexec() -> None:  # pragma: no cover - replaces the process image
    """Re-launch DistrictSync and replace this process. Never returns on success.

    The argv is computed by the pure :func:`src.ui_flet.grant_access.reexec_argv`, which is
    where the frozen-vs-source decision is tested; this is the one line that cannot be.
    """
    argv = grant_access.reexec_argv()
    os.execv(argv[0], argv)  # nosec B606 - re-launching OUR OWN interpreter/exe, argv from sys, no shell


def show_grant_window() -> bool:  # pragma: no cover - ft.run glue
    """Render the pre-shell grant window. ``False`` means it could not be shown at all.

    The caller (``launcher.main``) falls back to its plain-language error dialog on
    ``False``, so a Flet that cannot open a window still tells the admin something.
    """
    try:

        def _page(page: ft.Page) -> None:
            page.title = grant_access.WINDOW_TITLE
            page.window.width = _WINDOW_WIDTH
            page.window.height = _WINDOW_HEIGHT
            page.bgcolor = tokens.color_content_wash
            page.add(build_grant_body(page, request=request_access, reexec=_reexec))

        ft.run(_page)
        return True
    except Exception:  # noqa: BLE001 - the launcher's dialog is the fallback
        print("DistrictSync could not show the shared-settings window.", file=sys.stderr)
        return False
