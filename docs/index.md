---
hide:
  - navigation
  - toc
---

<div class="mb-hero">
<img src="assets/spacesedu-wordmark.png" alt="SpacesEDU" style="height:40px;margin-bottom:1rem;filter:brightness(0) invert(1)">
<h1>GDE2Acsv</h1>
<p>Convert MyEducation BC General Data Extracts to SpacesEDU Advanced CSV — automatically, every night.</p>

[Download for Windows](https://github.com/myblueprint/GDE2Acsv/releases/latest/download/GDE2Acsv-windows.exe){ .md-button }
[View on GitHub](https://github.com/myblueprint/GDE2Acsv){ .md-button .md-button--secondary }

</div>

<div class="mb-stats">
  <div class="mb-stat-card"><div class="stat-value">5</div><div class="stat-label">CSV outputs</div></div>
  <div class="mb-stat-card"><div class="stat-value">4+</div><div class="stat-label">District configs</div></div>
  <div class="mb-stat-card"><div class="stat-value">3</div><div class="stat-label">Platforms</div></div>
  <div class="mb-stat-card"><div class="stat-value">91%</div><div class="stat-label">Test coverage</div></div>
</div>

---

## Download

Get the latest release for your platform:

| Platform | Download | Notes |
|----------|----------|-------|
| **Windows** | [GDE2Acsv-windows.exe](https://github.com/myblueprint/GDE2Acsv/releases/latest/download/GDE2Acsv-windows.exe) | Double-click to open Setup Wizard |
| **Linux** | [GDE2Acsv-linux](https://github.com/myblueprint/GDE2Acsv/releases/latest/download/GDE2Acsv-linux) | `chmod +x` before first run |
| **macOS** | [GDE2Acsv-macos](https://github.com/myblueprint/GDE2Acsv/releases/latest/download/GDE2Acsv-macos) | Allow in System Settings › Privacy & Security |

!!! tip "First time?"
    Start with the **[Partner Installation Guide](partner/installation.md)** for step-by-step setup instructions including the Setup Wizard walkthrough.

---

## What it does

```
GDE .txt files  →  GDE2Acsv  →  SpacesEDU CSV files  →  SFTP upload
```

| Input (MyEdBC GDE) | Output (SpacesEDU) |
|---|---|
| `StudentDemographicInformation.txt` | `Students.csv` |
| `StaffInformationEnhanced.txt` | `Staff.csv` |
| `EmergencyContactInformation.txt` | `Family.csv` |
| `StudentSchedule.txt` + `CourseInformation.txt` | `Classes.csv` |
| `StudentSchedule.txt` + demographics | `Enrollments.csv` |

### Key features

- **Active student filtering** — only Active students exported; PreReg and Inactive excluded
- **CEDS grade mapping** — BC grade codes (K, 1–12) mapped to CEDS standard format
- **Blended class detection** — same teacher/time/multi-grade sections merged automatically
- **Homeroom generation** — elementary homeroom classes auto-generated from demographics
- **District config inheritance** — district-specific overrides layer on top of the base MyEdBC config
- **Automated scheduling** — runs daily via Windows Task Scheduler or cron
- **SFTP upload** — uploads generated CSVs directly to SpacesEDU after each run
- **Atomic writes** — all-or-nothing commit; a failed run never leaves partial output

---

## GitHub Actions CI/CD

Releases are fully automated. As a developer you simply:

```bash
git tag v1.x.0
git push origin --tags
```

GitHub Actions then:

1. Runs the full test suite (Python 3.9, 3.11, 3.13) + lint + type check + config validation
2. Builds platform executables — Windows `.exe`, Linux binary, macOS binary — in parallel
3. Creates a GitHub Release with all three files attached and download links in the release notes

Partners can always get the latest version from the [Releases page](https://github.com/myblueprint/GDE2Acsv/releases/latest) — the `/releases/latest/download/` URL always resolves to the most recent release.

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
