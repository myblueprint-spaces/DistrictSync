# Invariants (claugentic harness)

Load-bearing constraints that **must stay true or something breaks**. Each entry
is a non-obvious "must hold" rule that already bit (or would bite) if a future
change "simplified" it. Consult this before changing the named subsystem.

---

- **The idle-watchdog's reach into Streamlit internals is pinned + isolated — re-verify on any streamlit bump.** _(Plan 0011, 2026-06-25 · `src/ui/lifecycle.py`.)_
  The graceful-shutdown watchdog reads the live browser-session count via the
  **private** `streamlit.runtime.get_instance()._session_mgr.num_active_sessions()`
  (verified on **streamlit 1.54.0**), and the single-instance guard keys off the
  `/_stcore/health` body **== `ok`**. These are unstable Streamlit internals, so the
  dependency is **pinned `streamlit>=1.54,<1.55`** (`requirements.txt` +
  `pyproject.toml [ui]`/`[dev]`; `requirements-dev.txt` inherits via `-r`), the
  private reach is isolated to one helper (`lifecycle._active_session_count`), and it
  **degrades to a logged no-op** if the internal shape changes (never crashes the UI
  — the Exit button is the manual fallback). On any streamlit upgrade past 1.54,
  re-verify all three: `_session_mgr.num_active_sessions()`, the `/_stcore/health`
  body, and that `already_running`'s transitive `requests` dep is still bundled
  (`--collect-all streamlit`). Don't widen the pin without re-checking.

- **Unattended Windows scheduling requires a stored-password logon (`LogonType=Password`), NEVER `S4U`.** _(Plan 0009, 2026-06-25 · `src/scheduler/windows.py`.)_
  The daily scheduled run that uploads via SFTP must run **whether or not the
  setup user is logged on** AND must have a **network token** (to reach the
  SpacesEDU SFTP host). Only a stored-credential logon
  (`New-ScheduledTaskPrincipal -LogonType Password` + `Register-ScheduledTask
  -User -Password`) provides both. `S4U` runs logged-off **without** storing a
  password, but it has **no network token** — the task would run yet silently
  fail to deliver. `S4U` (and the loose `-User/-Password/-RunLevel`
  parameter-set inference that can degrade to it) is therefore **rejected by
  design**; the explicit `-LogonType Password` principal is the **documented way
  to force** `TASK_LOGON_PASSWORD` (rather than rely on parameter-set
  inference). **Proof-it-took (pending user verification):** the registered task
  must query as `LogonType = Password` / `RunLevel = Highest`, and a logged-off
  run must reach SFTP. Do not "simplify" the principal to S4U or rely on
  parameter-set inference.
