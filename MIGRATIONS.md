# Opscore — Database Migrations and Upgrades

This document describes how the Opscore database evolves: the migration
mechanism, the complete schema history, upgrade and restore procedures, and
the rules for adding future migrations.

For the runtime data model see [Architecture](ARCHITECTURE.md). For the
end-user backup/restore workflow see the [User Guide](USER_GUIDE.md).

---

## 1. Introduction

Opscore stores everything — operational records, accounts, sessions and
recovery-key state — in a single SQLite database per installation. The
database is created and upgraded automatically; no separate migration tool or
framework is required.

Opscore has a real, ordered migration mechanism, not just schema
initialization. It is implemented in `ducklab/store_ops_db.py` and uses the
standard SQLite `PRAGMA user_version` pattern.

## 2. Database locations

| Context | Location |
|---|---|
| Source default | `ducklab/store_ops.db` |
| Override (any context) | `DUCKLAB_DB_PATH` environment variable |
| Linux desktop | `~/.local/share/opscore/store_ops.db` |
| Windows desktop | `%LOCALAPPDATA%\Opscore\store_ops.db` |

The desktop launcher sets `DUCKLAB_DB_PATH` to the per-user data directory
before starting the backend. Build scripts compile the frontend against a
disposable database and refuse to package any `.db` file.

The authentication tables live in the **same** database file as operational
data. There is no separate auth database.

## 3. Current schema version

**Schema version: 15** (`SCHEMA_VERSION` in `ducklab/store_ops_db.py`).

You can see the schema version of a running installation in **Settings →
About**.

## 4. Migration architecture

### Version tracking

Two mechanisms track the schema version:

- **`PRAGMA user_version`** — the SQLite-native version stamp. This is the
  **source of truth** the migration runner reads to decide which steps to
  apply.
- **`schema_migrations`** — an audit table with one row per applied version
  (`version`, `applied_at`). It does not choose which migrations run, but is
  cross-checked before changes: version N must have exactly rows 1 through N.

Missing versions, unexpected/future versions and a missing audit table for a
nonzero version are rejected with an actionable consistency error before a
backup, migration or restore. A version-zero database initializes normally.

### The runner

`init_db(db_path)`:

1. Reads `PRAGMA user_version`, refuses a version newer than the app, then
   validates that `schema_migrations` contains exactly versions `1…N`.
2. If an existing database has pending migrations, saves a consistent
   pre-migration copy before the first transaction. Backup failure aborts
   migration without changing the live database.
3. Iterates a static, ordered list `(1, _apply_v1) … (15, _apply_v15)`.
4. Skips versions `<= current`.
5. For each pending version, runs `BEGIN → apply → PRAGMA user_version = N →
   COMMIT`, rolling back the whole step on any error.
6. Re-running after success is a no-op and creates no additional backup.
7. After successful checks/migrations, controlled initialization enables WAL.
   Application and backup connections use a 10-second busy timeout and always
   enforce foreign keys. WAL is not enabled by the generic desktop preflight.

Migrations are **strictly linear and forward-only**. There are no down
migrations and no branching.

### Atomicity

Each schema change and its version stamp commit in one transaction. The test
suite injects a failure after every `_apply_vN` and asserts that the database
is byte-identical to its pre-attempt state and that a retry then succeeds
(`tests/test_store_ops_db.py`).

### Idempotence

Migrations use `CREATE TABLE IF NOT EXISTS` and `INSERT OR IGNORE` where
relevant, and applied versions are skipped by version number, so
initialization is idempotent and non-destructive.

### Authentication schema

Schema **v15** is the authentication baseline. The shared migration runner
creates all `auth_*` tables and indexes, seeds the five roles, adds
`auth_users.tour_done` when absent, and creates `auth_login_failures` with its
index in the same transaction. Existing accounts, roles, store assignments,
sessions, recovery keys, completed tours and login-failure records survive the
v14 → v15 upgrade. Initialization remains additive and idempotent.

`auth_db.init_auth()` is a thin compatibility wrapper around `init_db()`; there
is no second schema definition. `AuthService.__init__` calls it, and persistent
rate limiting assumes the initialized v15 schema. Isolated unit tests can still
use the in-memory limiter backend.

## 5. Migration naming and ordering conventions

- Each migration is a module-level function `_apply_vN(conn)` in
  `ducklab/store_ops_db.py`.
- The version constant `SCHEMA_VERSION` is the ceiling.
- Each migration inserts its own `schema_migrations` audit row with
  `INSERT OR IGNORE`.
- Steps are appended; existing migration functions are never edited after
  release.

## 6. Schema history

The complete v1–v15 history, from the migration functions in
`ducklab/store_ops_db.py`:

| Version | Change | Type |
|---|---|---|
| **v1** | Initial schema: `schema_migrations`, `stores`, `expense_categories`, `stock_loss_codes` (13 seeded codes), `monthly_configs`, `monthly_expense_budgets`, `daily_records`, `daily_stock_losses`, `expense_transactions`, `actions`, `monthly_snapshots`. | Baseline |
| **v2** | `actions` gains `created_by`, `created_at`, `updated_by`, `updated_at` audit columns. Existing rows keep NULL audit data. | Additive |
| **v3** | `stores.state`; `monthly_configs` gains `target_transactions`, `target_spr_cents`, `target_ipr`; new tables `public_holidays`, `contribution_shares`, `basket_benchmarks` (single-value). Existing rows keep NULL. | Additive |
| **v4** | `basket_benchmarks` becomes monthly (concept, state, month). The v3 single-value table was never read and holds only re-importable reference data, so it is **dropped and recreated** — the sole destructive step. Re-import the DSC workbook afterwards. | Rebuild (sanctioned exception) |
| **v5** | `reference_data_info` singleton records the last DSC import timestamp. | Additive |
| **v6** | New tables `staff` (store-scoped), `shift_codes` (shared, seeded), `schedule_entries`, `schedule_targets`. | Additive |
| **v7** | Soft default colors for the standard shift codes. Only codes still using the v6 default colors are updated; customized colors are preserved. | Data update |
| **v8** | `staff` gains `employment_type` (`Full-Time` / `Part-Time` / `Not Set`, default `Not Set`). | Additive |
| **v9** | Index `idx_actions_store_raised_date` on `actions (store_id, raised_date)` for month-range queries. | Additive |
| **v10** | `reference_data_info.bundled` records whether the current reference tables came from the bundled workbook. DSC import stays compatible with v5–v9 by feature-detecting the column. | Additive |
| **v11** | `monthly_expense_budgets.budget_cents` (fixed RM) is replaced by `budget_pct` (a fraction of the active sales target, e.g. 0.025 = 2.5%). Existing fixed-RM budgets are cleared; the bundled default percentages are then seeded by the startup lifespan task. Finalized snapshots keep their historical RM figures. | Rebuild (sanctioned exception) |
| **v12** | `daily_records.leave_emergency` stores actual Emergency Leave. `schedule_entries.assignment_source` distinguishes manual assignments from automatic ABM public-holiday OPH. Seeds RPL and changes only known default M900–A121 colours to white, preserving custom labels/times/colours. | Additive + safe data update |
| **v13** | `shift_codes.show_in_legend` controls inclusion in the generated View legend. Existing codes default to included. | Additive |
| **v14** | Configured public holidays replace all ABM assignments with automatic OPH, including existing manual shifts. Other staff and non-holiday assignments are unchanged. | Safe data update |
| **v15** | Versioned authentication baseline: all auth tables/indexes, five roles, additive `tour_done`, and persistent `auth_login_failures`. Existing auth records are preserved. | Additive baseline |

## 7. Migration execution process

Migrations run automatically; no operator action is required.

1. On app start, importing the application constructs `AuthService`, whose
   compatibility `init_auth` calls `init_db`. Pending base/auth migrations apply
   **at import/startup time**, before any request is served.
2. Two Reflex lifespan tasks then run:
   - `seed_default_expense_categories` — inserts the ten default categories
     only for stores with zero categories.
   - `load_bundled_reference_data` — imports the bundled workbook when both
     reference tables are empty, and refreshes it when the stored data still
     came from the bundle but its content no longer matches the bundled
     workbook (an upgrade). User-imported data is never replaced. Failures
     are logged and non-fatal.

On a desktop upgrade, the first launch of the new version applies pending
migrations to the existing database in place.

## 8. Upgrade procedures

### Recommended upgrade steps

1. **Back up first.** In **Settings → Data → Download Backup**, download a
   copy of the database and keep it somewhere safe. Opscore also creates an
   automatic local copy before pending migrations, but that copy is beside
   the live database and is not a substitute for an off-device backup.
2. Install the new package.
3. On first launch, choose to keep your existing database (the default) when
   asked. Pending migrations apply automatically and preserve records and
   accounts.

### Desktop keep/fresh choice

On the first launch of a newly installed version, if a database exists and
`app_version.txt` does not yet record the new version, the launcher offers a
choice:

- **Use existing database** (default) — keeps the database; migrations apply.
- **Start fresh (backup kept)** — renames the database to
  `store_ops.db.backup-N` (the lowest free N) and starts with a new database.

The Windows installer records this choice in a one-shot `db-choice.json`;
otherwise a GTK dialog asks (with a zenity fallback). The choice is offered
once per installed version.

## 9. Backup and restore

### Backup

`backup.create_backup` uses SQLite's **online backup API**, producing a
consistent copy while the app is running. The UI downloads it as
`opscore-backup-YYYY-MM-DD-HHMM.db`.

### Restore

`backup.restore` never touches the live database until every check passes:

1. Copy the uploaded file to a temporary file **in the same directory** as
   the live database (so the final replace is atomic).
2. Validate the copy:
   - SQLite file signature.
   - `PRAGMA integrity_check` must return `ok`.
   - `PRAGMA foreign_key_check` must return no violations.
   - Required tables and columns must be present.
   - `user_version` must not exceed the app's `SCHEMA_VERSION` (newer
     databases are rejected with "upgrade the app first"), and the audit table
     must exactly match versions 1 through that version.
3. Run `init_db(..., backup_before_migration=False)` on the copy to migrate
   it forward without a redundant temporary-copy backup, then re-validate.
4. SQLite's online backup API normalizes the self-contained upload into a
   private copy without trusting uploaded sidecars. Backups created by Opscore
   already include all committed WAL content.
   The live database is checkpointed and quiesced, old WAL/SHM state is removed,
   and `os.replace` atomically swaps in the validated copy. Open connections
   must finish and close; otherwise replacement fails without mixing old WAL
   state into the restored database.

Before the replace, `save_pre_restore_backup` copies the current database to
`backups/pre-restore-<timestamp>.db` next to it. Pre-migration and pre-restore
copies share a newest-10 retention policy and appear together in the UI.
Only recognized automatic filenames are pruned. Any failure before
`os.replace` leaves the live database untouched.

Because authentication lives in the same database, **a restore also replaces
users, roles, password hashes, sessions and recovery-key state**, and the
current session is signed out.

## 10. Data preservation and downgrade limits

- **Upgrades preserve data.** Migrations are additive (with the documented
  v4 and v11 exceptions) and preserve existing records.
- **No down migrations exist.** Reinstalling an older application version
  restores only the code; it does not reverse the database schema.
- **Downgrading requires a backup.** The only way back to an older schema is
  restoring an older database backup through **Settings → Data**.
- **Older code vs newer schema.** Restore and ordinary startup reject a
  `user_version` newer than `SCHEMA_VERSION` before any migration or backup.
  Desktop startup also displays the actionable incompatibility message. An
  older application cannot modify a newer database.

## 11. Recovery procedures

| Artifact | Created by | Location |
|---|---|---|
| Manual backup | **Settings → Data → Download Backup** | Downloaded file you keep; never pruned |
| Pre-migration backup | Pending startup migration | `backups/pre-migration-vN-to-vM-<timestamp>.db`; shared newest-10 retention |
| Pre-restore backup | Restore flow | `backups/pre-restore-<timestamp>.db`; shared newest-10 retention |
| Numbered fresh-start copy | Keep/fresh choice → Start fresh | `store_ops.db.backup-N` next to the database; newest 10 retained after successful creation |
| Old WebView profile | Desktop version/profile replacement | `webview.old-<timestamp>`; newest 3 retained during startup |
| Recovery-script backup | `scripts/recover_admin_password.py` | `<db>.bak-<timestamp>` next to the database |

To undo a restore, restore the relevant pre-restore backup through the same
**Settings → Data** flow.

To inspect a database while Opscore is closed, use a SQLite tool to run
`PRAGMA integrity_check`, `PRAGMA user_version`, and query `schema_migrations`.
The expected integrity result is `ok`, and the version and migration rows
should match the installed application. Always back up before manual database
work.

## 12. Known limitations

- **Automatic backups are local.** The newest 10 pre-migration and
  pre-restore copies are retained beside the database; keep a separate
  downloaded backup for device-loss protection.
- **v4 and v11 are destructive** by design (reference-data rebuild; fixed-RM
  budgets cleared in favour of percentages) and are the sanctioned
  exceptions to the additive-only rule.
- **WAL with bounded lock waiting.** Controlled initialization enables WAL and
  connections wait up to 10 seconds for locks. Restore still requires active
  connections to finish/close before the live file can be replaced safely.
- **Generated-artifact cleanup is bounded and selective.** Exact-name,
  non-symlink pre-migration/pre-restore and fresh-start backups retain 10;
  replaced WebView profiles retain 3. Manual downloads and recovery-script
  backups are never deleted automatically. Pruning failures are non-fatal.

## 13. Developer guide: adding a future migration

Follow these rules when raising `SCHEMA_VERSION`:

1. **Additive-only and idempotent.** Never drop, rename or retype existing
   columns or tables in a routine migration. The v4 reference-data rebuild is
   the only sanctioned exception.
2. **Bump `SCHEMA_VERSION`** in `ducklab/store_ops_db.py` to the new number.
3. **Append one migration function** `_apply_vN(conn)` and register it in the
   ordered `migrations` tuple in `init_db`. Do not edit existing migration
   functions.
4. **Insert the audit row** inside the migration with `INSERT OR IGNORE INTO
   schema_migrations (version) VALUES (N)`.
5. **Keep it atomic.** The runner already wraps each step and its
   `PRAGMA user_version` update in one transaction; do not add explicit
   commits inside the migration function.
6. **Add fault-injection test coverage** following the existing pattern in
   `tests/test_store_ops_db.py`: start from the previous schema, apply the
   migration, verify records survive, and verify rollback on failure.
7. **Non-additive migrations are breaking changes.** They require a written
   migration and recovery plan before release, plus testing against a copy of
   an existing desktop database.
8. **Update this document** with the new version and its change, and record
   the schema bump in the [Changelog](CHANGELOG.md).

### Recommendations for future hardening

Keep future migrations additive, transactional and covered by upgrade/failure
fixtures, including WAL-enabled databases and realistic authentication records.
