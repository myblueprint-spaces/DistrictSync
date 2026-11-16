# Updates in the installed build (plan 0054)

Developer guide to the update **check** (DSYNC-63, slice S4). The apply, health and rollback side arrives in S5 and is
added here. The plan is `.claude/plans/0054-windows-installer-silent-update.md`; the shared package that does the
heavy lifting is `myb_desktop_updater` (see "The private shared updater package" in `release.md`).

## What is where

| Piece | File | Job |
|---|---|---|
| Build flavour | `src/utils/build_flavor.py` | `installed` or `portable`. Stamped only by the one-folder Windows job; absent means portable. |
| Velopack start-up | `src/update/startup.py` | `App().run()` with apply-at-start OFF, installed flavour under a real install only. |
| The package's seat | `src/update/package.py` | `load_updater()` (lazy, installed only) and DistrictSync's `AppIdentity`. |
| Feed and keys | `src/update/feed.py`, `ALLOWED_UPDATE_HOSTS` | The ONE host and the trusted public keys. **Both empty today.** |
| The check | `src/update/check.py` | Triggers, guards, budget, outcome, last-check record. |
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
