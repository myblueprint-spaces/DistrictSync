# 0051 — Convert loads the config's file set, not the whole folder

- **Status:** BOTH SLICES IMPLEMENTED. Stage 3 review returned CHANGES REQUIRED × 8, all folded
  into the body and the code.
- **Resumable from:** nothing outstanding; the release (version bump + tag) is the next step.
- **Blockers:** none
- **Flags:** Slice 2 changes shared ETL behaviour on a path all 20 districts run — see Risks.
  Slice 1 began landing in the working tree before Stage 3 closed (user instruction to proceed);
  the review caught that and it is declared here rather than left unmentioned.
- **Disposition at close:** two slices; Slice 1 alone satisfies the reported fault.
- **Roadmap item:** the 2026-09-22 "Convert parses EVERY `.csv`/`.txt` in the input folder" entry in
  `docs/claugentic-ROADMAP.md`. This plan closes findings (1) and (3); finding (2) is Slice 2.
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · commit `431dc40` (the SD67 config fix that
  surfaced this) · plan 0050 (the adjacent output-folder pre-flight, same screen)

## Problem

**Convert's file set is the folder's contents. The CLI's file set is the config's. They disagree, and
the folder wins.**

`convert_job` reads its inputs through two functions that never see the config:

- `screens/convert.py:502` `_read_gde_bytes(input_dir)` — reads **every** file whose suffix is
  `.csv`/`.txt` (`_GDE_SUFFIXES`, `convert.py:176`), keyed by its on-disk name.
- `screens/convert.py:301` `DataExtractor("").load_from_bytes(sources, file_headers)` — parses
  **every** key it is handed, raising `ExtractionError` on any it cannot parse
  (`extractor.py:195`).

The pipeline's disk path reads `extract_required_files(config)` (`pipeline.py:128`) — the enabled
entities' `source_files`, and nothing else.

So a district that points Convert at its raw MyEd BC export folder has its run decided by extracts
DistrictSync never reads.

### What SD67 hit (reproduced, not inferred)

A district with no accident records exports `AccidentInformation.txt` **empty**. An empty file has no
delimiter on line 1, so `_detect_delimiter` returns `None` (logged as `auto`), the C engine is
skipped entirely, and the python engine's sniffer raises — `_read_with_fallback` returns `None` and
`_load_bytes` raises. Their log shows exactly that sequence. Convert died on a file the config does
not name, while the nightly CLI run over the same folder would have succeeded.

Reproduced against `sd67myedbc`: the required set is 8 files; `AccidentInformation.txt` is not one of
them, and `_read_gde_bytes` picks it up regardless.

**It was reported as a custom-mapping fault and is not one.** The overlay was sound — `_build_renames`
(`config/authoring.py:459`) propagates a rename to *every* site naming the original, so the admin's
one-file rename did reach all three `student_demographic` sites. The same folder fails identically on
the bundled config. Any diagnosis that stops at the overlay is wrong.

### Two more divergences on the same two functions

Both found while reproducing the above; both verified by repro, not read off the source.

- **Case.** `_read_gde_bytes` keys by the on-disk name, so matching is exact. `load_data` falls back
  to `_resolve_case_insensitively` (`extractor.py:62`), which also **fails loudly on a case
  collision** rather than guessing. A district whose export is `studentdemographicenh.txt` against a
  config spelling `StudentDemographicEnh.txt` converts fine on the nightly and, in Convert, produces
  no Students at all. It does **not** ship bad data — `check_delivery_integrity` refuses it — but as
  `incomplete_roster`, which names the symptom and points nowhere near the filename.
- **Empty-but-present.** An **absent** named source yields an empty frame and skips the entity
  (`load_data`, `extractor.py:127`); an **empty** one raises. The same "no records" fact, answered
  two ways, on **both** paths — so an empty `EmergencyContactInformation.txt` fails a nightly today.
  This one is not a Convert/CLI divergence; it is a shared-core inconsistency the same investigation
  exposed.

### Why this drifted

`load_from_bytes` documents itself as being for "browser uploads" (`extractor.py:148`) — a survivor
of the retired Streamlit UI, where the bytes genuinely arrived without a folder. The Flet Convert
screen reads a **local folder on disk**, so it has been paying the cost of a second parsing
entrypoint while getting none of its benefit. Measured blast radius: `_read_gde_bytes` has **one**
caller and `load_from_bytes` has **one** production caller — the same line.

## Goals / Non-goals

- **Goal:** Convert loads exactly the files the config names, so an unrelated file in the input
  folder cannot decide a run. This is the reported fault and the user's stated requirement.
- **Goal:** Convert and the CLI resolve filenames the **same way** from the same folder — one
  resolution rule, not two implementations that agree today.
- **Goal:** close the divergence structurally, by deleting the second path, rather than by teaching
  it to filter. A filter would leave two parsing entrypoints free to drift again.
- **Goal (Slice 2):** one answer to "this named source file has no records", on both paths.
- **Non-goal — the expected-files chips.** `_expected_files` (`convert.py:1546`) deliberately uses
  `advisory_expected_files`, NOT `extract_required_files`: it is free to narrow past the
  scope-agnostic set the extractor must keep (`pipeline.py:172`). Three lists is correct here; this
  plan collapses the *loading* list onto the CLI's and leaves the advisory one alone.
- **Non-goal — telling the admin which folder files were ignored.** Once Convert only reads what it
  needs, the extra files are simply irrelevant, and a chip enumerating a district's whole export
  folder is noise. Revisit only if a district asks.
- **Non-goal — the preflight's blind spot.** `preflight_report` (`screens/creator.py:491`) reads
  `PipelineResult.input_columns`, built from `raw_data` (`pipeline.py:1062`) — already narrowed to
  the config's files, so a creator preflight cannot report a column in a file the config doesn't
  name. Pre-existing, unchanged by this plan, and out of scope.
- **Non-goal — surfacing a new error category.** Nothing here adds one.
- **Non-goal — the version bump.** `pyproject.toml` + the `[Unreleased]` → `[3.24.0]` promotion
  happen at release time over this and `431dc40`. The CHANGELOG **entry** is NOT deferred: this slice
  changes shipped behaviour twice (case collision, locked file), and `[Unreleased]` is live.

## Approach

### Slice 1 — Convert reads through `load_data`

Replace both lines at `convert.py:300-301` with the disk path the CLI already uses:

```
required = extract_required_files(config)
raw_data = DataExtractor(str(input_dir)).load_data(required, file_headers=file_headers)
```

and **delete** `_read_gde_bytes` (its only caller is the line being replaced).

**It is three lines, not two — the emptiness test has to change with them.** `convert_job` returns
`NO_INPUT` on `not raw_data`, which worked only because `load_from_bytes` OMITS a key for a file it
was not given. `load_data` inserts an EMPTY frame per absent file, so the dict is never falsy and the
status becomes dead code — and the fallout is not a dead enum member: an empty or wrong folder would
fall through to `check_delivery_integrity` → `NO_OUTPUT` → a **failed Run History row** and a red
Home, with copy pointing at the district instead of the folder. The single most common admin error
would get the wrong answer. Fixed by lifting the CLI's own predicate to
`pipeline.has_no_usable_input(raw_data)` and calling it from BOTH sites — **single-sourced, never
re-spelled**, because re-spelling it recreates the two-implementations fault this plan exists to
delete.

Everything else the divergence cost comes back for free, because it is the CLI's own behaviour:

| | today (Convert) | after |
|---|---|---|
| files read | every `.csv`/`.txt` in the folder | the config's `source_files` |
| an unreferenced file | parsed; can fail the run | never opened |
| case mismatch | silent miss | `_resolve_case_insensitively` |
| case collision | silent, arbitrary | raises, naming both files |
| absent named file | key omitted | empty frame (same downstream) |

**`_GDE_SUFFIXES` stays.** It still serves `_present_gde_files` (`convert.py:1534`), the chips'
folder listing — which *should* show the admin everything they dropped in. Only the **loading** list
narrows.

**Why not filter `_read_gde_bytes` by the required set instead?** It would fix the crash and leave
the case rule, the collision guard and the second parsing entrypoint diverged — three of the four
rows above. The reported fault is a symptom of two implementations of one job; fixing the symptom is
what leaves the next one to be found by a district.

**Then `load_from_bytes` has no production caller, and it is RETIRED** — `_load_bytes` stays as the
core `load_data` dispatches to. A second public parsing entrypoint that nothing calls is exactly the
surface that drifted here.

Its tests are audited case-by-case rather than bulk-deleted, so no coverage is lost silently:

| test | disposition |
|---|---|
| `test_load_utf8_comma_csv_bytes` · `test_load_tab_separated_bytes` · `test_load_latin1_encoding_bytes` · `test_column_names_normalized_bytes` · `test_multiple_sources_bytes` · `test_empty_bytes_raises_extraction_error` · `test_headers_only_bytes_returns_empty_dataframe` · `test_legacy_cp1252_bytes` · `test_utf8_with_junk_bytes_uses_replace` | **DELETE** — each has an exact disk twin that still runs |
| `test_headerless_injection_via_bytes` | **MIGRATE** to `load_data`. No disk twin existed: the echoed-header class reaches for the private `_load_bytes`, so nothing asserted a `headers:` block survives the trip through the public entrypoint at all — and that is how SD40's schedule and SD51's daily absences are read |
| `test_clean_utf8_accents_round_trip_exactly` (was `test_clean_utf8_bytes`) | **MIGRATE**. The nearest disk test writes latin1 and asserts only that the ASCII part survived, so it would pass on mojibake; this one would not |
| `test_not_supplied_source_is_absent_not_backfilled` | **DELETE — with the contract it pinned.** "This method must NOT back-fill empty frames for missing keys" is the OLD design's invariant; `load_data` deliberately does the opposite, and that reversal is what makes `has_no_usable_input` necessary |
| `TestDiskBytesParity` (3 tests) | **DELETE.** It existed only to prove the two entrypoints agreed — a tautology once there is one, and exactly the vacuous green CLAUDE.md bans |

Landed as a **second commit inside the slice**, so the district-facing fix stays revertible alone.

### Slice 2 — one answer for an empty named source file

Make a file whose bytes hold no parseable content resolve to an **empty DataFrame**, not an
`ExtractionError` — matching what an *absent* file already does.

The rule is narrow and must stay narrow: **empty is not the same as unparseable.** A file with
content that no encoding/delimiter can read is still a loud failure. Only "there is nothing here"
becomes an empty frame.

**The predicate is NOT "empty or whitespace-only" — that draft was both incomplete and too wide**
(probed against the real extractor, review item 4):

- **BOM-only bytes still raise and MUST be caught.** `b"ï»¿"` and `b"ï»¿

"`
  are not whitespace to `bytes.strip()`, so a naive predicate misses them — and that is precisely
  what PowerShell `Out-File` / `Export-Csv -Encoding UTF8` writes for an empty export, i.e. the most
  likely real shape. Strip a leading UTF-8/UTF-16 BOM first, or test the decoded text.
- **Whitespace-only does NOT raise today, so it is out of scope.** `b"   
  
"` parses into a 1×4
  junk frame and the entity RUNS. Folding it in would be a silent behaviour change on a path all 20
  districts share, dressed up as a bugfix. Slice 2 changes **only the raising cases**.
- **Already correct, must stay untouched:** `b",,,
"` and `b"		
"` already yield 0-row frames.

So: strip a leading BOM; if what remains is empty or nothing but line endings, log WARNING **naming
the file** and return an empty frame. Implemented as `DataExtractor._carries_no_record`, whose
matched set is EXACTLY what used to raise — measured, then pinned both ways: seven shapes that used
to raise now load as zero rows, and five that parsed before (whitespace with SPACES, `,,,
`,
`		
`, a header row) are asserted untouched. The ` ` strip covers the UTF-16 case, whose BOM
leaves NUL padding behind.

**The diagnostic obligation.** Today an empty required source raises an `ExtractionError` that NAMES
the file. After Slice 2 the run walks on to `incomplete_roster` / `NO_OUTPUT`, which names the
symptom and points nowhere near the filename — the exact loss this plan criticises in finding (3).
The load-site log becomes the only trace, so the WARNING level, the filename in the message, and an
assertion on both are part of the slice, not polish.

**Why this is safe rather than a swallowed error.** "Fail loudly" exists to stop a config/column
mismatch shipping a wrong roster. An empty file has no mismatch to hide, and the genuinely
catastrophic case — an empty *demographic* export — is already caught downstream by
`check_delivery_integrity` on **both** paths (verified: an empty roster yields `incomplete_roster`,
not a delivery). What the current raise actually buys is a nightly that dies on an empty
`EmergencyContactInformation.txt` when the correct outcome is "no family rows this run".

**Sequencing.** Slice 2 is independent of Slice 1 and each lands complete on its own. Slice 1 alone
satisfies the reported fault; Slice 2 closes the fault a district hits when the empty file is one we
*do* read. Recommend both in the release, Slice 1 first.

## Architecture & holistic fit

- **Layering.** Strictly improved: `convert.py` stops owning a bytes-reading loop and calls the ETL
  the way every other caller does. No new UI→ETL surface; one fewer.
- **Single source of truth.** The point of the change. After Slice 1 there is one list of files to
  load (`extract_required_files`) and one resolution rule, reached identically from both paths.
- **SOLID > DRY > KISS > YAGNI.** Deleting `_read_gde_bytes` (and, if approved, `load_from_bytes`)
  removes code rather than adding a filter — the YAGNI-consistent direction.
- **No permissive default on a safety-relevant parameter.** Slice 2 must not widen into "unparseable
  → empty frame". The narrowness *is* the safety property; the reviewer should attack it there.

## Affected files

| file | slice | change |
|---|---|---|
| `src/ui_flet/screens/convert.py` | 1 | call `load_data`; delete `_read_gde_bytes`; update the module + `convert_job` docstrings, which name `load_from_bytes` at `:11`, `:223`, `:229` |
| `src/etl/extractor.py` | 1 (opt) | retire `load_from_bytes` if approved |
| `src/etl/extractor.py` | 2 | empty/whitespace-only bytes → empty frame in `_load_bytes` |
| `src/etl/pipeline.py` | 1 | add `has_no_usable_input`; route `run_pipeline`'s own check through it |
| `tests/test_convert_input_scoping.py` | 1 | NEW — the slice's red-first artefact |
| `tests/test_pipeline_parity.py` | 1 | `_run_ui_path` was a THIRD hand-rolled copy of `convert_job`; it now CALLS `convert_job`, so the lock guards the real thing instead of mirroring it |
| `tests/test_pipeline_run_store.py` | 1 | `:616-617` monkeypatches both deleted/changed names |
| `tests/test_extractor.py` | 1 | retire the bytes-path classes (audit below) |
| `tests/test_extractor.py` | 2 | the **disk** empty-file assertion `test_empty_file_raises_extraction_error` inverts (NOT the bytes twin, which Slice 1 deletes) |
| `CHANGELOG.md` | 1 | `[Unreleased]` — the fix plus the two behaviour changes |
| `docs/claugentic-ROADMAP.md` | both | close the entry |
| `docs/claugentic-DECISIONS.md` | both | dated entry: Convert/CLI parity; empty ≠ unparseable |

`docs/claugentic-ARCHITECTURE_TREE.md:17` DOES change: it describes `extractor.py` as having "two
public entrypoints … `load_data` (disk) and `load_from_bytes`", false after this slice. The
mechanical gate only checks add/move/remove, but CLAUDE.md's keep-it-current rule covers the
description. No file is added, moved or removed.

## Risks & mitigations

- **A config whose school-year source file is OUTSIDE its required set.** The plan first called this
  risk nil; it is not. `determine_school_year_detailed` resolves `global_config.school_year_sources`
  filenames out of the loaded data (`transformers/dates.py:257`), and `extract_required_files`
  includes those only when an enabled entity also names them — its own docstring says so. Verified
  across all 20 shipped configs: **`mbp_core`, `mbponly`, `sd38myedbc` and `sd51attendance`** name
  `StudentSchedule.txt` as their school-year source while it is NOT in their required set. So Convert
  moves from source-derived to calendar-fallback for those four — which is what the CLI has always
  done, so this is the parity this plan is for, not a new divergence. **No output delta today:**
  verified that none of those four configs' ACTIVE entities carries a `use_academic_year` /
  `append_year_to_id` field. That is an accident of their `enabled_entities`, not construction, so it
  is PINNED by `TestSchoolYearSourcesAreNotSilentlyNarrowed` — enabling `Classes` on one of those
  tiers can never silently re-key Class IDs via `append_year_to_id`.
- **A locked / unreadable input file now stops the run.** `_read_gde_bytes` swallowed a per-file
  `OSError` and carried on; `load_data` reads unguarded. An export held open by Excel or an AV
  scanner goes from "entity silently dropped, run reports built" to Convert's error card.
  **Accepted deliberately:** a run that quietly ships a roster missing everyone in the locked file is
  the failure mode this product fails loud to avoid, and it is what the CLI has always done. Pinned
  by `TestAnUnreadableSourceFileFailsLoudly`. It is a new user-visible failure mode on a shipped
  path, so it is in the CHANGELOG.
- **`load_data` logs `ERROR File not found` per absent file.** Pre-existing CLI behaviour, now also
  on Convert. A district missing an optional extract will see ERROR lines for a run that succeeds.
  Cosmetic, but it reaches `etl_tool.log`, which support reads — the reviewer should decide whether
  it stays as-is in this plan or gets its own item.
- **The case-collision raise is new to Convert.** A district with both `Students.txt` and
  `students.txt`, where the config names neither exactly, now fails loudly instead of silently
  loading one. Correct, and the reason the guard exists — but it is a behaviour change on a shipped
  path, so the release note must say so.
- **Slice 2 touches every district.** A shared-core change on a path all 20 run. Mitigated by the
  narrowness (empty ≠ unparseable), by the downstream integrity gate, and by the SD74 snapshot.
- **Both slices ship in one release with an unrelated config change (`431dc40`).** Keep them as
  separate commits so either can be reverted alone.

## Test strategy

Red-first. Every case below is already reproduced in a scratch harness, so the tests are
transcriptions of observed behaviour, not predictions.

**Slice 1** — `tests/test_convert_input_scoping.py` (new; 16 cases, 4 of them red before the change):

1. Convert over a valid GDE set **plus** an empty `AccidentInformation.txt` succeeds. The reported
   bug. *(red)*
2. A second unparseable shape — blank-line-only — is likewise ignored, so the pin is not keyed to
   zero bytes alone. *(red)* Deliberately **not** "random bytes": those PARSE (latin1 never fails),
   so a garbage-content test would be green today and prove nothing.
3. The unreferenced file is **never opened** — assert on the resolved file SET, not on the run
   surviving. The no-vacuous-greens pairing: (1) alone starts passing for the wrong reason the moment
   Slice 2 lands.
4. A case-mismatched named source resolves. *(red — reached `incomplete_roster` before)*
5. A case collision raises, naming both files. *(red; skipped on a case-insensitive filesystem via
   the directory-entry probe `test_extractor.py` already uses — a glob would not do, being itself
   case-insensitive on Windows. CI's Linux leg runs it.)*
6. **`NO_INPUT` stays reachable** (empty folder) and its twin — a partial folder still runs, so the
   predicate cannot be over-tightened into failing legitimate partial loads.
7. The four narrowed-school-year configs are still exactly those four, and none of their ACTIVE
   entities carries a year-derived field. Two tests, because the first keeps the second honest: if a
   config leaves the at-risk set, the list must shrink rather than silently over-assert.
8. An unreadable required file raises rather than being skipped.

Plus: `test_pipeline_parity.py::_run_ui_path` now calls the real `convert_job`, so the CLI↔UI
byte-parity lock guards production rather than a copy of it.

**Slice 2** — `tests/test_extractor.py::TestAnEmptyExportIsNoRecordsNotAParseFailure` (15 red
before) + two end-to-end cases in `test_convert_input_scoping.py`:

6. Each of the seven empty shapes loads as an empty frame, **and** each logs a WARNING naming the
   file — the diagnostic obligation, asserted on level and filename, because this log line is now
   the only trace of an empty required source.
7. The five shapes that parsed before still parse. Without this the predicate could be widened
   later and nothing would notice.
8. Its positive twin: the `ExtractionError` site is still reachable and still names the file, so
   "empty yields an empty frame" cannot quietly become "anything unreadable does". Asserted by
   forcing the reader to fail rather than by hunting for real unparseable bytes — latin1 never
   raises, so no such byte sequence exists to write.
9. End to end: an empty OPTIONAL export (family contacts) skips its entity and the run succeeds;
   an empty DEMOGRAPHIC export is still refused as `incomplete_roster` and writes nothing. The
   second is the fail-safe — the extractor no longer raises on it, so the way-OUT gate is what
   stops it, and this is the test that notices if that gate ever moves.

Full suite + SD74 snapshot + 20-config validation + ruff/mypy/bandit + tree-check per Definition of
Done, and **CI's own result read and quoted** before either slice is called landed (land gate,
2026-07-30).

## Decomposition (slices)

- **Slice 1 — Convert reads through `load_data`.** Closes the reported fault and findings (1) + (3).
  Small: one substitution, one deletion, docstrings, tests. Self-contained.
- **Slice 2 — empty ≠ unparseable.** Closes finding (2). Independent; own red-first pass.

## Review  _(Stage 3 — adversarial plan-review, 2026-09-22)_

**Verdict: CHANGES REQUIRED** — the diagnosis is right, the direction is right, and the
substitution is *not* the two lines the plan shows. Line refs below are against `431dc40`
unless stated.

**Process note first.** Slice 1 is already **partially implemented in the working tree**
(`src/ui_flet/screens/convert.py` + `src/etl/pipeline.py` modified, `tests/test_convert_input_scoping.py`
untracked) while this plan sits at the Stage-3 gate and the plan file itself is untracked. Two
of the required changes below are already fixed in that diff but absent from the plan. The plan
is the durable artefact the spec is built from — update it to match, commit it, and either
declare that diff as Slice-1-in-progress or park it until Stage 5. An untracked test file in
`tests/` before the approval gate is one `git clean` from gone.

### Required changes

1. **The substitution is three lines, not two — `NO_INPUT` becomes unreachable.**
   `convert.py:302` tests `if not raw_data`. `load_data` inserts a key for EVERY required file,
   empty frame included (`extractor.py:129`), so after the swap the dict is never falsy and the
   status is dead code. The consequence is not cosmetic: an empty/wrong folder falls through to
   `check_delivery_integrity` → `NO_OUTPUT` → `_record_manual_run(status="failed")`, so the
   single most common admin error (wrong folder) flips from *"No files could be read … Check the
   folder and try again"* (`convert_result.py:149-154`, no run record) to *"No output was
   produced … Check that the right district is selected"* (`convert_result.py:156-161`) **plus** a
   failed row in Run History and a red verdict on Home. The existing regression
   `tests/test_pipeline_run_store.py:252 test_no_input_run_records_nothing` goes RED — the affected-files
   table (plan:176) lists only `:616-617` of that file. Fix = the CLI's own predicate
   (`pipeline.py:919`), **single-sourced, not re-spelled** — re-spelling it would recreate the exact
   two-implementations fault this plan exists to delete. *(The working tree's
   `pipeline.has_no_usable_input` is the right shape; put it in the plan, with its two tests —
   empty folder → `NO_INPUT`, partial folder → not `NO_INPUT`.)*

2. **Risk 1 ("Impossible by construction … Risk is nil", plan:185-187) is false as written.**
   There *is* a lookup outside `extract_required_files`: `determine_school_year_detailed` resolves
   `all_data.get(filename)` from `global_config.school_year_sources`
   (`src/etl/transformers/dates.py:257-258`), and `extract_required_files` includes those files
   only when an enabled entity also names them — its own docstring says so (`pipeline.py:131-134`).
   Checked against all 20 shipped configs: **`mbp_core`, `mbponly`, `sd38myedbc` and
   `sd51attendance` name `StudentSchedule.txt` as their school-year source while it is NOT in their
   required set.** So today Convert determines the school year FROM SOURCE for those configs (the
   folder read hands it over) and after Slice 1 it will use the calendar fallback — precisely the
   silent narrowing `extract_required_files`' docstring warns against (`pipeline.py:143-148`, the
   SD83 contract-test incident). I verified no *active* entity in those four uses a year-derived
   field (`use_academic_year` / `append_year_to_id`), so there is **no output delta today** — but
   that is an accident of their `enabled_entities`, not construction. Replace the "nil" claim with
   this statement and pin the accident with a test, so enabling `Classes` on one of those tiers
   can never silently re-key Class IDs.

3. **A locked/unreadable input file becomes an uncaught crash — missing from the table and the
   risks.** `_read_gde_bytes` swallows a per-file `OSError` and continues (`convert.py:502-520`);
   `load_data` reads unguarded (`extractor.py:137`). A demographic export held open by Excel or an
   AV scanner goes from "entity silently skipped, run reports built" to an exception routed to
   `on_error`'s generic card. Fail-loud is the right direction and matches the CLI — but it is a
   new user-visible failure mode on a shipped path, on a screen that has otherwise been given
   bounded categories for every folder fault (`OUTPUT_FOLDER_UNUSABLE`). Add the row, state the
   decision (accept as-is is defensible), and test it.

4. **Slice 2's predicate is neither sound nor complete as stated** ("empty or whitespace-only
   bytes", plan:143-144). Probed against the real extractor:
   - **BOM-only still raises.** `b"\xef\xbb\xbf"` and `b"\xef\xbb\xbf\r\n"` are not
     whitespace to `bytes.strip()`, so the predicate misses them — yet that is exactly "there is
     nothing here", and it is what PowerShell `Out-File` / `Export-Csv -Encoding UTF8` produces for
     an empty export. Strip a leading UTF-8/UTF-16 BOM first, or run the test on the
     `_detect_encoding`-decoded text.
   - **Whitespace-only is on the wrong side of your own framing.** `b"   \n  \n"` does **not**
     raise today — the python sniffer parses it into a 1×4 junk frame (`unnamed: 0…3`), so the
     entity RUNS. Slice 2 would flip that to skipped. Better outcome, but it is not "a raise
     becomes an empty frame" — it is a silent behaviour change on a path all 20 districts share.
     Name it and test it, or narrow the predicate to the raising cases only.
   - Already correct and must stay untouched: `b",,,\n"` and `b"\t\t\n"` already yield 0-row frames.

5. **Slice 2's affected-file line points at a test Slice 1 deletes.** plan:177 says the empty-file
   assertion at `tests/test_extractor.py:291` inverts — that is the `load_from_bytes` twin, which
   disappears with the retirement. The one that must invert is the **disk** test
   `test_empty_file_raises_extraction_error` (`tests/test_extractor.py:69-75`). And add the
   diagnostic obligation: after Slice 2 the load-site log is the ONLY trace of an empty required
   source (today it is an `ExtractionError` naming the file; after, the run walks on to
   `incomplete_roster` / `NO_OUTPUT`, which names the symptom — the same "points nowhere near the
   filename" loss the plan rightly criticises in finding (3)). Require WARNING level, the filename
   in the message, and an assertion on it.

6. **Keep the `load_from_bytes` retirement in Slice 1 — the plan's own question, answered — but
   scope it honestly.** "~14 harness uses" (plan:132) both overstates the work and understates the
   risk. `TestLoadFromBytes` (`tests/test_extractor.py:245-322`) is an 8-test near-duplicate of the
   disk class at `:9-87`; `TestLoadFromBytesEncodingDetection` (`:324-353`) duplicates `:92-179`;
   and `TestDiskBytesParity` (`:356-395`) exists ONLY to prove the two entrypoints agree — it must
   be **deleted**, not "retargeted", or it becomes the tautology the no-vacuous-greens rule bans.
   Two must-dos: (a) enumerate test-by-test which are deleted (has a disk twin) vs migrated (no
   twin — check `:310` headerless injection and `:334` utf8-with-junk), so coverage is not silently
   lost; (b) delete `test_not_supplied_source_is_absent_not_backfilled` (`:300`) **and** the
   contract it pins (`extractor.py:151-155`: "this method must NOT back-fill empty frames") — that
   is the old design's invariant and Slice 1 reverses it. Land the retirement as a **second commit
   inside the slice** so the district-facing fix stays revertible alone (plan:198-199 already asks
   for that granularity).

7. **`tests/test_pipeline_parity.py::_run_ui_path` (`:290-299`) is a THIRD hand-rolled copy of
   `convert_job`.** "Keep it, retargeted" (plan:175) leaves the plan's own thesis — two
   implementations of one job drift — standing inside the test that is supposed to guard it. Either
   point it at `convert_screen.convert_job` (the pattern `tests/test_pipeline_run_store.py` already
   uses) or state in the plan what it still pins after the retarget (loader BOM split + field
   order). Do not keep it as-is without saying which.

8. **"No architecture-tree change" (plan:181) is wrong in substance, and the CHANGELOG non-goal is
   too wide.** `docs/claugentic-ARCHITECTURE_TREE.md:17` describes `extractor.py` as having "two
   public entrypoints … `load_data` (disk) and `load_from_bytes` (in-memory, e.g. browser uploads)"
   — false after Slice 1. The mechanical gate only checks add/move/remove; CLAUDE.md's "keep it
   current" rule covers the description. Separately, `CHANGELOG.md` has a live `## [Unreleased]`
   section, so the case-collision behaviour change the plan itself says "the release note must say
   so" (plan:192-195) belongs in this slice — narrow the non-goal (plan:93-94) to the version bump.

### Sizing / completeness check

- **Slice 1 — OK, no split needed.** Even with items 1-3 and 6-8 folded in it is ~9 files
  (`convert.py`, `extractor.py`, `pipeline.py`, 4 test files, ARCHITECTURE_TREE, CHANGELOG +
  DECISIONS/ROADMAP) and the largest lump — the `load_from_bytes` test churn — is mostly deletion
  of duplicates, not rewriting. One session, lands vertically complete. Two conditions: the
  in-flight working-tree diff is declared and reviewed as part of it (see the process note), and
  `tests/test_convert_input_scoping.py` is named in the plan as the slice's red-first artefact —
  it already contains the `NO_INPUT` pair the plan omits, which is exactly how a fix that lives
  only in code and not in the plan becomes undocumented behaviour.
- **Slice 2 — OK, small, genuinely independent.** Add the BOM case, the whitespace-parses-today
  case, and the disk-test inversion (items 4-5); state the SD74 snapshot expectation explicitly
  (byte-identical — the frozen fixture holds no empty file, so say so rather than implying the
  snapshot proves the predicate).
- **The plan's own reviewer question on the per-absent-file `ERROR File not found` line
  (plan:188-191): leave it as-is.** It is already the CLI's behaviour for the same district on the
  same folder, the UI reads the run store rather than the log for every verdict, and a second
  cosmetic item would be scope with no user. Record the decision; do not open an item.

### Verified sound — do not re-litigate

- The SD67 diagnosis reproduces exactly: `sd67myedbc`'s required set is 8 files and
  `AccidentInformation.txt` is not among them; an empty file yields `sep=None` and the python
  sniffer raises `_csv.Error: Could not determine delimiter` → `_read_with_fallback` returns
  `None` → `extractor.py:195` raises.
- The "absent key vs empty frame is the same downstream" claim holds, including the one place the
  plan's justification does not cover: `sources.get_source_file` branches on key **presence**
  (`src/etl/transformers/sources.py:45`), and both shapes land on an empty frame — only a
  now-obsolete warning line differs. `run_transform` is `.get`-based (`pipeline.py:315`).
- Finding (3) is confirmed from the other side: the chips layer already folds case
  (`convert.py:1512-1516`) with a comment claiming it matches "what the extractor actually does on
  disk" — true of the CLI, false of Convert until this change.
- Deleting rather than filtering is the right call, and the non-goals (three lists, no
  ignored-files chip, preflight blind spot) are correctly drawn.

### Harness impact

- `docs/claugentic-ARCHITECTURE_TREE.md:17` — rewrite the `extractor.py` description (one
  entrypoint). Required by CLAUDE.md, not caught by the gate.
- `CLAUDE.md` — one dense line in the Extractor / Convert entry: *Convert loads
  `extract_required_files(config)` through `load_data`; there is ONE parsing entrypoint.* This is
  a non-obvious project rule a future agent will otherwise undo.
- `docs/claugentic-INVARIANTS.md` (WORKFLOW 9(f)) — record **"three lists, one loader"**:
  `extract_required_files` is what is LOADED, `advisory_expected_files` is UI-only and free to
  narrow, `_present_gde_files` is the folder listing; the advisory list must never become the
  loading list. Plus, with Slice 2, *empty ≠ unparseable* and why the narrowness is the safety
  property.
- `docs/claugentic-DECISIONS.md` + `docs/claugentic-ROADMAP.md` + `CHANGELOG.md [Unreleased]` — as
  amended by item 8.
- No new STANDARD and no new agent. No partner/developer doc describes Convert's folder read, so
  `docs/partner/` needs nothing.
- **One adjacent ROADMAP line (not this plan's bug, but it lives in the function Slice 2 edits):**
  a genuine single-column file is already mangled — `b"Name\nBob\nAmy\n"` loads as two columns
  `['na', 'e']`, because `_detect_delimiter` returns `None` (`extractor.py:268-271`) and the csv
  sniffer then picks `m`. `_detect_delimiter`'s docstring explicitly claims this case is handled.
