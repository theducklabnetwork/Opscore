# Changelog

All notable changes to Opscore are documented here. Versions follow the git
tags and published releases; see the provenance note at the end.

---

## v1.3 — 2026-11-18

This entry covers the work merged after v1.2 (pull requests #77–#87).
Schema version: **19**.

### Added

- **Daily Void and Refund totals.** Daily Entry and live Excel exports now
  record optional informational Void and Refund amounts. They remain separate
  from Sales and do not affect transaction/unit counts or any KPI. Daily
  Records also shows the totals, and its Sales per Staff table column is now
  labelled **SPS**.
- **Imported BD Target.** The workbook's BD Target column is now imported as
  reference data for October 2026. Monthly Setup shows the imported figure as
  a note and the **Load History From Directory** history icon fills the BD
  Target field with it. It is reference data only: it feeds no KPI, target
  level, budget or achievement number, and the field stays editable.
- **Bundled store directory.** The monthly-sales workbook is now bundled into
  the desktop builds and seeded automatically on startup: an empty directory
  is imported on first launch, a stale bundled copy is refreshed on upgrade,
  and a directory you uploaded yourself is never replaced. No manual import
  step is needed to get Sales History, New Store auto-fill or the Monthly
  Setup history button.
- **Store directory import.** A monthly-sales workbook (one row per store, one
  column per month) can be imported from Settings → Reference Data, or with
  `scripts/import_store_directory.py`. It fills a store directory without
  creating any stores or daily records, so the store list stays exactly as it
  was.
- **New Store auto-fill.** Typing a store's exact full code or full name in the
  New Store form fills in its Name, State and Zone from the directory. Partial
  input never matches, editing an existing store never auto-fills, and Concept
  stays a manual choice so the daily contribution figures keep working.
- **Monthly Setup history button.** The history icon beside the Monthly Setup
  fields (tooltip: **Load History From Directory**) fills Sales Last Month,
  Sales Last Year and the imported BD Target from the directory figures for
  the selected store and month. It only fills the form; nothing is saved until
  Save Setup, and months the workbook left blank or zero stay blank. The icon
  is disabled when the directory has no figures for that store and month.
- **BD Target field.** Monthly Setup gains an optional BD Target (RM), stored
  per store per month. It is a reference figure only: it does not affect Level
  1–4, achievement percentages, expense budgets or daily targets.
- **Sales History chart.** Sales Performance gains a Sales History
  card with a **Timeline / Year vs Year** toggle, drawn from the directory. It
  works for stores with no daily entries yet, and unreported months are gaps
  rather than zero.
- **PT Workforce Mix.** Manpower Efficiency now reports PT Active Days,
  Average/Peak PT Working and PT Contribution from daily attendance, without a
  separate PT roster. FT headcount and turnover formulas remain unchanged.
- **Staffing review guidance.** FT-only labels and attendance instructions
  clarify roster size versus staff actually working. Non-blocking warnings flag
  possible FT roster/attendance inconsistencies and PT coverage of FT vacancies.
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
  and replace every ABM assignment with automatic OPH, including later Planner
  edits. Other staff keep their assigned shifts, and removing a holiday clears
  only automatically created OPH assignments.
- **RPL shift code.** Replacement Leave joins the ordered, editable shift-code
  configuration. EL/MC/OPH are hidden from Config, and unexpected EL/MC are
  excluded from Planner.
- **Generated schedule refinements.** Per-code View checkboxes control the
  generated legend, long staff names clamp to two lines beside a compact
  Position column. Staff are ordered ABM, SV, ASV, then RA, and the centered
  Manpower count excludes ABM. **Save PDF** writes a fitted A4-landscape PDF
  directly without browser print dialogs or popups.

### Fixed

- **Restore cleanup ordering.** Stale WAL/SHM sidecars are removed before the
  atomic database replacement. Cleanup failure can no longer install a restore
  while reporting that the database was not replaced.
- **Shared transaction failure handling.** SQL errors poison the active
  `AppService` transaction even when inner code catches them, preventing an
  outer commit. Nested or overlapping transactions on the same service are
  rejected before they can corrupt shared connection state.

- **Linux zenity startup fallback.** GTK initialization failure now uses the
  discovered zenity executable directly for keep/fresh responses instead of
  referencing an undefined variable.
- **Windows build controls.** Removed the unused folder-build `-AllowOverwrite`
  parameter. Folder publication remains controlled by `-AllowReleaseOutput`;
  deliberate installer replacement uses `/DOPSCORE_ALLOW_OVERWRITE=1`.

### Changed

- **Five-year month selection.** The combined month dropdown is now separate
  **Month** and **Year** controls covering a rolling 60-month window (the
  current month and the previous 59 months). Historical months without Monthly
  Setup still show the configuration prompt, and selections outside the window
  clamp to its nearest boundary.
- **Contribution column matches the HQ workbook.** Sales Performance's daily
  table now shows each day's **normalized** share of the monthly target
  (1 decimal, e.g. 4.4%) instead of the raw imported DSC share. It is the
  figure that multiplies out to the Daily Target beside it and matches the
  workbook's **S.Ctrb** column. Daily Target formulas are unchanged.
- **Bundled reference data refreshes on upgrade.** Startup re-imports the
  bundled DSC/SPR workbook when the stored reference tables still came from
  the bundle but their content no longer matches it, so corrected day shares
  reach existing installs. Workbooks uploaded by the user are never replaced.
- **Manpower clarity and precision.** Monthly Sales per Staff preserves
  fractional cents and explains its staffed-day/effective-staff-day basis.
  Incomplete-month salary ratios keep their numeric values but use neutral
  status colors until the month is complete.
- **Bounded generated artifacts and live logs.** The newest 10 pre-migration,
  pre-restore and fresh-start copies are retained; old WebView profiles retain
  the newest three. Manual downloads and recovery-script backups are never
  pruned. `opscore.log` rotates at 5 MiB with three archives during startup and
  runtime through a launcher-owned sink that drains backend output continuously.
- **Versioned authentication and SQLite hardening.** Schema v15 transactionally
  creates the complete auth schema, including persistent login throttling.
  Migration audit history is cross-checked before backup, migration and restore.
  WAL is enabled during controlled initialization, with a 10-second busy
  timeout on app and backup connections. WAL-safe backup/restore includes
  committed content and never replays stale sidecar files.
- **Working-shift default colours.** M900–A121 default to white; existing
  custom colours, labels and times are preserved by the v12 migration.
- **Cobalt-blue interface.** Links, active navigation, primary actions and
  charts use one blue accent in light and dark modes. Dialogs use semantic
  titles/descriptions, form fields have associated labels, and the current
  navigation item is identified without relying on colour alone.
- **Accessibility and usability refinements.** Keyboard focus is visible;
  coarse-pointer controls receive 44px targets; reduced-motion preferences are
  respected. Metric abbreviations have hover/focus explanations and tappable
  definitions on touch devices. Compact values expose exact figures, charts
  have accessible names and concise summaries with existing detail tables,
  successful saves are announced politely, and empty Action/Staff states offer
  appropriate next actions. Planner below-target days include text as well as
  warning colour.

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

- **Version anchors** are the git tags `v0.1.0`, `v0.1.1`, `v1.0`, `v1.1`,
  `v1.2` and `v1.3` and their published GitHub releases. Tag dates are in
  UTC+08:00.
- **v1.3** covers the merged pull requests #77–#87 and this release
  preparation. Its Windows installer is signed with the self-signed **Opscore
  Development** Authenticode certificate, with a DigiCert timestamp; Windows
  does not trust that publisher by default.
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
