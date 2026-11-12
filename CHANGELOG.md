# Changelog

## Unreleased

### Changed

- Removed the retired VPS deployment pipeline and hosted-development script; desktop migration guidance and reusable health, security and dependency tests are preserved.

### Added

- Bundled the Git-tracked `ducklab/data/DSCSPR_alias_2027.xlsx` reference workbook in `.deb` and Windows builds, with automatic first-run loading into empty reference tables.
- Added schema v10 for bundled-reference tracking while keeping DSC imports compatible with v5 through v9.
- Made restores safer with pre-restore backups, integrity and foreign-key checks, temporary-copy migration and atomic replacement; migrations now commit each schema change and version update in one transaction.
- Restricted global shift-code changes and DSC uploads to configuration managers, with role validation before upload reads and a 64 MiB DSC size limit.
- Included paid RM0 expenses in Daily Operations, seeded ten default categories for stores without any, and excluded draft snapshots from finalized reports and warnings.
- Updated Monthly Setup with BV/UPT terms, Target Transaction suggestions, Minimum RTM in HQ defaults, and Copy From Last Month.
- Fixed Dashboard `?store=` deep links, Action Tracker status refreshes, Settings selectors, stale Store Management state, payroll editing on deactivated stores, and malformed schedule-week CSV exports.
- Updated release staging with temporary frontend-build databases, actual schema versions in manifests, strict `/_event` health checks, a 60-second readiness deadline (`DUCKLAB_READINESS_TIMEOUT`), `packaging/` in release sources, and installer creation of `/opt/ducklab-current`.
- Windows compatibility and Windows test coverage.
- Native Windows application windows through pywebview/WebView2, with Microsoft Edge app mode and the default browser as fallbacks.
- Self-contained Windows folder builds using standalone CPython 3.14.
- Per-user Windows installer built with Inno Setup and installed under `%LOCALAPPDATA%\Programs\Opscore`.
- GitHub Actions workflows for Windows checks, frozen-build smoke tests and installer release artifacts.

### Fixed

- Rotated stale WebView profiles after application version changes while preserving the database and other user data.
- Fixed the Planner **Export CSV** button: it previously crashed with an uncaught `AttributeError` (the handler referenced a nonexistent `User.stores` attribute), surfacing only as the generic "An error occurred" toast. The export now resolves the store via `AppService.get_store` and writes the per-staff `date,shift_code` CSVs under `Documents/Opscore Schedules/<store>/<week>/` as intended.

### Release status

- Windows packaging and installer automation are implemented in source. Published-release and real-machine validation status are unverified.

### Verification

- 1,144 tests passed, 22 skipped and 131 subtests passed on 2026-11-12 after removing the VPS deployment tooling.
- The production Reflex frontend export passed with a disposable database on 2026-11-12.
- One non-failing Starlette deprecation warning was reported for its HTTPX test-client integration.

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
