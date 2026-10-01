# Plan 0053 S15 — CLAUDE.md disposition map

Base: `712ff26:CLAUDE.md` (352 lines, 114,185 bytes). Every heading and every bold-led sub-block, with its disposition, target and reason. Line numbers are the BASE file's.

**Dispositions.** **KEEP** — stays in `CLAUDE.md` verbatim. **KEEP-RULE** — the prohibition / safety / privacy / gate / contract stays in `CLAUDE.md`, as a one-liner where the original was a narrative (a one-liner restates; it is not a copy). **MOVE** — moved VERBATIM into the named guide with a one-line pointer left behind (most MOVE rows are also KEEP-RULE for the rule they carry). **POINT-TO-EXISTING** — another doc already says it; the text is dropped. **BYTE-IDENTICAL** — the harness-managed regions, proved by sha256.

**What "verbatim" means here.** Each guide is generated from the base file by line number (`build_guides.py`, kept outside the repo). Inside a moved block the only changes are (a) paragraph breaks inserted at a sentence boundary before a bold-led span — whitespace only, list items continuing as indented paragraphs — and (b) the seven changes listed under **In-block changes**. Headings between blocks are new; one lead-in sentence (`ui-surfaces.md`'s "Seven surfaces in `screens/`:", the end of the base's shell paragraph) is placed under the "The screens" heading it introduces.

## Where the ~50 KB with no named target went (and why)

| New guide | Holds | Why a new guide rather than an existing one |
|---|---|---|
| `docs/developer/scheduler.md` | `windows.py` / `elevation.py` / `linux.py` bullets, the principal's pure half (0046 B), the seasonal sync window | `src/scheduler/` had no guide; `architecture.md`'s one-line table row for `windows.py` still says PowerShell. Scheduling is one subsystem read by one kind of change; folding it into `machine-scope.md` would mix task registration (which runs on every per-user install) with the per-install profile model. |
| `docs/developer/configuration-reference.md` | Configuration-Driven Design: the mapping types, `excluded_course_codes`, both grade scopes, the bundled-config roster, `district_domains` + unknown keys, `row_filters`/`cross_enrollment` | `adding-district.md` is a procedural how-to (Steps 1–7) plus the frozen overlay shape a test reads by heading; this text is the maintainer's per-key invariant reference. 18 KB of it would bury the steps. Cross-linked: Step 5 links here, this guide's intro links back. Holds two of the four count sentences (repointed pin). |
| `docs/developer/etl-internals.md` | Architecture's subsystem sections, the Key Data Flow bullets, the `history/store.py` / `sftp/uploader.py` / `column_names.py` bullets | `architecture.md` is a 13 KB overview in its own words; appending the dense notes would put two descriptions of the extractor/transformers/loader on one page. A sibling page, linked from `architecture.md`'s intro, keeps the overview readable. |

Rejected homes: `testing.md` (no moved block is about tests; the Tests commands and Testing Conventions are KEEP) and `failure-policy.md` (a 141 KB policy whose tables carry their own parity test; Key Data Flow is entity behaviour, not policy).

## Preamble and Project Overview

| Base | Block | Disposition | Target | Reason |
|---|---|---|---|---|
| L1, L3 | `# CLAUDE.md` + the guidance line | KEEP | — | Identity of the file |
| new | "This file is RULES + an INDEX" | NEW | — | D13: tells the reader where narratives went |
| L5–7 | `## Project Overview` | KEEP (one stale fix) | — | Orientation; "(see **Output Targeting** below)" still resolves. Fix: "up to 7 output CSVs" omitted StudentAttendance (the base defines 8 templates) |

## Commands (L9)

| Base | Block | Disposition | Target | Reason |
|---|---|---|---|---|
| L11–16 | `### Run (development)` fence + CLI flags | KEEP | — | Command |
| L18 | `--diagnose` (plan 0049 D7): argv pre-check beside `--elevated-apply`, console attach only, guards, exits 0, **not PII-free** | MOVE + KEEP-RULE | `machine-scope.md` → "`--diagnose` — the support report" | The PII sentence is a privacy rule (kept verbatim); the why-before-argparse detail is narrative; review round: the not-PII-free caveat is also the declared exception in Security's **Bounded diagnostic output** bullet |
| L20–26 | `### SFTP credential setup` fence | KEEP | — | Commands |
| L27 | Handlers / host allowlist / keyring / `config.json` location | KEEP | — | Command reference; anchors the profile rule below |
| L29a | **`DISTRICTSYNC_DATA_DIR`** preamble: definition, wins outright, legacy no-op, why it exists (Windows `LOCALAPPDATA`), the MEASURED `WIN_PD_OVERRIDE_*` correction, banner, **Never run the app/CLI locally without it** | MOVE + KEEP-RULE | `machine-scope.md` → "The profile location and `DISTRICTSYNC_DATA_DIR`" | Definition, banner and never-run-bare sentences kept verbatim (cited by `.claude/hooks/session-start.sh:35`, `docs/FLET_1.0_CONVENTIONS.md:40`); the `WIN_PD_OVERRIDE_*` refusal and "never machine scope" as one line |
| L29b | **Machine scope (plan 0049, REACHABLE since S-2b …)** — ladder, HKLM switch + `MACHINE_SCOPE_KEY_ACCESS`, `_assert_machine_dir_trusted`, `MachineScopeRefused`, `_MACHINE_SCOPE_CAUSES` | MOVE + KEEP-RULE | `machine-scope.md` → "The machine-scope ladder and its trust checks" | Rule kept: a new reason needs a cause (completeness test) |
| L29c | **`OPEN_ACE` (S-1b-i)** | MOVE | same | Detail |
| L29d | **It NEVER falls through to the per-user profile** — unreadable switch refuses, pin once per process, `is_machine_scope()` ONE predicate, override never machine scope, `handshake_dir`, `runs/` per-writer log, `dpapi_call(..., *, flags)` | MOVE + KEEP-RULE | same | Never-fall-through, pin, ONE predicate, required `flags` kept; review round: `handshake_dir()` per-user + non-creating in every scope kept (Machine scope) |
| L29e | **The SELECT RULE (S-1a-ii)** + `run_as` record key | MOVE + KEEP-RULE | `machine-scope.md` → "One secret store: the SELECT RULE" | "Exactly one store, never a fallback chain" kept |
| L29f | **PROVISIONING (S-1b-i, …)** | MOVE + KEEP-RULE | `machine-scope.md` → "Provisioning (the elevated half)" | SID strings, the 64-bit mask kept; review round: create-WITH-DACL / `ERROR_ALREADY_EXISTS` never-adopt and created-only rollback kept (Machine scope) |
| L29g | **Resume rule** + icacls FATAL + result vocabulary + the moved AC1 registry pin | MOVE + KEEP-RULE | same | `provisioning.py` ONLY registry writer, icacls FATAL kept; review round: result vocabulary (step id + exit code, never stderr/paths/secret) kept (Security → Bounded diagnostic output) |
| L29h | **THE PARENT (unelevated) HALF (S-1b-ii, …)** | MOVE | `machine-scope.md` → "The parent (unelevated) half" | Narrative; review round: `complete_handover` gates on the parent's OWN re-read, never the child's claim — kept (Machine scope) |
| L29i | **The `MOVED.txt` FENCE (amendment 2)** | MOVE | same | Narrative |
| L29j | **The auto-grant window (amendment 3)** | MOVE | same | Narrative |
| L29k | **S-2a (honest predicates)** | MOVE + KEEP-RULE | `machine-scope.md` → "Honest predicates (S-2a)" | Single-sourced scope words, "the wrong answer is SILENT" kept; review round: `run_as_display`, never a raw `DOMAIN\user` — kept (Security → Bounded diagnostic output) |
| L29l | **S-2b (the flow)** | MOVE | `machine-scope.md` → "The flow (S-2b)" | Narrative |
| L29m | **S-3 (the principal model)** | MOVE + KEEP-RULE | `machine-scope.md` → "The principal model (S-3) and gMSA (S-4)" | "Principal DECLARED, never inferred" kept (Scheduler rules) |
| L29n | **S-4 (gMSA, Settings only)** incl. the retired-hedge decision and the `[HRESULT ` anchor | MOVE + KEEP-RULE | same | "A new gMSA failure gets a FORK" kept; the anchor now lives in this guide (partner pin declares it) |
| L29o | **THE NAMED HOLE** | MOVE + KEEP-RULE | `machine-scope.md` → "The named hole" | One-line "Open hole" kept |
| L31–37 | `### Tests` fence + coverage/omit/drift-matrix line | KEEP | — | Command + gate |
| L39–47 | `### Lint + Format` | KEEP | — | Gate |
| L49–54 | `### Type Check` | KEEP | — | Gate; cited by `.claude/hooks/session-start.sh:28` |
| L56–61 | `### Security Scan` (the `-c pyproject.toml` rule) | KEEP | — | Gate |
| L63–66 | `### Validate configs` ("validates all N configs") | KEEP | — | Count sentence 1, pinned in CLAUDE.md |
| L68–71 | `### Desktop UI (Flet)` | KEEP | — | Command |
| L73–78 | `### Build executables` | KEEP | — | Cited by `src/sftp/uploader.py:52` |
| L80–81 | `### Documentation` (duplicate of `## Documentation`) | POINT-TO-EXISTING | `CLAUDE.md` `## Documentation` | Internal duplicate; the 3 units are the checker's declared exceptions |

## Architecture (L83)

| Base | Block | Disposition | Target | Reason |
|---|---|---|---|---|
| L85–93 | Pipeline sentence + diagram | KEEP | — | Orientation; pointer line to `architecture.md` + `etl-internals.md` added |
| L95–96 | `### Extractor` | MOVE | `etl-internals.md` → Pipeline subsystems | Detail |
| L98–111 | `### Transformer` + 11 bullets (`base`, `context`, `registry`, `students`, `staff` — the plan 0052 role rule, `family`, `classes`, `enrollments`, `blended`, `course_info`, `student_courses`) | MOVE + KEEP-RULE | same | Staff role rule kept under Key Data Flow; `blended.py`'s "(see `class_rostering_grades` below)" re-pointed |
| L113–118 | `### Loader`: atomic `save_all`; **backup-and-restore atomic**; **Output-folder pre-flight (0050)** GOTCHA | MOVE | same | Gotcha + mechanism; the pointer line names both |
| L120–122 | `### Config` (`models.py`, `loader.py`) | MOVE | same | Detail |
| L124–125 | `### Quality` | MOVE | same | Detail |
| L127 | `### Desktop UI (src/ui_flet/)` heading | KEEP (re-homed) | `CLAUDE.md` → Subsystem guides → Desktop UI | Rules grouped under the same heading |
| L128 | Intro: launcher; **Boot order … load-bearing**; **The gate is no longer one-way**; app body + rail; **Launch selection is Home in EVERY state since S6** + badge suppression | MOVE + KEEP-RULE | `ui-surfaces.md` → "The shell, the boot order and the gate" | Boot order + `page.add` once kept |
| L129 | `screens/identity.py` (launch page; **`NOT_LISTED_NOTE_TAIL`** single source) | MOVE + KEEP-RULE | `ui-surfaces.md` → "The screens" | "Read the deliberately-absent docstring first" kept |
| L130 | `screens/home.py` (**Home HOSTS the setup wizard**, **FOUR** welcome variants, **S7 (slim Home)**, **Three identity cards (0038 S4b)**) | MOVE | same | Narrative |
| L131 | `screens/setup.py` (identity section; **first-run 5-step WIZARD**; **S6**; **two ADVANCE-only gates**; **`schedule_skipped`**; **S5**; **seed fires ONLY when nothing is saved**; **Service account (0046 B)**) | MOVE + KEEP-RULE | same | identity_save/identity_clear and principal-never-decides-what-to-SEND kept |
| L132 | `screens/convert.py` | MOVE | same | Narrative; review round: D9: explicit district (no `configs[0]`), no input-dir fallback, fails loud on unset output — kept (Desktop UI → No silent district) |
| L133 | `screens/run_history.py` | MOVE | same | Narrative |
| L134 | **Home's branch (a) also carries the ONE way back to the launch page** | MOVE + KEEP-RULE | same | "identity_clear never for a mistyped address" kept |
| L135 | `screens/mapping.py` | MOVE | same | Narrative |
| L136 | `screens/help.py` | MOVE | same | Narrative; review round: address sent nowhere, `support_mailto` subject-only — kept (Security → Bounded diagnostic output) |
| L138 | Pure COUNTED logic / view glue / build via `components.py` | MOVE + KEEP-RULE | `ui-surfaces.md` → "Pure (counted) logic versus view glue" | Factories + Flet traps kept in the Design-system rule; coverage omit already in Tests (KEEP) |
| L140 | **The scheduled-task PRINCIPAL (0046 B, pure half)** | MOVE + KEEP-RULE | `scheduler.md` → "The scheduled-task principal — the pure half" | Required-undefaulted principal + `principal_key` kept |
| L142 | **Self-service districts (plan 0044)**; **The verified fact is DIGEST-keyed**; **FIVE `sis_type` writer surfaces** (+ provenance, floors, threat model); **Owner review round 2** | MOVE + KEEP-RULE | `self-service-mappings.md` (four sections) | THIN overlay, ONE writer/activation, five gated surfaces, raise-vs-warn never inverted, frozen shape, MAPPING vocabulary kept. "this file's quoted-literal set" re-pointed; review round: export path shown, never logged — kept (Security → Bounded diagnostic output) |
| L144 | **Design system** | KEEP-RULE + MOVE | `CLAUDE.md` one-liner; verbatim copy in `ui-surfaces.md` → "The design system" | Authority stays `docs/DESIGN_SYSTEM.md` + the `districtsync-design` skill; review round: AA contrast pairs (`UI_CONTRAST_PAIRS`) stay green — kept in the Design-system line |
| L146 | `### Supporting modules` heading | dissolved | — | Bullets distributed by subsystem (checker's declared exception) |
| L147 | `src/etl/sync_window.py` — **Seasonal sync window** | MOVE + KEEP-RULE | `scheduler.md` → "The seasonal sync window" | "Confirmed-MISSING OUTRANKS the pause" kept (cited by `screens/setup.py:621`, `tests/test_ui_flet_shared_records_copy.py:108`) with the app-not-OS, exit-0, bypass, fail-LOUD rules |
| L148 | `src/config/app_config.py` (**`schedule_run_as_user` (0046 B)**, **Identity (0038)**, `identity_clear`) | MOVE + KEEP-RULE | `ui-surfaces.md` → "The settings model and the identity modules" | Prefix contracts, NAME-not-password, ONE identity write path kept; review round: `sis_type` defaults to `""` (D9) — kept (Desktop UI → No silent district) |
| L149 | `src/utils/identity.py` | MOVE + KEEP-RULE | same | EXACT matching, fail OPEN kept; review round: validation messages never echo the address — kept (Security → Bounded diagnostic output) |
| L150 | `src/ui_flet/identity_gate.py` | MOVE + KEEP-RULE | same | "Can only ask a question" kept; review round: a stored identity re-validated at read, never echoed — kept (Security → Bounded diagnostic output) |
| L151 | `src/history/store.py` | MOVE + KEEP-RULE | `etl-internals.md` → "The run store, delivery and column names" | Store non-fatal / privacy split kept (Run record line) |
| L152 | `src/sftp/uploader.py` | MOVE | same | Host allowlist rule already in Security (KEEP) |
| L153 | `src/scheduler/windows.py` (**In-process COM**, **principal DECLARED (S-3)**, **literal `return False, …` AST-banned** + the `[HRESULT ` anchor) | MOVE + KEEP-RULE | `scheduler.md` → "The Windows scheduled task" | Declared principal, password never argv/env/log, `_fail` + one grep anchor, EXACT classifier, `account_is_current`, never elevated kept. Base bullet ends mid-sentence ("returns `DOMAIN\user`", no period) — kept verbatim; review round: the principal-mismatch WARNING names no account — kept (Security → Bounded diagnostic output) |
| L154 | `src/scheduler/elevation.py` | MOVE + KEEP-RULE | `scheduler.md` → "Elevation and the cron adapter" | DPAPI CurrentUser, never LocalMachine kept |
| L155 | `src/scheduler/linux.py` | MOVE | same | Detail |
| L156 | `src/etl/column_names.py` | MOVE | `etl-internals.md` | The rule is Configurable Columns (KEEP) |
| L157 | `src/utils/validators.py` | KEEP | `CLAUDE.md` `## Security` (bullet) | A security index line |

## Configuration-Driven Design (L159) — heading KEEP, rules one-liners

| Base | Block | Disposition | Target | Reason |
|---|---|---|---|---|
| L161–170 | YAML location, `--sis`, the field-mapping types (two role transforms; academic-year + frozen SD74 snapshot; email templates) | MOVE + KEEP-RULE | `configuration-reference.md` → "Field-mapping types" | First two sentences kept verbatim; ALLOWED_TRANSFORMS, distinct role transforms, SD74 snapshot do-not-sync kept |
| L172 | `excluded_course_codes` | MOVE | → "`excluded_course_codes`" | Detail |
| L174 | `class_rostering_grades` + **UNGATED since plan 0043** | MOVE + KEEP-RULE | → "Grade scopes: `class_rostering_grades`" | CEDS space, non-idempotent conversion, branch on resolved scope kept |
| L176 | `student_rostering_grades` + **The inherited bound** + `SUPPORTED_CONFIG_MINOR` is 15 | MOVE + KEEP-RULE | → "Grade scopes: `student_rostering_grades`" | Minor-bump-with-consumer, quoted version kept; review round: an unresolvable grade column RAISES (fail-open would deliver unlicensed students' PII) — kept (Grade scopes line) |
| L178 | The bundled-config roster (… **Phase-2 batch**, **`unitychristianmyedbc`**, **`sd45myedbc`**, "Total: N bundled configs") | MOVE | → "The bundled configs" | Count sentence 2 now pinned HERE; two dangling refs re-pointed; Unity correction appended |
| L180 | `district_domains` (**PICKERS ARE SCOPED**, **Picker ORDER** with "pinned N-config count", unknown keys by origin, **No `version:` bump for a presentation key**) | MOVE + KEEP-RULE | → "`district_domains`, picker scoping and unknown keys" | Count sentence 3 pinned HERE; "Fourteen shipped rows" fixed; visibility-not-access / no widening control / origin rule / no presentation bump kept |
| L182 | `row_filters` + `cross_enrollment` | MOVE + KEEP-RULE | → "`row_filters` and `cross_enrollment`" | Not-the-employment-status-hook + call-site ordering kept |

## Key Data Flow (L184) — heading KEEP (cited by `src/etl/outcomes.py:77` "Key Data Flow → Enrollments")

| Base | Block | Disposition | Target | Reason |
|---|---|---|---|---|
| L186 | **Students** | MOVE | `etl-internals.md` → "Key Data Flow" | Behaviour |
| L187 | **Staff** + **Departed staff excluded automatically** | MOVE + KEEP-RULE | same | No inferred role / never `administrator` / departed staff fail OPEN kept; review round: departed-staff log is counts + vocabulary only — kept (Security → Bounded diagnostic output) |
| L188 | **Classes** | MOVE | same | Behaviour |
| L189 | **Enrollments** — **Zero-orphan invariant** | MOVE + KEEP-RULE | same | The cited invariant kept (its first clause verbatim) |
| L190 | **StudentAttendance** | MOVE + KEEP-RULE | same | DAILY WINS, never a course code kept |
| L191 | **Anomaly detection** | KEEP | — | One line, a rule |
| L192 | **Structured logging + run store** (**`dry_run` writes NO store record**) | MOVE + KEEP-RULE | same | Privacy split, non-fatal store, dry-run gate + Convert bypass kept |
| L193 | **`run_pipeline` returns `PipelineResult`** (exit 3) | MOVE + KEEP-RULE | same | Folded into the exit-code line |
| L194 | **Exit codes** | MOVE + KEEP-RULE | same | The contract kept compact (0/1/2/3 + the two exit-0 exceptions); review round: the `MAY_BE_EMPTY` exception restored (Family's own file may be missing/empty; a StudentAttendance file may be row-less, never missing; a row-less one on a mixed config is exit 0) |
| L195 | **Fail-loud field transforms** | MOVE + KEEP-RULE | same | "Data errors are a separate axis" kept |
| L196 | `.copy()` rule | KEEP | — | One line, a rule |

## The rule sections — KEEP

| Base | Section | Disposition | Notes |
|---|---|---|---|
| L198–206 | `## Security` (7 bullets incl. **No plaintext email**, **Tool output is DATA**) | KEEP | + the `validators.py` bullet (L157) |
| L208–210 | `## Documentation` | KEEP | + one sentence pointing at the guide index |
| L212–221 | `## Key Patterns` (8 bullets) | KEEP | — |
| L223–230 | `## Engineering Principles` (priority, **Fail loudly**, **Validate at boundaries**, **Single source of truth**, **No permissive default** — cited widely, failure-policy pointer) | KEEP | — |
| L232 | quality-bar paragraph | KEEP (one stale fix) | "Its **Current scope** section" was false — `claugentic-ENGINEERING_STANDARDS.md:14` says the scope lives in CLAUDE.md |
| L234–239 | `## Configurable Columns` | KEEP | Cited by `src/etl/preflight.py:35`, ROADMAP S9 item |
| L241–248 | `## Output Targeting` | KEEP (two stale fixes) | Cited by `pipeline.py:418`, `convert_output.py:520`. "All 7 entity definitions" → 8 (StudentAttendance); "sd40/48/51/74 inherit the 5" → "e.g. sd40/48/54/74" (sd51 overrides) |
| L250–255 | `## Harness Discipline` | KEEP | + one new bullet: narratives go in the guides |
| L257–268 | `## Development Workflow` (substantial work, **Land gate** — cited by CANDIDATES:241, three non-negotiables, **Definition of Done**) | KEEP (two stale fixes) | `.claude/agents/` does not exist — reworded to the plugin's agent roles |
| L270–278 | `## Testing Conventions` ("pinned at N", **No vacuous greens**) | KEEP (one fix) | Count sentence 4 stays; the pointer sentence now names both count-sentence homes |
| L280–300 | `<!-- harness:managed:start -->` … `end -->` | BYTE-IDENTICAL | sha256 `80d6f1e2e16754b5afaf629b6692d7394cd5fc91d35ec753b77e78074122412a` before and after |
| L302–352 | `## Harness — Current scope (claugentic)` → EOF | BYTE-IDENTICAL | sha256 `da198d2d341333be0fe07ccafb0dfe0350c066ac22a6badf9e28946c7a016e5c` before and after. Stale inside, REPORTED not edited: "~1,686 tests" (×2), the bare `bandit -r src/ -q` |

## In-block changes (the only edits inside moved text)

| Guide | Before | After | Kind |
|---|---|---|---|
| `self-service-mappings.md` | "keeps this file's quoted-literal set EMPTY" | "keeps `CLAUDE.md`'s (and this guide's) quoted-literal set EMPTY" | dangling reference |
| `configuration-reference.md` | "Base `myedbc` defines all 7 entity templates;" | "Base `myedbc` defines all 8 entity templates (incl. `StudentAttendance`);" | stale statement (the base mapping defines 8; CLAUDE.md's Output Targeting got the same fix) |
| `configuration-reference.md` | "(see **Output Targeting**)" | "(see `CLAUDE.md` → **Output Targeting**)" | dangling reference |
| `configuration-reference.md` | "(see **Key Data Flow**)" | "(see **Key Data Flow** in [etl-internals.md](etl-internals.md))" | dangling reference |
| `configuration-reference.md` | Unity's "re-enable it with." | + a dated *(Superseded 2026-09-14 …)* sentence: Family is ON again | stale statement (the config enables Family) |
| `configuration-reference.md` | "Fourteen shipped rows: sd10/…/83 at `sd##.bc.ca`, SD60 at `prn.bc.ca` and SD75 at `mpsd.ca`" | "Sixteen shipped rows: sd10/27/38/40/45/48/51/54/60/67/69/71/74/75/83 at `sd##.bc.ca` — SD45 also at `wvschools.ca`, SD60 at `prn.bc.ca`, SD75 at `mpsd.ca` — plus Unity Christian at `unitychristian.ca`" | stale statement (16 configs declare domains) |
| `etl-internals.md` | "(see `class_rostering_grades` below)" | "(see `class_rostering_grades` in [configuration-reference.md](configuration-reference.md))" | dangling reference |

## Pins repointed / registered

| Test | Change |
|---|---|
| `tests/test_config_count_pin.py` | Four count sentences, each with ONE home: CLAUDE.md ("validates all N configs", "pinned at N"), `configuration-reference.md` ("Total: N bundled configs", "pinned N-config count"); present exactly once at home, absent from the other |
| `tests/test_partner_doc_schedule_copy_parity.py` | CLAUDE.md `{log_anchor}` → `frozenset()`; `scheduler.md` + `machine-scope.md` declare `log_anchor`; the other four guides registered empty |
| `tests/test_creator_doc_copy_parity.py` | All six guides registered empty |
| `tests/test_ui_flet_band_copy_parity.py` | `ui-surfaces.md` registered with the four constants it quotes |
