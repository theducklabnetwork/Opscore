# Changelog

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
