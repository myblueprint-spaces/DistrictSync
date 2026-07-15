# 0034 — Trust & correctness batch (post-v3.6.0)

- **Status:** Queued — owner approved the batch 2026-07-15; a FRESH session should run the full
  workflow gates (triage → plan-review → spec → implement per slice) starting from this file.
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
