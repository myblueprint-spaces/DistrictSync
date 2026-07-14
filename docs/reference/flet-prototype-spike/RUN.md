# DistrictSync — Flet spike — RUN

Throwaway bake-off prototype. Two screens (Home + Convert), mock data only.

Venv python (use this for everything — short path avoids the Windows MAX_PATH limit):

```
C:/Users/shan.peiris/dssp/.venv/Scripts/python.exe
```

App dir:

```
C:/Users/shan.peiris/AppData/Local/Temp/claude/C--Users-shan-peiris-Documents-Integrations-DistrictSync/d20e218a-1fcc-49d0-9916-b86fbcf74781/scratchpad/spike/flet_app
```

## One-time dependencies (already installed into the venv during this spike)

```
C:/Users/shan.peiris/dssp/.venv/Scripts/python.exe -m pip install flet-desktop==0.85.3 flet-web==0.85.3
```

- `flet` 0.85.3 was pre-installed.
- `flet-desktop` — the native desktop client launcher (required for native window mode).
- `flet-web` — the web/uvicorn server + bundled Flutter-web assets (required for `SPIKE_WEB=1`).

> Note: on the **first** native launch, `flet-desktop` downloads the Flutter
> Windows client (`flet-windows.zip`, ~80–100 MB) from GitHub releases into
> `~/.flet/client/flet-desktop-…-0.85.3`. Native mode therefore needs internet
> on first run (or a pre-seeded `~/.flet` cache). Web mode does not — `flet-web`
> bundles its assets in the wheel.

## (a) Native desktop window mode — DEFAULT

```powershell
& "C:/Users/shan.peiris/dssp/.venv/Scripts/python.exe" `
  "C:/Users/shan.peiris/AppData/Local/Temp/claude/C--Users-shan-peiris-Documents-Integrations-DistrictSync/d20e218a-1fcc-49d0-9916-b86fbcf74781/scratchpad/spike/flet_app/app.py"
```

Bash equivalent:

```bash
cd ".../scratchpad/spike/flet_app"
"C:/Users/shan.peiris/dssp/.venv/Scripts/python.exe" app.py
```

Opens a native 1180×860 window titled "DistrictSync". Closing the window (or the
in-app **Exit** button) tears the app down and exits the process cleanly.

## (b) Web mode — fixed port 8701 (headless screenshots)

PowerShell:

```powershell
$env:SPIKE_WEB = "1"
& "C:/Users/shan.peiris/dssp/.venv/Scripts/python.exe" ".../scratchpad/spike/flet_app/app.py"
# -> serves http://localhost:8701  (does not auto-open a browser)
```

Bash:

```bash
cd ".../scratchpad/spike/flet_app"
SPIKE_WEB=1 "C:/Users/shan.peiris/dssp/.venv/Scripts/python.exe" app.py
# -> http://localhost:8701
```

## Startup contract

On startup the app prints `SPIKE_PID=<pid>` to stdout so a harness can capture
the process tree and verify clean teardown.

## Stopping web mode

`Ctrl-C` in the foreground, or kill the PID printed at startup:

```bash
taskkill //PID <pid> //F //T
```

Web mode runs the server in-process (uvicorn in the same Python process), so a
single kill frees port 8701 with no orphans.
