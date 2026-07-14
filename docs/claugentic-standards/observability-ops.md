<!-- claugentic-dev-harness@0.1.40 managed — do not edit (copied from the claugentic-dev-harness plugin) -->
---
module: observability-ops
title: Observability & Ops
version: 0.1.0
status: draft
iso_25010: [reliability]
load_scope:
  keywords: [log, logging, trace, metric, monitor, alert, config, env, deploy, feature-flag, rollback]
  globs: ["src/**", "**/*config*"]
last_reviewed: 2026-06-04
---

# Observability & Ops — structured signals, safe config, and operable deployments

> **Loads when:** changes touch logging, metrics, tracing, alerting, health checks, environment config, deployment pipelines, feature flags, or rollback paths.
> **ISO/IEC 25010:** reliability · **Status:** draft · **v0.1.0**

Each entry below is one **auditable dimension**. Per change, the reviewer applies the
*relevant* ones **fully** (select-don't-skip), right-sized to the change — never
gold-plating an irrelevant one, never skipping a relevant one.

---

## Structured logging

- **Good looks like —** Logs are structured (JSON or key=value), carry appropriate severity levels, emit actionable error messages, and contain **no secrets or PII**. Log lines are machine-parseable and filterable by level/service/trace-id.
- **Auditor checks —** Scan log statements for unredacted tokens, passwords, or user data `[J]`; confirm a structured logger (not bare `print`/`console.log`) is used `[D]`; verify ERROR/WARN are reserved for actionable conditions `[J]`.
- **Confidence —** `judgment` — a linter can catch bare print calls but secret/PII presence is a reviewer call.
- **Tradeoff (plain English) —** Structured logs make incidents 10× faster to diagnose; they do add a library dependency and slightly more verbose call sites. Skipping this means outage searches devolve into grepping free-text across machines.
- **Sources —** Google SRE Book ch. 6 "Monitoring Distributed Systems"; 12-Factor App §11 "Logs" (https://12factor.net/logs).

---

## Metrics, tracing, and health checks

- **Good looks like —** Key operations emit counters/gauges/histograms; distributed traces carry a consistent trace-id through service boundaries; health endpoints (`/healthz`, `/readyz`) report liveness and dependency status; anomaly checks exist for critical SLOs.
- **Auditor checks —** Confirm new code paths increment relevant counters or are justified as trivial `[J]`; verify trace-id propagation across async boundaries `[J]`; check that health endpoints exist and test real dependencies `[D]`.
- **Confidence —** `judgment` — coverage of instrumentation points is a reviewer call.
- **Tradeoff (plain English) —** Metrics and traces let you know a service is broken before users file tickets; they cost a small runtime overhead and require a metrics backend. Without them, you're flying blind at 2 AM.
- **Sources —** OpenTelemetry specification (https://opentelemetry.io/docs/); Google SRE Book ch. 6.

---

## Alerting hooks

- **Good looks like —** Alerts are tied to meaningful SLO thresholds (not just "CPU > 80%"), have a clear owner, and page only when human action is required. Alert fatigue is actively avoided by tuning thresholds.
- **Auditor checks —** If new failure modes are introduced, confirm a corresponding alert or runbook reference exists `[J]`; check that alert thresholds are documented and not arbitrary `[J]`.
- **Confidence —** `judgment` — alert design is a judgment call.
- **Tradeoff (plain English) —** Well-tuned alerts wake the right person fast; poorly tuned ones cause alert fatigue and get silenced, defeating the purpose. Skipping leaves failures undetected until users complain.
- **Sources —** Google SRE Book ch. 5 "Alerting on SLOs"; PagerDuty "Incident Response Guide" (https://response.pagerduty.com/).

---

## 12-factor configuration

- **Good looks like —** All environment-specific values (URLs, credentials, feature flags, resource limits) live in environment variables or a secrets manager — **never hardcoded, never committed**. Config is validated at startup so the process fails fast on bad config, not mid-request.
- **Auditor checks —** Grep for hardcoded hostnames, ports, or credential strings `[D]`; confirm config validation runs at process startup `[J]`; verify no env-specific branches (`if env == "prod"`) exist in application code `[J]`.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Externalised config lets the same image run in every environment safely; the cost is a small operational discipline around env setup. Hardcoded config means a production incident every time a URL changes.
- **Sources —** 12-Factor App §3 "Config" (https://12factor.net/config); OWASP Secrets Management Cheat Sheet (https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html).

---

## Environment separation and reproducible builds

- **Good looks like —** Dev/staging/production environments are isolated and cannot share live data. Build artifacts are reproducible (same inputs → same output); container images are pinned by digest or tag. Rollback is a one-step operation documented in the runbook.
- **Auditor checks —** Confirm no shared DB/queue between staging and prod `[J]`; verify image/dependency pins are present `[D]`; check that rollback steps exist in the runbook or CI pipeline `[J]`.
- **Confidence —** `mixed`
- **Tradeoff (plain English) —** Environment separation prevents a staging test from corrupting production data; reproducible builds mean a hotfix deployed at 3 AM is the same binary you tested. Without both, "it worked in staging" is meaningless.
- **Sources —** 12-Factor App §§2,10 "Codebase / Dev/prod parity" (https://12factor.net/dev-prod-parity); NIST SP 800-218 "Secure Software Development Framework" §PS.3.

---

## Feature flags and progressive rollout

- **Good looks like —** New behavior is gated behind a feature flag that can be toggled without a redeploy. Rollout is incremental (canary → percentage → full). Flag state is observable and auditable.
- **Auditor checks —** If a change introduces risk that warrants a flag, confirm one exists `[J]`; verify flag cleanup tickets exist to prevent permanent flag accumulation `[J]`.
- **Confidence —** `judgment` — whether a flag is warranted is a risk-sizing call.
- **Tradeoff (plain English) —** Feature flags let you ship code before you're ready to expose it and pull back instantly if something goes wrong; they add branching complexity and must be cleaned up or they rot. Skipping them forces big-bang releases.
- **Sources —** Martin Fowler "Feature Toggles" (https://martinfowler.com/articles/feature-toggles.html); LaunchDarkly "Feature Flag Best Practices" (https://launchdarkly.com/blog/best-practices-feature-flags/).

---

## Authoring rules (the catalog meta-rules — do not delete)

- **Additive floor:** add dimensions as you discover them; **never delete** one. This catalog is meant to become "every standard we can think of."
- **Right-size:** apply only *relevant* dimensions per change (`KISS`/`YAGNI`); never skip a relevant one. Relevance is a per-change judgment — see `README.md`.
- **Novel patterns allowed** when they add clear value — justify (problem → why existing patterns fall short → benefit) and record in `claugentic-DECISIONS.md`. Unconventional ≠ wrong.
- **Every dimension carries a Confidence tag** so the harness can separate what it *proved* (deterministic gates) from what it *asserts* (judgment). Trust the oracle, not the model's word.
