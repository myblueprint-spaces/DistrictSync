"""Unit tests for ``src.ui.folder_picker.pick_directory``.

tkinter is fully mocked so no real GUI window ever opens and the tests run
headless in CI. The picker imports tkinter lazily *inside* the function, so we
patch ``tkinter.Tk`` / ``tkinter.filedialog.askdirectory`` at their canonical
locations (the lazy import resolves to those module attributes at call time).
"""

from unittest.mock import MagicMock, patch

from src.ui.folder_picker import pick_directory


def test_returns_selected_path():
    """A chosen directory is returned verbatim."""
    with (
        patch("tkinter.Tk", return_value=MagicMock()),
        patch("tkinter.filedialog.askdirectory", return_value="/srv/districtsync/input"),
    ):
        assert pick_directory() == "/srv/districtsync/input"


def test_cancel_returns_none():
    """askdirectory() returns '' on cancel; that normalizes to None."""
    with (
        patch("tkinter.Tk", return_value=MagicMock()),
        patch("tkinter.filedialog.askdirectory", return_value=""),
    ):
        assert pick_directory() is None


def test_no_gui_returns_none():
    """A headless/display-less box raises when building Tk → graceful None."""
    with patch("tkinter.Tk", side_effect=RuntimeError("no display")):
        # Must not propagate the exception.
        assert pick_directory() is None


def test_initialdir_forwarded():
    """The supplied initialdir is passed through to askdirectory()."""
    with (
        patch("tkinter.Tk", return_value=MagicMock()),
        patch("tkinter.filedialog.askdirectory", return_value="/picked") as mock_ask,
    ):
        pick_directory(initialdir="/srv/start/here")

    mock_ask.assert_called_once()
    assert mock_ask.call_args.kwargs["initialdir"] == "/srv/start/here"


def test_root_destroyed_on_success():
    """The hidden Tk root is always torn down, even on the happy path."""
    fake_root = MagicMock()
    with (
        patch("tkinter.Tk", return_value=fake_root),
        patch("tkinter.filedialog.askdirectory", return_value="/done"),
    ):
        pick_directory()

    fake_root.withdraw.assert_called_once()
    fake_root.destroy.assert_called_once()


def test_root_destroyed_when_dialog_raises():
    """If the dialog itself raises, the root is still destroyed and None returned."""
    fake_root = MagicMock()
    with (
        patch("tkinter.Tk", return_value=fake_root),
        patch("tkinter.filedialog.askdirectory", side_effect=RuntimeError("Tcl error")),
    ):
        assert pick_directory() is None

    fake_root.destroy.assert_called_once()
