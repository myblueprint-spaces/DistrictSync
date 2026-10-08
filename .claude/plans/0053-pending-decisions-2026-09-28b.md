# Pending owner decisions (2026-09-28, second round) — apply to DECISIONS after S13c lands, then build S13d + S14

## Q5 ANSWERED by the owner (SpacesEDU product owner), 2026-09-28 — record as Q5-status: answered in docs/developer/output-contract.md (S14)
- Q5a (rostering file absent): "if missing we cannot generate and send since the generated files will be incomplete and will overwrite what is already there" -> DistrictSync must never send an incomplete set (required-file rule).
- Q5b (header-only file): owner asked for a suggestion; AGREED rule below (empty input file = stop, except attendance/Family; never send a header-only output).
- Q5c (course/attendance feed absent): "nothing changes and nobody is alerted" (SpacesEDU side). But if a required file is missing and the generated ACSV differs from the truth / yesterday's import it overwrites -> cannot allow broken; "an edge case once it's set up ... if they are [missing] it's presumably a problem and we shouldn't ship incomplete data".
- Q5d (CourseInfo + StudentCourses together): depends on the mapping (config types: SpacesEDU rostering = primary; myBlueprint+ mappings with the 2 extra files, authored by Alasdair, docs in Confluence). "If the mapping needs the file it should be there ... some of the important data is concatenated key."
- Q5e (family unlink etc.): handled by per-partner INTERNAL import settings, usually OFF, used sometimes when a partner's data is dirty: "Remove from classes students unenrolled in the imports", "Unenroll teachers absent or unenrolled in the imports", "Remove admins absent from imports", "Update Family Records".
- Consequence: the partner FAQ's "pending confirmation" clause (Family) is replaced by the settings-dependent truth; test_failure_policy_parity's pending-clause pin flips with Q5-status.

## Empty-file rule (owner confirmed "Yes, that's right")
- Missing input file = not in the folder; empty input file = present with only a header row or zero bytes.
- BOTH stop the night, EXCEPT: an empty attendance file (no absences that day) is fine; Family is optional (missing -> Family left out, amber).
- OUTPUT side: DistrictSync never sends a header-only CSV; if a REQUIRED (CRITICAL) output would come out empty (e.g. every row filtered away), the night STOPS rather than sending an incomplete set. (Replaces S8's "EMPTY CRITICAL output = skip + amber".)

## Required VALUES (owner: "Leave out + count + amber")
- Rows missing a value the SpacesEDU Advanced CSV spec REQUIRES are left out, counted, and shown amber (Family's existing no-email rule generalised). Spec: "SIS/LMS Sync (AdvancedCSV) for SpacesEDU - 1.0" (Google Doc 1BePvuk5rg-YjUUvdwjb3X3Z0JWEUc5AtVjDfR3nub0U, modified 2025-11-28).
  - Students required: User ID, Student Number, First Name, Last Name, Grade, Enroll Status, School Code, Email Address. Optional: DOB, Homeroom, PreRegSchoolCode, Preferred names, Community Hours, Literacy Test Completed.
  - Staff: all six required. Family: all four required. Classes: Class ID, Name, School ID required (Grade, Start/End optional). Enrollments: all four required.
  - Cascade: a left-out student/staff must not leave orphans (roster / teacher rows filtered accordingly).
  - Note: the spec covers the 5 rostering files only; course/attendance feeds follow the myBlueprint+ docs (Confluence).
- Spec findings to keep in mind (not decisions): Classes Name "Unknown Course" fallback (S11 recorded note); header spellings Enrollments "School ID"/"User ID" and lowercase Role values work in practice; PreRegSchoolCode spec says "" but DECISIONS 2026-09-01 emits Next school code.

## Security heads-up given to owner
- A Drive search snippet exposed plaintext SFTP logins in the sheet "FTP Accounts (Non-Automated & Automated)" — owner told; not opened/copied.

## Build order
1. S13c (running) — only Family isolatable; missing/empty required input file stops; builder gate; School Number optional; School Year label.
2. S13d — required-value exclusion + count + amber; empty CRITICAL output stops; (and S14 folded in or right after): record Q5 answers, FAQ/help-centre/output-contract updates, parity flip.
3. S15 — CLAUDE.md restructure (D13 approved), LAST.

## Constraint: no outbound alerting / telemetry (owner, 2026-09-28)
- DistrictSync runs on DISTRICT servers; we rely on the district's diligence to check the app (Home/Run History) and logs. Email alerts or telemetry are a FUTURE consideration only — at this stage they are a security risk and some districts' policies forbid them. Record in DECISIONS; ROADMAP item "opt-in alerting/telemetry — deferred (security/partner policy)". Consequence to state in docs: a stopped night is visible only in the app and log; SpacesEDU keeps the last good sync meanwhile (stale, never wrong) — which is WHY stopping beats shipping incomplete data.
