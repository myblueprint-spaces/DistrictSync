# Updater end-to-end proof, run 2026-11-16 against shared 0.3.1 (plan 0054 S5, DSYNC-64)

Output of `scripts/updater_e2e.py` on a real Velopack install (vpk 1.2.161, unsigned, pack id `myBlueprint.DistrictSync`, loopback feed, RFC 6979 test key, throwaway `DISTRICTSYNC_DATA_DIR` profile), with `myb_desktop_updater` 0.3.1 (commit `a585e90`). Local times are the machine clock. Section d is the G-097 case: a package that is only waiting in the install folder is not applied by a restart, and the twin shows it really was applicable. A crashed window-less exe waits on its error box, so the script kills a run after 60 s, as Task Scheduler would at its time limit (a known limit, see the plan).

```
[17:16:38] work folder: %LOCALAPPDATA%\Temp\claude\2ca2275a324631af\cdeefc00-2b7e-4b2a-98c4-5a5614215734\scratchpad\e2e
[17:16:38] PASS  nothing is installed at %LOCALAPPDATA%\myBlueprint.DistrictSync before the proof starts
[17:16:38] PASS  no other DistrictSync process is running
[17:16:38] building 3.99.1 (e2e flavour)
[17:18:03] packing 3.99.1 with vpk (unsigned)
[17:18:33] building 3.99.2 (e2e flavour)
[17:19:53] packing 3.99.2 with vpk (unsigned)
[17:20:21] building 3.99.3 (e2e flavour) - BAD BUILD: corrupt logging.conf
[17:21:35] packing 3.99.3 with vpk (unsigned)
[17:22:05] installing 3.99.1 for the current user (Setup.exe --silent)
[17:22:39] PASS  3.99.1 is installed under %LOCALAPPDATA%\myBlueprint.DistrictSync
[17:22:39] --- a. a tampered manifest is refused
[17:22:40] PASS  the failing nightly exits 1, exactly as it would without any update
[17:22:40] PASS  the check recorded bad_signature ({'at': '2026-11-16T23:22:40.482121+00:00', 'reason': 'bad_signature', 'status': 'refused', 'trigger': 'after_run', 'version': None})
[17:22:52] PASS  the running version is still 3.99.1
[17:22:52] PASS  nothing was staged
[17:22:52] --- b. a good manifest: staged, verified, kept safe, handed off, applied after exit
[17:23:02] PASS  the failing nightly still exits 1 (the update never changes a run's result)
[17:23:02] PASS  the check handed 3.99.2 to the installer ({'at': '2026-11-16T23:22:54.442866+00:00', 'reason': 'handed_off', 'status': 'handed_off', 'trigger': 'after_run', 'version': '3.99.2'})
[17:23:59] PASS  after the nightly exited, 3.99.2 is the installed version
[17:23:59] PASS  the running 3.99.1 package was kept before applying (['myBlueprint.DistrictSync-3.99.1-full.nupkg'])
[17:23:59] --- c. a bad build: installs, dies at start, is rolled back, never offered again
[17:24:00] PASS  3.99.2 is recorded healthy although its run failed (3.99.2)
[17:24:39] PASS  the bad 3.99.3 was taken (it is a signed, verified, newer version)
[17:24:44] PASS  start 1 of 3.99.3 dies in its own start-up (exit 1)
[17:24:44] PASS  after failed start 1 it is still 3.99.3 (tolerated)
[17:24:47] PASS  start 2 of 3.99.3 dies in its own start-up (exit 1)
[17:24:47] PASS  after failed start 2 it is still 3.99.3 (tolerated)
[17:25:47] the run did not end within 60s (a crashed windowed exe waits on its error box); killed it
[17:25:47] PASS  start 3 of 3.99.3 dies in its own start-up (exit -1)
[17:25:47] PASS  after failed start 3 it is still 3.99.3 (tolerated)
[17:26:48] the run did not end within 60s (a crashed windowed exe waits on its error box); killed it
[17:27:19] PASS  the fourth start asked for the kept copy; after it exited 3.99.2 is back
[17:27:19] PASS  3.99.3 is recorded bad (['3.99.3'])
[17:27:20] PASS  the feed still offers 3.99.3 but it is not offered to this app again ({'at': '2026-11-16T23:27:20.652537+00:00', 'reason': 'up_to_date', 'status': 'none', 'trigger': 'after_run', 'version': None})
[17:27:20] PASS  and 3.99.2 stays
[17:27:20] --- d. G-097: a package that is only WAITING in the install folder is not applied by a restart
[17:27:20] PASS  a newer full package (3.99.3) is waiting in the install's packages folder
[17:27:54] PASS  after two restarts the waiting package was NOT applied: still 3.99.2
[17:27:54] PASS  and the waiting package is still there, untouched
[17:27:54] premise twin: the waiting package really is applicable (Update.exe apply, as an auto-apply-on start would do)
[17:28:19] PASS  asked to apply it explicitly, 3.99.3 becomes the installed version
[17:28:19] cleaning up
[17:28:55] PASS  %LOCALAPPDATA%\myBlueprint.DistrictSync is gone (the test install was removed)
[17:28:55] RESULT: ALL ASSERTIONS HELD
```
