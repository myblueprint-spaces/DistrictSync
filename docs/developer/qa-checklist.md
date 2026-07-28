# Pre-Release QA Checklist

A ~12-minute manual pass on the **built Windows exe** (not `python -m src.main`), run before every tag. These seven checks are exactly what CI is structurally blind to: native file dialogs, real user-profile state, upgrade migration, and **whether the UI actually renders** (the pack smoke proves a window exists and closes — it asserts nothing about what is drawn in it).

> **Tip — keep it non-destructive:** set `DISTRICTSYNC_DATA_DIR` to a throwaway folder before launching the exe and every step runs against a scratch profile instead of your real one (step 1 then needs no moving-aside at all).

| # | Step | Expected |
|---|------|----------|
| 1 | **Fresh profile.** Move `%LOCALAPPDATA%\DistrictSync` and `~\.districtsync` aside, then launch. | Setup wizard opens on the District step with the "Choose your district" placeholder — no error banner. |
| 2 | **Both Browse pickers.** Click Browse in the wizard Folders step AND on Convert (the v3.8.0 field bug). | Native dialog opens; the picked path shows in the field; the validation line updates. |
| 3 | **Convert fixture data.** Run Convert on `tests/snapshots/input` with the `sd74` config. | CSVs written; "Open folder" opens the output dir; Run History gains a `manual` row. |
| 4 | **District coherence.** Change Convert's district dropdown away from the saved district. | Header chip stays on the SAVED district and the amber note appears (by design). |
| 5 | **Stale-profile upgrade.** Restore a previous release's profile (`config.json` with forward-slash paths), then launch. | Settings intact; Browse still opens; no crash. |
| 6 | **Exit + long path.** Quit the app and check Task Manager; also launch the exe from a deeply nested folder. | No `flet.exe` orphans; the deep-path launch behaves normally. |
| 7 | **Light-flavor exe renders** (the gate CI cannot be: it asserts a window EXISTS, never what is drawn). Open the built exe, walk one navigation (e.g. Home → Convert → Home), open the Schedule step's clock (`TimePicker`), and open one Browse dialog. | Every surface paints — text, icons, the navy rail; the TimePicker and the native Browse dialog both open and dismiss; nothing blank, garbled, or missing. |
