# 0041 — Legit distribution: COM scheduler + onedir installer (+ dormant signing hooks)

- **Status:** Draft
- **Roadmap item:** `docs/claugentic-ROADMAP.md` — the `[AV / DISTRIBUTION]` backlog entry (2026-08-04 Bitdefender ATC incident), its fixes (2) and (3); overlaps the queued `0036 bigger bets — startup/installer spike`.
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-06-15 XML / 2026-06-25 EncodedCommand + CLIXML / 2026-07-08 D4 read-back + D5 elevation / 2026-07-28 light-flavor + no-UPX / 2026-07-30 land gate / 2026-07-31 v3.9.0 override) · plan 0038 (house style) · `.claude/plans/0032-ui-ux-sweep-proposal.md` (installer spike detail)

## Problem

DistrictSync's Windows artifact behaves like malware to behavioural antivirus, and on 2026-08-04 Bitdefender ATC acted on that: it blocked the exe and quarantined the Flet client's `flet.exe` out of `%USERPROFILE%\.flet\`, bricking the window on every later launch (ROADMAP `[AV / DISTRIBUTION]`, confirmed live by the owner). Two of our own behaviours produce the signature:

1. **`powershell.exe -EncodedCommand <base64>`** (`src/scheduler/windows.py:591-596` for register, `:1005` read-back, elevation bootstrap `:696`) — base64-encoded PowerShell creating a scheduled task under UAC elevation is the textbook persistence chain, explicitly weighted by Defender ASR ("potentially obfuscated scripts"), Bitdefender ATC and most EDR. The encoding exists only because PS 5.1 silently no-ops a multi-line `try/catch` piped to `-Command -` (DECISIONS 2026-06-25) — a workaround for a transport we can stop using entirely.
2. **A ~96 MB unsigned one-file exe that self-extracts into `%TEMP%` and executes from there** (`flet pack`'s `--onefile` default), then on first launch **drops and executes a second unsigned exe into the user profile** (`flet_desktop.ensure_client_cached()` → `~/.flet/client/...`). The ROADMAP entry calls this "the most malware-shaped thing the product does", and it is also the exact chain the quarantine broke.

The owner's direction (2026-08-05): make the software *legitimately shaped* rather than working around any one engine — "idk what antivirus partners use … lets work around it and try to do our best to make it legit." Signing at ~$10/mo (Azure Artifact Signing) is approved in principle; free routes don't fit a closed-source org product.

Secondary problems the same work discharges:

- The scheduled task bakes in whatever path the exe was launched from — usually `Downloads` (`setup.py:1414-1415` bakes `sys.executable`; the `_TRANSIENT_LOCATION_WARNING` at `setup.py:151-155` exists *because* of this). An installer gives the task a stable `Program Files` path and largely obsoletes the warning.
- Schedule registration has **no subprocess timeout** (ROADMAP "Robustness under bad conditions") — Setup can hang with both buttons greyed. An in-process COM call gets an explicit bound as part of the rewrite.
- Every nav-click schedule probe spawns a PowerShell process (`read_schedule`, 10s bound) — in-process COM removes a process-spawn per click and the console-flash class with it.
- One-file's runtime extraction is the slow first paint (7.1s measured in 0032) and the 150s/120s smoke budgets exist to absorb it.

## Goals / Non-goals

**Goals**

1. Zero `powershell.exe` child processes in the scheduler's steady-state paths (register, read-back, delete) — Task Scheduler COM API (`pywin32`) in-process, with **every current behavioural guarantee preserved and re-pinned** (the full inventory is in Approach → "The contract being carried").
2. The elevated (stored-password / RunLevel Highest) path stops launching PowerShell too: the elevated child becomes **DistrictSync itself** in a locked-down `--elevated-apply` mode running the *same* Python registration code — preserving the D5 DPAPI handshake unchanged and the single-source property the PS `_register_body` pin protects today.
3. The Windows artifact becomes **`--onedir` wrapped in an Inno Setup installer**: installs to `Program Files\DistrictSync`, Start-menu entry, uninstaller, in-place upgrades that keep the registered task's exe path valid. No self-extraction at runtime, no bootloader re-exec.
4. CI builds, smokes and releases the installer artifact with the same evidence discipline as today (three-OS matrix, offline-embed proof, zero-orphan close, CLI smokes, checksums).
5. Signing **hooks** wired in CI but dormant (skip-if-no-secret), so the day the Azure Artifact Signing account exists, signing the inner exe + installer is a secrets change, not an engineering slice.
6. Linux/macOS artifacts unchanged (single binaries, byte-comparable pipeline).

**Non-goals**

- Procuring the signing account/certificate (owner/business action; PLAT-4 stays deferred until then).
- MSI / winget / auto-update / "update available" signals (update checks need network egress beyond the three allowlisted SFTP hosts — a posture change the owner must approve separately; DECISIONS-recorded deferral).
- The Flet-client self-heal stat-check (owner-deferred 2026-08-04; onedir + signing shrink its blast radius but it stays a separate ROADMAP item).
- Migrating `crontab`/Linux scheduling; the `Scheduler` protocol and cron adapter are untouched.
- Single-instance mutex, splash screen, and the rest of the 0036 startup spike (YAGNI here; the installer slice deliberately does not annex them).
- Re-litigating no-UPX (DECISIONS 2026-07-28 — UPX stays off; it triggers AV and undermines signing).

## Approach

### A. Scheduler: PowerShell → Task Scheduler COM (pywin2), single-sourced across the elevation boundary

Replace the three `-EncodedCommand` invocations with in-process COM (`win32com.client.Dispatch("Schedule.Service")`). One new pure-Windows module, `src/scheduler/task_com.py`, owns COM session setup + the task-definition builder + HRESULT mapping; `windows.py` keeps the public API, message contract and orchestration (validators → COM call → read-back confirm) so every consumer (`get_scheduler()`, `schedule_probe`, `setup_errors`, the five `_MSG_*` constants) is untouched.

**The contract being carried** (each item is a pin in the rewritten tests; sources: current script text + DECISIONS history):

| # | Guarantee | COM form |
|---|---|---|
| 1 | Settings quintet: no catch-up run (`StartWhenAvailable=False`), `IgnoreNew` multiple-instances, `PT2H` execution limit, battery-operation enabled (both flags) | Explicit `TaskDefinition.Settings` — **COM defaults differ on all five** (PT72H, battery disallowed) |
| 2 | Logon discipline: explicit `TASK_LOGON_PASSWORD` (pw) / `TASK_LOGON_INTERACTIVE_TOKEN` + Limited (no pw); **S4U unrepresentable**; `run_highest` ignored without a password | `RegisterTaskDefinition(..., logonType=...)` with the literal constants; a test asserts the S4U constant appears nowhere |
| 3 | Credential validation + "Log on as a batch job" grant on the password path | Provided by the Task Scheduler service on `TASK_LOGON_PASSWORD` registration — verified live in the slice's manual QA row (the `setup_errors` batch-logon coaching copy depends on it) |
| 4 | Culture-invariant times | We compose `Trigger.StartBoundary` ourselves as invariant ISO-8601; boundary date = the retired XML's fixed past date (2024-01-01) so output is deterministic and can never interact with catch-up semantics; `read_schedule` serialises COM datetimes back to the same invariant ISO strings `ScheduleReadback` promises |
| 5 | Tri-state read-back: `found=False` **only** on the definitive not-found HRESULT (`0x80070002`/`SCHED_E_TASK_NOT_FOUND`); access-denied, RPC failure, COM-init failure, non-Windows → `found=None` | HRESULT-keyed classification — never localized `FormatMessage` text |
| 6 | Never-run sentinel: PS nulls `LastRunTime` via `Year -gt 1900`; COM reports the 1899-12-30 epoch + `LastTaskResult=267011` (`SCHED_S_TASK_HAS_NOT_RUN`) | Same Year>1900 null + 267011 mapped, or the fired-but-no-record contradiction false-alarms on every never-run task |
| 7 | Success ≠ API-didn't-throw: register/delete confirmed via `read_schedule` (D5's read-back-confirmed invariant), on the elevated path especially | Unchanged orchestration in `windows.py` |
| 8 | Bounded, never-raises probe: today 10s subprocess timeout | COM query runs on a daemon worker with `join(10)`; timeout → `found=None` (a leaked blocked thread on a hung RPC is accepted and logged — strictly better than today's killed subprocess? No: equivalent honesty, no UI wedge; recorded as a known trade) |
| 9 | Message contract: `setup_errors.classify_schedule_error` keys on exact strings ("Access is denied", "The user name or password is incorrect", the five `_MSG_*`) | HRESULT→canonical-English mapping table in `task_com.py` (`E_ACCESSDENIED`→"Access is denied.", `SCHED_E_ACCOUNT_INFORMATION_NOT_SET`/logon-failure HRESULTs→"The user name or password is incorrect.") — mapping by HRESULT, not substring, actually *removes* today's implicit en-locale assumption |
| 10 | Action shape byte-compatible: `Execute`=exe, args = `_build_action_args` verbatim incl. `--sftp` conditional and `--source scheduled` always (D2c), python-mode `-m src.main` + project-root workdir; `action_path` read-back stays the first action's `Execute` | `_build_action_args` reused as-is |
| 11 | Root folder `\`, bare task name, create-or-replace | `GetFolder("\\")` + `TASK_CREATE_OR_UPDATE` |
| 12 | Password hygiene: never argv, never parent env, never logged, never in returned messages | Improves: the password becomes an in-process BSTR argument to `RegisterTaskDefinition` — the child-env mechanism disappears on the direct path entirely |
| 13 | Validators before any OS call; fresh-env discipline at surviving subprocess sites; `system_binary` System32 pinning wherever subprocess remains | Unchanged |
| 14 | Threading: every scheduler call runs off the UI thread (`page.run_thread`) | `CoInitialize`/`CoUninitialize` per entry point (context-managed in `task_com.py`) — forgetting this fails only in the packed app, so the QA walk covers it explicitly |

**The elevated child** (the fork-risk decision): a filtered token cannot register a RunLevel-Highest task, so the password path still needs one elevated *process*. Keeping the PS bootstrap would fork the registration logic across two languages — the exact drift `_register_body` single-sourcing exists to prevent. Instead the elevated child becomes **our own exe**: `elevation.run_elevated_powershell` generalises to `run_elevated(exe, args)` launching `DistrictSync.exe --elevated-apply <req> <res>` (dev mode: `python -m src.main ...`) via the same `ShellExecuteExW("runas")`, bounded wait, DPAPI-CurrentUser request file, atomic JSON result, `DSYNC_DIFFERENT_ACCOUNT` fail-closed sentinel, orphan sweep. The child mode:

- refuses to run unless the request file exists, DPAPI-unseals for this SID, and parses to the known payload shape (fail-closed, exit 0 + sentinel result on any mismatch — mirroring today's bootstrap);
- executes the SAME `task_com` registration/delete functions the direct path uses (single source restored, now in Python);
- never writes the password to the result, argv, or logs; the result file carries `{ok, message}` only;
- is a **named, documented, locked-down CLI mode** (CLAUDE.md exit-code section + `--help` suppression decision recorded) — the one genuinely new attack-surface item, mitigated by "does nothing without a valid same-SID DPAPI file" (an attacker who can plant one is already this user).

Also in scope: `delete_task` migrates from `schtasks.exe` to COM `Folder.DeleteTask` (the ROADMAP consistency follow-up — after which `schtasks.exe` leaves the `system_binary` allowlist), and registration gets the explicit timeout it never had (same worker+join pattern, generous 120s to match `_ELEV_TIMEOUT_S`).

**Dependency**: `pywin32` (`sys_platform == "win32"` marker) — `win32com.client` + `pythoncom`. Imported lazily inside functions under platform guards (`src/scheduler/__init__.py:37` imports `windows` at module level on every OS; a top-level `import win32com` would break Linux/macOS CI outright). PyInstaller: hidden-imports `win32com`, `win32com.client`, `pythoncom`, `win32timezone` (the classic dynamic-import trap), Windows row only. `comtypes` rejected: its runtime code-gen cache misbehaves under frozen exes. `windows.py:4`'s "No third-party dependencies" prose dies. Bandit: the B404/B603/B607 skips stay (elevation + surviving subprocess sites) but the note in CLAUDE.md gets re-scoped.

**What stays PowerShell/subprocess**: nothing in steady state. `_clean_ps_stderr`/`_sanitize_child_message` reduce to `_sanitize_child_message` over the child's JSON message; the CLIXML machinery retires with its transport (tests move from "de-CLIXML works" to "no path can produce CLIXML any more" — an AST/grep pin that `-EncodedCommand` and `powershell.exe` appear nowhere in `src/scheduler/`).

### B. Distribution: `--onedir` + Inno Setup installer (Windows only)

**Layout**: PyInstaller `--onedir` → `dist/DistrictSync/DistrictSync.exe` + `_internal/` (config, assets, embedded Flet client archive all land under the contents dir; `sys.frozen` stays True and `sys._MEIPASS` points at the persistent `_internal`, so `paths.bundle_root()`, the launcher's `chdir`, icon resolution and version stamping all keep working — the packaging read verified each consumer at file:line; only "deleted on exit" prose rots and gets rewritten).

**Spike first (Slice 2 opens with it)**: `flet pack` hardcodes `--onefile` via flet-cli defaults; the slice's first commit proves `--pyinstaller-build-args` can counter it on flet-cli 0.85.3 *with the client-embed hook still riding* (`--assert-embed` over `Analysis-00.toc` is the existing proof and survives onedir unchanged). Fallback if flet-cli refuses: generate once with `flet pack`, then maintain the `.spec` directly (tracked, reviewed) — a decision the spike answers with evidence before anything else lands.

**Installer**: Inno Setup (`ISCC.exe` preinstalled on `windows-latest` runners; free, boring, ubiquitous). New tracked `installer/DistrictSync.iss`:

- `{autopf}\DistrictSync` (per-machine, admin install — district servers; also what makes the task path stable), `AppId` GUID fixed forever so upgrades replace in place; version injected via `/DAppVersion=` from the tag.
- Start-menu shortcut; **no** desktop icon by default; launch-after-install optional.
- Uninstaller removes the install dir only — **never** `%LOCALAPPDATA%\DistrictSync` (the profile: config, history.db, logs belong to the district) and **never** the keyring credential. Uninstall does not delete the scheduled task automatically (the name is config-mutable; deleting someone's nightly on uninstall-for-reinstall would be data-loss-shaped) — it *shows* the standard Inno uninstall prompt plus a doc note; revisit only with field evidence.
- Icon roles preserved (DECISIONS 2026-07-08): `districtsync.ico` = exe-file + installer icon; `myblueprint.ico` = runtime window icon.

**CI/release plumbing** (every break the packaging read enumerated, fixed in the same slice): `ci_flet_pack_smoke.resolve_artifact` gains the inner-exe candidates (`dist/DistrictSync/DistrictSync[.exe]`) + unit rows; Windows-row workflow steps: pack onedir → smoke the *built tree* (GUI + CLI phases unchanged — they're layout-independent once resolution is fixed) → `ISCC` → smoke the *installed* copy (`/VERYSILENT /DIR=` into a temp dir, re-run the version + dry-run phases against it — the install-shape twin) → upload `DistrictSync-Setup.exe`. `release.yml`: Windows `mv` becomes the installer; checksums + `files:` updated; release-body table rewrites the Windows row ("Run the installer; DistrictSync installs to Program Files"). **Artifact naming is an owner decision surfaced at approval**: the permanent permalink `releases/latest/download/DistrictSync-windows.exe` (partner emails) cannot point at an installer without a name change — recommendation: new asset `DistrictSync-Setup.exe`, and keep publishing a `DistrictSync-windows.zip` (zipped onedir) for one release cycle as the bridge, retiring it by DECISIONS entry. Linux/macOS rows byte-identical throughout.

**Upgrade/coexistence honesty**: existing installs ran from arbitrary paths with tasks pointing at them. The installer can't find those exes; the *task* keeps running the old exe until the admin re-registers. Mitigation: docs + the existing Settings reconcile (any Save re-registers with the new path) + the `_TRANSIENT_LOCATION_WARNING` already coaching exactly this population. A QA row walks upgrade-over-install and old-task-still-fires.

**Docs**: `installation.md` rewritten around the installer; `troubleshooting.md:141` embedded-config line; smoke-budget comments (150s/120s) re-labelled conservative; QA checklist gains install/upgrade/uninstall rows; the one-file prose in `paths.py`/`launcher.py`/`main.py`/`pipeline.py` docstrings corrected in the slice that makes them false.

### C. Signing: dormant hooks (Slice 4)

`azure/trusted-signing-action` steps for the inner exe (before ISCC) and the installer (after), each guarded `if: secrets.AZURE_SIGNING_* present`, plus a `signtool verify` assert when they ran. Lands green with no secrets (skipped, loudly labelled in the step summary), flips on the day the owner creates the account. Release-body "not yet code-signed" line becomes conditional. PLAT-4's procurement note stays in ROADMAP pointing here.

**Alternatives considered**: `schtasks /XML` instead of COM (still a child process + the 2026-06-25 `/RU /RP` regression class — rejected); keeping the PS bootstrap for the elevated child only (forks register logic across languages — rejected, see A); `comtypes` (frozen-exe cache writes — rejected); MSI/WiX (heavier authoring for zero district-visible gain — rejected); per-user install `{localappdata}\Programs` (avoids install-time UAC but re-arms the profile-execution AV pattern and unstable-ish paths — rejected); signing-only without behaviour change (signed persistence chains still trip ATC — insufficient alone, the owner's framing agrees).

## Architecture & holistic fit

- **Codebase fit**: the `Scheduler` protocol boundary (`get_scheduler()`, W4a T2.3) is exactly why A is possible without touching any UI consumer — `windows.py` keeps its API + message contract, `task_com.py` slots under it as the platform-private engine, matching the extractor/transformer layering rule (UI ↔ business ↔ platform). The elevated-child redesign *strengthens* single-source (registration logic exists once, in Python, both sides of the UAC boundary) versus today's PS-text-string sharing.
- **Product fit**: districts asked for nothing here; what they get is the app not being called malware, a real installer (the shape IT expects), faster launch, and a nightly task that survives the exe being tidied out of Downloads. The persona note ("The Installer" is a *person* in PRODUCT.md — this plan says "the installer artifact" throughout to avoid the collision).
- **Quality dimensions** (per-slice detail in specs): `security` (elevation boundary redesign, locked-down CLI mode, password-off-argv preserved, allowlist shrink), `reliability-resilience` (registration timeout, read-back discipline carried, HRESULT-keyed tri-state), `testing` (the entire scheduler pin-set re-authored against COM objects — behavioural coverage identical, transport asserts retired; no-vacuous-greens twins throughout), `observability-ops` (same canonical messages, same log discipline, step-summary size table redefined for installer vs install-dir bytes), `privacy` (no new PII surface; child result JSON is message-only), `maintainability-structure` (one platform-private module; CLIXML apparatus deleted, not stranded).
- **Future-proofing**: signing hooks dormant-by-design; the installer opens the winget/auto-update door without committing to it; `task_com.py` is where a later gMSA/service-account principal would land (owner-deferred, named in ROADMAP).

## Affected files

| Path | Change |
|---|---|
| `src/scheduler/task_com.py` | **New** — COM session mgmt (CoInitialize ctx), task-definition builder (settings quintet, principal matrix, trigger boundary), HRESULT→canonical-message table, bounded-call helper |
| `src/scheduler/windows.py` | Register/read/delete re-plumbed onto `task_com`; PS script builders, `-EncodedCommand` transport, CLIXML cleaner retired; message constants + orchestration + validators unchanged |
| `src/scheduler/elevation.py` | `run_elevated_powershell` → `run_elevated(exe, args)`; DPAPI handshake/API unchanged |
| `src/main.py` | `--elevated-apply` locked-down mode (guarded, undocumented in `--help`); exit-code note |
| `requirements.txt` | `pywin32 ; sys_platform == "win32"` |
| `Makefile` / `.github/workflows/flet-pack.yml` | pywin32 hidden-imports (Windows row); `--onedir` args; ISCC step; installed-copy smoke; artifact upload path; signing steps (dormant) |
| `.github/workflows/release.yml` | Windows artifact rename/checksum/`files:`/body-table for the installer (+ bridge zip, one cycle) |
| `installer/DistrictSync.iss` | **New** — per-machine install, AppId pinned, profile-preserving uninstall |
| `scripts/ci_flet_pack_smoke.py` | `resolve_artifact` inner-exe candidates; budget comments |
| `tests/test_schedulers.py`, `tests/test_scheduler_runas.py`, `tests/test_scheduler_elevation.py` | Pin-set re-authored: COM-object asserts (mocked `win32com`) covering rows 1–14; transport pins → absence pins (`powershell.exe`/`-EncodedCommand` nowhere in `src/scheduler/`) |
| `tests/test_ci_flet_pack_smoke.py` | Resolver rows for the onedir shape |
| `src/utils/paths.py`, `src/ui_flet/launcher.py`, `src/etl/pipeline.py` docstrings · `docs/partner/installation.md` · `docs/partner/troubleshooting.md` · `docs/developer/qa-checklist.md` · `docs/developer/release.md` · CLAUDE.md · ARCHITECTURE_TREE · DECISIONS · CHANGELOG | Prose/rows ride the slice that makes them true |

## Risks & mitigations

- **CSV output: zero.** No ETL path is touched; SD74 golden byte-identical is a gate on every slice.
- **The `LogonType` regression class** (the 2026-06-25 scar): mitigated by row 2's explicit-constant pins, the S4U-absence assert, and a *mandatory manual QA row on a real Windows box* — register unattended, log off, confirm the nightly fires with network (SFTP) access. This is the one guarantee unit tests cannot prove.
- **COM behind mocks ≠ COM live**: the pin-set is mocked; the QA walk (register/re-register/change-time/delete, elevated + not, wrong password, logged-off fire) is the live gate, and Slice 1's spec carries it as acceptance criteria, not advice.
- **Hung Task Scheduler RPC leaks a worker thread** (row 8's trade): logged WARN with thread name; accepted — bounded honesty preserved, UI never wedges.
- **flet-cli 0.85.3 may fight `--onedir`**: the Slice-2 spike is first and cheap; the `.spec`-file fallback is named with its cost (owning what flet pack generated).
- **Partner permalink break**: surfaced as an explicit owner decision at approval (naming + bridge-zip recommendation in B).
- **Upgrade-over-scattered-installs**: old tasks keep firing the old exe; reconcile-on-Save + docs + QA row; no silent task rewriting.
- **`pywin32` packaging on 3 OSes**: Windows-only marker + Windows-only hidden-imports; Linux/macOS pack rows prove non-regression in the same PR's CI.
- **Bitdefender may *still* flag v-next**: possible — behaviour change removes the persistence-chain signature but no one can promise a heuristic engine. The plan's honesty bar: CHANGELOG claims "removes the behaviours commonly flagged", never "fixed AV".

## Test strategy

Behaviour-equivalence over transport: every guarantee in table rows 1–14 gets a COM-object pin with the same name/intent as the PS-text pin it replaces (reviewable as a rename, not a rewrite). Absence pins for the retired transport. Elevation flow tests keep the outcome ladder (declined/timeout/no-result/different-account/ok-confirmed) with the child now our exe — argv/DPAPI asserts updated, leak-closure sweeps (password nowhere) re-run verbatim. `--elevated-apply` gets a refusal table (no file / wrong SID / malformed payload / oversized) with positive twin. Smoke-script resolver unit rows. CI: three-OS pack matrix + the new installed-copy smoke are the deterministic gates; the QA-checklist walk on the *installed* exe (new rows: install, upgrade-in-place, uninstall-preserves-profile, task-survives-upgrade, unattended-fires-logged-off) is the live gate. D-0037-6's certification pass is **owed before this plan's release tag** (back in force since 2026-07-31; 3.10.x were field-test exceptions) — scheduled after the last slice, before tagging.

## Decomposition (slices — each = one complete PR to main)

- [ ] **Slice 1 — COM scheduler + elevated self-child** · `task_com.py`, `windows.py` re-plumb, `elevation.py` generalisation, `--elevated-apply`, pywin32 dep + hidden-imports, full pin-set re-author, QA rows for the live walk · lands complete because the public API + message contract are frozen (no consumer edits), the release artifact is untouched (one-file still ships until Slice 3), and the transport-absence pins prevent half-migration.
- [ ] **Slice 2 — onedir spike + build/smoke adaptation** · prove `--onedir` under flet-pack 0.85.3 with embed intact (or adopt the spec-file fallback with evidence), fix `resolve_artifact` + tests, adapt flet-pack.yml Windows row to build+smoke the tree, keep uploading a runnable artifact · lands complete because flet-verify's three-OS matrix is the proof and the release workflow still consumes what it expects (bridge naming until Slice 3 flips it).
- [ ] **Slice 3 — Inno installer + release contract + docs/QA** · `DistrictSync.iss`, ISCC + installed-copy smoke in CI, release.yml artifact/checksum/body changes + bridge zip, installation/troubleshooting rewrite, QA install/upgrade/uninstall rows, one-file prose corrections · lands complete because the next tag ships the installer end-to-end with evidence, and the owner's naming decision is already made at approval.
- [ ] **Slice 4 — dormant signing + release truthing** · guarded signing steps for inner exe + installer, `signtool verify` assert, conditional "not yet signed" copy, ROADMAP/DECISIONS wiring · lands complete because it is green with zero secrets and needs only credentials to activate.

**Ordering:** 1 is independent and highest-risk-first; 2 → 3 are sequential (installer wraps the tree); 4 rides last. The certification pass (D-0037-6) runs after Slice 4, before the release tag.

## Review

*(Stage-3 adversarial panel pending — filled by the review round below.)*

## Spec

*(Per-slice, after Review passes.)*
