# Changelog

## Unreleased

### Added

- Windows compatibility and Windows test coverage.
- Native Windows application windows through pywebview/WebView2, with Microsoft Edge app mode and the default browser as fallbacks.
- Self-contained Windows folder builds using standalone CPython 3.14.
- Per-user Windows installer built with Inno Setup and installed under `%LOCALAPPDATA%\Programs\Opscore`.
- GitHub Actions workflows for Windows checks, frozen-build smoke tests and installer release artifacts.

### Release status

- Windows packaging and installer automation are implemented in source. Published-release and real-machine validation status are unverified.

## v1.0 — 2026-10-31

First stable desktop release.

### Added

- Monthly report finalization and correction workflows, including finalized-report Excel export.
- Schedule-staffing integration, staff employment types and schedule previews in Daily Operations.
- All Stores dashboard aggregation and live monthly reporting.
- Versioned release pipeline with staged verification, activation, health checks and rollback.
- Offline Ubuntu/Debian desktop package with bundled CPython 3.14 and a GTK/WebKit launcher.

### Changed

- Refactored page state into flat per-page states and improved report and chart performance.

### Verification

- 1,032 tests passed; 115 subtests passed for this release.

## 0.1.1 — 2026-10-30

### Added

- Weekly schedule planner under **Manpower Efficiency → Schedule**.
- Store-specific staff roster with staff IDs and positions.
- Configurable shift codes, working times, labels and colors.
- Weekly target manpower with daily headcount totals and below-target highlighting.
- Automatic schedule saving.
- Generated, print-friendly weekly schedule view.
- Per-staff CSV export containing dates and shift codes.
- Application screenshots in the project README.
- Schedule persistence, authorization and validation tests.

### Changed

- Updated standard shift-code colors.
- Clarified prototype naming and generated-data development practices.

### Upgrade notes

- Download a database backup before upgrading.
- Install over Opscore 0.1.0 with `sudo apt install ./opscore_0.1.1_amd64.deb`.
- The database automatically migrates from schema version 5 to version 7 on first launch.
- Existing operations data and user accounts are preserved.
- Schedule CSV files are saved to `~/Documents/Opscore Schedules/<store-code>/<week-start>/`.

### Verification

- 936 tests passed; 112 subtests passed.
