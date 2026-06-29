# DistrictSync — Flet spike — NOTES (honest assessment)

Framework: **Flet 0.85.3** (Python 3.13, venv at `C:/Users/shan.peiris/dssp/.venv`).
Flet renders a **Flutter** UI; the Python side is a thin declarative control tree
driven over a local socket to a Flutter client (native window) or Flutter-web
canvas (browser).

> ⚠️ Version note: 0.85 is a **major rewrite** of the old Flet 0.x API. Most
> tutorials/StackOverflow answers online are for the legacy API and are wrong for
> 0.85. The friction list below is mostly "0.85 moved my cheese". Pin to a known
> version and read the in-tree source, not the web.

---

## (a) Graceful shutdown / window-close-exits-cleanly

How it's wired in `app.py`:

- **Native window close (the acceptance gate).** Flet's default behaviour already
  tears the app down when the OS window closes — the Flutter client exits, the
  local Python host process follows. We keep `page.window.prevent_close = False`
  (do **not** intercept) and additionally bind `page.window.on_event`; on a
  `WindowEventType.CLOSE` we call `page.window.destroy()` as a belt-and-braces
  teardown. We also set `page.on_disconnect` to `os._exit(0)` in native mode, so
  if the client disconnects for any reason the host can't be left orphaned.
- **In-app Exit affordance.** The nav rail's **Exit** button calls
  `page.window.destroy()` (native) — same clean path as the OS close button. In
  web mode there's no OS window, so Exit is a no-op there (documented in code).
- **Web mode teardown.** `flet-web` runs uvicorn **in-process** (same Python PID),
  not as a child process. So there is no server/pywebview/flutter child to orphan
  — one `taskkill //PID <pid> //F` frees the port. Verified below.

Caveat I hit: `page.window.center()` is an **async** method in 0.85; calling it
synchronously emits a `RuntimeWarning: coroutine 'Window.center' was never
awaited`. I removed the call (windows center by default) rather than thread an
await into a sync setup block. No other lifecycle warnings.

I could not launch the **native** window in this headless context (no display +
the first-run client download), so native window-close was validated by code
+ API, not by an on-screen close. The web-mode teardown WAS validated live (see g).

## (b) Native file dialog

- Flet ships a real **native OS file picker**. In 0.85 it's a *service*
  (`ft.FilePicker`) you register on `page.services`, and `pick_files(...)` is an
  **async coroutine that returns the selected files directly** (the old
  `on_result` callback pattern is gone). Returns `FilePickerFile` objects with
  `.path` / `.bytes`.
- We call it with `dialog_title`, `allow_multiple=True`,
  `allowed_extensions=["csv","txt"]` — i.e. it filters to the GDE file types.
- This is a genuine OS dialog (not an HTML `<input type=file>`) in native desktop
  mode — a real advantage for a district-desktop tool. In **web** mode the picker
  degrades to the browser's file chooser (expected).
- For headless demo/screenshots the Convert screen also has a **"Use sample
  files"** button that seeds plausible filenames without opening a dialog, so the
  results flow is demonstrable without a GUI.

## (c) Brand theming — how applied + how close

Applied three ways:

1. **Material-3 `ft.Theme`** with `color_scheme_seed=PRIMARY (#1D5BB5)`,
   `use_material3=True`, an explicit `ft.ColorScheme(primary/secondary/surface/
   on_primary)`, `font_family="Segoe UI"`, comfortable visual density.
2. **Brand tokens as module constants** (navy/primary/sky/success/tint/border/
   text/muted) used directly on every surface — exact hex, no drift.
3. **Component-level styling**: navy→blue `LinearGradient` header band, rounded
   16px cards with a soft navy shadow + 1px `#DBEAFE` border, page tint
   `#F0F6FF` background, accent-bar metric tiles, pill file-chips, a numbered
   stepper, and one brand-blue primary button per screen (navy for the secondary
   "Download CSVs").

How close to brand: **very close** — see the screenshots captured during this
spike (`flet_home.png`, `flet_convert.png`, `flet_files.png`, `flet_progress.png`,
`flet_results3.png`). The **real SpacesEDU wordmark PNG loads** (copied into a
local `assets/` dir and served by Flet; text lockup is the fallback). Material-3
gives a clean, modern, trustworthy desktop feel out of the box.

Theming friction in 0.85: the old convenience helpers were **removed** —
`ft.padding.symmetric/only`, `ft.border.all/only`, `ft.border_radius.all` no
longer exist; you must build `ft.Padding(...)` / `ft.Border(...)` /
`ft.BorderRadius(...)` dataclasses directly. I added thin `pad()/pad_sym()/
b_all()/b_only()` helpers to keep call sites readable. Buttons use
`ButtonStyle` with `ControlState` keyed dicts (e.g. DEFAULT/DISABLED bgcolor).
Alignment is `ft.Alignment(x, y)` in -1..1 (no `ft.alignment.center` constant).

## (d) Single-exe packaging + Windows code-signing

**Packaging path:** `flet pack app.py` (wraps **PyInstaller**) → one
`dist/app.exe`. Neither `flet-cli` nor `pyinstaller` is installed in this venv yet
(`pip install flet-cli pyinstaller` first). Newer Flet also pushes `flet build
windows` (Flutter-toolchain MSIX/exe), which is heavier (needs the Flutter SDK +
Visual Studio build tools) but produces the most "native" artifact.

**The packaging gotcha that matters here:** the native Flutter desktop client is
**not** in the `flet-desktop` wheel — that wheel is ~15 KB of Python glue. On the
first native launch it **downloads `flet-windows.zip` (~80–100 MB)** from GitHub
releases into `~/.flet/client/flet-desktop-…-0.85.3` and runs it. For an **offline
single-exe** (school servers, no GitHub egress) you must **pre-seed that client
into the bundle** (`flet pack` handles the standard case; otherwise add the
`~/.flet/client/...` tree as PyInstaller `--add-data`). Expect a **~90–150 MB exe**
— the Flutter engine + CanvasKit dominate. (Footprints observed in this venv:
`flet` 3.7 MB, `flet-web` 69 MB of bundled web assets, `flet-desktop` ~0 MB wheel
→ the heavy client is the separate download.)

**Code-signing (Windows):** standard, well-trodden. The PyInstaller/`flet pack`
output is a normal PE `.exe`, so `signtool sign /fd SHA256 /tr <RFC3161 TSA>
/td SHA256 app.exe` with an **OV or (better) EV** Authenticode cert works. EV is
strongly recommended for a district-distributed exe — it gives immediate
SmartScreen reputation; an unsigned or OV-freshly-signed PyInstaller exe will
trip **Windows SmartScreen / Defender** warnings on first download (PyInstaller
single-file exes are a frequent false-positive). No Flet-specific signing
blocker. `flet build windows` (MSIX) is also signable via the standard MSIX
signing flow.

**Net:** packaging is feasible and the signing story is conventional, but the exe
is **large** and the **first-run client download** is the thing to plan around for
locked-down district machines.

## (e) Dependency / bundle-size footprint

Measured in this venv:

| package        | on-disk            | notes                                            |
|----------------|--------------------|--------------------------------------------------|
| `flet`         | 3.7 MB             | core Python control library                       |
| `flet-web`     | 69.1 MB            | uvicorn/FastAPI server + bundled Flutter-web/CanvasKit |
| `flet-desktop` | ~0 MB (wheel)      | glue only; downloads ~80–100 MB Windows client on 1st native run |

Runtime deps already present: `httpx`, `anyio`, `msgpack`, `oauthlib`, `repath`,
plus (for web) `fastapi`/`uvicorn`. A packaged **native** exe lands roughly
**90–150 MB**; a packaged **web** server is dominated by the 69 MB Flutter-web
bundle. Heavyweight vs. a pure-HTML toolkit, light-feeling vs. Electron.

## (f) Rough edges / API friction (0.85)

- **Legacy docs everywhere.** The 0.85 rewrite invalidated most online examples.
  Expect to read the installed source. The biggest moved-cheese items:
  - `ft.app(target=main)` is **deprecated** → use `ft.run(main, ...)` (still works,
    just warns). We use `ft.run`.
  - `ft.padding.*` / `ft.border.*` helper functions **removed** → dataclasses.
  - `FilePicker` is a **service** + **async returns files** (no `on_result`).
  - `page.window` is a runtime object; `Window.center()` is **async**.
  - SnackBar shown via `page.show_dialog(snackbar)` (it's a `DialogControl`);
    there's no `page.open()`.
  - Dropdown uses `on_select` and `ft.dropdown.Option(key=, text=)`.
- **Two extra wheels not installed by default.** Out of the box `flet` alone
  cannot run native *or* web — you must add `flet-desktop` (native) and
  `flet-web` (web). They self-install on demand but only if network is allowed.
- **First-run network dependency for native.** The ~90 MB client download is a
  real deployment consideration for offline district servers.
- **Flutter canvas = opaque DOM.** In web mode the UI is a single canvas, so the
  accessibility tree is sparse and Playwright/DOM automation can't target
  controls by role/text — you drive it by pixel coordinates. Fine for screenshots,
  awkward for DOM-based e2e testing.
- **Upside:** once you know the 0.85 API, the code is clean and declarative,
  Material-3 looks polished with little effort, async event handlers are
  first-class (`async def` handlers + `asyncio.sleep` for the mock work just
  work), and the native file dialog is a genuine OS dialog.

## (g) Web-mode self-verify result — **PASS**

Procedure (web mode only; native windows were NOT launched):

1. `SPIKE_WEB=1 <venvpy> app.py` in the background. Startup printed
   `SPIKE_PID=32348` and bound a listener:
   `TCP 0.0.0.0:8701 LISTENING 32348` (and the IPv6 equivalent).
2. HTTP probe of `http://localhost:8701` → **HTTP 200**, 3775-byte Flet/Flutter
   bootstrap shell; page title resolved to **"DistrictSync"** once loaded.
3. Drove the full flow live in a headless browser to confirm rendering: Home
   (dominant green health card + 4 metric tiles + wordmark) and Convert
   (district dropdown → "Use sample files" chips → "Run conversion" → ~1.5s
   spinner → results **DataTable** Students 4,821 / Staff 312 / Family 5,120 /
   Classes 642 / Enrollments 11,890 + "Looks healthy" verdict + "Download CSVs").
   Screenshots saved alongside this app.
4. Teardown: `taskkill //PID 32348 //F //T` → process gone, and
   `netstat` confirmed **nothing left listening on 8701**. No orphans.

> Earlier first attempt failed to serve because `flet-web` was not installed —
> the process started and printed its PID but never opened a listener. Installing
> `flet-web==0.85.3` fixed it. (This is itself a finding: bare `flet` can't serve
> web.) Nothing from this spike is left running; port 8701 is free.
