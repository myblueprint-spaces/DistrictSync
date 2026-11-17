# Updater end-to-end proof, run 2026-11-17 against shared 0.4.0 (plan 0054 S5 follow-up, DSYNC-66)

Output of `scripts/updater_e2e.py` on a real Velopack install (vpk 1.2.161, unsigned, pack id `myBlueprint.DistrictSync`, loopback feed, RFC 6979 test key, throwaway `DISTRICTSYNC_DATA_DIR` profile) with `myb_desktop_updater` 0.4.0 (commit `98964ed`, which includes `myb_start_guard`). Four builds: 3.99.1 and 3.99.2 good; 3.99.3 dies in its own start-up (corrupt bundled logging config); 3.99.4 fails at import (pandas removed from the folder). Section f opens the real window with the bundled Flet client, force-ends the app and requires no window process to remain. Section e is the import-time case the health store alone could not see. Section d is the G-097 case. Times are the machine clock.

```
[05:53:13] work folder: %LOCALAPPDATA%\Temp\claude\2ca2275a324631af\cdeefc00-2b7e-4b2a-98c4-5a5614215734\scratchpad\e2e
[05:53:13] PASS  nothing is installed at %LOCALAPPDATA%\myBlueprint.DistrictSync before the proof starts
[05:53:13] PASS  no other DistrictSync process is running
[05:53:13] building 3.99.1 (e2e flavour)
[05:54:44] packing 3.99.1 with vpk (unsigned)
[05:55:45] building 3.99.2 (e2e flavour)
[05:57:04] packing 3.99.2 with vpk (unsigned)
[05:57:39] building 3.99.3 (e2e flavour) - BAD BUILD: corrupt logging.conf
[05:58:56] packing 3.99.3 with vpk (unsigned)
[05:59:30] building 3.99.4 (e2e flavour) - BAD BUILD: pandas removed (fails at import)
[06:00:57] packing 3.99.4 with vpk (unsigned)
[06:01:31] installing 3.99.1 for the current user (Setup.exe --silent)
[06:02:03] PASS  3.99.1 is installed under %LOCALAPPDATA%\myBlueprint.DistrictSync
[06:02:03] --- f. the window: a forcibly ended app leaves no window or process behind
[06:02:04] PASS  no flet.exe is running before the window test
[06:02:07] PASS  the window process (flet.exe) is up
[06:02:23] PASS  after the app was forcibly ended, no window process is left
[06:02:23] PASS  and no DistrictSync process is left
[06:02:23] PASS  the window start was confirmed at the first screen (count cleared)
[06:02:23] --- a. a tampered manifest is refused
[06:02:25] PASS  the failing nightly exits 1, exactly as it would without any update
[06:02:25] PASS  the check recorded bad_signature ({'at': '2026-11-17T12:02:25.273870+00:00', 'reason': 'bad_signature', 'status': 'refused', 'trigger': 'after_run', 'version': None})
[06:02:37] PASS  the running version is still 3.99.1
[06:02:37] PASS  nothing was staged
[06:02:37] --- b. a good manifest: staged, verified, kept safe, handed off, applied after exit
[06:02:43] PASS  the failing nightly still exits 1 (the update never changes a run's result)
[06:02:43] PASS  the check handed 3.99.2 to the installer ({'at': '2026-11-17T12:02:38.715079+00:00', 'reason': 'handed_off', 'status': 'handed_off', 'trigger': 'after_run', 'version': '3.99.2'})
[06:03:30] PASS  after the nightly exited, 3.99.2 is the installed version
[06:03:30] PASS  the running 3.99.1 package was kept before applying (['myBlueprint.DistrictSync-3.99.1-full.nupkg'])
[06:03:30] --- c. a bad build: installs, dies at start, is rolled back, never offered again
[06:03:32] PASS  3.99.2 is recorded healthy although its run failed (3.99.2)
[06:04:13] PASS  the bad 3.99.3 was taken (it is a signed, verified, newer version)
[06:04:14] PASS  start 1 of 3.99.3 dies in its own start-up (exit 1)
[06:04:14] PASS  after failed start 1 it is still 3.99.3 (tolerated)
[06:04:16] PASS  start 2 of 3.99.3 dies in its own start-up (exit 1)
[06:04:16] PASS  after failed start 2 it is still 3.99.3 (tolerated)
[06:04:17] PASS  start 3 of 3.99.3 dies in its own start-up (exit 1)
[06:04:17] PASS  after failed start 3 it is still 3.99.3 (tolerated)
[06:04:51] PASS  the fourth start asked for the kept copy; after it exited 3.99.2 is back
[06:04:51] PASS  3.99.3 is recorded bad (['3.99.3'])
[06:04:52] PASS  the feed still offers 3.99.3 but it is not offered to this app again ({'at': '2026-11-17T12:04:52.814757+00:00', 'reason': 'up_to_date', 'status': 'none', 'trigger': 'after_run', 'version': None})
[06:04:52] PASS  and 3.99.2 stays
[06:04:52] --- e. a build that fails at IMPORT: counted, exits at once with no dialog, recovered, never offered again
[06:05:30] PASS  the import-broken 3.99.4 was taken (signed, verified, newer)
[06:05:31] PASS  start 1 of 3.99.4 fails at import and EXITS 1 at once, no error box (exit 1, 0.6s)
[06:05:31] PASS  start 2 of 3.99.4 fails at import and EXITS 1 at once, no error box (exit 1, 0.6s)
[06:05:32] PASS  start 3 of 3.99.4 fails at import and EXITS 1 at once, no error box (exit 1, 0.6s)
[06:05:32] PASS  the crash was logged by type and place (['ModuleNotFoundError', '  __init__.py:124 in run_guarded'])
[06:05:32] PASS  and the log carries no message text
[06:05:34] PASS  the fourth start stops before the app runs (exit 1)
[06:06:05] PASS  recovered: the kept copy 3.99.2 is back after exit
[06:06:05] PASS  3.99.4 is recorded bad (['3.99.3', '3.99.4'])
[06:06:07] PASS  3.99.4 is still offered but never taken again ({'at': '2026-11-17T12:06:07.114501+00:00', 'reason': 'up_to_date', 'status': 'none', 'trigger': 'after_run', 'version': None})
[06:06:07] PASS  and 3.99.2 stays
[06:06:07] --- d. G-097: a package that is only WAITING in the install folder is not applied by a restart
[06:06:07] PASS  a newer full package (3.99.3) is waiting in the install's packages folder
[06:06:40] PASS  after two restarts the waiting package was NOT applied: still 3.99.2
[06:06:40] PASS  and the waiting package is still there, untouched
[06:06:40] premise twin: the waiting package really is applicable (Update.exe apply, as an auto-apply-on start would do)
[06:06:59] PASS  asked to apply it explicitly, 3.99.3 becomes the installed version
[06:06:59] cleaning up
[06:07:38] PASS  %LOCALAPPDATA%\myBlueprint.DistrictSync is gone (the test install was removed)
[06:07:38] RESULT: ALL ASSERTIONS HELD
```
