# Updater end-to-end proof, run 2026-11-17 (re-run after the DSYNC-66 review fixes) against shared 0.4.0 at commit 22e70eb (plan 0054 S5 follow-up, DSYNC-66)

Output of `scripts/updater_e2e.py` on a real Velopack install (vpk 1.2.161, unsigned, pack id `myBlueprint.DistrictSync`, loopback feed, RFC 6979 test key, throwaway `DISTRICTSYNC_DATA_DIR` profile) with `myb_desktop_updater` 0.4.0 (commit `98964ed`, which includes `myb_start_guard`). Four builds: 3.99.1 and 3.99.2 good; 3.99.3 dies in its own start-up (corrupt bundled logging config); 3.99.4 fails at import (pandas removed from the folder). Section f opens the real window with the bundled Flet client, force-ends the app and requires no window process to remain. Section e is the import-time case the health store alone could not see. Section d is the G-097 case. Times are the machine clock.

```
[10:57:45] work folder: %LOCALAPPDATA%\Temp\claude\2ca2275a324631af\cdeefc00-2b7e-4b2a-98c4-5a5614215734\scratchpad\e2e
[10:57:45] PASS  nothing is installed at %LOCALAPPDATA%\myBlueprint.DistrictSync before the proof starts
[10:57:45] PASS  no other DistrictSync process is running
[10:57:45] building 3.99.1 (e2e flavour)
[10:59:18] packing 3.99.1 with vpk (unsigned)
[11:00:00] building 3.99.2 (e2e flavour)
[11:01:40] packing 3.99.2 with vpk (unsigned)
[11:02:37] building 3.99.3 (e2e flavour) - BAD BUILD: corrupt logging.conf
[11:04:21] packing 3.99.3 with vpk (unsigned)
[11:05:09] building 3.99.4 (e2e flavour) - BAD BUILD: pandas removed (fails at import)
[11:06:52] packing 3.99.4 with vpk (unsigned)
[11:07:32] installing 3.99.1 for the current user (Setup.exe --silent)
[11:08:07] PASS  3.99.1 is installed under %LOCALAPPDATA%\myBlueprint.DistrictSync
[11:08:07] --- f. the window: a forcibly ended app leaves no window or process behind
[11:08:07] PASS  no flet.exe is running before the window test
[11:08:11] PASS  the window process (flet.exe) is up
[11:08:27] PASS  after the app was forcibly ended, no window process is left
[11:08:28] PASS  and no DistrictSync process is left
[11:08:28] PASS  the window start was confirmed at the first screen (count cleared)
[11:08:28] --- a. a tampered manifest is refused
[11:08:29] PASS  the failing nightly exits 1, exactly as it would without any update
[11:08:29] PASS  the check recorded bad_signature ({'at': '2026-11-17T17:08:29.746745+00:00', 'reason': 'bad_signature', 'status': 'refused', 'trigger': 'after_run', 'version': None})
[11:08:41] PASS  the running version is still 3.99.1
[11:08:41] PASS  nothing was staged
[11:08:41] --- b. a good manifest: staged, verified, kept safe, handed off, applied after exit
[11:09:00] PASS  the failing nightly still exits 1 (the update never changes a run's result)
[11:09:00] PASS  the check handed 3.99.2 to the installer ({'at': '2026-11-17T17:08:43.374534+00:00', 'reason': 'handed_off', 'status': 'handed_off', 'trigger': 'after_run', 'version': '3.99.2'})
[11:09:47] PASS  after the nightly exited, 3.99.2 is the installed version
[11:09:47] PASS  the running 3.99.1 package was kept before applying (['myBlueprint.DistrictSync-3.99.1-full.nupkg'])
[11:09:47] --- c. a bad build: installs, dies at start, is rolled back, never offered again
[11:09:48] PASS  3.99.2 is recorded healthy although its run failed (3.99.2)
[11:10:29] PASS  the bad 3.99.3 was taken (it is a signed, verified, newer version)
[11:10:31] PASS  start 1 of 3.99.3 dies in its own start-up (exit 1)
[11:10:31] PASS  after failed start 1 it is still 3.99.3 (tolerated)
[11:10:32] PASS  start 2 of 3.99.3 dies in its own start-up (exit 1)
[11:10:32] PASS  after failed start 2 it is still 3.99.3 (tolerated)
[11:10:33] PASS  start 3 of 3.99.3 dies in its own start-up (exit 1)
[11:10:33] PASS  after failed start 3 it is still 3.99.3 (tolerated)
[11:11:12] PASS  the fourth start asked for the kept copy; after it exited 3.99.2 is back
[11:11:12] PASS  3.99.3 is recorded bad (['3.99.3'])
[11:11:13] PASS  the feed still offers 3.99.3 but it is not offered to this app again ({'at': '2026-11-17T17:11:13.433312+00:00', 'reason': 'up_to_date', 'status': 'none', 'trigger': 'after_run', 'version': None})
[11:11:13] PASS  and 3.99.2 stays
[11:11:13] --- e. a build that fails at IMPORT: counted, exits at once with no dialog, recovered, never offered again
[11:11:51] PASS  the import-broken 3.99.4 was taken (signed, verified, newer)
[11:11:52] PASS  start 1 of 3.99.4 fails at import and EXITS 1 at once, no error box (exit 1, 0.6s)
[11:11:52] PASS  start 2 of 3.99.4 fails at import and EXITS 1 at once, no error box (exit 1, 0.6s)
[11:11:53] PASS  start 3 of 3.99.4 fails at import and EXITS 1 at once, no error box (exit 1, 0.6s)
[11:11:53] PASS  the crash was logged by type and place (['ModuleNotFoundError', '  __init__.py:124 in run_guarded'])
[11:11:53] PASS  and the log carries no message text
[11:11:56] PASS  the fourth start stops before the app runs (exit 1)
[11:12:29] PASS  recovered: the kept copy 3.99.2 is back after exit
[11:12:29] PASS  3.99.4 is recorded bad (['3.99.3', '3.99.4'])
[11:12:30] PASS  3.99.4 is still offered but never taken again ({'at': '2026-11-17T17:12:30.849504+00:00', 'reason': 'up_to_date', 'status': 'none', 'trigger': 'after_run', 'version': None})
[11:12:30] PASS  and 3.99.2 stays
[11:12:30] --- d. G-097: a package that is only WAITING in the install folder is not applied by a restart
[11:12:31] PASS  a newer full package (3.99.3) is waiting in the install's packages folder
[11:13:03] PASS  after two restarts the waiting package was NOT applied: still 3.99.2
[11:13:03] PASS  and the waiting package is still there, untouched
[11:13:03] premise twin: the waiting package really is applicable (Update.exe apply, as an auto-apply-on start would do)
[11:13:24] PASS  asked to apply it explicitly, 3.99.3 becomes the installed version
[11:13:24] cleaning up
[11:14:13] PASS  %LOCALAPPDATA%\myBlueprint.DistrictSync is gone (the test install was removed)
[11:14:13] RESULT: ALL ASSERTIONS HELD
```

## The SIGNED installer from the rehearsal, on a real machine (DSYNC-67 step 3, 2026-11-18)

The Setup is the artifact `DistrictSync-windows-installer-signed` of release-rehearsal run 44143958304 (installed flavour, vpk 1.2.161, signed through Azure Trusted Signing). It is installed per user with a throwaway `DISTRICTSYNC_DATA_DIR`, then run, then removed. **What it does not cover:** the installed flavour's feed host and trusted keys are empty on purpose (inert until the feed exists), so a local-feed update and rollback cannot run against THIS artifact; those are proven above on the e2e flavour. The nightly is registered live for the INSTALLED exe through the app's own code (the interactive-token kind), read back through COM, and deleted; it was not run, because a scheduled task cannot carry `DISTRICTSYNC_DATA_DIR` and would write the real profile. The stored-password branch is shown on a real COM task definition that is never registered (a dummy value, no credential). The first attempt of this script failed one check of its own (Task Scheduler omits the limited default `RunLevel` from the task XML); the check was corrected to read the level through COM and the whole proof re-run: the transcript below is that second, complete run.

```
[08:22:21] PASS  nothing is installed before the proof starts
[08:22:21] PASS  no DistrictSync process before the proof starts
[08:22:22] Setup signature: Valid|CN=myBlueprint Corp., O=myBlueprint Corp., L=Toronto, S=Ontario, C=CA|ts=True
[08:22:22] PASS  the downloaded Setup is validly signed and timestamped
[08:22:22] installing the signed Setup (per user, silent)
[08:22:30] PASS  installed: myBlueprint.DistrictSync\current\DistrictSync.exe exists
[08:22:40] PASS  the post-install first start was ended
[08:22:40] PASS  installed current/DistrictSync.exe: Valid, timestamped
[08:22:40] PASS  installed current/_internal/flet_view/flet.exe: Valid, timestamped
[08:22:41] PASS  installed Update.exe: Valid, timestamped
[08:22:41] PASS  the install carries Velopack's version record
[08:22:42] --version -> exit 0: DistrictSync 0.0.49
[08:22:42] PASS  the installed exe runs (--version)
[08:22:50] CLI smoke: version: PASS | dry-run: PASS | write-run: PASS | user-overlay: PASS | corrupt-profile: PASS | cli smoke: PASS
[08:22:50] PASS  CLI smoke on the installed signed copy (version, dry-run, write-run, user-overlay, corrupt-profile)
[08:22:53] window smoke: bundled-client boot: PASS (attempt 1): window booted from the bundled client; ~/.flet absent | close: PASS � zero new-view orphans | embed: PASS  |  close: PASS
[08:22:53] PASS  window smoke on the installed signed copy (bundled client, no orphans)
[08:22:53] PASS  no DistrictSync/flet process left after the smokes
[08:22:53] registering the nightly as a live Task Scheduler task through the app's own code
[08:22:54] live task: RunLevel=None Command=%LOCALAPPDATA%\myBlueprint.DistrictSync\current\DistrictSync.exe Arguments=--sis myedbc --source scheduled
[08:22:54] live task RunLevel read back through COM: 0 (0 = limited, 1 = highest); XML element: None (absent = the limited default)
[08:22:54] PASS  the live task runs at the LIMITED run level although Highest was requested
[08:22:54] PASS  the live task runs the INSTALLED exe
[08:22:54] PASS  the live task carries the scheduled-run arguments
[08:22:54] PASS  a REAL COM definition for a stored-password principal is LIMITED for the installed exe (not registered)
[08:22:54] PASS  positive twin: the same request for an exe outside an install is still Highest
[08:22:54] PASS  the proof task is deleted
[08:22:54] uninstalling and cleaning up
[08:23:05] PASS  uninstalled: the install's current folder is gone
[08:23:08] PASS  no process left
[08:23:08] PASS  throwaway profile and work folders removed
[08:23:08] RESULT: ALL ASSERTIONS HELD
```

