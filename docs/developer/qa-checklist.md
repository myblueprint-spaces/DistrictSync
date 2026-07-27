# Pre-Release QA Checklist

A ~10-minute manual pass on the **built Windows exe** (not `python -m src.main`), run before every tag. These six checks are exactly what CI is structurally blind to: native file dialogs, real user-profile state, and upgrade migration.

| # | Step | Expected |
|---|------|----------|
| 1 | **Fresh profile.** Move `%LOCALAPPDATA%\DistrictSync` and `~\.districtsync` aside, then launch. | Setup wizard opens on the District step with the "Choose your district" placeholder — no error banner. |
| 2 | **Both Browse pickers.** Click Browse in the wizard Folders step AND on Convert (the v3.8.0 field bug). | Native dialog opens; the picked path shows in the field; the validation line updates. |
| 3 | **Convert fixture data.** Run Convert on `tests/snapshots/input` with the `sd74` config. | CSVs written; "Open folder" opens the output dir; Run History gains a `manual` row. |
| 4 | **District coherence.** Change Convert's district dropdown away from the saved district. | Header chip stays on the SAVED district and the amber note appears (by design). |
| 5 | **Stale-profile upgrade.** Restore a previous release's profile (`config.json` with forward-slash paths), then launch. | Settings intact; Browse still opens; no crash. |
| 6 | **Exit + long path.** Quit the app and check Task Manager; also launch the exe from a deeply nested folder. | No `flet.exe` orphans; the deep-path launch behaves normally. |
