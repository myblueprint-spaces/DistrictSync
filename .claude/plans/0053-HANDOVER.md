# 0053 — Handover (paste the prompt below into a fresh session)

Last updated: 2026-09-23, end of the planning session (Fable 5.1 orchestrated the plan; Opus 5.5 started execution).

---

## Prompt for the next session

> You are continuing **plan 0053 — ETL failure policy** in DistrictSync (`C:\Users\shan.peiris\Documents\Integrations\DistrictSync`). Read, in this order: `CLAUDE.md` (auto-loaded), `.claude/plans/0053-HANDOVER.md` (this file — the **State** section tells you exactly where to resume), then `.claude/plans/0053-etl-failure-policy.md` (the plan: Problem, Approach, the **Policies P1–P16** table, the **Naming table**, the conflict map, and the **Spec** for the slice you are on — you do not need to read other slices' specs). Do not re-litigate decisions recorded in the plan's Owner-decisions table or in `docs/claugentic-DECISIONS.md`.
>
> Work the next unchecked slice per the **State** section. Follow the plan's *Execution scaffolding* section: isolated branch `claude/0053-s<n>-<slug>` off a fast-forwarded `origin/main`; one implementer agent per slice (Opus; effort high, xhigh for S4/S9/S10), an architect-reviewer audit before landing, all gates green, CI's three-OS result read and QUOTED, the **owner merges** (never the agent). Keep your own context lean: delegate reads to Sonnet agents and judgment/review to Opus; the owner reads long replies with fatigue — answer point-first in a few lines. Update this handover file's **State** section at the end of every slice.

---

## State

| Slice | Status | Branch / PR | Notes |
|---|---|---|---|
| Plan | Stage 3 complete; Gate A decided except §3/§5 approval | untracked → committed with S0 | D1 = all four optional feeds ISOLATABLE (owner overrode the (b) recommendation); D3 = exit 0 + PARTIAL; D4 = validated config-declared labels |
| **S0** | **PR OPEN — awaiting CI + owner review/merge** (docs only) | `claude/0053-s0-failure-policy` (off `8d33664`) → PR: see `gh pr list --head claude/0053-s0-failure-policy` | Local gates green 2026-09-23: 7,627 passed / 98 skipped, coverage 96.5%, ruff clean, email scan OK. Written by an Opus writer + 3 rounds of adversarial verification (truth-vs-code, catalogue, harness) |
| S1–S15 | not started | — | S1 waits for the owner to approve §3 + §5 of `docs/developer/failure-policy.md` after S0's PR |

**S0 findings the next slices need:** `failure-policy.md` §5 is the verified site catalogue (39 rows incl. new defects: blended `_add_session_key` silently merges classes when time/school columns are absent — #39; a missing `User ID` SOURCE column makes the roster `{"<NA>"}` and drops every downstream student row — #27(ii); `staff._merge_roster` raw KeyError — #13a). Prefer its line numbers over the plan's. S2's `DEPENDS_ON` gains `Classes→{Students}` (already in the plan).

**Next action:** if S0's PR is open, read its CI (`gh pr checks <N> --watch` in the background; check `PIPESTATUS`, never `--fail-level`), quote the result to the owner, and ask for §3/§5 approval + merge. Once merged, start S1 on a new branch.

### S0 checklist (from the plan's S0 Spec — tick as done)
- [x] `docs/developer/failure-policy.md` §0–§14, ≤300 lines, every row's Status truthful about TODAY
- [x] §3 criticality table per D1 (decided), with the "CourseInfo/StudentCourses isolatable by owner decision ahead of partner evidence" rationale
- [x] §5 site catalogue (every site listed in the S0 spec)
- [x] `docs/developer/output-contract.md` Q5 (a–e) after Q4 with priority note + `Q5-status: open`; `tests/test_output_contract_doc.py` `_EXPECTED_QUESTION_COUNTS` updated and green
- [x] `docs/partner/faq.md:19-21` + `help-centre-myedbc-districtsync-guide.md` (:175 and the missing-file passage) restated to today's truth
- [x] DECISIONS entry (+ D1/D3/D4 recorded) · INVARIANTS pointer rows · ROADMAP "Plan 0053" cross-reference heading · CANDIDATES lesson · CLAUDE.md +1 line · adding-transformer.md / adding-district.md checklist lines
- [x] `python scripts/check_no_emails.py` green; `pytest tests/test_output_contract_doc.py` + any doc-parity tests green; full suite green
- [ ] commit (plan + handover + S0), push, PR, CI read and quoted

---

## Context a fresh session needs (not in the plan)

- **The incident:** Unity Christian's 2026-09-22 drop reverted `EmergencyContactInformation.txt` to the PLAIN report (18 cols). `unitychristianmyedbc`'s Family `row_filters` on `Parent Auth / Guardian` raised and the whole run failed. Workaround used on 2026-09-23: a throwaway user-dir overlay (`_base: unitychristianmyedbc`, Family removed from `enabled_entities`) run through the **released v3.25.0 exe** (`%USERPROFILE%\Downloads\DistrictSync-windows.exe`, SHA-256 `960b7aad…` = the release asset) → Students 667 / Staff 46 (all `teacher`) / Classes 147 / Enrollments 2,738 in `…\roster-validation\partners\unitychristianchilliwack\20260922\output`. The overlay and temp profile were deleted. The owner is asking the school for the Enhanced report again.
- **Lesson already paid for:** the first regeneration used a stale local checkout (v3.23.0) and shipped administrators in `Staff.csv`. Always `git fetch && git merge --ff-only origin/main` before running from source, or use the released exe and prove it by checksum.
- **Local runs:** always set `DISTRICTSYNC_DATA_DIR` to a scratch dir and use a SHORT output path (MAX_PATH). `make` does not exist on this machine — use the venv python forms (`.\.venv\Scripts\python.exe -m pytest …`). Run bandit AFTER the last edit (`-c pyproject.toml`). Check Windows-only imports with `mypy --platform linux`.
- **Real partner drops for measurement** (never commit): `C:\Users\shan.peiris\source\repos\roster-validation\partners\<district>\<yyyymmdd>` — Unity `unitychristianchilliwack\20260922` (plain) and `\20260914` (enhanced), SD51 `sd51\20260918`.
- **Research artefacts** (only if a slice needs the original evidence): workflow `wf_e90e9a97-c7f` journal under `C:\Users\shan.peiris\.claude\projects\C--Users-shan-peiris-Documents-Integrations-DistrictSync\84c8225a-ae9f-4057-850d-cc700b2707ad\subagents\workflows\wf_e90e9a97-c7f\journal.jsonl` (large — have a Sonnet agent digest it; never read raw).
- **Partner fact to keep straight:** `docs/partner/faq.md` ("What happens to students or staff no longer in the file?" and "What happens to enrollments no longer in the file?") covers rows missing from a DELIVERED file only. What SpacesEDU does with an ABSENT CSV is unknown (Q5) — never write partner copy that claims "no side effects" for an omitted file.
- **Owner preferences:** point-first, short answers; Fable only for planning, Opus executes; the owner merges PRs; ask before any irreversible/public step (push/PR/release are pre-authorised for S0 only — ask again per slice unless the owner widens it).
