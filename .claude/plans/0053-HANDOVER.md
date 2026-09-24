# 0053 — Handover (paste the prompt below into a fresh session)

Last updated: 2026-09-24 (Gate A approved; S0 awaiting the owner's merge; S1 next).

---

## Prompt for the next session

> You are continuing **plan 0053 — ETL failure policy** in DistrictSync (`C:\Users\shan.peiris\Documents\Integrations\DistrictSync`). Read, in this order: `CLAUDE.md` (auto-loaded), `.claude/plans/0053-HANDOVER.md` (this file — the **State** section tells you exactly where to resume), then `.claude/plans/0053-etl-failure-policy.md` (the plan: Problem, Approach, the **Policies P1–P16** table, the **Naming table**, the conflict map, and the **Spec** for the slice you are on — you do not need to read other slices' specs). Do not re-litigate decisions recorded in the plan's Owner-decisions table or in `docs/claugentic-DECISIONS.md`.
>
> Work the next unchecked slice per the **State** section. Follow the plan's *Execution scaffolding* section, with the **landing model changed by the owner on 2026-09-24**: branch `claude/0053-s<n>-<slug>` off the tip of the INTEGRATION branch `claude/0053-etl-failure-policy` (merge `origin/main` into the integration branch first if `main` moved); one implementer agent per slice (Opus; effort high, xhigh for S4/S9/S10), an architect-reviewer audit, all gates green locally; then YOU fast-forward the slice into the integration branch and push, and read + QUOTE the CI result on the integration draft PR. **Nothing merges to `main`** — the owner tests the CI exe after the last slice and merges the one PR. No release after S4; the release comes at the end. Keep your own context lean: delegate reads to Sonnet agents and judgment/review to Opus; the owner reads long replies with fatigue — answer point-first in a few lines. Update this handover file's **State** section at the end of every slice.

---

## State

| Slice | Status | Branch / PR | Notes |
|---|---|---|---|
| Plan | Stage 3 complete; **Gate A COMPLETE 2026-09-24** (owner approved §3/§5 as written — DECISIONS 2026-09-24) | committed with S0 | D1 = all four optional feeds ISOLATABLE (owner overrode the (b) recommendation); D3 = exit 0 + PARTIAL; D4 = validated config-declared labels |
| **S0** | **MERGED 2026-09-24** (`f0e3d46`; docs only). The Gate A record commit landed after the merge, so it rides S1's branch | `claude/0053-s0-failure-policy` (off `8d33664`) → **PR #144** (https://github.com/myblueprint-spaces/DistrictSync/pull/144), commits `d88aa5f`, `6542ff0` (Q5 evidence; CI run 36016686379 green), + the Gate A record | Local gates green 2026-09-23: 7,627 passed / 98 skipped, coverage 96.5%, ruff clean, email scan OK. Written by an Opus writer + 3 rounds of adversarial verification (truth-vs-code, catalogue, harness) |
| S1 | **DONE — integrated** (PR #145 CI green, run 36030089404/36030089777: test 17m55s, test-windows 11m30s, pack ×3 pass; #145 closed in favour of the integration PR) | `claude/0053-s1-typed-errors` (off `f0e3d46`) → `claude/0053-etl-failure-policy` | Implemented + 3-lens review (architect APPROVE; 5 findings confirmed and fixed, 4 refuted). Local: 7,707 passed / 98 skipped, coverage 96.53%, ruff/mypy(+linux)/bandit/emails/tree/20 configs green. Real drops: Unity 20260922 `data`→`source_schema`; Unity 20260914 + SD51 20260918 outputs byte-identical to base |
| S2–S15 | not started | — | — |

### Q5 evidence gathered 2026-09-24 (now recorded in `output-contract.md` "Evidence so far" as E1–E6, on #144; `Q5-status` stays `open`)
- **Owner (2026-09-24):** the import runs in a HIERARCHY — it can import users (Students/Staff) alone, but not Enrollments without Classes, and not Family without users. This matches the plan's CRITICAL set and `DEPENDS_ON`.
- **Owner (2026-09-24):** Unity Christian has the family-association removal toggle **OFF** (E2). Narrowed for Unity, not resolved: what an absent `Family.csv` does is still Q5a, unconfirmed.
- **Confluence:** "import the files that are on the SFTP, even if not all file types are there" (SpacesEDU - Advanced CSV Requirements, NP, 2025-03-17). Family: toggle "Remove imported School-Family-Student Associations no longer in data" — when ON, "the family data set must be a complete file set" (REQ page 4149313554, 2026-07-15). Staff deactivation and student class removal are ALSO per-district toggles, and student removal is a SOFT remove (Imports KB, SKB 3847160494) — `docs/partner/faq.md` now states them as setting-dependent (corrected on #144).
- Still NOT found: header-only file behaviour (Q5b); alerting on a missing course/attendance feed (Q5c).

**S0 findings the next slices need:** `failure-policy.md` §5 is the verified site catalogue (39 rows incl. new defects: blended `_add_session_key` silently merges classes when time/school columns are absent — #39; a missing `User ID` SOURCE column makes the roster `{"<NA>"}` and drops every downstream student row — #27(ii); `staff._merge_roster` raw KeyError — #13a). Prefer its line numbers over the plan's. S2's `DEPENDS_ON` gains `Classes→{Students}` (already in the plan).

**Gate A answers (owner, 2026-09-24 — all three confirm §3/§5 as written):** (1) an unlisted entity is CRITICAL until promoted; (2) a missing LINKING column (class (b), co-teacher columns included) fails the night, behind S10's measurement gate; (3) a missing NICE-TO-HAVE column (class (d), e.g. homeroom teacher name — site #35) is blank + warn, never a run failure.

**Integration PR:** `claude/0053-etl-failure-policy` → `main`, DRAFT (see the PR link in the State table's header note below). **Next action:** start S2 (spec: plan § "S2 — Per-entity outcome ledger").

**S1 findings the next slices need:** `errors.available_columns_note(count)` is the one "count, never headers" phrasing; site #4 (`grades.filter_to_grade_scope`) carries the RESOLVED lower-cased grade column until S9; `ExtractionError` also covers the case-insensitive filename collision (records `input_unreadable`); `config_editor.humanize_config_error` still routes by message text (S3/S5 concern); SD51 20260918 builds Family = 0 rows (feeds the plan's SD51-Family verify item, S6); no site tags added (S11 owns them).

### S0 checklist (from the plan's S0 Spec — tick as done)
- [x] `docs/developer/failure-policy.md` §0–§14, ≤300 lines, every row's Status truthful about TODAY
- [x] §3 criticality table per D1 (decided), with the "CourseInfo/StudentCourses isolatable by owner decision ahead of partner evidence" rationale
- [x] §5 site catalogue (every site listed in the S0 spec)
- [x] `docs/developer/output-contract.md` Q5 (a–e) after Q4 with priority note + `Q5-status: open`; `tests/test_output_contract_doc.py` `_EXPECTED_QUESTION_COUNTS` updated and green
- [x] `docs/partner/faq.md:19-21` + `help-centre-myedbc-districtsync-guide.md` (:175 and the missing-file passage) restated to today's truth
- [x] DECISIONS entry (+ D1/D3/D4 recorded) · INVARIANTS pointer rows · ROADMAP "Plan 0053" cross-reference heading · CANDIDATES lesson · CLAUDE.md +1 line · adding-transformer.md / adding-district.md checklist lines
- [x] `python scripts/check_no_emails.py` green; `pytest tests/test_output_contract_doc.py` + any doc-parity tests green; full suite green
- [x] commit `d88aa5f` (plan + handover + S0), pushed, PR #144 opened · [x] CI read and quoted: run 35914480408 — `test` (ubuntu) pass 17m45s, `test-windows` pass 11m27s, watch exit 0 (PR CI has no macOS job; macOS runs only in release.yml)

---

## Context a fresh session needs (not in the plan)

- **The incident:** Unity Christian's 2026-09-22 drop reverted `EmergencyContactInformation.txt` to the PLAIN report (18 cols). `unitychristianmyedbc`'s Family `row_filters` on `Parent Auth / Guardian` raised and the whole run failed. Workaround used on 2026-09-23: a throwaway user-dir overlay (`_base: unitychristianmyedbc`, Family removed from `enabled_entities`) run through the **released v3.25.0 exe** (`%USERPROFILE%\Downloads\DistrictSync-windows.exe`, SHA-256 `960b7aad…` = the release asset) → Students 667 / Staff 46 (all `teacher`) / Classes 147 / Enrollments 2,738 in `…\roster-validation\partners\unitychristianchilliwack\20260922\output`. The overlay and temp profile were deleted. The owner is asking the school for the Enhanced report again.
- **Lesson already paid for:** the first regeneration used a stale local checkout (v3.23.0) and shipped administrators in `Staff.csv`. Always `git fetch && git merge --ff-only origin/main` before running from source, or use the released exe and prove it by checksum.
- **Local runs:** always set `DISTRICTSYNC_DATA_DIR` to a scratch dir and use a SHORT output path (MAX_PATH). `make` does not exist on this machine — use the venv python forms (`.\.venv\Scripts\python.exe -m pytest …`). Run bandit AFTER the last edit (`-c pyproject.toml`). Check Windows-only imports with `mypy --platform linux`.
- **Real partner drops for measurement** (never commit): `C:\Users\shan.peiris\source\repos\roster-validation\partners\<district>\<yyyymmdd>` — Unity `unitychristianchilliwack\20260922` (plain) and `\20260914` (enhanced), SD51 `sd51\20260918`.
- **Research artefacts** (only if a slice needs the original evidence): workflow `wf_e90e9a97-c7f` journal under `C:\Users\shan.peiris\.claude\projects\C--Users-shan-peiris-Documents-Integrations-DistrictSync\84c8225a-ae9f-4057-850d-cc700b2707ad\subagents\workflows\wf_e90e9a97-c7f\journal.jsonl` (large — have a Sonnet agent digest it; never read raw).
- **Partner fact to keep straight:** `docs/partner/faq.md` ("What happens to students or staff no longer in the file?" and "What happens to enrollments no longer in the file?") covers rows missing from a DELIVERED file only. What SpacesEDU does with an ABSENT CSV is unknown (Q5) — never write partner copy that claims "no side effects" for an omitted file.
- **Owner preferences:** point-first, short answers; Fable only for planning, Opus executes; the owner merges PRs; ask before any irreversible/public step (push/PR/release are pre-authorised for S0 only — ask again per slice unless the owner widens it).
