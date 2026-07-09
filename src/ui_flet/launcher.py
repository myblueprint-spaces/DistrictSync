"""Entry point for the native Flet UI (reached from ``main.py``'s no-argv
branch — Flet is the default/only UI).

Replicates the prior launcher's frozen-cwd handling so ``config/`` resolves
for a later ``run_pipeline``, then runs the Flet shell. Because the shipped exe is
**windowed / no-console**, a boot failure can't print to a console — so the legacy
app-data migration, log-sink setup, import + ``ft.run`` are wrapped in an
early-failure path that (a) writes the FULL traceback to the ETL log sink and
(b) shows a PLAIN-LANGUAGE error (the traceback goes to the LOG ONLY, never the
dialog), then exits non-zero. The window can never die silently — including when
the profile itself is locked or permission-denied at migration / log-open time.

The pure helpers (``resolve_frozen_cwd``, ``resolve_log_path``,
``format_user_error``) are factored out and unit-tested; the ``ft.run`` glue and
the dialog rendering are thin and coverage-omitted.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from src.utils.logger import get_logger
from src.utils.paths import migrate_legacy_data_dir, user_log_file

_LOG_NAME = "etl_tool.log"


def boot_logging() -> None:
    """Configure the shared file-log sink for the UI session (deferred from import).

    A Flet session that never configured logging would run silently — its diagnostics
    (and, from Slice 4b, its run records) would go nowhere. Call this once at launch so
    the UI writes to the same ``etl_tool.log`` sink as the CLI and scheduled runs. The
    sink path resolves through ``paths.user_data_dir()`` at call time (single seam).
    """
    get_logger("src.ui_flet")


def resolve_frozen_cwd() -> Path | None:
    """The directory to ``chdir`` into when frozen (PyInstaller ``_MEIPASS``), else None.

    Pure/inspectable: returns the path WITHOUT performing the chdir, so it's
    testable. A frozen one-file exe extracts to a temp dir and the
    scheduled-task runtime has cwd ``%SystemRoot%\\System32``, so
    ``config/`` only resolves after chdir'ing to the bundle root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return None


def resolve_log_path() -> Path:
    """Canonical ETL log path — the same sink the ETL writes to.

    Reuses ``src/utils/paths.user_log_file()`` (single source of truth) so the
    early-failure traceback lands where Run History / support already look. If that
    helper itself is unavailable (paths.py broken), falls back to a bare filename in
    the current directory — deliberately NOT re-deriving the app-data location here,
    which would duplicate the single ``paths.py`` seam.
    """
    try:
        return user_log_file()
    except Exception:
        return Path(_LOG_NAME)


def format_user_error(exc: BaseException) -> str:
    """A short, plain-language message for the failure dialog (NO traceback).

    The traceback is for the log; the dialog is for a non-technical admin. Names
    where the details went so they (or support) can find them.
    """
    log_path = resolve_log_path()
    return (
        "DistrictSync couldn't open its window.\n\n"
        "Your scheduled nightly sync is not affected — it runs separately.\n\n"
        f"Technical details were saved to:\n{log_path}\n\n"
        "Please share that file with support if this keeps happening."
    )


def _write_traceback(exc: BaseException) -> None:
    """Append the full traceback to the ETL log sink (best-effort, never raises)."""
    try:
        log_path = resolve_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("\n=== DistrictSync Flet UI failed to launch ===\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:  # nosec B110 — logging the failure must never mask the original failure
        pass


def _show_error_dialog(message: str) -> None:  # pragma: no cover - view glue
    """Show a minimal plain-language error window, falling back to tkinter/stderr.

    Tried in order so the no-console exe always surfaces *something*: a minimal
    Flet error window; then a ``tkinter`` messagebox (covers the case where Flet
    itself failed to import); finally stderr.
    """
    try:
        import flet as ft

        def _err(page: ft.Page) -> None:
            page.title = "DistrictSync"
            page.window.width = 520
            page.window.height = 320

            # Flet 0.85.3 `Window.destroy()` is a coroutine — a synchronous call is an
            # un-awaited no-op (Close would do nothing). Await it via an async handler;
            # `os._exit(0)` is the fallback so the boot-error window can always close.
            async def _close(_e: ft.ControlEvent) -> None:
                try:
                    await page.window.destroy()
                except Exception:
                    os._exit(0)

            page.add(
                ft.Container(
                    padding=ft.Padding(left=28, top=28, right=28, bottom=28),
                    content=ft.Column(
                        spacing=14,
                        controls=[
                            ft.Text("DistrictSync", size=20, weight=ft.FontWeight.W_800),
                            ft.Text(message, size=14, selectable=True),
                            ft.FilledButton("Close", on_click=_close),
                        ],
                    ),
                )
            )

        ft.run(_err)
        return
    except Exception:  # nosec B110 — cascade to the next fallback if Flet can't show a window
        pass

    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("DistrictSync", message)
        root.destroy()
        return
    except Exception:  # nosec B110 — cascade to stderr if tkinter is unavailable too
        pass

    print(message, file=sys.stderr)


def main() -> None:  # pragma: no cover - view glue (ft.run + dialog)
    """Launch the Flet shell with an early-failure safety net."""
    frozen_cwd = resolve_frozen_cwd()
    if frozen_cwd is not None:
        os.chdir(frozen_cwd)

    try:
        # Relocate a legacy ~/.districtsync profile, THEN open the log sink in the
        # post-migration location — both inside the safety net so a locked or
        # permission-denied profile surfaces the plain-language error dialog instead
        # of dying silently before the window ever opens. ``_write_traceback`` stays
        # independent: it re-resolves the log path itself (falling back to the legacy
        # dir), so it still records the traceback even if ``boot_logging`` was the
        # thing that failed.
        migrate_legacy_data_dir()
        boot_logging()

        # Best-effort sweep of any orphaned elevation-handshake files (D5) — never fatal.
        from src.scheduler.elevation import sweep_orphans

        sweep_orphans()

        import flet as ft

        from src.ui_flet import shell

        assets_dir = str(Path(__file__).parent / "assets")
        ft.run(shell.main, assets_dir=assets_dir if Path(assets_dir).exists() else None)
    except Exception as exc:
        _write_traceback(exc)
        _show_error_dialog(format_user_error(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
