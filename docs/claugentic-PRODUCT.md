<!-- claugentic-dev-harness@0.3.0 -->
# DistrictSync — Product spec (narrative)

> The durable, human-readable product spec for the DistrictSync desktop cockpit (the Flet 1.0
> rebuild, IA-1..IA-9). The per-surface sections describe what LANDED; the personas, the three
> journeys, the trust bar, and the resolved 0029 direction describe the durable product truth the
> cockpit serves and the approved direction it is evolving toward. It is distinct from any
> machine-readable acceptance-criteria spec produced by `/product` — this is the prose the whole
> rebuild has been serving, kept honest so the next agent inherits the intent, not just the code.

## Who it's for

A **non-technical BC school-district administrator**. They are not an engineer. They open
DistrictSync perhaps **2–3 times a year** — once to set it up, then occasionally to check it's still
working or to reconfigure after a change. Between those visits, the tool runs unattended every night
on a district server via the Windows Task Scheduler.

Because the audience is non-technical and the tool handles **student PII (FERPA-adjacent)**, one rule
is absolute: the admin is **never** shown a machine identifier, a filesystem path, a raw config id
(`sd48myedbc`), a raw ISO timestamp, a raw exception string, or a stack trace. Every surface speaks in
plain, calm, human language.

### The three hats — one admin, three moments

That one admin wears **three hats at three different moments**, and the cockpit has to serve all
three. (They are the same person, not three user types — but the job, the mood, and the stakes are
different each time, so the surfaces are reviewed against all three.)

- **The Installer (first run, ~once).** Job: *"Get this configured and prove tonight's sync will
  actually reach SpacesEDU, then walk away."* Wants a linear, verifiable path — folders → district →
  schedule → delivery → a checked "you're done, here's what happens tonight" — and cares about
  certainty, not features.
- **The Watcher (steady state, ~95% of the product's life).** Job: *"Tell me in one glance that last
  night's roster reached SpacesEDU — and if not, what to do."* Glances in occasionally, or after an
  email. Wants Home to be a trustworthy verdict and Run History to be a clean, district-scoped ledger.
- **The Firefighter (incident, rare + high-stress).** Job: *"Something's wrong — SFTP failed, the
  roster dropped, the schedule didn't fire — show me what, and the shortest fix."* Wants honest
  fault-naming and a routed fix path that lands where it says and keeps "you are here" truthful.

## The job-to-be-done

> *"When my district's roster needs to reach SpacesEDU, I want the nightly sync to just work — and
> when I check in, I want to trust it ran and delivered, in words I understand."*

## The promise

A **verdict-first cockpit**. Open the window and immediately know the one thing that matters: *is my
roster syncing?* The answer is a single health verdict (HEALTHY / WARNING / FAILED) with a
plain-language headline, before any detail or numbers. The nightly sync runs on its own; setup is a
calm, one-scroll flow; nothing ever dead-ends in jargon.

## The trust bar

DistrictSync is a **trust instrument**, not a feature console. Its entire value is the admin
*believing* the nightly sync works without watching it — and trust instruments die on unverified
assertions. So one rule governs every surface:

> **Never assert a state you didn't check. Every success names WHAT it checked and WHEN** — "we tested
> the connection to <host> as <user> just now and it worked", "the task is registered — next run
> tonight at HH:MM" — never a promise about the future, never a self-reported boolean standing in for
> a real check.

When a fact can't be checked right now (a schedule the OS won't report, a credential stored under
another account), the honest answer is *"we couldn't confirm this right now"* — never a green
borrowed from stale config.

## The design language

- **Verdict-first spine.** Every trust surface leads with a HEALTHY / WARNING / FAILED verdict (green
  / amber / red) and a plain headline. Detail and metrics come after, never before.
- **Plain language everywhere.** District names are humanized (`friendly_district_name`), never a raw
  config id. Times are relative phrases ("5 hours ago", "yesterday at 3:00 AM"), never a raw ISO
  (`friendly_timestamp`). Counts are safe scalars, never identifiers.
- **Category-only faults.** A failure is named by its *category* (ETL failed / didn't reach SpacesEDU
  / anomaly / data warnings / SFTP couldn't connect), never by echoing a raw error, path, column, or
  exception. The raw error belongs to the log, never to an admin card.
- **PII-free by construction.** The pure derivation models carry only counts, booleans, and plain
  strings — never a DataFrame, never a free-text `error` field. A roster row cannot leak into a
  headline because the shape that would carry it does not exist.
- **Never-crash floors.** A surface that fails renders a calm `ErrorCard` (a red-bordered card with a
  clear cause), never a stack trace. Graceful degradation (an unreadable log, a broken config) is a
  first-class calm WARNING output, not an exception.
- **Decouple reassurance.** Wherever the admin might worry they've broken something, the copy
  reassures them their nightly sync keeps running in the background and their existing files are safe.
- **Clean native close.** The desktop window opens fast and closes cleanly (zero orphaned threads),
  the way a native app should.

## Per-surface flow and states

### Home — the sync-health dashboard

The landing surface answers *"is my roster syncing?"* three ways:

- If the install isn't set up yet (`needs_setup`), Home routes to **onboarding** — a calm first-run
  welcome that points at Setup.
- Otherwise Home derives a single **verdict** from the newest run record (`derive_home_status`): a
  HEALTHY "Your roster is syncing" with metric tiles (per-entity output counts + the plain last-run
  time + an SFTP-delivered flag), or an amber/red WARNING/FAILED with a plain headline and a "Check
  Run History" fix button.

States:
- **Loading** — reads the run log synchronously; fast.
- **Empty** — no runs yet: a calm "No sync has run yet" WARNING (never red), with the scheduled nightly
  time if one is registered.
- **Degraded** — the log couldn't be read: "Sync status unavailable" WARNING, reassuring the nightly
  sync may still be running.
- **Stale** — a clean success that's simply too old (>~1.5 nightly cycles): "No recent sync" WARNING.
- **Error** — the never-crash floor renders an `ErrorCard`, never a trace.

### Convert — ad-hoc, on-demand conversion

The admin picks a GDE input folder and runs a conversion on a background worker thread (the window
never freezes). The result is a verdict + entity tiles + a collapsible quality report. Anomalies
(>20% drops) gate delivery behind an explicit acknowledgment; a single-flight guard prevents
double-runs; SFTP delivery is pre-flighted.

States:
- **Empty** — no folder picked yet.
- **Running** — a spinner while the worker builds the roster.
- **Needs-ack** — a WARNING that some files look much smaller than usual; the admin reviews before
  delivering.
- **Error** — a fixed category card ("The conversion couldn't finish") — the raw exception is
  discarded, the existing files are explicitly unchanged.

### Run History — the read-only log of nightly runs

A verdict banner (the same "is my sync OK?" answer as Home, over the same latest record, so the two
can never disagree) above a read-only table of recent runs. The table carries **no error column** —
each run shows a plain time, a category-only status label, per-entity counts, an SFTP glyph, a
warnings count, and a plain duration. A raw error cannot be rendered because the row shape has no
`error` field.

States:
- **Empty** — no runs yet: a calm WARNING.
- **Degraded** — history unavailable (log unreadable): a calm WARNING, not red.
- **Error** — the never-crash `ErrorCard` floor.

### Setup — the one-scroll first-run flow

A single sectioned scroll: pick the input and output folders, choose the district, set the nightly
schedule (with the Windows run-as password for unattended operation), and configure SFTP delivery
(an allowlist host dropdown + credentials stored in the OS keyring, with a credential round-trip
check and a "Test connection" button). Saves are **structurally gated** — the Save button stays
disabled until the inputs validate, so an invalid path can never reach the config.

Every error card reads in the same calm, verdict-first voice (as of IA-9):
- **Validation error** — "That run time isn't valid" / "That SFTP host isn't allowed" with a fixed,
  actionable hint (enter HH:MM; pick from the dropdown) — never echoing the admin's raw input.
- **Credential save failure** — "Couldn't save the SFTP credential" with fixed category prose ("Try
  again, or run DistrictSync as the account the nightly task uses.") — never the raw keyring
  exception.
- **Credential unreadable** — a FAILED verdict explaining the credential couldn't be read back on this
  account.
- **SFTP test failure** — "SFTP connection failed" with a **bounded, category-mapped reason**
  (`friendly_sftp_reason`): the username/password was rejected, the host couldn't be found, the server
  couldn't be reached, or the remote folder wasn't accessible — with a mandatory catch-all. The admin
  learns *why* (the category their next action differs on) without ever seeing a raw paramiko/socket
  string.

### Mapping — review and switch the active district config

The admin reviews the active district mapping — its friendly name and the plain-language list of
output CSVs it produces (derived by the same empty-means-all rule the pipeline uses, so the summary
can never disagree with what actually ships) — and can switch to a different **pre-built** config
(a gated Apply). A broken partner-authored config is rendered calmly as a degraded summary (Apply
disabled), never a raw Pydantic/OS error. The raw `sis_type` appears only as a muted secondary
technical hint, never the primary label.

States:
- **Degraded config** — a broken YAML renders as a safe degraded summary, calmly.
- **No-op / broken Apply** — the Apply button is disabled when there's nothing valid to switch to.

### Help — a link-out to answers and a human

A static surface: a friendly greeting, a link to the SpacesEDU Help Centre knowledge base, and a
selectable support email. Rendered as selectable text (not just clickable) so it works even if
`launch_url` no-ops. No async, no failure surface.

## The three journeys

The six surfaces above are the map; these are the routes the three hats actually walk, with the
state each step owes the user.

### Journey 1 — First-run setup (the Installer)

A single guided path that ends in a checked promise: **folders → district → schedule → delivery → a
verified "you're set up" summary**. Schedule and Delivery are skippable ("set up later") so the first
success isn't gated on having a Windows password and live SFTP credentials in hand.

- **Empty / start** — one front door (Home's onboarding hero → the wizard), never three competing
  entrances.
- **Per step** — a valid/invalid inline state; the step advances only when its own gate is satisfied.
- **Schedule (async)** — a UAC prompt in flight ("waiting for the Windows permission prompt…"),
  declined, failed, or timed out — each named; success is confirmed by reading the task back, not
  assumed.
- **Delivery (async)** — testing / worked / failed / skipped; a success names the host and user tested.
- **Finish** — an honest, adaptive summary: what was checked, when, and what happens tonight (or "add
  a schedule / delivery whenever you're ready" when a step was skipped). Reaching the finish line —
  not any single step — is the only thing that marks the install "set up".

### Journey 2 — Daily trust check (the Watcher)

Open → read one verdict → done. Home derives a single HEALTHY / WARNING / FAILED verdict over the
newest run record.

- **Loading** — a synchronous local read; fast, no spinner needed.
- **Empty (genuinely new)** — a calm "no sync has run yet", with the scheduled time if one is
  registered.
- **Empty (fresh store, existing install)** — *"Run history starts fresh with this update — earlier
  runs aren't shown"*, never "no sync has run yet" (the store starts clean at this update; there is no
  backfill).
- **Healthy** — the verdict + metric tiles, greeting the *current* district.
- **Warning / failed** — the fault named by category + a fix path that routes and keeps the nav
  highlight truthful.
- **Degraded / error** — "sync status unavailable" or a calm ErrorCard, never a stack trace; a Refresh
  affordance re-checks in place for the Watcher who leaves the window open overnight.

### Journey 3 — Incident recovery (the Firefighter)

Home or Run History names the fault → a routed fix path → resolved. A previously-configured install
whose schedule broke lands on Home with a WARNING and a fix path into Setup's schedule section —
**never** back in first-run onboarding (the Firefighter is not a newcomer).

- **Fault named** — category only (ETL failed / didn't reach SpacesEDU / anomaly / schedule not
  firing), never a raw error.
- **Routed** — the fix CTA navigates *and* moves the "you are here" highlight; orientation never
  breaks mid-incident.
- **Output findable** — a locally-successful convert shows its output folder + an "Open folder"
  button, so manual delivery is possible when the nightly path is down.

## What "good" feels like

Calm, legible, reassuring. An admin opens the window, reads one plain sentence, and knows their
roster is syncing — or, if not, exactly what category of thing to check next and how. They close the
window trusting the nightly sync keeps running. No jargon, no dead ends, and **never** a raw machine
string.

## What's deliberately out (today)

- **The full column-mapping editor.** The Mapping surface reviews and switches between *pre-built*
  district configs; authoring a brand-new column mapping in-app (the visual field-mapping editor) is
  scope-locked to a later epic (IA-8b) on the ROADMAP.
- **Bundled offline docs in Help.** Help links out to the SpacesEDU Help Centre rather than rendering
  the bundled `docs/` markdown in-app. In-app offline docs are a future consideration, not shipped.
- **Management / multi-district views.** DistrictSync is a single-district admin's cockpit. Aggregate
  or fleet-management views are out of scope.

## Resolved product direction (program 0029 — trust & professionalism redesign)

A 2026-07-08 field test of the shipped Flet cockpit surfaced a pattern: the UI repeatedly **asserted
state it never verified**. Program 0029 is the response. The product decisions below are **resolved**
(user-approved 2026-07-08) and are the durable direction every 0029 slice builds toward — distinct
from the "what landed" descriptions above, which remain accurate for the pre-0029 build until each
slice lands.

- **One combined redesign program**, not a scatter of patches.
- **Setup becomes a wizard → settings hybrid.** First run is a stepped wizard with a checked finish
  line; once complete, the same surface graduates into a flat **Settings** scroll for edits. The
  Schedule and Delivery steps are **skippable** so the first success isn't gated on an admin password
  and live SFTP credentials being at hand.
- **Explicit district everywhere.** No silent default; "Choose your district" until chosen;
  auto-select only when exactly one config exists; Convert refuses to run without an explicit choice.
- **One front door.** While unconfigured, Home's onboarding hero is the only entrance to setup — no
  competing Setup-tab / Setup-led-nav doors.
- **Stable nav order.** The rail order is fixed (spatial memory is protected); a newcomer is guided by
  the initial selection + a "needs attention" badge, never by reordering the rail.
- **Output visible at the view layer.** The resolved output folder is shown before and after a
  convert, with an "Open folder" button — the folder path is app-owned config, not student PII, so it
  lives at the view layer and never enters the PII-free result model.
- **Provenance-honest checks.** "Test connection" names the host, user, and credential source it
  tested, and writes nothing to the keyring on the test path.
- **Run history is a district-scoped, store-backed ledger.** Runs are written to a per-user SQLite
  store (both the nightly and the manual paths), scoped to the district. It **starts fresh at this
  update** — no backfill (the only source, the diagnostic log, is ~98.6% test pollution); the empty
  state says so honestly.
- **Per-operation UAC elevation.** Registering the schedule self-elevates via a normal one-time
  Windows permission prompt; the app itself stays non-admin.
- **A single per-user app-data home, industry-standard per OS.** The store, config, and logs move
  from `~/.districtsync` to the platform-standard user-data directory (Windows LocalAppData, macOS
  Application Support, Linux XDG), with a transparent, idempotent, one-time migration.
