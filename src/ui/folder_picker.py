"""Native OS folder-selection dialog for the local Setup Wizard.

DistrictSync's packaged ``.exe`` runs on the district server, and the
administrator opens the Streamlit UI in a browser **on that same machine**.
That co-location is what makes a native dialog the right "browse" mechanism:
the OS folder picker pops on the operator's screen and returns a real
server-side path that the ETL pipeline can read/write directly.

The dialog is provided by ``tkinter.filedialog.askdirectory``. tkinter is
imported lazily inside :func:`pick_directory` so this module never fails to
import on a box where Tk is unavailable (e.g. a headless build container), and
so it can be unit-tested without a display.

Per the project's "fail loudly but don't crash the UI" principle, every failure
mode logs the specific reason and returns ``None`` rather than raising. A
``None`` return means "cancelled or no GUI available" — the caller is expected
to fall back to its manual text-entry box.

This module is intentionally pure/OS-only: it does NOT import Streamlit, so it
stays unit-testable and reusable.
"""

import logging

logger = logging.getLogger(__name__)


def pick_directory(initialdir: str | None = None) -> str | None:
    """Open a native folder-selection dialog and return the chosen path.

    Args:
        initialdir: Directory to open the dialog at. ``None`` (or an empty
            string) lets the OS pick a sensible default.

    Returns:
        The selected directory path as a string, or ``None`` if the user
        cancelled or no GUI / Tk runtime is available. Callers should treat
        ``None`` as "fall back to manual entry".
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # pragma: no cover - tkinter missing in bundle
        logger.warning("Folder picker unavailable: tkinter import failed (%s)", exc)
        return None

    try:
        root = tk.Tk()
        try:
            root.withdraw()  # Hide the empty root window; show only the dialog.
            root.wm_attributes("-topmost", 1)  # Bring the dialog to the front.
            selected = filedialog.askdirectory(
                initialdir=initialdir or None,
                title="Select folder",
            )
        finally:
            root.destroy()
    except Exception as exc:
        # A headless/display-less environment raises a Tcl error here.
        logger.warning("Folder picker unavailable: could not open dialog (%s)", exc)
        return None

    # askdirectory() returns "" when the user cancels.
    if not selected:
        return None
    return selected
