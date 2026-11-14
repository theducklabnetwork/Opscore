# Changelog

All notable changes to Opscore are documented here. Versions follow the git
tags and published releases; see the provenance note at the end.

---

## v1.3 — in development

Schema version: **12**.

### Added

- **MyESS batch schedule export.** Planner retains its individual
  `date,shift_code` files and adds one combined
  `employee_no,date,shift_code` CSV for multi-employee upload in the MyESS
  HRMS Android app. Opscore Staff ID supplies `employee_no`.
- **Automatic pre-migration backups.** An existing database is copied through
  SQLite's online backup API before pending migrations begin. A failed backup
  prevents migration; restore staging does not create a redundant copy.
- **Newer-schema startup protection.** Older application code refuses to
  modify a newer database and displays an actionable desktop error.
- **Schedule-linked Daily Entry.** New entries auto-fill editable FT/PT,
  Annual Leave, RPL/Other Paid Leave and Unpaid Leave from the schedule.
  Unexpected MC and EL remain manual day-end actuals; saved entries do not
  change when the schedule changes. Reports include MC in paid leave, show EL
  separately and count each leave person-day once.
- **Store holidays in Planner.** Configured public holidays receive PH markers
  and automatic OPH only for blank ABM cells. Manual shifts are preserved, and
  removing a holiday clears only automatically created OPH assignments.
- **RPL shift code.** Replacement Leave joins the ordered, editable shift-code
  configuration. EL/MC/OPH are hidden from Config, and unexpected EL/MC are
  excluded from Planner.

### Changed

- **Bounded automatic backups and logs.** The newest 10 pre-migration and
  pre-restore backups are retained and listed together in Settings → Data.
  Manual downloads, fresh-start copies and recovery-script backups are not
  pruned. At startup, `opscore.log` rotates at 5 MiB with three archives;
  the active file may grow during a continuously running session.
- **Working-shift default colours.** M900–A121 default to white; existing
  custom colours, labels and times are preserved by the v12 migration.

---

## v1.2 — 2026-11-12

This entry covers the work merged after v1.1 (pull requests #67–#73 and
subsequent v1.2 fixes). The `v1.2` tag points to `50f999b`; the Linux `.deb`
and Windows installer were published separately.

### Added

- **Single-use recovery keys.** Each account can hold one recovery key, shown
  once and saved as a recovery-kit file. **Forgot password?** on the sign-in
  page accepts the key and a new password; the key is consumed atomically,
  all sessions are revoked, and a replacement key is issued. (#69)
- **First-account recovery key handoff.** The first Super Admin created at
  first launch now receives a recovery key as part of bootstrap. (#71)
- **Installer keep/fresh database choice.** On the first launch after
  install/upgrade, the Windows installer wizard and the Linux launcher offer
  to keep the existing database or start fresh (renaming the old file to
  `store_ops.db.backup-N`). (#68)
- **Default expense categories on store creation.** New stores receive the
  ten default categories. Also includes an offline
  `scripts/recover_admin_password.py` for locked-out administrators. (#67)
- **Signed Windows installer.** The published `Opscore-Setup-1.2.exe` carries
  an Authenticode signature from the self-signed **Opscore Development**
  certificate, with a DigiCert timestamp. Its signed file digest and timestamp
  verify successfully, but the publisher is not trusted by Windows by default.
  Signing was a post-build release step, not part of the build scripts.

### Fixed

- **Windows shutdown and Planner CSV export.** The Windows backend now stops
  gracefully, and malformed schedule weeks produce a validation error instead
  of a broken CSV export. (#70)

### Removed

- **Retired VPS deployment tooling.** The hosted/server deployment pipeline
  and hosted-development script were removed; Opscore is desktop-only. (#72)
  Documentation was rebuilt as part of this change (#73).

---

## v1.1 — 2026-11-09

The `v1.1` tag is a lightweight tag on a docs commit; the functional content
is the merged range `v1.0..v1.1`, and the GitHub release was published on
2026-11-10. Schema version: **10**.

### Added

- **Windows desktop support.** Self-contained Windows folder build and a
  per-user Inno Setup installer, with WebView2 / Edge app mode / default
  browser fallbacks.
- **Bundled reference workbook and schema v10.** The Git-tracked
  `ducklab/data/DSCSPR_alias_2027.xlsx` workbook is bundled into both builds
  and loaded automatically into empty reference tables on first run.
- **Target Transaction suggestion** and **Copy From Last Month** in Monthly
  Setup; BV/UPT terminology and Minimum RTM in HQ defaults.
- **Ten default expense categories** seeded for stores without any.

### Changed

- **Safer restores and migrations.** Restores validate integrity, foreign
  keys and schema on a temporary copy, save a pre-restore backup, and replace
  the live database atomically. Migrations now commit each schema change and
  its version update in one transaction.
- **Stricter permissions.** Global shift-code changes and DSC uploads are
  restricted to configuration managers (Super Admin and Administrator); DSC
  uploads are role-checked before reading and capped at 64 MiB.
- **Daily Operations** includes paid RM0 expenses.

### Fixed

- Draft snapshots are excluded from finalized reports and warnings; payroll
  editing is gated on deactivated stores.
- Dashboard `?store=` deep links, Action Tracker status refreshes, Settings
  selectors, stale Store Management state, and malformed schedule-week CSV
  exports.
- Release staging uses a temporary frontend-build database.

### Validation note

The published v1.1 release notes state the Windows build was "tested on a
fresh Windows VM", but the in-repository documentation at the `v1.1` tag
stated that Windows packaging's published-release and real-machine validation
were unverified. There is no in-repo evidence of the VM test, so the
conservative position — real-machine validation unconfirmed at release time —
is the one reflected here and in the other documents.

---

## v1.0 — 2026-10-31

First stable desktop release. The `v1.0` changelog entry was not added to the
repository at tag time; this entry is reconstructed from the annotated tag
message, the published GitHub release, and the verified `v0.1.1..v1.0` commit
range. Schema version: **9**.

### Added

- **Monthly report finalization and corrections.** Finalize Month saves an
  immutable version; Correct Month adds a new version without overwriting the
  old one.
- **Schedule-staffing integration.** Staff employment types, schedule
  preview in daily entry, and Apply Schedule.
- **All Stores dashboard and live monthly report** aggregation across
  assigned stores.
- **Versioned desktop release pipeline**, including the self-contained Ubuntu
  `.deb` with bundled CPython 3.14 and GTK/WebKit launcher.

### Changed

- **Performance and state-management work:** report presenter unification,
  query optimization, chart and state optimization, and a flat shared-state
  structure.

---

## v0.1.1 — 2026-10-30

Schema version: **7**. Source: the in-repository changelog carried forward to
`v1.1`, cross-checked against commits.

### Added

- **Weekly schedule planner** with a printable generated view (schema 5 → 7).
- **Weekly schedule CSV export** (per-staff files).
- README screenshots.

---

## v0.1.0 — 2026-10-27

First published desktop `.deb` line. `v0.1.0` is a lightweight tag with no
in-repository changelog entry; this entry is reconstructed from the published
GitHub release and the commit range. Schema version: **5**.

### Added

- Initial desktop application: Dashboard, Monthly Report, Daily Operations,
  Sales Performance, Expenses Management, Manpower Efficiency, Stock
  Management, Action Tracker, Store Management and Settings.
- Daily Sales Contribution import (daily targets and carry-forward).
- Five-role server-side access control.
- Self-contained Ubuntu/Debian `.deb` package.

### Known limitations at release

- Monthly Report not yet locked (no finalization workflow).
- Ubuntu-only packaging (no Windows build).

---

## Provenance

- **Version anchors** are the git tags `v0.1.0`, `v0.1.1`, `v1.0`, `v1.1`
  and `v1.2` and their published GitHub releases. Tag dates are in UTC+08:00.
- **v1.2** is tagged at `50f999b`; its content includes the merged pull
  requests #67–#76, with subsequent documentation on `main`.
- **v1.0 and v0.1.0** entries are reconstructed from tag messages, release
  bodies and commit ranges because no complete in-repository changelog existed
  at those tags; they are marked as such.
- **Historical verification figures** (test totals, workbook-parity counts)
  recorded in older release notes are not restated here as current results;
  they were prose claims that could not be independently re-verified from the
  current tree.
- Where a published release body and in-repository documentation disagree
  (notably Windows real-machine validation in v1.1), the conservative
  in-repository position is used and the discrepancy is noted.
