# 0047 — Schedule-failure diagnosability (plan 0046's "Slice A", shipped alone)

- **Status:** In Review (2c done — the 2b panel's 123 findings folded in; awaiting the Stage 3 plan-gate → owner approval)
- **Resumable from:** Stage 3 plan-gate verdict.
- **Blockers:** none. (SD60's exact error text was requested by the owner on 2026-09-16 morning; a reply would *confirm* the policy hypothesis but the slices do not wait on it.)
- **Flags:** `split Slice A out of 0046 into its own plan — two plan files over one lingering plan` · `two slices (A1 engine · A2 classifier+docs) rather than one ~23-file diff — each lands complete; A1's canonical split is safe alone because the classifier's fallback already handles a string it does not know` · `classify_schedule_error gains a REQUIRED keyword-only account_is_current (three mechanical tokens in screens/setup.py) — the panel's security and contracts lenses both refused a defaulted safety-relevant parameter, and the YAGNI sentinel's "cut it" would leave 0046-B free to forget it silently` · `ErrorCard detail becomes selectable=True (one word in components.py) — the house rule for a value an admin must relay off a locked-down server; a design-system touch, so noted in DESIGN_SYSTEM.md` · `the two shipped "check the schedule status below" strings are corrected to "above" in the same pass (the readout renders first) — a factual fix to owner-approved copy` · `delete-path failures now log too (unless the message is the absent-task canonical) — the earlier "stays unlogged" flag was refuted: read_schedule already discriminates HR_NOT_FOUND by scode 200 lines below`
- **Disposition at close:** two slices; done when both land.
- **Roadmap item:** none yet — a live district blocker (SD60, 2026-09-14), surfaced by the 0046 handover §12.
- **References:** `.claude/plans/0046-HANDOVER.md` §3 / §5(b) / §6 (the SSH-vs-S4U note) / §12 · `.claude/plans/0046-service-account-scheduled-task.md` (A7) · `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-09-15, 2026-06-25) · `docs/claugentic-INVARIANTS.md` (S4U entry)

> **Honesty constraint (owner, handover §12):** this work makes a schedule-registration failure *legible*. If the policy is SD60's cause, **no app change makes their unattended sync work** — the district must lift the policy for that computer, or run logged-on-only, or run by hand. Nothing in the copy, the CHANGELOG, the partner doc or a district email may imply otherwise.

## Problem

A district admin at SD60 tried to enable the nightly sync on 2026-09-14, got a failure, ran the app as administrator, got *the same* failure, and reported *"I didn't see anything informative in the log."* Seven defects, each verified in the code on 2026-09-16 by the code map and re-verified by the 2b panel:

| # | Defect | Evidence |
|---|---|---|
| A1 | `0x80070520` (`ERROR_NO_SUCH_LOGON_SESSION`) is unmapped. It falls to Windows' own text (`task_com._canonical_message`, `src/scheduler/task_com.py:296-302`), then to the classifier's generic branch, which says **"Try again in a moment"** — advice that can never work for a policy. | `0x80070520` absent from all of `src/`; fallback at `src/ui_flet/setup_errors.py:121-124` |
| A2 | `SCHED_E_ACCOUNT_INFORMATION_NOT_SET` (`0x8004130F`) and `ERROR_LOGON_FAILURE` (`0x8007052E`) share one canonical string, so the table is not injective and exact-equality classification cannot tell them apart. An admin whose task has *no saved account information* is told to retype a password forever. | `task_com.py:84-89`; pinned by `tests/test_task_com.py:91` |
| A3 | `classify_schedule_error` has **no credential branch**: `"The user name or password is incorrect."` matches nothing and lands in the generic fallback. The only credential coaching sits inside the `access_denied and elevated` branch (`setup_errors.py:107-114`), which a wrong password does not reach on the COM path (live-observed 2026-08-05: a wrong password fails with `0x8007052E`, `tests/test_scheduler_runas.py:207-209`). | `tests/test_ui_flet_setup_errors.py:58-66` asserts the generic output for exactly that input |
| A4 | The failure does not reach the log usefully. Of `register_task`'s ~11 failure returns only the direct `TaskComError` arm logs the canonical text — **never the HRESULT** (`windows.py:405-409`); the elevated `ok=False` arm **logs nothing** (`:609-613`), nor does the cross-SID `DIFFERENT_ACCOUNT` arm inside it; the generic `except Exception` (`:410-412`) logs only the exception's class name at WARNING and **discards a `com_error` raised at apartment entry** (Task Scheduler service stopped — measured by the reliability lens); `_register_elevated`'s pre-consent `write_request` can raise `OSError` straight through to the view floor (`screens/setup.py:2157`) with zero log lines; `delete_task` logs nothing on any failure (`:444-447`, `:650-651`). | SD60: "nothing informative in the log" |
| A5 | Two classifier branches are **dead**: `"PowerShell not found"` / `"ScheduledTasks module not available"` were retired with the PowerShell transport at plan 0041 S1b (`windows.py:149-151`); nothing produces them. Their live successor `task_com.MSG_COM_UNAVAILABLE` (a frozen build missing pywin32 — **permanent**; produced at `windows.py:403-404`, `:445`, `:731`, `elevated_apply.py:129`) has **no** branch. The module docstring (`setup_errors.py:9-11`) still names the retired producers as its keying contract; so does the always-loaded `CLAUDE.md` row for `windows.py`. | — |
| A6 | `docs/partner/troubleshooting.md:58` reproduces the PIN / microsoft.com / batch-logon coaching in prose and attributes it to **"Access is denied" even after approving the prompt** — the retired PowerShell-era diagnosis (DECISIONS 2026-06-25). `:51` claims a wrong password "causes Windows to report an error in the wizard", which the policy case contradicts. | file read 2026-09-16 |
| A7 | **A marker collision is LIVE today, not hypothetical.** `_canonical_message`'s unmapped branch returns Windows' own description verbatim; Windows' text for `0x80070520` is *"A specified logon session does not exist. It may already have been terminated."* and for `0x80070003` *"The system cannot find the path specified."* Both carry an `_ABSENT_DELETE_MARKERS` marker (`src/ui_flet/schedule_status.py:41`), so on the delete path (`windows.py:447` → `interpret_unregister`, `:270`) a **failed removal reads as the success-shaped "No schedule was registered"**, after which `screens/setup.py:2196-2201` persists `schedule_registered = False`. Measured live by the reliability lens with a faked `com_error` through the real `delete_task` + `interpret_unregister`. `"access is denied"` in a description likewise fires the adapter's elevated retry (`src/scheduler/__init__.py:141`); `"DSYNC_"` in any message is collapsed by `_sanitize_child_message` (`windows.py:506`). Nothing enforces the inverse for a new canonical either. | measured 2026-09-16 |

Plan 0046's A7 (*the PIN / microsoft.com coaching is wrong against a service account and coaches a personal cloud credential into a service-account field*) rides here, because the credential branch is created here.

## Goals / Non-goals

**Goals**
- G1 — Each HRESULT in scope gets its own calm, plain-prose, secret-free copy with a real next step. In scope: a code already mapped and **mis**-mapped (`0x8004130F`); a code with a documented mechanism elsewhere and a live district hypothesis (`0x80070520`); the two already-mapped codes (`0x8007052E`, `0x80070005`, the latter with its elevated-child provenance made visible); plus `MSG_COM_UNAVAILABLE`.
- G2 — **Every failure return** of `register_task` / `_register_elevated` / `delete_task` / `delete_task_elevated` (except the idempotent absent-task delete) writes ONE `ERROR` line through ONE helper, carrying the message and `[HRESULT 0x%08X]` or `[HRESULT n/a]`, never a secret or an account name — pinned by an arm sweep, so a district's `etl_tool.log` has a single grep anchor.
- G3 — No string `_canonical_message` can **return** carries a marker another consumer keys on (`_ABSENT_DELETE_MARKERS`, `"access denied"`, `"DSYNC_"`) unless it is the code that owns it; and the producible message set is injective (A2's class cannot return). Both pinned; both recorded in INVARIANTS.
- G4 — The generic fallback stops promising that a retry helps, keeps a promise-free "try once more", keeps the support path and the details clause, and omits the clause when the message itself says there is no detail.
- G5 — The credential copy is principal-aware by construction: `account_is_current` is a **required** keyword, so 0046-B cannot forget it; B re-reads the `False` copy against the real field label when the field exists.
- G6 — The partner troubleshooting doc and the always-loaded `CLAUDE.md` row say what the app does, and a parity test pins the doc's quoted sentences and its log-anchor to the source.
- G7 — Byte-identical behaviour for every path that succeeds today; zero `src/etl/**` / `config/**` touch.

**Non-goals**
- N1 — Making SD60's unattended sync **work** under the policy. See the honesty constraint.
- N2 — Mapping codes unreachable from today's UI or only observable at run time: `0x80070534` / `0x80041310` (a bogus account name — needs 0046-B's field; Community-sourced for the general case), `0x80070569` (batch-logon, a **run-time** `LastTaskResult`; 0046-C's `run_result_verdict` is its home), `SCHED_S_BATCH_LOGON_PROBLEM` (`0x0004131C`, a **success** code pywin32 cannot surface), expired / disabled / locked-out codes. **`0x80070775` (locked out) is the first candidate to promote** once G2's log line produces it in the field. The log line is what turns the next unknown code into evidence.
- N3 — A classifier branch for `_MSG_ACCOUNT_NEEDS_PASSWORD`: unreachable until 0046-B (`screens/setup.py:2154` passes `run_as_user=None`); pinned as *deliberately unclassified* with that reason.
- N4 — A `(headline, detail)` classifier contract, or a typed `RegisterOutcome(ok, message, scode)` that would retire string keying altogether. Both correct, both touch the view / adapter / cron path; recorded in DECISIONS as considered-and-deferred (the growing invariant list is the cost of deferring them).
- N5 — Pre-flight detection of the policy via `HKLM\…\Lsa\DisableDomainCreds`: a fail-closed refusal on Strongly-implied data would block a district that would have succeeded. Considered-and-rejected, recorded in DECISIONS.
- N6 — Logging in `read_schedule`'s UNKNOWN arms: the probe fires on nav clicks and would churn the 5 MB × 3 rotating log, evicting the ERROR lines this plan adds.
- N7 — Retiring the register primary after a permanent failure, or persisting a failed registration's code so tomorrow's Setup readout can say *why* there is no schedule. Both real; ROADMAP.
- N8 — Any gMSA / SYSTEM / machine-scope-secret work (owner, 2026-09-16).

## Approach

**One principle: map by HRESULT in the engine, key by exact canonical string in the classifier, log the code beside the text, and make the properties the classifier depends on (injectivity, no foreign markers) true of what the engine can *return*, not just of what we wrote.**

1. **`task_com.py` — named canonicals and one hex formatter.** Every `_HRESULT_CANONICAL` value becomes a module-level `MSG_*` constant (the module already does this for `MSG_COM_UNAVAILABLE`; `setup_errors` imports `windows._MSG_*` *"so the copy can never drift from the producer"* — `setup_errors.py:19`). Add `HR_NO_SUCH_LOGON_SESSION = 0x80070520` with a **descriptive, not diagnostic**, string (the engine reports what Windows said; the policy inference lives in the classifier's hedged copy). Give `HR_ACCOUNT_INFO_NOT_SET` its own string; keep `HR_LOGON_FAILURE`'s text byte-identical. Add `format_hresult(scode) -> str` (masks `& 0xFFFFFFFF`; `"n/a"` for `None`) and `hresult_for(message) -> int | None` (the inverse over the injective table — how the classifier and the elevated parent recover the code from a message without a second spelling of the pairing). The unmapped branch appends `({format_hresult(scode)})` to Windows' description so the fallback's details clause carries the code.
2. **Marker guard at the producer boundary (`messages.py`, new).** A tiny import-free `src/scheduler/messages.py` owns `ABSENT_TASK_MARKERS` (today's `_ABSENT_DELETE_MARKERS`, which `schedule_status` now imports — its comment still attributes the list to the retired `schtasks`), `ACCESS_DENIED_MARKERS`, `SECRET_SENTINEL_PREFIX = "DSYNC_"` and `carries_foreign_marker(text, *, owned=())`. `_canonical_message` gates **both** uncontrolled escapes (the `excepinfo` description and `str(exc)` when `scode is None`): a candidate carrying a foreign marker is dropped for `f"The schedule operation failed ({format_hresult(scode)})."` — the code still answers *why*. `HR_NOT_FOUND` / `HR_ACCESS_DENIED` return from the table before the guard and keep the marker they own. A table sweep + a description sweep + an injectivity sweep pin G3.
3. **`windows.py` — one failure helper.** `_fail(task_name, message, *, scode=None, verb="register") -> tuple[bool, str]` logs `_FAIL_LOG_FORMAT = "Failed to %s task '%s': %s [HRESULT %s]"` at ERROR and returns `(False, message)`. Every failure return of `register_task` and `_register_elevated` routes through it (the pre-flight refusal, `ImportError`, `TaskComError` with its scode, the generic `except Exception` — now ERROR, with `task_com.com_error_scode(exc)` so an apartment-entry `com_error` keeps its code — the pre-consent failures, no-readable-result, the child `ok=False` arm **including the `DIFFERENT_ACCOUNT` leg** (logging the fixed `_MSG_DIFFERENT_ACCOUNT`, never `child_msg`), and a new `except OSError` around `write_request` → `_run_elevated_child` so a DPAPI / profile-dir / icacls failure no longer escapes to the view floor unlogged). The elevated arm derives its code with `hresult_for(msg)`. `delete_task` / `delete_task_elevated` use the same helper with `verb="remove"` unless the message is exactly `MSG_NOT_FOUND`. `_confirm_registration` gains `path_label` so a direct-path timeout stops logging "Elevated registration". Inline failure literals become named constants (`_MSG_UNEXPECTED_FAILURE` reusing `task_com.MSG_OPERATION_FAILED`, `_MSG_CHILD_DETAIL_UNAVAILABLE`, `_MSG_CHILD_NO_DETAIL`, `_MSG_REMOVAL_TIMED_OUT`); `windows` imports `elevated_apply.DIFFERENT_ACCOUNT_SENTINEL` instead of re-spelling it. **New canonical `_MSG_ELEVATED_ACCESS_DENIED`:** when the sanitized child message equals `MSG_ACCESS_DENIED`, `_register_elevated` returns this instead — because the classifier's `elevated` flag is the *parent's* token, a UAC-approved child refusal used to classify as "right-click and Run as administrator", the exact loop SD60 ran.
4. **`elevated_apply.py`.** The refusal literals become `_MSG_*` constants exported as `CHILD_REFUSALS`. The result-file schema is **unchanged** (`{ok, message}`) — the panel showed a `scode` field is redundant once every canonical is distinct and every unmapped message carries its code.
5. **`setup_errors.py` — classify what is produced.** Delete the two dead branches and their tests. Add five exact-equality branches (no-logon-session · account-info · credential · COM-unavailable · elevated-child-access-denied), placed with the other exact markers. `MSG_ACCESS_DENIED` by exact equality carries its code; the fuzzy `access_denied` substring branch stays as the total fallback **without** a code (a substring match must not assert a status it may not have). The elevated copy uses provenance wording (*"In our testing a wrong password reports a different message, so this is more likely something on this computer blocking the change"*) — there is no source for what causes an elevated `0x80070005`. The fallback moves into a named `_unclassified_copy(msg)` producer (the parity sweep's oracle) and omits `(Details: …)` for the no-detail literals. Signature: `classify_schedule_error(msg, elevated, *, account_is_current: bool)`; the three view call sites pass `True`. Rule, stated in the docstring: **every branch's first sentence names the cause, not the outcome**, because all five render under the same red "Couldn't schedule the nightly sync" headline. Vocabulary: *signed in / signed out*, matching the section's own caption. Code shown on every HRESULT-keyed branch (consistency; the credential branch's second-failure clause makes it an escalation path too).
6. **Produced-vs-classified sweep, by introspection.** The producible set is derived (`MSG_`/`_MSG_` string attributes of `task_com`, `windows`, `elevated_apply` + `_HRESULT_CANONICAL.values()`), so a new constant is red by construction; each is classified (`!= _unclassified_copy`) or named in `DELIBERATELY_UNCLASSIFIED` with a reason; a third documented bucket lists the messages that never reach the classifier (`_MSG_ELEVATION_REMOVE_UNCONFIRMED`, `_MSG_REMOVAL_TIMED_OUT`, `_MSG_NOT_WINDOWS`, with the `screens/setup.py` line as evidence); `_WORKER_ERROR_*` are named as bypassing the classifier.
7. **Docs in the same slices.** `docs/partner/troubleshooting.md`'s Task Scheduler section rewritten to match (wrong-password bullet, the "Access is denied after approving" bullet, bullets for account-info and Task-Scheduler-unreachable, a compact policy block carrying what the app cannot: the code, the setting's name and registry value, "DistrictSync cannot work around this", the options, the log anchor, and the *documented* wording about "Do not store password": *Microsoft documents tasks set up that way as having no access to network resources or encrypted files; DistrictSync never sets a task up that way and can't support one — nightly delivery may stop working*). A bidirectional parity test pins the doc's quoted sentences, the retired phrases' absence, the `[HRESULT ` anchor and the codes (derived, never hand-typed). `CLAUDE.md`'s `windows.py` row, the tree, DECISIONS (in the file's supersession shape), INVARIANTS (house shape), CHANGELOG, ROADMAP, one line in DESIGN_SYSTEM.

**Alternatives rejected (one line each):**
- *Carry `scode` in the elevated result file* — redundant after the split + the hex suffix; touches the one IPC surface the codebase guards hardest; `hresult_for(msg)` recovers the code with no schema change.
- *A defaulted `account_is_current=True`* — a permissive default on the parameter that selects personal-credential coaching; CLAUDE.md's non-negotiable, and the panel refused it twice.
- *Cut `account_is_current` entirely* — leaves 0046-B free to forget the switch with nothing red.
- *Keep the marker rule table-only* — the hazard is measured live in the description pass-through; an INVARIANTS entry a future reviewer would trust must be true of the function.
- *Narrow G2 to "the two HRESULT-bearing arms"* — leaves SD60's own path (self-elevating, pre-consent) unlogged; the helper costs ~6 lines + one-line edits.
- *Put the "Do not store password" warning in the banner* — doc content for the IT reader in Task Scheduler; it was the banner's longest clause and displaced the action sentence.
- *"This usually means …"* — a frequency word hedging a provenance gap with N=0 observations; say what Microsoft documents instead.
- *Duplicate a canonical literal in the classifier* — single-source; the house pattern imports the constant.
- *A `DowngradeInterrupt`-style dialog for the policy case* — no in-app fix to offer; a banner with real next steps is the right weight.
- *One slice* — ~23 files; the engine half lands complete and valuable alone.

## Architecture & holistic fit

- **Codebase fit.** Four layers, each extended along its seam: `messages.py` (import-free vocabulary the engine and the UI both consume — the ONE home for the marker lists, so `task_com` can guard what it returns without importing `ui_flet`) · `task_com` (engine — HRESULT ↔ locale-free canonical, both directions; owns hex formatting and the boundary guard) · `windows.py` / `elevated_apply.py` (transport — one failure-log helper; the result-file schema untouched) · `setup_errors` (COUNTED pure classifier — keys on imported constants, derives codes via `hresult_for`, owns the fallback producer). The view changes are three keyword tokens, two type-name-only floor log lines and one `selectable=True`. No COM type crosses `task_com`'s boundary; the classifier never sees an `scode`; the secret-handling contract (`repr=False`, DPAPI request, plaintext result, `_sanitize_child_message`) is unchanged. SOLID: single responsibility per layer preserved; open/closed — a new HRESULT is one table row + one branch + one sweep decision; the S4U ban survives untouched. The `_fail` funnel replaces a convention repeated at eleven sites with one function a test can sweep.
- **Product fit.** The admin reads a cause and a real next step (the Convert page as the bridge on every honest dead end; named controls, never "below"); support reads the code off a copyable card or the log's single anchor. Copy is verdict-first, plain prose, in the schedule section's own vocabulary (`docs/DESIGN_SYSTEM.md` §7 exempts it from the "account / credentials" ban), and never claims a fix the app cannot make.
- **Quality dimensions to uphold** (→ Spec "In-scope standards dimensions"): `security` · `observability-ops` · `product-ux` · `testing` · `reliability-resilience` · `api-and-contracts` · `maintainability-structure` · `docs-traceability`.
- **Future-proofing.** 0046-B passes `account_is_current` from the recorded principal and re-reads the `False` copy; 0046-C's `run_result_verdict` consumes `last_result` (dead today) and hosts `0x80070569` and the "registered logged-on-only and never run" honesty gap; every unmapped code is now observable in the log; every new canonical is a sweep decision; `RegisterOutcome` and `(headline, detail)` are recorded as the next structural step, not built.

## Affected files

**Slice A1 — engine**
- `src/scheduler/messages.py` — **new**: marker vocabulary + `carries_foreign_marker`.
- `src/scheduler/task_com.py` — `HR_NO_SUCH_LOGON_SESSION`; `MSG_*` constants (table built from them); `MSG_OPERATION_FAILED`; `format_hresult`; `hresult_for`; boundary guard in `_canonical_message`; unmapped branch carries the code; comment block gains the rule + the exact-equality-consumer mirror sentence (`windows.py:110-113`'s).
- `src/scheduler/windows.py` — `_fail` + `_FAIL_LOG_FORMAT`; every failure return routed; generic arm → ERROR with `com_error_scode`; `except OSError` around the pre-consent handshake; `_MSG_ELEVATED_ACCESS_DENIED`; inline literals → constants; sentinel imported; `_confirm_registration(path_label=)`; delete arms logged unless `MSG_NOT_FOUND`; module docstring `:33-36` and the `Returns:` block `:316-318` name the contract by constant.
- `src/scheduler/elevated_apply.py` — refusal literals → `_MSG_*` + `CHILD_REFUSALS`. Schema unchanged.
- `src/ui_flet/schedule_status.py` — `_ABSENT_DELETE_MARKERS` imported from `messages`; the comment names today's producers.
- `docs/claugentic-ARCHITECTURE_TREE.md` (new file entry; `task_com` / `windows` / `elevated_apply` / `schedule_status` lines; refresh the stale PowerShell-era `tests/test_scheduler_runas.py` line) · `docs/claugentic-INVARIANTS.md` · `CLAUDE.md` (the `windows.py` row's HRESULT/log half).
- Tests: `tests/test_task_com.py`, `tests/test_scheduler_runas.py`, `tests/test_scheduler_elevation.py`, `tests/test_elevated_apply.py`, `tests/test_schedulers.py` (re-point hand-typed canonicals at the constants, keeping ONE golden pin in `test_task_com.py`).

**Slice A2 — classifier + docs**
- `src/ui_flet/setup_errors.py` — five new branches; two dead branches removed; `MSG_ACCESS_DENIED` exact vs fuzzy; `_unclassified_copy`; `account_is_current` required; two "below" → "above" fixes; module + function docstrings rewritten (keying = `task_com.MSG_*` + `windows._MSG_*`; the mixed non-leak proof — substring branches by fixed copy, exact branches by unreachability; the classifier's real boundary: `register_task`'s returns + two remove-path markers; the first-sentence rule).
- `src/ui_flet/screens/setup.py` — `account_is_current=True` at the three call sites (`:2129`, `:2135`, `:2191`); `logger.error("… raised unexpectedly: %s", type(exc).__name__)` at the two worker floors (`:2157`, `:2220`).
- `src/ui_flet/components.py` — `selectable=True` on `ErrorCard`'s detail text (+ one line in `docs/DESIGN_SYSTEM.md`).
- `docs/partner/troubleshooting.md` · `CLAUDE.md` (the classifier half of the row) · `docs/claugentic-ARCHITECTURE_TREE.md` (`setup_errors` line) · `docs/claugentic-DECISIONS.md` · `docs/claugentic-ROADMAP.md` · `CHANGELOG.md`.
- Tests: `tests/test_ui_flet_setup_errors.py`; **new** `tests/test_partner_doc_schedule_copy_parity.py`.

Untouched, by design: `src/scheduler/elevation.py` (the result reader is unchanged), `src/scheduler/__init__.py`, everything under `src/etl/` and `config/`.

## Research / grounding

**Files reviewed (2026-09-16; five read-only mappers + two critics + six follow-ups, then eleven 2b reviewers who re-verified every `file:line` and measured two claims live):** `src/scheduler/task_com.py:66-121, 129-147, 182-204, 232-243, 264-308` · `src/scheduler/windows.py:33-36, 110-161, 316-318, 340-352, 368-379, 378-415, 418-450, 496-508, 524-536, 553-617, 650-651, 726-738` · `src/scheduler/elevated_apply.py:37, 52-68, 81-133` · `src/scheduler/elevation.py:82-83, 242-254` · `src/scheduler/__init__.py:110-128, 140-141` · `src/ui_flet/setup_errors.py` (whole) · `src/ui_flet/schedule_status.py:37-41, 193-218, 256-270` · `src/ui_flet/screens/setup.py:193, 213, 818, 1949-1985, 2022-2158, 2172-2220, 2335-2357, 2469, 2603` · `src/ui_flet/components.py:874, 897-902` · `src/utils/logger.py`, `config/logging.conf:24`, `src/ui_flet/launcher.py:32, 142` (a `src.scheduler.windows` ERROR propagates to root and reaches `etl_tool.log` once `boot_logging()` ran) · `tests/test_task_com.py:42-103` · `tests/test_ui_flet_setup_errors.py` (whole) · `tests/test_scheduler_runas.py:34-38, 207-226, 284-324, 438-445, 488-495` · `tests/test_scheduler_elevation.py:310-364, 471-515` · `tests/test_elevated_apply.py:156, 173-199` · `tests/test_schedulers.py:181-196, 293-307` · `tests/test_creator_doc_copy_parity.py` (the doc-parity house pattern) · `tests/test_ui_flet_home_wizard_host.py:328` (compares mount-time text only; a failure banner is out of its scope) · `docs/partner/troubleshooting.md:42-79` · `docs/claugentic-INVARIANTS.md` · `docs/DESIGN_SYSTEM.md:32-40` · `docs/claugentic-CHARTER.md` (entry 1: pure predicate modules → RED-FIRST edge table).

**Harness docs consulted:** `docs/claugentic-WORKFLOW.md` · `docs/claugentic-PLAN_TEMPLATE.md` · `docs/claugentic-standards/README.md` · `docs/claugentic-DECISIONS.md:6` (the supersession protocol), 2026-09-15 / 2026-06-25 / 2026-06-05 · CLAUDE.md gotchas: bandit `-c pyproject.toml` (B105 keys on the binding NAME — `MSG_LOGON_FAILURE` needs no `# nosec`; `windows.py:133` does); `check_no_emails.py`; no `make`; venv interpreter; LF endings; `elevation.write_request` is Windows-only and must stay mocked so the Linux CI leg matches.

**Microsoft sources (fetched 2026-09-16; confidence decides the wording):**

| Claim the copy or code rests on | Confidence | Source |
|---|---|---|
| `0x80070520` = `HRESULT_FROM_WIN32(1312)` = `ERROR_NO_SUCH_LOGON_SESSION`, "A specified logon session does not exist. It may already have been terminated." | **Documented** | system-error-codes 1300-1699; `HRESULT_FROM_WIN32` |
| Enabling *Network access: Do not allow storage of passwords and credentials for network authentication* makes Task Scheduler fail to create a "Run whether user is logged on or not" task with that text; Task Scheduler stores the run-as credential via Credential Manager, which the policy blocks; "Run only when user is logged on" is unaffected. | **Documented — on an ARCHIVED Microsoft blog** (2012, Server 2008 R2 / Win 7, the MMC UI; no numeric code printed) | learn.microsoft.com/…/archive/blogs/supportingwindows/task-scheduler-error-a-specified-logon-session-does-not-exist |
| `0x80070520` + the same policy + credential-storage refusal, on a current Microsoft article (SCOM agent deploy). Registry: `HKLM\SYSTEM\CurrentControlSet\Control\Lsa\DisableDomainCreds`. | **Documented** (for SCOM) | troubleshoot/system-center/scom/deploy-operations-manager-agents-error-80070520 |
| ⇒ the policy causes `0x80070520` at DistrictSync's COM registration. | **Strongly implied** (composite; the policy page itself never mentions Task Scheduler — fetched and checked; no page covers the COM path or Win10+) — copy says *"Microsoft documents this when …"*, never "usually" | — |
| The policy's effective default is **Disabled**; CIS Windows 11 control 2.3.10.4 recommends **Enabled (L1)** — it is a hardening setting, not a misconfiguration; presence in the Microsoft Security Baseline is unverifiable from a fetchable page. | Documented / Community / gap | policy page; third-party CIS mirror |
| "Do not store password" ⇔ S4U; S4U discards the credential after authenticating; "the service can only use the security context to access local resources… If your task requires access to network resources, you cannot use S4U." | **Documented** (archived Microsoft page) | cc722152(v=ws.11) |
| `TASK_LOGON_S4U`: "no password is stored by the system and there is no access to either the network or to encrypted files." | **Documented** (current API reference) | ne-taskschd-task_logon_type |
| Whether SFTP-over-SSH (no Windows authentication) or DPAPI / the keyring works under S4U | **UNDOCUMENTED either way** — handover §6 notes S4U's "no network" is about Windows-authenticated resources, not TCP — so the doc says delivery *may stop working* and that DistrictSync does not support the mode; the banner says nothing about it | four S4U pages; handover §6 |
| `0x8004130F` = "No account information could be found in the Task Scheduler security database for the task indicated." | **Documented** | task-scheduler-error-and-success-constants |
| `0x8004130F` differs from a wrong password: two Documented definitions (above; `0x8007052E` = "The user name or password is incorrect."), and MS-TSCH defines the former as a GET-path lookup miss with no password parameter. Absent from `RegisterTaskDefinition`'s documented return table; its cause is not documented. | **Documented** (the difference) / **Strongly implied** (the protocol reading) — copy says *"this isn't about the password you typed"* and names no cause | MS-TSCH 3.2.5.3.x; registertaskdefinition |
| `0x8007052E` = wrong user name or password | **Documented** + live-observed 2026-08-05 | 1300-1699; MS-TSCH App. B |
| The PIN / Microsoft-Account clauses in the credential copy | carried over unchanged from `setup_errors.py:110-113`; **not re-grounded** in this pass | — |
| Batch-logon: registration returns the success code `SCHED_S_BATCH_LOGON_PROBLEM`; Task Scheduler auto-grants the right, defeated by a Deny policy or a domain GPO managing the Allow list; the run-time `0x80070569` is tied to Task Scheduler only by an archived forum thread. | Documented / Documented / **Community** | registertaskdefinition; log-on-as-a-batch-job; archived forum |
| A bogus run-as name → `0x80070534` | Documented only for SYSTEM + NULL + NULL + `TASK_LOGON_SERVICE_ACCOUNT`; **Community** for the general case | registertaskdefinition; Q&A |
| An elevated `0x80070005` at registration is caused by … | **no source** — the copy says only what our testing shows (a wrong password reports a different message) | — |

**Findings.** Reuse: the `_MSG_*`-import pattern, `_FakeComError`/`_wrapped`, `TestPasswordLeakClosure`'s caplog sweep, `_capture_registration()`, `TestRegisterElevated._patch_win_nonelevated` + `_register()` (the existing harness for the `ok=False` arm — `tests/test_scheduler_elevation.py:471-485` is the test of that arm), `test_creator_doc_copy_parity.py`'s declared-quote table. Build: `messages.py`, named canonicals, `format_hresult` / `hresult_for`, the boundary guard, `_fail`, `_MSG_ELEVATED_ACCESS_DENIED`, five branches, `_unclassified_copy`, three sweeps, the doc-parity test. Gotchas: `"does not exist"` is the marker Windows' own text for this code fires (not `"no such"`); `TaskComError.scode` is `None` when `com_error_scode` cannot coerce `excepinfo[5]` (`task_com.py:279-282`) — that path, not the unreachable `:410`, is what `"n/a"` exists for; `bool` is an `int`; `tests/test_task_com.py:95-98` (exact equality on the description) is the test the hex suffix breaks — `:100-103` survives as its positive twin; `tests/test_ui_flet_setup_errors.py:37-43` and `:102-107` break on the elevated rewrite and the second is a SECURITY twin whose positive anchor must be **re-anchored**, never dropped; `:218-223` survives the new fallback unchanged.

## Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | A canonical or a Windows description carries `"no such"` / `"does not exist"` / `"cannot find"` / `"access denied"` / `"DSYNC_"` → a failed removal reads as success, a UAC prompt fires for noise, or a message collapses on the elevated path only. | The boundary guard on both escapes + three sweeps (table, description, injectivity) + the INVARIANTS entry over what the function *returns*. |
| R2 | The new log lines become a secret or PII channel. | `_fail` formats only `task_name`, a canonical, and an int; `TestPasswordLeakClosure` extended with a positive twin (code present) and TWO absence sentinels (the password AND a unique account marker) on the direct and elevated paths; the `DIFFERENT_ACCOUNT` leg logs the fixed canonical, never `child_msg`. |
| R3 | `scode=None` crashes the format. | `format_hresult` is total; tested on `None`, `0`, a negative int (the mask), and via a `com_error` whose `excepinfo[5]` is non-numeric. |
| R4 | Splitting `0x8004130F` from `0x8007052E` breaks a consumer. | Only pin is `tests/test_task_com.py:91`; the other five hand-typed copies of the logon-failure literal are re-pointed at the constant. |
| R5 | The policy copy over-claims. | Provenance wording, no frequency word; the hardening nature named; DECISIONS records the labels; `honesty-reviewer` at Verify. |
| R6 | The S4U wording claims delivery fails. | Banner says nothing about S4U; the doc says what Microsoft documents plus "may stop working"; handover §6 cited. |
| R7 | The rewritten fallback / removed branches break the existing shape tests. | Named per test in Test strategy with the reason; `:102-107` re-anchored on the new copy. |
| R8 | `account_is_current` is forgotten by 0046-B. | Required keyword: a missing argument is a `TypeError` and a mypy error. |
| R9 | The partner doc drifts from the app again. | Bidirectional parity test (quotes present · retired phrases absent · `[HRESULT ` anchor · codes derived). |
| R10 | The 1:1 message↔code pairing is restated and diverges. | `hresult_for` is the inverse of the one table; the classifier and the elevated parent both use it. |
| R11 | SD74 snapshot / ETL output. | Zero `src/etl/**` + `config/**` touch — assert at Verify. |
| R12 | `check_no_emails.py`. | No address anywhere (first names + SD numbers only); the hook runs it. |
| R13 | The Linux CI leg diverges from the local Windows green. | `elevation.write_request` stays mocked in every new elevated test; no Windows-only import at module level. |

## Test strategy

RED-FIRST per CHARTER entry (1): write the edge tables for `_canonical_message`, `classify_schedule_error` and `_fail`, watch them fail, implement, then **mutation-check** by restoring `HEAD`'s copy of each changed module and re-running.

**A1**
- `tests/test_task_com.py` — `:91` split into `(hr, expected)` rows with the reason in the docstring; a row for `HR_NO_SUCH_LOGON_SESSION`; `:95-98` rewritten deliberately (`== "Windows' own description (0x80041318)"`), `:100-103` kept as its twin; the **table sweep** (`interpret_unregister(False, msg).success_shaped is (hr == HR_NOT_FOUND)` for every row — the positive twin and the negative sweep in one line, imported from the real consumer) + the `"access is denied"` and `"DSYNC_"` rows; the **description sweep** (`_FakeComError` with `excepinfo[2]` = the measured Windows text for `0x80070520`, and `"The system cannot find the path specified."` for `0x80070003` → output carries the hex and no marker; a marker-free description still passes through); the `scode is None` escape with a marker-bearing `str(exc)`; the **injectivity sweep** over the derived producible set; `format_hresult` over `None` / `0` / `0x80070520` / `-2147024891`; `hresult_for` over every row and an unknown string; a grep-shaped assertion that `src/` contains exactly one `:08X`.
- `tests/test_scheduler_runas.py` — `TestPasswordLeakClosure` gains the positive twin (`0x80070005` in `caplog.text`) and the account sentinel; `TaskComError(None, …)` logs `n/a`; the generic arm logs ERROR with the code of an apartment-entry `com_error`; the **arm sweep**: every `(False, msg)` return of `register_task` emits exactly one ERROR record matching `_FAIL_LOG_FORMAT`, with `LOGS_NO_CODE` reasons for the arms that carry none; the pre-consent `OSError` arm.
- `tests/test_scheduler_elevation.py::TestRegisterElevated` — extend `:471-485`: the `ok=False` arm logs with `hresult_for`'s code; the `DIFFERENT_ACCOUNT` leg logs the canonical and not the sentinel; a child `MSG_ACCESS_DENIED` returns `_MSG_ELEVATED_ACCESS_DENIED`; the password and account sentinels absent from `caplog`.
- `tests/test_elevated_apply.py` — success shape unchanged (`:156` kept as the twin); `CHILD_REFUSALS` covers every `_write_result` failure literal (reflective).
- `tests/test_schedulers.py` — hand-typed canonicals re-pointed at the constants; the delete arm logs unless `MSG_NOT_FOUND`.

**A2**
- `tests/test_ui_flet_setup_errors.py` — dead-branch tests deleted (`:45-56`, `:109-122`; loops `:73-82`, `:231-239` re-seeded); `:37-43` rewritten to the new elevated copy (policy wording present; "PIN" / "microsoft.com" / "batch" absent; `0x80070005` present); `:102-107` **re-anchored** on a sentence of the new copy, all four negatives kept; `:58-66`, `:124-136`, `:212-216` rewritten for the new fallback (`:206`, `:218-223`, `:225` survive); a class per new branch — no-logon-session (names the setting verbatim · names the Convert page · names both controls "Windows account password" / "Schedule nightly sync" · carries the reboot consequence in the caption's words · `0x80070520` · no "try again" / `(Details:` / `**` / "below") · account-info (`"isn't about the password you typed"` · both control names · `0x8004130F`) · credential (`True` mentions PIN + microsoft.com; `False` mentions neither and asks for "the account you entered"; both carry the lock-out caution and `0x8007052E`) · COM-unavailable (Convert page + support, no code, no "try again") · elevated-child-access-denied (provenance wording, `0x80070005`); `MSG_ACCESS_DENIED` exact → code, fuzzy `"access denied while registering"` → no `0x`; `MSG_ACCESS_DENIED` with `elevated=False` still says Run as administrator (byte-identical); the no-detail literals get no `(Details:`; the **produced-vs-classified sweep** (derived set = reflected set guard; `!= _unclassified_copy`; `DELIBERATELY_UNCLASSIFIED` with `_MSG_ACCOUNT_NEEDS_PASSWORD`, `MSG_OPERATION_FAILED`, `MSG_NOT_FOUND`, `CHILD_REFUSALS`, the two child floors; the never-reaches bucket documented); the first-sentence rule (each new branch's first sentence contains its cause phrase).
- `tests/test_partner_doc_schedule_copy_parity.py` — **new**, shaped after `test_creator_doc_copy_parity.py`: every quoted headline in `docs/partner/troubleshooting.md` appears in the classifier's output for its canonical; the retired phrases are absent from the section; the `[HRESULT ` anchor is quoted and equals the prefix of `_FAIL_LOG_FORMAT`'s bracket; codes via `format_hresult(HR_*)`.
- Regression (both): full suite · SD74 snapshot · the 20-config validation (inline Makefile command) · tree-check · ruff / mypy / bandit (`-c pyproject.toml`) · `check_no_emails.py` · **CI's own `test pass` line quoted** before "land gate met".

## Decomposition (slices)

- [ ] **Slice A1 — engine: canonicals, the boundary guard, one failure log line.** `messages.py` · `task_com` · `windows` · `elevated_apply` · `schedule_status` import · tree / INVARIANTS / `CLAUDE.md` row (log half) · five test files. Lands complete because every registration and removal failure is logged with its code, the false-success delete is closed, and the split canonical is handled by the classifier's existing fallback (no worse than today for that one code until A2). One implementer session (≈12 files).
- [ ] **Slice A2 — classifier copy, docs, parity.** `setup_errors` · three view tokens + two floor log lines · `components.py` one word · troubleshooting.md · `CLAUDE.md` row (classifier half) · DECISIONS / ROADMAP / CHANGELOG / DESIGN_SYSTEM · two test files. Lands complete because it consumes only A1's constants. One implementer session (≈11 files).

Order: A1 → A2. Two PRs, two land gates.

---

## Review  _(filled by synthesizer-gate in its plan-gate altitude, Stage 3)_
- **Verdict:** _pending_
- **Required changes:** …
- **Sizing/completeness:** …
- **Harness impact:** …

---

## Spec  _(Stage 4)_

### In plain English (shown first at the approval gate — covers both slices)
- *What this builds.* When scheduling the nightly sync fails, the app names what Windows reported and gives a real next step for the four failures we can now identify: Windows would not save the task password (Microsoft documents this under a security-hardening policy — the one we believe SD60 hit); the task's own saved account details are missing (which is *not* about the password typed); a plain wrong password; and this copy of DistrictSync cannot reach Task Scheduler at all. Anything else still shows a calm message plus the raw detail for support, and no longer promises that waiting a moment will help. Every failure — scheduling or removing, on the normal and the administrator-prompt paths — writes one log line with the Windows code or "n/a", so a district's log answers "why" without a round trip. A live bug is closed on the way: today a failed *removal* can read as "nothing to remove" because Windows' own error text contains the words the app treats as "task not found". The partner troubleshooting page is corrected to say the same things, and the error card becomes copyable so the code can be pasted into an email to IT.
- *What "done" means for you.* Each of the four causes shows its own message (screenshots at Verify); one log line per failure with the code; the old dead branches and the stale doc paragraph gone; a parity test pins the doc to the app; all gates green in two PRs; CI's own result quoted for each; SD74 output byte-identical.
- *What you're accepting.* (1) This makes SD60's failure **legible, not fixed** — if the policy is the cause, their IT team must lift it for that computer, or the sync runs only while someone is signed in, or by hand from the Convert page. (2) The policy message says "Microsoft documents this when …" because the link is documented for Task Scheduler's own UI (2012) and for another product, not for our exact code path. (3) Six existing tests that pinned the old behaviour are rewritten on purpose and four that only tested the two deleted branches are removed; every new test is mutation-checked. (4) Codes nobody has observed yet (mistyped account name, batch-logon at run time, locked-out account) are **not** mapped — the log line is how they become evidence. (5) Three small view touches outside the pure modules: a required keyword at three call sites, two type-name-only log lines at the worker floors, and error-card text becoming selectable.

### Slice A1 — engine

**`src/scheduler/messages.py` (new, import-free)**
```python
"""Message vocabulary shared by the scheduler engine and the UI classifier (plan 0047).

Import-free on purpose: ``task_com`` must guard what it RETURNS against markers the UI keys on,
and it may not import ``ui_flet`` to learn them. ONE home for the lists, two consumers.
"""
ABSENT_TASK_MARKERS: tuple[str, ...] = ("cannot find", "does not exist", "no such", "no crontab")  # interpret_unregister → success-shaped
ACCESS_DENIED_MARKERS: tuple[str, ...] = ("access is denied", "access denied")                       # adapter elevated-retry + classifier
SECRET_SENTINEL_PREFIX = "DSYNC_"                                                                    # _sanitize_child_message collapses it

def carries_foreign_marker(text: str, *, owned: tuple[str, ...] = ()) -> bool:
    """True when ``text`` (case-insensitively) carries a marker some OTHER consumer keys on."""
```

**`src/scheduler/task_com.py`**
```python
HR_NOT_FOUND = 0x80070002
HR_ACCESS_DENIED = 0x80070005
HR_NO_SUCH_LOGON_SESSION = 0x80070520  # ERROR_NO_SUCH_LOGON_SESSION — a credential-storing registration had no logon session to store into
HR_LOGON_FAILURE = 0x8007052E
HR_ACCOUNT_INFO_NOT_SET = 0x8004130F

# Canonical, secret-free, locale-independent text per HRESULT. setup_errors.classify_schedule_error keys on
# these by EXACT equality and IMPORTS them — any edit here must be mirrored there. RULE (pinned; INVARIANTS):
# no string this module can RETURN — table value, Windows' own description, or str(exc) — may carry a marker
# another consumer owns (messages.ABSENT_TASK_MARKERS / ACCESS_DENIED_MARKERS / SECRET_SENTINEL_PREFIX)
# unless it IS that code; and the producible set is injective. Windows' own text for 0x80070520 reads
# "…does not exist…" — which is why its canonical is descriptive, not Windows' wording.
MSG_ACCESS_DENIED = "Access is denied."
MSG_NOT_FOUND = "The system cannot find the file specified."
MSG_LOGON_FAILURE = "The user name or password is incorrect."
MSG_ACCOUNT_INFO_NOT_SET = "Windows has no saved account information for the task."
MSG_NO_LOGON_SESSION = "Windows could not store the task credential (no logon session available)."
MSG_OPERATION_FAILED = "The schedule operation failed."
_HRESULT_CANONICAL: dict[int, str] = {HR_ACCESS_DENIED: MSG_ACCESS_DENIED, HR_NO_SUCH_LOGON_SESSION: MSG_NO_LOGON_SESSION,
                                      HR_LOGON_FAILURE: MSG_LOGON_FAILURE, HR_ACCOUNT_INFO_NOT_SET: MSG_ACCOUNT_INFO_NOT_SET,
                                      HR_NOT_FOUND: MSG_NOT_FOUND}
_MESSAGE_TO_HRESULT: dict[str, int] = {v: k for k, v in _HRESULT_CANONICAL.items()}  # well-defined only while injective (pinned)
_OWNED_MARKERS: dict[int, tuple[str, ...]] = {HR_NOT_FOUND: ("cannot find",), HR_ACCESS_DENIED: ("access is denied", "access denied")}

def format_hresult(scode: int | None) -> str:
    """``0x%08X`` (masked — the result channel may hand back a signed int) or ``"n/a"``; total."""

def hresult_for(message: str) -> int | None:
    """The HRESULT a canonical message stands for; ``None`` for anything else (unmapped text carries its own code)."""

def _canonical_message(scode, exc):
    if scode is None:
        text = str(exc).strip()
        return text if text and not carries_foreign_marker(text) else MSG_OPERATION_FAILED
    if scode in _HRESULT_CANONICAL:
        return _HRESULT_CANONICAL[scode]
    description = …excepinfo[2]…
    if description and not carries_foreign_marker(description):
        return f"{description} ({format_hresult(scode)})"
    return f"The schedule operation failed ({format_hresult(scode)})."
```

**`src/scheduler/windows.py`**
```python
from src.scheduler.elevated_apply import DIFFERENT_ACCOUNT_SENTINEL as _DIFFERENT_ACCOUNT_SENTINEL
_FAIL_LOG_FORMAT = "Failed to %s task '%s': %s [HRESULT %s]"   # the ONE grep anchor; the partner doc quotes "[HRESULT "
_MSG_ELEVATED_ACCESS_DENIED = "Windows refused the elevated schedule change."   # child MSG_ACCESS_DENIED, re-labelled at the parent
_MSG_CHILD_DETAIL_UNAVAILABLE / _MSG_CHILD_NO_DETAIL / _MSG_REMOVAL_TIMED_OUT   # today's inline literals, named

def _fail(task_name: str, message: str, *, scode: int | None = None, verb: str = "register") -> tuple[bool, str]:
    logger.error(_FAIL_LOG_FORMAT, verb, task_name, message, task_com.format_hresult(scode))
    return False, message
# register_task: every `return False, X` → `return _fail(task_name, X[, scode=…])`; the generic arm:
#   except Exception as exc: return _fail(task_name, task_com.MSG_OPERATION_FAILED, scode=task_com.com_error_scode(exc))
# _register_elevated: `try: … write_request / _run_elevated_child … except OSError as exc: return _fail(task_name, task_com.MSG_OPERATION_FAILED)`;
#   the ok=False arm: sentinel leg → _fail(task_name, _MSG_DIFFERENT_ACCOUNT); else msg = _sanitize_child_message(child_msg);
#   if msg == task_com.MSG_ACCESS_DENIED: msg = _MSG_ELEVATED_ACCESS_DENIED; return _fail(task_name, msg, scode=task_com.hresult_for(child_msg_sanitized))
# _confirm_registration(task_name, *, on_unconfirmed, path_label) — "Registration" / "Elevated registration"
# delete_task / delete_task_elevated: TaskComError / ok=False arms → _fail(..., verb="remove") unless message == task_com.MSG_NOT_FOUND
```

**`src/scheduler/elevated_apply.py`** — `_MSG_REQUEST_INVALID` / `_MSG_REQUEST_MISSING` / `_MSG_REQUEST_UNREADABLE` / `_MSG_CHILD_FLOOR`; `CHILD_REFUSALS: tuple[str, ...]`. `_write_result` unchanged.

**`src/ui_flet/schedule_status.py`** — `from src.scheduler.messages import ABSENT_TASK_MARKERS as _ABSENT_DELETE_MARKERS`; the comment names today's producers (`task_com._HRESULT_CANONICAL` + the guarded description pass-through on Windows; `linux.py`'s crontab wording on Unix) and points at the INVARIANTS entry.

**`docs/claugentic-INVARIANTS.md`** (house shape: rule · why · provenance) — *No string `task_com._canonical_message` can RETURN may carry a marker another consumer keys on (`messages.ABSENT_TASK_MARKERS`, `ACCESS_DENIED_MARKERS`, `SECRET_SENTINEL_PREFIX`) unless it is the code that owns it, and the producible schedule-message set is INJECTIVE.* Why: `interpret_unregister` turns a marker into the success-shaped "No schedule was registered" and Setup then persists `schedule_registered = False` over a task that is still live; `"access is denied"` fires the adapter's elevated retry; `"DSYNC_"` collapses the message on the elevated path only; exact-equality classification is only sound on an injective set (defect A2). Provenance: Windows' own text for `0x80070520` is literally "…does not exist…" and was reaching `interpret_unregister` unscrubbed — measured 2026-09-16, plan 0047.

**`CLAUDE.md`** — the `src/scheduler/windows.py` row: drop the two retired canonical literals; say "HRESULT-keyed canonicals (`task_com.MSG_*`, injective, marker-guarded — INVARIANTS) · every failure logs `[HRESULT 0x… | n/a]` through `_fail`"; qualify the credential contract as `0x8007052E`-only.

**Acceptance criteria (A1):**
1. `_canonical_message(HR_NO_SUCH_LOGON_SESSION, …) == MSG_NO_LOGON_SESSION`; `_canonical_message(HR_ACCOUNT_INFO_NOT_SET, …) != _canonical_message(HR_LOGON_FAILURE, …)`; `MSG_LOGON_FAILURE` byte-identical to today's text.
2. For every `(hr, msg)` in the table: `interpret_unregister(False, msg).success_shaped is (hr == HR_NOT_FOUND)`; no table value contains an `ACCESS_DENIED_MARKERS` entry except `MSG_ACCESS_DENIED`; none contains `"DSYNC_"`; the derived producible set (`task_com` + `windows` + `elevated_apply` `MSG_`/`_MSG_` strings + table values) has no duplicates.
3. A faked `com_error` whose description is *"A specified logon session does not exist. It may already have been terminated."* under an unmapped code yields a message containing the hex and no marker; a marker-free description passes through with the hex appended; `str(exc)` under `scode=None` is guarded the same way.
4. `format_hresult(None) == "n/a"`, `format_hresult(-2147024891) == "0x80070005"`; `hresult_for(MSG_X) == HR_X` for every row and `None` otherwise; `src/` contains exactly one `:08X`.
5. Every `(False, msg)` return of `register_task` and `_register_elevated` emits exactly one ERROR record matching `_FAIL_LOG_FORMAT`; the direct `TaskComError(HR_ACCESS_DENIED, …)` line contains `0x80070005`; `TaskComError(None, …)` → `n/a`; an apartment-entry `com_error` is logged at ERROR with its code; the elevated `ok=False` line carries `hresult_for`'s code; the `DIFFERENT_ACCOUNT` leg logs `_MSG_DIFFERENT_ACCOUNT` and never the sentinel; a pre-consent `OSError` is logged and returned as `MSG_OPERATION_FAILED`; a child `MSG_ACCESS_DENIED` returns `_MSG_ELEVATED_ACCESS_DENIED`; no log line contains the password or the account sentinel.
6. `delete_task` logs a `TaskComError` unless its message is `MSG_NOT_FOUND`; the success result file is byte-identical to today; `CHILD_REFUSALS` equals the set of failure literals `elevated_apply` writes.
7. Tree, INVARIANTS, `CLAUDE.md` row updated; `check_no_emails.py` green; full suite ≥ 80 %; SD74 byte-identical; 20 configs validate; ruff / mypy / bandit green; **CI's own `test pass` line quoted**.

### Slice A2 — classifier + docs

**`src/ui_flet/setup_errors.py`**
```python
from src.scheduler.task_com import (MSG_ACCESS_DENIED, MSG_ACCOUNT_INFO_NOT_SET, MSG_COM_UNAVAILABLE, MSG_LOGON_FAILURE,
                                    MSG_NO_LOGON_SESSION, format_hresult, hresult_for)
from src.scheduler.windows import (_MSG_DIFFERENT_ACCOUNT, _MSG_ELEVATED_ACCESS_DENIED, _MSG_ELEVATION_LAUNCH_FAILED,
                                   _MSG_ELEVATION_NO_RESULT, _MSG_ELEVATION_TIMEOUT, _MSG_UAC_DECLINED, _MSG_CHILD_DETAIL_UNAVAILABLE, _MSG_CHILD_NO_DETAIL)
from src.scheduler.messages import ACCESS_DENIED_MARKERS

def _code(msg: str) -> str: return f" (Windows code {format_hresult(hresult_for(msg))})"

def classify_schedule_error(msg: str, elevated: bool, *, account_is_current: bool) -> str:
    # 1. the five elevation markers — unchanged (the two shipped "below" → "above")
    # 2. exact-equality, HRESULT-keyed — first sentence names the CAUSE
    if msg == MSG_NO_LOGON_SESSION:
        return (
            "Windows would not save the password for the nightly task, so it can't be scheduled to run while no one is signed in. "
            "Microsoft documents this when the security setting 'Network access: Do not allow storage of passwords and credentials "
            "for network authentication' is switched on — a hardening setting your IT team controls; DistrictSync can't change it."
            "\n\n"
            "Send your IT team that setting's name and the code shown here; until it's resolved you can run the sync by hand from the "
            "Convert page. If someone stays signed in overnight, you can instead clear the Windows account password field above and "
            "choose Schedule nightly sync again — it will not run after a reboot with no one signed in." + _code(msg)
        )
    if msg == MSG_ACCOUNT_INFO_NOT_SET:
        return ("Windows is missing the nightly task's own saved account details — this isn't about the password you typed. If a nightly "
                "schedule is listed above, choose Remove nightly sync, then Schedule nightly sync again; if it keeps failing, the Help page "
                "has our support contact." + _code(msg))
    if msg == MSG_LOGON_FAILURE:
        lead = ("Windows rejected the user name or password. Enter your Windows account password — the one you use to sign in to this "
                "computer, not a Windows Hello PIN; for a Microsoft Account it's your microsoft.com password."
                if account_is_current else
                "Windows rejected the user name or password for the account you entered. Check the account name and re-enter that account's password.")
        return lead + (" If it's rejected again, stop rather than retry (repeated attempts can lock the account) and check with your IT "
                       "team, quoting the code shown here." + _code(msg))
    if msg == MSG_COM_UNAVAILABLE:
        return ("This copy of DistrictSync can't reach Windows Task Scheduler, so the nightly sync can't be scheduled from here — you can "
                "still run conversions from the Convert page. The Help page has our support contact.")
    if msg == _MSG_ELEVATED_ACCESS_DENIED or (msg == MSG_ACCESS_DENIED and elevated):
        opener = ("even after the permission prompt was approved" if msg == _MSG_ELEVATED_ACCESS_DENIED else "even though you're running as administrator")
        return (f"Windows refused the schedule change {opener}. In our testing a wrong password reports a different message, so this is more "
                "likely something on this computer blocking the change than a password problem. Check with your IT team, quoting the code "
                "shown here and the ERROR line in DistrictSync's log file." + f" (Windows code {format_hresult(HR_ACCESS_DENIED)})")
    # 3. substring, defensive, NO code (a fuzzy match must not assert a status): today's two access-denied copies, byte-identical
    # 4. fallback
    return _unclassified_copy(msg)

_NO_DETAIL = frozenset({_MSG_CHILD_DETAIL_UNAVAILABLE, _MSG_CHILD_NO_DETAIL})
def _unclassified_copy(msg: str) -> str:
    lead = "The schedule change didn't go through. You can try once more; if it fails again, the Help page has our support contact — include the detail shown here."
    return lead if msg in _NO_DETAIL else f"{lead} (Details: {msg})"
```
Module docstring: keyed on `task_com.MSG_*` + `windows._MSG_*` exact constants (single-sourced, imported); the non-leak proof is MIXED — substring branches return fixed copy independent of `msg`, exact-equality branches are unreachable unless `msg` IS the constant; the classifier receives `register_task`'s returns plus two remove-path pre-consent markers; plain prose, `\n\n` allowed, no markdown; first sentence names the cause. Function docstring: `elevated` = the PARENT process's token (a child refusal arrives as `_MSG_ELEVATED_ACCESS_DENIED` so provenance, not the parent's bit, decides); `account_is_current` = whether the task's principal is the signed-in account (today always `True`; 0046-B passes the recorded principal and re-reads the `False` copy against the real field label).

**`src/ui_flet/screens/setup.py`** — `classify_schedule_error(msg, _elevated_now(), account_is_current=True)` at `:2129`, `:2135`, `:2191`; at the two worker floors (`:2157`, `:2220`) add `logger.error("Scheduling the nightly sync raised unexpectedly: %s", type(exc).__name__)` (type name only — a validator's message could echo input).

**`src/ui_flet/components.py`** — `ErrorCard` detail: `ft.Text(detail, size=14, color=…, selectable=True)`; `docs/DESIGN_SYSTEM.md` gains one line under the `ErrorCard` row: *detail text is selectable — an admin may have to relay a code off a locked-down server.* (Load the `districtsync-design` skill.)

**`docs/partner/troubleshooting.md`** — "Task does not run after a reboot" bullet 2 → *A wrong password is reported in the wizard as "Windows rejected the user name or password" — re-run the schedule step with the correct password.* UAC block, last bullet → *"Windows refused the schedule change even after the permission prompt was approved" — in our testing a wrong password reports a different message, so this is more likely a setting on the computer; ask your IT team, quoting the Windows code the message shows and the `etl_tool.log` line containing `[HRESULT ` .* New bullets: *"Windows is missing the nightly task's own saved account details"* (remove + re-schedule; support) and *"This copy of DistrictSync can't reach Windows Task Scheduler"* (Convert page; support). **New policy block** (four bullets, what the app cannot say): the code `0x80070520` · the setting's name and registry value (`HKLM\SYSTEM\CurrentControlSet\Control\Lsa\DisableDomainCreds`) · *DistrictSync cannot work around this policy* — the three options (IT lifts it for this computer · logged-on-only, which will not run after a reboot with no one signed in · run by hand from Convert) · *don't tick "Do not store password" in Task Scheduler as a workaround: Microsoft documents tasks set up that way as having no access to network resources or encrypted files; DistrictSync never sets a task up that way and can't support one — nightly delivery may stop working.* "Task shows a non-zero Last Run Result" — unchanged.

**`docs/claugentic-DECISIONS.md`** — one entry, **2026-09-16**, in the file's supersession shape: *supersedes the 2026-06-25 Plan 0009 Slice 2 classifier contract* (restate: elevated + access-denied → batch-logon / wrong-password hint; the two PowerShell canonicals → their own lines; else the clean `msg`); evidence that met the condition: 0041 S1b retired both producers (`windows.py:149-151`); a wrong password was live-observed to fail with `0x8007052E`, not access-denied (2026-08-05); what the evidence does NOT prove: that the policy is SD60's cause (Strongly-implied composite), or `0x8004130F`'s cause. Then: the split; the marker rule at the function boundary and the measured false-success it closes; `account_is_current` required; considered-and-rejected: pre-flight registry detection; considered-and-deferred: `(headline, detail)` / `RegisterOutcome`; `0x80070775` the first promotion candidate. Compact — under ~250 words; the plan carries the rest.

**`docs/claugentic-ROADMAP.md`** — lines for: a failed registration leaves no durable trace (the readout can say "no schedule" but never why — 0046-B/C host); a permanent schedule failure should retire the register primary; "registered logged-on-only AND never run" must not read HEALTHY (0046-C); a typed `RegisterOutcome` would retire the substring invariants; tree hygiene sweep over unindexed `tests/` files and the PowerShell-era `:243`/`:245` entries.

**`CHANGELOG.md`** — `[Unreleased]` → `### Fixed`: *Scheduling the nightly sync: four failures now name what Windows reported — the status Windows returns when it will not store the task password (Microsoft documents this under a security policy) · missing saved account information · a wrong password · Task Scheduler unreachable — instead of "try again in a moment"; every scheduling or removal failure now logs the Windows code; and a failed removal can no longer read as "nothing to remove" because Windows' own error text happened to contain "does not exist". This does not change what Windows allows: a district whose policy blocks stored passwords still needs the policy lifted for that computer, a logged-on-only schedule, or a manual run.*

**In-scope standards dimensions (both slices; target bar = the module):** `security` · `observability-ops` · `product-ux` · `testing` · `reliability-resilience` · `api-and-contracts` · `maintainability-structure` · `docs-traceability`.

**Acceptance criteria (A2):**
1. `classify_schedule_error(MSG_NO_LOGON_SESSION, e, account_is_current=True)` for both `e`: names the setting verbatim, names the Convert page, names "Windows account password" and "Schedule nightly sync", carries "will not run after a reboot with no one signed in", contains `0x80070520`, contains none of "try again" (any case) / "usually" / `(Details:` / `**` / "below".
2. The account-info copy contains "isn't about the password you typed", both control names, `0x8004130F`.
3. The credential copy with `True` mentions "PIN" and "microsoft.com"; with `False` mentions neither and says "the account you entered"; both carry the lock-out caution and `0x8007052E`; calling without the keyword is a `TypeError`.
4. The COM-unavailable copy names the Convert page and carries no `0x`; `_MSG_ELEVATED_ACCESS_DENIED` and `MSG_ACCESS_DENIED`+`elevated=True` both carry the provenance wording and `0x80070005`; `MSG_ACCESS_DENIED`+`elevated=False` is byte-identical to today; `"access denied while registering"` classifies without any `0x`.
5. The fallback: no "in a moment"; ends with `(Details: {msg})` except for the two no-detail literals; `_MSG_ACCOUNT_NEEDS_PASSWORD` reaches it (pinned deliberate).
6. The dead branches and their tests are gone; the derived produced-vs-classified sweep passes with the declared `DELIBERATELY_UNCLASSIFIED` set and equals the reflected set; every new branch's first sentence contains its cause phrase.
7. `docs/partner/troubleshooting.md` carries the four new/rewritten bullets and the policy block and no "PIN" / "microsoft.com" / "Log on as a batch job"; the parity test pins them and the `[HRESULT ` anchor; `CLAUDE.md`, tree, DECISIONS (supersession shape), ROADMAP, CHANGELOG, DESIGN_SYSTEM updated; `ErrorCard` detail selectable.
8. Full suite ≥ 80 %; SD74 byte-identical; 20 configs validate; ruff / mypy / bandit green; `check_no_emails.py` green; **CI's own `test pass` line quoted**.
