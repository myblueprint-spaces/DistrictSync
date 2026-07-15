# 0034 — Trust & correctness batch (post-v3.6.0)

- **Status:** Queued — owner approved the batch 2026-07-15 **and pre-approved AUTONOMOUS
  execution** ("can it do it in parallel autonomously so that i can … step away"). A fresh
  session executes end-to-end per the §Autonomous execution addendum below — no owner
  questions mid-run; it STOPS at a verified, PR-ready state (merge + release stay with the owner).

## Autonomous execution addendum (owner pre-approvals — do not re-ask)
- **Endpoint:** one branch `fix/0034-trust-correctness` off latest main → one PR with full gate
  evidence + a manual-verify checklist. Do NOT merge, do NOT tag/release, do NOT push to main.
- **Parallelization map (respect the coupling):** Slices 2 (deliver-from-disk) and 4 (false-green)
  touch disjoint files → run in PARALLEL via isolated worktrees. Slices 1 (Mapping reconcile) and
  3 (Settings-Save honesty) both orbit the schedule/register machinery (`setup_flow.TaskArgs`,
  `screens/setup.py` handle, `scheduler/windows.py`) → run SEQUENTIALLY (1 then 3), after or
  alongside the parallel pair. Integration lands slices onto the batch branch one at a time with
  the FULL suite re-run after each landing; per-slice adversarial verify per the workflow.
- **Shared-doc discipline:** parallel slice agents change CODE + TESTS only; the orchestrator
  applies all shared-doc edits (DECISIONS, CHANGELOG `[Unreleased]`, ARCHITECTURE_TREE) at
  integration — avoids doc merge conflicts.
- **Pre-answered product decisions:**
  - Slice 1: on district Apply with a LIVE schedule → confirm dialog offers "Update the nightly
    schedule too" (default) / Cancel; if re-registration fails or is declined, show the honest
    banner "Your nightly schedule still uses <old district> — open Settings and Save to update it."
  - Slice 2: deliver-from-disk uploads the committed CSVs as-is; confirm dialog shows labelled
    Server / Folder facts (host named once); a manual delivery writes ONE run-store record
    (source="manual", sftp axes; never double-counts the build).
  - Slice 3: when a reconcile would downgrade an unattended task, interrupt with the explicit
    choice: "Keep running when signed out — re-enter the Windows password" vs "Continue — the sync
    will only run while signed in" (calm copy, no default that downgrades silently).
  - Slice 4: missed-run rule = schedule read-back LIVE **and** no run record in the last **26 h**
    → Home WARNING "We expected a nightly sync that didn't arrive" + route to Run History/Setup.
  - Copy stays within the districtsync-design skill's vocabulary; UAC/live-schedule behaviour that
    can't be exercised headlessly goes on the PR's manual-verify checklist instead of being claimed.
- **Escalation rule:** if genuinely blocked (a pre-approval doesn't cover it, a gate can't go
  green), do NOT guess and do NOT wait — record the open question at the top of this plan file,
  finish every unblocked slice, and say so plainly in the PR body.
- **Source of detail:** `.claude/plans/0032-ui-ux-sweep-proposal.md` (Tier 1 #3, Tier 2 #1/#2/#3/#7 —
  each carries file:line grounding from the 5-lens sweep). This file is the QUEUE + acceptance bar;
  re-verify the cited line numbers against current main (v3.6.0 shipped 0033 after the sweep).
- **Branch/release:** one branch (`fix/0034-trust-correctness`), one PR, ships as v3.6.1 or v3.7.0.
- **Standing constraints:** the `districtsync-design` skill governs any UI surface touched; run-record
  privacy split (bounded categories in the store, rich detail in the log) is inviolable; every fix
  fail-loud, never silent.

## Slice 1 — Mapping "Apply" must reconcile the scheduled task (0032 Tier 2 #1; the worst live bug)
`screens/mapping.py _on_apply` writes `AppConfig.sis_type` but never runs the Settings task-args
reconcile — a LIVE nightly task keeps converting the OLD district while the banner says "your
schedule is unchanged". Fix: route through the same `task_args_changed` + re-register flow Settings
Save uses (`setup_flow.TaskArgs`; re-register only when a task is LIVE and args changed); confirm
dialog names the schedule update; honest copy when re-registration is declined/fails ("your nightly
schedule still uses <old district> — open Settings and Save to update it").
**Accept:** switching district with a LIVE schedule either re-registers (verified read-back) or says
plainly that the schedule still uses the old district. Pure-logic tests for the decision; view glue smoke.

## Slice 2 — Deliver from disk (0032 Tier 2 #3) — also dissolves the anomaly auto-ack bypass (Tier 1 #3)
Convert's "Deliver to SpacesEDU" currently RE-RUNS the whole conversion with `anomaly_ack=True`
hardcoded (`screens/convert.py _confirm_and_deliver`) — slow, ships different data if the input
folder changed after the reviewed build, and structurally bypasses the >20%-drop write-gate.
Fix: deliver uploads the ALREADY-COMMITTED output CSVs (`SFTPUploader.upload_csvs(output_dir)` — they
are on disk from the atomic `save_all`); no re-transform → the anomaly-gate question disappears; the
confirm dialog gets labelled Server/Folder facts. Record the manual delivery in the run store
(source="manual", sftp axes) without double-counting the build.
**Accept:** deliver never re-transforms; a between-build-and-deliver input change cannot alter what
ships; BUILT_NOT_DELIVERED retry works from disk; run store shows an honest record.

## Slice 3 — Settings Save trustworthiness (0032 Tier 2 #2)
(a) A reconcile re-register must never SILENTLY downgrade an unattended task to logged-on-only:
detect (persist a `schedule_unattended` flag or read LogonType back in `read_schedule`) and interrupt
with an explicit choice — re-enter the Windows password vs continue logged-on-only. (b) A run-time
edit persists to `cfg.schedule_time` on Save even when no task is registered (it is config, not only
a register side-effect). (c) Button labels scope-accurate ("Save folders & district").
**Accept:** no path exists where Save changes the task's logon type without the admin choosing it;
an edited run time survives Save+restart with no schedule registered.

## Slice 4 — Kill the false green (0032 Tier 2 #7)
(a) Every scheduled/CLI invocation writes a run record even on early `sys.exit(1)` paths
(`pipeline.py` pre-transform exits — emit a "failed"/bounded-category record first). (b) Home
treats "a nightly run was expected (LIVE schedule, next-run time passed) but no record since" as
WARNING with plain copy. (c) Run History gains a Source column (Nightly/Manual — store field exists)
+ a muted district note when a record's `sis_type` differs from the active district.
**Accept:** killing the task or breaking the input dir shows on Home within one expected-run window;
Run History distinguishes nightly from manual; no PII widening in the store.

## Definition of done (batch)
All gates green per slice (full suite · ruff · mypy · bandit · validate-config · SD74 snapshot ·
tree-check · render smoke · AA contrast); DECISIONS entries for judgment calls; CHANGELOG
`[Unreleased]` entries per slice (no release-PR in flight this time); adversarial verify per the
workflow's Verify stage; honest copy reviewed on every user-facing string touched.
