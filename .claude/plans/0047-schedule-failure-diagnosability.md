# 0047 — Schedule-failure diagnosability (plan 0046's "Slice A", shipped alone)

- **Status:** Draft (2a) — awaiting 2b advisory panel → Stage 3 plan-gate → Stage 4 spec → **owner approval**
- **Resumable from:** Stage 2b — panel not yet convened.
- **Blockers:** none. (SD60's exact error text was requested by the owner on 2026-09-16 morning; a reply would *confirm* the GPO hypothesis but the slice does not wait on it.)
- **Flags:** `split Slice A out of 0046 into its own plan (independent, ships first, closes alone) — chose two plan files over one lingering plan` · `classify_schedule_error gains a keyword-only account_is_current=True that nothing in src/ passes until 0046-B — chose the O1 "extend in place, keyword-only, defaulted" ruling over a Slice-B re-touch of the same copy` · `registration failures only: delete_task's TaskComError arm stays unlogged (an absent task raises HR_NOT_FOUND there and is an idempotent success, so an ERROR line would lie) — noted, not built`
- **Disposition at close:** single slice; done when it lands.
- **Roadmap item:** none yet — this is a live district blocker (SD60, 2026-09-14), surfaced by the 0046 handover §12 rather than the audit backlog.
- **References:** `.claude/plans/0046-HANDOVER.md` §3 / §5(b) / §12 · `.claude/plans/0046-service-account-scheduled-task.md` (A7) · `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-09-15, 2026-06-25) · `docs/claugentic-INVARIANTS.md` (S4U entry)

> **Honesty constraint (owner, handover §12):** this slice makes a schedule-registration failure *legible*. If the GPO is SD60's cause, **no app change makes their unattended sync work** — the district must exempt the computer from the policy, or run logged-on-only. Nothing in the copy, the CHANGELOG, the partner doc or a district email may imply otherwise.

## Problem

A district admin at SD60 tried to enable the nightly sync on 2026-09-14, got a failure, ran the app as administrator, got *the same* failure, and reported *"I didn't see anything informative in the log."* Four defects, each verified in the code on 2026-09-16 (the handover's table, re-verified by the code map this plan was built from):

| # | Defect | Evidence |
|---|---|---|
| A1 | `0x80070520` (`ERROR_NO_SUCH_LOGON_SESSION`) is unmapped. It falls to Windows' own text (`task_com._canonical_message`, `src/scheduler/task_com.py:296-302`), then to the classifier's generic branch, which says **"Try again in a moment"** — advice that can never work for a policy. | `0x80070520` absent from all of `src/`; fallback copy at `src/ui_flet/setup_errors.py:121-124` |
| A2 | `SCHED_E_ACCOUNT_INFORMATION_NOT_SET` (`0x8004130F`) and `ERROR_LOGON_FAILURE` (`0x8007052E`) share the canonical string `"The user name or password is incorrect."` An admin whose task has *no saved account information* is told to retype a password forever. | `src/scheduler/task_com.py:84-89`; **pinned** by `tests/test_task_com.py:91` (a deliberate contract change, not a bug fix) |
| A3 | `classify_schedule_error` has **no credential branch**: `"The user name or password is incorrect."` matches nothing and lands in the generic fallback. The only credential coaching in the app sits inside the `access_denied and elevated` branch (`setup_errors.py:107-114`), which a wrong password never reaches on the COM path (live-confirmed 2026-08-05: a wrong password fails with `0x8007052E`, not access-denied). | `tests/test_ui_flet_setup_errors.py:58-66` asserts the generic output for exactly that input |
| A4 | The failure does not reach the log usefully. The direct path logs the canonical text but **never the HRESULT** (`src/scheduler/windows.py:405-409`); the elevated path's `ok=False` branch **logs nothing at all** (`windows.py:609-613`) and the child's result file drops `exc.scode` (`src/scheduler/elevated_apply.py:125-126`, schema `{ok, message}` at `:59-68`). | SD60: "nothing informative in the log" |

Three more defects the code map surfaced that are in the same blast radius and would otherwise ship as debt:

| # | Defect | Evidence |
|---|---|---|
| A5 | Two classifier branches are **dead**: `"PowerShell not found"` / `"ScheduledTasks module not available"` were retired with the PowerShell transport at plan 0041 S1b (`windows.py:149-151`); nothing in `src/` produces them. Their live successor, `task_com.MSG_COM_UNAVAILABLE` (a frozen build missing pywin32 — **permanent**), has **no** branch and gets "Try again in a moment". | `setup_errors.py:91-101`; producers at `windows.py:403-404`, `elevated_apply.py:129`; the module docstring (`setup_errors.py:9-11`) still names the retired producers as its keying contract |
| A6 | `docs/partner/troubleshooting.md:61` reproduces the classifier's PIN / microsoft.com / batch-logon coaching in prose and attributes it to **"Access is denied" even after approving the prompt** — a diagnosis from the retired PowerShell era (DECISIONS 2026-06-25: the `schtasks /RU /RP` handoff failed *Access is denied*). Against today's COM path that paragraph is already wrong, and `:50-51` claims a wrong password "causes Windows to report an error in the wizard", which the GPO case contradicts. | file read 2026-09-16 |
| A7 | The obvious English for `0x80070520` — *"no such logon session"* — contains `"no such"`, one of `schedule_status._ABSENT_DELETE_MARKERS` (`src/ui_flet/schedule_status.py:41`). Any failure message carrying a marker is turned into a **success-shaped** "No schedule was registered" on the unregister path (`interpret_unregister`, `:270`). `"access denied"` likewise keys the adapter's elevated-retry (`src/scheduler/__init__.py:141`) and the classifier (`setup_errors.py:89`). Nothing enforces the inverse — that a *new* canonical must not carry another code's trigger substring. | no test in `tests/` asserts it |

Plan 0046's A7 (*"the PIN / microsoft.com coaching is wrong against a service account and coaches a personal cloud credential into a service-account field"*) rides here, because the credential branch is created here and it is the branch that must be principal-aware once 0046-B exists.

## Goals / Non-goals

**Goals**
- G1 — Each *observed or Microsoft-documented* registration HRESULT in scope (`0x80070520`, `0x8004130F`, `0x8007052E`, `0x80070005`, plus `MSG_COM_UNAVAILABLE`) produces its own calm, plain-prose, secret-free copy with a real next step and its Windows code, so a screenshot answers "why".
- G2 — Every registration failure, direct **and** elevated, writes ONE `ERROR` log line carrying the canonical message **and** the HRESULT (`0x%08X`, or `n/a` when there is none), with no secret in it — so a district's `etl_tool.log` answers "why" without a round trip.
- G3 — The generic fallback stops promising that a retry helps (the cause is unknowable there) while keeping the support path and the details clause.
- G4 — The credential copy is principal-aware by construction (`account_is_current`), so 0046-B wires one keyword and never re-reviews copy.
- G5 — The partner troubleshooting doc says the same thing the app does, for the same code.
- G6 — Byte-identical behaviour for every path that succeeds today; zero `src/etl/**` / `config/mappings/**` touch (SD74 snapshot unchanged).

**Non-goals**
- N1 — Making SD60's unattended sync **work** under the GPO. Not possible in-app; see the honesty constraint.
- N2 — Mapping HRESULTs nobody has observed and Microsoft does not document for `RegisterTaskDefinition`: `0x80070534` / `0x80041310` (bogus account name — Community-sourced for the general case; 0046-B measures it live once the account field exists), `0x80070569` (batch-logon — a **run-time** `LastTaskResult`, Community-sourced; 0046-C's `run_result_verdict` is its home), expired/disabled/locked-out codes (not evidenced). The HRESULT log line (G2) is what turns the *next* unknown code into evidence.
- N3 — A branch for `_MSG_ACCOUNT_NEEDS_PASSWORD`. Unreachable from the UI until 0046-B (`screens/setup.py:2143` passes `run_as_user=None`); it moves out of the fallback in B, where its copy can name the field it refers to. Pinned here as *deliberately unclassified*.
- N4 — Detecting `SCHED_S_BATCH_LOGON_PROBLEM` (`0x0004131C`). It is a **success** HRESULT of a call pywin32 reports as succeeded; nothing in the COM path can observe it without reading the raw `HRESULT` of a successful `Invoke`. Recorded, not built.
- N5 — Any change to `screens/setup.py`. Its three `classify_schedule_error(msg, _elevated_now())` call sites stay positional; the new keyword defaults to today's truth.
- N6 — Logging on the delete path (see Flags).
- N7 — Any gMSA / SYSTEM / machine-scope-secret work (owner, 2026-09-16 — parked in 0046 §6).

## Approach

**One principle: map by HRESULT in the engine, key by exact canonical string in the classifier, log the code beside the text — the three existing contracts, extended rather than replaced.**

1. **`task_com.py` — named canonicals.** Promote every `_HRESULT_CANONICAL` value to a module-level `MSG_*` constant (the module already does this for `MSG_COM_UNAVAILABLE`; `windows.py` does it for every `_MSG_*` elevation marker, which `setup_errors` imports *"so the copy can never drift from the producer"* — `windows.py:110`). Add `HR_NO_SUCH_LOGON_SESSION = 0x80070520` with its own string; give `HR_ACCOUNT_INFO_NOT_SET` its own string; keep `HR_LOGON_FAILURE`'s text byte-identical (live-confirmed 2026-08-05; three tests and the register docstring quote it). Add `format_hresult(scode: int | None) -> str` (the one hex formatter — `task_com.py:302` is the only existing `0x%08X` in `src/`). An **unmapped** HRESULT that has Windows' own description now surfaces `"<description> (0x%08X)"` so the details clause carries the code too.
2. **Forbidden-substring rule, enforced.** No canonical string may contain an `_ABSENT_DELETE_MARKERS` marker or `"access denied"` / `"access is denied"` (case-insensitive) unless it *is* the code that owns that marker (`HR_NOT_FOUND` → `"cannot find"`, `HR_ACCESS_DENIED` → `"access is denied"`). Pinned by a sweep over the whole table with positive twins for the two owners. Recorded in `docs/claugentic-INVARIANTS.md`.
3. **`elevated_apply.py` — carry the code, not a secret.** `_write_result(res_path, ok, message="", *, scode=None)` adds `"scode"` to the JSON **only when it is an int** (success shape `{"ok": true, "message": ""}` is byte-identical — `tests/test_elevated_apply.py:156` keeps pinning it). The `TaskComError` arm passes `exc.scode`. The child still has no logger (its module docstring, `:37`) — that is by design and unchanged.
4. **`windows.py` — one log line per failure, both paths.** The direct `TaskComError` arm logs `... [HRESULT 0x%08X]`; the elevated `ok=False` arm gains its first log line, reading `scode` from the result **only if `isinstance(x, int) and not isinstance(x, bool)`** (the result file is plaintext our own child wrote, but the parent validates its shape everywhere else and this is no exception). Everything else in those arms is unchanged — the returned message is still the canonical text, still `_sanitize_child_message`'d.
5. **`setup_errors.py` — classify what is produced.** Delete the two dead branches. Add four exact-equality branches (GPO · account-info · credential · COM-unavailable), placed with the other exact-equality markers *before* the `access_denied` substring block (the ordering rule at `setup_errors.py:60`). Rewrite the `access_denied and elevated` copy: it loses the credential and batch-logon clauses (a wrong password never reaches it on the COM path; batch-logon at registration is a success code, N4) and gains its code. Rewrite the fallback: support-first, no "try again", details clause kept. Add `*, account_is_current: bool = True`; only the credential branch reads it. Every branch's copy ends with `(Windows code 0x…)` built from the imported `HR_*` int — single-sourced, no duplicated literal.
6. **Produced-vs-classified parity test.** A test enumerates every message `register_task` can return (the `task_com.MSG_*` table, `windows._MSG_*`, `MSG_COM_UNAVAILABLE`, the two generic literals) and asserts each is either classified (no `(Details:` in the output) **or** named in an explicit `DELIBERATELY_UNCLASSIFIED` set with a reason. A future canonical goes red until someone decides — closing the "nothing goes red either way" gap the dead branches sat in for two months.
7. **Docs in the same slice.** `docs/partner/troubleshooting.md`'s Task Scheduler section is rewritten to match the app (the wrong-password bullet, the "Access is denied after approving" bullet, and a new GPO block that says plainly DistrictSync cannot work around the policy). Tree, DECISIONS, INVARIANTS, CHANGELOG.

**Alternatives rejected (one line each):**
- *Put the GPO coaching in `task_com`'s canonical string* — the engine's strings go to logs and to the fallback verbatim; long admin copy belongs in the pure classifier, keyed by exact equality (the existing split).
- *Duplicate the canonical literal in `setup_errors`* — violates single-source and CLAUDE.md's "any literal copied out of `src/` needs a parity test"; importing the constant IS the house pattern.
- *Keep "Try again in a moment" for transient-looking messages only* — permanence is unknowable on the fallback; a neutral, support-first fallback is the only honest one.
- *A `DowngradeInterrupt`-style dialog for the GPO case* — the failure has no in-app fix to offer; a banner with a next step is the right weight (KISS).
- *Log the HRESULT from `screens/setup.py`* — the view logs nothing today and should not start; `windows.py` already owns the failure log and both paths converge there.
- *Map `0x80070534` now* — Community-sourced for the general case; N2.
- *Fold the whole slice into 0046* — it is independent, ships first and must close alone; a plan lingers until its last slice.

## Architecture & holistic fit

- **Codebase fit.** Three layers, each extended along its existing seam: `task_com` (engine — HRESULT → locale-free canonical; owns hex formatting) · `windows.py` / `elevated_apply.py` (transport — the log sink and the result-file producer/consumer) · `setup_errors` (COUNTED pure classifier — canonical string → admin copy; imports producer constants, never duplicates them). The view (`screens/setup.py`) is untouched; it keeps calling the classifier positionally. Layer isolation holds: no COM type crosses `task_com`'s boundary, the classifier never sees an `scode` (it keys on strings and *names* codes from imported ints), and the child's result file gains a non-secret integer — the secret-handling contract (`repr=False`, DPAPI request, plaintext result, `_sanitize_child_message`) is unchanged. SOLID: single responsibility per layer preserved; open/closed — a new HRESULT is one table row + one branch + one parity-test decision; the S4U ban (`TASK_LOGON_S4U` undefined) survives untouched.
- **Product fit.** The admin reads a cause and a next step in plain language; support reads the code off a screenshot or the log. Copy is verdict-first, plain prose (no markdown — `setup_errors.py:25-29`), uses the schedule section's carved-out vocabulary (`docs/DESIGN_SYSTEM.md` §7 exempts the Windows-schedule copy from the "account / credentials" ban) and never claims a fix the app cannot make. Serves the SD60 job-to-be-done directly (*"why did the schedule fail?"*) and every later district's.
- **Quality dimensions to uphold** (→ Spec "In-scope standards dimensions"): `security` (no secret in any log line, result file or copy; the classifier stays a non-leaking mapper) · `observability-ops` (one ERROR line per failure with the code; PII-free) · `product-ux` (honest, plain-language, actionable copy; no false "try again") · `testing` (mutation-checked new tests; no vacuous greens — every "not present" has a positive twin; the parity sweep) · `reliability-resilience` (`scode=None` and malformed result values degrade to `n/a`, never a format crash) · `api-and-contracts` (result-file schema extended additively; canonical-string contract extended with a forbidden-substring rule; classifier signature extended keyword-only) · `maintainability-structure` (dead code and its tests removed; constants single-sourced) · `docs-traceability` (partner doc parity, tree, DECISIONS, INVARIANTS, CHANGELOG).
- **Future-proofing.** 0046-B passes `account_is_current` and moves `_MSG_ACCOUNT_NEEDS_PASSWORD` out of `DELIBERATELY_UNCLASSIFIED`; 0046-C's `run_result_verdict` gives `ScheduleReadback.last_result` (a dead field today — no consumer in `src/`, and `schedule_status.py:201`'s docstring wrongly claims one) its first reader and the natural home for `0x80070569`; the log line makes every unmapped code observable, and the parity test makes every new canonical a decision. Nothing is built for gMSA.

## Affected files

**Engine / transport**
- `src/scheduler/task_com.py` — `HR_NO_SUCH_LOGON_SESSION`; `MSG_ACCESS_DENIED` / `MSG_NOT_FOUND` / `MSG_LOGON_FAILURE` / `MSG_ACCOUNT_INFO_NOT_SET` / `MSG_CREDENTIAL_STORAGE_REFUSED` (table built from them); `format_hresult`; unmapped-with-description carries the code; comment block at `:77-83` gains the forbidden-substring rule.
- `src/scheduler/elevated_apply.py` — `_write_result(..., *, scode=None)`; `TaskComError` arm passes `exc.scode`; module docstring's result-shape sentence.
- `src/scheduler/windows.py` — direct `TaskComError` arm logs the code; elevated `ok=False` arm logs (new) with the code read defensively; `_result_scode(result) -> int | None` helper; register docstring (`:279`) updated for the split string.

**Pure UI logic (COUNTED)**
- `src/ui_flet/setup_errors.py` — imports; dead branches removed; four new branches; two rewritten branches; fallback rewritten; `account_is_current`; module docstring rewritten (it still names retired producers).

**Docs**
- `docs/partner/troubleshooting.md` — the "Task Scheduler does not run the task" section: wrong-password bullet, the UAC block's last bullet, a new GPO block, codes named.
- `docs/claugentic-ARCHITECTURE_TREE.md` — `task_com` / `elevated_apply` / `setup_errors` / `windows` lines; entries added for `tests/test_task_com.py` and `tests/test_ui_flet_setup_errors.py` (both exist with no entry today).
- `docs/claugentic-DECISIONS.md` — one dated entry (flat-bullet form, top of file).
- `docs/claugentic-INVARIANTS.md` — the canonical-substring exclusivity invariant.
- `CHANGELOG.md` — `[Unreleased]` → `### Fixed`, honest wording.

**Tests**
- `tests/test_task_com.py`, `tests/test_ui_flet_setup_errors.py`, `tests/test_scheduler_runas.py`, `tests/test_elevated_apply.py` — see Test strategy.

Untouched, by design: `src/ui_flet/screens/setup.py`, `src/scheduler/__init__.py`, `src/ui_flet/schedule_status.py`, everything under `src/etl/` and `config/`.

## Research / grounding

**Files reviewed (2026-09-16, five parallel read-only mappers + two critics + six follow-ups; all `file:line` above are from that pass):** `src/scheduler/task_com.py:66-121, 129-147, 182-204, 232-243, 264-308, 405-411` · `src/scheduler/windows.py:110-161, 279, 340-352, 378-415, 496-500, 553-616` · `src/scheduler/elevated_apply.py:37, 52-68, 81-133` · `src/scheduler/__init__.py:140-141` · `src/ui_flet/setup_errors.py` (whole) · `src/ui_flet/schedule_status.py:41, 193-218, 256-270` · `src/ui_flet/screens/setup.py:213, 1984, 2022-2143, 2172-2216, 2469, 2603` · `src/utils/logger.py:15`, `config/logging.conf`, `src/ui_flet/launcher.py:32` (a `src.scheduler.windows` WARNING/ERROR propagates to root and reaches `etl_tool.log` once `boot_logging()` ran — so G2 is reachable in the desktop app) · `tests/test_task_com.py:42-93` · `tests/test_ui_flet_setup_errors.py` (whole) · `tests/test_scheduler_runas.py:34, 284-324, 438-445` · `tests/test_elevated_apply.py:156, 173-199` · `tests/test_ui_flet_home_wizard_host.py:328` (two-mount parity compares mount-time text/labels only — a failure banner painted after a register attempt is not in its scope) · `docs/partner/troubleshooting.md:42-79` · `docs/claugentic-INVARIANTS.md` (S4U entry) · `docs/DESIGN_SYSTEM.md:32-40` · `docs/claugentic-CHARTER.md` (entry 1: pure predicate modules → RED-FIRST edge table).

**Harness docs consulted:** `docs/claugentic-WORKFLOW.md` (whole) · `docs/claugentic-PLAN_TEMPLATE.md` · `docs/claugentic-standards/README.md` (lens list) · DECISIONS 2026-09-15 / 2026-06-25 / 2026-06-05 · CLAUDE.md gotchas: bandit `-c pyproject.toml` + `# nosec B105` on a name-matched constant (`windows.py:131-133`); `check_no_emails.py`; no `make`; venv interpreter; LF endings.

**Microsoft sources (five parallel fetch-and-quote researchers + critic + six follow-ups, 2026-09-16; every claim below was FETCHED, none recalled). Confidence labels decide how the copy is worded:**

| Claim the copy or code rests on | Confidence | Source |
|---|---|---|
| `0x80070520` = `HRESULT_FROM_WIN32(1312)` = `ERROR_NO_SUCH_LOGON_SESSION`, "A specified logon session does not exist. It may already have been terminated." | **Documented** | system-error-codes 1300-1699; `HRESULT_FROM_WIN32` |
| Enabling *Network access: Do not allow storage of passwords and credentials for network authentication* makes Task Scheduler fail to create a "Run whether user is logged on or not" task with "A specified logon session does not exist"; mechanism: Task Scheduler stores the run-as credential via Credential Manager, which the policy blocks; "Run only when user is logged on" is unaffected. | **Documented — on an ARCHIVED Microsoft blog** (2012, Server 2008 R2 / Win 7, the MMC UI; no numeric code printed) | learn.microsoft.com/…/archive/blogs/supportingwindows/task-scheduler-error-a-specified-logon-session-does-not-exist |
| `0x80070520` + the same policy + credential-storage refusal — on a *current* Microsoft article (a different product: SCOM agent deploy). Registry: `HKLM\SYSTEM\CurrentControlSet\Control\Lsa\DisableDomainCreds`. | **Documented** (for SCOM) | troubleshoot/system-center/scom/deploy-operations-manager-agents-error-80070520 |
| ⇒ *The policy causes `0x80070520` at DistrictSync's COM registration.* | **Strongly implied** (composite; the COM path and Win10+/Server 2016+ are not covered by any Microsoft page — the policy page itself never mentions Task Scheduler, verified 2026-09-16) | — |
| The policy's effective default is **Disabled** everywhere; CIS Windows 11 control 2.3.10.4 recommends **Enabled (L1)**; presence in the Microsoft Security Baseline is unverifiable from a fetchable page. | Documented / Community / gap | policy page; third-party CIS mirror |
| "Do not store password" ⇔ S4U; S4U discards the credential after authenticating; S4U "can only use the security context to access local resources… If your task requires access to network resources, you cannot use S4U; doing so will cause your task to fail." | **Documented** (archived Microsoft page, NOINDEX) | cc722152(v=ws.11) |
| `TASK_LOGON_S4U`: "no password is stored by the system and there is no access to either the network or to encrypted files." | **Documented** (current API reference) | ne-taskschd-task_logon_type; nf-taskschd-itaskfolder-registertaskdefinition |
| Whether DPAPI / Credential Manager / the keyring is readable under S4U | **UNDOCUMENTED in either direction** — the copy therefore says "no network access", never "your delivery password becomes unreadable" | four S4U pages checked |
| `0x8004130F` = "No account information could be found in the Task Scheduler security database for the task indicated." | **Documented** | task-scheduler-error-and-success-constants |
| `0x8004130F` is *not* a wrong-password condition: MS-TSCH defines it as a GET-path lookup miss (`SAGetAccountInformation`, no password parameter) and separately defines the wrong-password case as `0x8007052E`. It is **absent** from `RegisterTaskDefinition`'s documented return table. | **Strongly implied** (protocol structure) — copy says "a different problem from a wrong password", never asserts a cause | MS-TSCH 3.2.5.3.x; registertaskdefinition |
| `0x8007052E` = "The user name or password is incorrect." | **Documented** + live-confirmed 2026-08-05 (`tests/test_scheduler_runas.py:209`) | 1300-1699; MS-TSCH App. B |
| Batch-logon: registration returns the **success** code `SCHED_S_BATCH_LOGON_PROBLEM` (`0x0004131C`); Task Scheduler auto-grants the right when a task is scheduled, defeated by a Deny policy **or** a domain GPO managing the Allow list; the run-time failure `0x80070569` is tied to Task Scheduler only by an archived forum thread. | Documented / Documented / **Community** | registertaskdefinition; log-on-as-a-batch-job; archived forum |
| A bogus run-as name → `0x80070534` (`ERROR_NONE_MAPPED`) | Documented only for SYSTEM + NULL + NULL + `TASK_LOGON_SERVICE_ACCOUNT`; **Community** for the general case | registertaskdefinition; Q&A |

**Findings.** Reuse: the `_MSG_*`-import pattern, `_FakeComError`/`_wrapped` fixtures, `TestPasswordLeakClosure`'s caplog sweep, `_capture_registration()`, the `test_elevated_apply` result-reading helpers. Build: named canonicals, `format_hresult`, the result-file `scode`, four branches, the parity sweep, the forbidden-substring sweep. Gotchas: `"no such"` is a delete-success marker; `TaskComError.scode` is genuinely `None` at `task_com.py:410`; `bool` is an `int` in Python (guard the result-file read); the elevated child has no logger by design; `tests/test_task_com.py:91` and four `test_ui_flet_setup_errors.py` tests (`:58-66`, `:124-136`, `:212-216`, `:218-223`) pin the behaviour this slice changes and must be **rewritten deliberately**, not retuned.

## Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | A new canonical string accidentally carries `"no such"` / `"does not exist"` / `"cannot find"` / `"access denied"` → a real failure reads as an idempotent unregister success or fires the elevated delete-retry. | The forbidden-substring sweep over the whole table (with positive twins) + the INVARIANTS entry. |
| R2 | The HRESULT log line or the result-file `scode` becomes a secret channel. | Both carry an `int` (or `n/a`); `TestPasswordLeakClosure` extended with a positive twin (code present, secret absent) on **both** paths; `test_the_result_never_carries_the_password` kept. |
| R3 | `scode=None` (`task_com.py:410`) or a malformed `scode` in the result file crashes the format. | `format_hresult` is total; the parent reads `scode` only when `isinstance(x, int) and not isinstance(x, bool)`; tests for `None`, `True`, `"0x…"`. |
| R4 | Splitting `0x8004130F` from `0x8007052E` breaks a consumer. | Grep 2026-09-16: the only pin is `tests/test_task_com.py:91`; `test_scheduler_runas.py:214` and `test_elevated_apply.py:180` use `HR_LOGON_FAILURE` only, whose text is unchanged. |
| R5 | The GPO copy over-claims (Strongly-implied composite presented as fact). | Copy says "This usually means…"; the DECISIONS entry records the confidence labels; `honesty-reviewer` on the panel. |
| R6 | The copy claims the keyring becomes unreadable under S4U (undocumented). | Copy quotes only the documented restriction (no network access) and DistrictSync's own non-support. |
| R7 | The rewritten fallback / removed dead branches break the six existing fallback-shape tests. | Named in Test strategy; each rewrite states *why* in its docstring (deliberate contract change). |
| R8 | The `account_is_current` keyword is dead until 0046-B (YAGNI). | Keyword-only, defaulted to today's truth, both values pinned now; 0046-B wires one argument. Flagged. |
| R9 | The partner doc and the app disagree again later. | The doc quotes the app's headline sentences; the DECISIONS entry names the doc as a parity surface. |
| R10 | SD74 snapshot / ETL output. | Zero `src/etl/**` + `config/**` touch — assert empty diff at Verify. |
| R11 | `check_no_emails.py` — the plan, DECISIONS and doc carry no address (first names + SD numbers only). | Run the gate locally before commit (it is in the pre-commit hook). |

## Test strategy

RED-FIRST per CHARTER entry (1): write the edge table for `_canonical_message` and `classify_schedule_error`, watch it fail, implement, then **mutation-check** by restoring `HEAD`'s copy of each changed module and re-running (the 0046 Slice 1 discipline; a test that stays green against the old module is vacuous).

- `tests/test_task_com.py` — `:91` split into a two-row parametrize `(hr, expected)` with a docstring recording the deliberate contract change; a row for `HR_NO_SUCH_LOGON_SESSION`; the forbidden-substring sweep + two positive twins; unmapped-with-description now ends with `(0x80041318)` (`:99-102` updated deliberately); `format_hresult` over `None` / `0x80070520` / `0`.
- `tests/test_ui_flet_setup_errors.py` — dead-branch tests deleted (`:45-56`, `:109-122`; the loops at `:73-82` and `:231-239` re-seeded with the new canonicals); a class per new branch: GPO (names the policy · warns against "Do not store password" · offers the logged-on-only alternative · no "try again" · carries `0x80070520`) · account-info (says it differs from a wrong password · `0x8004130F`) · credential (`account_is_current=True` mentions the PIN / microsoft.com clauses; `False` mentions **neither** and asks for that account's password; both carry `0x8007052E`) · COM-unavailable (Convert page + support, no "try again"); access-denied-elevated no longer contains "PIN" / "microsoft.com" / "batch"; every classified branch: no `(Details:`, no `**`, output independent of `elevated` where the branch is elevation-independent; fallback rewritten (`:58-66`, `:124-136`, `:212-216`, `:218-223` rewritten; `:206`, `:225` survive as-is); `_MSG_ACCOUNT_NEEDS_PASSWORD` pinned as reaching the fallback (with the 0046-B note); the **produced-vs-classified parity sweep** with its `DELIBERATELY_UNCLASSIFIED` set.
- `tests/test_scheduler_runas.py` — `TestPasswordLeakClosure.test_failure_path_never_logs_the_password` gains the positive twin (`"0x80070005"` in `caplog.text`, secret absent); a `TaskComError(None, …)` logs `n/a`; an elevated-parent test (mocked `read_result` → `{"ok": False, "message": MSG_LOGON_FAILURE, "scode": 0x8007052E}`) asserts the new log line carries `0x8007052E`, the returned message is the canonical, the secret is absent; malformed `scode` (`True`, `"0x8007052E"`) → `n/a`.
- `tests/test_elevated_apply.py` — a `TaskComError` failure writes `{"ok": false, "message": …, "scode": <int>}`; the success shape stays exactly `{"ok": true, "message": ""}` (`:156`); the `ImportError` result has no `scode` key; the password-never-in-result test (`:199`) kept.
- Regression: full suite · SD74 snapshot · the 20-config validation (inline Makefile command) · tree-check · ruff / mypy / bandit (`-c pyproject.toml`) · `check_no_emails.py` · CI's own result read and quoted (land gate).

## Decomposition (slices)

- [ ] **Slice A (the whole plan)** — engine constants + hex formatter · result-file `scode` · two log lines · classifier rewrite · parity + forbidden-substring sweeps · partner doc · tree / DECISIONS / INVARIANTS / CHANGELOG · lands complete because every change is one seam deep, the view is untouched, and the new copy is fully pinned before the PR. One implementer session (≈10 files, ≈400 changed lines incl. tests).

---

## Review  _(filled by synthesizer-gate in its plan-gate altitude, Stage 3)_
- **Verdict:** _pending_
- **Required changes:** …
- **Sizing/completeness:** …
- **Harness impact:** …

---

## Spec  _(Stage 4)_

### Slice A

**In plain English (shown first at the approval gate):**
- *What this builds.* When scheduling the nightly sync fails, the app names the actual cause in plain language and gives a real next step — including the case where a Windows security policy stops the password being saved (the one we believe SD60 hit), the case where the task's saved account details are missing (which is *not* a wrong password), and a plain wrong-password case. It stops saying "try again in a moment" when that cannot help. Every failure also writes one log line with the Windows error code, on both the normal and the administrator-prompt paths, so a district's log answers "why" without a round trip. The partner troubleshooting page is updated to say the same things.
- *What "done" means for you.* Each of the four causes above shows its own message (screenshots attached at Verify); the log line carries the code and never a password; the old dead branches and the stale doc paragraph are gone; all gates green; CI's own result quoted; SD74 output byte-identical.
- *What you're accepting.* (1) This makes SD60's failure **legible, not fixed** — if the policy is the cause, their IT team must exempt the computer or the sync runs only while someone is logged in. (2) The GPO message says "usually", because the link between that policy and this code is documented by Microsoft for the Task Scheduler UI (2012) and for another product, but not for our exact code path. (3) The classifier gains a keyword that nothing passes until 0046-B. (4) Two existing tests that pinned the *old* behaviour are rewritten on purpose. (5) Codes nobody has observed yet (mistyped account name, batch-logon at run time, expired password) are **not** mapped — the new log line is how they become evidence.

**Files & changes.**

`src/scheduler/task_com.py`
```python
HR_NOT_FOUND = 0x80070002
HR_ACCESS_DENIED = 0x80070005
HR_NO_SUCH_LOGON_SESSION = 0x80070520  # ERROR_NO_SUCH_LOGON_SESSION — Windows refused to store the task credential (documented for the GPO "Network access: Do not allow storage of passwords and credentials for network authentication")
HR_LOGON_FAILURE = 0x8007052E
HR_ACCOUNT_INFO_NOT_SET = 0x8004130F

# Canonical, secret-free, locale-independent text per HRESULT. Consumers key on EXACT equality
# (setup_errors) or on a substring THEY own (the "cannot find" delete marker, the "access is denied"
# elevated-retry predicate). RULE (pinned + INVARIANTS): no string may contain a marker another
# code owns — `_ABSENT_DELETE_MARKERS` ("cannot find" / "does not exist" / "no such" / "no crontab")
# or "access (is )denied" — because interpret_unregister turns a marker into a success-shaped
# outcome and the adapter into an elevated retry. That is why HR_NO_SUCH_LOGON_SESSION's text is
# NOT Windows' own "no such logon session".
MSG_ACCESS_DENIED = "Access is denied."
MSG_NOT_FOUND = "The system cannot find the file specified."
MSG_LOGON_FAILURE = "The user name or password is incorrect."  # nosec B105 - name-matched; user-facing copy
MSG_ACCOUNT_INFO_NOT_SET = "Windows has no saved account information for the task."
MSG_CREDENTIAL_STORAGE_REFUSED = "Windows refused to store the credential for the task."
_HRESULT_CANONICAL: dict[int, str] = {
    HR_ACCESS_DENIED: MSG_ACCESS_DENIED,
    HR_NO_SUCH_LOGON_SESSION: MSG_CREDENTIAL_STORAGE_REFUSED,
    HR_LOGON_FAILURE: MSG_LOGON_FAILURE,
    HR_ACCOUNT_INFO_NOT_SET: MSG_ACCOUNT_INFO_NOT_SET,
    HR_NOT_FOUND: MSG_NOT_FOUND,
}

def format_hresult(scode: int | None) -> str:
    """``0x%08X`` for a real status, ``"n/a"`` when the failure carried none — total, never raises."""
    return "n/a" if scode is None else f"0x{scode & 0xFFFFFFFF:08X}"

# _canonical_message, unmapped branch:
#   return f"{description} (0x{scode:08X})" if description else f"The schedule operation failed (0x{scode:08X})."
```

`src/scheduler/elevated_apply.py`
```python
def _write_result(res_path: Path, ok: bool, message: str = "", *, scode: int | None = None) -> None:
    payload: dict[str, object] = {"ok": ok, "message": message}
    if scode is not None:            # ADDITIVE: the success shape {"ok": true, "message": ""} is byte-identical
        payload["scode"] = int(scode)
    ...
# TaskComError arm:  _write_result(res_path, False, exc.message, scode=exc.scode)
```

`src/scheduler/windows.py`
```python
def _result_scode(result: dict[str, object]) -> int | None:
    """The child's HRESULT, ONLY when it is a genuine int (bool is an int; a string is not a code)."""
    value = result.get("scode")
    return value if isinstance(value, int) and not isinstance(value, bool) else None

# direct TaskComError arm:
logger.error("Failed to register task '%s': %s [HRESULT %s]", task_name, exc.message, task_com.format_hresult(exc.scode))
# elevated ok=False arm (after the DIFFERENT_ACCOUNT check), replacing the bare return:
msg = _sanitize_child_message(child_msg)
logger.error("Elevated registration of '%s' failed: %s [HRESULT %s]", task_name, msg, task_com.format_hresult(_result_scode(result)))
return False, msg
```

`src/ui_flet/setup_errors.py`
```python
from src.scheduler.task_com import (
    HR_ACCESS_DENIED, HR_ACCOUNT_INFO_NOT_SET, HR_LOGON_FAILURE, HR_NO_SUCH_LOGON_SESSION,
    MSG_ACCOUNT_INFO_NOT_SET, MSG_COM_UNAVAILABLE, MSG_CREDENTIAL_STORAGE_REFUSED, MSG_LOGON_FAILURE,
    format_hresult,
)

def classify_schedule_error(msg: str, elevated: bool, *, account_is_current: bool = True) -> str:
    # 1. the five elevation markers — unchanged, exact equality
    # 2. NEW exact-equality branches (before the access_denied substring block):
    if msg == MSG_CREDENTIAL_STORAGE_REFUSED:
        return (
            "Windows would not store the password for the nightly task, so it can't be scheduled to run "
            "while no one is logged in. This usually means a Windows security policy managed by your IT "
            "team — 'Network access: Do not allow storage of passwords and credentials for network "
            "authentication' — is switched on for this computer; DistrictSync can't change that. Ask your "
            "IT team whether this computer can be exempted, or schedule the sync without a password (it "
            "will then run only while you're logged in). Don't work around it by ticking 'Do not store "
            "password' in Task Scheduler: Windows documents tasks set up that way as having no network "
            f"access, so the sync would be scheduled but could never deliver. (Windows code {format_hresult(HR_NO_SUCH_LOGON_SESSION)})"
        )
    if msg == MSG_ACCOUNT_INFO_NOT_SET:
        return (
            "Windows reports that the nightly task has no saved account information — a different problem "
            "from a wrong password. Remove the nightly schedule below, then schedule it again; if that "
            f"doesn't clear it, the Help page has our support contact. (Windows code {format_hresult(HR_ACCOUNT_INFO_NOT_SET)})"
        )
    if msg == MSG_LOGON_FAILURE:
        if account_is_current:
            return (
                "Windows rejected the user name or password. Enter your Windows account password — the one "
                "you use to log in to this computer, not a Windows Hello PIN; for a Microsoft Account it's "
                f"your microsoft.com password. (Windows code {format_hresult(HR_LOGON_FAILURE)})"
            )
        return (
            "Windows rejected the user name or password for the account you entered. Check the account "
            f"name (DOMAIN\\name) and re-enter that account's password. (Windows code {format_hresult(HR_LOGON_FAILURE)})"
        )
    if msg == MSG_COM_UNAVAILABLE:
        return (
            "This copy of DistrictSync can't reach Windows Task Scheduler, so the nightly sync can't be "
            "scheduled from here — you can still run conversions from the Convert page. The Help page has "
            "our support contact."
        )
    # 3. DELETED: the "PowerShell not found" / "ScheduledTasks module not available" branches (retired producers, 0041 S1b)
    # 4. access_denied and not elevated — today's copy + f" (Windows code {format_hresult(HR_ACCESS_DENIED)})"
    # 5. access_denied and elevated — REWRITTEN:
    #    "Windows refused the schedule change even though you're running as administrator. This is usually a
    #     security policy on this computer rather than a password problem — a wrong password shows a different
    #     message. Check with your IT team and quote the code below and DistrictSync's log file. (Windows code 0x80070005)"
    # 6. fallback — REWRITTEN (no retry promise):
    #    "The schedule change didn't go through. The Help page has our support contact — please include the
    #     details below when you write. (Details: {msg})"
```
Module docstring rewritten: keyed on `task_com.MSG_*` + `windows._MSG_*` exact constants; the non-leak contract sentence unchanged; the plain-prose rule unchanged.

`docs/partner/troubleshooting.md` (section "Task Scheduler does not run the task")
- "Task does not run after a reboot" bullet 2 → *A wrong password is reported in the wizard as "Windows rejected the user name or password" — re-run the schedule step with the correct password.*
- UAC block, last bullet → *"Windows refused the schedule change even though you're running as administrator" — usually a security policy on the computer, not a password problem; ask your IT team, quoting the Windows code the message shows and the `ERROR` line in `etl_tool.log`.*
- **New block** — *"Windows would not store the password for the nightly task"*: names the policy, says DistrictSync cannot work around it, gives the two honest options (IT exemption · logged-on-only), warns against "Do not store password" (documented as no network access), names `0x80070520`.
- "Task shows a non-zero Last Run Result" — unchanged.

`docs/claugentic-ARCHITECTURE_TREE.md` — `task_com` (+ named `MSG_*`, `format_hresult`, five rows, the forbidden-substring rule) · `elevated_apply` (`{ok,message[,scode]}`) · `windows` (HRESULT logged on both register paths) · `setup_errors` (branch list) · new entries for `tests/test_task_com.py`, `tests/test_ui_flet_setup_errors.py`.
`docs/claugentic-DECISIONS.md` — **2026-09-16** entry: the split, the GPO row with its confidence, the forbidden-substring rule, the dead branches, "try again" removed, `account_is_current`, N2/N3/N4 and the honesty constraint.
`docs/claugentic-INVARIANTS.md` — *A `_HRESULT_CANONICAL` string must never contain a marker another consumer keys on (`_ABSENT_DELETE_MARKERS`, "access denied") unless it is the code that owns it.* (Plan 0047, 2026-09-16.)
`CHANGELOG.md` — `[Unreleased]` → `### Fixed` — *Scheduling the nightly sync: failures now name their cause (a policy that blocks saving the password · missing saved account information · a wrong password · Task Scheduler unreachable) instead of "try again in a moment", and every failure logs the Windows error code. This does not change what Windows allows: a district whose policy blocks stored passwords still needs an exemption or a logged-on-only schedule.*

**In-scope standards dimensions (target bar = the module; Verify audits against each):** `security` · `observability-ops` · `product-ux` · `testing` · `reliability-resilience` · `api-and-contracts` · `maintainability-structure` · `docs-traceability`.

**Tests to add / rewrite:** exactly the Test strategy list above, each new test mutation-checked against `HEAD`'s module.

**Acceptance criteria (the spec's checklist):**
1. `_canonical_message(HR_NO_SUCH_LOGON_SESSION, …) == MSG_CREDENTIAL_STORAGE_REFUSED`; the string contains none of the forbidden markers; `classify_schedule_error(MSG_CREDENTIAL_STORAGE_REFUSED, e)` for both `e` names the policy verbatim, warns against "Do not store password", offers the logged-on-only alternative, contains `0x80070520`, contains neither "try again" (any case) nor `(Details:` nor `**`.
2. `_canonical_message(HR_ACCOUNT_INFO_NOT_SET, …) != _canonical_message(HR_LOGON_FAILURE, …)`; `tests/test_task_com.py:91` rewritten with the reason in its docstring; the account-info copy says it differs from a wrong password and carries `0x8004130F`.
3. `classify_schedule_error(MSG_LOGON_FAILURE, e)` returns credential coaching for both `e`, carries `0x8007052E`; with `account_is_current=False` it contains neither "PIN" nor "microsoft.com".
4. The fallback contains no "try again" (any case), still ends with `(Details: {msg})`, still leads with calm copy; `MSG_COM_UNAVAILABLE` no longer reaches it; `_MSG_ACCOUNT_NEEDS_PASSWORD` still does (pinned as deliberate).
5. On a direct `TaskComError(HR_ACCESS_DENIED, …)` the ERROR line contains `0x80070005` and not the password; on `TaskComError(None, …)` it contains `n/a`; on an elevated `ok=False` result with `"scode": 0x8007052E` a new ERROR line contains `0x8007052E` and not the password; a `bool`/`str` `scode` logs `n/a`; the child's failure result carries `"scode"` and its success result is byte-identical to today.
6. The two dead branches and their tests are gone; the produced-vs-classified sweep passes with `_MSG_ACCOUNT_NEEDS_PASSWORD`, `"The schedule operation failed."`, `MSG_NOT_FOUND` and the elevated-child refusal literals in `DELIBERATELY_UNCLASSIFIED`.
7. `docs/partner/troubleshooting.md` no longer attributes a rejected password to "Access is denied" and carries the GPO block; tree / DECISIONS / INVARIANTS / CHANGELOG updated; `check_no_emails.py` green.
8. Full suite green with coverage ≥ 80 %; SD74 snapshot byte-identical; all 20 configs validate; ruff / mypy / bandit green; **CI's own `test pass` line quoted** before "land gate met".
