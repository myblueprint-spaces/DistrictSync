# Updates in the installed build (plan 0054)

Developer guide to the update **check** (DSYNC-63, slice S4) and the **apply, health and rollback** wiring (DSYNC-64, slice S5). The plan is `.claude/plans/0054-windows-installer-silent-update.md`; the shared package that does the
heavy lifting is `myb_desktop_updater` (see "The private shared updater package" in `release.md`).

## What is where

| Piece | File | Job |
|---|---|---|
| Build flavour | `src/utils/build_flavor.py` | `installed` or `portable`. Stamped only by the one-folder Windows job; absent means portable. |
| Velopack start-up | `src/update/startup.py` | `App().run()` with apply-at-start OFF, installed flavour under a real install only. |
| The package's seat | `src/update/package.py` | `load_updater()` (lazy, installed only) and DistrictSync's `AppIdentity`. |
| Feed and keys | `src/update/feed.py`, `ALLOWED_UPDATE_HOSTS` | The ONE host and the trusted public keys. **Both empty today.** |
| The check | `src/update/check.py` | Triggers, guards, budget, outcome, last-check record. |
| Apply | `src/update/apply.py`, `check._hand_off` | Hands a staged update to the package's `hand_off`; the keep-clear-of-the-nightly rule. |
| Health and rollback | `src/update/health.py` | Records a start early, marks it healthy before ETL / at the first screen, restores the kept copy after more than two failed starts. |
| Help line | `src/update/status.py`, `screens/help.py` | One plain sentence in the About card, installed build only. |

## The two triggers

`after_run`: once, after the app's **own scheduled** run (`--source scheduled`), after its result is decided and
before the exit code is chosen. A hand-run CLI, a district's own cron, `--dry-run` and manual Convert never trigger it.
`window`: off-thread when the window opens, installed build only. Both are bounded by `MIN_INTERVAL` (6 hours).

## When it must not run (zero connections)

First applicable guard wins, in this order, and nothing is opened: `portable`, `not_installed` (no `Update.exe` beside
`current\`), `machine_scope`, `data_dir_override` (`DISTRICTSYNC_DATA_DIR` is a test or support profile),
`off_in_settings` (`AppConfig.update_enabled`), `not_configured` (no feed host or key), `package_missing`, `off_switch`
(`DISTRICTSYNC_UPDATES=off` or the marker file in the state folder; the package owns both), `unknown_version`
(a `dev` stamp), `recent`.

There is no environment variable, setting or option that points the release build at another host or key. A test hands
`run_update_check` its own `FeedTarget`; production call sites never do.

## It cannot change a run

A check stages at most. Every failure, refusal and slowness is a returned `CheckOutcome` (`status`, `reason` code,
`version`), logged as counts and codes, recorded in `<profile>\updater\last-check.json`. The wall-clock budget
(`BUDGET_SECONDS`) is enforced on a daemon thread because the package bounds each request, not the whole check. The
conversion's exit code and run record are computed from the run alone (`tests/test_update_check.py::TestAfterScheduledRun`).

## State

`<profile>\updater\` (inside the profile, so `DISTRICTSYNC_DATA_DIR` and uninstall behave): `last-check.json`
(written by DistrictSync, only for attempted checks), `health.json` and `staging\` (the package's). The last-check record
is deliberately not in `AppConfig`, so the nightly adds no new writer of `config.json`.

## Switching it on

Put the one feed host in `ALLOWED_UPDATE_HOSTS` and the public key(s) in `feed.TRUSTED_KEYS`. Both need the owner's Azure
steps (host name, front door, signing key) and are listed in the plan under "Provisions needed from Doug".

## Applying (S5)

A staged update is given to the package's `hand_off`, which refuses unless: updates are on, no other DistrictSync process runs
(or the count is unknown), the staged files still match what was verified, the app was installed by Velopack, the running version is
known healthy, and the running package could be kept first. Velopack then applies it **after this process exits**; nothing restarts
the app. The package's refusal while another copy runs is the single-writer rule (no separate lock file).

**Keep clear of the nightly.** The window trigger does not request an apply within `NIGHTLY_KEEP_CLEAR_MINUTES` (30) before the
registered run time (`AppConfig.schedule_time`, local clock). The scheduled trigger applies right after the nightly, the moment
furthest from the next one. The after-run check also runs after a FAILED run, so a build whose every nightly fails can still take the
release that fixes it; the exit code is computed from the run alone.

## Health and rollback (S5)

`begin_start()` runs right after the Velopack start-up call, only for a start that will reach a healthy mark (the window, and any
run with `--sis`; never `--version`, `--diagnose`, the SFTP subcommands or the elevated helpers). `mark_healthy()` is called before ETL
begins and when the first screen is up: **healthy is start-up completing, never "the conversion succeeded"**. After more than two
failed starts the package records the version bad (the feed never offers it again) and `restore_last_good` puts the kept copy back,
applied after exit; this works while updates are switched off.

**Known limit:** a crash before `begin_start` runs (an import-time failure while `src.main` loads its modules) leaves no record, so it
cannot be rolled back by this mechanism. Closing that needs a recording step before the heavy imports and is a follow-up, not part
of S5.

## The end-to-end proof

`scripts/updater_e2e.py` builds three unsigned one-folder copies in the **e2e flavour** (the installed flavour plus a stamped
loopback feed and a published RFC 6979 TEST key, `src/_e2e_target.py`, git-ignored), packs them with `vpk` under the real pack id,
installs 3.99.1 for the current user and drives the nightly through: a tampered manifest refused; a good update applied after exit; a
bad build that dies at start rolled back and never offered again. It uses `DISTRICTSYNC_DATA_DIR` for a throwaway profile and removes the
install afterwards. The e2e flavour exists for that script alone; tests (`tests/test_update_e2e_flavour.py`) pin that no release path can
stamp it or point the release build at loopback or a test key. Not run in CI yet (it needs the private package in the pack job).
