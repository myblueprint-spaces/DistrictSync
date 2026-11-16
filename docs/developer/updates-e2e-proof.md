# Updater end-to-end proof, run 2026-11-16 (plan 0054 S5, DSYNC-64)

Output of `scripts/updater_e2e.py` on a real Velopack install (vpk 1.2.161, unsigned, pack id `myBlueprint.DistrictSync`, loopback feed, RFC 6979 test key, throwaway `DISTRICTSYNC_DATA_DIR` profile). Local times are the machine clock (ADT). Earlier runs of the same script found and fixed three harness faults, none in the product: a hand-off asked for while the previous apply was still settling failed `apply_failed` (a staged update is retried at the next check, by design); the first timeout was a crashed windowed exe waiting on its error box (the script now kills it at a time limit, as Task Scheduler would); and the feed port must be stamped identically into every build.

```
[15:51:28] work folder: <scratch>\e2e
[15:51:28] PASS  nothing is installed at C:\Users\shan.peiris\AppData\Local\myBlueprint.DistrictSync before the proof starts
[15:51:28] PASS  no other DistrictSync process is running
[15:51:28] building 3.99.1 (e2e flavour)
[15:52:48] packing 3.99.1 with vpk (unsigned)
[15:53:22] building 3.99.2 (e2e flavour)
[15:54:35] packing 3.99.2 with vpk (unsigned)
[15:55:06] building 3.99.3 (e2e flavour) - BAD BUILD: corrupt logging.conf
[15:56:22] packing 3.99.3 with vpk (unsigned)
[15:56:48] installing 3.99.1 for the current user (Setup.exe --silent)
[15:57:20] PASS  3.99.1 is installed under %LOCALAPPDATA%\myBlueprint.DistrictSync
[15:57:20] --- a. a tampered manifest is refused
[15:57:21] PASS  the failing nightly exits 1, exactly as it would without any update
[15:57:21] PASS  the check recorded bad_signature ({'at': '2026-11-16T21:57:21.631748+00:00', 'reason': 'bad_signature', 'status': 'refused', 'trigger': 'after_run', 'version': None})
[15:57:33] PASS  the running version is still 3.99.1
[15:57:33] PASS  nothing was staged
[15:57:33] --- b. a good manifest: staged, verified, kept safe, handed off, applied after exit
[15:57:37] PASS  the failing nightly still exits 1 (the update never changes a run's result)
[15:57:37] PASS  the check handed 3.99.2 to the installer ({'at': '2026-11-16T21:57:34.987922+00:00', 'reason': 'handed_off', 'status': 'handed_off', 'trigger': 'after_run', 'version': '3.99.2'})
[15:58:08] PASS  after the nightly exited, 3.99.2 is the installed version
[15:58:08] PASS  the running 3.99.1 package was kept before applying (['myBlueprint.DistrictSync-3.99.1-full.nupkg'])
[15:58:08] --- c. a bad build: installs, dies at start, is rolled back, never offered again
[15:58:09] PASS  3.99.2 is recorded healthy although its run failed (3.99.2)
[15:58:45] PASS  the bad 3.99.3 was taken (it is a signed, verified, newer version)
[15:59:45] the run did not end within 60s (a crashed windowed exe waits on its error box); killed it
[15:59:45] PASS  start 1 of 3.99.3 dies in its own start-up (exit -1)
[15:59:45] PASS  after failed start 1 it is still 3.99.3 (tolerated)
[16:00:46] the run did not end within 60s (a crashed windowed exe waits on its error box); killed it
[16:00:46] PASS  start 2 of 3.99.3 dies in its own start-up (exit -1)
[16:00:46] PASS  after failed start 2 it is still 3.99.3 (tolerated)
[16:01:46] the run did not end within 60s (a crashed windowed exe waits on its error box); killed it
[16:01:46] PASS  start 3 of 3.99.3 dies in its own start-up (exit -1)
[16:01:46] PASS  after failed start 3 it is still 3.99.3 (tolerated)
[16:02:46] the run did not end within 60s (a crashed windowed exe waits on its error box); killed it
[16:03:17] PASS  the fourth start asked for the kept copy; after it exited 3.99.2 is back
[16:03:17] PASS  3.99.3 is recorded bad (['3.99.3'])
[16:03:18] PASS  the feed still offers 3.99.3 but it is not offered to this app again ({'at': '2026-11-16T22:03:18.784142+00:00', 'reason': 'up_to_date', 'status': 'none', 'trigger': 'after_run', 'version': None})
[16:03:18] PASS  and 3.99.2 stays
[16:03:19] cleaning up
[16:03:59] PASS  C:\Users\shan.peiris\AppData\Local\myBlueprint.DistrictSync is gone (the test install was removed)
[16:03:59] RESULT: ALL ASSERTIONS HELD
```
