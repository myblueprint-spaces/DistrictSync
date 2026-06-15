# Changelog

All notable changes to DistrictSync are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Per-release download links and auto-generated commit notes live on the
[GitHub Releases](https://github.com/myblueprint-spaces/DistrictSync/releases) page.

## [Unreleased]

### Added

- Setup Wizard Step 1 now has a 📁 Browse button beside the input/output
  directory fields that opens the native folder picker (the text box still
  accepts manual entry/paste).

## [3.2.0] - 2026-06-15

Config-driven active-student filtering. This release changes the **default**
roster contents, so review the "Changed" notes before rolling out to a district.

### Changed

- **Active-student roster is now config-driven.** A student is included only
  when their enrolment status is in `active_values` (default `["Active"]`).
  Districts can override per-config via `EnrollStatus.active_values`.
- **PreReg (and other non-active) students are now excluded by default**, matching
  the partner FAQ. Previously some non-active statuses could appear in `Students.csv`.
- **Enrolment status value now wins over the withdraw date.** The previous hard
  withdraw-date override was dropped — a student whose status is active is kept
  even if a stale past withdraw date is present.

### Fixed

- **Zero-orphan enrollments.** Homeroom + subject student enrollment rows and
  auto-generated homeroom classes are filtered to the active roster, so no row in
  `Enrollments.csv` or `Classes.csv` references a student missing from
  `Students.csv`. Teacher enrollments are not filtered.

### Internal

- Extracted `TransformContext.get_demo_student_col()` to de-duplicate the
  student-id-column resolution shared by the Classes and Enrollments transformers.

## [3.1.1] - 2026-06-05

- Unattended scheduled-task + SFTP hardening (run-as account/password, redacted
  logging, host allowlist). See the GitHub release for details.

## [3.1.0] - 2026-06-04

- See the GitHub release for details.

## [3.0.0] - 2026-04-16

- myBlueprint+ output tiers (`CourseInfo`, `StudentCourses`) and `enabled_entities`
  output targeting. See the GitHub release for details.

[3.2.0]: https://github.com/myblueprint-spaces/DistrictSync/releases/tag/v3.2.0
[3.1.1]: https://github.com/myblueprint-spaces/DistrictSync/releases/tag/v3.1.1
[3.1.0]: https://github.com/myblueprint-spaces/DistrictSync/releases/tag/v3.1.0
[3.0.0]: https://github.com/myblueprint-spaces/DistrictSync/releases/tag/v3.0.0
