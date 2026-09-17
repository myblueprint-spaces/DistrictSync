# 0046 — Run the nightly task as a service account

- **Status:** CLOSED 2026-09-17 — Slice 1 (#117), B (#124), C (#125), D (#126) LANDED and released in v3.21.0; Slice A shipped as **plan 0047**. The parked gMSA / machine-scope half continues as **plan 0049** (`0049-machine-scope-gmsa.md`), which supersedes the *Revision* block's N1/N2 stands (DECISIONS 2026-09-17). The Investigations and Review sections below remain the evidence of record.
- **Roadmap item:** partially discharges *"Service-account / machine-scope secret storage (SYSTEM/gMSA task principals; non-keyring secret sources) — enterprise-scope, L"*. This plan takes the **password-account half only**.
- **Supersedes:** the 2026-06-05 decision *"the task's run-as account must equal the setup account"* (`docs/claugentic-DECISIONS.md:444`).
- **References:** plan 0034 (reconcile), plan 0041 (COM scheduler), `docs/DESIGN_SYSTEM.md`
- **Review:** 3 adversarial passes (plan-gate · YAGNI · security lens). Verdict was **CHANGES REQUIRED**; this revision incorporates them. See `## Review`.

> ## Revision — 2026-09-16 (owner scope decision + `0046-HANDOVER.md`)
>
> - **Slice 1 LANDED** (PR #117, `8e1f24e`; DECISIONS 2026-09-15). **Slice A → plan 0047** (`0047-schedule-failure-diagnosability.md`): schedule-failure diagnosability, independent of the feature, ships first, closes alone. Slices 2–4 below are renamed **B / C / D**, revised as listed here, and are NOT deep-spec'd until 0047 lands (JIT spec per `docs/claugentic-WORKFLOW.md`).
> - **N1 STANDS — gMSA / SYSTEM / LOCAL SERVICE / NETWORK SERVICE are PARKED** (owner, 2026-09-16: *"gMSA is OUT until explicitly asked for. Keep it simple."*). The handover's §4 "compatible with both" direction is superseded by that later decision; the gMSA research is preserved in handover §6 so nobody re-does it. `validate_run_as_user` keeps rejecting `$`; `TASK_LOGON_SERVICE_ACCOUNT` is not touched; no explicit principal-kind enum is added (see next bullet).
> - **N2 STANDS** (no machine-scope secret storage) — it only becomes load-bearing for the parked combinations.
> - **Explicit principal kind on `RegisterParams`** (handover §4 called it "worth doing regardless") — considered and **DEFERRED**: with gMSA parked there are exactly two kinds, and Slice 1's `run_as_password or None` normalisation already makes `password is not None` the explicit, single-spelled choice. Revisit when a third kind is asked for.
> - **A10 (delete-then-create) is an OPEN OWNER DECISION**, not settled: the 2026-09-15 spike measured that `TASK_CREATE_OR_UPDATE` replaces the whole definition and re-derives `UserId` on every call (handover §5), so the delete step is unnecessary as a *mechanism* and rests solely on the EDR argument — which the owner's 2026-09-07 rationale and A10's rationale reach from opposite premises. Decide at B's plan-review; both options are buildable.
> - **Slice B = the feature (was 2)**, plus what 0047 and the handover add: `_run_as_account → _keyring_owner_account` **FIRST** (A6; `screens/setup.py:2603` must keep reporting the keyring owner — one caller, verified 2026-09-16) · the principal on `RegisteredSchedule`, never `TaskArgs` (A2) · the prefilled field sends the **typed** value (a request naming the current account case-insensitively is not a principal change — DECISIONS 2026-09-15) · `_register` catches `ValueError` (carried item 3; `register_task` raises for a malformed account) · `_FOLDERS_SAVED_BLOCKED` / `_SFTP_RECONCILE_BLOCKED` (`setup_flow.py:778/786`) name the missing-service-account-password cause (carried item 4) · `classify_schedule_error(..., account_is_current=…)` wired from the recorded principal (0047 G4) and `_MSG_ACCOUNT_NEEDS_PASSWORD` moved out of 0047's `DELIBERATELY_UNCLASSIFIED` set with its own copy (A7 completes) · `runas --sftp-configure` is **necessary, not optional** (Credential Manager has no cross-user scope — handover §5a) · **two live measurements on the owner's machine (UAC clicks, owner's approval):** the HRESULT a mistyped account name produces on our COM path (`0x80070534` is Community-sourced for the general case — map it only once observed) and S0 (a batch-logon task reading its OWN account's Credential Manager — handover §9.2; a RED result is decisive).
> - **Slice C = signal honesty (was 3)** — A5 (a recorded foreign principal makes a record gap EXPECTED for `schedule_status._is_contradiction` + `home_status._is_missed_run`) · A4's pure `run_result_verdict(last_result)` rendered in the existing readout — note `ScheduleReadback.last_result` has **no consumer in `src/` today** and `schedule_status.py:201`'s docstring wrongly claims one (fix the docstring here) — with `0x80070569` (batch-logon at RUN time; Community-sourced; registration only returns the success code `SCHED_S_BATCH_LOGON_PROBLEM`, unobservable through pywin32) as its first non-exit-code row · A9 (seasonal-window limitation surfaced, not solved).
> - **Slice D = docs (was 4)** — extend `docs/partner/headless-sftp-setup.md` (its Task Scheduler section still says *"no special handling is required"* and shows a bare `schtasks /Create` — misleading once 0047 lands) · the `runas --sftp-configure` step documented as ONE action that is both necessary (§5a) and the profile-warming step Microsoft recommends (§6 trap 2) · DECISIONS: the Credential-Manager cross-user fact, the gMSA parking and why, A8's recorded `run_highest` · ROADMAP: narrow the deferred item to gMSA / machine-scope secrets, add UPN (N8) and A9.
> - **Owner decisions still open** (surfaced 2026-09-16): release sequencing (ship 0047 alone as a patch release first?) · A10 delete-vs-in-place · approval to run the two live measurements · nothing is ever sent to a district by an agent (the owner already asked SD60 for the exact error text on 2026-09-16 morning; no reply yet).

## Problem

The district admin at SD54 (Ted Owens, MyEd BC custodian) has DistrictSync set up and working. His ask, verbatim (2026-08-17):

> I have set up the sync now, one minor thing I would like to see changed is I have to run the task under my name, I have set up a service account to do things like this so I don't have my account running tasks. If that could be added to future releases that would be great.

Districts run production tasks under service accounts so the task survives its owner leaving, rotating a password, or being disabled. Today DistrictSync cannot: `screens/setup.py:2154` passes `run_as_user=None`.

### The premise correction (found by all three reviewers, measured by the security lens)

The first draft claimed *"the engine already supports this; one hardcoded argument is the entire blocker."* **That is false.** `src/scheduler/windows.py:304-308`:

```python
has_password = bool(run_as_password)
if has_password:
    user = validate_run_as_user(run_as_user or current_run_as_user())
else:
    user = current_run_as_user()          # <- run_as_user DISCARDED, silently
```

An explicitly-supplied `run_as_user` with **no password** is silently replaced by the interactive user, and `register_task` returns `(True, "Schedule registered.")`. Measured by the security reviewer against the real function with a mocked COM sink:

| call | registered principal | return |
|---|---|---|
| `run_as_user="SVC_DistrictSync", run_as_password=None` | `CORP\ted` | `(True, …)` |
| `run_as_user="SVC_DistrictSync", run_as_password=""` | `CORP\ted` | `(True, …)` |

Unreachable today (the sole call site passes `None`) — but the UI change makes it reachable from **three** blank-password paths at once. This is exactly the shape CLAUDE.md bans: *"No permissive default on a safety-relevant parameter — make the unsafe call unrepresentable rather than defaulted."*

A second, related defect: `windows.py:304` treats `""` as "no password" (`bool`), while `task_com.apply_definition:181` treats it as "unattended" (`is not None`). So `password=""` reaches `RegisterTaskDefinition` as **`TASK_LOGON_PASSWORD` + `RunLevel Highest` with a blank credential, from a non-elevated process, with no UAC** (measured). Today only `setup.py:2155`'s `(password or None)` normalisation prevents it.

**So the engine half must be fixed first, and it is the highest-value item in this plan.**

### Switching an existing task IS supported — as delete-then-create (owner decision, 2026-09-07)

> *"we can support editing an existing and avoid malware tripwires by deleting the task and creating a new one"*

The app supports changing the principal on an already-configured install. The **implementation** is unregister-then-register, not `TASK_CREATE_OR_UPDATE` in place — because silently re-pointing an existing benign task at different stored credentials is the ATT&CK T1053.005 task-hijack signature, and EDR heuristics weight it more heavily than a fresh registration. See A10 for the cost this carries.

### What still matters after the task exists

Dropping migration does **not** drop the reconcile work — that concern is about *every subsequent Save*, not the one-time switch. Three blank-password register paths exist downstream of any UI gate (security lens, SEC-4):

1. The Register button's documented blank choice (`setup.py:1976`, "logged-on-only").
2. `_register(force_blank_password=True)` — the downgrade dialog (`setup.py:2277`), which passes the gate then blanks the password.
3. The Settings reconcile (`setup.py:2305`), which reads the **live** password field — blank on any Save that isn't a deliberate re-credential.

Path 3 is the one that bites in normal use: once Ted is running on the service account, changing the run time or a folder triggers a re-register with an empty password field. Without A1 that silently hands the nightly back to his account; with A1 it fails loud and tells him to re-enter the service-account password. A UI gate cannot cover paths 2 and 3 — the engine refusal closes all three structurally.

**Consequence for BLOCKED copy** (plan-gate): `_FOLDERS_SAVED_BLOCKED` / `_SFTP_RECONCILE_BLOCKED` (`setup_flow.py:776-787`) hardcode *"fix the run time"* as the only cause a reconcile can be blocked for. A1 introduces a second cause — a missing service-account password — so that copy must name it or it will misdirect.

## Goals / Non-goals

**Goals**
- G1 — Type a service-account username + password at the Schedule step; the task registers to that principal.
- G2 — The principal survives a Settings Save / reconcile without silent downgrade.
- G3 — Surfaces that report the **task principal** report what was registered — *without* re-pointing the surfaces that report the **keyring owner** (see A6).
- G4 — The admin gets an honest signal about whether the service account can actually run the sync, without the app shipping production data to prove it.
- G5 — Byte-identical behaviour when the field is left at its default.

**Non-goals**
- N1 — gMSA / SYSTEM / virtual accounts. `validate_run_as_user` keeps rejecting `$`.
- N2 — Machine-scope secret storage. SFTP credential stays per-user keyring.
- N3 — Shared `history.db`. Owner accepted (2026-09-06, reaffirmed 2026-09-07): *"its okay to lose run history when using a service account — he will know to check the service account logs and IT professional will use the service account and review its logs."* **N3 waives VISIBILITY, not truthfulness** — A5 remains in scope because it is about the app asserting a fault that did not occur, which is a different thing from showing nothing.
- N4 — Rotation alerting. Owner accepted (2026-09-07). Documented, not built.
- N5 — Misuse hardening. Owner decision (2026-09-07), *"trusted IT staff… KISS."* No ceremony, modal, or audit trail. **Note:** N5 waives *malice*; it does not waive *accident* controls (A3, A6, A7).
- N6 — Spawning `--sftp-configure` as the service account from in-app.
- N7 — Linux/cron (`CronScheduler.register` discards `run_as_user` at `__init__.py:192`; N7 holds structurally).
- **N8 (new, from YAGNI CUT 3) — UPN form.** `DOMAIN\user`, bare `user` and `.\user` all already validate. Nobody asked for UPN; widening a security-boundary regex on zero demand is speculative. → ROADMAP line.
- **N9 (new, from YAGNI CUT 1) — an in-app "run it now" button.** See A5.
- **N10 (revised 2026-09-07) — Rollback of a failed switch.** Windows never returns a task's stored password, so a failed re-create CANNOT be rolled back (A10). We fail loud and tell the admin the schedule is gone; we do not attempt restore.

## Approach

### A1 — Engine refusal (SLICE 1, was missing entirely)

Make the unsafe call unrepresentable at the boundary:
- Normalise `run_as_password = run_as_password or None` **once**, at `register_task`'s entry, so `""` and `None` have one meaning across both halves.
- Validate the **caller-supplied** `run_as_user` on both branches — **never** the machine-derived fallback. (Security lens: `current_run_as_user()` can legitimately return `CORP\John Smith`; the regex rejects spaces, so unconditional validation would break logged-on-only registration for those districts — a G5 regression.)
- When a caller-supplied account differs from `current_run_as_user()` and no password is given → return `(False, <canonical message>)`. Never fall back.
- `elevated_apply._do_register:151` — drop the `if password is not None` guard on `validate_run_as_user`; the module promises *"every input is re-validated here"* and `user` is the field naming the principal.

### A2 — The account is a PRINCIPAL fact

`setup_flow.TaskArgs` is documented as *"fields baked into the **action**"* (`setup_flow.py:444`); `RegisteredSchedule.unattended` sits beside it as a principal fact. Extend `RegisteredSchedule` with `run_as_user: str | None`.

> **Decided fork.** YAGNI argued for a plain `principal_changed: bool` kwarg instead (no dataclass change). Plan-gate argued to extend the record. **Ruling: extend the record** — because *two* consumers now need the principal (`schedule_reconcile` **and** `downgrade_interrupt`, per A4), which defeats YAGNI's "one comparison the view already holds" premise. `TaskArgs` is untouched, so no persisted record invalidates.

### A3 — Account and password move together

A pure gate in `setup_gates` (extended **in place**, keyword-only, defaulting to today's semantics — ruling on O1) covering `(account, password)` as a pair. And:
- `force_blank_password=True` must **also clear the account** and say so in the dialog copy — registering signed-in-only is a principal change, not just a logon downgrade.
- `downgrade_interrupt` takes the recorded principal and produces principal-aware copy.

### A4 — Signal honesty (replaces the first draft's A4)

The first draft proposed `run_task_now` + a poller + a Protocol capability + a UI button. **Cut**, on three converging grounds:
- **Unsound as designed** (plan-gate): `TaskFacts` has no `State` field and nothing in `src/` reads `IRegisteredTask.State`, so the poller cannot tell RUNNING from FINISHED and would report the *previous* run's `LastTaskResult`.
- **It ships production data** (YAGNI): `IRegisteredTask.Run` executes the real nightly — writing the output dir, archiving stale CSVs and, with `--sftp` baked, delivering a real roster zip to SpacesEDU mid-afternoon. A button that does that is not "a check".
- **It re-opens an owner-deferred item** (`ROADMAP.md:179`, *"Run one now to prove it"*), deferred on copy judgment.

**Instead** (YAGNI's middle ground): `read_schedule()` **already returns `last_result`**, and the schedule section already re-probes via `_refresh_readout` → `probe_schedule`. Add a pure `run_result_verdict(last_result)` mapping the documented exit-code contract to plain language, render it in the **existing readout line**, and point the admin at `taskschd.msc`'s own Run button in the partner guide. ~15 lines, no new COM, no poller, no side-effecting button — and it keeps working for every nightly run, not just a one-shot.

Honesty constraint: exit 3 is *any* SFTP failure (`pipeline.py:966-1010`), so the copy must say *"delivery failed — check this account has its own SFTP credential"*, never *"precisely: no credential."*

### A5 — Don't create a permanent false alarm (found by plan-gate AND YAGNI; plan-blocking)

Once the nightly runs as a service account, its run records land in **that** profile's `history.db`, so the admin's own store has a permanent record gap. Two existing predicates then fire **every night, forever**:
- `schedule_status._is_contradiction` (`schedule_status.py:193`) — *"Your last scheduled run reported a problem… re-register the schedule"*, which walks Ted straight back into the bug A1 fixes.
- `home_status._is_missed_run` (`home_status.py:963`).

This lands on Home, the Run History banner **and** the Setup rail badge. N3 accepted losing Run History *rows*; **nobody accepted the app asserting a fault that isn't there.** Fix: one extra input — a recorded foreign principal means a record gap is *expected*, not a contradiction.

### A6 — Split "keyring owner" from "task principal" (security lens SEC-3)

`_run_as_account()` (`setup.py:2469`) serves **both** concepts today because they are the same value. `setup.py:2603` renders: *"Your delivery password is saved and readable by `<it>`."* A naive G3 sweep would make that read *"…readable by SVC_DistrictSync"* — the precise inverse of the truth, giving a false all-clear on the single most likely real failure. Rename to `_keyring_owner_account()` **before** any G3 wiring, and add `setup.py:2603` to a "must NOT change" list beside G3's "must change" list.

### A7 — Principal-aware failure coaching (security lens SEC-13)

`setup_errors.py:107` currently coaches *"make sure you entered **your** Windows account password (not your Windows Hello PIN — for a Microsoft Account, your microsoft.com password)"*. Against a service account every clause is wrong, and it actively coaches a **personal cloud credential** into a service-account field. `setup_errors.py` was missing from the first draft's affected files.

### A8 — Recorded, not changed: `run_highest` (security lens SEC-10)

Every unattended task registers at `TASK_RUNLEVEL_HIGHEST` (`windows.py:257` default; nothing in `src/` passes `False`). Under 0046 that becomes a shared, non-expiring service account with a stored password and admin run level, nightly. **We are not changing the default** — dropping to Limited could break a district whose output dir is only writable by an elevated token (G5 risk). We are **recording it as a decided choice** in DECISIONS, and the partner guide must say *"grant Modify on the input/output folders"*, never *"Full Control"* or *"add to Administrators"*.

### A9 — Known limitation: the seasonal sync window (plan-gate)

`main._cli` loads the **running** account's `AppConfig`; a service account has none, so an enabled seasonal window never pauses the nightly while the UI still says "resumes `<date>`". Opt-in and default-off, so it bites only districts that enabled it. **Fix scope: surface it, don't solve it** — a note in Settings when a window is enabled *and* a foreign principal is recorded, plus a documented limitation. Solving it needs the shared-profile work N3 excluded.

### A10 — Replace, not update (and what it costs)

`apply_definition` registers with `TASK_CREATE_OR_UPDATE` (`task_com.py:115`, used at `:189` and `:200`) — **one atomic call**: if it fails, the existing task survives untouched. Delete-then-create trades that away, and the trade is **not** recoverable: the task XML carries the principal but never the credential, so a failed re-create cannot restore the original unattended task. The district is left with **no nightly sync**.

Three decisions follow:

1. **Confined to principal changes.** Delete-then-create fires ONLY when the recorded principal differs from the requested one — precisely the hijack-shaped case the tripwire concern is about. Every other re-register (run time, folders, district — the common Save) keeps today's atomic `TASK_CREATE_OR_UPDATE`. This keeps the tearable path rare and deliberate instead of making all 19 districts' routine Saves tearable for no benefit.
2. **Two UAC prompts, reusing the two existing operations** (owner decision, 2026-09-07: *"can prompt creds twice"*). No new elevated op. The switch is `delete_task` (which already falls back to `delete_task_elevated` on the access-denied a RunLevel-Highest task produces → prompt 1) followed by `register_task` (which already self-elevates via `_register_elevated` on the password path → prompt 2). Both paths are built, tested and read-back-confirmed today, so `elevated_apply`'s dispatch, payload shape and privileged-half validation surface are **unchanged** — the biggest risk reduction available in Slice 2. The UI warns up front that permission will be asked twice.

   *Consequence accepted:* the tear window now spans a user interaction — if the second prompt is declined or times out, the old task is already gone. Mitigated by (3), and cheap to recover: the account and password are still in the form, so retry is one click.
3. **Fail loud on the tear.** If the delete succeeds and the register fails, the result must say so explicitly — *"your previous nightly schedule was removed and the new one could not be created; you have no scheduled sync right now"* — and clear `schedule_registered` so the readout reports MISSING honestly rather than showing a schedule that no longer exists.

Rejected: snapshotting the task XML for rollback (the credential is absent, so the restore would silently downgrade an unattended task to a broken one — worse than failing loud).

## Affected files

**Engine**
- `src/scheduler/windows.py` — A1 refusal + password normalisation + canonical message.
- `src/scheduler/elevated_apply.py` — unconditional `validate_run_as_user` (SEC-14) **only**; no new op, no payload change (A10.2).
- `src/utils/validators.py` — **no regex change** (N8). Correct the stale docstring: `validators.py:40-44`/`:129-137` still describe the PowerShell `-User` / child-env transport that plan 0041 S1b **retired**. Leaving a dead threat model beside a security boundary is how a maintainer reasons about the wrong interpreter.

**Config**
- `src/config/app_config.py` — `schedule_run_as_user: str = ""` + a naming-contract comment (the `schedule_` prefix keeps it out of `_ADVISORY_FIELD_PREFIXES` **by construction** — ruling on O3; mirror the `sync_window_*` comment at `:220`).

**Pure UI logic (COUNTED)**
- `src/ui_flet/setup_flow.py` — `RegisteredSchedule.run_as_user`; principal-aware `schedule_reconcile` + `downgrade_interrupt`; `_FOLDERS_SAVED_BLOCKED`/`_SFTP_RECONCILE_BLOCKED` copy (`:776-787`) must name the missing-password cause A1 introduces.
- `src/ui_flet/setup_gates.py` — `can_register_schedule` extended in place (keyword-only, defaulted).
- `src/ui_flet/schedule_status.py` — `run_result_verdict`; `_is_contradiction` foreign-principal input.
- `src/ui_flet/home_status.py` — `_is_missed_run` same input.
- `src/ui_flet/setup_errors.py` — principal-aware coaching.

**View glue (coverage-omitted)**
- `src/ui_flet/screens/setup.py` — account field · gate wiring · `_keyring_owner_account` rename · honest banner · record/clear the principal · `force_blank_password` clears the account · readout verdict · sync-window note.

**Docs**
- `docs/partner/headless-sftp-setup.md` — **extend, do not add a file** (YAGNI CUT 4): it already owns `--sftp-configure` and has a Task Scheduler section.
- `docs/claugentic-DECISIONS.md` — supersede 2026-06-05; record A1, A8, and the N3 consequence. Target ~4 lines, not five paragraphs.
- `docs/claugentic-ROADMAP.md` — narrow the deferred item to gMSA/machine-scope; add the UPN line; record A9.

## Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | Silent identity downgrade — **the measured defect**, reachable from three paths. | A1 engine refusal (structural, not UI discipline) + A3. Pinned by asserting the *registered* `RegisterParams.user`, not the return tuple. |
| R2 | `password=""` → unattended + Highest with blank credential, no UAC. | A1 normalisation; parametrised test over `{None, "", "x"}` asserting the logon constant in **both** halves. |
| R3 | False all-clear on the keyring (SEC-3). | A6 rename before G3 wiring + explicit must-not-change list. |
| R4 | **Permanent false amber** on Home/Run History/rail badge. | A5. This is a regression the feature *creates*; it is not optional. |
| R5 | Upgrade regression for 19 shipped districts. | A2 leaves `TaskArgs` untouched → no persisted record invalidates; empty default → `None` → today's path. Pinned by a pre-0046-record test. |
| R6 | SD74 snapshot / ETL output. | Zero `src/etl/**` + `config/mappings/**` touch — assert empty diff at verify. |
| R7 | Password leak via the new field. | Contract unchanged. Note `windows.py:522-532`'s raw `payload` dict carries the password with **no `repr=False`** — safe only because nothing formats it; forbid adding a debug log there. |
| R8 | Plan/docs trip the no-plaintext-email CI gate — **the first draft did** (measured with `scripts/check_no_emails.py`). | N8 removes the UPN examples entirely; any remaining example uses an IANA-reserved domain. |
| R9 | Different-account UAC fails closed (`DSYNC_DIFFERENT_ACCOUNT`). Pre-existing, likelier here. | Out of scope; ensure the classified message is reachable. Documented. |

## Test strategy

- **Engine (Slice 1):** the refusal, asserting registered `RegisterParams.user` not the tuple; `{None, "", "x"}` logon-constant parity across `windows`/`task_com`; spaced-local-account fallback still registers (G5); elevated-child validation unconditional; existing leak tests extended.
- **Pure:** reconcile re-registers on account-only change; pre-0046 record unchanged; gate pairs account+password; `run_result_verdict` over 0/1/2/3/never-run/unknown; A5 predicates quiet under a foreign principal and still fire without one.
- **View:** typed account reaches `register_task`; banner names the registered account; `setup.py:2603` **unchanged**; wizard/Settings two-mount parity.
- **Regression:** full suite · SD74 snapshot · `make validate-config` · tree-check · ruff/mypy/bandit · `check_no_emails.py`.

## Decomposition (slices)

- [x] **Slice 1 — Engine correctness.** LANDED 2026-09-15 — see `## Slice 1 implementation notes`. A1 + password normalisation + elevated-child validation + docstring correction. **Lands complete and is valuable alone:** it makes a currently-latent misregistration unrepresentable, independent of any UI.
- [ ] **Slice 2 — The feature.** Config field · gate · reconcile/downgrade principal · A6 rename · account field · honest banner · A7 coaching. **Vertical** — ships G1/G2/G3.
- [ ] **Slice 3 — Signal honesty.** A5 false-amber fix · `run_result_verdict` in the existing readout · A9 note. **Lands complete** because it consumes only Slice 2's recorded principal.
- [ ] **Slice 4 — Docs + decisions.** Partner-guide section · DECISIONS · ROADMAP.

Order matters: 1 → 2 → 3 → 4. Slice 1 is independently landable today.


---

## Slice 1 implementation notes  _(2026-09-15)_

**Landed:** `windows.py` refusal + password normalisation · `elevated_apply.py` unconditional
`validate_run_as_user` + the same normalisation · `validators.py` docstring correction ·
16 new test cases (12 in `test_scheduler_runas.py`, 4 in `test_elevated_apply.py`), of which
**7 fail against the pre-fix engine** — mutation-checked by restoring `HEAD`'s copy of each
module and re-running, so none of them is a vacuous green · DECISIONS entry.

Gates, all read rather than assumed: full suite **5741 passed / 41 skipped, 96.11% coverage**
(exit 0, 15:00); `ruff check` + `ruff format --check`; `mypy src/ --exclude 'src/ui_flet'`;
`bandit -r src/ -q -c pyproject.toml` (the new canonical message needed a `# nosec B105` — a
NAME-matched false positive, same as `main.SFTP_PASSWORD_ENV_VAR`); tree-check;
`check_no_emails.py`; all 20 configs. Scheduler coverage: `windows.py` 96%, `elevated_apply.py`
98%, with no uncovered line inside the changed blocks.

**One deviation from the Spec, deliberate.** The spec's branch table validated the
caller-supplied account on both branches and *never* the machine-derived fallback. Shipped
instead as three branches:

| requested | password | user | validated? |
|---|---|---|---|
| a DIFFERENT account | yes | the requested account | **yes** (caller input) |
| a DIFFERENT account | no  | — | **refused** before any COM call or UAC prompt |
| absent / the current account | yes | the current account | yes — *unchanged from today* |
| absent / the current account | no  | the current account | no — *unchanged from today* |

Why: the spec's own rationale (a legitimate `PC\John Smith` must keep registering) is about
the LOGGED-ON-ONLY branch. Applied literally to the password branch it would have let the
DIRECT path register a spaced fallback that `elevated_apply`'s now-unconditional
re-validation refuses — a direct-vs-elevated divergence in exactly the pair of paths 0041
S1b made single-source. This shape keeps G5 byte-identical *and* makes the elevated child's
floor a true no-op on every legitimate request. Recorded in DECISIONS 2026-09-15.

**Carried into Slice 2 (not debt, but must not be forgotten):**
1. `setup_errors.classify_schedule_error` has **no branch** for `_MSG_ACCOUNT_NEEDS_PASSWORD`
   — it falls through to the generic "didn't go through (Details: …)" copy. Unreachable from
   the UI today (`setup.py:2154` passes `run_as_user=None`); A7 owns the real copy.
2. A request naming the CURRENT account case-insensitively is **not** a principal change.
   That is what makes the spec'd prefilled account field safe — but the field must still send
   the typed value, not a sanitised one, or the case-insensitive comparison is bypassed.
3. `register_task` still **raises** `ValueError` (it does not return `(False, msg)`) for a
   malformed account. Slice 2's UI must catch it or gate on shape first — `setup.py`'s
   `_register` currently has no `except ValueError`.

**S0 and the CREATE_OR_UPDATE spike:** see `## Investigations`.

---

## Review  _(Stage 3 — three adversarial passes, 2026-09-07)_

**Verdict: CHANGES REQUIRED** → all incorporated above.

**Convergent finding (all three, independently):** the engine silently discards `run_as_user` on the no-password path and reports success — inverting the plan's premise. Security lens *measured* it; I verified `windows.py:304-308` directly.

| Source | Finding | Disposition |
|---|---|---|
| all three | Engine silent substitution | **Accepted** → A1, moved to Slice 1 |
| security (measured) | `""` vs `None` logon-type disagreement | **Accepted** → A1 |
| security | Three blank-password entry points below the gate | **Accepted** → A3 |
| security | `_run_as_account()` conflation → false keyring claim | **Accepted** → A6 |
| plan-gate + YAGNI | Permanent false amber on Home/rail | **Accepted** → A5 |
| plan-gate | A4 poller unsound (`TaskFacts` has no State) | **Accepted** → A4 cut |
| YAGNI | A4 ships production data; duplicates deferred ROADMAP item | **Accepted** → A4 cut to pure verdict |
| YAGNI | UPN widening speculative, argued from a stale docstring | **Accepted** → N8; docstring corrected |
| security (measured) | Plan file trips `check_no_emails.py` | **Accepted** → R8 |
| security | `setup_errors.py` missing; coaches personal MS-account password | **Accepted** → A7 |
| security | `run_highest=True` inherited silently | **Accepted as recorded choice** → A8 |
| plan-gate | Seasonal window breaks under a service account | **Accepted as surfaced limitation** → A9 |
| security | Elevated child skips `user` re-validation | **Accepted** → A1 |
| YAGNI | Drop `RegisteredSchedule` extension for a bool kwarg | **Rejected** — two consumers need it (A2 fork, recorded) |
| YAGNI | Collapse to 2 slices | **Partly** — 4 vertical slices; Slice 1 lands alone |

**O1** — extend `can_register_schedule` in place, keyword-only, defaulted (a sibling predicate is the drift the module docstring exists to prevent).
**O2** — dissolved with A4.
**O3** — **not** advisory; the `schedule_` prefix keeps it counted by `_carries_chosen_settings` by construction. Add the naming-contract comment.

---

## Spec

### Slice 1 — Engine correctness

**`src/scheduler/windows.py`**
```python
_MSG_ACCOUNT_NEEDS_PASSWORD = (
    "A password is required to schedule the task for a different account."
)

# in register_task, replacing lines 304-308:
run_as_password = run_as_password or None          # ONE spelling of "no password"
requested = (run_as_user or "").strip()
current = current_run_as_user()
if requested:
    user = validate_run_as_user(requested)          # caller input only, never the fallback
    if run_as_password is None and user.casefold() != current.casefold():
        return False, _MSG_ACCOUNT_NEEDS_PASSWORD   # refuse; never substitute
else:
    user = current
has_password = run_as_password is not None
```
- Export `_MSG_ACCOUNT_NEEDS_PASSWORD` for `setup_errors` to key off (canonical-message contract).
- `password=run_as_password` at `:337` now carries the normalised value.

**`src/scheduler/elevated_apply.py:151`** — `user = validate_run_as_user(str(payload["user"]))`, unconditional.

**`src/utils/validators.py`** — docstring only: replace the PowerShell `-User` / child-env rationale with the COM `RegisterTaskDefinition` BSTR reality. **No regex change.**

**Tests** (`tests/test_scheduler_runas.py`)
- `test_explicit_account_without_password_is_refused` — asserts `(False, _MSG_ACCOUNT_NEEDS_PASSWORD)` **and** `register_task_definition` never called.
- `test_empty_password_is_not_an_unattended_registration` — parametrised `{None, "", "x"}`, asserting `TASK_LOGON_*` in both `windows` and `apply_definition`.
- `test_same_account_without_password_still_registers` — case-insensitive match → today's interactive path (G5).
- `test_spaced_local_account_fallback_still_registers` — `USERNAME="John Smith"`, no password → registers (guards the SEC-1 over-fix).
- `test_elevated_child_validates_user_without_password`.

**Acceptance:** an explicit foreign account with a blank password cannot register, on any path; `""` and `None` are indistinguishable downstream; no existing test changes behaviour.

### Slice 2 — The feature

**`app_config.py`** — `schedule_run_as_user: str = ""`; written only at confirmed register; cleared in the same block as `schedule_unattended`/`schedule_task_args` at unregister. Naming-contract comment mirroring `:220`.

**`setup_flow.py`** — `RegisteredSchedule.run_as_user: str | None`; `registered_schedule(..., raw_run_as_user)`; `schedule_reconcile` returns `REREGISTER` when the recorded principal differs from pending; `downgrade_interrupt` gains the recorded principal and principal-aware copy; the two BLOCKED strings gain the missing-service-account-password cause.

**`setup_gates.py`** — `can_register_schedule(config_complete, run_time, *, account="", current_account="", password="")`; returns `False` when `account` is set, differs case-insensitively from `current_account`, and `password` is blank. Existing call sites compile unchanged.

**`screens/setup.py`** — `:1961` caption → `ft.TextField` prefilled from `cfg.schedule_run_as_user or scheduler.run_as_user()`; password label becomes `f"Password for {account}"`; `:2469` `_run_as_account` → `_keyring_owner_account` (**`:2603` must keep rendering the keyring owner**); `:2112` banner names the registered account; `force_blank_password=True` clears the account too.

**Tests:** account reaches `register_task`; banner names the registered account; `:2603` string unchanged under a foreign principal; two-mount parity; reconcile re-registers on account-only change; pre-0046 record parses unchanged.

### Slice 3 — Signal honesty

- `schedule_status.run_result_verdict(last_result: int | None) -> tuple[Verdict, str]` — 0 healthy · 1 ETL failed · 2 bad arguments · 3 *"ran, but delivery failed — check this account has its own SFTP credential"* · never-run · unknown code degrades.
- `_is_contradiction` / `_is_missed_run` gain `foreign_principal: bool`; a recorded foreign principal makes a record gap **expected**, not a fault.
- Render the verdict in the existing readout; Settings note when a sync window is enabled with a foreign principal (A9).

**Tests:** every exit code maps distinctly; unknown degrades; both predicates quiet under a foreign principal and unchanged without one.

### Slice 4 — Docs

`docs/partner/headless-sftp-setup.md` gains **one section**, leading with the switch procedure (*unregister the existing nightly task, then register with the service account* — N10); then: grant "Log on as a batch job"; grant **Modify** (never Full Control) on input/output; use **UNC paths, not mapped drives**; run `--sftp-configure` as the service account; the three PII locations that now exist under that account; Run History goes quiet; rotation kills the task silently. DECISIONS ~4 lines. ROADMAP: narrow the deferred item, add UPN + A9.


---

## Open fork  _(added 2026-09-07 after product-designer + security-lens design review)_

The owner asked to also provision the service account's **own** SFTP credential in-app, using the service-account credentials collected at the Schedule step (reversing N6). Two reviews say the proposed mechanism should not be built as described.

### S0 — an untested premise sits under the whole SFTP half (MEASURE FIRST)

Nobody has verified that a task running under `TASK_LOGON_PASSWORD` as `SVC_X` **can read `SVC_X`'s Credential Manager**. That read needs the account's DPAPI master key, which needs a loaded profile. `task_com.py:110-112` bans `TASK_LOGON_S4U` for a *network-token* reason and says nothing about profile/DPAPI.

**If a batch-logon task runs without the profile loaded, no delivery mechanism fixes this** and the only answer is machine-scope storage. Measurement: register a task as a throwaway local account with a password, action `DistrictSync --sftp-test`, read `LastTaskResult` back via `read_schedule`. Binary result. Record in DECISIONS.

### Why `CreateProcessWithLogonW` is rejected

| # | Finding |
|---|---|
| S1 | It performs an **interactive** logon, so the account needs `SeInteractiveLogonRight` — while the nightly needs only `SeBatchLogonRight`. Service accounts are routinely under a "Deny log on locally" GPO; that hardening is *why they exist*. We would be asking districts to widen the account permanently for a one-time step. |
| S2 | The `--sftp-password-stdin` channel **does not cross the boundary** (no `bInheritHandles`; the child is created by seclogon). The released exe is GUI-subsystem, so `sys.stdin is None` → uncaught `AttributeError` at `main.py:205`, no stderr, parent sees a bare exit code. |
| S3 | The natural repair (env var) breaks the live no-env contract (`windows.py:16-18`), and if the block is built from `os.environ`, `USERPROFILE` propagates → `Path.home()` → `migrate_legacy_data_dir()` **copies the admin's `~/.districtsync` (config.json with `identity_email`, `history.db`, logs) into the service account's profile**. |
| S12 | **Temp-profile false success:** if profile creation degrades, the keyring write "succeeds", the child exits 0, the UI says "delivery is set up", and the nightly fails forever — the exact bug the feature exists to fix, now behind a green banner. |
| S14 | **EDR.** "App launches itself as another user with a plaintext credential" is a T1078/runas heuristic. Plan 0041 exists because Bitdefender ATC blocked this product live on 2026-08-04. This **directly contradicts the owner's own delete-then-create rationale** ("avoid malware tripwires") — it is a larger tripwire than the one being avoided. |
| S15 | `tests/test_schedulers.py:401-415` claims *"the scheduler spawns no child process at all"* but only asserts two imports — it would stay **green while its own docstring became false**. |

### The three options

1. **Keep N6** — document the `runas` procedure (already in Slice 4). Zero code, zero new credential channel, zero new privilege.
2. **Machine-scope secret** (the ROADMAP item N2 defers): one DACL'd/DPAPI-LocalMachine blob under `%ProgramData%\DistrictSync`, read by `_sftp_upload` when the keyring is empty. Removes the cross-account problem entirely — no child, no logon right, no profile creation, no EDR signature. Cost: a real confidentiality downgrade, paid once, visibly, in code we own. **Also the only option that survives an S0 failure.**
3. One-shot **batch**-logon task (not `CreateProcessWithLogonW`) with a DACL'd secret file — reverses A10.2's risk reduction and downgrades the DPAPI boundary.

### Settled regardless of the fork (product-designer)

- **UI shape:** turn the static *"This task will run as: X"* (`setup.py:1961`) into a two-option chooser; reveal the account fields inline. **No sixth step, no rail change**, and `_build_schedule_section` is shared, so the wizard step and Settings both get it free.
- **Reframe:** this is a *handover*, not a field edit — **"prove the account can do the job, then give it the job."**
- **Provision-first is a safety property**, not a preference: register-first leaves a task running as an account with no credentials.
- **Spike worth doing (may delete the scariest state):** does `TASK_CREATE_OR_UPDATE` already replace the **principal**? If yes, the delete step, the second UAC prompt and the entire "no schedule at all" tear (A10.3) all vanish.
- Home's permanent-amber (A5) and the sync-window regression (A9) both **re-confirmed independently**.


---

## Slice B — acceptance-criterion correction (Verify, 2026-09-16)

The drafted AC for A6 read *"`_keyring_owner_account` has exactly one call site"*. That was a stale
expectation carried from the draft, and the implementation has EIGHT — because the review's own
journey gap 8 directed reusing it as the defensive resolver (`scheduler.run_as_user()` is now called
per keystroke, and `getpass.getuser()` can raise; one unguarded raise would strand the whole schedule
section).

**The call-site COUNT was never the property worth protecting.** The property is that the delivery
banner names the account whose Credential Manager actually holds the secret — the KEYRING OWNER —
and never the task principal, because printing the principal there would be the exact inverse of the
truth and a false all-clear on the failure most likely to hit a service-account district (Credential
Manager has no cross-user scope, handover §5a). Verified at `screens/setup.py` (`"Your delivery
password is saved and readable by …"` resolves `_keyring_owner_account()`), and the success banner
names the REGISTERED principal with the signed-in account only as the fallback for the case where
they are the same. All eight call sites carry "the signed-in account" semantics.

**AC restated:** the delivery banner resolves the keyring owner, the success banner resolves the
registered principal, and neither substitutes the other — pinned with a positive twin, not by counting
call sites.

## Owner decisions — 2026-09-16 (asked and answered in session; do not re-litigate)

1. **Switching a LIVE nightly sync onto a service account: the app REFUSES and routes the admin to
   "Remove nightly sync" then "Schedule nightly sync" again.** It does NOT orchestrate the swap itself.
   This keeps the delete-then-create shape the owner already chose (their own EDR-tripwire experience,
   above) but performs it *with the admin watching*: no hidden tear window where the district has no
   schedule, no second UAC prompt the app owns, and nothing to roll back when a step fails halfway —
   which makes A10.3 / N10 moot rather than solved. An app-orchestrated switch stays UNBUILT until a
   district actually asks for it.
2. **The one-time `--sftp-configure` step is named in BOTH places.** A short line in the schedule
   section on screen (rendered only when delivery is enabled AND a service account is in use), pointing
   at the partner guide for the full steps. Rationale the owner accepted: Credential Manager has no
   cross-user scope (handover §5a), so this is the single most likely thing to silently break a
   district's nightly DELIVERY — and an admin mid-setup does not have the partner guide open. The guide
   still carries the complete procedure. No email address in either surface.

**Ordering correction (orchestrator, 2026-09-16).** The drafted Slice C spec deferred its second half —
suppressing the "did this run?" alarms under a foreign principal — on the grounds that the recorded
principal "doesn't exist in the code yet". **Slice B adds exactly that fact** (the principal on
`RegisteredSchedule`), so with the handover's B → C order the deferral does not apply: C builds BOTH
halves. This matters for the district outcome, not just tidiness — shipping B without C's suppression
would make `schedule_status._is_contradiction` and `home_status._is_missed_run` fire **every night,
forever**, on every district that adopts a service account. C is therefore not optional after B.

## Investigations  _(run before Slice 2, per the Open fork)_

### Spike — does `TASK_CREATE_OR_UPDATE` already replace the PRINCIPAL? **PARTIAL: strong yes on the mechanism, not yet conclusive on a foreign account.**

Measured live on this Windows 11 host against the real Task Scheduler COM API, with a
throwaway task (`DistrictSync_Spike0046`, `cmd.exe /c exit`, deleted and read back as gone):

| step | registered as | read back `Principal.UserId` / `LogonType` / action args |
|---|---|---|
| 1. create | `DESKTOP-…\shan.peiris`, interactive token | `shan.peiris` / 3 / `/c exit 0` |
| 2. update, same name | `shan.peiris` (bare), interactive token | `shan.peiris` / 3 / **`/c exit 1`** |
| 3. update, same name | `NT AUTHORITY\SYSTEM`, service-account logon | **failed, `0x80070005` access denied** (we are not elevated) |

What step 2 establishes: `TASK_CREATE_OR_UPDATE` **replaces the whole definition**, not merges
it — the action arguments changed under a name that already existed — and the stored `UserId`
is written from the `userId` argument on every call (step 1's qualified `DOMAIN\user` came back
NORMALISED to the bare local name, which only happens if the principal is re-derived at
registration). Both halves of "the principal is rewritten in place" are therefore observed.

What it does NOT establish: the two registrations named the SAME account in two spellings. A
genuinely different principal was attempted (step 3) and refused for lack of elevation, not for
lack of support. Closing that gap needs an elevated process **and a second account** — i.e. the
same prerequisite as S0 below, which is why the two should run in one session.

**Consequence if it lands as "yes":** A10's delete step, its second UAC prompt and the entire
"no schedule at all" tear window (A10.3, N10) become unnecessary *as a mechanism*. They would
then rest solely on the EDR argument — and note that the owner's stated rationale (2026-09-07,
*"avoid malware tripwires by deleting the task and creating a new one"*) and A10's rationale
(in-place re-pointing IS the T1053.005 signature) point the same way but from opposite premises.
That is an owner decision, not a measurement.

### M1 — what does a MISTYPED run-as account name report? **MEASURED 2026-09-16, elevated. Owner authorised the UAC prompt in-session.**

Run against the real Task Scheduler COM API (`Schedule.Service`, raw — deliberately NOT through
`task_com`, which was mid-edit for plan 0047), elevated, three throwaway tasks prefixed
`DSYNC_PROBE_0046_`, all deleted and the folder swept for survivors (none). No real credential
was used: both failing cases were driven with a deliberate junk password string.

| case | registered as | password | result |
|---|---|---|---|
| bogus account name | `DSYNC_NOSUCHACCOUNT_46`, `TASK_LOGON_PASSWORD` | junk | **`0x80070534`** (`ERROR_NONE_MAPPED`), `excepinfo[2]` = `"(21,8):UserId:"` |
| real account, wrong password | `DESKTOP-…\shan.peiris`, `TASK_LOGON_PASSWORD` | junk | **`0x8007052E`** (`ERROR_LOGON_FAILURE`), `excepinfo[2]` EMPTY |
| delete-then-create | current user, interactive token | none | create -> read back -> delete -> **verified gone (`0x80070002`)** -> re-create under the SAME name, clean |

**What this settles for Slice B.** `0x80070534` was *Community-sourced* for the general case
(handover §5c documents it only for the SYSTEM + NULL + NULL + `TASK_LOGON_SERVICE_ACCOUNT`
shape). It is now **measured on our exact COM path** for the case SD54 will actually hit first —
a typo'd service-account name — and it is **distinct from a wrong password**. So B maps it to its
own canonical and its own classifier branch ("check the account name"), instead of dropping a
name typo into the credential branch and looping the admin on the password. Both halves of the
pairing are evidenced, which is what the `hresult_for` inverse requires.

Two secondary findings worth carrying:
- The real code arrives in `excepinfo[5]` under a `DISP_E_EXCEPTION` (`0x80020009`) wrapper, exactly
  as `task_com.com_error_scode` assumes — that machinery is confirmed against a second code.
- `0x80070534`'s `excepinfo[2]` is a **field locator** (`"(21,8):UserId:"`), not prose. Plan 0047's
  unmapped branch will therefore render `"(21,8):UserId: (0x80070534)"` — carrying the code, which
  is the point, but unreadable as an explanation. That is an argument FOR mapping it in B, not a
  defect in 0047.

**A10 — decided (owner, 2026-09-16): DELETE-THEN-CREATE.** Not on the mechanism (the spike below
plus this probe's third row show in-place replacement works and delete-then-create is clean), but
on the owner's own first-hand evidence: *"updating an existing task triggered malware tripwires in
the past; delete and create new task untested but hopefully less chance of triggering malware
alarms."* The measurement removes the remaining doubt about the alternative's cost — re-creating
under the same name after a verified-gone delete is clean, so the tear window is the only price.

**Not measurable on this host:** the machine is **standalone (`PartOfDomain=False`, WORKGROUP)**, so a
genuinely different DOMAIN principal cannot be authenticated here at all. A throwaway domain account
would not help; that half stays unmeasured and B must not assume it.

### S0 — can a batch-logon task read its own account's Credential Manager? **MEASURED 2026-09-16: YES, on the mechanism. Owner authorised the prompts in-session.**

**Result.** A throwaway task was registered with `TASK_LOGON_PASSWORD` (logon type 1 — "run
whether user is logged on or not", i.e. a BATCH logon), pointed at a small child script, and run.
It returned `LastTaskResult = 0`, and in that batch session the child **read back the Credential
Manager entry the same account had written interactively** — `read_ok: true`, `matched: true`,
against `keyring.backends.Windows.WinVaultKeyring` on both sides. `%USERPROFILE%` resolved to the
real profile (`profile_looks_default: false`), so KB 2968540's profile-load race did not fire here.
The task was deleted, the folder swept (no survivors) and the throwaway keyring entry removed. No
real credential was involved: the probe wrote and deleted its own random token under the service
name `DistrictSync_PROBE_0046`. The owner typed the account password into a `getpass` prompt in the
elevated console; it reached only the `RegisterTaskDefinition` BSTR and was never written, logged
or returned.

**What this settles:** the SAME-ACCOUNT half of S0 — the only half that was open, since the
cross-account half is already Documented and NEGATIVE (Credential Manager has no cross-user scope,
handover §5a). A batch logon does get a credential set, as the `hh994565` "scheduled task or batch
job" line says. So Slice B's delivery half rests on a measured mechanism, not an assembled chain.

**What it does NOT settle — carry these into B's spec, do not let a green here launder them:**
1. **The account was interactively signed in at the time**, so its profile was already warm. A real
   district service account that has never logged on interactively is the untested case. This is
   precisely why `runas --sftp-configure` is documented as ONE action that is both *necessary*
   (it is the only way that account's own credential gets written) and Microsoft's recommended
   *profile-warming* step (§6 trap 2). The measurement strengthens that framing rather than
   removing the step.
2. **Standalone Windows 11 Home, `PartOfDomain=False`.** Trap 1 — a domain account's first-ever
   DPAPI use needing a reachable writable DC, failing `0x80090345` — cannot be reproduced here, and
   SD54 is a DOMAIN account (owner, 2026-09-16). B's docs must still cover it.
3. **Trap 3** (the temporary-profile fallback, Event ID 1511) is a hotfixed bug class that a healthy
   host will not show. Unreproduced, not disproved.

The plan calls this cheap. It is cheap in code and not cheap in consent: the measurement needs a
throwaway **local account with a password** created on the host, an elevated registration, and at
least two UAC prompts. It also needs the credential to be written into that account's
Credential Manager first, which itself requires an interactive `runas` as that account (and
creates its profile — the very thing whose absence is the hypothesis under test, so the write
step must not be allowed to mask the result).

Validity caveat to record with whatever it returns: this host is **Windows 11 Home, standalone**.
Profile-load and DPAPI behaviour under `TASK_LOGON_PASSWORD` is OS-level rather than
domain-specific, so the result should generalise — but a district file server is domain-joined
with roaming-profile and GPO policy this host cannot reproduce. A green here is evidence, not a
guarantee; a RED here is decisive (if it fails on the simplest possible host it will not pass on
a hardened one).
