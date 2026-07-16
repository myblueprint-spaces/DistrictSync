# 0034 — Trust & correctness batch (post-v3.6.0)

## Verification record (2026-07-15, autonomous run, main @ 3ab300f)
All cited claims re-verified against current main before implementation. Slices 1/2/3 grounding
CONFIRMED (anchors: `mapping.py:151-167 _on_apply`; `convert.py:443-481 _confirm_and_deliver`
rebuild-with-`anomaly_ack=True` at ~460; `setup.py:562-573 _reconcile` silent-downgrade path).
ONE divergence: Slice 4(a)'s premise is partly stale — the no-usable-input path ALREADY writes a
failed record (`src/etl/pipeline.py:471-477`, `error_category='no_input'`). Scope decision (per
the escalation rule, no owner question needed): 4(a) covers only the still-silent `SystemExit`
paths inside `run_pipeline` (input-dir missing ~428-430, config-load failure ~434-441);
`main.py` argparse-level exits are out of scope (unreachable from tests; scheduled tasks bake
validated args). Gate note: `bandit` requires `-c pyproject.toml` (bare form false-fails);
`make` absent on this machine → direct python equivalent of `validate-config` used.

- **Status:** IMPLEMENTED, PR-ready (2026-07-15 autonomous run) — all 4 slices landed on
  `fix/0034-trust-correctness` one at a time, full gate suite green after each landing,
  per-slice adversarial verify complete (slice 1 PASS + docstring polish; slice 2
  CHANGES_REQUIRED → `_counts_source` success-guard + delivery-aware FAILED_DELIVERY copy,
  re-gated PASS; slice 4 PASS + district-display memoization + 2 ROADMAP notes; slice 3
  CHANGES_REQUIRED → `ReconcileOutcome` honest Save notes + `force_blank_password`,
  re-gated). No blocked questions — the escalation rule was never triggered.
  Owner approved the batch 2026-07-15 **and pre-approved AUTONOMOUS
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
  - Slice 1 (owner 2026-07-15: **route to Settings** — no inline re-register in Mapping): Apply
    switches the district; when a LIVE schedule exists, show a prominent honest notice "Your
    nightly schedule still uses <old district> — open Settings and Save to update it" with a
    button that navigates to Setup/Settings (shell `on_navigate` pattern). Re-registration
    (password + UAC) stays Settings-owned; Mapping never collects credentials. No dependency on
    Slice 3 — the 1→3 order stands.
  - Slice 2 (owner 2026-07-15: **standalone deliver INCLUDED**): deliver-from-disk uploads the
    committed CSVs as-is; confirm dialog shows labelled Server / Folder facts (host named once);
    ONE run-store record per delivery (source="manual", sftp axes; never double-counts a build).
    ADDITIONALLY Convert offers a standalone "Deliver the files in your output folder" action —
    gated on SFTP configured + stored credential + top-level CSVs present — carrying an HONEST
    freshness fact ("files last built <friendly time>", derived from the newest output-CSV mtime)
    in both the card and the confirm dialog, so the admin always knows the vintage of what ships.
    No transform runs → the anomaly write-gate is untouched by ANY deliver path.
  - Slice 3: when a reconcile would downgrade an unattended task, interrupt with the explicit
    choice: "Keep running when signed out — re-enter the Windows password" vs "Continue — the sync
    will only run while signed in" (calm copy, no default that downgrades silently).
  - Slice 4: missed-run rule = schedule read-back LIVE **and** no run record in the last **26 h**
    → Home WARNING "We expected a nightly sync that didn't arrive" + route to Run History/Setup.
    **Fresh-start guard (no false alarm):** suppress the warning when the schedule/store is too
    new for a run to have been expected — i.e. also require that the registration is old enough
    for a scheduled occurrence to have passed (use `store_meta().created_at` / newest record /
    schedule read-back facts already available; when in doubt, stay silent — a false "missed run"
    on day one costs more trust than a 1-day-late first warning).
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
