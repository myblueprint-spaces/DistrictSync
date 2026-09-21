# Handover — plan 0049, dev work COMPLETE (2026-09-18)

Paste the block at the bottom into a fresh session. Everything above it is the state that block refers to.

## Where the build actually is

**All four remaining slices are implemented, CI-green, and OPEN — awaiting the owner's merge, in this order:**

| PR | Slice | CI |
|---|---|---|
| #136 | **S-2a** — honest predicates + copy (inert) | green |
| #137 | **S-2b** — the provisioning flow (makes machine scope reachable) | green |
| #138 | **S-3** — the principal model | green |
| #139 | **S-4** — gMSA in Settings, labelled untested | green |

They are **stacked** — each branch is cut from the one before, so every PR's diff also shows its
predecessors' commits until they land. All four target `main` deliberately: `ci.yml` fires only on
PRs targeting `main`, so a stacked PR carries **no test gate at all** (learned on #130). Each diff
collapses to its own slice as the one before it merges.

**On `main` (merged earlier):** S-1a-i (#129), S-1a-ii (#130), S-1b-i (#132), S-1b-ii (#133), plus a
UI fix (#131). **`main` also carries `v3.22.0`** (PR #135, the SD51 heading-row fix) — cut while
S-2a/S-2b were in review, so **the shipped release contains none of the machine-scope work.**

## What remains, and whose it is

1. **The owner merges the four PRs**, in order. The agent never merges: `main` requires an approving
   review, an author cannot approve their own PR, so merging would mean `--admin` — a deliberate
   bypass of branch protection.
2. **The owner's manual walk**, with their IT team, on a domain-joined laptop. Decision recorded
   2026-09-18: *finish all the dev work first, then test* — the walks are not interleaved between
   slices, and the IT prerequisites ask is the owner's, not a gate on the build.
3. **Hand the owner a build**: `gh run download` the CI **pack** artifact from #139's run. Do NOT
   point them at a release — none contains this work, and cutting one is the owner's call.
4. **`docs/partner/managed-service-accounts.md`** (new in S-4) is the one-page hand-to-IT document:
   the three gMSA prerequisites, what DistrictSync does, and what it cannot undo.

## Three NAMED holes — read these before claiming the plan is finished

1. **`prune_principal` has a handler and an implementation but NO PRODUCER in `src/`.** Remove still
   sends only `{"op": "delete"}`, so on a machine-scoped install a retired service account keeps RX
   on `C:\ProgramData\DistrictSync` and M on `runs/`. S-4 made the op kind-aware and **nothing asks
   it to run.** A genuine hole in the plan, not a slip by a slice — S-1b built it "inert until S-2"
   and neither S-2's nor S-4's spec wired it. Needs two decisions: whether to prune on an
   *unconfirmed* delete (the existing invariant says NO — pruning a live task's principal breaks the
   nightly silently), and what to tell the admin when the prune fails after the task is gone.
   Severity, honestly: a least-privilege gap, **not a new escalation** — the account already had that
   access, and the delivery secret there is DPAPI **LocalMachine**, which the plan's own threat model
   says any process on that machine can unseal regardless of the ACE.
2. **`service_account_delivery_note`'s manual form says "sign in as it once"** — impossible for a
   managed service account. Keyed on `foreign` + `will_provision`, not the kind, so it reaches an MSA
   only in the one state where `DELIVERY_SECRET_UNREADABLE` closes the gate. Fixing it means a fourth
   required keyword on a pure function with its own copy pins.
3. **The read-back principal reaches no UI surface.** S-3 added `ScheduleReadback.run_as` /
   `.logon_type`, but every principal fact on screen still comes from the RECORD — what this app
   wrote at its last confirmed registration. A task re-pointed outside DistrictSync is described with
   a stale name everywhere. Narrowed, not closed; the open question is what to say when the two
   sources disagree *and* when the live one is unreadable.

## Four things that have gone wrong repeatedly — do not rediscover them

1. **Windows-only code is invisible to the local gates.** The suite runs on Windows here, so anything
   Windows-only is green locally and red on CI's Linux leg. Five instances on this plan. Two shapes:
   a typeshed-guarded import failing `mypy --platform linux` (#129), and a **runtime** one — a
   `sys.platform` guard does NOT protect a Windows-only import from a test that patches the platform,
   and `ImportError` is outside `OSError` (#136, five tests). **The cheap local proof:** rig the
   Windows-only entry point to raise (or `monkeypatch.setitem(sys.modules, "winreg", None)`) and
   re-run the file. Seconds, against ~20 minutes of CI.
2. **`mypy src/ --exclude 'src/ui_flet' --platform linux` is a REQUIRED local gate**, not optional.
3. **Run `bandit` AFTER the last edit.** B105 keys on the *identifier* containing secret/password, not
   the value, and **no other gate can see it** — #137's Linux leg died at 49s on three new copy
   constants while ruff, mypy and 7271 tests were green.
4. **Never trigger a UAC prompt in an unattended session.** Every elevation is the owner's to approve
   personally. Drive elevated paths through monkeypatched seams, as `tests/test_elevated_apply.py`
   does. **Any test forcing machine scope on must seed ALL THREE** of the trust predicate's raw reads
   (`_machine_switch_on`, `_read_dir_security`, `_read_dacl_aces`) — seeding fewer reaches the real
   Win32 API: green on Windows, red on Linux. That cost two PRs (#132, #133).

## Process the owner has set

- Per slice: JIT spec → a short adversarial review sized to the slice → implement → verify → PR →
  **read and quote CI's own `test` and `test-windows` lines** → **the owner merges**.
- Continue without a per-slice approval pause (authorised 2026-09-18). Surfacing real decisions in
  the report is still required.
- **Keep messages short.** The owner is fatigued by long ones.
- The `claugentic-dev-harness:*` subagent types no longer exist. Use `general-purpose` with the role
  written into the prompt, `Explore` for read-only mapping. Set `model` explicitly: implementers and
  critics `opus`, mappers `sonnet`.
- **A seam map before every spec.** The plan's own line references were wrong on S-2, S-3 and S-4 —
  each spec's `S-x.1` section records the corrections. Do not brief an implementer from plan prose.

## Honesty constraints that outrank everything

- **Nothing may claim gMSA works until a district reports a green nightly.** The S-4 UI says "not yet
  tested against a live domain", the CHANGELOG says "available, untested". No domain controller
  exists here.
- **A failed provisioning attempt is NOT free** — provisioning runs before registration, so the
  machine-scope switch is already committed when a register then fails. Both the CHANGELOG and the IT
  page say so; an earlier draft of each claimed otherwise.
- Every install provisioned before S-2 **stays per-user** until Remove -> Schedule. The partner guide
  opens with how to tell which kind of install you have, and gates the manual `--sftp-configure` step
  on that observable fact — because **the wrong answer there is silent**: delivery just stops.

---

## Paste this into the new session

```
You are picking up plan 0049 (machine-scoped install + gMSA principal) for DistrictSync. I'm the owner. The DEV WORK IS DONE — do not rebuild it.

Read in this order, then start:
(1) .claude/plans/0049-HANDOVER-S2.md — every section; it says exactly where things stand.
(2) CLAUDE.md — its machine-scope paragraph now describes S-2a through S-4 as shipped.
(3) .claude/plans/0049-machine-scope-gmsa.md — the approved design and all four Spec sections. Read each spec's S-x.1 ("corrections") before trusting any line reference in the design.

State: S-1a and S-1b are merged. S-2a/S-2b/S-3/S-4 are PRs #136/#137/#138/#139 — all CI-green, stacked, awaiting my merge. main carries v3.22.0, which contains NONE of this work.

First actions: (a) check which of #136-#139 have merged; if any are still open, tell me and stop rather than merging — I merge, never you. (b) If they have all merged, fetch me a Windows build from the CI pack artifact of the last one's run so I can test with my IT team; do not cut a release. (c) Then tell me which of the three NAMED HOLES in the handover you recommend doing first, and why — I'll decide.

Rules: you orchestrate and judge. A seam map before any spec; the plan's own line references have been wrong three times. Implementers and critics on opus, mappers on sonnet, always set model explicitly. Never merge, never send email, never trigger a UAC prompt, never cut a release without asking. Run bandit after the last edit and mypy with --platform linux. Keep messages short.
```
