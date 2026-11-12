# Opscore: Project Overview

**Opscore** is an offline desktop application for tracking a store's daily
operations — sales against targets, expenses, manpower, stock loss and
corrective actions — in one place, one month at a time.

This document explains why Opscore exists, what it does, who it is for, and
where its scope ends. For step-by-step instructions see the
[User Guide](USER_GUIDE.md); for technical design see
[Architecture](ARCHITECTURE.md); for what changed between releases see the
[Changelog](CHANGELOG.md).

---

## 1. Background

Opscore started as a replacement for the *Store Operations Monitoring* Excel
workbook used to follow store performance. Store managers responsible for
more than one store had no tool built for their level: reports from HQ and
the zone describe outcomes, but there was no simple, shared way to track the
daily figures, follow up on issues, and close out a month.

The application grew out of that workbook into a standalone desktop app that
any manager can install and use for their own stores, with no spreadsheet
skills and no server required.

## 2. The problem

| Problem | Effect |
|---|---|
| No tool built for the store-manager level | Performance is only visible through HQ/zone reports, not through a manager's own daily tracking. |
| Shortfalls found late | Sales gaps surface at month-end, when there is no time left to recover them. |
| Issues not followed up | Problems raised at a store are forgotten and repeat. |
| Data scattered and fragile | Figures live in separate files and chats; a bad paste or overwritten formula silently breaks totals. |
| Blank days ambiguous | In a spreadsheet, an unreported day can look exactly like a zero-sales day. |
| No locked final version | Once a month is reported there is no immutable "final" copy to refer back to. |
| Sensitive figures over-shared | Anyone with the workbook sees everything, including payroll. |

## 3. The solution

| Problem | Opscore's answer |
|---|---|
| Weak daily picture | **Dashboard** and **Daily Operations**: sales, BV, UPT, salary ratio and stock loss for each store on one screen. |
| Shortfalls found late | **Daily Target** and **Adjusted Target**: every day shows its target and whether it was achieved, with shortfall carried forward. |
| Issues forgotten | **Action Tracker**: every issue logged with an owner, priority and status until done. |
| Scattered, fragile data | One local database per installation, with parameterized writes and validated inputs. |
| Blank vs zero | **Blank is not zero.** Unreported days are stored as NULL and excluded from averages; an entered 0 is a real zero. |
| No final version | **Finalize Month** saves an immutable, versioned snapshot; **Correct Month** adds a new version instead of overwriting. |
| Over-shared sensitive figures | Five roles enforced server-side; payroll figures are only populated for Super Admin and Administrator. |

## 4. Goals and design principles

1. **Built for store managers.** Simple pages, short labels, no formulas on screen.
2. **Correct numbers.** Every KPI follows the original workbook's rules; all
   calculation logic lives in one pure module (`ducklab/kpi.py`), separate
   from the UI and the database.
3. **Blank is not zero.** "Not reported" and "zero" are distinct throughout
   storage, calculation and display.
4. **Private by design.** Each installation keeps its own database on its own
   machine. There is no shared server, no sync, no telemetry of business data.
5. **Works offline.** No internet connection is needed after installation.
6. **No company names in code.** Store concept names are aliases (Outlet,
   Mall, Retail, and so on); the bundled reference data uses the same aliases.

## 5. Target users and roles

The primary users are store managers (including assistant branch managers
responsible for several stores), their zone managers, and the administrators
who configure stores and targets.

| Role | Identifier | What they can do |
|---|---|---|
| Super Admin | `super_admin` | Everything, across all stores: stores, users and assignments, all records, Monthly Setup, finalize/correct reports, backup and restore. |
| Administrator | `administrator` | Assigned stores: operational records, Monthly Setup, budgets, categories, payroll, scheduling configuration, finalize/correct reports. No user administration or store registration. |
| Zone Manager | `zone_manager` | Assigned stores: daily records, stock, expenses, actions, staff and schedules; read reports. No configuration or finalization. |
| Branch Manager | `branch_manager` | Same operational write scope as Zone Manager for their assigned stores. |
| Viewer | `viewer` | Assigned stores: read-only. |

Access is deny-by-default and enforced server-side in `ducklab/authz.py`.
Store access comes only from explicit assignments, never from zone names or
client-supplied values. Payroll values are only populated for Super Admin and
Administrator rather than merely hidden in the interface.

## 6. Core capabilities

All capabilities below are implemented in the current source. None of the
routed features are marked experimental or "coming soon".

| Module | What it does |
|---|---|
| **Dashboard** | Month at a glance: pillar cards for Sales, Expenses, Salary Ratio and Stock Loss, Sales Trend, targets, Alerts and Summary. An **All Stores** view aggregates across assigned stores. Cards deep-link into their modules. |
| **Daily Operations** | Per-day entry of sales, transactions, units, FT/PT staffing, hires, resignations, four leave types, and all stock-loss codes. Paid expenses for the day are shown read-only. **Apply Schedule** previews schedule-derived staffing before applying it. Explicit clearing stores NULL. |
| **Sales Performance** | Levels 1–4 achievement, actual and projected sales, Level Reached, average daily sales, BV/UPT/ASP against imported state benchmarks, Sales Trend, Daily Sales vs Target, Sales by Day, Sales Calendar, target ladder and daily table. Editing is done in Daily Operations. |
| **Expenses Management** | Paid/Incurred/Pending/Budget/Utilization/Variance KPIs, Needs Review filtering, Budget vs Paid and Expense Mix charts, and a transaction register with filtering and sorting. Incomplete drafts are allowed and flagged. |
| **Manpower Efficiency** | Overview of headcount, productivity, salary, turnover and leave; payroll editing for configuration managers. The **Schedule** tab adds a weekly Planner (autosaving shift cells and target manpower), Staff roster, shift-code Config, and a printable generated View. |
| **Stock Management** | Read-only analytics: Total Loss, Loss Ratio, Missing and Extra, daily loss and category charts, and previous-month comparison. All editing happens in Daily Operations. |
| **Action Tracker** | Open/Overdue/Completed tracking with filters, carried-over actions from earlier months, and full add/edit dialogs. |
| **Store Management** | New Store, Edit, Activate/Deactivate (never delete), user-to-store assignment, and Monthly Setup (targets, holidays, manpower, stock, budgets, month-end inputs) with HQ defaults and Copy From Last Month. |
| **Monthly Report** | Live/Finalized modes, version selector, sectioned reporting, Finalize Month / Correct Month with non-blocking warnings, and Excel export with payroll gated by role. |
| **Settings** | Account (password, single-use recovery key, tour replay), Reference Data (expense categories and DSC workbook import), Data (backup/restore, Super Admin only), and About (version, schema). |

## 7. Main operating workflow

The intended routine is a monthly cycle:

1. **Set up.** A Super Admin creates the store; a configuration manager
   completes **Monthly Setup** for the month (targets, holidays, budgets,
   manpower, stock).
2. **Record daily.** Operators enter each day's figures in **Daily
   Operations** and log expenses and actions as they arise.
3. **Monitor.** Dashboard, Sales Performance, Manpower Efficiency and Stock
   Management show progress against targets throughout the month.
4. **Finalize.** At month-end a Super Admin or Administrator reviews the
   finalization warnings and clicks **Finalize Month** to save an immutable
   version.
5. **Export and archive.** The live or finalized report is exported to Excel;
   a Super Admin downloads a database backup from **Settings → Data**.

## 8. Supported platforms

Opscore is a local desktop app for **Ubuntu/Debian (amd64)** and **Windows
10/11 (x64)**. See the [User Guide](USER_GUIDE.md) for installation and
[Architecture](ARCHITECTURE.md) for platform-specific behavior.

There is no macOS package, no mobile app, and no hosted/server deployment.
The retired VPS deployment tooling was removed in v1.2.

## 9. Functional scope and boundaries

These are deliberate boundaries of the current implementation, not unfinished
work-in-progress:

- **Local, one database per installation.** Multiple accounts and roles exist
  within one installation, but there is no shared server or synchronization
  between machines.
- **No general user-administration UI.** Creating users, changing roles and
  enabling/disabling accounts exist only in the service layer and scripts;
  the interface has no page for them. Store Management's **Users** panel only
  assigns existing users to stores.
- **Shift codes are global**, not store-owned. Administrator-level edits to
  shift codes affect every store.
- **Schedule CSV export writes files**, one per scheduled staff member, under
  `Documents/Opscore Schedules/<store>/<week>/`. It is not a browser download.
- **Month selectors** offer the current month and the previous 11 months.
- **No general CSV/Excel import** for daily records, expenses, staff or
  actions. The only workbook import is the DSC/SPR reference workbook.
- **Finalized snapshots preserve KPI outputs**, not a full history of source
  input records. Corrections do not record a reason, operator or approver.
- **Expense register display** is capped at 100 filtered rows.
- **Expense categories and actions cannot be deleted** through the interface;
  stores are deactivated rather than deleted.

## 10. Current implementation status

Current release line: **v1.2**. Database schema version: **10**.

| Area | Status |
|---|---|
| All operational modules | Implemented |
| KPI engine and daily-target logic | Implemented |
| Monthly Report finalization and corrections | Implemented |
| Weekly scheduling, printing and CSV export | Implemented |
| First-launch account creation and guided tours | Implemented |
| Single-use recovery keys | Implemented (v1.2) |
| Ubuntu/Debian desktop package | Released |
| Windows desktop package and installer | Released; see the [Changelog](CHANGELOG.md) for the Windows validation-status note |

Historical verification claims (for example workbook-parity checks and test
totals recorded in older releases) are documented with their releases in the
[Changelog](CHANGELOG.md), not restated as current results here.

## 11. Known limitations

In addition to the scope boundaries above, the following are verified
limitations in the current implementation. They are documented so users and
maintainers know the boundaries; they are not fixed by this documentation.

- **No automatic pre-migration backup.** Upgrading the app migrates the
  database at startup without first taking a safety copy. Download a backup
  from **Settings → Data** before upgrading. See [Migrations](MIGRATIONS.md).
- **No guard against older code opening a newer database.** Ordinary startup
  does not refuse a database whose schema is newer than the app expects.
  (Restore does reject such files.)
- **No log rotation.** `opscore.log` grows unbounded over time.
- **Unbounded retention.** Numbered `store_ops.db.backup-N` copies, old
  WebView profiles and pre-restore backups are not cleaned up automatically.
- **Windows real-machine validation** is not confirmed by in-repository
  evidence; see the [Changelog](CHANGELOG.md) note.
- **A Linux launcher fallback bug**: when GTK is unavailable and the system
  falls back to a zenity prompt for the keep/fresh database choice, a
  `NameError` can abort startup (`packaging/opscore_desktop.py`). Standard
  GTK and Windows paths are unaffected.

## 12. Future development considerations

There is no published roadmap. The only future item referenced in the source
is a possible **Users tab in Settings** for account administration (noted in
`ducklab/ops_settings_pages.py`). Items such as automatic pre-migration
backups, log rotation and a newer-database startup guard are documented above
as known limitations and would be natural candidates for future work, but
none of them should be described as planned features.

## 13. Related documents

- [README](README.md) — introduction and quick start
- [User Guide](USER_GUIDE.md) — how to install and use every page
- [Architecture](ARCHITECTURE.md) — how the system is built
- [Changelog](CHANGELOG.md) — release history
- [Migrations](MIGRATIONS.md) — database schema evolution and upgrades
