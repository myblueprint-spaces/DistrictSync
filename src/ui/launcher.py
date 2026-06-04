"""PyInstaller entry point for the DistrictSync UI executable.

When frozen with PyInstaller, this script locates the Streamlit app
and launches it programmatically, opening the browser automatically.

The production build uses src/main.py as the PyInstaller entry so a
single binary serves both CLI and UI (argv-less launch → UI, with args
→ CLI). See .github/workflows/release.yml for the authoritative build
command. paramiko + keyring are top-level imports in
src/sftp/uploader.py so PyInstaller picks them up automatically; only
keyring.backends.<platform> still needs --hidden-import because
keyring discovers credential-store backends dynamically at runtime.
"""

import os
import sys
from pathlib import Path


def get_app_path() -> Path:
    """Return the path to Home.py, whether frozen or running from source."""
    if getattr(sys, "frozen", False):
        # PyInstaller extracts files to sys._MEIPASS
        return Path(sys._MEIPASS) / "src" / "ui" / "Home.py"  # type: ignore[attr-defined]
    return Path(__file__).parent / "Home.py"


def main() -> None:
    app_path = get_app_path()

    if not app_path.exists():
        print(f"Error: could not find Home.py at {app_path}", file=sys.stderr)
        sys.exit(1)

    # Ensure config/ is resolvable relative to the bundle root
    if getattr(sys, "frozen", False):
        bundle_dir = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        os.chdir(bundle_dir)

    # Launch Streamlit
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        "--global.developmentMode=false",
        "--server.headless=false",
        "--browser.gatherUsageStats=false",
        "--client.toolbarMode=minimal",
        "--server.port=8501",
        # Pin the light base theme. Without this, Streamlit follows the
        # browser/OS preference, and a dark-mode OS renders canvas widgets
        # (st.dataframe) and other theme-derived elements dark — unreadable
        # against the light brand styling. .streamlit/config.toml isn't
        # bundled in the frozen exe, so the flag is the reliable path.
        "--theme.base=light",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
