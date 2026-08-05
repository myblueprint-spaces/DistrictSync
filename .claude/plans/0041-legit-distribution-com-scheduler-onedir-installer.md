# 0041 — Legit distribution: COM scheduler + onedir installer (+ signing)

- **Status:** In Review (Round 1 incorporated — see ## Review)
- **Roadmap item:** `docs/claugentic-ROADMAP.md` — the `[AV / DISTRIBUTION]` backlog entry (2026-08-04 Bitdefender ATC incident), its fixes (1), (2) and (3); overlaps the queued `0036 bigger bets — startup/installer spike`.
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-06-15 XML / 2026-06-25 EncodedCommand + CLIXML / 2026-07-08 D4 read-back + D5 elevation / 2026-07-28 light-flavor + no-UPX / 2026-07-30 land gate / 2026-07-31 v3.9.0 override) · plan 0038 (house style) · `.claude/plans/0032-ui-ux-sweep-proposal.md` (installer spike detail)

## Problem

DistrictSync's Windows artifact behaves like malware to behavioural antivirus, and on 2026-08-04 Bitdefender ATC acted on that: it blocked the exe and quarantined the Flet client's `flet.exe` out of `%USERPROFILE%\.flet\`, bricking the window on every later launch (ROADMAP `[AV / DISTRIBUTION]`, confirmed live by the owner). Three of our own behaviours produce the signature:

1. **`powershell.exe -EncodedCommand <base64>`** (`src/scheduler/windows.py:591-596` register, `:1005` read-back, elevation bootstrap `:696`) — base64-encoded PowerShell creating a scheduled task under UAC elevation is the textbook persistence chain, explicitly weighted by Defender ASR, Bitdefender ATC and most EDR. The encoding exists only because PS 5.1 silently no-ops a multi-line `try/catch` piped to `-Command -` (DECISIONS 2026-06-25) — a workaround for a transport we can stop using entirely.
2. **A ~96 MB unsigned one-file exe that self-extracts into `%TEMP%` and executes from there** (`flet pack`'s one-file default).
3. **First launch drops and executes a second unsigned exe into the user profile**: `flet_desktop.ensure_client_cached()` extracts `flet.exe` into `~/.flet/client/` and runs it from there — **and this is layout-independent: `--onedir` alone does NOT remove it** (verified against flet_desktop 0.85.3: the cache extraction runs whenever the cache dir is absent, one-file or onedir; `flet.exe` executes FROM the cache). This is the exact behaviour Bitdefender quarantined. Removing it requires `FLET_VIEW_PATH` (checked *before* the cache, `flet_desktop/__init__.py:367-377`; already documented at `docs/FLET_1.0_CONVENTIONS.md:125`) pointed at a client we ship inside the install tree.

The owner's direction (2026-08-05): make the software *legitimately shaped* rather than working around any one engine. Signing at ~$10/mo (Azure Artifact Signing) approved in principle; free routes don't fit a closed-source org product.

Secondary problems the same work discharges: the scheduled task bakes in whatever path the exe launched from — usually `Downloads` (`setup.py:1414`; the `_TRANSIENT_LOCATION_WARNING` exists because of this) — an installer gives it a stable `Program Files` path; schedule registration has **no timeout** (ROADMAP "Robustness") — the rewrite adds one; every nav-click probe spawns a PowerShell process — in-process COM removes a spawn per click; one-file's runtime extraction is the slow first paint (7.1s, 0032) and the reason the 150s/120s smoke budgets exist; and the quarantine-brick failure mode (`ensure_client_cached` early-returns on a gutted cache dir) becomes structurally unreachable once the app never consults `~/.flet` at all.

## Goals / Non-goals

**Goals**

1. Zero `powershell.exe` child processes in the scheduler's steady-state paths (register, read-back, delete) — Task Scheduler COM API (`pywin32`) in-process, every current behavioural guarantee preserved and re-pinned (Approach → "The contract being carried").
2. The elevated (stored-password) path stops launching PowerShell too: the elevated child becomes **DistrictSync itself** in a locked-down, dispatch-first `--elevated-apply` mode running the *same* Python registration code — DPAPI handshake unchanged, single-source restored in Python.
3. The Windows artifact becomes **`--onedir` (flet-cli's native `flet pack --onedir`, verified present at 0.85.3) wrapped in an Inno Setup installer**: `Program Files\DistrictSync`, Start menu, uninstaller, in-place upgrades keeping the registered task's exe path valid. No `%TEMP%` self-extraction, no bootloader re-exec.
4. **The Flet client ships pre-extracted inside the install tree and `~/.flet` is never consulted**: the frozen launcher sets `FLET_VIEW_PATH` to the bundled client dir. This — not onedir itself — is what removes the profile drop-and-execute, makes `flet.exe` a signable file in our tree, and kills the quarantine-brick mode.
5. CI builds, smokes and releases the installer with today's evidence discipline (three-OS matrix, offline proof redesigned for the no-profile-drop world, zero-orphan close, CLI smokes, checksums) — **with the release workflow reconciled in the same PR as any artifact-shape change** (no window where a tag breaks `publish-release`).
6. Signing (Azure Artifact Signing) for the inner exe, the bundled `flet.exe`, and the installer — a slice whose *start* is gated on the owner creating the account, so its steps are verified live in the slice that lands them, never dormant-and-untested.
7. Linux/macOS artifacts unchanged (flet-cli's `--onedir` exits 1 on macOS anyway; both stay one-file single binaries).

**Non-goals**

- Procuring the signing account (owner/business action; Slice 5 starts when it exists).
- MSI / winget / auto-update / "update available" signals (update checks need egress beyond the three allowlisted SFTP hosts — separate owner posture decision; DECISIONS-recorded deferral).
- The `~/.flet` self-heal stat-check (obsoleted structurally by Goal 4 for v-next installs; the ROADMAP entry is *closed by* Slice 3, not by a stat-check).
- Migrating cron/Linux scheduling; the `Scheduler` protocol and cron adapter are untouched.
- Single-instance mutex, splash, and the rest of the 0036 startup spike.
- Re-litigating no-UPX (DECISIONS 2026-07-28).

## Approach

### A. Scheduler: PowerShell → Task Scheduler COM (pywin32), single-sourced across the elevation boundary

New pure-Windows module `src/scheduler/task_com.py` owns COM session setup (`CoInitialize` context manager per entry), the task-definition builder, and the HRESULT→canonical-message table; `windows.py` keeps the public API, message constants and orchestration (validators → COM call → read-back confirm), so `get_scheduler()`, `schedule_probe`, `setup_errors` and the five `_MSG_*` constants are untouched. Dynamic `win32com.client.Dispatch` **only** — `EnsureDispatch`/`gencache`/`makepy` are banned by an absence pin (runtime code-gen into a cache dir is both the frozen-exe failure that disqualified `comtypes` *and* an AV-shaped behaviour in a plan about removing AV-shaped behaviours).

**The contract being carried** (each row = a pin in the re-authored tests):

| # | Guarantee | COM form |
|---|---|---|
| 1 | Settings quintet: `StartWhenAvailable=False` (no catch-up), `IgnoreNew`, `PT2H` limit, battery-operation enabled (both flags) | Explicit `TaskDefinition.Settings` — COM defaults differ on all five |
| 2 | Explicit `TASK_LOGON_PASSWORD` / `TASK_LOGON_INTERACTIVE_TOKEN`+Limited; S4U unrepresentable (absence pin); `run_highest` ignored without a password | Literal constants to `RegisterTaskDefinition` |
| 3 | Credential validation + batch-logon-right grant on the password path | Service-provided under `TASK_LOGON_PASSWORD`; verified live in the Slice-1b QA walk (the `setup_errors` batch-logon coaching depends on it) |
| 4 | Culture-invariant times: we compose `Trigger.StartBoundary` as invariant ISO-8601 with the retired XML's fixed past date (2024-01-01, deterministic + catch-up-inert); read-back re-serialises COM datetimes to the invariant ISO strings `ScheduleReadback` promises | Ours to emit — never locale-formatted |
| 5 | Tri-state read-back: `found=False` **only** on the definitive not-found HRESULT (`0x80070002`/`SCHED_E_TASK_NOT_FOUND`); access-denied, RPC failure, COM-init failure, non-Windows → `found=None` | HRESULT-keyed, never message-text-keyed |
| 6 | Never-run sentinel: 1899-12-30 epoch nulled (the PS `Year -gt 1900` rule) + `LastTaskResult=267011` (`SCHED_S_TASK_HAS_NOT_RUN`) mapped, or the fired-but-no-record contradiction false-alarms on every never-run task | Explicit mapping |
| 7 | Success ≠ API-didn't-throw: register/delete confirmed via `read_schedule` (D5's read-back-confirmed invariant) | Orchestration unchanged |
| 8 | Bounded, never-raises probe (today: 10s subprocess timeout) | Daemon worker + `join(10)`; timeout → `found=None`; a hung-RPC leaked thread is logged WARN and accepted (recorded trade) |
| 9 | Message contract: `setup_errors.classify_schedule_error` branches on "Access is denied" (+ elevation state) and the five `_MSG_*` constants by exact value; everything else falls to its calm else-branch with the raw text demoted to "(Details: …)" — *note: there is no "user name or password is incorrect" branch; that text reaches the else-branch and must stay readable, not become a com_error repr* | HRESULT→canonical-English table: `E_ACCESSDENIED (0x80070005)`→"Access is denied."; logon-failure HRESULTs (`SCHED_E_ACCOUNT_INFORMATION_NOT_SET`, `0x8007052E`)→"The user name or password is incorrect."; unmapped HRESULTs→the `excepinfo` description text (readable, secret-free) |
| 10 | **Never-raise, never-leak totality**: `register_task`/`delete_task`/`read_schedule` keep their `(ok, message)` / dataclass contracts — no `com_error`/`ImportError` escapes; pywin32 missing from a frozen build maps to a named canonical message (the `_MSG_NO_POWERSHELL` analogue), classifier-visible | try/except floors at the `task_com` boundary |
| 11 | Action shape byte-compatible: `_build_action_args` reused verbatim (`--sftp` conditional, `--source scheduled` always — D2c, python-mode `-m src.main` + workdirs); `action_path` = first action's `Execute` | Unchanged builder |
| 12 | Root folder `\`, bare name, `TASK_CREATE_OR_UPDATE` (re-register over an existing task — including one created by the shipped PS path — must succeed) | Explicit flag |
| 13 | **The adapter's elevated-retry trigger**: `WindowsTaskScheduler.delete` retries elevated only when the failure message contains `"access is denied"` (`src/scheduler/__init__.py:141`) — the COM delete's `E_ACCESSDENIED` must route through row 9's table so that substring survives, pinned both ways (retry fires on COM-shaped access-denied; does not fire on other HRESULTs) | Same canonical table |
| 14 | Password hygiene: never argv, never parent env, never logged, never in messages; the in-process BSTR is an improvement (child-env mechanism disappears on the direct path). The **direct-path registration timeout** (new, 120s worker+join) cannot cancel COM — on timeout the worker may still complete and still holds the password; resolution goes through `_confirm_registration` with hedged copy (mirror of `_MSG_ELEVATION_TIMEOUT` semantics), never a bare "failed" over a task that may now exist | Leak-closure sweeps re-run verbatim; the leaked-worker password lifetime named in the security dimension |
| 15 | **Elevated-target change, named (Round-1 security blocker)**: the UAC-elevated binary moves from immutable System32 `powershell.exe` to **our own exe** — which lives in user-writable paths (Downloads one-file) until Slice 3 installs to Program Files and Slice 5 signs it. A same-user process could swap the image between launch and UAC consent (TOCTOU). Accepted with eyes open: UAC is not a Microsoft-defined security boundary, the attacker in that position already runs as this user, `_TRANSIENT_LOCATION_WARNING` coaches the population, and Slices 3+5 restore on-disk integrity (ACLs + signature). Surfaced in the owner-decisions list; DECISIONS entry at Slice-1b land | — |
| 16 | Validators before any OS call; fresh-env + `system_binary` System32 pinning at surviving subprocess sites (elevation launch, icacls); threading: `CoInitialize`/`CoUninitialize` per off-UI-thread entry (fails only in the packed app if forgotten — QA-covered) | Unchanged / context-managed |

**The elevated child** (fork-risk decision unchanged, hardened per Round 1): `elevation.run_elevated_powershell` generalises to `run_elevated(exe, args)` launching `DistrictSync.exe --elevated-apply <req> <res>` (dev: `python -m src.main ...`) via the same `ShellExecuteExW("runas")`, bounded wait, DPAPI-CurrentUser request, atomic JSON result, `DSYNC_DIFFERENT_ACCOUNT` fail-closed sentinel, orphan sweep. **Dispatch-first, minimal-child (Round-1 security blocker)**: `--elevated-apply` is recognised by an argv check at the very top of `_cli`, **before** `migrate_legacy_data_dir()`, console attach, log-sink configuration and `sweep_orphans()` — the elevated child performs *none* of the CLI preamble's filesystem side effects (today's PS child touches only the request/result files; ours must match). Child behaviour: read request → DPAPI-unseal (same-SID only) → validate payload shape → `task_com` register/delete → atomic result write → exit. Refusal table (no file / wrong SID / malformed / oversized → sentinel result, exit 0) pinned with positive twin. Diagnostics ride the result file's message, never a log sink the child would have to configure.

Also in scope: `delete_task` → COM `Folder.DeleteTask` (ROADMAP consistency follow-up; `schtasks.exe` then leaves the `system_binary` allowlist). `_clean_ps_stderr`/CLIXML apparatus retires with its transport; absence pins (`powershell.exe`, `-EncodedCommand`, `gencache`) land in the slice that completes the retirement, so a half-migration is unrepresentable.

**Dependency**: `pywin32 ; sys_platform == "win32"`; lazy imports under platform guards (`scheduler/__init__.py:37` imports `windows` on every OS); hidden-imports `win32com`, `win32com.client`, `pythoncom`, `win32timezone` (the dynamic-import trap), Windows row only. `comtypes` rejected (runtime code-gen cache under frozen exes). Bandit skips re-scoped in CLAUDE.md.

### B. Distribution: `--onedir` + bundled Flet client + Inno Setup installer (Windows only)

**Layout**: `flet pack --onedir` (native flag, verified in flet-cli 0.85.3's `pack.py`; the old spike premise — fighting a hardcoded `--onefile` — was wrong and is retired) → `dist/DistrictSync/DistrictSync.exe` + `_internal/`. `sys.frozen` stays True, `sys._MEIPASS` → the persistent `_internal`, so `paths.bundle_root()`, the launcher chdir, icons and version stamping keep working (each consumer verified at file:line in the plan-inputs read); "deleted on exit" prose corrected where it rots.

**The client leaves the profile (Goal 4)**: at build time the Windows job extracts `flet-windows.zip` into the bundle (`_internal/flet_view/`); the frozen Windows launcher sets `FLET_VIEW_PATH` to that dir when it exists (env respected at `flet_desktop/__init__.py:367-377`, before any cache logic). Consequences, all deliberate: no first-launch extraction, no `~/.flet` consult, `flet.exe` becomes a signable shipped file, the quarantine-brick mode is unreachable, and **the offline-embed smoke phase is redesigned** — its current pass-condition *is* the profile drop ("`~/.flet` repopulated ⇒ client embedded"); the new assertion is "window boots with `~/.flet` absent, `FLET_CLIENT_URL` unreachable, and `~/.flet` **still absent after**" (a strictly stronger offline proof, plus its negative twin). Linux/macOS keep today's cache behaviour (one-file, unchanged rows).

**Installer**: Inno Setup — new tracked `installer/DistrictSync.iss`. `{autopf}\DistrictSync`, per-machine admin install (district servers; the stable task path); `AppId` GUID fixed forever; version via `/DAppVersion=` from the tag; `districtsync.ico` exe/installer icon + `myblueprint.ico` window icon roles preserved (DECISIONS 2026-07-08). **Launch-after-install carries `runasoriginaluser`** (Round-1 catch: default Inno `[Run] postinstall` runs with the elevated installer's token — violating "the app itself never runs elevated" and, worse, seeding an elevated first-run's profile writes) — flags: `postinstall nowait skipifsilent runasoriginaluser`. Uninstaller removes the install dir only — never `%LOCALAPPDATA%\DistrictSync`, never the keyring, never the scheduled task (config-mutable name; deleting a nightly on uninstall-for-reinstall is data-loss-shaped; doc note instead, revisit on field evidence). `ISCC` invoked by resolved path (present on `windows-latest`/2025 images, but not warranted on PATH).

**CI/release plumbing — reconciled per-slice, never split across a tag window** (Round-1 blocker): Slice 2 flips the Windows row to onedir *and in the same PR* teaches `flet-pack.yml` to zip the tree (`DistrictSync-windows.zip`) and `release.yml` to consume it (mv/checksums/`files:`/body row) — a tag between Slices 2 and 3 publishes the zip, correctly. Slice 3 swaps the zip for the installer end-to-end: `ISCC` step → **installed-copy smoke** (`/VERYSILENT /DIR=` temp install, re-run version + dry-run phases against the installed exe) → upload `DistrictSync-Setup.exe`. `resolve_artifact` gains inner-exe candidates **ordered before** the bare `dist/DistrictSync` candidate (or switches to `is_file()` — the existing candidate 2 matches the onedir *directory* first otherwise; unit rows either way).

**Artifact naming (owner decision at approval, corrected per Round 1)**: the permanent permalink `releases/latest/download/DistrictSync-windows.exe` (partner emails, `faq.md`, `headless-sftp-setup.md`) 404s under any renamed asset — a differently-named bridge zip bridges nothing. The one option that preserves links: **dual-publish the installer bytes as both `DistrictSync-Setup.exe` and legacy-named `DistrictSync-windows.exe` for one cycle**, retiring the legacy name by DECISIONS entry once partner docs/emails migrate. The onedir **zip** has exactly one real consumer — the headless/no-admin population in `headless-sftp-setup.md` (they hand-register `schtasks` against a bare exe path and may not have install rights); if the owner wants that population served, the zip stays and its lifetime tracks their doc migration, not "one cycle"; if not, it dies at Slice 3 and that doc migrates to the installed path. Both variants priced in the approval ask.

**Upgrade/coexistence honesty**: existing installs ran from arbitrary paths with tasks pointing at them; the installer can't find those exes, so the old task keeps firing the old exe until a Settings Save re-registers (the reconcile already does this). Docs + QA row (upgrade-over-install; old-task-still-fires; **non-admin operator runs the wizard + a manual Convert from the real Program Files install** — the temp-dir CI install can't catch a residual install-dir write, an admin runner masks ACLs).

**Interim-shape constraint, named (Round 1)**: between Slice 1b and Slice 3 the elevated child is an unsigned one-file exe self-extracting under an elevated token — a *worse* transient AV shape than today's System32 PowerShell child. Acceptable only because **no release tag occurs between Slice 1b and Slice 3** (already implied by certification-before-tag; now an explicit sequencing constraint the slices' land-records assert).

### C. Signing (Slice 5 — starts when the account exists)

Azure Artifact Signing steps for the **inner exe + bundled `flet.exe`** (before ISCC) and the **installer** (after), plus `signtool verify` asserts, landed and verified live in one slice *once the owner has created the account* — not dormant-and-never-executed (Round-1: guarded steps that have never run aren't known-good; "flip a secret" day would really be "debug never-run steps" day). Until then the release body keeps its "not yet code-signed" line and ROADMAP/PLAT-4 points here. Recommendation to the owner rides the approval ask: create the account during Slices 1–3 so Slice 5 lands before the release tag and v-next ships signed.

**Alternatives considered**: `schtasks /XML` (child process + the 2026-06-25 regression class — rejected); PS bootstrap kept for the elevated child (forks register logic across languages — rejected); `comtypes` (frozen code-gen cache — rejected); MSI/WiX (heavier, no district-visible gain — rejected); per-user install (re-arms profile-execution patterns, unstable-ish paths — rejected); signing without behaviour change (signed persistence chains still trip ATC — insufficient alone); onedir without `FLET_VIEW_PATH` (leaves standing the exact quarantined behaviour — rejected at Round 1).

## Architecture & holistic fit

- **Codebase fit**: the `Scheduler` protocol boundary is why A lands without touching any UI consumer; `task_com.py` slots under `windows.py` as the platform-private engine (UI ↔ business ↔ platform layering). The elevated-child redesign strengthens single-source: registration logic exists once, in Python, on both sides of the UAC boundary. Goal 4 additionally deletes a whole class of runtime mutation (the app stops writing executables anywhere at runtime).
- **Product fit**: districts get the app not being called malware, an installer (the shape IT expects), faster launch, a nightly that survives exe-tidying, and — via Goal 4 — immunity to the quarantine-brick incident class. ("The Installer" stays a persona in PRODUCT.md; this plan says "the installer artifact".)
- **Quality dimensions**: `security` (elevation redesign + dispatch-first child + TOCTOU trade named + allowlist shrink), `reliability-resilience` (registration timeout via read-back hedge, HRESULT-keyed tri-state, quarantine-brick unreachable), `testing` (pin-set re-authored with behavioural parity; absence pins; smoke redesign with negative twin), `observability-ops` (canonical messages preserved; size-summary semantics redefined installer-vs-install-dir), `privacy` (no new PII; child result is message-only), `maintainability-structure` (CLIXML apparatus deleted; one platform-private module).
- **Future-proofing**: installer opens winget/auto-update doors without committing; `task_com.py` is where a later gMSA principal lands; `FLET_VIEW_PATH` decouples us from flet's cache policy ahead of the Flet-1.0-stable upgrade item.

## Affected files

| Path | Change |
|---|---|
| `src/scheduler/task_com.py` | **New** — COM session ctx, task-definition builder (rows 1–6, 11–12), HRESULT table (rows 9, 13), bounded-call helper (rows 8, 14) |
| `src/scheduler/windows.py` | Register/read/delete on `task_com`; PS builders + `-EncodedCommand` + CLIXML retired; constants/orchestration/validators unchanged |
| `src/scheduler/elevation.py` | `run_elevated(exe, args)` generalisation; handshake unchanged |
| `src/main.py` | `--elevated-apply` **dispatch-first** (argv check above the CLI preamble), minimal child, refusal table |
| `requirements.txt` | `pywin32 ; sys_platform == "win32"` |
| `Makefile` / `.github/workflows/flet-pack.yml` | `--onedir`; pywin32 hidden-imports (Win row); client-extract + `FLET_VIEW_PATH` bundle step; zip step (S2); ISCC by resolved path + installed-copy smoke (S3); signing steps (S5) |
| `.github/workflows/release.yml` | S2: consume the zip; S3: installer + dual-publish legacy name; checksums/`files:`/body per slice |
| `installer/DistrictSync.iss` | **New** — per-machine, AppId pinned, `runasoriginaluser` launch, profile-preserving uninstall |
| `src/ui_flet/launcher.py` | Set `FLET_VIEW_PATH` when frozen + bundled client dir exists; prose fixes |
| `scripts/ci_flet_pack_smoke.py` | `resolve_artifact` inner-exe-first candidates / `is_file()`; offline-embed phase redesign (+ negative twin); budget comments |
| `tests/test_schedulers.py` / `test_scheduler_runas.py` / `test_scheduler_elevation.py` | Pin-set re-authored per rows 1–16; transport-absence pins |
| `tests/test_ci_flet_pack_smoke.py` | Resolver + redesigned-smoke rows |
| `src/utils/paths.py` / `src/etl/pipeline.py` docstrings · `docs/partner/installation.md` · `docs/partner/troubleshooting.md` · **`docs/partner/faq.md`** · **`docs/partner/headless-sftp-setup.md`** · `docs/developer/release.md` (permalinks) · `docs/developer/qa-checklist.md` (+ preamble count) · `docs/claugentic-PRODUCT_SPEC.md` / `PRODUCT.md` install-shaped claims · CLAUDE.md · ARCHITECTURE_TREE · DECISIONS · CHANGELOG | Each rides the slice that makes it true |

## Risks & mitigations

- **CSV output: zero.** No ETL touch; SD74 golden gates every slice.
- **LogonType regression class** (2026-06-25 scar): row-2 explicit-constant pins + S4U absence pin + the mandatory live QA walk — logged-off fire must confirm (a) SFTP delivered (keyring readable in the batch session), (b) run record `source=scheduled`, (c) `LastTaskResult` 0; plus the password+Limited variant and `TASK_CREATE_OR_UPDATE` over a v3.x PS-registered task.
- **COM behind mocks ≠ COM live**: the QA walk is the live gate, carried as Slice-1a/1b acceptance criteria.
- **Hung-RPC leaked worker** (row 8) and **timeout-after-completion** (row 14): read-back-hedged copy, WARN logs, named trades.
- **Elevated-target TOCTOU** (row 15): named, accepted, mitigated by Slices 3+5; owner sees it at approval.
- **flet-cli `--onedir` + embed hook interaction**: the Slice-2 spike is now confirmation-shaped (native flag exists) but still first — evidence before plumbing.
- **Partner permalink break**: solved by dual-publish, owner decides the legacy name's retirement cycle.
- **Headless/no-admin population**: zip decision explicitly owner-priced.
- **Interim AV shape S1b→S3**: no-tag constraint, asserted in land records.
- **Bitdefender may still flag v-next**: possible; the CHANGELOG claims "removes the behaviours commonly flagged", never "fixed AV".

## Test strategy

Behaviour-equivalence over transport: every row 1–16 gets a COM-object pin with the same name/intent as the PS-text pin it replaces; absence pins for the retired transport land where retirement completes. Elevation outcome-ladder tests keep declined/timeout/no-result/different-account/ok-confirmed with the child now our exe; leak-closure sweeps verbatim; `--elevated-apply` refusal table + positive twin; row-13 both-ways retry pin. Smoke: resolver units; redesigned offline proof + negative twin; installed-copy smoke. Deterministic gates per slice (full suite, SD74 golden, 11/11 configs, tree, ruff/mypy/bandit, email scan, three-OS CI read + quoted). Live gates: the QA walk rows named above + non-admin-from-Program-Files + upgrade rows. **Certification (D-0037-6) + the Phase-2 ordering question** are owner decisions surfaced at approval (below), scheduled before this plan's release tag.

## Owner decisions surfaced at approval

1. **D-0037-6's Phase-2 half** (Round-1 process blocker): the recorded decision says Phase 2 (mapping creator) lands before the next non-exception release. This plan's release would precede Phase 2. Choose: (a) sequence this release after Phase 2, or (b) a dated DECISIONS entry superseding the ordering for this release on AV-incident grounds, stating whether the certification pass runs once (Phase 1 + distribution now) with a second owed after Phase 2, or is being spent now.
2. **Artifact naming**: dual-publish `DistrictSync-Setup.exe` + legacy `DistrictSync-windows.exe` (same bytes) for one cycle — approve, and set the retirement cycle.
3. **The headless zip**: keep serving the no-admin/hand-registered population with `DistrictSync-windows.zip` (lifetime = their doc migration), or drop it at Slice 3.
4. **Elevated-target TOCTOU trade** (row 15): acknowledge.
5. **Signing account timing**: create the Azure Artifact Signing account during Slices 1–3 so Slice 5 lands verified before the tag (recommended), or ship v-next unsigned and sign in the following release.

## Decomposition (slices — each = one complete PR to main)

- [ ] **Slice 1a — COM read + delete** · `task_com.py` (session ctx, HRESULT table, bounded reads), `read_schedule` + `delete_task` on COM, rows 4–6, 8–10, 13 pinned, pywin32 dep + hidden-imports, adapter retry pin, schtasks retirement + allowlist shrink · lands complete because register still rides the proven PS path (no half-rewritten security boundary), the per-nav-click PowerShell spawn — the highest-frequency AV surface — dies here, and the read/delete pin halves are re-authored in the same PR.
- [ ] **Slice 1b — COM registration + elevated self-child** · direct + elevated registration on `task_com`, `elevation.py` generalisation, dispatch-first `--elevated-apply` + refusal table, rows 1–3, 11–12, 14–16, full PS/CLIXML retirement + transport-absence pins, elevation test re-author, live QA walk rows · lands complete because the absence pins make half-migration unrepresentable and the QA walk is in the acceptance criteria, not advice.
- [ ] **Slice 2 — onedir + bundled client + release bridge** · `flet pack --onedir` spike-then-adopt, client extract + `FLET_VIEW_PATH`, offline-smoke redesign (+ twin), `resolve_artifact` fix + units, **flet-pack.yml zip + release.yml consuming it in this same PR**, one-file prose corrections · lands complete because a tag on the day it merges publishes a working zip artifact end-to-end.
- [ ] **Slice 3 — Inno installer + release contract + docs/QA** · `DistrictSync.iss`, ISCC + installed-copy smoke, dual-publish naming per decision 2 (+ zip per decision 3), partner-doc rewrites (installation/troubleshooting/faq/headless), QA install/upgrade/uninstall/non-admin rows, ROADMAP AV-entry closure note · lands complete because the next tag ships the installer with evidence and every named doc rode along.
- [ ] **Slice 4 — release truthing + certification prep** · CHANGELOG, release-body rewrite, `docs/developer/release.md` permalink section, DECISIONS entries (rows 15, naming, D-0037-6 outcome), qa-checklist preamble count · lands complete because it is the paperwork slice the certification pass then walks.
- [ ] **Slice 5 — signing (gated on the account existing)** · sign inner exe + `flet.exe` + installer, `signtool verify` asserts, conditional "not yet signed" copy retirement · lands complete because its steps execute and verify live in this PR — never dormant.

**Ordering:** 1a → 1b (independent of packaging) ; 2 → 3 → 4 sequential; 5 whenever the account exists (ideally before 4 closes). **No release tag between 1b and 3** (interim-shape constraint). Certification pass (per decision 1) after the last landed slice, before the tag.

## Review

**Round 1 — 2026-08-05, three-lens adversarial panel (security/elevation · packaging/AV-efficacy · process/sizing), all `CHANGES REQUIRED`, all findings evidence-verified against the repo.** Blockers, all incorporated: elevated child inherited `main.py`'s CLI preamble as elevated side effects → dispatch-first minimal child (§A); elevated-target System32→own-exe TOCTOU trade unnamed → row 15 + owner decision 4; adapter delete-retry substring dependency unpinned → row 13; **onedir does not remove the profile drop-and-execute (the exact quarantined behaviour)** → Goal 4 `FLET_VIEW_PATH` redesign + offline-smoke redesign; Slice 2→3 tag window broke `publish-release` → release bridge folded into Slice 2; D-0037-6's Phase-2 ordering half unaddressed → owner decision 1; Slice 1 over one-session calibration → split 1a/1b. Shoulds incorporated: native `flet pack --onedir` (spike premise corrected), `resolve_artifact` candidate-order trap, dual-publish-legacy-name as the only real permalink bridge + headless-zip consumer identification, Inno `runasoriginaluser`, ISCC resolved path, non-admin Program Files QA row, interim-shape no-tag constraint, dormant-signing over-promise → gated Slice 5, row-9 factual correction (no password-incorrect branch in `setup_errors`), gencache/EnsureDispatch absence pin, direct-timeout read-back hedge, expanded logged-off QA evidence, partner-doc rows (faq/headless-sftp), unmapped-HRESULT/pywin32-missing totality row 10. Panel notables preserved: fail-closed elevation ladder, uninstaller posture, Linux/macOS-unchanged forcing, scope discipline (0036 not annexed), plan-committed-at-draft.

**Verdict after incorporation: pending a Round-2 gate check** (the synthesizer-gate pass runs when the owner approves the direction — the structural changes above are large enough that approval should precede re-verification effort).

## Spec

*(Per slice, after Review passes and the owner answers decisions 1–5.)*
