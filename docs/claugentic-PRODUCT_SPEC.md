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

- **Getting the app onto the server.** The **Windows** executable is code-signed as `myBlueprint Corp.` (v3.16.0+), so Windows names the publisher rather than warning about an unknown one; **macOS and Linux are still unsigned**. A signed build can still meet a SmartScreen warning until that specific file accumulates download reputation, and the app still unpacks an unsigned helper into the user profile on first launch, so a behaviour-based antivirus can still object. Getting past either is covered by the partner installation guide and district IT, not by the app.
- **Getting GDE files into the input folder.** DistrictSync **reads** the MyEducation BC extract files; it does not produce them. The district must already have its MyEdBC GDE export scheduled to drop files into the input folder on its own cadence. The wizard validates that the input folder *is a usable folder* — it does not verify that tonight's extract will land there.

Naming these is the trust bar applied to the product's own edges: a tool that says "you're set up" should not be read as a claim about links it never checked.

## Features

Each feature below carries a **Flow** (the happy path), **States** (only which of loading/empty/error this surface has — the *bar* for those states is the standard [`docs/claugentic-standards/product-ux.md`](claugentic-standards/product-ux.md) → *Loading / empty / error states* and *User-flow completeness*, not restated here), and **What good feels like**. The pytest suite (~1,686 tests, SD74 golden-file snapshot, config validation, 80% coverage gate) is the real automated gate behind these; the acceptance criteria are the plain-English projection a person can check.

### Launch identity — "who looks after this sync?"

The first thing a fresh install shows: one question, one field, and a dozen ways past it.
It exists because the highest-consequence wrong click in this product is picking the wrong
district — a wrong mapping ships a wrong roster — so knowing which district an admin belongs
to lets every picker lead with theirs.

**It is identification, never authentication.** There are no accounts, nothing is unlocked,
and every district mapping ships inside the executable no matter what is typed. The copy
never says sign in / log in / verify / unlock / authorized / account / credentials / access,
and the district-domain list is never described as protected, secured, anonymous or
encrypted — it is a list of PUBLIC district domains used to shorten a picker.

- **Flow**
  1. Launch a fresh install. The launch page asks for the work email of the person who looks
     after the sync. Continue is disabled until something is typed.
  2. Press Continue. The address is matched — locally, instantly — on the part after the `@`
     against the district staff domains carried in the bundled mappings. Nothing leaves the
     machine.
  3. **Matched one district** → it is named as a *pre-selection you can correct*: "That's
     SD48 – Sea to Sky School District — you'll confirm it on the next step", beside "That's
     not my district".
     **Matched several** (one district with two setups — SD51 and its attendance tier) →
     "Your district has more than one setup", with both named.
     **No match** → calm, not an error: "We don't have a district on file for that address
     yet — no problem", plus an optional district-number box and "My district isn't listed
     yet".
  4. Press Get started. The answer is saved to this computer, then the app opens.
- **States** — **error** (an invalid address format, shown on leaving the field or pressing
  Continue, never mid-keystroke, and never echoing the value back). There is no loading
  state and no empty state: resolution is a local lookup, so a spinner would imply a server
  that does not exist.
- **Deliberately absent** — no lockout, no attempt counter, no artificial delay, no lock or
  shield glyph, no password field, no network/"connecting…" state. Each absence is the
  register: this is a question, not a door.
- **Every path leads INTO the app** — a match, no match, a typo, "That's not my district",
  "I'm not the person who looks after this sync", a settings file we cannot write, or a
  crash in the identity layer itself. A failure to save still opens the app; the question is
  simply asked again next launch.
- **Changeable and clearable, always** — the answer lives in Settings → "Who looks after this
  sync" (shown plainly, editable, and cleared by saving a blank field), and is echoed
  read-only on Help. Clearing removes it from the settings file *and* from the older
  settings copies that held it, and says which of those actually happened.
- **What good feels like** — Being recognised, not challenged. The page reads like a
  receptionist asking who you're here to see, not a guard asking for ID. Nobody is ever
  stuck on it: the person at the console who is not the admin has a one-click way past that
  stores nothing, and a mistyped address has a one-click way back to the field.
- **Upgrade in place** — an install that has already finished setup NEVER sees this page. It
  keeps booting straight to its dashboard; the same question arrives later as a dismissible
  card on Home (S4b), which changes no setting and never interrupts the nightly sync. An
  install whose settings file cannot be read is never asked at all — we could not record the
  answer, so asking would be a question we would silently drop.

### First-run setup wizard

The Installer's single guided path from a fresh download to a verified nightly sync.

- **Flow**
  1. Open the app for the first time. The launch page asks who looks after this sync (see
     **Launch identity** above); past it, **Home IS the wizard** — one calm welcome line and
     step 1 right there. There is no hero and no "Start setup" button pointing at another
     part of the app: the front door opens onto the work. The welcome line knows the
     difference between a brand-new install and one that has been running (an install with
     run history reads "Let's finish setting up — your files and run history are safe",
     never "Welcome").
  2. **District** — choose the district config from a "Choose your district" picker (auto-selected only when exactly one config exists — never a silent default). District leads: *pick who you are first, then where your files live.*
  3. **Folders** — pick the GDE input folder and the output folder.
  4. **Delivery** — enter and test the SFTP credential, or "Set up later". Delivery precedes Schedule so the delivery setting is already baked in when the task is registered.
  5. **Schedule** — pick a nightly run time and register the Windows task (a one-time UAC permission prompt), or "Set up later".
  6. **Finish** — an honest, adaptive summary names what was checked and when, plus a checked-summary card listing each step as configured (✓) or deferred. Reaching this finish line — not any single step — is the only thing that marks the install "set up". The summary stays on screen until the admin presses Finish setup; from Home that press lands them on Home's health view, and from the Setup tab it turns the page into Settings in place.
- **States** — **loading** (schedule registration and SFTP test run with a spinner/"waiting for the Windows permission prompt…"), **error** (validation, declined/failed/timed-out schedule, SFTP test failure — each a calm category card; and if the finish line itself cannot be saved, the summary stays put with "we couldn't save your settings just now — nothing was lost", never a silent return to step 1). The wizard **resumes from real state** (the first step not truthfully done, read from validated folders + a live schedule read-back + a keyring check) and **reconciles** against side effects already performed ("already scheduled — daily at HH:MM", "a delivery password is already saved") instead of double-registering.
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
  2. Read the verdict: HEALTHY "Your roster is syncing", whose detail carries the last-sync phrasing plus ONE roster-size number ("It included 4,812 students") — or an amber/red WARNING/FAILED with a plain headline. There are no metric tiles: the tile row retired at 0038 S7. Per-entity counts live in Run History for the rostering and myBlueprint+ entities; an attendance district's row count reaches exactly ONE place — this size sentence — since that table's columns exclude `StudentAttendance`, and it is shown only on the healthy verdict. An OPEN gap in `claugentic-ROADMAP.md`.
  3. If not healthy, follow the one fix button, which routes to the right place (Run History or Settings) and keeps the nav "you are here" highlight truthful.
  4. Below the verdict block sits a quick-action strip — the few places you actually go next (Convert / Run History / Settings), minus whichever one the fix button already offers.
- **States** — **loading** (a fast synchronous local read), **empty** (no runs yet → a calm amber WARNING, never red: an install whose run store has never been created reads "No runs recorded yet" — a claim about the ledger, since a pre-v3.5.0 upgrader has no store either — naming the nightly time only when the schedule read-back CONFIRMS one (a merely-registered task whose read-back is unprobed or UNKNOWN names no time) and no mention of a nightly at all when nothing confirms or records one; an install whose store predates this update reads "Run history starts fresh here"), **error** (the never-crash `ErrorCard` floor — never a stack trace; a Refresh re-checks in place). Degraded and stale reads render as calm WARNINGs, not red.
- **What good feels like** — Calm and honest. "Your roster is syncing" is asserted only on a confirmed-LIVE schedule read-back; a local-only run reads "completed — files were written to your output folder"; "delivered to SpacesEDU" appears only when the upload actually succeeded. The roster-size number is the one sanity check the verdict alone cannot give (a sync that quietly shrank to 12 students is "delivered" by every structured field on the record); it names the entity the config actually produces, and disappears rather than guessing — including when the run it would describe came from a district other than the one saved now. When a LIVE nightly task exists but no run arrived in the last ~26 hours (and a run was genuinely expected), Home says "We expected a nightly sync that didn't arrive" and routes to Run History. Faults are named by category only, never by echoing a raw error.

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
    "id": "AC-identity-1",
    "feature": "Launch identity",
    "flow": [
      "Point DISTRICTSYNC_DATA_DIR at an empty folder and launch the app",
      "Observe the first surface",
      "Type any local part at a real district staff domain (sd48.bc.ca) and press Continue",
      "Press Get started, then close and relaunch"
    ],
    "expect": [
      "the launch page opens first — 'Who looks after this sync?' — with no navigation rail and no Exit button",
      "Continue is disabled until something is typed",
      "the result names SD48 - Sea to Sky School District and offers 'That's not my district' beside it",
      "no text on the page says sign in, log in, verify, unlock, authorized, account, credentials or access",
      "no password field, no lock or shield icon, no spinner, no attempt counter appears in any state",
      "after Get started the app opens, and the relaunch goes straight to the app — the question is not repeated",
      "Settings > 'Who looks after this sync' shows the address that was typed"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-identity-2",
    "feature": "Launch identity",
    "flow": [
      "On a cleared scratch profile, type 'admin' (no @) and click away from the field",
      "Correct it to any address at example.com and press Continue",
      "Type 99 in the district-number box and click away",
      "Press 'My district isn't listed yet'"
    ],
    "expect": [
      "the format error appears only AFTER leaving the field, never while typing, and never quotes what was typed",
      "the no-match state reads calm and grey ('no problem'), never red, and never says we don't have that ADDRESS on file",
      "SD99 reports no mapping yet and still offers a way forward",
      "the not-listed note says we'll need to build a mapping and points at Help — it never suggests choosing the closest district",
      "'That's not my address - try again' returns to the email field with the typed value intact"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-identity-3",
    "feature": "Launch identity",
    "flow": [
      "On a cleared scratch profile, click 'I'm not the person who looks after this sync'",
      "Open config.json in the profile folder",
      "Close the app and relaunch"
    ],
    "expect": [
      "the app opens immediately",
      "config.json carries no identity value at all",
      "the launch page asks again on the next launch"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-identity-4",
    "feature": "Launch identity",
    "flow": [
      "With an address on file, open Settings > 'Who looks after this sync' and press Change",
      "Enter a different district's address and Save",
      "Press Change again, blank the field, and Save",
      "Inspect the profile folder"
    ],
    "expect": [
      "the change names the new district inline and is written to config.json",
      "the blank Save empties identity_email and identity_sd_number and resets identity_prompt_dismissed to false",
      "the note reports exactly what happened to the older config.corrupt-*.json copies — that they were removed, or how many could not be, and claims nothing when there were none",
      "changing or clearing WHO never changes WHICH district the sync converts (sis_type is untouched)"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-identity-5",
    "feature": "Launch identity",
    "flow": [
      "Restore a v3.8.x profile (a completed install: folders, district, registered schedule) and launch",
      "Separately, truncate config.json mid-object and launch again"
    ],
    "expect": [
      "the completed install boots straight to its dashboard — the launch page never appears",
      "the unreadable profile also never sees the launch page (we could not save an answer, so we do not ask)",
      "neither launch changes any existing setting, and the nightly schedule is untouched"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-identity-6",
    "feature": "Launch identity",
    "flow": [
      "On a cleared scratch profile, answer the launch page with an address at sd48.bc.ca and press Get started",
      "Look at the wizard's District dropdown, then finish setup and open Settings > Folders & district, Convert, and Mapping",
      "Blank the address in Settings > Who looks after this sync and press Save, then reopen those three surfaces",
      "Separately: set the district to sd74myedbc, restore the sd48.bc.ca address, and reopen those three surfaces"
    ],
    "expect": [
      "every district list shows only SD48 - Sea to Sky School District, and the District step opens with it already selected",
      "the District step is still landed on and the selection is still changeable",
      "NO surface offers a 'Show all districts' row (retired 2026-08-04) — the scoping has no per-screen escape",
      "with the address cleared, every one of those lists shows all eleven again (the escape is at the input)",
      "with a different saved district, every list carries BOTH the matched district and the saved one",
      "an unmatched address, no address, or an unreadable settings file all show the full list of eleven"
    ],
    "states": ["empty"],
    "check": "manual"
  },
  {
    "id": "AC-convert-run-district",
    "feature": "Convert",
    "flow": [
      "On a set-up install, open Convert and note the district chip in the header",
      "Change the district dropdown to a different district (if your list is scoped to one, blank the address in Settings > Who looks after this sync first)",
      "Press 'Change mapping'",
      "Return to Convert and set the dropdown back to the saved district"
    ],
    "expect": [
      "an amber 'This run: <the district just picked>' pill appears beside the header chip, naming the PICKED district and never the saved one",
      "the header chip continues to show the SAVED district — the two are different facts and both are visible",
      "'Change mapping' opens the Mapping surface and changes no setting by itself",
      "Convert stays runnable the whole time — the pill is a label, never a gate",
      "setting the district back to the saved one makes the pill disappear"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-setup-1",
    "feature": "First-run setup wizard",
    "flow": [
      "Launch a freshly installed DistrictSync desktop app",
      "Get past the launch identity page (answer it or skip it)",
      "Observe the Home surface",
      "Click the Setup rail item, then click Home again"
    ],
    "expect": [
      "the launch identity page precedes Home on a fresh install (see AC-identity-1)",
      "Home IS the setup wizard — one calm welcome line above the District step, no dashboard, no metrics, and no 'Start setup' button pointing anywhere else",
      "the rail's highlight is on Home, and the Setup item carries no attention badge",
      "a 'Step 1 of 5' style progress indicator is visible (District leads, then Folders)",
      "no district is pre-selected — a 'Choose your district' placeholder is shown unless exactly one option is VISIBLE (which a matched identity can make true; see AC-identity-6)",
      "the Setup rail item shows the same wizard at the same step — one wizard, two ways in"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-setup-5",
    "feature": "First-run setup wizard",
    "flow": [
      "On a scratch profile that has RUN before but never finished the wizard (run history present, setup_completed false AND schedule_registered false — has_completed_setup() re-infers the flag on load from a registered schedule), open the app",
      "Read the line above the wizard",
      "Separately, on a fresh scratch profile, read the same line",
      "Separately again, on a profile with a district and folders saved but NO runs (walk the wizard to Delivery and close), read the same line"
    ],
    "expect": [
      "the install with history reads \"Let's finish setting up — your files and run history are safe.\"",
      "the word 'Welcome' appears nowhere on an install that has run before",
      "the fresh profile reads \"Welcome — this takes about 3 minutes.\"",
      "the saved-choices-but-no-runs profile reads \"Let's finish setting up — everything you've already entered is safe.\" — it must NOT say 'run history', which this install does not have; the same line is used when saved choices sit beside a run store that cannot be read",
      "a profile with NOTHING entered beside an unreadable run store reads the bare \"Let's finish setting up.\" — it may name neither the run history nor the settings, because it has been checked to have neither",
      "no variant names a step count — the wizard's own 'Step 1 of 5' indicator owns that",
      "none of the lines is a coloured hero — each is one quiet sentence above the step",
      "the Setup rail item shows the same wizard with NO welcome line — the band belongs to the landing, not to the wizard"
    ],
    "states": [],
    "check": "manual"
  },
  {
    "id": "AC-setup-6",
    "feature": "First-run setup wizard",
    "flow": [
      "Reach the wizard's finish step on a scratch profile",
      "Make the profile folder read-only (or otherwise unwritable), then press 'Finish setup'",
      "Restore write access and press 'Finish setup' again"
    ],
    "expect": [
      "the failed save keeps the finish summary exactly where it is and adds 'We couldn't save your settings just now — nothing was lost. Please try again.'",
      "it does NOT return to step 1, and it does not open Settings or the Home dashboard",
      "the retry succeeds, the note disappears, and Home shows its health view"
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
      "a host with no pinned key (or a missing/unreadable pin file) is refused fail-closed — delivery aborts with a distinct 'identity could not be verified' message pointing at restoring the pinned known_hosts, never accepted with just a warning"
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
      "there is no metric-tile row and no 'Latest roster' label anywhere on the surface",
      "the verdict's detail carries exactly ONE roster-size number, opening 'It included ' and naming an entity the config that produced THAT RUN actually emits",
      "'delivered to SpacesEDU' appears only if the upload actually succeeded"
    ],
    "states": ["loading", "empty", "error"],
    "check": "manual"
  },
  {
    "id": "AC-home-1b",
    "feature": "Home health verdict",
    "flow": [
      "On an attendance-only or myBlueprint+-only district (e.g. sd51attendance, mbponly) with a recent successful run, open the app",
      "Read the healthy verdict's detail line"
    ],
    "expect": [
      "the size number names attendance rows / courses, whichever config produced THE RUN being described",
      "it never reads '0 students' — the entity is chosen from the district's own config, never from the run record's zero-filled count keys",
      "when the run on record was produced by a DIFFERENT district than the one saved now (a mapping switch, or a Convert run against a district that was never saved), the size sentence is absent — the counts and the entity list must come from the same district or no number is printed",
      "on a district whose config cannot be read at all, the size sentence is simply absent (never a guessed number)"
    ],
    "states": ["empty"],
    "check": "manual"
  },
  {
    "id": "AC-home-2",
    "feature": "Home health verdict",
    "flow": [
      "On a configured install whose run store has never recorded anything (a brand-new install, including one that has JUST finished the setup wizard), open the app",
      "Read the Home verdict"
    ],
    "expect": [
      "Home shows a calm WARNING (amber), never red",
      "the headline reads 'No runs recorded yet' — a claim about the ledger, not about the world: an install upgrading from v3.4.0 or earlier has no run store either (history.db shipped in v3.5.0 and there is no backfill), and it may have been syncing nightly for months",
      "the detail names the nightly time ONLY when the schedule read-back CONFIRMS a live task — a task this install merely recorded registering, whose read-back has not returned yet or came back UNKNOWN, names no time (it says the nightly sync will appear here, which is all that is known)",
      "it mentions no nightly sync at all when nothing confirms OR records one — an admin who skipped the Schedule step is never told about automation they declined",
      "no sentence calls the coming sync the FIRST one: an install upgrading from v3.4.0 or earlier has months of nightly syncs behind it and still arrives with no run store",
      "it never says anything about 'an earlier version' — that sentence belongs only to an install that HAS a run store, which then reads 'Run history starts fresh here'; a store stamp is evidence a run was once recorded, so its absence cannot be read as proof of a first run",
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
      "exactly one FILLED button exists on the screen and it is the fix; the quick-action strip below is outlined and drops whichever destination the fix already offers",
      "the fix routes to the right surface (Run History or Settings)",
      "the nav 'you are here' highlight follows the route so orientation is never lost"
    ],
    "states": ["error"],
    "check": "manual"
  },
  {
    "id": "AC-home-5",
    "feature": "Home health verdict",
    "flow": [
      "Open Home on a healthy install, then on a faulted one",
      "Count the filled (solid blue) buttons on each"
    ],
    "expect": [
      "exactly one filled button in BOTH states — 'Convert now' when there is nothing to fix, the fix CTA when there is",
      "every other action on the surface is outlined or text-tier, including the identity card's Save",
      "the same three destinations are reachable in both states (Convert / Run History / Settings)"
    ],
    "states": [],
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
