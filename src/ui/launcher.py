"""PyInstaller entry point for the GDE2Acsv UI executable.

When frozen with PyInstaller, this script locates the Streamlit app
and launches it programmatically, opening the browser automatically.

Build command (Windows):
    pyinstaller --onefile --name GDE2Acsv-UI
        --add-data "config;config"
        --add-data "src/ui;src/ui"
        --hidden-import=streamlit
        --hidden-import=streamlit.web.cli
        --hidden-import=paramiko
        --hidden-import=keyring
        --hidden-import=keyring.backends.Windows
        --hidden-import=pandas
        --hidden-import=yaml
        --hidden-import=pydantic
        --hidden-import=pydantic_core
        src/ui/launcher.py
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
        "--server.port=8501",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
