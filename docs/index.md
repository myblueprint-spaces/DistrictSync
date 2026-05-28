---
hide:
  - navigation
  - toc
---

<div class="mb-hero">
<img src="assets/spacesedu-wordmark.png" alt="SpacesEDU" style="height:40px;margin-bottom:1rem;filter:brightness(0) invert(1)">
<h1>DistrictSync</h1>
<p>Convert MyEducation BC General Data Extracts to SpacesEDU Advanced CSV — automatically, every night.</p>

[Download for Windows](https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-windows.exe){ .md-button }
[View on GitHub](https://github.com/myblueprint-spaces/DistrictSync){ .md-button .md-button--secondary }

</div>

<div class="mb-stats">
  <div class="mb-stat-card"><div class="stat-value">7</div><div class="stat-label">CSV outputs</div></div>
  <div class="mb-stat-card"><div class="stat-value">6</div><div class="stat-label">District configs</div></div>
  <div class="mb-stat-card"><div class="stat-value">3</div><div class="stat-label">Platforms</div></div>
  <div class="mb-stat-card"><div class="stat-value">91%</div><div class="stat-label">Test coverage</div></div>
</div>

---

## Download

Get the latest release for your platform:

| Platform | Download | Notes |
|----------|----------|-------|
| **Windows** | [DistrictSync-windows.exe](https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-windows.exe) | Double-click to open Setup Wizard |
| **Linux** | [DistrictSync-linux](https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-linux) | `chmod +x` before first run |
| **macOS** | [DistrictSync-macos](https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-macos) | Allow in System Settings › Privacy & Security |

!!! tip "First time?"
    Start with the **[Partner Installation Guide](partner/installation.md)** for step-by-step setup instructions including the Setup Wizard walkthrough.

---

## What it does

```
GDE files  →  DistrictSync  →  SpacesEDU CSV files  →  SFTP upload
```

| Input (MyEdBC GDE) | Output (SpacesEDU) | Tier |
|---|---|---|
| Student Demographic | `Students.csv` | rostering |
| Staff Information – Enhanced | `Staff.csv` | rostering |
| Emergency Contact Information | `Family.csv` | rostering |
| Student Schedule + Course Information | `Classes.csv` | rostering |
| Student Schedule + Class Information – Enhanced | `Enrollments.csv` | rostering |
| Course Information | `CourseInfo.csv` | myBlueprint+ |
| Course History + Selection + Information | `StudentCourses.csv` | myBlueprint+ |

### Output tiers

Which CSVs DistrictSync produces is controlled by `enabled_entities` in the config:

| Config | Outputs |
|---|---|
| `myedbc` (and inheriting district configs `sd40myedbc`, `sd48myedbc`, …) | The 5 rostering CSVs |
| `mbp_all` | 5 rostering CSVs + `CourseInfo.csv` + `StudentCourses.csv` (full myBlueprint+ tier) |
| `mbp_core` | `Students.csv` + `CourseInfo.csv` + `StudentCourses.csv` only (minimal myBlueprint+ tier) |

A district with non-standard file naming AND the myBlueprint+ tier composes the two — create a child config with `_base:` pointing to the district config and override `enabled_entities`.

### Key features

- **Active student filtering** — only Active students exported; PreReg and Inactive excluded
- **CEDS grade mapping** — BC grade codes (K, 1–12) mapped to CEDS standard format
- **Blended class detection** — same teacher/time/multi-grade sections merged automatically
- **Homeroom generation** — elementary homeroom classes auto-generated from demographics
- **District config inheritance** — district-specific overrides layer on top of the base MyEdBC config
- **Automated scheduling** — runs daily via Windows Task Scheduler or cron
- **SFTP upload** — zips all CSVs into a single dated file and uploads to SpacesEDU after each run; host allowlist restricts uploads to SpacesEDU servers only
- **Atomic writes** — all-or-nothing commit; a failed run never leaves partial output
- **Anomaly detection** — warns when output record counts drop more than 20% compared to the previous run
- **Structured run logging** — each run writes a machine-readable JSON log tag for the Run History page
- **Mapping Editor** — web UI wizard for creating or modifying district configs without editing YAML
- **Help & Docs page** — in-app access to documentation and support links

---

## GitHub Actions CI/CD

Releases are fully automated. As a developer you simply:

```bash
git tag v1.x.0
git push origin --tags
```

GitHub Actions then:

1. Runs the full test suite (Python 3.9, 3.11, 3.13) + lint + format check + type check + security scan (bandit) + config validation
2. Builds platform executables — Windows `.exe`, Linux binary, macOS binary — in parallel
3. Creates a GitHub Release with all three files attached and download links in the release notes

Partners can always get the latest version from the [Releases page](https://github.com/myblueprint-spaces/DistrictSync/releases/latest) — the `/releases/latest/download/` URL always resolves to the most recent release.

---

## Quick links

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem;margin-top:1rem">

<div style="background:#fff;border:1px solid #DBEAFE;border-radius:0.6rem;padding:1rem 1.2rem">
<strong>📦 Partner Guide</strong><br>
<a href="partner/installation/">Installation</a> · <a href="partner/troubleshooting/">Troubleshooting</a> · <a href="partner/faq/">FAQ</a>
</div>

<div style="background:#fff;border:1px solid #DBEAFE;border-radius:0.6rem;padding:1rem 1.2rem">
<strong>🛠 Developer Guide</strong><br>
<a href="developer/setup/">Setup</a> · <a href="developer/architecture/">Architecture</a> · <a href="developer/release/">Releases</a>
</div>

<div style="background:#fff;border:1px solid #DBEAFE;border-radius:0.6rem;padding:1rem 1.2rem">
<strong>🏫 SpacesEDU</strong><br>
<a href="https://www.spacesEDU.com">spacesEDU.com</a> · <a href="mailto:support@myBlueprint.ca">support@myBlueprint.ca</a>
</div>

</div>
