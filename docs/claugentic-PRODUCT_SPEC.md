# PRODUCT_SPEC — what DistrictSync is supposed to be

> Plain-English, durable statement of what DistrictSync promises: who it's for, the job it does, each feature's flow and the states that matter — ending in a machine-readable list of acceptance criteria. This is **user-owned**: it is never stamped and never auto-refreshed. The narrative product context (personas, the three hats, the trust bar, the design language, the three journeys) lives in [`docs/claugentic-PRODUCT.md`](claugentic-PRODUCT.md); this file is its checkable projection.

## Who it's for

A **non-technical BC school-district administrator**. They are not an engineer. They open DistrictSync perhaps **2–3 times a year** — once to set it up, then occasionally to check it's still working or to reconfigure after a change. Between those visits, the tool runs unattended every night on a district server via the Windows Task Scheduler (or cron on Linux/macOS).

The same admin wears **three hats at three moments** — the same person, different job, mood, and stakes each time:

- **The Installer** (first run, ~once) — *"Get this configured and prove tonight's sync will actually reach SpacesEDU, then walk away."* Wants a linear, verifiable path and certainty, not features.
- **The Watcher** (steady state, ~95% of the product's life) — *"Tell me in one glance that last night's roster reached SpacesEDU — and if not, what to do."*
- **The Firefighter** (incident, rare + high-stress) — *"Something's wrong — show me what, and the shortest fix."*

Because the audience is non-technical and the tool handles **student PII (FERPA-adjacent)**, one rule is absolute: the admin is **never** shown a machine identifier, a filesystem path, a raw config id (`sd48myedbc`), a raw timestamp, a raw exception, or a stack trace. Every surface speaks in plain, calm, human language.

## The job-to-be-done

> *"When my district's roster needs to reach SpacesEDU, I want the nightly sync to just work — and when I check in, I want to trust it ran and delivered, in words I understand."*

## The promise

**Any partner downloads the app, runs the wizard, and has a working nightly sync.** Once set up, DistrictSync is a **verdict-first cockpit**: open the window and immediately know the one thing that matters — *is my roster syncing?* — as a single health verdict (HEALTHY / WARNING / FAILED) with a plain headline, before any detail or numbers. The nightly sync runs on its own; setup is a calm, guided flow; nothing ever dead-ends in jargon.

The governing rule is the **trust bar**: *never assert a state you didn't check.* Every success names **what** it checked and **when** — "we tested the connection to <host> as <user> just now and it worked", "the task is registered — next run tonight at HH:MM" — never a promise about the future, never a self-reported boolean standing in for a real check. When a fact can't be confirmed right now, the honest answer is *"we couldn't confirm this right now"* — never a green borrowed from stale config.

## What the promise assumes (the boundary of what we own)

The promise above covers **download → wizard → nightly sync**. Two links in the partner's real path sit *outside* the product today, and the spec names them rather than implying we own them:

- **Getting the app onto the server.** The released binaries are **not code-signed**, so a first download can meet a Windows SmartScreen warning or a district antivirus prompt before the app ever opens. Getting past that is covered by the partner installation guide and district IT, not by the app.
- **Getting GDE files into the input folder.** DistrictSync **reads** the MyEducation BC extract files; it does not produce them. The district must already have its MyEdBC GDE export scheduled to drop files into the input folder on its own cadence. The wizard validates that the input folder *is a usable folder* — it does not verify that tonight's extract will land there.

Naming these is the trust bar applied to the product's own edges: a tool that says "you're set up" should not be read as a claim about links it never checked.

## Features

Each feature below carries a **Flow** (the happy path), **States** (only which of loading/empty/error this surface has — the *bar* for those states is the standard [`docs/claugentic-standards/product-ux.md`](claugentic-standards/product-ux.md) → *Loading / empty / error states* and *User-flow completeness*, not restated here), and **What good feels like**. The pytest suite (~1,686 tests, SD74 golden-file snapshot, config validation, 80% coverage gate) is the real automated gate behind these; the acceptance criteria are the plain-English projection a person can check.

### First-run setup wizard

The Installer's single guided path from a fresh download to a verified nightly sync.

- **Flow**
  1. Open the app for the first time; Home shows a calm onboarding welcome (the single front door) and a "Start setup" button.
  2. **District** — choose the district config from a "Choose your district" picker (auto-selected only when exactly one config exists — never a silent default). District leads: *pick who you are first, then where your files live.*
  3. **Folders** — pick the GDE input folder and the output folder.
  4. **Delivery** — enter and test the SFTP credential, or "Set up later". Delivery precedes Schedule so the delivery setting is already baked in when the task is registered.
  5. **Schedule** — pick a nightly run time and register the Windows task (a one-time UAC permission prompt), or "Set up later".
  6. **Finish** — an honest, adaptive summary names what was checked and when, plus a checked-summary card listing each step as configured (✓) or deferred. Reaching this finish line — not any single step — is the only thing that marks the install "set up".
- **States** — **loading** (schedule registration and SFTP test run with a spinner/"waiting for the Windows permission prompt…"), **error** (validation, declined/failed/timed-out schedule, SFTP test failure — each a calm category card). The wizard **resumes from real state** (the first step not truthfully done, read from validated folders + a live schedule read-back + a keyring check) and **reconciles** against side effects already performed ("already scheduled — daily at HH:MM", "a delivery password is already saved") instead of double-registering.
- **What good feels like** — Certainty over celebration. No confetti (a trust instrument doesn't cheer): the finish card reads "Delivery configured", never "data was delivered". Skippable Schedule/Delivery mean the first success isn't gated on having a Windows password and a live SFTP credential in hand. Every error is fixed-category prose with an actionable hint, never the admin's raw input echoed back.

### Nightly scheduled sync

The unattended engine — the whole point of the product. Once registered, the district server runs the conversion every night and delivers the roster to SpacesEDU with no one watching.

- **Flow**
  1. The Setup wizard (or Settings) registers a scheduled task at the chosen time, running the app with `--source scheduled`.
  2. Each night the task runs the ETL pipeline: reads the GDE files, produces the CSVs, and (when delivery is configured) uploads them via SFTP.
  3. Output is written with **atomic, all-or-nothing** writes (staged then committed; a mid-run failure rolls back so the output folder is never left torn) and the **zero-orphan invariant** (no enrollment or homeroom class references a student absent from `Students.csv`).
  4. The run is recorded to the durable run store, which powers Home and Run History the next time the admin looks.
- **States** — not a UI surface; behavior is observed through exit codes and the run record. A delivery failure is a first-class outcome (exit 3, files still on disk), never a silent swallow.
- **Optional school-year window (opt-in).** The nightly task fires every night year-round, but if a seasonal window is configured (in the wizard/Settings), the app itself checks each night whether today is inside the district's school-year window and, if outside it (summer), does nothing and exits cleanly — no ETL, no delivery, no torn output. Because it's a date check, it recurs every year with nothing to renew, and the scheduled task never changes. Left off by default (year-round). The window governs only this automatic nightly run — a hand-run CLI, a headless cron, and manual Convert always run. A paused night is a healthy state, never a failure.
- **What good feels like** — It just works, and it fails *loud and safe*. SFTP delivery verifies the server's **identity** against pinned host keys bundled in `config/known_hosts` (zero setup — keys ship with the app; a per-district override in the app-data folder wins without a new release). A pinned-key mismatch hard-fails with a clear "server identity changed" message (the man-in-the-middle case) and is never retried; a transient network blip retries up to 3 times with backoff. Upload is restricted to the three known SpacesEDU hosts. Exit codes are a documented contract: **0** success · **1** ETL/validation error · **2** argument misuse · **3** SFTP delivery failed (ETL output still present). And when a school-year window is on, a partner sets it up **once and never touches it again** — the sync pauses over summer and resumes every fall on its own, giving the SIS time to update; the home screen reads a calm "Paused for the summer — resumes <date>" rather than a false "sync didn't arrive" alarm.
- **What "registered" does and does not promise** — a live schedule read-back confirms the task **exists and is enabled**; it does not and cannot confirm the task will **successfully log on** tonight. An unattended task runs under the admin's Windows account, so a routine district password rotation can leave a task that reads perfectly LIVE and silently stops running. This is the honest reading of the trust bar, and it is why the missed-run warning below is load-bearing rather than a nicety: **a registered schedule is evidence, an arrived run is proof.**

### Home health verdict

The Watcher's daily glance. One plain sentence answers *"is my roster syncing?"* before any number.

- **Flow**
  1. Open the app; Home derives a single verdict from the newest run record.
  2. Read the verdict: HEALTHY "Your roster is syncing" (with metric tiles — per-entity counts, the plain last-run time, an SFTP-delivered flag), or an amber/red WARNING/FAILED with a plain headline.
  3. If not healthy, follow the one fix button, which routes to the right place (Run History or Settings) and keeps the nav "you are here" highlight truthful.
- **States** — **loading** (a fast synchronous local read), **empty** (no runs yet → a calm "No sync has run yet" WARNING, never red, with the scheduled time if registered; or the fresh-store "run history starts fresh with this update" note), **error** (the never-crash `ErrorCard` floor — never a stack trace; a Refresh re-checks in place). Degraded and stale reads render as calm WARNINGs, not red.
- **What good feels like** — Calm and honest. "Your roster is syncing" is asserted only on a confirmed-LIVE schedule read-back; a local-only run reads "completed — files were written to your output folder"; "delivered to SpacesEDU" appears only when the upload actually succeeded. When a LIVE nightly task exists but no run arrived in the last ~26 hours (and a run was genuinely expected), Home says "We expected a nightly sync that didn't arrive" and routes to Run History. Faults are named by category only, never by echoing a raw error.

### Convert — run a sync now

The on-demand path: run a conversion immediately (a first proof-of-life, a mid-day re-sync, or manual delivery when the nightly path is down).

- **Flow**
  1. Pick a GDE input folder (an explicit district and a set output folder are required — no fallback).
  2. Review the read-only caption naming **where files will be written**.
  3. Run; a background worker builds the roster while the window stays responsive.
  4. Read the result — a verdict, per-entity tiles, a collapsible quality report, and the output folder with an "Open folder" button.
  5. Optionally deliver to SpacesEDU — which sends the files already on disk, never a rebuild.
- **States** — **empty** (no folder picked; "Choose your district" placeholder; Convert disabled until a district and output folder are set), **loading** (a spinner while the worker builds), **error** (a fixed "The conversion couldn't finish" card — the raw exception discarded, existing files explicitly unchanged; a fresh install leads with a routed "Finish setup first" card). Anomalies (>20% drops) gate delivery behind an explicit acknowledgment.
- **What good feels like** — Never freezes, never dead-ends, never silently writes to the wrong place. The output folder is always findable so manual delivery is possible when the nightly path is down. Delivery confirms with labelled Server / Folder facts and an honest "Files last built…" freshness line derived from the files on disk — and records honestly as "Delivered saved files", never pretending a delivery was a build.

### Run History

The Watcher's ledger and the Firefighter's timeline — a read-only record of what actually ran.

- **Flow**
  1. Open Run History.
  2. Read the verdict banner (the same latest-record answer as Home, so the two can never disagree).
  3. Scan the table of recent runs: a plain time, a category-only status, per-entity counts, an SFTP glyph, a warnings count, a plain duration, and a Source ("Nightly" / "Manual" / "Command line").
- **States** — **empty** (no runs yet, or a fresh store → a calm WARNING that says so honestly, never "no sync has run yet" when the store simply started clean at this update), **error** (the never-crash `ErrorCard` floor; an unreadable store renders a calm "history unavailable" WARNING, not red).
- **What good feels like** — Trustworthy and PII-free by construction. The table has **no raw error column** — the row shape has no `error` field, so a roster row or a stack trace cannot leak into it. A "Different district: …" note flags a run that belongs to a district other than the active one.

### Mapping review & switch

Review the active district configuration and switch to a different pre-built one — without editing YAML by hand.

- **Flow**
  1. Open Mapping; read the active district's friendly name and the plain-language list of output CSVs it produces.
  2. Pick a different pre-built config from the dropdown; review its summary.
  3. Apply (gated: enabled only when the target loads cleanly and differs from the current one).
- **States** — **error** (a broken partner-authored config renders as a calm degraded summary with Apply disabled — never a raw Pydantic/OS error; the raw `sis_type` appears only as a muted secondary hint).
- **What good feels like** — Honest about consequences. After switching, the confirmation says "Your folders are unchanged" and, when a nightly schedule exists, tells the admin the schedule still points at the old district until they open Settings and Save (which re-registers the task with the new district). It never claims the schedule silently followed the switch.

### Help

A calm off-ramp to answers and a human.

- **Flow**
  1. Open Help.
  2. Follow the link to the SpacesEDU Help Centre, or copy the support email.
  3. Read the About block (version, release notes, a prefilled PII-free support email).
- **States** — none required (static surface, no async fetch). Links and the support email are rendered as **selectable text**, so they still work even if the OS URL-launch no-ops. A never-crash `ErrorCard` floor still applies.
- **What good feels like** — Reassuring and always-legible. Copy reminds the admin their nightly sync keeps running in the background; nothing here can fail into a blank screen. The prefilled support email carries only the version and district display name — never a path or student data.

### Headless CLI & SFTP setup

The no-UI partner path — for a district that drives DistrictSync from a scheduler, a Docker container, or a server with no desktop.

- **Flow**
  1. Run the executable with `--sis <district> --input <folder> --output <folder>` to convert; add `--sftp` to deliver, `--dry-run` / `--diff` / `--quality` to preview or inspect.
  2. Configure delivery credentials without a browser: `--sftp-configure` (interactive or fully headless with `--sftp-host/--sftp-user/--sftp-remote`; password from the `DISTRICTSYNC_SFTP_PASSWORD` env var, `--sftp-password-stdin`, or a prompt).
  3. Verify with `--sftp-test`; inspect the saved (password-free) config with `--sftp-show`.
- **States** — not a UI surface; behavior is observed through exit codes and console output. Errors fail loud with an actionable message, never a silent swallow.
- **What good feels like** — Scriptable, secure, and predictable. The SFTP host is validated against the allowlist before anything runs; the password is stored only in the OS keyring, never on argv and never logged. The same exit-code contract as the nightly path holds, so a scheduler can branch on success/failure reliably.

## Acceptance criteria

The checkable projection of the Features above. All checks are `manual` — DistrictSync is a **native desktop app with no HTTP API and no browser surface**, so `e2e` and `api` do not apply; a person walks the desktop UI or runs a CLI command. (The pytest suite is the real automated backstop; these are the human-verifiable slice.)

```json
[
  {
    "id": "AC-setup-1",
    "feature": "First-run setup wizard",
    "flow": [
      "Launch a freshly installed DistrictSync desktop app",
      "Observe the Home surface",
      "Click 'Start setup'"
    ],
    "expect": [
      "Home shows a calm onboarding welcome with a 'Start setup' button (no dashboard, no metrics)",
      "clicking 'Start setup' opens the wizard at the District step (District leads, then Folders)",
      "a 'Step 1 of 5' style progress indicator is visible",
      "no district is pre-selected — a 'Choose your district' placeholder is shown unless exactly one config exists"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-setup-2",
    "feature": "First-run setup wizard",
    "flow": [
      "In the setup wizard, advance past District and reach the Folders step with no folder chosen",
      "Observe the Continue button",
      "Pick a valid input folder and a valid output folder",
      "Observe the Continue button again"
    ],
    "expect": [
      "Continue is disabled while the step's inputs are invalid",
      "Continue becomes enabled once the folders validate",
      "an invalid path never advances the wizard (Enter cannot bypass the gate)"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-setup-3",
    "feature": "First-run setup wizard",
    "flow": [
      "Progress through the wizard to the Schedule and Delivery steps",
      "Choose 'Set up later' on both",
      "Reach the finish step"
    ],
    "expect": [
      "both Schedule and Delivery offer a 'Set up later' option",
      "the finish screen shows a checked-summary card marking each step configured or deferred with its concrete value",
      "the finish copy names what was checked and when (no future guarantee, no confetti)",
      "the install is now marked set up and Home shows the verdict dashboard on next open"
    ],
    "states": ["loading"],
    "check": "manual"
  },
  {
    "id": "AC-setup-4",
    "feature": "First-run setup wizard",
    "flow": [
      "On an install where a nightly schedule was already registered, reopen the setup wizard",
      "Advance to the Schedule step"
    ],
    "expect": [
      "the wizard resumes at the first step not truthfully done, not step 1",
      "the Schedule step reconciles against reality (e.g. 'already scheduled — daily at HH:MM') rather than offering to register a duplicate task"
    ],
    "states": ["loading", "error"],
    "check": "manual"
  },
  {
    "id": "AC-nightly-1",
    "feature": "Nightly scheduled sync",
    "flow": [
      "Complete the Schedule step (or Settings) with a nightly run time and approve the Windows permission prompt",
      "Reopen Setup/Settings and read the schedule status"
    ],
    "expect": [
      "a Windows scheduled task is registered that runs the app with '--source scheduled'",
      "the schedule read-back confirms the task is live with the chosen next run time in plain language",
      "the app itself did not require running as administrator (only the one-time elevation prompt)"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-nightly-2",
    "feature": "Nightly scheduled sync",
    "flow": [
      "Run the pipeline for a configured district with valid GDE input and SFTP delivery enabled",
      "Inspect the output folder and the process exit code"
    ],
    "expect": [
      "the 5 rostering CSVs (Students, Staff, Family, Classes, Enrollments) exist in the output folder",
      "the output CSVs were delivered to SpacesEDU as a dated zip via SFTP",
      "the process exits with code 0"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-nightly-3",
    "feature": "Nightly scheduled sync",
    "flow": [
      "Run the pipeline with '--sftp' where SFTP delivery cannot succeed",
      "Inspect the exit code and the output folder"
    ],
    "expect": [
      "the process exits with code 3",
      "the ETL output CSVs are still present on disk (not rolled back)",
      "the failure is logged as a delivery failure, not a crash"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-nightly-4",
    "feature": "Nightly scheduled sync",
    "flow": [
      "Attempt SFTP delivery to a host whose SSH host key does not match the pinned key in config/known_hosts",
      "Observe the outcome"
    ],
    "expect": [
      "delivery hard-fails with a plain 'server identity changed' category message (host name only, no paths or credentials)",
      "the mismatch is never retried",
      "a host with no pinned key connects as before, with a log warning pointing at the pinning file"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-nightly-5",
    "feature": "Nightly scheduled sync",
    "flow": [
      "Enable a school-year window in the wizard/Settings whose season does not include today",
      "Let the scheduled nightly task fire (or run the app with --source scheduled)",
      "Inspect the output folder and the exit code"
    ],
    "expect": [
      "the run does no ETL and no delivery, and exits 0 (a paused night is healthy, not a failure)",
      "the output folder is unchanged (the previous run's files are neither overwritten nor archived)",
      "with the window disabled, or today inside the window, the run proceeds and delivers exactly as before"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-nightly-6",
    "feature": "Nightly scheduled sync",
    "flow": [
      "With a school-year window enabled and today outside it, open the app",
      "Read Home and the Setup nav-rail badge"
    ],
    "expect": [
      "Home shows a calm 'Paused for the summer — resumes <date>' state (green, not amber/red)",
      "the missed-run and stale warnings do NOT fire while paused, and the Setup badge is not lit by the fired-but-no-record contradiction",
      "a genuinely failed last run, or a schedule the OS confirms is gone, still surfaces despite the pause"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-home-1",
    "feature": "Home health verdict",
    "flow": [
      "On a configured install with a recent successful run, open the app",
      "Read the Home surface top to bottom"
    ],
    "expect": [
      "Home leads with a single HEALTHY verdict and a plain headline before any numbers",
      "metric tiles show per-entity output counts and a plain last-run time",
      "'delivered to SpacesEDU' appears only if the upload actually succeeded"
    ],
    "states": ["loading", "empty", "error"],
    "check": "manual"
  },
  {
    "id": "AC-home-2",
    "feature": "Home health verdict",
    "flow": [
      "On a configured install with no runs recorded yet, open the app",
      "Read the Home verdict"
    ],
    "expect": [
      "Home shows a calm WARNING (amber), never red",
      "the copy reads that no sync has run yet, with the scheduled nightly time if one is registered",
      "no stack trace or raw error is shown"
    ],
    "states": ["empty"],
    "check": "manual"
  },
  {
    "id": "AC-home-3",
    "feature": "Home health verdict",
    "flow": [
      "On an install whose most recent run failed or dropped a delivery, open the app",
      "Read the verdict and click the fix button"
    ],
    "expect": [
      "the fault is named by category (e.g. didn't reach SpacesEDU / ETL failed), never a raw error string",
      "a single fix button routes to the right surface (Run History or Settings)",
      "the nav 'you are here' highlight follows the route so orientation is never lost"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-home-4",
    "feature": "Home health verdict",
    "flow": [
      "On an install with a confirmed-live nightly task but no run recorded in the last ~26 hours, open the app"
    ],
    "expect": [
      "Home shows an amber warning reading that an expected nightly sync didn't arrive",
      "a route to Run History is offered",
      "a day-one install (store too new for a run to be expected) is not falsely warned"
    ],
    "states": ["empty"],
    "check": "manual"
  },
  {
    "id": "AC-convert-1",
    "feature": "Convert — run a sync now",
    "flow": [
      "Open Convert, pick a valid GDE input folder with a district and output folder set",
      "Run the conversion",
      "Read the result"
    ],
    "expect": [
      "the window stays responsive with a spinner while the roster builds",
      "the result shows a verdict, per-entity tiles, and the resolved output folder with an 'Open folder' button",
      "no student data or raw path appears in any error or headline"
    ],
    "states": ["empty", "loading", "error"],
    "check": "manual"
  },
  {
    "id": "AC-convert-2",
    "feature": "Convert — run a sync now",
    "flow": [
      "Open Convert with no district chosen and/or no output folder set",
      "Attempt to run"
    ],
    "expect": [
      "Convert is disabled with a 'Choose your district' placeholder when no district is chosen",
      "when no output folder is set, Convert is blocked with a routed 'Set your output folder in Settings first' (never a silent write into the input folder)"
    ],
    "states": ["empty"],
    "check": "manual"
  },
  {
    "id": "AC-convert-3",
    "feature": "Convert — run a sync now",
    "flow": [
      "Run a conversion whose output is more than 20% smaller than the previous run",
      "Attempt to deliver to SpacesEDU"
    ],
    "expect": [
      "a WARNING flags that some files look much smaller than usual",
      "delivery is gated behind an explicit acknowledgment before it can proceed"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-convert-4",
    "feature": "Convert — run a sync now",
    "flow": [
      "After a successful build, use the 'Deliver to SpacesEDU' action",
      "Read the deliver confirmation"
    ],
    "expect": [
      "the files already on disk are uploaded (the conversion is not re-run)",
      "the confirmation shows labelled Server and Folder facts plus a 'Files last built …' freshness line",
      "the delivery records in Run History as a delivery, not as a build"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-history-1",
    "feature": "Run History",
    "flow": [
      "On an install with several recorded runs, open Run History",
      "Read the banner and the table"
    ],
    "expect": [
      "a verdict banner matches Home's answer over the same latest record",
      "each row shows a plain time, a category-only status, per-entity counts, an SFTP glyph, and a Source (Nightly / Manual / Command line)",
      "no raw error, path, or student data appears in any row"
    ],
    "states": ["empty", "error"],
    "check": "manual"
  },
  {
    "id": "AC-history-2",
    "feature": "Run History",
    "flow": [
      "On an install whose run store is fresh (no records), open Run History"
    ],
    "expect": [
      "the empty state honestly says run history starts fresh with this update (not 'no sync has run yet' when the store simply started clean)",
      "the surface is a calm WARNING, never red, and never a stack trace"
    ],
    "states": ["empty"],
    "check": "manual"
  },
  {
    "id": "AC-history-3",
    "feature": "Run History",
    "flow": [
      "Open Run History on an install that has recorded a failed run",
      "Inspect every column of the table"
    ],
    "expect": [
      "there is no raw error column and no file-path column",
      "a failed run reads as a category-only status label",
      "a run from a non-active district shows a plain 'Different district: …' note"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-mapping-1",
    "feature": "Mapping review & switch",
    "flow": [
      "Open Mapping",
      "Read the active configuration card",
      "Pick a different pre-built config and Apply"
    ],
    "expect": [
      "the active district shows a friendly name and a plain-language list of the output CSVs it produces",
      "Apply is enabled only when the chosen config loads cleanly and differs from the current one",
      "the raw sis_type appears only as a muted secondary hint, never as the primary label"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-mapping-2",
    "feature": "Mapping review & switch",
    "flow": [
      "Open Mapping where a broken/partner-authored config is present",
      "Select it"
    ],
    "expect": [
      "the broken config renders as a calm degraded summary, not a raw Pydantic or OS error",
      "Apply stays disabled when there is nothing valid to switch to"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-mapping-3",
    "feature": "Mapping review & switch",
    "flow": [
      "With a nightly schedule registered, switch districts in Mapping and Apply",
      "Read the post-Apply confirmation"
    ],
    "expect": [
      "the confirmation says the folders are unchanged (not 'folders and schedule are unchanged')",
      "it tells the admin the nightly schedule still points at the old district until Settings is saved to re-register the task"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-help-1",
    "feature": "Help",
    "flow": [
      "Open Help",
      "Read the surface and try the Help Centre link and the support email"
    ],
    "expect": [
      "a link to the SpacesEDU Help Centre and a support email are shown",
      "the link and email are rendered as selectable text so they remain usable even if the OS URL launch no-ops",
      "reassurance copy states the nightly sync keeps running in the background"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-help-2",
    "feature": "Help",
    "flow": [
      "Open Help and read the About block",
      "Use the prefilled support-email action"
    ],
    "expect": [
      "the About block shows the app version and a release-notes link",
      "the prefilled support email carries only the version and district display name — no file paths and no student data"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-cli-1",
    "feature": "Headless CLI & SFTP setup",
    "flow": [
      "Run the executable with '--sis <district> --input <folder> --output <folder>'",
      "Inspect the output folder and exit code"
    ],
    "expect": [
      "the conversion runs without opening the desktop UI",
      "the expected CSVs are written to the output folder",
      "the process exits with code 0 on success"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-cli-2",
    "feature": "Headless CLI & SFTP setup",
    "flow": [
      "Run '--sftp-configure' headless with host/user/remote and the password supplied via env var or stdin",
      "Repeat with a host not on the allowlist"
    ],
    "expect": [
      "an allowlisted host is accepted and the credential is stored in the OS keyring (never on argv, never logged)",
      "a host outside the allowlist is rejected with an actionable error and nothing is stored"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-cli-3",
    "feature": "Headless CLI & SFTP setup",
    "flow": [
      "Run '--sftp-test' with a stored credential",
      "Run '--sftp-show'"
    ],
    "expect": [
      "'--sftp-test' reports whether the stored credentials connect, without writing anything",
      "'--sftp-show' prints the saved non-sensitive config and never prints the password"
    ],
    "states": [],
    "check": "manual"
  }
]
```

## What's deliberately out (today)

Listed so a gap review never flags these as missing — they are decided scope boundaries, not gaps.

- **The full column-mapping editor.** Mapping reviews and switches between *pre-built* district configs; authoring a brand-new column mapping in-app (the visual field-mapping editor) is scope-locked to a later epic on the ROADMAP. New configs are added by hand in `config/mappings/`.
- **Bundled offline docs in Help.** Help links out to the SpacesEDU Help Centre rather than rendering the bundled `docs/` markdown in-app.
- **Management / multi-district (fleet) views.** DistrictSync is a single-district admin's cockpit. Aggregate or fleet-management views are out of scope.
- **Email / push alerting on failed or missed runs.** The product surfaces run health *inside the app* (Home verdict, missed-run warning, Run History). Proactive out-of-band alerting is owner-deferred (2026-07-15), not a current promise. **The deliberate mitigation, stated:** an admin who opens the app 2–3 times a year is not the only line of defence — the documented **exit-code contract** makes every run machine-readable by the district's own scheduler or monitoring (a non-zero exit is theirs to alert on), and a roster that stops arriving is visible to SpacesEDU ops from the other end. This is a *pull* cockpit by choice; the boundary is named, not unacknowledged.
- **Graduate-transcript and alpha-marks handling for the myBlueprint+ course tiers.** The myBlueprint+ `CourseInfo` / `StudentCourses` outputs ship for senior-course data as configured; graduate-transcript and alpha-mark edge cases are owner-adjudicated with field data, not a committed behavior today.

<!-- product-critic:rejected-proposals -->
<!--
User-owned memory of product proposals already decided against, so a future Product Excellence
pass recognizes and skips them. One terse line each; never stamped; never auto-edited.

- Academic-year pin staleness signal ("your district's year is pinned to <year>") — WITHDRAWN as
  factually wrong: all 11 configs resolve use_academic_year: true; no config pins fixed dates.
- Removing Mapping from the nav rail (kill-test result) — rail order is deliberately frozen for
  spatial memory; raised as an observation, not a recommendation.
-->

_Proposals from the 2026-07-21 Product Excellence pass that are **owner decisions, not spec fixes**, are recorded in [`claugentic-ROADMAP.md`](claugentic-ROADMAP.md) (prove-it-now run at the wizard finish; input-folder GDE preflight; code-signing; an update-available signal). They are **not** picked up automatically by any build step — they enter when the owner names one._
