# Brief 0037 — the new-user front door, district identity, slim Home, and the mapping creator

> Status: OWNER-APPROVED INPUT to Stage 1 (triage/plan). Not a plan yet — the plan(s) derive
> from this via the claugentic pipeline (plan → adversarial review → spec → owner approval →
> implement → verify → land). Owner decisions below are locked (2026-07-27) unless re-opened.

## Owner decisions (locked 2026-07-27)

- **D-0037-1 — Light Flet flavor everywhere.** The app uses no audio/video; Linux already
  ships light (PLAT-0b). Switch Windows/macOS to `FLET_DESKTOP_FLAVOR=light` (~−13 MB).
- **D-0037-2 — Home hosts the setup wizard for unconfigured users.** The wizard (one shared
  component — the existing Setup wizard, not a copy) IS the new-user Home. The Setup rail item
  stays exactly as it is today (wizard pre-completion, Settings scroll after).
- **D-0037-3 — Slim Home post-setup.** After setup: ONE plain-language sync-verdict line
  (single-sourced with Run History via `home_status` — they can never disagree) + quick
  actions (Convert now · Run History · Settings). The metric tiles / dashboard go away.
- **D-0037-4 — District identity by required, stored email, resolved against a BAKED
  ALLOWLIST (revised 2026-07-27).** The launch page asks for the admin's work email before
  entering the app. It resolves against a per-district `authorized_emails:` list carried in
  the district's bundled mapping YAML (collected by the owner at mapping-creation time —
  "who will be setting this up?"). This replaces the earlier domain→district table idea:
  allowlists survive multi-domain districts and match the owner's onboarding workflow.
  NOT stored in `history.db` — that file is a per-machine runtime artifact created on first
  run and is not part of the release; the bundled config assets are the bakeable home.
  Unmatched email → type the SD number or "my district isn't listed" (custom-mapping path).
  The email is STORED in AppConfig (also prefills the Help/support mail flow, and is the
  designated recipient for future failure notifications — see D-0037-7). Every district list
  (wizard District step, Convert dropdown, Mapping catalog) then shows ONLY that district's
  mappings + the defaults. An advanced "show all districts" affordance remains for
  owner/support. Honesty bar: this is identification for UX filtering, NOT authentication or
  access control — all mappings still ship inside the exe, and knowing an admin's email
  unlocks their view. Trade-off accepted: changing an allowlisted email requires a release
  (fine at current district count; an updatable registry is a future option).
  **PRIVACY AMENDMENT (2026-07-27): the repo is PUBLIC**, so plaintext emails may never
  appear in committed files or the shipped YAMLs. The allowlist stores **salted hashes**
  (`authorized_email_hashes:`) — the entered email is normalized (lowercase/trim), hashed
  with the app salt, and compared; identical UX, nothing harvestable from the repo or the
  exe. The plaintext collection sheet lives OUTSIDE the repo on the owner's machine
  (`~/Documents/DistrictSync_allowlist_candidates_2026-07-27.md` — candidates already
  extracted from the Partner List; SD54 missing, SD74 needs confirmation).
- **D-0037-5 — Mapping creator = FULL structured UI** (no YAML surface). Every mapping
  capability gets a real form: the 8 field-map types, `row_filters`, headerless-file
  `headers`, `excluded_course_codes`, blended classes, `cross_enrollment`,
  `enabled_entities`, `entity_order`, `_base` inheritance. Sample-GDE-driven: the user points
  at a sample extract folder; real column names populate every picker; live preview shows
  transformed rows; activation is gated on schema validation + output-contract check +
  dry-run with quality report. Shipped mappings are READ-ONLY ("Save as my copy"); user
  mappings live in the app-data `mappings/` dir (already loadable today), editable and
  exportable as a file for support.
- **D-0037-6 — ONE release, many PRs (revised 2026-07-27).** The work lands as a sequence
  of PRs to main — Phase 1 (front door / identity / slim Home / contract / P1 leftovers)
  then Phase 2 (the creator) — each PR a complete slice with all gates green. A single
  release is tagged only after ALL PRs have merged AND the certification pass has run:
  /audit + /product gap analysis + spec review + the manual QA-checklist walk on the built
  exe. Version number decided at tag time (the scope likely warrants v3.9.0 or v4.0.0).
  (Supersedes the earlier two-release plan; phasing survives as PR ordering, not tags.)
- **D-0037-7 — Failure notifications DEFERRED to a later release, after this batch (owner
  decision 2026-07-27).** Baking SMTP credentials into the exe is permanently ruled out — PyInstaller
  archives are trivially unpacked, so a baked credential is public the day it ships (spam /
  phishing / unrevocable exposure of the support address). Candidate transports recorded for
  the future feature, in recommendation order: (1) server-side missing-upload watch on the
  SpacesEDU side alerting the district contact — the only transport that catches dead-machine
  / deleted-task failures, zero client secrets; (2) a scoped, revocable send-only notify
  endpoint the app calls for failures it can see; (3) per-install SMTP creds in the OS
  keyring (never baked). The registered identity email (D-0037-4) is the designated
  recipient whichever transport lands. Until then the spec's existing mitigation stands
  (Run History + the Home verdict as the in-app truth).

## Phase 1 scope (PR sequence — was "v3.9.0")

1. Light-flavor build (flet-pack.yml matrix) + pack-smoke/QA verification.
2. Home restructure per D-0037-2/3. Upgrade-in-place must not regress (D4a bake): existing
   configured installs land on slim Home, never back into the wizard.
3. Launch identity page per D-0037-4 + mapping filtering in all three district lists.
   Existing configured installs: one-time, non-blocking identity prompt (default proposal —
   plan may refine). Mapping labels must make same-district variants legible (sd51 rostering
   vs sd51 attendance read as two products, not duplicates).
4. `docs/developer/output-contract.md` anchored to the authoritative SpacesEDU doc
   (https://docs.google.com/document/d/1BePvuk5rg-YjUUvdwjb3X3Z0JWEUc5AtVjDfR3nub0U —
   Advanced CSV v1.0, 2025-07-23): per-CSV columns + semantics + encodings (incl. the
   StudentAttendance no-BOM rule), format version/date. RESOLVED 2026-07-27: the owner IS
   the SpacesEDU team — the live importer is authoritative, the tool's emitted headers
   (`EnrollStatus`/`SchoolCode`) are confirmed correct, and the Google Doc v1.0 is the
   partner-facing reference that lags slightly (functionality has evolved). The in-repo
   contract doc is therefore the MAINTAINED authority, stamped "confirmed against the live
   importer 2026-07-27", citing the Doc as the published reference.
   Strengthen `test_contract.py`: pin column ORDER
   (the doc requires it), per-entity BOM, add CourseInfo/StudentCourses/StudentAttendance
   schemas. The doc covers only the 5 rostering CSVs — myB+ course files + attendance need
   their own authority link or an explicit "internal spec" designation (owner input).
5. P1 leftovers from the 2026-07-27 field-test analysis: Convert header "This run: X"
   override pill + note copy + route-to-Mapping; CI smokes of the built exe (`--version`,
   `--dry-run` convert, stale-profile boot); spec sections (upgrade-in-place feature,
   district-resolution invariant); QA-checklist rows for every new surface.

## Phase 2 scope (PR sequence — was "v3.10.0")

The full structured mapping creator (D-0037-5). Safety rails are non-negotiable: a wrong
mapping ships a wrong roster (PII), so activation passes the SAME gates the runtime uses
(Pydantic schema, `ALLOWED_TRANSFORMS`, output-contract columns, delivery-integrity, dry-run
+ quality report). Previews render real sample data on the admin's machine but never log it.
User-mapping lifecycle: app updates validate user YAMLs fail-loud with a "fix or open
read-only" path (degraded catalog already exists); creator can clone any visible mapping;
export/import a mapping file for support tickets.

## Process requirements (owner's own)

- Full harness pipeline per `docs/claugentic-WORKFLOW.md`; slices sized for one-agent
  completion; per-slice Definition of Done; `districtsync-design` skill on every
  `src/ui_flet` change.
- PRODUCT_SPEC.md / PRODUCT.md / ROADMAP.md / DECISIONS.md / ARCHITECTURE_TREE.md updated as
  part of planning (roadmap entries written directly — no gap-analysis pass first).
- Final certification after ALL PRs merge, before the single release tag:
  /claugentic-dev-harness:audit + /claugentic-dev-harness:product gap mode + spec review,
  walking all UI/UX flows against the QA checklist on the built exe.

## Format authorities (resolved by search, 2026-07-27)

- **StudentAttendance — EXTERNAL AUTHORITY EXISTS.** Partner-facing docs:
  "Attendance Import for SpacesEDU (BC & Aspen)" Google Doc
  `15u_f7Jd91KDkk5ovyg69eaG1QVjXSZBd4tKnaTXzAmo` (v1.0, 2024-09-26, MyEdBC/Aspen GDE
  columns, DD-MMM-YYYY dates, category vocabularies, filename-must-end-with rule) and the
  generic "Attendance Import for SpacesEDU" `1s-Ai0OD70A0LJLz_n8QtA1z7cAlxdN4c0xc6yQ4Tgvs`
  (4 required fields; K-7 0.5/entry vs 8-12 0.25/entry weighting). Public Help Centre:
  https://help.spacesedu.com/en-ca/article/how-do-attendance-imports-work-4gc9ay/ .
  RECONCILIATION FLAG for the contract doc: the docs' category set (A, AD, A-E, A-E
  OffSite / AL, AL-E, L, L AUTH, L-E) and DD-MMM-YYYY date format differ slightly from the
  vocabulary noted in `myedbc_mapping.yaml`'s attendance comments — live importer wins
  (owner is the team); record the verdict per value.
- **CourseInfo/StudentCourses — NO partner-facing doc exists → "internal spec"
  designation.** The in-repo `output-contract.md` becomes the authority for the two myB+
  course files, citing internal provenance: Confluence REQ "[PathwaysEDU/SpacesEDU] Support
  for Advanced CSV files" (page 3761406457) and SKB "Imports" (page 3847160494). A
  real-world SD22 sample (`courseinfo.csv`; file id held in the team's internal Drive;
  scrubbed from this public repo 2026-07-29 — note git history retains the pre-existing
  occurrence) shows
  header-spelling variants (`CourseCode`/`SchoolID`/`Integration Id` vs our
  `Course Code`/`School ID`/`IntegrationId`) — same lenient-importer reconciliation as the
  rostering headers; owner confirms per column when the contract doc is written.

## Open items needing owner input (deferred to pre-release, per owner)

- Allowlist contacts: CANDIDATES ALREADY COLLECTED from the Partner List (Official) sheet
  into the owner-local file named in D-0037-4 (kept out of this public repo). Remaining
  owner actions at pre-release: supply the SD54 Bulkley Valley contact (absent from the
  sheet), confirm the SD74 contact spelling, and approve the final list before hashing.
  The owner's own work email is allowlisted across ALL districts (the support / "show all"
  identity; address recorded in the local file, not here).
