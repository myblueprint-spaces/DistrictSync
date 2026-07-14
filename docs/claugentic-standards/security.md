<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
---
# ── Module contract: every docs/claugentic-standards/ module copies this frontmatter ──
module: security
title: Security & Privacy
version: 0.1.0
status: draft
iso_25010: [security]
load_scope:
  keywords: [auth, authz, login, token, password, secret, crypto, session, encryption, pii, injection, csrf, ssrf, cors, dependency]
  globs: ["**/auth/**", "**/*login*", "**/middleware/**", "**/*.env*", "**/security/**"]
last_reviewed: 2026-06-04
---

# Security & Privacy — keep untrusted input, attackers, and regulators from turning code into a breach

> **Loads when:** a change touches authentication/authorization, sessions/tokens, secrets or crypto, input handling or query/command construction, cross-origin/redirect/outbound-fetch behavior, deserialization, dependencies, or any personal/regulated data (PII, student, health, payment).
> **ISO/IEC 25010:** Security (confidentiality, integrity, non-repudiation, accountability, authenticity) · **Status:** draft · **v0.1.0**

Each entry below is one **auditable dimension**. Per change, the reviewer applies the
*relevant* ones **fully** (select-don't-skip), right-sized to the change — never
gold-plating an irrelevant one, never skipping a relevant one. Security dimensions are
**threat-driven**: relevance is set by what the change exposes (a new endpoint, a new
sink, a new data field), not by how big the diff is. A one-line change can be in scope.

---

## Authentication (proving who the caller is)

- **Good looks like —** Credentials verified server-side against a vetted identity provider or library; passwords stored with a memory-hard, salted KDF (Argon2id / scrypt / bcrypt — **never** MD5/SHA-1/unsalted/fast hashes); minimum length enforced (≥12, target ≥15 for single-factor) with **no arbitrary composition rules** and a breached-password blocklist check; MFA available for sensitive accounts; generic failure messages ("invalid credentials") that don't reveal whether the username exists; brute-force throttling / lockout / rate-limiting on login + password-reset; secure password-reset (single-use, short-TTL, side-effect-free tokens); no credentials in the URL, query string, or logs.
- **Auditor checks —** `[D]` grep for weak hash calls (`md5`, `sha1(`, `hashlib.sha256(password`), hardcoded credential literals, and passwords logged. `[D]` a dependency/secrets scan flags committed credentials. `[J]` is authN delegated to a vetted lib/IdP rather than hand-rolled? `[J]` are reset tokens single-use + expiring? `[J]` is the failure path uniform (no user-enumeration via message or timing)? `[J]` is throttling present on auth endpoints?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Strong, standard login makes account takeover hard; rolling your own or skipping rate-limits is the single fastest way to get users' accounts stolen. The cost is using a proven library and a few extra checks, not bespoke crypto.
- **Sources —** OWASP ASVS 5.0 V6 (Authentication) · OWASP Top 10:2021 A07 (Identification & Authentication Failures) · NIST SP 800-63B-4 §3.1.1 (Passwords — length, no composition rules, breached-password blocklist) · OWASP Authentication & Password Storage Cheat Sheets · CWE-287, CWE-916.

## Authorization, least privilege & object-level access (IDOR / BOLA)

- **Good looks like —** Every protected action and **every object access** is authorized server-side on each request — deny-by-default, fail-closed. Object/resource ownership is checked against the authenticated principal (no "trust the ID in the request" — the classic IDOR/BOLA hole); access decisions never rely on a hidden field, client-supplied role, or sequential ID being unguessable. Roles/scopes are least-privilege (the minimum needed); privileged operations re-verify; horizontal (peer's data) and vertical (admin functions) escalation are both blocked; multi-tenant queries are always tenant-scoped.
- **Auditor checks —** `[J]` for each new endpoint/handler, is there an authZ check *and* an ownership/tenant check before the resource is read or mutated? `[J]` can changing an `id` in the request reach another user's/tenant's row? `[D]` grep for routes/handlers added without passing through the auth middleware/decorator (where the framework makes that greppable). `[J]` are roles least-privilege, not "admin for convenience"? `[J]` are authZ checks server-side (not just hidden UI)?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Checking "is this person allowed to touch *this specific* record" on every request is what stops one user from reading everyone's data by editing a number in the URL. Skipping it is invisible in testing and catastrophic in production.
- **Sources —** OWASP Top 10:2021 A01 (Broken Access Control) · OWASP API Security Top 10:2023 API1 (BOLA) / API5 (BFLA) · OWASP ASVS 5.0 V8 (Authorization) · CWE-639, CWE-285, CWE-862, CWE-863.

## Session & token management

- **Good looks like —** Session IDs / tokens are high-entropy, server-issued, and rotated on privilege change (login, step-up); cookies are `HttpOnly`, `Secure`, and `SameSite` (Lax/Strict); idle + absolute session timeouts; logout and password-change **invalidate** server-side sessions/refresh tokens. Self-contained tokens (JWT/PASETO) verify signature with a pinned algorithm (reject `alg:none` and algorithm-confusion), validate `exp`/`nbf`/`iss`/`aud`, are short-lived with a revocable refresh path, and carry no secrets in the (base64, **not** encrypted) payload. No token in the URL; no long-lived bearer token in `localStorage` for high-value sessions.
- **Auditor checks —** `[D]` grep cookie set-calls for missing `HttpOnly`/`Secure`/`SameSite`; grep JWT verify calls for unpinned/`none` algorithms or skipped verification. `[J]` are sessions rotated on login and killed on logout/password-change? `[J]` are token claims (`exp`,`aud`,`iss`) actually validated, not just decoded? `[J]` is token lifetime short with revocation possible?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Proper session handling means a stolen or expired token stops working and logging out actually logs you out. Getting it wrong lets an attacker ride a valid session indefinitely or forge their own access tokens.
- **Sources —** OWASP ASVS 5.0 V7 (Session Management) / V9 (Self-Contained Tokens) · OWASP Session Management & JWT Cheat Sheets · CWE-384 (Session Fixation), CWE-613 (Insufficient Session Expiration), CWE-347 (Improper Signature Verification).

## Secrets management

- **Good looks like —** **No secret in source, history, config committed to VCS, container images, or logs.** Secrets come from a vault / secret manager / KMS or, at minimum, injected env vars; they are referenced, not embedded. `.env*` and key material are git-ignored; pre-commit / CI secret-scanning is wired; leaked secrets are **rotated**, not just deleted from the tip. Distinct secrets per environment; least-privilege scopes; rotation is supported (no "forever" credentials).
- **Auditor checks —** `[D]` secret-scanner (gitleaks / trufflehog / detect-secrets) over the diff **and** history is clean; `.gitignore` covers `.env*`/keys. `[D]` grep for high-entropy strings, `AKIA`, `-----BEGIN ... PRIVATE KEY-----`, `password=`, `api_key=` in tracked files. `[J]` are secrets sourced from a manager/env rather than literals? `[J]` is rotation possible and per-env separation real?
- **Confidence —** `deterministic`
- **Tradeoff (plain English) —** Keeping secrets out of the repo means one leaked clone doesn't hand over your database and third-party accounts. The cost is a secret manager and a scanner in CI; the cost of skipping it is a public-key-on-GitHub incident.
- **Sources —** OWASP Top 10:2021 A05 (Security Misconfiguration) / A02 (Cryptographic Failures — exposed secrets/keys) · OWASP Secrets Management Cheat Sheet · NIST SP 800-57 (key management) · CWE-798 (Hardcoded Credentials), CWE-312 (Cleartext Storage), CWE-532 (Info Exposure via Logs).

## Input validation at trust boundaries

- **Good looks like —** Every input crossing a trust boundary (HTTP params/body/headers, file uploads, message-queue payloads, third-party responses) is validated **server-side** against an explicit schema — type, range, length, format, allowlist of permitted values — **positive (allowlist) validation, not deny-list blocking.** Invalid input is rejected (fail-closed) with a generic error, not coerced silently. Validation is centralized at the boundary (the harness rule: *validate at boundaries, trust internal code*); canonicalize before validating; client-side checks are UX only and re-checked on the server. File uploads validate type/size and store outside the web root with non-executable, generated names.
- **Auditor checks —** `[J]` is there a schema/validator at each new boundary, and is it allowlist-based? `[D]` where a schema lib is used (pydantic/zod/JSON-Schema/bean-validation), grep confirms new DTOs/endpoints are typed/validated rather than reading raw `request.body`/`params` directly. `[J]` is invalid input rejected (not silently defaulted/truncated)? `[J]` are uploads constrained and stored safely?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Validating input at the door is the foundation every other defense stands on; it stops malformed and malicious data before it reaches a database, a shell, or another user's screen. The cost is one schema per boundary — cheap, and it doubles as documentation.
- **Sources —** OWASP ASVS 5.0 V2 (Validation & Business Logic) · OWASP Input Validation Cheat Sheet · CWE-20 (Improper Input Validation), CWE-434 (Unrestricted Upload).

## Injection prevention (SQL/NoSQL · command · path · XSS · template/LDAP)

- **Good looks like —** Code is **separated from data** at every interpreter boundary: parameterized queries / prepared statements / ORM bindings for all DB access (**never** string-concatenated SQL/NoSQL); no shell-string concatenation — use argument arrays / native APIs, never `os.system`/`shell=True` with user input; path access is confined to a base dir with canonicalization (no `../` traversal); **output is contextually encoded** (HTML/attr/JS/URL) and untrusted HTML sanitized with a vetted library — XSS defense is output-encoding, with CSP as defense-in-depth; templates auto-escape and aren't fed user-controlled template source (SSTI); LDAP/XML/header values are escaped/validated.
- **Auditor checks —** `[D]` grep for the danger sinks: f-string/`+`/`%`/`.format` building SQL, `os.system`/`subprocess(... shell=True)`/`eval`/`exec` with variable input, `innerHTML`/`dangerouslySetInnerHTML`/`v-html`, `Path`/`open` joined to raw user input. `[D]` SAST (semgrep / CodeQL / Bandit) on the diff shows no injection findings. `[J]` for each flagged sink, is the input trusted or properly parameterized/encoded/sanitized? `[J]` is encoding correct for the *output context*?
- **Confidence —** `deterministic`
- **Tradeoff (plain English) —** Keeping user input from being executed as a query, a command, or a script is what stops data theft, server takeover, and one user attacking another through your page. Parameterized queries and output-encoding cost nothing extra and are non-negotiable.
- **Sources —** OWASP Top 10:2021 A03 (Injection) · OWASP ASVS 5.0 V1 (Encoding & Sanitization) / V2 (Validation & Business Logic) · OWASP SQLi, XSS Prevention, Command Injection & OS Command Injection Defense Cheat Sheets · CWE-89, CWE-78, CWE-79, CWE-22, CWE-94, CWE-643.

## CSRF, CORS, clickjacking & cross-origin posture

- **Good looks like —** State-changing requests on cookie-authenticated apps are CSRF-protected (synchronizer/double-submit token **and** `SameSite` cookies — SameSite alone is not sufficient); safe methods stay side-effect-free. CORS is explicit and minimal — a **specific allowlist of origins**, never reflecting `Origin` or `Access-Control-Allow-Origin: *` together with `Allow-Credentials: true`. Clickjacking is blocked via `frame-ancestors` CSP (and/or `X-Frame-Options`). Security headers are set (CSP, HSTS, `X-Content-Type-Options: nosniff`, `Referrer-Policy`). Token-/header-authenticated APIs (no ambient cookies) document why CSRF tokens are unnecessary.
- **Auditor checks —** `[D]` grep CORS config for wildcard origin, `Origin` reflection, or `*` + credentials; grep for missing CSP/HSTS/frame-ancestors where a server config exists. `[J]` do new state-changing routes have CSRF protection (or a justified exemption for header-auth APIs)? `[J]` is the CORS origin list specific and intentional?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** These controls stop a malicious site from making your logged-in users perform actions or read your data without their knowledge. Misconfiguring CORS to `*`-with-credentials silently hands your API to any website on the internet.
- **Sources —** OWASP CSRF Prevention, CORS, Clickjacking Defense & HTTP Security Response Headers Cheat Sheets · OWASP ASVS 5.0 V3 (Web Frontend Security) · CWE-352 (CSRF), CWE-942 (Permissive CORS), CWE-1021 (Clickjacking).

## SSRF & outbound-request safety

- **Good looks like —** Any server-side fetch to a user-influenced URL (webhooks, link previews, image/PDF fetch, import-from-URL, federated calls) is constrained: **allowlist** the permitted hosts/schemes (deny-by-default), block private/loopback/link-local/metadata ranges (`127.0.0.0/8`, `169.254.169.254`, `10/172.16/192.168`, IPv6 ULA/`::1`), validate the resolved IP **after** DNS (defend DNS-rebinding), disable or constrain redirects, and never echo raw upstream responses. Cloud metadata endpoints are unreachable from the app; egress is firewalled where possible.
- **Auditor checks —** `[J]` does any new code fetch a URL derived from user input? If so, is there an allowlist + private-range block + redirect control? `[D]` grep for HTTP clients (`requests.get`, `fetch(`, `urllib`, `httpx`) taking a variable URL near request input. `[J]` is the post-resolution IP validated (rebinding-safe), not just the string?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Stopping the server from fetching attacker-chosen internal URLs prevents it being used as a proxy to reach your private network and cloud credentials — a top cause of cloud breaches. The cost is an allowlist on outbound calls.
- **Sources —** OWASP Top 10:2021 A10 (SSRF) · OWASP SSRF Prevention Cheat Sheet · CWE-918 (SSRF).

## Safe deserialization & dynamic execution

- **Good looks like —** Untrusted bytes are never fed to an unsafe deserializer that can instantiate arbitrary types or run code (Python `pickle`/`yaml.load`, Java native serialization, PHP `unserialize`, .NET `BinaryFormatter`, JS `node-serialize`). Prefer data-only formats (JSON) with **schema validation**; if a richer format is required, use safe modes (`yaml.safe_load`), type allowlists, signed/integrity-checked payloads, and sandboxing. No `eval`/`exec`/dynamic `import`/template-from-string on untrusted input; no untrusted classpath/plugin loading.
- **Auditor checks —** `[D]` grep for `pickle.load`, `yaml.load(` without `SafeLoader`, `eval(`, `exec(`, `BinaryFormatter`, `unserialize(`, `node-serialize` on any externally-sourced data. `[J]` is the deserialized data's origin trusted/signed, or is a safe loader + schema in place?
- **Confidence —** `deterministic`
- **Tradeoff (plain English) —** Unsafe deserialization lets an attacker turn a crafted payload into code execution on your server — full compromise. Using JSON-plus-schema or a safe loader removes the class of bug entirely for a negligible cost.
- **Sources —** OWASP Top 10:2021 A08 (Software & Data Integrity Failures) · OWASP Deserialization Cheat Sheet · CWE-502 (Deserialization of Untrusted Data), CWE-94 (Code Injection).

## Dependency & supply-chain hygiene

- **Good looks like —** Dependencies are **pinned** (lockfile committed) and sourced from trusted registries; an automated vulnerability scanner (e.g. Dependabot / `npm audit` / `pip-audit` / OSV / Trivy / Snyk) runs in CI and blocks known-critical CVEs; transitive deps are visible; unused/abandoned packages are pruned (typosquat / dependency-confusion awareness). Build/CI is reproducible; where required, artifacts are integrity-verified (hashes / signatures / SBOM / SLSA provenance). New dependencies are justified — license-checked and not adding an outsized attack surface for a trivial need.
- **Auditor checks —** `[D]` lockfile present and updated with the change; dependency-audit gate is green (no unaddressed critical/high). `[D]` CI runs an SCA scan. `[J]` is each newly-added dependency justified (real need, maintained, reasonable footprint, acceptable license)? `[J]` are pins exact, not floating `latest`?
- **Confidence —** `deterministic`
- **Tradeoff (plain English) —** Pinning and scanning dependencies means you don't unknowingly ship a library with a published, exploitable hole — the cause of many of the largest breaches. The cost is a lockfile and a CI scan; the savings is not being the next supply-chain headline.
- **Sources —** OWASP Top 10:2021 A06 (Vulnerable & Outdated Components) / A08 (Software & Data Integrity) · OWASP Dependency-Check (https://owasp.org/www-project-dependency-check/) · OWASP SCVS — Software Component Verification Standard (https://scvs.owasp.org/) · SLSA framework (https://slsa.dev/) · CWE-1104 (Use of Unmaintained Components), CWE-1357.

## Secure defaults & misconfiguration

- **Good looks like —** Ship locked-down by default: debug/verbose errors and stack traces **off** in production (generic error responses, no internal detail leaked to clients); default/sample credentials removed; admin/management/actuator endpoints disabled or authenticated; directory listing and unnecessary services off; TLS enforced (HTTPS-only, HSTS, modern ciphers); least-privilege OS/container/cloud roles (non-root, read-only FS where feasible); a deny-by-default network/permission posture; framework security features left **on**, not disabled "to make it work." Security-relevant config is environment-specific and reviewed.
- **Auditor checks —** `[D]` grep for `DEBUG = True`, `app.run(debug=True)`, stack-trace-to-client handlers, disabled CSRF (`CSRF_ENABLED=False` or equivalent), `chmod 777`, container `USER root`. `[J]` are errors generic to the client while detailed server-side? `[J]` are admin endpoints protected and unused features off? `[J]` is TLS enforced end-to-end? (Disabled-TLS-verification grep — `verify=False`/`rejectUnauthorized:false` — see Cryptography correctness dimension.)
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Secure defaults mean a forgotten setting doesn't become an open door, and an error message doesn't hand attackers a map of your system. The cost is resisting "just turn the check off to ship"; the benefit is the most common breach category (misconfiguration) doesn't apply to you.
- **Sources —** OWASP Top 10:2021 A05 (Security Misconfiguration) · OWASP ASVS 5.0 V13 (Configuration) · CIS Benchmarks · NIST SP 800-123 · CWE-16, CWE-209 (Error-Message Info Leak), CWE-756.

## Cryptography correctness

- **Good looks like —** Use **vetted, current** primitives via standard libraries — AES-GCM/ChaCha20-Poly1305 (authenticated) for symmetric, TLS 1.2+ for transport, SHA-256+ for hashing, a password KDF for passwords; **no home-grown crypto, no ECB mode, no static/zero IVs, no MD5/SHA-1/DES/RC4** for security purposes. Randomness for tokens/keys/IVs comes from a CSPRNG (`secrets`, `crypto.randomBytes`, `SecureRandom`), never `Math.random`/`rand()`. Keys are managed (generated, stored, rotated via KMS/keyring — not in code), correctly sized, and separated by purpose; constant-time comparison for secrets/MACs; certificate validation is **never** disabled.
- **Auditor checks —** `[D]` grep for `MD5`/`SHA1`/`DES`/`RC4`/`ECB`, `Math.random()` used for security tokens, `verify=False`/`rejectUnauthorized:false`/disabled cert checks, hardcoded keys/IVs. `[J]` is an authenticated cipher used and is the construction standard (right mode, fresh IV, CSPRNG)? `[J]` are keys managed and comparisons constant-time?
- **Confidence —** `deterministic`
- **Tradeoff (plain English) —** Using standard, modern crypto correctly keeps data unreadable and untampered even if intercepted; inventing your own or using broken ciphers gives a false sense of safety that collapses under a real attacker. The cost is calling the right library function.
- **Sources —** OWASP Top 10:2021 A02 (Cryptographic Failures) · OWASP ASVS 5.0 V11 (Cryptography) / V12 (Secure Communication) · OWASP Cryptographic Storage & Transport Layer Security Cheat Sheets · NIST SP 800-175B / FIPS 140-3 · CWE-327 (Broken Crypto), CWE-330 (Insufficient Randomness), CWE-295 (Improper Cert Validation).

## PII minimization & data classification

- **Good looks like —** Personal data is **classified** (what's PII / sensitive / regulated) and **minimized** — collect only what's needed for the stated purpose, retain only as long as needed, and avoid spreading copies. Sensitive fields are masked/tokenized/redacted in UIs, exports, and analytics; data is anonymized or pseudonymized where the use case allows; the most sensitive categories (health, biometric, government ID, full payment data, children's data) get the strictest handling and aren't collected casually. New fields capturing personal data are deliberate, documented, and lawful-basis-aware.
- **Auditor checks —** `[J]` does the change collect/store/transmit new personal data, and is each field actually needed for the feature? `[J]` is sensitive data minimized, masked where displayed/exported, and not duplicated into logs/caches/analytics? `[J]` is the most-sensitive data avoided unless essential and specially handled?
- **Confidence —** `judgment`
- **Tradeoff (plain English) —** Collecting and keeping less personal data shrinks both the harm of a breach and your regulatory exposure — you can't leak what you didn't store. The cost is saying no to "collect it just in case"; the benefit is smaller breach blast-radius and easier compliance.
- **Sources —** GDPR Art. 5 (data minimization, storage limitation) · NIST SP 800-122 (PII protection) · OWASP ASVS 5.0 V14 (Data Protection) · OWASP Privacy Risks (Top 10) · CWE-359 (Exposure of Private Information).

## Encryption in transit & at rest

- **Good looks like —** All personal/sensitive data is encrypted **in transit** (TLS 1.2+, HSTS, no plaintext HTTP for sensitive flows, valid certs) and **at rest** (database/disk/object-store encryption; field-level or envelope encryption for the most sensitive columns; encrypted backups). Keys are managed via KMS and rotated; the most regulated data (payment, health) follows its mandate (e.g. PCI: never store full PAN/CVV unprotected). Sensitive data isn't cached in plaintext where it shouldn't be (browser caches, CDNs, temp files) and is wiped from memory/temp when feasible.
- **Auditor checks —** `[D]` grep for `http://` to sensitive endpoints, disabled TLS verification, sensitive columns/files written without encryption where the platform expects it. `[J]` is at-rest encryption enabled for the new data store/field and are backups covered? `[J]` are keys managed/rotated (not embedded), and is the most-regulated data handled per its standard?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Encrypting data on the wire and on disk means a stolen laptop, backup, or intercepted connection yields gibberish instead of records. The cost is enabling platform encryption and managing keys; skipping it turns any physical or network breach into a full data loss.
- **Sources —** OWASP Top 10:2021 A02 (Cryptographic Failures) · OWASP ASVS 5.0 V11 (Cryptography) / V12 (Secure Communication) / V14 (Data Protection) · NIST SP 800-111 (storage encryption) · PCI DSS v4.0 Req. 3 & 4 · CWE-311 (Missing Encryption), CWE-319 (Cleartext Transmission).

## Retention, deletion & data-subject rights

- **Good looks like —** Each data category has a **defined retention period** and is purged when it expires (automated where possible) — including from logs, backups, caches, search indexes, and analytics, not just the primary table. Deletion/erasure ("right to be forgotten"), access/portability (export), and rectification requests are supported and propagate to all stores and downstream processors; "soft delete" of regulated data has a real hard-delete path. Third-party processors are bound by contract and their deletion is triggered too.
- **Auditor checks —** `[J]` does new persisted data have a retention/deletion story, or is it written somewhere it can never be removed? `[J]` does an erasure request reach this data everywhere it lands (replicas, backups, logs, indexes, third parties)? `[J]` is there a hard-delete path, not only soft-delete, for regulated data?
- **Confidence —** `judgment`
- **Tradeoff (plain English) —** Being able to delete data and not keeping it forever is both a legal right you owe users and a way to limit how much a breach can expose. The cost is building deletion/export paths; skipping it means unbounded liability and unlawful indefinite retention.
- **Sources —** GDPR Art. 5(1)(e) (storage limitation) & Arts. 15–20 (access, erasure, portability) · NIST SP 800-88 (media sanitization) · OWASP ASVS 5.0 V14 (Data Protection) · CCPA/CPRA deletion rights.

## Consent, lawful basis & purpose limitation

- **Good looks like —** Personal data is processed only with a valid lawful basis (consent, contract, legitimate interest, etc.), and where consent is the basis it is **freely given, specific, informed, opt-in (not pre-ticked), granular, and revocable** — with the choice recorded (what/when/version). Data is used only for the **purpose it was collected for** (no silent repurposing for new analytics/ML/marketing); cookies/trackers and third-party data sharing honor the user's choices; sensitive categories and minors get stricter consent handling. Privacy notices reflect what the code actually does.
- **Auditor checks —** `[J]` does new processing/sharing have a lawful basis, and where consent applies, is it opt-in, granular, recorded, and revocable? `[J]` is data being used only for its original purpose (no scope creep into new analytics/ML/marketing)? `[J]` do trackers/third-party shares respect consent state?
- **Confidence —** `judgment`
- **Tradeoff (plain English) —** Asking permission properly and using data only for what it was given for is both the law and the basis of user trust; quietly repurposing data is how companies earn fines and headlines. The cost is consent plumbing and discipline about reuse.
- **Sources —** GDPR Arts. 6–7 (lawful basis, consent) & Art. 5(1)(b) (purpose limitation) · EU ePrivacy / cookie-consent rules · OWASP Privacy Risks · NIST Privacy Framework.

## Regulatory compliance mapping (GDPR · FERPA · HIPAA · PCI DSS)

- **Good looks like —** The change is checked against the regimes that apply to *this* data and audience: **GDPR/CCPA** (personal data, consent, rights, breach-notification, processor agreements), **FERPA** (US student education records — consent/disclosure limits, directory-info handling), **HIPAA** (US protected health info — Privacy/Security Rules, BAAs, access controls, audit), **PCI DSS** (payment-card data — scope minimization, never storing prohibited authentication data, segmentation). Where a regime applies, its specific controls (encryption, access, logging, retention, contracts) are met rather than assumed; data residency/cross-border-transfer rules are respected.
- **Auditor checks —** `[J]` which regimes does this data/audience trigger (student → FERPA, health → HIPAA, EU resident → GDPR, card data → PCI), and are that regime's controls present? `[J]` are processor/BAA/data-transfer obligations covered for any third party touched? `[J]` is PCI-prohibited data (full track, CVV) never stored?
- **Confidence —** `judgment`
- **Tradeoff (plain English) —** Matching the rules for the kind of data you handle (student, health, payment, EU-resident) avoids fines, lawsuits, and loss of the right to operate — these are existential, not optional. The cost is identifying which rules apply and meeting their specific controls; the engineer's job is to surface the obligation, not to silently ignore it.
- **Sources —** GDPR (Reg. (EU) 2016/679) · FERPA (20 U.S.C. § 1232g) · HIPAA Privacy & Security Rules (45 CFR Parts 160/164) · PCI DSS v4.0 · NIST SP 800-66 (HIPAA Security Rule guidance).

## Audit trails & accountability

- **Good looks like —** Security-significant events are logged immutably and traceably — authentication (success/failure), authorization denials, privilege changes, access to sensitive/regulated records, admin actions, config/security changes, and data exports/deletions — each with *who, what, when, from where*, a correlation/trace ID, and tamper-resistance (append-only / shipped off-host / integrity-protected). Logs are retained per policy, time-synced, and reviewable; they support both incident forensics and regulatory audit (HIPAA access logs, GDPR accountability). **Audit logs themselves contain no secrets, passwords, tokens, or raw sensitive PII.**
- **Auditor checks —** `[J]` are the security-significant events for this change logged with actor/action/timestamp/source and a correlation ID? `[D]` grep to confirm the audit log lines don't include passwords/tokens/secrets/raw PII (overlaps with the logging-hygiene dimension below). `[J]` are audit logs tamper-resistant and retained per the applicable regime?
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** A good audit trail is how you detect a breach, prove what happened, and satisfy regulators after the fact; without it an incident is invisible and unprovable. The cost is logging key events carefully (and never logging secrets); the benefit is forensic and legal defensibility.
- **Sources —** OWASP Top 10:2021 A09 (Security Logging & Monitoring Failures) · OWASP ASVS 5.0 V16 (Logging) · OWASP Logging Cheat Sheet · NIST SP 800-92 · HIPAA §164.312(b) (audit controls) · CWE-778 (Insufficient Logging).

## Sensitive-data hygiene — never log or commit real user data

- **Good looks like —** Real production user data, credentials, tokens, full PII, and payment/health data are **never** written to application logs, error trackers, traces, analytics events, test fixtures, seed data, screenshots, or the repository. Logs redact/mask sensitive fields by default (structured logging with allowlisted fields, not "dump the whole object"); test/dev environments use synthetic or anonymized data; debugging dumps that include payloads are gated and scrubbed; error trackers strip request bodies/headers (auth, cookies) and PII. Committed fixtures and example `.env` files contain only fake values.
- **Auditor checks —** `[D]` grep added log/print/tracker calls for whole-request/whole-object dumps, password/token/PII fields, `Authorization`/`Cookie` headers; scan committed fixtures/seeds for realistic PII or live credentials. `[D]` secret-scanner over the diff (shared with secrets management). `[J]` does any new log statement risk emitting sensitive data under real inputs? `[J]` is test/seed data synthetic?
- **Confidence —** `deterministic`
- **Tradeoff (plain English) —** Keeping real user data out of logs and the repo means a leaked log file or a public repo isn't itself a data breach — logs and repos sprawl to many places (CI, third-party log tools, laptops) you don't fully control. The cost is redaction-by-default and synthetic test data; the payoff is that your *defenses* don't become the leak.
- **Sources —** OWASP Top 10:2021 A09 · OWASP Logging Cheat Sheet (data to exclude) · OWASP ASVS 5.0 V14 (Data Protection) / V16 (Security Logging & Error Handling) · NIST SP 800-122 · CWE-532 (Info Exposure Through Log Files), CWE-312 (Cleartext Storage of Sensitive Info).

---

## Authoring rules (the catalog meta-rules — do not delete)

- **Additive floor:** add dimensions as you discover them; **never delete** one. This catalog is meant to become "every standard we can think of."
- **Right-size:** apply only *relevant* dimensions per change (`KISS`/`YAGNI`); never skip a relevant one. Relevance is a per-change judgment — see `README.md`.
- **Novel patterns allowed** when they add clear value — justify (problem → why existing patterns fall short → benefit) and record in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **Every dimension carries a Confidence tag** so the harness can separate what it *proved* (deterministic gates) from what it *asserts* (judgment). Trust the oracle, not the model's word.
