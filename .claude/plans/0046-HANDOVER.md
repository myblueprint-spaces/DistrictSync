# Handover — DistrictSync: run the nightly scheduled task as a service account (plan 0046)

You are taking over a piece of work mid-flight. Slice 1 has landed on `main`. Slices 2–4 are
not built. The requirements were just re-evidenced from the owner's email and they **changed
the shape of the problem**, so do not treat the existing plan file as settled.

> **SCOPE DECISION (owner, 2026-09-16): gMSA is OUT until explicitly asked for. Keep it simple.**
> Today's scope is (1) the `0x80070520` diagnosability fix and (2) the service-account
> **username + password** path. §4's combinations 4 and 5, and everything about gMSA in §6, are
> **parked research** — preserved so nobody re-does it, not a build target. Do not design for
> gMSA. Do not widen `validate_run_as_user` for `$`. Do not touch `TASK_LOGON_SERVICE_ACCOUNT`.

Read this whole document before touching anything. Then read
`.claude/plans/0046-service-account-scheduled-task.md` in the repo — it is 429 lines, carries
three adversarial review passes, and its `## Slice 1 implementation notes` and
`## Investigations` sections were written by the previous agent and are current.

---

## 1. Repo, environment, and the things that will waste your time

**Repo:** `C:\Users\shan.peiris\Documents\Integrations\DistrictSync` (Windows, git worktrees in use).
The previous work happened in the worktree
`.claude\worktrees\unity-christian-mapping-edaf9b` on branch `claude/service-account-tasks-0f6140`.
That branch is **merged and its remote is deleted** — start a fresh branch off `origin/main`.

**`CLAUDE.md` is authoritative** and it is long. It is loaded into every session. Read it. The
harness process lives in `docs/claugentic-WORKFLOW.md`; the standards bar in
`docs/claugentic-ENGINEERING_STANDARDS.md`; the code index in
`docs/claugentic-ARCHITECTURE_TREE.md` — **use the tree to locate files, do not explore blindly.**

**Gotchas that cost the previous agent real time:**

- **There is no `make` on this machine.** `CLAUDE.md` and the docs tell you to run
  `make validate-config`; it does not exist. Copy the inline command out of the `Makefile`.
- **A worktree has no `.venv`.** Use the main checkout's interpreter explicitly:
  `C:/Users/shan.peiris/Documents/Integrations/DistrictSync/.venv/Scripts/python.exe`
  A bare `python` resolves to a *different* system Python — note that the system one has
  `pywin32` installed and the venv one does **not**, which matters for any live COM probe.
- **The full test suite takes ~15 minutes** (5741 tests). Background it. A backgrounded pytest
  writes **nothing** to its output file until the process exits — a 0-byte file means "running",
  not "broken". Do not poll it.
- **CI takes ~18 minutes** on this repo even for small PRs. `gh pr checks <N> --watch` exceeds
  the foreground tool timeout — background it and read the output file.
- **`gh` quirks that produce a FALSE GREEN:** `--fail-level` is not a flag on the installed `gh`
  (it prints usage and exits non-zero, which `| tail` masks as 0). `gh run list --commit <sha>`
  returns nothing here. Report `${PIPESTATUS[0]}`, and confirm you actually see
  `test  pass  <duration>`.
- **Line endings:** `.gitattributes` enforces `eol=lf`. Python's `write_text` translates `\n` to
  CRLF on Windows — if you edit files with a script, write bytes or normalise afterwards, or you
  will produce a whole-file diff.
- **Escaping:** writing backslashes through tool-call JSON halves them. If you generate Python or
  Markdown containing `\` via a heredoc, verify what actually landed in the file.
- **`bandit` must be run as `bandit -r src/ -q -c pyproject.toml`** — the bare form false-fails.
  Note a constant *named* `..._PASSWORD` trips B105 on the name alone; the repo's convention is an
  inline `# nosec B105` with a reason (see `main.SFTP_PASSWORD_ENV_VAR`).
- **`python scripts/check_no_emails.py` is a CI gate that scans every tracked file for plaintext
  email addresses.** Do not put district staff email addresses into any file in this repo —
  including plans, docs and DECISIONS. First names and SD numbers only.
- **`DISTRICTSYNC_DATA_DIR`**: never run the app or CLI locally without it. A bare run writes into
  the real user profile's `etl_tool.log` and `history.db`.

**Local gate set** (all must pass; CI runs the same):

```
<venv>/python.exe -m pytest tests/ -q --cov=src --cov-fail-under=80
<venv>/python.exe -m ruff check src/ tests/
<venv>/python.exe -m ruff format --check src/ tests/
<venv>/python.exe -m mypy src/ --exclude 'src/ui_flet'
<venv>/python.exe -m bandit -r src/ -q -c pyproject.toml
<venv>/python.exe scripts/claugentic-check_architecture_tree.py
<venv>/python.exe scripts/check_no_emails.py
# plus the Makefile's validate-config command, inline
```

**Land gate (owner decision, non-negotiable):** a slice is not landed until **CI's own result has
been read and quoted**. A local green is not a green — the local gates run on Windows only and a
Linux-only failure once sat on `main` for three pushes. **The agent does not merge**: `main`
requires an approving review and `gh pr merge` would need `--admin`. Report "land gate met" with
the quoted check line and leave the merge to the owner **unless the owner explicitly authorises
the bypass in that session** (they did so once, for PR #117 — that authorisation was
session-specific, do not assume it carries).

---

## 2. What has already landed

**PR #117 — "fix(scheduler): refuse a different run-as account that arrives without a password"**
— merged to `main` as `8e1f24e` (commit `07ccaa5`). CI: `test pass 17m43s`, quoted.

This was **Slice 1 (engine correctness)** and it fixed a latent defect, not a feature:

`register_task` resolved the principal with `has_password = bool(run_as_password)` and, on the
no-password branch, read `user = current_run_as_user()` — silently **discarding** an explicitly
supplied `run_as_user` and returning `(True, "Schedule registered.")`. A task registered to the
wrong identity, reported as success. Separately, `windows.py` read `""` as *absent* while
`task_com.apply_definition` reads `password is not None` as *unattended*, so a blank string could
register `TASK_LOGON_PASSWORD` + RunLevel Highest with a blank credential from a non-elevated
process.

What shipped:

| requested account | password | behaviour |
|---|---|---|
| a DIFFERENT account | yes | registers to that account (validated) |
| a DIFFERENT account | no / `""` | **refused** — `_MSG_ACCOUNT_NEEDS_PASSWORD`, before any COM call or UAC prompt |
| absent / the current account | yes | unchanged from today |
| absent / the current account | no | unchanged from today |

Also: `run_as_password or None` normalised once at entry; `elevated_apply._do_register` now
re-validates `user` unconditionally and normalises identically; `validators.py` docstrings
corrected (they still described the PowerShell `-User` transport retired at plan 0041 S1b).

**A deliberate deviation from the plan's spec is recorded** in `docs/claugentic-DECISIONS.md`
(2026-09-15) and the plan's implementation notes — read it, because it constrains what you do
next. Short version: the spec said never validate the machine-derived fallback on either branch;
what shipped validates the caller-CHOSEN account always and keeps today's behaviour for the
fallback on each branch. Applied literally the spec would have created a direct-vs-elevated
divergence.

**16 new test cases**, 7 of which fail against the pre-fix engine (mutation-checked). Suite at
merge: 5741 passed, 96.11% coverage.

### ⚠️ The Slice 1 refusal interacts directly with gMSA support

If gMSA turns out to require *"foreign account + NULL password + `TASK_LOGON_PASSWORD`"* — which
is the design question below — then Slice 1's refusal blocks exactly that call. **This is not a
bug in Slice 1.** It is the correct outcome of "make the unsafe call unrepresentable": a
passwordless *ordinary* account must still be refused. gMSA must become an **explicit, separate
principal kind** that the caller states, never a blank-password fallthrough. Design accordingly.

---

## 3. The requirement evidence — read this before designing anything

Gathered 2026-09-15/16 from the owner's email, last ~3 weeks. **Two districts asked for service
accounts, not three.** (The owner's own message to SD60 on 2026-09-14 says "2 districts have
requested for it".)

### SD54 Bulkley Valley — Ted (MyEd BC custodian), 2026-08-17 — service account, no error

> "I have set up the sync now, one minor thing I would like to see changed is I have to run the
> task under my name, I have set up a service account to do things like this so I don't have my
> account running tasks. If that could be added to future releases that would be great."

He already has the account. **A typed username + password serves him completely.** He said nothing
about gMSA, domain vs local, batch-logon rights, or whether the account has a password. Setup
otherwise succeeded; he framed this as "minor".

### SD60 Peace River North — Jarrod (Director of Instruction), 2026-09-11 and 2026-09-14 — BOTH a service-account ask AND a hard blocker

The ask (2026-09-11):

> "For the nightly schedule, is it possible to run as a service, I've forgotten the term, rather
> than using my account/password. Our security team made changes to avoid that kind of scheduled
> task with user login credentials this summer."

The error (2026-09-14):

> "Enabling the nightly sync fails to create the schedule change. I suspect it may be a security
> setting on our side."

After trying elevation (2026-09-14):

> "No luck on the schedule. I ran the app as an administrator and it gives the same error. I
> didn't see anything informative in the log but I can send that to you if you'd like."

**Why this district is the crux of the design:** his constraint is a ban on scheduled tasks *with
user login credentials*. A Schedule step that asks him to type a username and password produces
exactly that. And "run as a service, **I've forgotten the term**" is someone reaching for a word
he cannot name — in an Entra/AD district that has just banned credential-bearing scheduled tasks,
the most likely word is **gMSA**, which plan 0046 excludes outright (N1).

**No district used the words gMSA, managed service account, virtual account, SYSTEM, or "no
password."** The above is inference from his stated constraint, not evidence. **One reply to
Jarrod settles it** and the owner has not sent one yet. Also unclaimed: **he offered his log a day
ago and nobody took him up on it.**

**LIKELY ROOT CAUSE FOUND — verify before building anything for him.** Microsoft's Platforms
Support team documents that Task Scheduler "uses Windows Credential Manager to store the
credentials of the account that is specified to perform a task", and that with the GPO
**"Network access: Do not allow storage of passwords and credentials for network authentication"**
enabled, task registration fails with **"A specified logon session does not exist"
(`0x80070520`)**.
<https://learn.microsoft.com/en-us/archive/blogs/supportingwindows/task-scheduler-error-a-specified-logon-session-does-not-exist>

That single hypothesis explains **all three** of Jarrod's observations at once: his security team
"made changes to avoid that kind of scheduled task with user login credentials this summer" (that
GPO is exactly how a security team enforces such a ban); registration fails; and **running as
administrator changes nothing**, because it is not a privilege problem. And
`0x80070520` is **NOT in `task_com._HRESULT_CANONICAL`** (verified — it appears nowhere in
`src/`), so it degrades to Windows' own cryptic text with no actionable guidance — which is
precisely why he "didn't see anything informative".

**If this is confirmed, the password path is IMPOSSIBLE at SD60, not merely unwelcome** — and gMSA
becomes their only route. Confirm by asking him for the exact error text / the log he offered.

**His registration failure survived running as administrator**, so it is *not* the elevation path.
It is either the service-account blocker or an independent registration defect. Treat it as a
separate triage item with its own slice — and note that "I didn't see anything informative in the
log" is **our** defect: a failed schedule registration should leave an actionable diagnostic.

### SD51 Boundary — Kim (Administrative Assistant) — error reports, NONE of them ours

- Missing Enrollments/Classes: upstream data — she later confirmed elementary schools had not
  scheduled students yet.
- Truncated zip (30–32 KB) and `kex_exchange_identification: read: Connection timed out` on port
  22: diagnosed in-thread as her district firewall blocking SSH. Client-side.
- She had **not** set up the scheduled task as of 2026-09-03.

**There is currently nothing SD51-shaped to build or ship.** Their attendance fix
(`2b15da3`) is already live in **v3.19.0** — verified: the commit is an ancestor of the tag, the
release is published with all binaries. If the owner asks again about "SD51's updates not making
it into a release", the answer is that v3.18.1 *was* mis-tagged (its tag points at v3.18.0's
commit, so its binaries lack the fix) and **v3.19.0 already corrected it**.

### The SFTP / Credential Manager risk — predicted, NOT reported

No district has reported delivery failing after a scheduled run — **because nobody has
successfully run the task under a different account yet**, so the failure has had no opportunity
to appear. Treat it as a risk to design around, not a symptom to reproduce.

---

## 4. The open design question the owner has already answered in principle

The owner's direction (2026-09-16):

> "regardless of what Jarrod wants can't we be compatible with both because we want adoption of
> this tool and we'll likely encounter partners who want all the combinations at some point."

So the target is a **principal model**, not a password field. The combinations:

1. current user, logged-on only — `TASK_LOGON_INTERACTIVE_TOKEN`, no credential *(ships today)*
2. current user, unattended — `TASK_LOGON_PASSWORD` + their password *(ships today)*
3. another account + password — *(plan 0046 Slice 2, engine half already landed)*
4. ~~**gMSA** — no password~~ **PARKED** (owner, 2026-09-16 — see the banner at the top)
5. ~~**SYSTEM / LOCAL SERVICE / NETWORK SERVICE**~~ **PARKED** — nobody has asked for these at all

The engine change that makes this representable is **small and worth doing regardless**: today
`task_com.apply_definition` infers the logon type from `password is not None`. That inference is
the root of both Slice 1 defects. Replace it with an **explicit principal kind / logon type** on
`RegisterParams`, decided by the caller, and the "make the unsafe call unrepresentable" rule in
`CLAUDE.md` is satisfied structurally for all five.

### ⚠️ The trap: this is not a UI problem, it is a secrets problem

For combinations 4 and 5 **the app's existing SFTP credential story does not work, and the
documented workaround does not exist**:

- The SFTP password lives in the **per-user Windows Credential Manager** (`keyring`).
- The agreed workaround for combination 3 (owner decision: option A, documented, not
  auto-provisioned) is to run `DistrictSync --sftp-configure` once **as** the service account via
  `runas`.
- **A gMSA cannot log on interactively at all**, so that workaround is impossible for it. Same
  problem for SYSTEM.
- Therefore supporting 4/5 *with SFTP delivery* requires **machine-scope secret storage** — the
  item plan 0046 defers as N2 and its "Open fork" option 2 (a DACL'd DPAPI-LocalMachine blob under
  `%ProgramData%\DistrictSync`, read by `_sftp_upload` when the keyring is empty).

**And there is now a second, sharper reason the password path may not be optional-vs-preferred but
simply IMPOSSIBLE at some districts:** the GPO in §5(b) makes `TASK_LOGON_PASSWORD` registration
fail outright. At such a district, combinations 2 and 3 are both unavailable and only 4/5 remain —
which is exactly the district (SD60) whose secret storage we have no answer for.

**Shipping "gMSA supported" without solving that would give a district a nightly sync that rosters
successfully and silently never delivers — behind a green banner.** That is precisely the failure
class this codebase repeatedly designs against. If you ship 4/5 before the secret work, it must be
gated honestly (e.g. only offered when SFTP delivery is off) rather than offered and quietly
broken.

---

## 5. Verified facts — do not re-derive these

**Spike: does `TASK_CREATE_OR_UPDATE` replace the PRINCIPAL of an existing task?**
Measured live against real Task Scheduler COM on the owner's Windows 11 host, throwaway task,
deleted and verified gone:

| step | registered as | read back `Principal.UserId` / `LogonType` / action args |
|---|---|---|
| create | `DESKTOP-…\shan.peiris`, interactive token | `shan.peiris` / 3 / `/c exit 0` |
| update, same name | `shan.peiris` (bare), interactive token | `shan.peiris` / 3 / **`/c exit 1`** |
| update, same name | `NT AUTHORITY\SYSTEM`, logon type 5 | **failed `0x80070005` access denied** (not elevated) |

**Established:** `TASK_CREATE_OR_UPDATE` replaces the **whole definition** (the action args
changed under an existing name), and `UserId` is re-derived from the `userId` argument on every
call (the qualified `DOMAIN\user` came back normalised to the bare local name).
**Not established:** both registrations named the same account in two spellings; a genuinely
different principal was refused for lack of elevation, not lack of support.

**Consequence if it confirms:** plan 0046's A10 delete-then-create step, its second UAC prompt and
the entire "no schedule at all" tear window (A10.3, N10) become unnecessary *as a mechanism*, and
rest solely on the EDR argument. **That is an owner decision, not a measurement** — note that the
owner's rationale ("delete and create avoids malware tripwires") and the plan's A10 rationale
("in-place re-pointing IS the T1053.005 hijack signature") reach the same conclusion from opposite
premises.

**`TASK_LOGON_TYPE` enum — CONFIRMED locally**, read out of `C:\Windows\System32	askschd.dll`'s
type library on the owner's machine via `pythoncom.LoadTypeLib` (authoritative, no web source
needed):

```
TASK_LOGON_NONE                          = 0
TASK_LOGON_PASSWORD                      = 1
TASK_LOGON_S4U                           = 2
TASK_LOGON_INTERACTIVE_TOKEN             = 3
TASK_LOGON_GROUP                         = 4
TASK_LOGON_SERVICE_ACCOUNT               = 5
TASK_LOGON_INTERACTIVE_TOKEN_OR_PASSWORD = 6
```

So `task_com.py`'s three constants are correct, and the S4U value it deliberately refuses to
define really is `2`. Anything a web source says that contradicts this list is wrong.

### Research findings (2026-09-16) — confidence labelled, do not re-derive

**(a) Credential Manager has NO cross-user scope. Documented and DECISIVE.**
Both persistence levels are per-user: `CRED_PERSIST_LOCAL_MACHINE` is "visible to other logon
sessions of **this same user**", `CRED_PERSIST_ENTERPRISE` "…for **this user** on other computers".
<https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw>
`keyring`'s Windows backend writes `CRED_TYPE_GENERIC` with `Persist = CRED_PERSIST_ENTERPRISE`
(confirmed by reading `keyring/backends/Windows.py`).

**Consequence:** a secret the admin stores interactively is **structurally invisible** to a task
running as `SVC_X`. There is no machine-scope flag that fixes this. So the `runas
--sftp-configure` workaround is **NECESSARY, not optional**, for combination 3 — and since a gMSA
cannot log on interactively at all, combinations 4/5 need a genuinely different secret mechanism.

**(b) A GPO can make `TASK_LOGON_PASSWORD` registration IMPOSSIBLE. Documented, and now
CORROBORATED BY TWO INDEPENDENT RESEARCH PATHS.**

The policy **"Network access: Do not allow storage of passwords and credentials for network
authentication"** (registry `HKLM\SYSTEM\CurrentControlSet\Control\Lsa\DisableDomainCreds`) blocks Task
Scheduler from saving the task password at registration. Both research passes landed on the same
failure, reported in different notations — **verified identical**:

```
0x80070520 == 2147943712 == ERROR_NO_SUCH_LOGON_SESSION (Win32 1312)
```

**`0x80070520` is unmapped in `_HRESULT_CANONICAL`** (verified absent from all of `src/`), so it
degrades to Windows' own cryptic "A specified logon session does not exist."

**The compounding failure that makes this worse than it looks:** when the GPO blocks password
storage, Task Scheduler pushes the admin onto **"Do not store password"** — which is **S4U**. And
S4U is documented to have **no stored password and no access to encrypted files**, i.e. **no usable
DPAPI master key** — so the SFTP credential becomes unreadable too. A district that works around
the GPO that way gets a task that registers, runs, and cannot deliver. **Our error message should
steer them away from that fallback, not just name the policy.**

**(c) SYSTEM / LOCAL SERVICE / NETWORK SERVICE. Documented.** Logon type
`TASK_LOGON_SERVICE_ACCOUNT` (5); the password "must be an empty VARIANT value such as `VT_NULL`
or `VT_EMPTY`". Documented trap: registration returns `80070534` when called by the System account
with *user* NULL, *password* NULL and *logonType* `TASK_LOGON_SERVICE_ACCOUNT`.
<https://learn.microsoft.com/en-us/windows/win32/api/taskschd/nf-taskschd-itaskfolder-registertaskdefinition>
Elevation is required to register these (the spike measured `0x80070005` unelevated).

**(d) Account-name character set. Documented.** The sAMAccountName disallowed set is
`" / \ [ ] : ; | = , + * ? < >`, max 20 chars for the name part.
<https://learn.microsoft.com/en-us/windows/win32/adschema/a-samaccountname>
So `$` **is** legal — and note **space is legal too**, which `_RUN_AS_USER_RE` currently rejects.
Since the value reaches COM as a BSTR (no shell, no argv), an allow-everything-except-that-set
check is defensible if the regex must be widened.

**(e) The only documented machine-wide secret primitive** is DPAPI `CRYPTPROTECT_LOCAL_MACHINE`:
"**Any user** on the computer … can use CryptUnprotectData to decrypt the data" — so it must carry
its own restrictive DACL. There is no Credential Manager equivalent.

**Other established facts:**

- The only caller of `register_task` is `screens/setup.py:2154`, which passes `run_as_user=None`.
  All 20 bundled districts are therefore byte-identical after Slice 1.
- `_run_as_account()` (`screens/setup.py:2469`) serves the **keyring owner** concept and is
  rendered at `screens/setup.py:2603` as *"Your delivery password is saved and readable by
  `<account>`."* **A naive sweep that repointed it at the task principal would print the exact
  inverse of the truth** — a false all-clear on the most likely real failure. Rename it to
  `_keyring_owner_account` BEFORE any principal wiring, and keep line 2603 on a "must NOT change"
  list.
- `TASK_LOGON_S4U` (2) is deliberately not even defined in `task_com.py` — S4U has no network
  token, which breaks the SFTP egress. **This ban must survive any redesign.**
- `RegisteredSchedule` (`setup_flow.py:526`) holds `args` + `unattended`, written and cleared
  atomically by every confirmed register/unregister. The principal belongs **here**, not in
  `TaskArgs` (which is documented as "fields baked into the action").

---

## 6. Unverified assumptions that could invalidate the design

**S0 — EFFECTIVELY ANSWERED (research, 2026-09-16): YES, with three named traps.**

The CROSS-ACCOUNT half is settled and negative (§5a): the admin's own credential can never be read
by another principal. The SAME-ACCOUNT half — can a batch-logon task read the credential that
account stored itself? — is now **Strongly implied: YES**, from a fully documented chain:

- **`CRED_PERSIST_LOCAL_MACHINE` / `CRED_PERSIST_ENTERPRISE` are visible "to other logon sessions of
  this same user on this same computer"** — **Documented**, verbatim from
  <https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw>.
  `keyring` writes `CRED_PERSIST_ENTERPRISE`. **This is what validates the `runas
  --sftp-configure` workaround.**
- "Runs a scheduled task or **batch job**" is explicitly listed as creating an LSA session **with
  stored credentials** — **Documented**,
  <https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/hh994565(v=ws.11)>
- `CredWrite` binds to "the logon session of the current token", and the ONLY session class
  documented as lacking a credential set is **network** logon — batch is not excluded.
  **Documented**, <https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew>
- Task Scheduler's own docs contrast the password path — "**unconstrained use** of the resulting
  token" — against S4U. **Documented**,
  <https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-R2-and-2008/cc722152(v=ws.11)>

**No single Microsoft sentence states "DPAPI CurrentUser works under a batch logon."** The chain is
documented; the conclusion is assembled. Treat as Strongly implied, not a Microsoft ruling.

**THE THREE TRAPS — design for these, they are the realistic failure modes:**

1. **First-ever DPAPI use by a domain account on that machine needs a reachable writable DC.** The
   master key is created lazily on first encrypt and backed up to an RWDC; if the backup fails the
   key is not created and you get **`0x80090345`**. **Documented**, KB 3205778
   <https://learn.microsoft.com/en-us/troubleshoot/windows-server/certificates-and-public-key-infrastructure-pki/dpapi-masterkey-backup-failures>
   The same KB lists "Opening Credential Manager fails with 0x80090345" as symptom #1 — Credential
   Manager is downstream of the master key.
2. **The profile-load race.** Task Scheduler loads the profile ASYNCHRONOUSLY; Microsoft's own KB
   says it "may not be fully loaded" when the task's first line runs, and `%USERPROFILE%` can
   resolve to `C:@BS@Users@BS@Default`. **Documented**, KB 2968540
   <https://learn.microsoft.com/en-us/troubleshoot/windows-server/system-management-components/scheduled-tasks-reference-incorrect-user-profile>
   Microsoft's own workaround is to warm the profile with a prior process — which is an argument for
   the one-time `runas` step being a **hardening step, not just a convenience**.
3. **The temporary-profile fallback.** A hotfixed bug class (Event ID 1511) landed batch tasks on a
   TEMPORARY profile for accounts with no local profile yet. In that state DPAPI data written
   earlier is unreachable and data written in that run is **silently discarded at unload** — the
   worst possible failure mode for a credential-reading nightly.

**Still worth measuring**, because all of the above is inference and a red result is decisive:

**gMSA — PARKED (owner decision, 2026-09-16). Research preserved so it is not re-done.**

If it is ever asked for, these are the findings, with the confidence they were established at:

- **Use `TASK_LOGON_PASSWORD` (1)** with `userId = "DOMAIN\gmsa$"` and a NULL/empty password.
  *Community-sourced but consistent across four independent write-ups, and the only claim the
  serialization behaviour supports.*
- **Do NOT use `TASK_LOGON_SERVICE_ACCOUNT` (5).** **VERIFIED FIRST-HAND** on the owner's machine
  via an in-memory `Schedule.Service` round trip: with a domain-style `UserId`, setting LogonType 5
  causes the `<LogonType>` element to be **silently dropped** from the serialized XML (values 1, 2
  and 3 all survive). The XML schema confirms it — `logonType` permits only `S4U`, `Password`,
  `InteractiveToken`, `InteractiveTokenOrPassword`.
  Community reports say the resulting task degrades to "run only when user is logged on"; a local
  attempt to reproduce THAT half **failed on a malformed test template and did not establish it**.
- **gMSA is NOT S4U** — the host retrieves the real password from AD, so it gets genuine network
  credentials. *Strongly implied from documented mechanism.*
- **Task Scheduler is a documented gMSA consumer** (one bullet on Manage-gMSAs), but **no Microsoft
  page documents how to register such a task** — every concrete recipe is community-authored.
- Prerequisites: host must be in `PrincipalsAllowedToRetrieveManagedPassword` (**documented,
  mandatory**); grant BOTH "log on as a batch job" and "log on as a service" (contested, costs
  nothing); `Install-ADServiceAccount` on the host.
- **Needs a live domain-joined test before shipping.** Neither the owner's Windows 11 Home machine
  nor CI can verify it.

**Useful regardless of gMSA:** SSH does **not** use Windows authentication — DistrictSync's SFTP
upload authenticates inside the SSH protocol. The S4U "no access to the network" wording means no
*Windows-authenticated* resource access, not no TCP sockets. So the network-token risk to OUR
delivery path is much smaller than it first appeared.

**`validate_run_as_user` rejects `$` — LEAVE IT THAT WAY.** `$` matters only for gMSA, which is
parked. Do not widen a security-boundary regex for a case nobody is building. (For the record if it
ever returns: the authoritative constraint is the sAMAccountName disallowed set, and the value
reaches COM as a BSTR with no shell or argv, so this is a shape check rather than an injection
defence.)

---

## 7. What to do with the existing plan

`.claude/plans/0046-service-account-scheduled-task.md` is **still the best starting point** — three
adversarial passes, a measured premise correction, and a spec — but it now needs revision:

- **N1 (no gMSA/SYSTEM) is contradicted by the owner's "compatible with both" direction.** Reopen
  it as a principal model.
- **N2 (no machine-scope secret storage) is load-bearing again** — it is the blocker for 4/5.
- **The Open fork's S0 and the CREATE_OR_UPDATE spike are partly resolved** (see §5) — fold the
  results in.
- **A5, A6, A7, A9 survive unchanged** and are genuinely good: the permanent-false-amber fix, the
  keyring-owner split, principal-aware error coaching, the seasonal-window limitation.
- **A new slice candidate that is not in the plan at all, and it is the HIGHEST-VALUE ITEM HERE:**
  map `0x80070520` (and audit the rest of the HRESULT surface) to an actionable message naming the
  GPO, plus the diagnosability gap SD60 reported ("nothing informative in the log"). See §3 and
  §5(b).

  Why it leads: it is **small**, it is **independent of the whole service-account feature**, it
  converts a dead-end error into a district-actionable one, and it addresses a **live blocker at a
  real district today**. It is also the fastest way to confirm or kill the SD60 root-cause
  hypothesis. Consider shipping it FIRST, before the principal model.

- **Re-scope N2 (machine-scope secrets).** §5(a) makes it structural rather than optional for
  combinations 4/5: Credential Manager has no cross-user scope, and a gMSA cannot log on
  interactively to seed its own. Either machine-scope secret storage lands, or 4/5 must be gated to
  installs with SFTP delivery OFF, honestly and visibly.

Run it through `docs/claugentic-WORKFLOW.md` properly: triage → plan → adversarial review → spec →
**owner approval** → implement → verify → land. The owner asked explicitly for
"plan and review and spec the plan and build it and review and iterate and test and make it
professionally implemented and robust."

---

## 8. Decisions that belong to the owner, not to you

1. **Does the release wait for this work?** The owner's sequencing was: fix the district-reported
   things → release → inform the districts.
2. **Do we ship combinations 4/5 gated (SFTP off only), or hold them until machine-scope secrets
   land?** This is a product call about what "supported" means.
3. **Delete-then-create vs in-place principal replacement**, given the spike (§5).
4. **Running S0 on the owner's machine** — needs their approval and their UAC clicks.
5. **Sending anything to a district.** Two useful emails are unsent: asking Jarrod for his exact
   error + log, and asking whether his policy permits a password-bearing service account or needs
   gMSA. **Do not send email from the owner's account without an explicit instruction in your own
   session.**
6. **PR #116 (`feat(sd10): class, enrollment and family rostering on the full tier`) is Alasdair's
   and is OFF LIMITS** — owner: "we don't know if he wants to merge it yet, he hasn't added me as
   reviewer." Do not merge, rebase, or comment on it. It is green and blocked only on review.

---

## 9. What still needs verifying (SCOPE-REDUCED — gMSA is parked)

Answered, do not re-research: the `TASK_LOGON_TYPE` enum (§5), Credential Manager's lack of
cross-user scope (§5a), the GPO failure mode (§5b), SYSTEM/LOCAL SERVICE logon type and password
(§5c), the account-name character set (§5d), the machine-wide secret primitive (§5e), and the
parked gMSA findings (§6).

**In scope, and worth settling:**

1. **Confirm the SD60 root cause** — get Jarrod's exact error text and the log he offered. If it is
   `0x80070520`, the GPO hypothesis in §3 is confirmed and it shapes both the fix and what we tell
   him. This is the cheapest, highest-value evidence available and it is **one email**.
2. **S0 — can a batch-logon task read its OWN account's Credential Manager?** Strongly implied yes
   (§6), never measured. It decides whether the `runas --sftp-configure` workaround actually
   delivers. Needs a throwaway local account + elevated registration + the owner's UAC clicks, and
   the experiment must be designed around the trap plan 0046 names (the interactive `runas` that
   seeds the credential creates the very profile under test).

**Out of scope until asked:** anything gMSA — its network token, its registration parameters, the
`$` in the account-name regex.

## 12. THE WORK PLAN — formalised 2026-09-16. Build this, in this order.

Scope is **two features**, not five. gMSA is parked (banner at top). Slice A is independent of
everything else and should ship first.

### SLICE A — Schedule-failure diagnosability  [DO THIS FIRST]

**Why first:** small, independently shippable, touches no UI state machine, and it addresses a
**live blocker at SD60 today**. It is also the fastest way to confirm or kill the GPO hypothesis —
once the message names the cause, the district tells you the cause.

**IMPORTANT HONESTY CONSTRAINT:** these fixes make the failure *legible*. If the GPO is the cause,
**no app change makes SD60's sync work** — the district must grant a policy exception, or we need
the credential-free path that is parked. Do not let the copy, the CHANGELOG or the district email
imply otherwise.

**Four defects, all verified in the code on 2026-09-16:**

| # | Defect | Evidence |
|---|---|---|
| A1 | **`0x80070520` (ERROR_NO_SUCH_LOGON_SESSION) is unmapped.** Falls to Windows' own text, then to the classifier's generic branch, which says **"Try again in a moment"** — advice that can never work for a GPO. | Verified absent from all of `src/`; `_canonical_message` fallback at `task_com.py:285`; generic branch at the end of `setup_errors.classify_schedule_error` |
| A2 | **`SCHED_E_ACCOUNT_INFORMATION_NOT_SET` (`0x8004130F`) is misdiagnosed as a bad password** — it shares the canonical string `"The user name or password is incorrect."` with `ERROR_LOGON_FAILURE`. It actually means *the task has no account information set*, the expected failure when EDITING an existing task whose stored account info is unreadable. An admin retypes a correct password forever. | `task_com._HRESULT_CANONICAL`; **the conflation is PINNED** by `tests/test_task_com.py:91`, which parametrises both HRESULTs to that one string — changing it is a deliberate contract change, not a bug fix, and that test must move with it |
| A3 | **`classify_schedule_error` has NO credential branch.** `"The user name or password is incorrect."` matches nothing and lands in the generic fallback, so even a correctly-identified wrong password produces "try again in a moment". The only credential coaching in the app sits inside the `access_denied and elevated` branch, unreachable unless the error is access-denied. | Traced through `setup_errors.py`; pinned by `tests/test_ui_flet_setup_errors.py:61-65`, which asserts the generic "(Details: ...)" output for exactly that input |
| A4 | **The failure does not reach the log usefully.** SD60: *"I didn't see anything informative in the log but I can send that to you if you'd like."* | District report, 2026-09-14 |

**Acceptance criteria:**

1. `0x80070520` maps to a canonical, secret-free string; `setup_errors` keys off it by exact
   equality (the house pattern) and produces copy that **names the policy** ("Network access: Do
   not allow storage of passwords and credentials for network authentication"), says plainly that
   it is a **domain policy, not a DistrictSync problem**, and **explicitly warns against the "Do
   not store password" checkbox** — that is S4U, documented to have no network access and no
   DPAPI, so it would register successfully and then fail every delivery. **Do not say "try
   again".**
2. `SCHED_E_ACCOUNT_INFORMATION_NOT_SET` is split from `ERROR_LOGON_FAILURE` with its own honest
   copy about the task's stored account information, aimed at the EDIT-an-existing-task case.
   `tests/test_task_com.py:91` updated deliberately, with the reason in the test docstring.
3. A genuine credential failure gets credential coaching **without** needing an access-denied
   error — a branch keyed on the `ERROR_LOGON_FAILURE` canonical string.
4. The generic fallback stops promising that retrying helps when the cause is permanent, while
   retaining a support path.
5. Every registration failure logs the **HRESULT** (`0x%08X`) beside the canonical message, so a
   district's log answers "why" without a round trip. **No secret may enter that log line** — the
   existing non-leak tests must be EXTENDED, not merely left passing.
6. **Plan 0046's A7 rides along here:** `setup_errors.py:107` coaches *"make sure you entered
   **your** Windows account password (not your Windows Hello PIN — for a Microsoft Account, your
   microsoft.com password)"*. Against a service account every clause is wrong, and it actively
   coaches a **personal cloud credential** into a service-account field.

**Do NOT** invent an error taxonomy beyond the HRESULTs actually evidenced. Map what is observed.

### SLICE B — The service account: username + password

Plan 0046's Slice 2, minus gMSA. Serves SD54 completely. The engine half landed in #117; this is
UI, state and honest reporting. Read plan 0046's `## Spec` -> "Slice 2" and §11 of this document
before starting. Easy to get wrong:

- **`_run_as_account` -> `_keyring_owner_account` rename FIRST**, before any wiring (§5).
- The principal goes on `RegisteredSchedule`, **not** `TaskArgs` (§5).
- The prefilled field must send the **typed** value — a request naming the current account
  case-insensitively is not a principal change, and that is what makes prefill safe.
- `register_task` **raises** `ValueError` for a malformed account; the UI must catch or pre-gate.
- The `runas --sftp-configure` step is **necessary, not optional** (§5a), and it doubles as the
  profile-warming step Microsoft recommends (§6, trap 2). Document it as ONE action.

### SLICE C — Signal honesty

Plan 0046's A5 + A9, unchanged and still correct. Once the nightly runs as another account its run
records land in **that** profile's `history.db`, so two existing predicates fire **every night,
forever**: `schedule_status._is_contradiction` (which walks the admin back into re-registering) and
`home_status._is_missed_run`. This is a regression the feature CREATES; it is not optional. Plus
the seasonal-window limitation (A9): surface it, do not solve it.

### SLICE D — Docs, DECISIONS, partner guide

Extend `docs/partner/headless-sftp-setup.md` (do not add a file). DECISIONS: the GPO finding, the
Credential Manager cross-user fact, the gMSA parking decision and why. Narrow the ROADMAP item to
gMSA / machine-scope secrets.

### Explicitly NOT in scope

gMSA · SYSTEM / LOCAL SERVICE / NETWORK SERVICE · machine-scope secret storage · widening
`validate_run_as_user` · an in-app "run it now" button (plan 0046 N9/A4, already cut with reasons).

---

## 10. Definition of done (from `CLAUDE.md`, non-negotiable)

A slice may land only when **all** hold: acceptance criteria met · in-scope
`ENGINEERING_STANDARDS` dimensions pass an architect-reviewer audit · all gates green (tests +
SD74 snapshot + tree-check + lint/type/security + config validation) · **no new tech debt** ·
**CI's own result read and quoted**. Iterate to that fixed bar, then stop. Genuinely separate work
goes to `docs/claugentic-ROADMAP.md` as backlog, not debt.

Two house rules that bite here specifically:

- **No vacuous greens.** An "X was not created/changed" assertion needs a positive twin proving the
  mechanism works at all. The previous agent mutation-checked every new test by restoring `HEAD`'s
  copy of the module and re-running — do the same.
- **Fail loudly.** Never swallow an exception to hide a config/column mismatch, and never add a
  permissive default on a safety-relevant parameter. The whole of Slice 1 exists because one
  defaulted parameter silently substituted a security principal.

---

## 11. Carried-forward items from Slice 1 (not debt, but do not lose them)

1. `setup_errors.classify_schedule_error` has **no branch** for `_MSG_ACCOUNT_NEEDS_PASSWORD` — it
   falls through to the generic "didn't go through (Details: …)" copy. Unreachable from the UI
   today; the real copy is plan 0046's A7.
2. A request naming the **current** account case-insensitively is **not** a principal change. That
   is what makes a prefilled account field safe — but the field must send the typed value, not a
   sanitised one, or the comparison is bypassed.
3. `register_task` still **raises** `ValueError` (it does not return `(False, msg)`) for a malformed
   account. `screens/setup.py`'s `_register` has no `except ValueError` — the UI must catch it or
   gate on shape first.
4. `setup_flow.py:776-787`'s `_FOLDERS_SAVED_BLOCKED` / `_SFTP_RECONCILE_BLOCKED` hardcode "fix the
   run time" as the only reason a reconcile can be blocked. A missing service-account password is a
   second reason; that copy must name it or it will misdirect.
