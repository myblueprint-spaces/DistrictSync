# Flet 1.0 — conventions & gotchas (DistrictSync)

> **STATUS: AUTHORITATIVE** for the **pin · packaging · lifecycle · CI pre-seed · API forms** facts. The pin/packaging/lifecycle/CI-preseed facts were empirically confirmed on a real Win11 target in **PLAT-0 (2026-06-29)**; the exact 1.0 **API forms** (typed-dataclass control forms, the worker→UI marshalling contract, the `FilePicker` async-service contract, the `SnackBar`/dialog show API, the `page.window.*` async forms) were harvested in **PLAT-1 (2026-06-29)** by introspecting the installed `flet==0.85.3` package while building `src/ui_flet/`. See [DECISIONS 2026-06-29](claugentic-DECISIONS.md).
>
> **READ THIS FIRST before writing ANY Flet code for DistrictSync.** Flet 1.0 is a **ground-up rewrite**; the vast majority of Flet tutorials, StackOverflow answers, and model training data describe the **OLD `0.2x` API**. Each rule below pairs the 1.0 way with the **❌ 0.2x trap** so you don't regress to remembered patterns. Anchor to **this doc + the version-matched docs** (https://flet.dev/docs — getting-started / controls / reference) + the **installed package source** (it is the canonical 0.85.3 API), **never** old tutorials.

## Pinned versions (exact) — LOCKED in PLAT-0
- **`flet==0.85.3` · `flet-desktop==0.85.3` · `flet-web==0.85.3`** (+ `flet-cli==0.85.3` as a *build/dev-only* dep for `flet pack`). PLAT-0 confirmed on PyPI that **`0.85.3` is the LATEST** published version (top of the `0.80.x–0.85.x` 1.0-beta line) — `INSTALLED == LATEST` — and all three wheels install clean on **Python 3.13.2** with `pip check` reporting no conflicts.
- **❌ GOTCHA (confirmed via `pip show`):** `flet` requires only `httpx, msgpack, oauthlib, repath` — it pulls **NEITHER** `flet-desktop` **NOR** `flet-web`. So bare `flet` runs neither native nor web; you MUST pin **all three** explicitly. `flet-desktop` → requires `flet`; `flet-web` → requires `flet` **+ `fastapi` + `uvicorn`** (heavier — web mode is for headless CI screenshots, not a runtime dep of the shipped native exe).
- **Pin them to the SAME exact version.** The downloaded/bundled Flutter client version is tied to the `flet-desktop` package version (cache dir is `flet-desktop-{flavor}-{version}`); a divergent `flet`↔`flet-desktop` pin risks a client↔runtime handshake mismatch.
- **Recommended (gate, PLAT-1):** a CI assertion that `flet`/`flet-desktop`/`flet-web` are exact-pinned **and** match the version named in this doc — converts the API-drift safeguard from doc-only to gate-enforced.
- It is **beta** — the API can still shift before 1.0-final. Pin exactly; a ROADMAP trigger tracks the bump to stable.

## Entry point
- `ft.run(main)`.  **❌ NOT** the 0.2x `ft.app(target=main)`.
- `main(page)` builds the page; **native desktop is the default**. Web mode (for headless screenshots / CI) is gated behind an env flag in the prototype (`SPIKE_WEB`) — see the reference app.

## Controls are typed dataclasses
- 1.0 controls are **typed Python dataclasses** with strongly-typed event handlers and docstrings — the **installed package is itself the canonical API reference** (introspect it; don't trust old web examples). To introspect: `import dataclasses as dc; [f.name for f in dc.fields(ft.ColorScheme)]`.
- **❌ 0.2x traps:** the convenience helpers `ft.padding.all()/symmetric()` and `ft.border.*` were removed/restructured into dataclass forms; `SnackBar` is shown via `page.show_dialog`-style APIs; `page.window.*` methods (e.g. `center()`) are **async**.
- **EXACT 1.0 forms (confirmed against `flet==0.85.3` in PLAT-1; used verbatim in `src/ui_flet/shell.py`):**
  - **Padding / border / borderside — use the dataclasses directly** (the `ft.padding.*`/`ft.border.*` helper funcs are gone). The shell keeps thin wrappers (`pad`/`pad_sym`/`b_all`/`b_only`) over them for readable call sites — COPY these:
    ```python
    ft.Padding(left=0, top=0, right=0, bottom=0)             # was ft.padding.only(...)
    ft.Padding(left=h, top=v, right=h, bottom=v)             # was ft.padding.symmetric(horizontal=h, vertical=v)
    ft.BorderSide(width, color)                              # a single edge
    ft.Border(top=side, bottom=side, left=side, right=side)  # was ft.border.all(width, color)
    ft.Border(right=ft.BorderSide(1, color))                 # was ft.border.only(right=...)
    ```
  - **Theme / colour scheme** (light-only): `ft.Theme(color_scheme_seed=, use_material3=True, color_scheme=ft.ColorScheme(...), font_family="Segoe UI", visual_density=ft.VisualDensity.COMFORTABLE)`. `ft.ColorScheme` is a flat dataclass of M3 roles (`primary`/`on_primary`/`secondary`/`tertiary`/`error`/`surface`/`on_surface`/`on_surface_variant`/`outline`/… — full list via `dc.fields(ft.ColorScheme)`). **There is NO `brightness` field on `ColorScheme`** — light/dark is set on the page via `page.theme_mode = ft.ThemeMode.LIGHT` (`ft.Brightness.LIGHT/DARK` exists separately for `page.window.brightness`).
  - **Buttons:** the label is the **`content`** field — `ft.FilledButton(content="Save", icon=, on_click=, style=ft.ButtonStyle(bgcolor={ft.ControlState.DEFAULT: C, ft.ControlState.DISABLED: C2}, color=, padding=, shape=ft.RoundedRectangleBorder(radius=12), text_style=ft.TextStyle(size=, weight=ft.FontWeight.W_700)))`. **❌ `ft.FilledButton(text="Save")` RAISES `TypeError`** on 0.85.3 — there is **no `text` kwarg** on `FilledButton`/`TextButton` (`dc.fields` confirms `content`, not `text`). Positional (`ft.FilledButton("Save")`) works (sets `content`); the `text=` **keyword** does not. `ft.ControlState.DEFAULT/DISABLED` keys a per-state `bgcolor` map. **Canonical factory: `src/ui_flet/components.py`** (`primary_button`/`secondary_button`/`text_button`) — use it rather than hand-rolling so this trap can't recur. (This doc previously showed `FilledButton(text, …)`; that error produced a latent render-crash in PLAT-2's Browse/Save buttons, fixed in DS-1.)
  - **Misc:** `ft.Colors.with_opacity(0.14, color)`; `ft.Icons.<NAME>` (member names like `HOME_ROUNDED`); `ft.Alignment(0, 0)` is a positional `(x, y)`; `ft.LinearGradient(begin=ft.Alignment(-1,-1), end=ft.Alignment(1,1), colors=[...])`.
  - **Entry point:** `ft.run(main, assets_dir=...)` (sig: `ft.run(main, before_main=None, name='', host=None, port=0, view=ft.AppView.FLET_APP, assets_dir='assets', upload_dir=None, ...)`). Web mode: `ft.run(main, view=ft.AppView.WEB_BROWSER, port=N)`.

## FilePicker is an async SERVICE (replaces tkinter)
- 1.0 `FilePicker` is a **service** with an **async-returns-files** API: register it on `page.services`, call `get_directory_path()` / `pick_files()` and **await the returned result**.  **❌ NOT** the 0.2x `on_result` callback.
- Returns a **real server-side filesystem path** (the UI runs on the district server) — a direct, better replacement for `src/ui/folder_picker.py`'s tkinter dialog (drops the tkinter hidden-import).
- **CONTRACT (confirmed against `flet==0.85.3` in PLAT-1; the picker CODE LANDED in PLAT-2 — `src/ui_flet/filepicker.py` is the canonical impl, with boundary validation; IA-5/IA-8 reuse it):**
  - **Register once, on the page services list:**
    ```python
    file_picker = ft.FilePicker()
    if file_picker not in page.services:
        page.services.append(file_picker)
    ```
  - **`pick_files` is async and RETURNS the files** (no callback):
    ```python
    # async signature:
    #   pick_files(dialog_title=None, initial_directory=None,
    #              file_type=ft.FilePickerFileType.ANY, allowed_extensions=None,
    #              allow_multiple=False, with_data=False) -> list[FilePickerFile]
    files = await file_picker.pick_files(
        dialog_title="Select MyEd BC extract files",
        allow_multiple=True, allowed_extensions=["csv", "txt"],
    )
    if files:                       # None/[] on cancel
        for f in files:
            path = f.path           # real server-side filesystem path
    ```
  - **`get_directory_path(dialog_title=None, initial_directory=None) -> str | None`** — async, returns the chosen dir path or `None` on cancel. This is the direct replacement for `pick_directory()`.
  - **Boundary note (LANDED in PLAT-2):** a path returned from `FilePicker` is **untrusted input to the core** — validate it before persist/forward (it feeds `run_pipeline`'s `input_path`) the same way the CLI validates `--input`. `src/ui_flet/filepicker.py` does this: `validate_input_dir` (exists+is_dir, mirrors `pipeline.py:292`) / `validate_output_dir` (parent-structural) are pure + tested; `check_writable` is a separate effectful probe (TOCTOU-deferred to the loader's atomic `save_all`). Never pass a picked path straight into the core.

## Worker-thread → UI marshalling (THE #1 correctness trap)
- The ETL core (`run_pipeline`) is **synchronous/blocking** (pandas) → run it on a **worker thread** so the window never freezes.
- **Hand UI updates back to the Flet event loop** — **never mutate controls cross-thread**, or the window corrupts/freezes.
- **CONTRACT (confirmed against `flet==0.85.3` in PLAT-1; the `JobRunner` CODE is deferred to IA-5 — its first real `run_pipeline` caller, per the program's "promote on 2nd use" rule — but the contract is fixed now):**
  - **`Page` exposes both `page.run_thread(handler, *args)` (run a blocking fn off the UI thread) and `page.run_task(coro)` (schedule a coroutine on the loop).** The worker does its blocking work on the thread, then marshals each UI update back via `page.run_task(...)` (or by mutating controls only inside a coroutine the loop owns) and calls `page.update()` there — never from the worker thread directly.
    ```python
    def _work():                                  # runs OFF the UI thread
        try:
            result = run_pipeline(...)             # blocking pandas/ETL
            page.run_task(_on_done, result)        # marshal back to the loop
        except SystemExit as ex:                   # see asymmetry below
            page.run_task(_on_error, ex)
        except Exception as ex:
            page.run_task(_on_error, ex)
    page.run_thread(_work)
    ```
  - **`SystemExit` vs `Exception` `on_error` ASYMMETRY (load-bearing — confirmed at `src/etl/pipeline.py:294/302/305` + `420-426`):** `run_pipeline` calls **`sys.exit(1)`** on bad input (input dir missing `:294`, config `FileNotFoundError` `:302`, config `ValueError` `:305`). That `SystemExit` is caught at `pipeline.py:420` and **re-raised BEFORE** `_emit_run_log("failed")` — so on this path **NO `__DISTRICTSYNC_RUN__` record is written** (Run History will show nothing). A caught `Exception` (`:422-425`) **does** write a "failed" run-log record first. **Therefore the UI's `on_error` is the ONLY failure signal on the `SystemExit` path** — the worker MUST catch `SystemExit` (it is NOT an `Exception` subclass — a bare `except Exception` lets it propagate and kill the thread silently) and surface it to the user; do **not** assume a run-log record exists on every failure.
  - **Exit-3 SFTP-failure shape:** a *successful ETL with a failed SFTP upload* is NOT an exception — `run_pipeline` returns a `PipelineResult` with `sftp_attempted=True, sftp_ok=False` (the CLI maps that to exit 3). The UI reads those booleans off the returned result in `on_done`, not via `on_error`.

## Lifecycle (RE-CONFIRMED ON-SCREEN in PLAT-0 — the headline win)
- **Native OS window-close terminates the whole process tree cleanly: ZERO orphans.** PLAT-0 verified this *on a real Win11 desktop* (the earlier bake-off could only assert it via code/API, headless): a real `WM_CLOSE` posted to the OS window tore down the full tree in **0.77s running from source** and **1.27s for the packaged exe** — zero orphans both ways (`psutil` process-tree check). This is *why* Flet was chosen — **no idle watchdog, no beacon, no zombie-websocket reaping, no "close this tab" page** (all the Streamlit-in-browser workarounds are deleted).
- **Process-tree shape (native):** the Python entry process re-execs/launches a **second Python host** which spawns the **`flet.exe`** Flutter client child (observed `python → python → flet.exe`). Window-close collapses the entire chain. Any orphan check must walk descendants recursively *and* sweep for stray `flet.exe` — the harness in PLAT-0 did both.
- Flet binds an **internal localhost port** (random) for the Python↔Flutter-client channel — **not** browser-facing; it closes with the window.
- **EXACT close/lifecycle forms (confirmed against `flet==0.85.3` in PLAT-1; used verbatim in `src/ui_flet/shell.py`):**
  ```python
  page.window.prevent_close = False            # OS close button tears the app down on its own
  page.window.on_event = on_window_event       # also bind for explicit close paths
  def on_window_event(e):
      if getattr(e, "type", None) == ft.WindowEventType.CLOSE or getattr(e, "data", None) == "close":
          page.window.destroy()                # collapses python→python→flet.exe; zero orphans (PLAT-0)
  page.on_disconnect = lambda _e: os._exit(0)  # native: ensure the host process can't orphan
  ```
  - **`page.window.*` sizing is plain attribute assignment** (`page.window.width/height/min_width/min_height = ...`) and is synchronous. **`page.window.center()` / `.destroy()` / `.close()` are async methods** — DO NOT call `center()` synchronously (it's a coroutine → `RuntimeWarning`); the window centers by default, so the shell just relies on that. `destroy()` is fire-and-forget in the close handler (tears the tree down). `ft.WindowEventType.CLOSE` is the close event type; `ft.Brightness.LIGHT/DARK` is for `page.window.brightness`.
- **Write-in-flight close guard: DEFERRED to IA-5** (its first write-in-flight surface). PLAT-1 ships the proven zero-orphan close + a documented `_on_leave(page)` leave-point **seam** (a no-op hook in `shell.py`); IA-2 attaches the "closing this window does not stop the nightly sync" reassurance and IA-5 attaches the write guard there. The loader's backup-and-restore atomicity (`save_all`) is the real safety net regardless.
- **Single-instance guard: DEFERRED** (YAGNI) — add only if field reports show double-launch confusion.
- **Dialogs / SnackBar — show via `page.show_dialog(control)`** (confirmed sig: `Page.show_dialog(self, dialog: DialogControl) -> None`; `page.pop_dialog()` dismisses). `ft.SnackBar(content=ft.Text(...), bgcolor=...)` and `ft.AlertDialog(...)` are both `DialogControl`s passed to `show_dialog`.  **❌ NOT** the 0.2x `page.snack_bar = ...; page.update()` / `page.dialog = ...` assignment pattern.

## Packaging (windowed · offline · signed) — MEASURED in PLAT-0
- **`flet pack app.py` defaults to `--noconsole --onefile`** (PLAT-0 observed the exact passthrough: `['app.py','--noconfirm','--noconsole','--name',…,'--onefile',…]`) → **ONE windowed exe, NO console**. This kills the original "console window hangs around" bug. (Use `flet pack`, not raw PyInstaller — the `flet pack` path runs the `hook-flet.py` PyInstaller hook that embeds the client; see below.)
- **No-console is deterministic, not vibes:** the built exe's **PE Optional-Header `Subsystem == 2` (`IMAGE_SUBSYSTEM_WINDOWS_GUI`)**, not `3` (console). PLAT-0's smoke asserts this with `pefile` — **LANDED** in `scripts/ci_flet_pack_smoke.py` (the productionized release-gate smoke against the real `DistrictSync-flet` exe), run by `.github/workflows/flet-pack.yml` on all 3 OS.
- **The real pack target is the launcher, not `main.py`** (PLAT-3, until CUT-1): `flet pack src/ui_flet/launcher.py --name DistrictSync-flet` — `launcher.py` is the UI-only Flet entry (no `main.py` dual-mode dispatch), so packing it sidesteps the Streamlit-default conflict and leaves `main.py` untouched. The full `src.*`+flet hidden-import set is bundled now (so IA-5's `run_pipeline` wiring needs no `release.yml` change).
- **`flet pack` has NO native `--paths`/`--exclude-module`/`--collect-submodules`.** Its CLI only takes `--add-data`/`--add-binary`/`--hidden-import`/version metadata/`-y`; raw PyInstaller flags MUST go through **`--pyinstaller-build-args`**, and **each token needs its own `--pyinstaller-build-args=<token>`** (its `nargs="*"` stops at flag-like tokens, and PyInstaller needs `--paths` and `.` as *separate* args). So `--paths .` becomes `--pyinstaller-build-args="--paths" --pyinstaller-build-args="."`.
- **Close-gating policy (PLAT-3):** the zero-orphan close is **GATING on Windows** (`scripts/ci_flet_pack_smoke.py … --require-close` — real `WM_CLOSE` via pure ctypes/user32, baseline-delta orphan sweep) and **INFO-only / non-gating on Linux & macOS** (no portable `WM_CLOSE`; the headless runner can't guarantee a real window-manager close — it does a best-effort `terminate()` teardown + prints the orphan count and always exits 0). Embed (gating, all 3 OS) and close are kept as **separate verdict axes**.
- **CONFIRMED offline:** `flet pack` **embeds the Flutter client in the exe**; the shipped app needs **no runtime download** and **works fully offline**. PLAT-0 proved this airtight — moved `~/.flet` aside **and** set `FLET_CLIENT_URL` to an unreachable host, and the window still opened (7.1s). Mechanism: `flet_desktop.ensure_client_cached()` resolves **cache → bundled archive (`get_package_bin_dir()`) → download**, and `__download_flet_client()` runs **only** when the bundled archive is *absent*. The bundled archive being present means the download branch is never reached; the client is extracted from the bundle into `~/.flet/client/` on first launch (that extraction — *not* a download — is why the cache reappears).
- **The embedded archive (`get_package_bin_dir()` → `flet_desktop/app/<archive>`), NOT the `~/.flet` cache, is the source of truth (anti-poisoning).** `flet pack`'s `hook-flet.py` adds the *populated* cache into the bundle as `('<bin_path>', 'flet_desktop/app')` and `pack.py` compresses it to a per-OS archive (`flet-windows.zip` / `flet-macos.tar.gz` / `flet-linux-<distro>[-light]-<arch>.tar.gz`). So a populated-but-tampered cache hit would still pass a cache-presence smoke — the gate that actually matters is that the **packed** exe references the archive. **PLAT-3 (LANDED)** proves this at **build time**: after the warm-up, the workflow asserts `~/.flet/client/flet-desktop-<flavor>-<ver>` exists and is non-empty, and after `flet pack` it greps the PyInstaller manifest (`build/DistrictSync-flet/Analysis-00.toc`) for `flet_desktop/app` + a client-archive name (`scripts/ci_flet_pack_smoke.py --assert-embed`). The runtime move-aside smoke is the necessary confirmation, not the only proof.
- **Measured size (PLAT-3, LANDED):** the **real `DistrictSync-flet` windowed onefile = ~190 MB** (`189,638,864` bytes — full flavor, Windows, local Win11 build with the full `src.*`+flet hidden-import set). Much larger than the ~58 MB flet-only spike because it bundles pandas/pydantic/paramiko/keyring + the ETL core; Streamlit is `--exclude-module`'d here and the exe is `--onefile` (compresses the ~96 MB uncompressed client). Treat ~190 MB as the size budget — the workflow echoes the measured size each build so an unexercised transitive can't quietly balloon it. (Linux/macOS sizes differ — Linux `light` is smaller; record from the 3-OS CI run.)
- **⚠️ OPEN RISK — macOS `flet pack` bundling UNVERIFIED.** PLAT-0 confirmed embedding on **Windows** only (can't test macOS here). The upstream 0.85.0 "bundle the client into `flet pack`" fix names **Windows + Linux**; the macOS path uses `.tar.gz`/`.app` + `FLET_VIEW_PATH` and Flet's macOS docs steer toward `flet build macos`. **PLAT-3 must verify on a `macos-latest` runner** that the `.app`/exe embeds the client (else fall back to `FLET_VIEW_PATH` or `flet build macos`). Linux embedding is claimed-fixed but should also be smoke-verified in PLAT-3.
- **First run *from source* (dev/CI, NOT the packaged exe)** downloads a ~96 MB client from GitHub into `~/.flet` (one-time, cached). On this machine it is already cached at `~/.flet/client/flet-desktop-full-0.85.3/` (96 MB; `flet.exe` + `flutter_windows.dll` + `libmpv-2.dll` + plugin DLLs + `data/`). Pre-seed in CI (see below) or accept the one-time delay.
- **No-console builds hide boot errors** → the launcher (`src/ui_flet/launcher.py`) has an **early-failure path** that writes the full traceback to `~/.districtsync/etl_tool.log` and shows an error dialog **before** the shell mounts. The smoke prints that log on ANY failure (the windowed exe can't print a boot error to a console).
- **Code-signing:** **SignPath Foundation (free)** — the repo is public/OSS so it qualifies; signs in GitHub Actions (cert from Sectigo). Caveats: SmartScreen publisher shows **"SignPath Foundation"**; SmartScreen reputation **builds over downloads** (only an EV cert clears it instantly). **Azure Artifact Signing (~$10/mo)** is the cheap-paid upgrade for *your* org name later. See [DECISIONS 2026-06-29](claugentic-DECISIONS.md) + Plan 0013 "Code-signing reality".

## CI offline client pre-seed (PLAT-0 decision → PLAT-3 release.yml)
- **Strategy:** cache `~/.flet` with **`actions/cache`** keyed on **`runner.os` + the flet version + the desktop flavor**, and run an explicit **headless warm-up `python -c "import flet_desktop; flet_desktop.ensure_client_cached()"`** *before* `flet pack`. PLAT-0 confirmed `ensure_client_cached()` is a **no-arg** function on 0.85.3 and that it download-and-extracts (no window) — so **no `xvfb`** is needed for the warm-up. The cache is a pure optimization: on a miss, `flet pack` self-downloads the client anyway and **fails loud** rather than producing a download-at-runtime exe, so a cache miss can never silently break the offline guarantee.
- **Cache key MUST include the OS + flavor.** The client is per-OS: Windows `flet-windows.zip`, macOS `flet-macos.tar.gz`, **Linux `flet-linux-{distro}[-light]-{arch}.tar.gz`** (the name encodes the distro). On Linux, **pin the runner image** (e.g. `ubuntu-22.04`, not floating `ubuntu-latest`) so the distro-keyed filename doesn't drift. Flavor default is **`light` on Linux, `full` elsewhere** — set `FLET_DESKTOP_FLAVOR=full` if you want Linux to match the Windows/macOS `full` client (decide in PLAT-3).
- **Air-gap escape hatch:** `FLET_CLIENT_URL` overrides the GitHub-Releases download URL (point it at an internal mirror). `FLET_VIEW_PATH` points directly at an extracted client dir (checked before the cache). `FLET_CACHE_DIR` relocates only the *`flet build`* template cache — **irrelevant to the `flet pack` path we use.** The CI pack/release path sets **NO `FLET_CLIENT_URL`** — it is only a *smoke-local* env for the offline assertion + a documented runtime air-gap hatch; baking it into CI would silently make the offline guarantee runtime-config-dependent.
- **The cache key MUST encode the flavor, not hardcode `full`.** Set `FLET_DESKTOP_FLAVOR` explicitly per-OS so warm-up, cache key, and `flet pack` provably agree: **`full` on Windows + macOS, `light` on Linux** (PLAT-0b proved `light` is enough). Confirm `pyproject.toml` has **no conflicting `[tool.flet]` desktop flavor** (it doesn't, as of PLAT-3).
- **Reference YAML (per-OS matrix step, before `flet pack`) — LANDED in `.github/workflows/flet-pack.yml` (PLAT-3):**
  ```yaml
  env:
    FLET_VERSION: "0.85.3"            # in lockstep with the pinned flet/flet-desktop/flet-web
  # ... matrix sets FLET_DESKTOP_FLAVOR per-OS (full/full/light for win/mac/linux)
  steps:
    - uses: actions/cache@v4
      with:
        path: ~/.flet
        key: flet-client-${{ runner.os }}-${{ env.FLET_VERSION }}-${{ env.FLET_DESKTOP_FLAVOR }}
    - name: Warm Flet client cache (headless, no window)
      run: python -c "import flet_desktop; flet_desktop.ensure_client_cached()"
    - name: Assert client cache populated     # RC1a — the warm-up download fallback is silent
      run: test -d "$HOME/.flet/client/flet-desktop-${FLET_DESKTOP_FLAVOR}-${FLET_VERSION}"
    - name: Pack windowed offline exe         # NO native --paths/--exclude-module on `flet pack`:
      run: |                                  # raw PyInstaller flags go via --pyinstaller-build-args
        flet pack src/ui_flet/launcher.py --name DistrictSync-flet --yes \
          --add-data "config;config" --hidden-import flet --hidden-import flet_desktop ... \
          --pyinstaller-build-args="--paths" --pyinstaller-build-args="." \
          --pyinstaller-build-args="--exclude-module" --pyinstaller-build-args="streamlit"
    - name: Assert packed exe embeds the client   # RC1b — build-time embed proof, not just runtime
      run: python scripts/ci_flet_pack_smoke.py --assert-embed build/DistrictSync-flet/Analysis-00.toc
  ```

## Cross-platform — ALL THREE PROVEN (Windows ✅ · macOS ✅ · Linux ✅)
- **Flet is cross-platform by design** (Flutter renderer per-OS; the Python UI code is identical). PLAT-0b proved `flet pack` produces a **windowed, offline-embedded** artifact on all three OSes — on **real CI runners** (Python 3.13; run [#28370535068](https://github.com/sh4npeiris/DistrictSync/actions/runs/28370535068) on `sh4npeiris/DistrictSync`) **plus** Windows + Linux locally. Offline-embedding signal = with `~/.flet` removed and `FLET_CLIENT_URL` unreachable, the binary still launches because it extracts the **embedded** client (download impossible):

  | OS | artifact | size | no-console | offline-embedded |
  |---|---|---|---|---|
  | Windows (`full`) | `.exe` | 58–62 MB | ✅ PE `Subsystem==2` | ✅ PASS |
  | macOS arm64 (`full`) | binary | 73 MB | n/a | ✅ PASS |
  | Linux (`light`) | ELF | 48–66 MB | n/a | ✅ PASS (under a display) |

- **macOS embeds the client too** — settled positively on a real `macos-latest` (arm64) runner; **no `flet build macos` fallback needed** (despite upstream's 0.85.0 embed-fix naming only Win+Linux). Keep `FLET_VIEW_PATH` / `flet build macos` in mind only if a future Flet version regresses macOS embedding.
- **⚠️ Linux DESKTOP GUI needs system libs + a display.** The Flet Linux client dynamically links **`libsecret-1.so.0`** (GNOME keyring, via `flutter_secure_storage`) + GL/GTK runtime (`libgl1`, `libglib2.0-0`, `libgtk-3-0`), and needs an **X/Wayland display** to launch. A normal Linux *desktop* has these; a **minimal/headless** box does NOT (fails `error while loading shared libraries: libsecret-1.so.0`, or silently can't open a window). **A headless Linux server should run the pure-Python CLI** (zero Flet deps — already cross-platform); only a Linux *desktop* user needs the cockpit, and then `apt install libsecret-1-0 libgtk-3-0 libgl1` is a documented prerequisite. (CI proved the desktop path under `xvfb`.)
- **Clean-close (zero-orphan) is proven on Windows** (real `WM_CLOSE`, 0.77s/1.27s — re-confirmed in PLAT-3 on the real `DistrictSync-flet` exe: PE subsystem 2 + offline-embed + zero-orphan close all PASS, ~190 MB, local Win11). So PLAT-3's smoke **GATES the close on Windows** (`--require-close`). On Linux/macOS the same teardown code runs but a headless CI runner can't guarantee a real WM `WM_CLOSE`, so the close is **INFO-only / non-gating** there (best-effort `terminate()` + orphan-count report); the embed check still gates on all 3 OS.
- **The matrix CI workflow is LANDED (PLAT-3):** the throwaway-fork `flet-xplatform-verify` mechanism became the reusable **`.github/workflows/flet-pack.yml`** (`on: workflow_call`), called by both **`release.yml`** (tag, as the `build-flet` job) and **`flet-verify.yml`** (`pull_request` on build/UI/deps paths + `workflow_dispatch` — anti-rot). The smoke is **`scripts/ci_flet_pack_smoke.py`**. The Flet exe is a **CI-artifact-only preview** (`actions/upload-artifact`), NOT attached to the public Release (which stays one Streamlit exe per OS + SHA-256 checksums + a one-line preview note); the dual-mode `main.py` repackage + Streamlit drop is CUT-1.

## Theming (brand → ft.Theme)
- Port the brand **values** from `src/ui/brand.py` (`#1D5BB5` primary · `#0F2D6B` navy · `#0EA5E9` sky · `#16A34A` green · `#F0F6FF` page tint · `#DBEAFE` border · `#0F172A` text · `#64748B` muted) into a tiered Python token module → one `build_theme()` → `ft.Theme(Material-3 ColorScheme)`.
- **❌ Do NOT port** `brand.py`'s ~350 lines of `!important` CSS — those exist only to fight Streamlit/BaseWeb dark-mode defaults; a typed Flet theme deletes that entire failure class.
- **Validate the full M3 ColorScheme mapping on a real screen** — `primary`/`secondary`/`tertiary`/`surface` roles are not 1:1 with the brand palette. **Light-only** (one `theme_mode` line); dark mode deferred (YAGNI).

## Entry-point wiring (the dual-mode contract — DO NOT break)
- `src/main.py` `__main__`: `if len(sys.argv) == 1: <Flet UI> else: <CLI>`. **Only the UI branch changes** (Streamlit launcher → `src/ui_flet/launcher.py`). The CLI path, `run_pipeline`, all flags, and **exit codes 0/1/2/3 stay byte-identical** — the nightly scheduled task calls the CLI, never the UI. `ft.run` blocks until the window closes, so the existing `sys.exit(0)` after the UI call still holds.
- New Flet code lives additively in **`src/ui_flet/`**; the Streamlit `src/ui/` stays intact as the rollback floor until **CUT-1**.

## Reference
- **Working bake-off prototype:** [`docs/reference/flet-prototype-spike/app.py`](reference/flet-prototype-spike/app.py) (+ `NOTES.md`, `RUN.md`, `assets/`) — a proven Home + Convert in Flet 1.0 showing the shell, `ft.Theme` brand mapping, `ft.FilePicker`, the async-run-on-thread pattern, and the clean-close lifecycle. **Throwaway reference — delete after PLAT-1.**
- Run it (from the prototype dir, with a Flet 1.0 venv): `python app.py` (native) · `SPIKE_WEB=1 python app.py` (web on :8701).
- **Docs (version-matched):** https://flet.dev/docs/getting-started/ · https://flet.dev/docs/controls/ · https://flet.dev/docs/reference/  — **NOT** old `0.2x` blog tutorials.
