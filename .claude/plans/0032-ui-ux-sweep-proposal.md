# 0032 — UI/UX redesign sweep proposal (5-lens harness sweep, 2026-07-15)

- **Status:** Proposal — awaiting owner tier/question decisions (NOT approved for build)
- **Provenance:** user-requested sweep ("redesign the ui ux, make it nicer"); 5 parallel lenses
  (IA/flows · visual craft · desktop professionalism · trust copy · product-discover via the
  harness `product-designer` agent) → 74 raw findings → synthesized, deduped, prioritized.
- **Already fixed in parallel (PR #53), NOT re-proposed here:** console-flash (CREATE_NO_WINDOW),
  run-time TimePicker, wizard District→Folders lead, Settings folders-first scroll, SFTP
  test/deliver parity + deliver credential gate.

⚠️ Two findings are TRUST BUGS worth pulling ahead of any redesign:
- **Mapping Apply never reconciles the scheduled task** (Tier 2 #1) — switching district shows
  "your schedule is unchanged" while the nightly task keeps converting the OLD district.
- **Deliver auto-acks the anomaly gate** (Tier 1 #3) — `_confirm_and_deliver` passes
  `anomaly_ack=True` unconditionally.

---

## Verdict

DistrictSync's Flet UI is a six-surface trust cockpit with unusually strong plumbing — pure COUNTED verdict/copy modules, OS read-backs, an anomaly write-gate — dressed in a repetitive gradient-hero visual system that reads more Material-demo than commercial software. The single biggest gap is that the honesty model leaks at the seams: banners assert states nobody checked ("delivered cleanly" with no SFTP attempted, "your roster is syncing" with no live schedule, "schedule unchanged" while the nightly task silently keeps the old district), and every failure dead-ends with no route to a fix. The redesign thesis: keep the architecture, make honesty literal — every banner branches on a verified axis, every high-stakes write reconciles with the registered task, every failure routes to its fix, and the visual hierarchy is inverted so the verdict, not decoration, is the loudest element on screen.

## Tier 1 — Quick wins (days)

1. **Banner copy honesty pass.** Branch the CLEAN/healthy detail on the record's SFTP axis (sftp_ok → "delivered to SpacesEDU"; not attempted → "completed — files written to your output folder"); replace the hard-coded "Last night's sync…" with `friendly_timestamp(timestamp)`; scope the "Your roster is syncing" headline to a LIVE schedule read-back (else "Your roster is up to date"). Where: `home_status.py:344/398-399`, `run_history.py:222`, `verdict.py:64` (pure modules + tests).
2. **No-dead-end failure copy.** Every FAILED banner ends with a concrete next step; rewrite `classify_schedule_error`'s else-branch to lead with calm fixed copy + support path, demoting raw PowerShell text to "(Details: {msg})"; append a next-action line to Convert's generic `_on_error` card. Where: `home_status.py`, `run_history.py`, `setup_errors.py:115`, `convert.py:426`.
3. **Deliver ack provenance.** Pass `anomaly_ack=True` on the Deliver path only when the preceding build surfaced an anomaly the user acknowledged; else `False`. Where: `convert.py:443-481`. (Interim; full deliver-from-disk is Tier 2 #3.)
4. **Verdict-first Home, literally.** HealthVerdictBanner as the top element; greeting shrinks to a plain one-line header; never two stacked saturated fills. Where: `home.py`.
5. **Cap content width in the shell** (~960px; Run History keeps its wide scroll). Where: `shell.py:248` `content_host`.
6. **Plain-language vocabulary sweep.** "SFTP" → "Delivery to SpacesEDU" (keep "SFTP host" on the host field only); "Register/Unregister schedule" → "Schedule nightly sync"/"Remove nightly sync"; "GDE" → "MyEd BC extract files" (defined once); rail label "Setup" → "Settings" at graduation (position+icon anchors kept — owner question below); 12-hour times everywhere; Mapping retitled "Your district's roster setup". Where: `sftp_copy.py`, `setup.py`, `components.py:289`, `nav_rail`, `mapping.py`.
7. **Convert cold-state fixes.** Pre-setup → calm "Finish setup first" routed card; mode-aware output caption; softer "You'll need these files" list; district dropdown defaults to saved district with an amber "differs from your nightly sync district" note on override. Where: `convert.py:383/729-758`, `convert_result.py:140`.
8. **Setup badge freshness.** Re-probe the rail attention badge after register/unregister success; share the freshest ScheduleStatus via a shell callback. Where: `shell.py`.
9. **Version, About, support context.** "v{app_version()}" in the rail footer; About block in Help (copy version, release notes); prefilled PII-free support mailto; copy-icon buttons on email/URL/output path; "Open log folder" on ErrorCard + boot dialog. Where: `nav_rail.py`, `help.py`, `components.py`, `launcher.py`.
10. **Interaction-state + micro-polish sweep.** Hover/pressed/focused states on buttons/fields; tooltips (badge, glyphs, chips, Refresh, Exit); Exit icon LOGOUT→CLOSE; ROUNDED icon family + real `ft.Icon`s instead of text glyphs; zero warnings as em dash; wizard "Step N of 4" fix + hero step-preview derived from `STEP_ORDER`; hide wizard Unregister unless read-back LIVE; shorter schedule-neutral rail reassurance line. Where: `components.py`, `picker_field.py`, `nav_rail.py`, `onboarding.py`, `setup.py`.

## Tier 2 — Medium (a slice each)

1. **Mapping Apply must reconcile the scheduled task** (top trust bug — see banner above). Where: `mapping.py` `_on_apply`, `setup_flow.TaskArgs`. Interim stopgap if slipped: honest banner "Your nightly schedule still uses <old district> — open Settings and Save to update it."
2. **Make Settings Save trustworthy.** (a) Never silently downgrade an unattended task to logged-on-only on reconcile — interrupt with an explicit choice (re-enter password vs continue); (b) persist run-time edits to `cfg.schedule_time` on Save even with no registered task; scope-accurate button labels. Where: `setup.py` schedule handle + save path.
3. **Deliver from disk, not by rebuild.** Upload the already-committed output CSVs instead of re-running the conversion (faster; ships exactly what the admin reviewed). Confirm dialog reshaped with labelled Server/Folder facts. Where: `convert.py`, `uploader.py`.
4. **Close the Firefighter loop.** Fix-routing per `LatestReason` (FAILED_DELIVERY → Setup SFTP section; FAILED_ETL → Help; stale → schedule read-back) + a bounded error-category mapper for Convert `_on_error` (category only, never raw message). Where: `home_status.py:355`, `run_history.py`, `convert.py`.
5. **Demote the gradient hero; real wizard progress.** `components.page_header()` (compact title, no card) swept across 8+ hero sites — gradient reserved for first-run onboarding; segmented step-progress in the wizard; banners flush at page level; toned (8-10% tint) HEALTHY/WARNING banner variants so full-fill red keeps alarm value (contrast pairs extended). Where: `components.py`, `screens/*`, `setup.py`, `convert.py`.
6. **Component-system slice: token scale + button hierarchy.** Spacing/radius/type ramp in `tokens.py`; `title()/section()/body()/caption()` + `text_field()/dropdown()` factories; 3-tier buttons (primary filled — ONE per view; secondary OUTLINED; tertiary text; destructive outlined-red) via the single `_filled_button` seam; a test gating token usage. NO dark mode now (both visual lenses agree). Where: `tokens.py`, `components.py`, `picker_field.py`, screens sweep.
7. **Kill the false green.** Write a run record even on early `sys.exit(1)` paths; Home WARNING when an expected nightly run never arrived; Run History gains Source (Nightly/Manual) + off-district note (store fields exist). Where: `pipeline.py:420-421`, `home_status.py`, `run_history.py`.
8. **Window geometry persistence.** Persist size/pos/maximized on exit; restore clamped to the work area; first-run height `min(860, workarea)`. Where: `shell.py:186-196`, `app_config.py`.

*(Bumped by the cap, still worth slicing: editable/paste-able UNC paths in PickerField; TTL + single-flight cache for the schedule probe; shell keyboard map (F1/F5/Ctrl+1..6, Enter/Escape); persistent "Your configuration" card in Settings reusing `finish_summary_rows`.)*

## Tier 3 — Program (sketch)

1. **Push alerting on failed/missed nightly runs** (email-first, PII-free category payloads) — converts "trustable if watched" into "trustworthy unwatched"; the central product gap.
2. **Desktop-grade startup & input:** onedir-in-installer vs 7.1s onefile first paint; splash; single-instance mutex; keyboard map; UNC paths; probe caching; honest a11y audit of Flet 0.85.3.
3. **Voice guide + enforceable copy tests** (tense-honesty rules, vocabulary map, banned-jargon scan test).
4. **Mapping transparency package (instead of an editor):** per-entity plain-language detail, bundled-vs-override cue, vendor-YAML import into the user-overrides dir, "request a change" support path.
5. **"Green means fresh and proven":** input-staleness warning; PII-free what-changed summary (reuse `--diff`); optional witnessed "Test it now" round-trip at wizard finish.

## Product questions for the owner

**(d) Wizard: register the schedule at the Schedule step (today) or defer to the Finish click?** Lenses split 2-2-1.
- *Keep step-local* (ia-flows, trust-copy): deferral saves zero UAC prompts (one either way), moves failure away from where the fix lives, forces the Windows password to outlive its step, and today's finish copy is honest because the schedule is a verified read-back fact. Instead: at Finish (and Schedule re-entry), if the task is LIVE and `task_args_changed` → re-register through the existing flow — which ALSO dissolves the Delivery-before-Schedule ordering constraint (enabling the user's preferred District → Folders → Schedule → Delivery order).
- *Defer to Finish* (visual-craft, desktop-professionalism): collect-then-commit matches installer convention; single UAC at the natural commitment moment; removes the ordering coupling — provided Finish is do-then-report (real read-back rendered, never a pre-assertion) and the password field moves to Finish.
- *Conditional* (product-discover): defer only if failures route back to the failed step.
- **Common ground:** everyone wants the `task_args_changed` reconcile at Finish regardless of timing.

**(e) Mapping editor: how much does this persona need?** All five lenses converge: **none** — a YAML editor for a non-technical 2-3×/year admin is the riskiest possible trust surface on a PII pipeline. The latent needs are visibility, verification, and a safe request path (Tier 3 #4 transparency package). Remaining owner calls: (1) re-scope roadmap IA-8b as a partner/integration-engineer tool off this persona's surface; (2) the operator-model question — is the real operator ever a myBlueprint partner running several districts from one machine? If yes, Mapping "switch" needs a persona-gated partner surface; if no, record single-district as canonical.

**(alerting)** Is email-on-failure the right first push channel for BC district IT, and who owns the recipient list?

**(freshness)** What counts as "stale" input — older than ~26h, or unchanged since the last run?

**(rail label)** OK to swap "Setup" → "Settings" at graduation? (Revisits a deliberate spatial-memory decision; position+icon anchors would be kept.)
