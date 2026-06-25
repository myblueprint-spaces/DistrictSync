# Invariants (claugentic harness)

Load-bearing constraints that **must stay true or something breaks**. Each entry
is a non-obvious "must hold" rule that already bit (or would bite) if a future
change "simplified" it. Consult this before changing the named subsystem.

---

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
