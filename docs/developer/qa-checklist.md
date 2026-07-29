# Pre-Release QA Checklist

A ~12-minute manual pass on the **built Windows exe** (not `python -m src.main`), run before every tag. These seven checks are exactly what CI is structurally blind to: native file dialogs, real user-profile state, upgrade migration, and **whether the UI actually renders** (the pack smoke proves a window exists and closes — it asserts nothing about what is drawn in it).

> **Tip — keep it non-destructive:** set `DISTRICTSYNC_DATA_DIR` (an absolute path) to a throwaway folder before launching the exe and steps 2, 3, 4, 6 and 7 run against a scratch profile instead of your real one.
>
> **Note — the override does NOT cover steps 1 and 5.** While it is set, `user_data_dir()` resolves to it outright and `migrate_legacy_data_dir()` is a no-op — so step 1 would prove nothing about a genuinely fresh install, and step 5's restored profile would never be read. **Run steps 1 and 5 with `DISTRICTSYNC_DATA_DIR` unset**, moving the real profile aside as those steps say.

| # | Step | Expected |
|---|------|----------|
| 1 | **Fresh profile.** Move `%LOCALAPPDATA%\DistrictSync` and `~\.districtsync` aside, then launch. | Setup wizard opens on the District step with the "Choose your district" placeholder — no error banner. |
| 2 | **Both Browse pickers.** Click Browse in the wizard Folders step AND on Convert (the v3.8.0 field bug). | Native dialog opens; the picked path shows in the field; the validation line updates. |
| 3 | **Convert fixture data.** Run Convert on `tests/snapshots/input` with the `sd74` config. | CSVs written; "Open folder" opens the output dir; Run History gains a `manual` row. |
| 4 | **District coherence.** Change Convert's district dropdown away from the saved district. | Header chip stays on the SAVED district and the amber note appears (by design). |
| 5 | **Stale-profile upgrade.** Restore a previous release's profile (`config.json` with forward-slash paths), then launch. **Say which location you restored to — they test different things:** `~\.districtsync` exercises the legacy→platform **relocation** path (`migrate_legacy_data_dir` + the `MOVED.txt` breadcrumb), `%LOCALAPPDATA%\DistrictSync` exercises the old **config-format** path only (no migration). Walking both is ~1 extra minute. | Settings intact; Browse still opens; no crash. From `~\.districtsync`: the data appears under `%LOCALAPPDATA%\DistrictSync` and `MOVED.txt` is left behind. |
| 6 | **Exit + long path.** Quit the app and check Task Manager; also launch the exe from a deeply nested folder. | No `flet.exe` orphans; the deep-path launch behaves normally. |
| 7 | **Light-flavor exe renders** (the gate CI cannot be: it asserts a window EXISTS, never what is drawn). Open the built exe, walk one navigation (e.g. Home → Convert → Home), open the Schedule step's clock (`TimePicker`), and open one Browse dialog. | Every surface paints — text, icons, the navy rail; the TimePicker and the native Browse dialog both open and dismiss; nothing blank, garbled, or missing. |
