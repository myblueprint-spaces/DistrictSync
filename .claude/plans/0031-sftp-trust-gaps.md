# 0031 — SFTP trust gaps: Test/delivery parity + deliver gate + fail-loud empty upload

- **Status:** Approved (user answered the 3 product questions 2026-07-15) — implementing
- **Blockers:** none
- **References:** field report (user, 2026-07-15): Convert → Deliver said "sent" while Setup → Test connection said "failed". `src/sftp/uploader.py` · `src/ui_flet/screens/convert.py` · `src/ui_flet/sftp_copy.py` · `src/ui_flet/humanize.py` (`_SFTP_REASON_RULES`)

## Diagnosis (code-confirmed; local forensics: this machine has NO manual-run/upload trace — user's session was on another machine/account)
1. Convert's DELIVERED banner cannot be produced without a genuine authenticated upload (`upload_csvs` failure → BUILT_NOT_DELIVERED). The "sent" claim was real.
2. Setup's Test asserts `sftp.listdir(remote_path)` — a capability delivery (`sftp.put`) does not need. Upload-only accounts (SpacesEDU-style) deny listing → **Test fails while delivery works**. paramiko maps SFTP_PERMISSION_DENIED → `IOError(errno.EACCES)` ⇒ `PermissionError`; SFTP_NO_SUCH_FILE → `FileNotFoundError` (distinct: bad path IS a delivery problem).
3. The Convert deliver card is gated on `sftp_is_configured()` (config fields only) — never checks a stored credential exists.
4. `upload_csvs` returns `[]` silently when the output dir has no CSVs → callers mark `sftp_ok=True` → false "delivered" (unreachable from Convert, reachable from CLI/scheduled misconfig).

## User decisions (2026-07-15)
- **No pre-flight** on Deliver — keep deliver-then-report (honest exit-3 verdict after attempt).
- **No stored credential → block + route to Setup** (single credential home; no transient-password entry in Convert).
- **Test parity: auth is the test** — signed-in but listing-denied = success-with-note.

## Changes
1. **`src/sftp/uploader.py`**
   - `test_connection`: split the listdir probe from the connect. Auth/connect failure → `(False, msg)` unchanged. `listdir` `PermissionError` → `(True, LISTING_DENIED_NOTE)` (module-level canonical constant, FIXED string, no host/path interpolation). `FileNotFoundError`/other listdir errors → `(False, ...)` unchanged (a missing remote path breaks `put` too).
   - `upload_csvs`: no CSVs found → `raise RuntimeError("No CSV files found to upload in <dir name only>")` instead of silent `[]` (fail-loud; pipeline exit-3 + Convert BUILT_NOT_DELIVERED become the honest outcomes).
2. **`src/ui_flet/sftp_copy.py`** — `sftp_test_copy(..., listing_denied: bool = False)`: appends a fixed note ("This account can't list the remote folder — that's normal for upload-only delivery accounts.") to the success detail. Pure, tested.
3. **`src/ui_flet/screens/setup.py`** — `_show_result` computes `listing_denied = ok and msg == LISTING_DENIED_NOTE` and threads it to `sftp_test_copy`.
4. **`src/ui_flet/screens/convert.py`** — deliver-card gate: configured AND `SFTPUploader(...).get_stored_password()` non-empty. Configured-but-no-credential → calm info card ("Delivery is set up, but no password is stored on this account — add it in Setup") instead of the deliver button. Never blocks the local build.
5. **`src/main.py`** `--sftp-test` — no signature change; prints the note and exits 0 on listing-denied (correct: delivery will work).
6. **Keep** `humanize._SFTP_REASON_RULES` untouched (permission needle still valid for connect-level failures).

## Tests
- uploader: listing-denied → `(True, NOTE)`; missing remote path → failure; auth failure unchanged; no-CSVs → raises (flip `test_upload_no_csv_files`).
- sftp_copy: note appended for `listing_denied=True` (stored + typed provenance), absent otherwise.
- exit-contract (`test_sftp_exit`): `--sftp` with empty output → exit 3.
- CLI (`test_sftp_cli`): `--sftp-test` listing-denied path exits 0 with note.

## Docs
- `docs/claugentic-DECISIONS.md` dated entry (auth-is-the-test rationale; deliver-gate; declined pre-flight + transient-password; fail-loud empty upload).
- `docs/claugentic-ARCHITECTURE_TREE.md` — uploader/sftp_copy/convert lines.
- CHANGELOG deferred until PR #52 (changelog rollup) lands — avoid conflicting edits.

## Verification
Full suite + ruff + mypy + bandit + validate-config; no snapshot impact expected (ETL untouched).
