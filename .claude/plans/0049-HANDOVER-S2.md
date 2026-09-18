# Handover — plan 0049, resuming at S-2 (2026-09-18)

Paste the block at the bottom into a fresh session. Everything above it is the state that block refers to.

## Where the build actually is

**On `main` (`89f0109`), merged and CI-green:**
- **S-1a-i** (#129) — the resolution ladder with the HKLM machine-scope switch, `_assert_machine_dir_trusted`, the process-pinned profile, `handshake_dir()`, `src/utils/dpapi.py`, `src/utils/accounts.py`, and a `windows-latest` pytest leg in `ci.yml`.
- **S-1a-ii** (#130) — `src/sftp/secret_store.py` (keyring ↔ DPAPI-LocalMachine behind `select_store()`), identity bound in the DPAPI entropy, verify-before-promote, `run_as` on the run record, scheduled-nightly zip staging.
- **S-1b-i** (#132) — `src/scheduler/provisioning.py`: the elevated `provision` / `grant_current_user` / `prune_principal` ops, create-with-SDDL, `migrate_profile`, the open-group ACE walk.
- A separate fix (#131) for a UI defect the owner reported: a gate-refused register left the previous failure card on screen.

**Open, green, awaiting the owner's merge:** **#133 — S-1b-ii** on `claude/0049-s1b-ii-surfaces`: `src/scheduler/provision_session.py` (`complete_handover`, `request_access`), the pre-shell grant window, `--diagnose`, and the `MOVED.txt` fence on `AppConfig.save()` / `write_run_record`.

**Next: S-2**, which is where machine scope stops being inert. Its branch must be cut from `claude/0049-s1b-ii-surfaces` (it calls `complete_handover`), and its PR retargeted to `main` once #133 merges. **Never stack a PR on a feature branch** — `ci.yml` fires only on PRs targeting `main`, so a stacked PR silently carries no test gate (learned the hard way on #130).

**After S-2:** the owner's manual walk #1 on a domain-joined laptop → **S-3** (the `PrincipalKind` engine model) → **S-4** (the gMSA option in Settings, labelled untested) → the owner's walk #2 → SD60.

## The specs are written and reviewed

`.claude/plans/0049-machine-scope-gmsa.md` carries `## Spec — S-1a` and `## Spec — S-1b`, each with a `S-1x.8 Review dispositions` section recording what was accepted, what was trimmed, and which amendments depart from the plan's letter. **S-2 has no spec yet** — writing it is the first real task.

Plan `## Design` **D6** is S-2's design of record, and the S-2 row of `## Slices` is its scope list.

## Three things that have gone wrong repeatedly — do not rediscover them

1. **Windows-only code is invisible to the local gates.** The suite runs on Windows here, so anything Windows-only is green locally and red on CI's Linux leg. Three instances on this plan: typeshed-guarded imports failing `mypy` (#129), a test that under-seeded a predicate's syscall seams and reached the real Win32 ACL API (#132), and a test rig that wrapped a seam but still called the real DPAPI behind it (#133). The gotcha list in `.claude/plans/0049-HANDOVER.md` §2 now carries the rule. **The cheap local proof** is to rig the Windows-only entry point to raise and re-run the file — e.g. set `elevation.dpapi_call` and `elevation._set_owner_only_dacl` to raise, then run the tests; if they pass, the path genuinely is not reached. That takes seconds against ~19 minutes of CI.
2. **`mypy src/ --exclude 'src/ui_flet' --platform linux` is a required local gate**, not an optional one.
3. **Never trigger a UAC prompt in an unattended session.** Every elevation is the owner's to approve personally. Drive elevated paths through the monkeypatched seams, as `tests/test_elevated_apply.py` does.

## Process the owner has set

- Per slice: JIT spec → a short adversarial review sized to the slice → implement → verify → PR → **read and quote CI's own `test` and `test-windows` lines** → **the owner merges; the agent never does**.
- The owner authorised continuing **without a per-slice approval pause** on 2026-09-18 ("continue until it's fully implemented"). Earlier slices used a ~6-line approval block; that gate is lifted, but surfacing real decisions in the report is not.
- Keep messages short. The owner is fatigued by long ones.
- **The `claugentic-dev-harness:*` subagent types are no longer available.** Use `general-purpose` for implementers and reviewers with the role instructions written into the prompt, and `Explore` for read-only mapping. Set `model` explicitly: implementers and critics on `opus`, mappers and digests on `sonnet`.
- Honesty constraint that outranks everything: **nothing may claim gMSA works until a district reports a green nightly.** The S-4 UI carries "not yet tested against a live domain" and the CHANGELOG says "available, untested".

## Owner-only items

- The IT ask for the domain-joined laptop (a domain user with local admin; a gMSA with the laptop in `PrincipalsAllowedToRetrieveManagedPassword`, `Install-ADServiceAccount` run there, "Log on as a batch job"; whether that laptop's GPO sets the credential-storage policy). **It gates both manual walks and has not been sent.** Offered twice; the owner has not taken it up.
- Every merge, every UAC click, the manual walks, and the reply to SD60.

---

## Paste this into the new session

```
You are continuing plan 0049 (machine-scoped install + gMSA principal) for DistrictSync at slice S-2. I'm the owner.

Read in this order, then start:
(1) .claude/plans/0049-HANDOVER-S2.md — every section; it says exactly where the build is.
(2) CLAUDE.md.
(3) .claude/plans/0049-machine-scope-gmsa.md — the approved design, its `## Design` D6 (S-2's design of record), the S-2 row of `## Slices`, and the `## Spec — S-1a` / `## Spec — S-1b` sections for the house style and the review dispositions.
(4) .claude/plans/0049-HANDOVER.md §2 (gotchas), §4 (tests that must move deliberately), §8 (hard constraints).

Context: SD60 is blocked (their GPO forbids stored task passwords; their IT uses gMSAs and asked for it by name). SD54 wants a service account without the manual runas step — S-2 is what delivers that. S-1a and S-1b are built and merged or in review; do not rebuild them.

Rules: you orchestrate and judge. Implementers and critics on opus, mappers on sonnet, always set model explicitly; the claugentic-dev-harness agent types no longer exist, so use general-purpose with the role written into the prompt. Per slice: JIT spec → a short adversarial review sized to the slice (S-2's is product + honesty, since it is mostly user-facing copy) → implement → verify → PR → read and quote CI's own `test` and `test-windows` lines → I merge. Never merge, never send email, never trigger a UAC prompt, never touch a PR you did not open. Keep messages short, and don't stop for per-slice approval — continue through S-2, S-3 and S-4, surfacing decisions in your reports.

First actions: (a) check whether PR #133 has merged — if it has, cut the S-2 branch from an up-to-date `main`; if not, cut it from `claude/0049-s1b-ii-surfaces` and retarget the PR to `main` once #133 lands (never stack a PR on a feature branch — ci.yml only fires on PRs targeting main, so a stacked PR gets no test gate). (b) Write the S-2 spec from plan D6, run its review, then implement.
```
