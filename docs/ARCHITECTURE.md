# Opscore Architecture

This document describes how Opscore (code name DuckLab) is built: its layers, data model, security model, KPI engine and desktop packaging.

---

## 1. Overview

Opscore is a full-stack Python app built with **Reflex 0.10**. Reflex compiles the UI to a React frontend and runs a Python backend. The two talk over a WebSocket. Data is stored in a single **SQLite** file per user.

```
┌──────────────────────────── Desktop window (pywebview, GTK/WebKit) ─┐
│  React frontend (compiled by Reflex, served as static files)        │
└───────────────┬─────────────────────────────────────────────────────┘
                │  WebSocket (Reflex events) + POST /api/session
┌───────────────▼─────────────────────────────────────────────────────┐
│  Reflex backend  127.0.0.1:47811                                    │
│                                                                     │
│  Pages + State     ops_*_pages.py / ops_*_state.py, auth_pages.py   │
│        │                                                            │
│  AppService        app_service.py  ──►  authz.py (who may do what)  │
│        │                                                            │
│  Services          operations_service.py (report assembly)          │
│                    operations_write_service.py (validated writes)   │
│                    operations_snapshots.py (finalized reports)      │
│                    auth_service.py (accounts, sessions)            │
│        │                     ▲                                      │
│  KPI engine        kpi.py ───┘  pure functions, no I/O              │
│        │                                                            │
│  Repositories      operations_repository.py (reads)                 │
│                    operations_write_repository.py (writes)          │
│        │                                                            │
│  Database          store_ops_db.py + auth_db.py (schema, migrations)│
└───────────────┬─────────────────────────────────────────────────────┘
                ▼
        ~/.local/share/opscore/store_ops.db   (SQLite, schema v10)
```

## 2. Technology

| Part | Choice |
|---|---|
| Language | Python 3.14 |
| Framework | Reflex 0.10 (React frontend, Python backend) |
| UI kit | Radix Themes (light default, dark selectable), Tailwind v4 |
| Charts | Plotly |
| Guided tours | driver.js 1.3.1 (bundled locally) |
| Database | SQLite |
| Passwords | Argon2id (argon2-cffi) |
| Excel | openpyxl (export); stdlib zipfile + XML (Daily Sales Contribution import) |
| Desktop window | pywebview 6.2 (GTK + WebKit2) |
| Packaging | nFPM, bundled standalone Python from uv |
| Tests | pytest, Playwright for browser checks |

Direct dependencies are listed in `requirements.txt`. All transitive versions are pinned in `requirements.lock`, which is what builds use.

## 3. Layers and rules

Each layer only calls the one below it.

| Layer | Files | Responsibility |
|---|---|---|
| Pages | `ops_*_pages.py`, `auth_pages.py`, `ops_shell.py`, `ui.py`, `design.py` | Layout and components. Display only. |
| State | `ops_shared_state.py`, `ops_state.py`, `ops_*_state.py` | Page state and event handlers. Formats values for display. Calls `AppService`. |
| App service | `app_service.py` | The single entry point for every operation. Resolves the signed-in user and checks access before anything runs. |
| Access control | `authz.py` | Deny-by-default role and store checks. |
| Services | `operations_service.py`, `operations_write_service.py`, `operations_snapshots.py`, `auth_service.py`, `backup.py`, `report_export.py`, `dsc_import.py` | Business workflows. |
| KPI engine | `kpi.py` | Every business calculation, as pure functions. |
| Repositories | `operations_repository.py`, `operations_write_repository.py` | Parameterized SQL, store-scoped, explicit transactions. |
| Database | `store_ops_db.py`, `auth_db.py` | Schema, migrations, connections. |

**Hard rules:**

1. **All KPI logic lives in `kpi.py`** (assembled by `operations_service.py`). Pages and state never calculate business figures.
2. **Every operation goes through `AppService`.** User IDs, roles or store IDs sent from the browser are never trusted.
3. **Money is integer cents** everywhere below the display layer. No floating-point currency maths.
4. **NULL means "not reported"** and is never turned into 0. An entered 0 is a real zero.
5. **Percentages are stored as fractions** (10% is 0.10).
6. **Page states are flat.** Every `/ops` page state inherits `SharedOpsState` directly:

```
AuthState (auth + theme)
└── SharedOpsState          (selectors, store-active flag, shared status
    ├── OpsState            surface, tours; load_selectors/resync_selection)
    │                       = the Report page (/ops/report): report data,
    │                         versions, finalization, export
    ├── DailyState          (/ops/daily)
    ├── ExpensesState       (/ops/expenses)
    ├── ScheduleState       (schedule tab on /ops/manpower)
    ├── ManpowerState       (/ops/manpower)
    ├── StockState          (/ops/stock)
    ├── StoresState         (/ops/stores: table + form)
    ├── SetupState          (/ops/stores: Monthly Setup)
    ├── AssignmentState     (/ops/stores: user assignment)
    ├── SalesState          (/ops/sales)
    ├── ActionsState        (/ops/actions)
    ├── OverviewState       (/ops dashboard)
    └── SettingsState       (/ops/settings)
```

No page state subclasses another page's state; siblings share one
`SharedOpsState` instance for selectors and the status surface, and wire
cross-page refreshes by yielding each other's events. Chart-capable states
register in `ops_charts.CHART_STATES` at import time so the theme toggle
can restyle open figures without cross-module imports.

Store-selection refreshes preserve current state: Dashboard applies `?store=`
once after selector initialization, Action Tracker refreshes active/write
status, Settings populates both global selectors and reloads categories on a
store change, and Store Management clears cached rows and privileged controls
after authentication or authorization failures.

## 4. Data model

One SQLite file holds both operations data and accounts. Schema version is tracked in `schema_migrations` and `PRAGMA user_version` (currently **10**). Initialization is idempotent and migrations are additive. Each migration runs in its own transaction, and its `PRAGMA user_version` update commits in that same transaction; a failure rolls back the whole step so it can be retried.

### Operations tables

| Table | Holds |
|---|---|
| `stores` | Code (unique), name, Concept, State, zone, zone PIC, branch manager, active flag |
| `monthly_configs` | One row per store and month: minimum RTM target, Level 1 base, Level 2 to 4 uplifts, last year and last month sales, salary ratio target, budget headcount, turnover threshold, stock loss target, transaction/BV/UPT targets, opening headcount, part-timer factor, payroll figures |
| `public_holidays` | Holiday dates per store and month |
| `daily_records` | One row per store and date: sales, transactions, units, staffing and leave counts |
| `daily_stock_losses` | Stock loss amount per daily record and loss code |
| `stock_loss_codes` | The 13 codes (DMO, DMC, DMP, 2DMP, DPI, DMW, DDR, DMN, DME, DMQ, DST, MISSING, EXTRA) and whether each adds to, reduces or doesn't affect Total Loss |
| `expense_categories` | Store's expense categories |
| `monthly_expense_budgets` | Budget per category per month |
| `expense_transactions` | Expense register lines: dates, category, amount, paid by, status, receipt, verification |
| `actions` | Action Tracker items: pillar, issue, owner, priority, status, due date |
| `monthly_snapshots` | Finalized monthly reports, versioned |
| `contribution_shares` | Daily Sales Contribution: each day type's share of the month by State and Concept |
| `basket_benchmarks` | State average BV and UPT by Concept and month |
| `reference_data_info` | When and from which file the reference data was loaded |
| `staff` | Store-scoped staff roster: name, staff ID, position and employment type (`Full-Time`, `Part-Time` or `Not Set`) |
| `shift_codes` | Shared shift codes, labels, working times and colors |
| `schedule_entries` | Staff shift assignments by store and date |
| `schedule_targets` | Target manpower per store and week |

### Account tables

| Table | Holds |
|---|---|
| `auth_users` | Email, display name, Argon2id hash, active flag, tour status |
| `auth_roles`, `auth_user_roles` | Roles and who has them |
| `auth_user_store_assignments` | Which stores each user may access |
| `auth_sessions` | Server-side sessions (12-hour lifetime) |
| `auth_audit_events` | Sign-in, password and admin events |
| `auth_page_tours` | Which page tours each user has completed |
| `auth_recovery_keys` | SHA-256 hashes of single-use recovery keys (raw keys never stored) |

## 5. KPI engine (`kpi.py`)

Pure functions with no database or UI access. The main groups:

| Area | Rules |
|---|---|
| Sales | Days entered, sales to date, projected month-end sales (actual if every day is entered, otherwise sales ÷ days entered × days in month) |
| Target levels | Level 1 = base. Levels 2 to 4 = Level 1 × (1 + uplift). Level 4 is never below the minimum RTM target. Highest level reached. Sales needed per remaining day. |
| Growth | SSSG/Year and SSSG/Month against last year and last month |
| Basket | BV (basket value) = sales ÷ transactions. UPT = units per transaction. ASP = sales ÷ units. Blank when the divisor is 0 or missing. |
| Daily targets | Each day's target = Level 1 × that day's contribution share (weekday or public holiday, by State and Concept). A shortfall carries forward to the next *reported* day only. |
| Expenses | Paid, incurred and pending totals, budget status, lines needing review |
| Manpower | Salary ratio and status, salary budget, headcount variance, turnover |
| Stock | Total Loss from signed loss codes, loss ratio against target |
| Actions | Month scope, open, overdue and completed counts |

Parsing helpers (`parse_money_cents`, `parse_count`, `parse_decimal`) are shared by every data-entry page so validation is identical everywhere.

**Proof of correctness:** `scripts/import_workbook_dev.py --verify` loads a real month from the original workbook and compares every figure the app shows with the workbook's own results. For September 2026, all 102 metrics matched.

### Weekly schedules

Schedule reads and writes go through `AppService`, with store access enforced by `authz.py`. Staff rosters and entries are store-scoped; shift codes are shared and can only be changed by users with the global configuration permission (Super Admin or Administrator). Each staff member has an employment type (`Full-Time`, `Part-Time` or `Not Set`). Only shift codes with working times count toward daily headcount, and schedule staffing uses employment type to separate full-time and part-time workers. `ops_schedule_pages.py` provides the planner, generated view and print styling, while `ops_schedule_state.py` (ScheduleState, a sibling of ManpowerState) writes per-staff CSV files to `~/Documents/Opscore Schedules/<store-code>/<week-start>/` and reports malformed schedule weeks as validation errors.

Daily Operations can apply a selected day's schedule through **Apply Schedule**. The **Schedule Preview** shows derived working staff and leave counts, with warnings where applicable, before the user applies them to the entry.

## 6. Monthly reports and snapshots

- **Preview:** `operations_service.py` assembles the live report for a store and month from the repositories and `kpi.py`. The Dashboard and Monthly Report also offer an **All Stores** live aggregate across the user's assigned stores; this view has no finalized versions.
- **Finalize:** **Finalize Month** on the Monthly Report page calls `operations_snapshots.py` to save the report's KPI results as a JSON snapshot (`ducklab.snapshot.v1`), starting at version 1.
- **Correct:** **Correct Month** creates a new version from current live data and supersedes the previous one. Old versions are kept and never overwritten, and the finalized view provides a version selector.
- **Export:** `report_export.py` writes the selected report and its source records to Excel. Live reports use `opscore-<store>-<month>.xlsx`; finalized reports use `opscore-<store>-<month>-finalized-v<version>.xlsx`.

Snapshots keep KPI outputs, not a full history of the input records. Latest-version lookups and the Finalized version list include only rows with `finalized = 1`, so draft snapshots never appear as finalized reports or finalized-month warnings.

Finalize and Correct are available on screen to Super Admin and Administrator users for active single-store months that pass the live-report preflight. All Stores remains live-only.

## 7. Security model

### Authentication

- Passwords hashed with Argon2id. Minimum 10 characters with letters and digits.
- Sessions are server-side with a 12-hour lifetime. The browser holds only an HttpOnly, SameSite=Lax cookie set by `POST /api/session`. No session token ever enters Reflex state, client storage or logs.
- Signing in rotates the session. Signing out revokes it on the server.
- Sign-in attempts are rate-limited per email and per client, stored in SQLite.
- The session endpoint and the WebSocket both check that Origin matches Host.

### Password recovery (recovery keys)

Each account can hold one **single-use recovery key**, issued at first-admin bootstrap and replaceable in **Settings → Account → Recovery Key** (requires the current password). Only the key's SHA-256 hash is stored; the raw key is shown once and saved by the owner as a recovery-kit text file (per-user data directory, owner-only permissions, or a browser download). On `/login`, **Forgot password?** accepts the key and a new password via `POST /api/session` `action=recover`: the key is consumed atomically, the Argon2id hash replaced, all sessions revoked, and a replacement key returned once in the response. Failures return a generic "Invalid recovery key" and are rate-limited per client; keys and passwords never enter audit metadata or logs. If both password and key are lost, the offline `scripts/recover_admin_password.py` remains the last resort.

### Authorization (`authz.py`)

Deny by default. Store access comes only from explicit assignments, never from matching zone names.

| Role | Can do |
|---|---|
| Super Admin | Everything, all stores. Users, roles, store access, backup and restore. |
| Administrator | Assigned stores: all records, Monthly Setup and budgets, finalize and correct reports |
| Zone Manager | Assigned stores: daily, stock, expenses, actions. Read reports. |
| Branch Manager | Assigned stores: daily, stock, expenses, actions. Read reports. |
| Viewer | Assigned stores: read only |

Payroll figures never enter page state unless the user can manage configuration (Super Admin or Administrator). A deactivated store becomes read-only but keeps all its history; authorized users can still view its payroll figures, but payroll editing controls are hidden.

### Environment settings

| Variable | Default | Purpose |
|---|---|---|
| `DUCKLAB_DB_PATH` | `ducklab/store_ops.db` | Database file. The desktop app sets it to `~/.local/share/opscore/store_ops.db`. |
| `DUCKLAB_SECURE_COOKIES` | off | Marks the session cookie Secure (HTTPS only). Server deployments only. |
| `DUCKLAB_TRUST_PROXY` | off | Trust `X-Forwarded-For` from a local reverse proxy for rate limiting and logs. |
| `DUCKLAB_FRONTEND_ORIGINS` | empty | Extra Origins allowed to call `/api/session`. Development only. |

None of these are needed for the desktop app. There are no secret keys: session tokens are random and checked on the server.

### Privacy

- Each user's data stays in their own database on their own computer.
- Company concept names are replaced with aliases in code and data.
- Real workbook data used for tests lives in the git-ignored `tests/private/`.
- The desktop build fails if any `.db` file would be included in the package.

## 8. Reference data import

`dsc_import.py` reads the Daily Sales Contribution workbook by column position:

- **DSC sheet:** State, Concept, day type (Mon to Sun or PH), and each month's share from November to October.
- **SPR sheet:** average BV and UPT by Concept and State for each month (the workbook labels these SPR and IPR).

Blank State or Concept cells under a merged group take the value above. Both tables are replaced in one transaction: the whole file imports or nothing changes. Import supports database schemas v5 through v9 as well as v10; on pre-v10 databases it updates `reference_data_info` without assuming the later `bundled` column exists. The same function powers **Settings → Reference Data** and `scripts/import_dsc.py`. The upload handler checks the global configuration permission before reading the file and rejects uploads larger than 64 MiB.

The Git-tracked default workbook `ducklab/data/DSCSPR_alias_2027.xlsx` is loaded by `reference_bootstrap.py` only when both reference tables are empty. It is bundled into `.deb` and Windows builds.

## 9. Backup and restore (`backup.py`)

- **Backup** uses SQLite's online backup API, so it's safe while the app is running.
- **Restore** checks a temporary copy of the uploaded file: it must be a real SQLite database, pass integrity and foreign-key checks, contain the required tables and columns, and not be from a newer schema. Older schemas are migrated on the temporary copy, which atomically replaces the live database only after all checks pass.
- Before every restore, the current database is copied into `backups/` next to it.

Super Admin only.

## 10. Desktop app

### Package layout

| Path | Contents |
|---|---|
| `/opt/opscore/python` | Bundled standalone Python 3.14 with all dependencies |
| `/opt/opscore/ducklab` | App code |
| `/opt/opscore/ducklab/data/DSCSPR_alias_2027.xlsx` | Bundled default reference workbook |
| `/opt/opscore/.web/build` | Prebuilt frontend |
| `/opt/opscore/packaging` | Launcher and backend scripts |
| `/usr/share/applications/opscore.desktop` | App menu entry |
| `/usr/share/icons/hicolor/*/apps/opscore.png` | Icons |

### Runtime

1. Clicking the icon runs `opscore-launcher`, which runs `packaging/opscore_desktop.py`.
2. The launcher takes a lock file so only one copy runs. A second click brings the open window to the front.
3. It starts the backend on `127.0.0.1:47811`, waits for `/api/health`, then opens a native pywebview window.
4. Closing the window stops the whole backend process group.

`rxconfig.py` switches to port 47811 only when `OPSCORE_DESKTOP=1`, so development still uses the normal ports.

### User data

| File | Path |
|---|---|
| Database | `~/.local/share/opscore/store_ops.db` |
| Log | `~/.local/share/opscore/opscore.log` |
| Pre-restore copies | `~/.local/share/opscore/backups/` |

The data folder is created with mode 0700. Installing or removing the package never touches it.

### Build (`scripts/build_deb.sh`)

1. Downloads nFPM and a standalone Python 3.14 through uv.
2. Installs `requirements.lock` into the bundled Python.
3. Exports the frontend for port 47811 and checks no other port remains.
4. Removes caches, uploads and any database, and fails if a `.db` file is found.
5. Builds `dist/opscore_<version>_amd64.deb`.

Depends on GTK 3 and WebKit2GTK 4.1. Supports Ubuntu 22.04 and newer.

## 11. Testing

| Kind | Where | Notes |
|---|---|---|
| Unit and integration | `tests/` | Disposable databases; current results are recorded in `CHANGELOG.md` |
| KPI rules | `test_kpi_synthetic.py`, `test_ops_levels_synthetic.py`, `test_daily_targets_t1.py` | Hand-made cases for every rule |
| Security | `test_security.py`, `test_auth.py`, `test_ops_payroll_policy.py` | Roles, sessions, payroll visibility |
| Packaging | `test_source_desktop.py`, `test_windows_build.py`, `test_health.py` | Desktop launcher, build and startup checks |
| Browser | `scripts/browser_test_*.py` | Playwright checks against seeded databases |
| Parity | `scripts/import_workbook_dev.py --verify` | App against the real workbook |

Every pull request must pass the full suite and a production Reflex compile with a disposable database.

## 12. Repository layout

```
ducklab/       App code (see section 3)
packaging/     Desktop launcher, backend runner, .desktop entry, nFPM config, install scripts
scripts/       Build, import, seed and browser-test tools
docs/          Project documents, migration policy and screenshots
tests/         Test suite
assets/        Logo, icons, favicon, tour scripts and styles
rxconfig.py    Reflex config (theme, desktop port)
```
