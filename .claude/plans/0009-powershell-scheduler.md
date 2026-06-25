# 0009 — Windows scheduler on PowerShell `Register-ScheduledTask` (fix unattended-run regression)

- **Status:** Approved — both slices implemented, gates green + Verify PASS; Slice-1 activation verified live (elevated); logged-off/SFTP operator sign-off pending before merge
- **Roadmap item:** none — regression fix (surfaced 2026-06-25 during SD54 rollout prep). Add a DECISIONS entry on land.
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` · regression commit `872f340` (the `/TR`→`/XML` long-path fix) · prior feature `0e38bbb` (unattended run-as) · `src/scheduler/windows.py` · `tests/test_scheduler_runas.py` · `tests/test_schedulers.py`

## Problem

Registering the Windows daily task **with a password** (the "run whether logged on or not" / unattended path) fails with `ERROR: Access is denied`, even when the process is elevated. This is the **critical-path blocker for SD54's rollout** — districts run DistrictSync unattended on a server via the setup wizard, and the wizard's final step (create scheduled task + SFTP) is exactly where it dies.

**Root cause — confirmed regression, not environment:**
- The long-path fix `872f340` switched registration from inline `schtasks /Create /TR "<cmd>"` to `schtasks /Create /XML <file>`. The `/TR` 261-char cap is real and only affects the **action command** — but the change *also* moved the **principal/credentials** into the XML as `<LogonType>Password</LogonType>` while *still* passing `/RU /RP` on the command line (`src/scheduler/windows.py:200-205`, `:317-319`). That XML-declared-Password + `/RU /RP` credential handoff is what fails.
- Evidence gathered 2026-06-25 on the user's machine (`DESKTOP-IU02J31\shan.peiris`, Microsoft-Account-backed local admin, Win11 Home, PowerShell 5.1):
  - The **no-password** `/XML` path **succeeds** here (registers an `InteractiveToken` task, even un-elevated) → the XML mechanism itself is fine.
  - The user reports the **old `/TR` + `/RU /RP`** path **worked** on this same account → the account/MSA is *not* fundamentally incapable of a stored-credential task.
  - Therefore the failure is isolated to the `/XML` + `<LogonType>Password</LogonType>` + `/RU /RP` combination.
- Secondary defect: the wizard maps **every** "Access is denied" to "Run as administrator" (`src/ui/pages/01_Setup_Wizard.py:136-139`), which misdiagnosed this (the user was already elevated) and sent them in circles.

## Goals / Non-goals

- **Goal:** Restore reliable **unattended** scheduling (runs whether or not the user is logged on, with network access for SFTP) for a supplied Windows password, with no 261-char action-command cap.
- **Goal:** Replace `schtasks.exe` registration with PowerShell's `ScheduledTasks` module (`Register-ScheduledTask` et al.) — the modern Task Scheduler COM API, no length cap, robust credential handling across district server configs (local / domain / MSA).
- **Goal:** Keep the password out of the process command line, out of logs, and off disk (a hardening improvement over the current `/RP <pw>`-in-argv model).
- **Goal:** Fix the wizard's error diagnostics so elevation vs. credential failures are distinguished (kills the misleading "Run as administrator" loop).
- **Goal:** Preserve the public `register_task(...)` signature so callers (`01_Setup_Wizard.py`, `Home.py`) are unchanged.
- **Non-goal:** Migrating `delete_task` / `query_task` off `schtasks.exe` — they work, have no length/credential issue (read-only + name-only), and are tested. Consistency migration is a possible ROADMAP follow-up, not this slice.
- **Non-goal:** Touching the Linux/cron path (`src/scheduler/linux.py`) — unaffected.
- **Non-goal:** Any change to the ETL pipeline — **SD74 snapshot must stay byte-identical** (scheduler is not in the ETL path).
- **Non-goal:** The SD54 duplicate-students / email-collision data issues — separate track (blocked on the Enhanced demographic extract).

## Approach

Rewrite `register_task` to build and invoke a PowerShell script that uses the `ScheduledTasks` cmdlets. **Secure invocation contract:**

- Spawn `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command -` with the **script piped via stdin** (`subprocess.run(..., input=script, ...)`). No script file on disk.
- Pass **all dynamic values via the child process environment** (a copy of `os.environ` with namespaced keys added), never interpolated into the script text — this eliminates PowerShell string-injection from district paths *and* keeps the password off argv:
  - `DSYNC_TASKNAME`, `DSYNC_USER`, `DSYNC_RUNTIME`, `DSYNC_EXE`, `DSYNC_ARGS`, `DSYNC_WORKDIR`, and (password path only) `DSYNC_TASK_PW`.
- The script is a **fixed, auditable string** that only references `$env:DSYNC_*`. It never echoes `$env:DSYNC_TASK_PW`.

**Script shape (password path) — LogonType forced via an EXPLICIT principal, not parameter-set inference (review req #1):**
```powershell
$ErrorActionPreference = 'Stop'
try {
  $act = New-ScheduledTaskAction -Execute $env:DSYNC_EXE -Argument $env:DSYNC_ARGS -WorkingDirectory $env:DSYNC_WORKDIR
  $at  = [DateTime]::ParseExact($env:DSYNC_RUNTIME,'HH:mm',[System.Globalization.CultureInfo]::InvariantCulture)
  $trg = New-ScheduledTaskTrigger -Daily -At $at
  $set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
           -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
           -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable:$false
  $prn  = New-ScheduledTaskPrincipal -UserId $env:DSYNC_USER -LogonType Password -RunLevel Highest
  $task = New-ScheduledTask -Action $act -Trigger $trg -Settings $set -Principal $prn
  Register-ScheduledTask -TaskName $env:DSYNC_TASKNAME -InputObject $task -User $env:DSYNC_USER -Password $env:DSYNC_TASK_PW -Force | Out-Null
  Write-Output 'DSYNC_OK'
} catch { Write-Error $_.Exception.Message; exit 1 }
```
- **`New-ScheduledTaskPrincipal -LogonType Password -RunLevel Highest` forces `TASK_LOGON_PASSWORD` explicitly** — `Register-ScheduledTask -InputObject $task -User -Password` then stores the credential. This is the documented robust form; we do **not** rely on the `-User/-Password/-RunLevel` top-level parameter-set (the loose-inference path that can silently degrade to `S4U`/`Interactive` — the very failure class the regression taught). `-RunLevel Limited` in the principal when `run_highest=False`. The implementer confirms the exact cmdlet combination live on the box; **user acceptance check #2 (LogonType=Password on the registered task) is the proof it took.**
- **Settings parity with the current XML (review req #2):** `-StartWhenAvailable:$false` preserves the no-catch-up-run guarantee (current XML `<StartWhenAvailable>false</StartWhenAvailable>`); `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries` = current `DisallowStartIfOnBatteries=false`/`StopIfGoingOnBatteries=false`; `ExecutionTimeLimit PT2H` and `MultipleInstances IgnoreNew` preserved. **`StartBoundary` decision:** the trigger's StartBoundary registers from *today's* date (vs the current fixed `2024-01-01`); with `StartWhenAvailable=false` a missed run does **not** catch up, so today-vs-2024 is behaviorally equivalent for the daily fire — documented choice, not an accident.
- **`run_time` is the raw `"HH:mm"` string** in `DSYNC_RUNTIME` (note: `validate_run_time` returns a `(hour, minute)` tuple — do **not** pass the tuple); parsed with `InvariantCulture` so a non-en-US district locale can't break it (review req #3).
- **No-password path (backward-compat parity, review req #5):** omit `DSYNC_TASK_PW`; build `New-ScheduledTaskPrincipal -UserId $env:DSYNC_USER -LogonType Interactive -RunLevel Limited` and `Register-ScheduledTask -InputObject $task -Force` (no `-User/-Password`) → `Interactive` logon (matches today's `InteractiveToken`, **not** `S4U`), `Limited` (LeastPrivilege). **`run_highest` stays ignored without a password** (today's semantics — `windows.py:173-176`); so `run_highest=True`+no-password still yields `Limited`. The wizard's existing "no password → logged-on-only" warning (`01_Setup_Wizard.py:129-133`) stays accurate.
- Success = `DSYNC_OK` on stdout **and** returncode 0; failure surfaces the PowerShell exception text (wrong password, batch-logon-right denied, etc.) to the caller — **subject to the leak guarantee below**.
- **Secret-leak closure (review req #4):** the child `env` is a **fresh copy** — `subprocess.run(..., env={**os.environ, "DSYNC_...": ...})` — `os.environ` is **never** mutated. The script never echoes `$env:DSYNC_TASK_PW`. `register_task` must not log/return any text that could carry the password; a test asserts the **returned stderr and `caplog` contain neither the password value nor the `DSYNC_TASK_PW` literal** on the failure path.
- **Fail-loud on missing PowerShell/module (review req #7):** `subprocess.run` raising `FileNotFoundError` (no `powershell.exe`) and a cmdlet-not-found PowerShell error (no `ScheduledTasks` module, pre-Win8) both map to a distinct actionable message and never crash the wizard — a defined, unit-tested code path.

**Mode detection** (unchanged semantics): frozen exe → `-Execute <exe>`, args without `-m src.main`, workdir = exe parent; Python source → `-Execute <python.exe>`, args `-m src.main …`, workdir = project root.

**Why this fixes it:** `Register-ScheduledTask -User -Password` registers via the Task Scheduler COM API (`RegisterTaskDefinition` with `TASK_LOGON_PASSWORD`), which performs the credential validation + "Log on as a batch job" grant directly and robustly — the step the `schtasks /Create /XML` credential handoff botched. Same mechanism class the working pre-regression `/TR` path used, via a cleaner API.

**PyInstaller note:** invocation is plain `subprocess` to `powershell.exe` (always on PATH on Windows) — **no new hidden imports** and no COM/pywin32 dependency. Confirmed available: PowerShell 5.1, all five `*-ScheduledTask*` cmdlets present.

**Alternatives rejected:**
- *Two-phase `schtasks /Create /XML` then `/Change /RU /RP /RL`* — restores the working flag-based credential step but keeps us on the brittle `schtasks` CLI and a temp XML file; PowerShell is cleaner and structured. (Was the fallback; user chose PowerShell.)
- *Patch the XML `<LogonType>Password</LogonType>` + `/RU /RP` in place* — exact failure cause inside `schtasks` is opaque; highest risk of not fully fixing.
- *`S4U` logon type* — runs logged-off **without** storing a password, but **cannot access the network** → breaks SFTP. Rejected.

## Affected files

- `src/scheduler/windows.py` — **rewrite the module docstring (`:1-29`, all `schtasks /Create /XML`/UTF-16/`/RP`)**; replace `_build_task_xml` with `_build_register_script` (fixed PS string) + an env-builder; rewrite `register_task` internals (PowerShell invocation, stdin script, fresh-copy child-env, explicit-principal script, success/error parsing, fail-loud on `FileNotFoundError`/cmdlet-missing). Remove `_redact_cmd` + XML-escaping helpers (password never in argv; values via env, not interpolated). `delete_task`, `query_task`, `current_run_as_user` **unchanged**.
- `src/utils/validators.py` — update `validate_run_as_user` docstring + `_RUN_AS_USER_RE` comment (`:33`, `:79-89`): the value now flows to PowerShell `-User`/principal `-UserId` via env, not a `schtasks /RU` arg list. The validation is **still wanted** (it's interpolated into a PS `-UserId` parameter), but the *rationale* shifts from "no shell metacharacters for the schtasks arg list" to "constrain the principal UserId"; regex unchanged. (Slice 1.)
- `tests/test_scheduler_runas.py` — rewrite for the PowerShell model: password in **fresh child env** (not argv), never in argv/stdin/logs/returned-stderr; LogonType/RunLevel assertions via script content; drop `_redact_cmd` tests; keep `current_run_as_user` + `validate_run_as_user` cases.
- `tests/test_schedulers.py` — remove `TestBuildTaskXML`; rewrite `TestWindowsRegisterTask` for PowerShell argv/stdin/env; **keep** `TestWindowsDeleteTask`, `TestWindowsQueryTask`, `TestLinuxRegisterCron` unchanged.
- `src/ui/pages/01_Setup_Wizard.py` — add `is_elevated()` use + classify registration errors (Slice 2); replace the blanket line 136-139 message.
- `docs/claugentic-ARCHITECTURE_TREE.md` — update the `src/scheduler/windows.py` one-liner (PowerShell, not schtasks XML); add `is_elevated()` home if it lands there (Slice 2).
- `docs/claugentic-DECISIONS.md` — dated entry (why PowerShell, why password-via-env, delete/query left on schtasks) **and mark the 2026-06-15 XML entry (`:29-35`) superseded-by-0009** — it asserts the XML path "Verified LIVE … no iteration needed", which is exactly the password case that regressed.
- `docs/claugentic-INVARIANTS.md` — new entry: unattended Windows scheduling requires a **stored-password** logon (`LogonType=Password`), **never `S4U`** (S4U runs logged-off but has no network token → breaks SFTP egress). (Harness, Stage 9.)
- `docs/claugentic-ROADMAP.md` — line tracking the deliberate inconsistency: `delete_task`/`query_task` remain on `schtasks` (possible consistency follow-up).
- `CLAUDE.md` — update the `src/scheduler/windows.py` description in the `## Architecture` fence (currently `schtasks.exe` + `/RU /RP /RL HIGHEST`) to a dense one-liner: PowerShell `Register-ScheduledTask`, password via child env, delete/query still schtasks.

## Risks & mitigations

- **The `LogonType=Password` outcome is the entire fix and must be forced, not inferred** → ship the **explicit `New-ScheduledTaskPrincipal -LogonType Password`** script (not the `-User/-Password/-RunLevel` parameter-set, which can degrade to `S4U`/`Interactive` on some PS 5.1 builds). Implementer verifies the **no-password** path live (no creds needed); the **password/unattended** path is verified by the **user** per the acceptance checklist in §Spec — including a query that the registered task actually shows `LogonType=Password`/`RunLevel=Highest` and a logged-off run whose SFTP upload succeeds. Definition-of-Done gate; do not land before the user confirms.
- **Password in child env var** (visible to same-user elevated processes) → strictly better than today's `/RP <pw>` in argv (visible to *all* users via the process list); var lives only in the spawned child's env, never the parent's; never logged. Documented in DECISIONS.
- **District server PowerShell/module availability** (needs Win8+/Server 2012+ for the ScheduledTasks module) → all supported district servers qualify; note the requirement in docs. Fail-loud: if `powershell.exe` or the cmdlet is missing, surface a clear actionable error.
- **`-ExecutionPolicy Bypass` + stdin `-Command -`** → no script file is written, so no on-disk policy/AV concern; bandit `# nosec B603/B607` with justification (validated inputs, list args, `shell=False`, no string interpolation of untrusted values).
- **SD74 snapshot** → scheduler is outside the ETL path; snapshot unaffected (assert unchanged in verify).
- **Frozen exe** → no new hidden imports (subprocess to a system binary); confirm `make build-win` path unaffected.

## Test strategy

All `subprocess.run` mocked — no real OS scheduler interaction in unit tests. Assert:
- argv = `["powershell"/"powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]`; **the password is not anywhere in argv**.
- the **stdin script** (`input=`) references `$env:DSYNC_TASK_PW` and does **not** contain the literal password; contains `New-ScheduledTaskAction/Trigger/SettingsSet`, an **explicit `New-ScheduledTaskPrincipal -LogonType Password -RunLevel Highest`**, `-StartWhenAvailable:$false`, and `InvariantCulture` in the `ParseExact`.
- the **child `env`** passed to `subprocess.run` carries the password under `DSYNC_TASK_PW` equal to the supplied value, is a **fresh copy** (parent `os.environ` unchanged before/after — assert key absent from `os.environ`).
- **no-password path:** `DSYNC_TASK_PW` absent; principal is `-LogonType Interactive -RunLevel Limited` (**not `S4U`**), no `-Password`; **`run_highest=True` with no password still yields `Limited`** (run_highest stays ignored without a password — guards existing dev-mode callers).
- `run_highest=False` (with password) → principal `-RunLevel Limited`; frozen vs source mode → `-Execute <exe>` (args without `-m`) vs `-Execute <python.exe>` (args `-m src.main …`).
- validation (bad user/task/time) raises **before** any subprocess call.
- **failure path (leak closure):** returncode≠0 surfaces the PowerShell stderr text to the caller, **and the returned message + `caplog` contain neither the password value nor the `DSYNC_TASK_PW` literal** — on both success and failure.
- **fail-loud:** `subprocess.run` raising `FileNotFoundError` → distinct actionable message, no crash; cmdlet-not-found stderr → distinct actionable message.
- Slice 2: the wizard error classifier maps {not-elevated access-denied → run-as-admin}, {elevated access-denied → batch-logon-right / wrong-password hint}, {logon failure → "use account password not PIN / MSA password"}, {else → raw} — keyed off the **canonical message substrings published in Slice 1's spec** (not guessed). `is_elevated()` has its own unit tests (win32 path mocked + non-win32 guard).

Full gate on land: `pytest` (80% cov) + `ruff check`/`format` + `mypy src --exclude src/ui` + `bandit -r src -q` + `make validate-config` + SD74 snapshot unchanged + architecture-tree check.

## Decomposition (slices)

- [x] **Slice 1 — PowerShell registration** (`register_task` + module docstring rewrite, `validators.py` copy, both test files, ARCHITECTURE_TREE/CLAUDE/DECISIONS/INVARIANTS/ROADMAP). Lands complete: fully replaces the broken mechanism, keeps the public signature, leaves delete/query/linux untouched, independently mock-testable + user-verifiable (live). **Deliverable contract:** Slice 1's spec publishes the **canonical failure-message substrings** `register_task` emits, so Slice 2 keys off real strings. **This is the SD54-unblocking slice.**
- [x] **Slice 2 — Wizard diagnostics** (`is_elevated()` **in `src/scheduler/windows.py`** so it's mypy-checked — UI is mypy-excluded — with a non-win32 guard; error classification replacing the blanket "Run as administrator"; its ARCHITECTURE_TREE line). Lands complete; small; keys off Slice 1's published message contract. Improves UX, not SD54-blocking → lands second.

---

## Review  _(filled by plan-reviewer, Stage 3)_

- **Verdict:** **CHANGES REQUIRED** (close — the approach is sound and correctly scoped; the gaps below are about parity, an unproven load-bearing claim, and one secret-leak edge, not about direction).

### Required changes

1. **Pin down the `-User/-Password/-RunLevel` → `LogonType=Password` claim — make the principal explicit, not "confirm live."** This is the single load-bearing assertion of the whole fix (plan §Approach line ~55, §Risks line ~82). `Register-ScheduledTask`'s `-User/-Password` parameter set does store a password credential, but `-RunLevel` lives on the *principal*, and combining a password credential with a run level via loose top-level parameters is exactly the kind of combination that silently degrades (e.g. to `S4U`/`Interactive`) on some PowerShell 5.1 builds. **The plan must specify the `New-ScheduledTaskPrincipal -LogonType Password -RunLevel Highest -UserId $env:DSYNC_USER` form as the primary script (passing `-Principal $prn` + `-Password`), not as a "flag if needed" fallback.** An explicit principal is the documented way to *force* `TASK_LOGON_PASSWORD`; relying on parameter-set inference is the risk the regression already taught us. Keep the live user-verification gate, but ship the explicit-principal script so the live test confirms a known-correct construction rather than discovering an inference failure.

2. **Settings parity with the current XML is incomplete — `StartWhenAvailable` is dropped and the catch-up-run guard is lost.** The current XML sets `<StartWhenAvailable>false</StartWhenAvailable>` plus a *fixed past* `StartBoundary` (`2024-01-01`) specifically so the daily task never fires a catch-up run (DECISIONS 2026-06-15, line 34; `windows.py:60`, `:211`). The plan's `New-ScheduledTaskTrigger -Daily -At $at` derives `StartBoundary` from *today* via `[DateTime]::ParseExact`, and the `New-ScheduledTaskSettingsSet` in the script (line ~47-49) omits `-StartWhenAvailable`. Result: behavior diverges from the audited baseline (a missed run *could* catch up). **Add `-StartWhenAvailable:$false` to the SettingsSet and state explicitly what `StartBoundary` will be** (registering with today's date is acceptable, but it must be a documented decision, not an accident). Also confirm `-DontStopIfGoingOnBatteries`/`-AllowStartIfOnBatteries` map to the current `DisallowStartIfOnBatteries=false`/`StopIfGoingOnBatteries=false` (they do — note it) and that `ExecutionTimeLimit PT2H` and `MultipleInstances IgnoreNew` are preserved (they are). This is a regression/parity gap, not a nit — the SD74 snapshot does *not* cover scheduler behavior, so nothing else catches it.

3. **`[DateTime]::ParseExact(...,$null)` is locale-fragile — use `InvariantCulture`.** Passing `$null` as the `IFormatProvider` resolves to the *current culture*; on a district server with a non-en-US locale, `'HH:mm'` parsing of `"03:00"` is usually fine but date *formatting* downstream and any 12/24h ambiguity is not guaranteed. Use `[DateTime]::ParseExact($env:DSYNC_RUNTIME,'HH:mm',[System.Globalization.CultureInfo]::InvariantCulture)`. Minor but it's a correctness-on-other-machines item and free to fix. (Note: `validate_run_time` returns `(hour, minute)` now — the plan correctly passes the raw `run_time` string into the env, so no tuple bug, but say so explicitly so the implementer doesn't pass the tuple.)

4. **Close the password-leak path through PowerShell error text.** The plan guarantees the password is off argv/disk and out of the script body (good), but the `catch { Write-Error $_.Exception.Message }` (script line ~53) surfaces an exception message that, for a *credential* failure, can in some cmdlet/locale builds interpolate the supplied `-User`/value context. The required guarantee must be stronger than "never echoes `$env:DSYNC_TASK_PW`": **add a test asserting the *captured stderr returned to the caller* and `caplog` contain neither the password value nor the `DSYNC_TASK_PW` literal on the failure path**, and ensure `register_task` does not log `result.stderr` without that assertion holding. Also state explicitly that the child `env` dict is a *fresh copy* (`{**os.environ, ...}`) and that `os.environ` is never mutated (the test for this is listed — good — but the approach text should name the copy idiom so the implementer doesn't `os.environ[...] = ...`).

5. **Backward-compat parity for the no-password path must match today's semantics exactly.** Today, no-password → `LogonType=InteractiveToken`, `RunLevel=LeastPrivilege`, and `run_highest` is *ignored* (`windows.py:173-176`). The plan's no-password path uses `-User -RunLevel Limited` with no `-Password` (line ~56). Confirm and assert in tests that (a) this yields `Interactive`/`InteractiveToken` logon (not `S4U`), (b) `run_highest=True` with *no* password still produces `Limited`/LeastPrivilege (i.e., `run_highest` stays ignored without a password — otherwise existing dev-mode callers silently change to elevated tasks), and (c) the wizard's existing "no password → logged-on-only" warning copy (`01_Setup_Wizard.py:129-133`) remains accurate.

6. **Stale `schtasks`-oriented copy in `validators.py` and the no-longer-true module docstring.** `validate_run_as_user`'s docstring and the `_RUN_AS_USER_RE` comment (`validators.py:33`, `:79-89`) justify the regex as "passed to schtasks /RU" — after this change the value flows to PowerShell `-User` via env, so the rationale (no shell metacharacters to break the `schtasks` arg list) shifts (env values aren't shell-parsed, but they *are* interpolated into a PowerShell parameter — the validation is still wanted, the *reason* changes). The plan's "Affected files" omits `validators.py` entirely. Either (a) add a one-line note to the plan that `validators.py` copy is updated to reflect the PowerShell target, or (b) explicitly decide to leave the regex as-is and note why. Also the **module docstring of `windows.py:1-29`** (all about `schtasks /Create /XML`, UTF-16, `/RP`) must be rewritten — it's in the "Affected files" implicitly via the rewrite but call it out so it isn't left describing the old mechanism.

7. **Fail-loud on missing `powershell.exe` / `ScheduledTasks` module must be a defined code path, not just a doc note.** Plan §Risks (line ~84) says "surface a clear actionable error" but the Approach/Test-strategy don't specify *how* it's detected or tested. Add: catch `FileNotFoundError` from `subprocess.run` (powershell.exe absent) and the cmdlet-not-found PowerShell error (module absent on a pre-Win8 box) and map both to a distinct actionable message; add a unit test (mock `subprocess.run` to raise `FileNotFoundError`) asserting the friendly message and that it does not crash the wizard. CLAUDE.md "fail loudly" makes this in-scope, not optional.

### Sizing / completeness check

- **Slice 1 (PowerShell registration + both test files + tree/CLAUDE/DECISIONS):** **Session-sized and lands complete — OK**, with one coupling caveat. It fully replaces the broken mechanism, keeps the public signature, and is independently mock-testable. The rewrite of `tests/test_schedulers.py` (`TestBuildTaskXML` deleted, `TestWindowsRegisterTask` reworked) + `tests/test_scheduler_runas.py` (drop `_redact_cmd` tests, re-assert env/stdin) is mechanical and bounded. **Caveat:** Slice 1 establishes the exact *error strings* that Slice 2's classifier keys off (plan line ~99). That contract must be **named in Slice 1's spec** (the canonical failure-message substrings Slice 1 emits) so Slice 2 isn't built against guessed strings. Add the message-contract to Slice 1's deliverables. With that, Slice 1 is genuinely complete with no debt.
- **Slice 2 (wizard diagnostics: `is_elevated()` + error classification):** **OK as a separate slice — do NOT fold into 1.** There is no existing `is_elevated()` helper anywhere in `src/` (confirmed — no `ctypes`/`IsUserAnAdmin` usage), so Slice 2 introduces a new helper (likely `ctypes.windll.shell32.IsUserAnAdmin()` on win32, with a non-Windows guard) + its tests + the ARCHITECTURE_TREE line for wherever it lands. That's real, self-contained, UI-layer work with its own test surface; keeping it second (UX, not SD54-blocking) is the right call. **Required:** Slice 2 must (a) say where `is_elevated()` lives (UI helper vs `windows.py`; prefer `windows.py` so the UI layer stays thin and it's mypy-checked — UI is mypy-excluded), and (b) add its tree entry. As written, Slice 2's home for `is_elevated()` is unspecified — pin it.
- **Neither slice leaves a half-migration that is itself debt:** leaving `delete_task`/`query_task` on `schtasks` is **sound, not a smell** — they are read-only/name-only, have no length or credential surface, are fully tested, and `query_task`'s LIST-parsing output contract is consumed by `Home.py:56-62` (`next_run_time`/`last_result` keys). Migrating them would *expand* blast radius and risk that parsing contract for zero benefit. The plan correctly scopes this as a possible ROADMAP follow-up. Good call — but **record it as a ROADMAP line on land**, not just a DECISIONS aside, so the deliberate inconsistency is tracked.

### Verification adequacy

The "implementer tests no-password live, user tests credentialed path" split is the right model given Claude has no creds and isn't elevated — but the user-gate is **under-specified to function as a Definition-of-Done gate.** Tighten it in the Spec to these exact, checkable live steps (the gate is not met until the user confirms each):
1. Wizard activation with a real password completes without "Access is denied".
2. `schtasks /Query /TN <task> /XML` (or `Get-ScheduledTask | ... Principal`) shows **`LogonType = Password`** and **`RunLevel = Highest`** — this is the assertion that proves change #1 above actually took, not just that registration returned 0.
3. The task **fires while the registering user is logged off** (or via `Start-ScheduledTask` from a *different* session) AND its run **produces output and the SFTP upload succeeds** (network access under stored-credential logon is the whole point vs the rejected S4U path) — a `Start-ScheduledTask` that runs but can't reach SFTP would be a silent failure of the core requirement.
4. `Last Run Result` is `0` (or `0x0`).
Without step 2 + the logged-off + SFTP-reachable check in step 3, a green wizard could still mask an `S4U`/`Interactive` degradation. Make those four the literal acceptance checklist.

### Harness impact

- **New invariant for `docs/claugentic-INVARIANTS.md` (Stage 9):** "Unattended Windows scheduling requires a *stored-password* logon (`LogonType=Password`/`-Password`), never `S4U` — S4U runs logged-off but has no network token, which breaks the SFTP egress the daily run depends on." This is a non-obvious "must stay true or X breaks" constraint that already bit once (rejected-alternatives, plan line ~68); record it so the next person doesn't "simplify" to S4U.
- **CLAUDE.md** scheduler one-liner + the `## Architecture` fence description of `src/scheduler/windows.py` must change (currently `schtasks.exe` + `/RU /RP /RL HIGHEST`) — already in Affected files; keep it to a dense one-liner (PowerShell `Register-ScheduledTask`, password via child env, delete/query still schtasks).
- **DECISIONS:** the new entry should *supersede-note* the 2026-06-15 XML entry (lines 29-35) rather than just append — that entry now describes a replaced mechanism and is a live source of confusion (it asserts the XML path "Verified LIVE … no iteration needed", which is exactly what regressed for the password case). Mark it superseded-by-0009.
- **No new STANDARD or agent** required; no new `.claude/agents/` role. Doc-budget: condensing the superseded XML DECISIONS entry as above keeps the ledger lean (workflow DoD gate 4).

### Re-review (Stage 3 close — revisions verified)

- **Verdict:** **PASS.**

**7 required changes — all resolved:**
1. ✓ Explicit principal — script (`:50-52`) uses `New-ScheduledTaskPrincipal -LogonType Password -RunLevel Highest` → `New-ScheduledTask -Principal $prn` → `Register-ScheduledTask -InputObject $task -User -Password`; the "parameter-set inference" path is explicitly rejected (`:56`, `:90`).
2. ✓ Settings parity — `-StartWhenAvailable:$false` added (`:49`); StartBoundary today-vs-2024 documented as behaviorally equivalent under no-catch-up (`:57`); battery flags / `PT2H` / `IgnoreNew` mapping stated.
3. ✓ `InvariantCulture` in `ParseExact` (`:45`); raw `"HH:mm"` string passed (tuple-trap called out, `:58`).
4. ✓ Leak closure — `{**os.environ, ...}` fresh-copy idiom named, `os.environ` never mutated, returned-stderr + `caplog` assertion for password value *and* `DSYNC_TASK_PW` literal (`:61`, `:102`, `:106`).
5. ✓ No-password parity — `Interactive`/`Limited`, no `-Password`, `run_highest` stays ignored (asserted `:103`); wizard "logged-on-only" warning confirmed accurate (`:59`, matches `01_Setup_Wizard.py:129-133`).
6. ✓ Stale copy — `windows.py:1-29` docstring rewrite + `validators.py:33,:79-89` copy update (regex unchanged) both in Affected files (`:77-78`); verified those are still the live schtasks-oriented lines.
7. ✓ Fail-loud — `"PowerShell not found"` (FileNotFoundError) + `"ScheduledTasks module not available"` (cmdlet-missing) as defined, unit-tested paths (`:62`, `:107`, Spec `:172-173`).

**Extras — all resolved:** ✓ Slice 1 publishes the canonical failure-message substrings Slice 2 keys off (Spec `:171-174`). ✓ `is_elevated()` pinned to `src/scheduler/windows.py` for mypy coverage (`:115`, `:187`). ✓ The 4 literal live-acceptance checks (no "Access is denied" · `LogonType=Password`/`RunLevel=Highest` query · logged-off run with SFTP reachable · `Last Run Result=0x0`) are the Spec DoD gate (`:180-184`). ✓ Harness items in Affected files: INVARIANTS new entry (`:84`), DECISIONS supersede-note on the 2026-06-15 XML entry (`:83`; that entry's "Verified LIVE … no iteration needed" at DECISIONS `:35` is exactly the regressed password case), ROADMAP line (`:85`).

**Cmdlet soundness (the load-bearing claim):** `Register-ScheduledTask -InputObject $task -User -Password -Force` with a `-Principal`-bearing task object is a **valid, documented combination** — `-User`/`-Password` supply the credential to validate/store while the task object's `LogonType=Password` principal declares the logon type; this is not a mutually-exclusive parameter-set conflict. The explicit-principal form is the correct way to *force* `TASK_LOGON_PASSWORD` rather than infer it. **No blocker.** The live user-acceptance check #2 (registered task shows `LogonType=Password`/`RunLevel=Highest`) remains the correct proof-it-took gate and is retained as a hard DoD gate.

**Sizing/completeness:** Slice 1 — OK (lands complete, public signature preserved, message contract published, no debt). Slice 2 — OK as a separate second slice, `is_elevated()` home now pinned. No split needed.

**Residual blockers:** none. Land gated on the §Spec user live-verification checklist (the credentialed/unattended path cannot be machine-verified by the implementer).

---

## Spec  _(Stage 4 — review changes folded in)_

### Slice 1 — PowerShell registration

**Files & changes**
- `src/scheduler/windows.py`: module docstring rewritten (PowerShell, no XML/UTF-16/`/RP`). New `_build_register_script(*, has_password: bool) -> str` returning the fixed PS string (password vs no-password principal variant). New `_build_env(...) -> dict[str,str]` returning `{**os.environ, "DSYNC_TASKNAME":…, "DSYNC_USER":…, "DSYNC_RUNTIME": run_time, "DSYNC_EXE":…, "DSYNC_ARGS":…, "DSYNC_WORKDIR":…[, "DSYNC_TASK_PW": …]}`. `register_task(...)` signature **unchanged**; internals: validate (as today) → build script + env → `subprocess.run([powershell, -NoProfile, -NonInteractive, -ExecutionPolicy, Bypass, -Command, "-"], input=script, env=child_env, capture_output=True, text=True)` wrapped in `try/except FileNotFoundError`. Delete `_build_task_xml`, `_redact_cmd`, `_xml_escape` import. `delete_task`/`query_task`/`current_run_as_user` untouched.
- `src/utils/validators.py`: docstring/comment copy only (regex unchanged).

**Canonical failure-message substrings `register_task` returns** (the contract Slice 2 keys off — these exact substrings must appear in the returned `message`):
- `"PowerShell not found"` — `FileNotFoundError` (no `powershell.exe`).
- `"ScheduledTasks module not available"` — cmdlet-not-found in PS stderr.
- Otherwise the raw PowerShell exception text is passed through verbatim (carries Windows' own `"Access is denied"` / `"The user name or password"` / logon-right wording for the classifier to match).

**Tests to add/rewrite:** per §Test strategy (argv has no password; stdin script shape incl. explicit `-LogonType Password`, `-StartWhenAvailable:$false`, `InvariantCulture`; fresh-copy env + `os.environ` untouched; no-password→Interactive/Limited + run_highest-ignored; run_highest=False→Limited; frozen vs source; validation-before-subprocess; leak-on-failure: password & `DSYNC_TASK_PW` absent from returned message + caplog; fail-loud FileNotFoundError + cmdlet-missing).

**Acceptance criteria (Slice 1):** all gates green (pytest 80% / ruff / mypy non-UI / bandit / validate-config / tree-check); SD74 snapshot byte-identical; the no-password path verified live by the implementer (`register_task` returns success; `Get-ScheduledTask` shows the task; cleaned up). **Credentialed path gated on the user checklist below.**

### User live-verification checklist (Definition-of-Done gate — Slice 1 cannot land until the user confirms ALL):
1. Wizard "Save & activate schedule" **with the real account password** completes with **no** "Access is denied".
2. `schtasks /Query /TN <task> /XML` (or `Get-ScheduledTask <task> | Select -ExpandProperty Principal`) shows **`LogonType = Password`** and **`RunLevel = Highest`** — proves the explicit-principal construction took (not an `S4U`/`Interactive` degradation).
3. The task **runs while you are logged off** (or `Start-ScheduledTask` from a different session) AND that run **produces output CSVs and the SFTP upload succeeds** — confirms the stored-credential logon has the network token S4U lacks.
4. Task Scheduler **`Last Run Result = 0x0`**.

### Slice 2 — Wizard diagnostics + readable PowerShell errors
**Files & changes:**
- **De-CLIXML the error surface (`src/scheduler/windows.py`, live-discovered 2026-06-25).** A real failed registration surfaces PowerShell's CLIXML error serialization (`#< CLIXML … <Objs>…<S S="Error"> : Access is denied.</S></Objs>`) — the whole script + a buried message — because `Write-Error` to a redirected stderr is CLIXML-wrapped. Fix at the source: the script's `catch` emits the plain text via `[Console]::Error.WriteLine($_.Exception.Message); exit 1` (bypasses PS CLIXML formatting). Add a defensive `_clean_ps_stderr(text)` in Python that, if `#< CLIXML` is still seen, extracts the human message from the `<S S="Error">…</S>` nodes (decoding `_x000D_`/`_x000A_`) and returns a clean one-liner — used for both the canonical-marker match and the returned message. **Must NOT leak: extract only the error message, never echo the script body** (which contains the `$env:DSYNC_TASK_PW` literal — not the value, but still noise). Canonical markers (`"PowerShell not found"`, `"ScheduledTasks module not available"`, `"Access is denied"`) still match against the cleaned text.
- `is_elevated() -> bool` in `src/scheduler/windows.py` (win32: `ctypes.windll.shell32.IsUserAnAdmin()`; non-win32: `False`/guard).
- `01_Setup_Wizard.py` `_register_schedule` failure branch (`:134-141`) classifies on Slice 1's message substrings + `is_elevated()`: not-elevated + access-denied → "Run as administrator"; elevated + access-denied → "Already running as administrator — the account may lack the 'Log on as a batch job' right, or the password was rejected (use your account password, not your Windows Hello PIN; for a Microsoft Account that's your microsoft.com password)"; `"PowerShell not found"`/`"ScheduledTasks module not available"` → their own actionable lines; else → raw (now CLEAN) `msg`.
**Tests:** classifier table (each branch) with `is_elevated` mocked both ways; `is_elevated()` win32-path mocked + non-win32 returns False; `_clean_ps_stderr` on a real CLIXML sample → clean "Access is denied." with NO script body and NO `DSYNC_TASK_PW`; the script `catch` uses `[Console]::Error.WriteLine` not `Write-Error`.
**Acceptance:** all gates green; tree updated; no SD54 dependency (UX only); SD74 byte-identical.
