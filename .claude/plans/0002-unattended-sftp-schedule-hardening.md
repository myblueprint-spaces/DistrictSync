# 0002 — Unattended SFTP + Schedule Hardening

- **Status:** Spec'd
- **Roadmap item:** docs/ROADMAP.md → "Unattended scheduled-run reliability"
- **References:** `docs/ARCHITECTURE_TREE.md` · `docs/DECISIONS.md` · `docs/partner/troubleshooting.md`

## Problem
The Setup Wizard promises set-and-forget ("you can close this window; the tool will run automatically" — `src/ui/pages/01_Setup_Wizard.py:680`), but the scheduled task it registers is **not** configured for unattended operation, and SFTP failures are invisible:

1. **Task only runs when the user is logged on.** `register_task` calls `schtasks /Create` with no `/RU` / `/RP` / `/RL` (`src/scheduler/windows.py:112`). Default = "run only when user is logged on." A server reboot with no interactive login → the task never fires, silently. The project's own docs say the user must *manually* re-create the task as "run whether logged on or not" + highest privileges (`docs/partner/troubleshooting.md:45`) — the wizard doesn't do it.
2. **Keyring is per-user.** The SFTP password is stored in Windows Credential Manager under the interactive user (`src/sftp/uploader.py:34`). The run-as account must equal the account that stored it, or `keyring.get_password` returns `None` → upload fails.
3. **SFTP failures are swallowed.** `_sftp_upload` catches everything, logs, returns `False`, and the run still reports success with exit code 0 (`src/etl/pipeline.py:283`). Task Scheduler shows green; nothing is delivered; no signal.

## Goals / Non-goals
- **Goal:** Wizard registers the task to run **as the current Windows user, whether logged on or not, with highest privileges**, collecting that user's Windows password and passing it to `schtasks /RU /RP /RL HIGHEST`.
- **Goal:** A requested SFTP upload that fails makes the process **exit non-zero (code 3)** so Task Scheduler's "Last Run Result" flags it; CSVs are still written.
- **Goal:** Wizard **verifies the SFTP password is readable** by the current account (keyring round-trip) at setup and shows which account the task will run as.
- **Goal:** Replace the overstated wizard success message with accurate copy.
- **Non-goal:** Service accounts / SYSTEM / gMSA. (User chose "same user who runs setup." SYSTEM can't read the per-user keyring.) → ROADMAP LATER.
- **Non-goal:** Machine-scope secret storage, email/SMS alerting. → ROADMAP LATER.
- **Non-goal:** Changing the ETL transform/output. No snapshot changes expected.

## Approach
**Run-as = current interactive user with stored password.** When the wizard registers the schedule, resolve the current user (`%USERDOMAIN%\%USERNAME%`, fallback `getpass.getuser()`), and pass it plus the wizard-collected password to `schtasks /Create … /RU <user> /RP <password> /RL HIGHEST`. Supplying `/RP` is what makes Task Scheduler run the task whether or not the user is logged on (S4U with stored password, so network/SFTP creds are available). We deliberately do **not** pass `/IT` (which would force logged-on-only). Because the task runs as the same account that stored the SFTP password, the per-user keyring stays readable — no secret-storage rework needed.

**Fail loudly on SFTP.** Keep `_sftp_upload`'s try/except (so a network blip doesn't crash mid-pipeline), but propagate `sftp_attempted`/`sftp_ok` to `main.py`, which exits **3** when an upload was attempted and failed, after logging an ERROR. ETL output is untouched (atomic write already committed).

Alternatives rejected: SYSTEM account (can't read per-user keyring — 1 line); `/IT` interactive token (forces logged-on-only, defeats the goal); rolling back CSVs on SFTP failure (conversion succeeded; only delivery failed — keep the files).

## Affected files
- `src/scheduler/windows.py` — `register_task` + `_build_task_command` gain `run_as_user`, `run_as_password`, `run_highest` kwargs; emit `/RU /RP /RL HIGHEST`; **redact password** in any log/return.
- `src/utils/validators.py` — add `validate_run_as_user()` (allow `DOMAIN\user` / `user`; reject shell metacharacters).
- `src/ui/pages/01_Setup_Wizard.py` — schedule step: password field + run-as display, pass through to `register_task`; Step 4: keyring round-trip verification; corrected success message.
- `src/etl/pipeline.py` — ensure `run_pipeline` result exposes `sftp_attempted`/`sftp_ok`; raise the failure log to ERROR.
- `src/main.py` — exit code 3 when `sftp_attempted and not sftp_ok`.
- `tests/` — scheduler flags + password-redaction; pipeline/CLI exit-code; validator.
- Docs: `docs/partner/troubleshooting.md`, `CLAUDE.md`, `docs/ARCHITECTURE_TREE.md`, `docs/DECISIONS.md`, `docs/ROADMAP.md`.

## Risks & mitigations
- **Password leakage** (argv/logs). → Pass as a single `subprocess` arg (no `shell=True`); never log `/RP` value — redact to `/RP ***` in any logged command string; don't persist it (Task Scheduler stores it, DistrictSync doesn't). Add a focused test asserting the password never appears in captured logs.
- **`bandit` flags subprocess/password.** → Keep arg-list form; if a `subprocess` warning needs suppression, add `# nosec` with a one-line justification, not a blanket ignore.
- **Backward compatibility / dev (source) runs.** → If no `run_as_password` is supplied, preserve current behavior (no `/RU`), so existing callers/tests and dev-mode `python -m src.main` scheduling still work. `/RL HIGHEST` only added alongside a run-as user.
- **Wrong Windows password** → `schtasks` returns non-zero; surface the stderr to the wizard so the user can retry (don't claim success).
- **Wizard is UI (coverage-excluded)** → put testable logic (user resolution, redaction) in `windows.py`/`validators.py`, not the page.

## Test strategy
- Scheduler: assert the built command contains `/RU <user> /RP <pw> /RL HIGHEST` when a password is passed; assert **no** `/RU`/`/RP` when omitted (back-compat); assert captured logs contain `***` and never the raw password; validator accepts `CORP\jane` / `jane`, rejects `jane && calc`.
- Pipeline/CLI: sftp-attempted-and-failed → exit 3 **and** CSVs present; sftp success → exit 0; `--sftp` absent → exit 0 unchanged; `--dry-run --sftp` → no upload attempted, exit 0.
- Full suite + 80% gate; ruff, mypy (non-UI), bandit must stay green.

## Decomposition (slices)
- [ ] **Slice 1 — Scheduler run-as** (`windows.py`, `validators.py`, tests). Lands complete: self-contained, defines the `register_task` contract others depend on.
- [ ] **Slice 2 — Fail-loud SFTP** (`pipeline.py`, `main.py`, tests). Lands complete: disjoint from Slice 1; independent exit-code behavior.
- [ ] **Slice 3 — Wizard wiring** (`01_Setup_Wizard.py`). Depends on Slice 1's signature. Lands complete: UI passes new params + keyring check + message.
- [ ] **Slice 4 — Docs + decisions** (orchestrator). troubleshooting/CLAUDE/ARCH_TREE/DECISIONS/ROADMAP.

---

## Spec

### Slice 1 — Scheduler run-as  *(implementer: parallel)*
**`src/scheduler/windows.py`**
- `register_task(...)` and `_build_task_command(...)` gain keyword-only params:
  `run_as_user: str | None = None`, `run_as_password: str | None = None`, `run_highest: bool = True`.
- Add a helper `current_run_as_user() -> str` returning `f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"` when both are set, else `getpass.getuser()`.
- In the `schtasks /Create` arg list: when `run_as_password` is truthy → append `["/RU", run_as_user or current_run_as_user(), "/RP", run_as_password]` and, if `run_highest`, `["/RL", "HIGHEST"]`. When no password → unchanged (no `/RU`/`/RP`/`/RL`), preserving today's behavior.
- Validate `run_as_user` via `validators.validate_run_as_user` before use.
- **Redaction:** any log line or returned echo of the command must replace the `/RP` value with `***`. Provide `_redact(cmd: list[str]) -> str` and use it everywhere the command is logged.
- subprocess: arg-list, no `shell=True`. Surface non-zero `schtasks` stderr to the caller (raise with the stderr text, password already absent from the args we echo).

**`src/utils/validators.py`**
- `validate_run_as_user(user: str) -> str`: strip; allow `[A-Za-z0-9._\\-]` plus a single optional `\` domain separator; max ~256; reject anything with shell metacharacters / whitespace; raise `ValueError` on bad input. Return normalized value.

**Tests** (`tests/test_scheduler*.py` or new `tests/test_scheduler_runas.py`): per Test strategy above. Mock `subprocess.run`. Patch `os.environ` for the user-resolution test.

**Acceptance:** command contains `/RU /RP /RL HIGHEST` with a password; omits them without; password never in logs; validator behaves; existing scheduler tests still pass.

### Slice 2 — Fail-loud SFTP  *(implementer: parallel)*
**`src/etl/pipeline.py`** — `run_pipeline` must return (or already returns) a result carrying `sftp_attempted: bool` and `sftp_ok: bool`. If it returns a dict/dataclass, ensure these are present. On failure, log at **ERROR**: `"SFTP upload FAILED — output files were NOT delivered to <host>"` (keep the existing structured `__DISTRICTSYNC_RUN__` emission with `sftp_ok=false`).
**`src/main.py`** — after `run_pipeline`, if `sftp_attempted and not sftp_ok`: ensure an ERROR was logged and `sys.exit(3)`. ETL success path otherwise exits 0. Do not exit 3 on dry-run (no upload attempted).
**Tests:** monkeypatch the uploader to fail → assert exit 3 and the 5 CSVs exist on disk; success → exit 0; no `--sftp` → exit 0; `--dry-run --sftp` → exit 0, no upload.
**Acceptance:** Task Scheduler "Last Run Result" would be non-zero on a failed delivery; files still written; no regression to the success path.

### Slice 3 — Wizard wiring  *(implementer: after Slice 1)*
**`src/ui/pages/01_Setup_Wizard.py`**
- Schedule step: add `st.text_input("Windows account password", type="password", help="Lets the task run after reboots with no one logged in. Used once to register the task; stored by Windows Task Scheduler, not by DistrictSync.")`. Display the run-as account from `windows.current_run_as_user()`.
- Pass `run_as_user=current, run_as_password=entered` into `_register_schedule` → `register_task`. If the field is blank: register the old way **and** show a visible `st.warning` that the task will only run while that user is logged in. If `schtasks` errors (bad password), show `st.error` with the message — do not show success.
- Step 4 (SFTP): after `store_password`, round-trip `keyring.get_password(KEYRING_SERVICE, username)`; show `st.success("Verified: credentials readable by <user>")` or `st.error(...)`. Add a caption: "SFTP runs use this Windows account's credential store; the scheduled task is configured to run as the same account."
- Replace the success message at ~`:680` with accurate copy: task runs as `<user>` whether logged on or not; reminder to ensure fresh GDE files land before the run time; point to Run History if uploads stop.
**Acceptance:** registering with a password produces an unattended task as the current user; blank password warns; keyring verification visible; copy accurate. (UI is coverage-excluded; keep logic in helpers.)

### Slice 4 — Docs  *(orchestrator)*
- `docs/partner/troubleshooting.md`: the "manually set run whether logged on or not" step is now automated; keep a short "if the task doesn't run, verify the Windows password was correct / re-run setup" note.
- `CLAUDE.md`: scheduler now registers run-as current user + `/RL HIGHEST`; SFTP failure exits 3.
- `docs/ARCHITECTURE_TREE.md`: refresh descriptions for `windows.py`, `pipeline.py`, `main.py`, `01_Setup_Wizard.py`.
- `docs/DECISIONS.md`: run-as interactive user + stored password; SYSTEM rejected (per-user keyring); SFTP failure → exit 3; secrets stay in per-user keyring.
- `docs/ROADMAP.md`: LATER — service-account/machine-scope secrets; alerting on SFTP failure; auto `--sftp-test` at setup.
