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

**Schema version: 11** (`SCHEMA_VERSION` in `ducklab/store_ops_db.py`).

You can see the schema version of a running installation in **Settings →
About**.

## 4. Migration architecture

### Version tracking

Two mechanisms track the schema version:

- **`PRAGMA user_version`** — the SQLite-native version stamp. This is the
  **source of truth** the migration runner reads to decide which steps to
  apply.
- **`schema_migrations`** — an audit table with one row per applied version
  (`version`, `applied_at`). It is written but never read for decisions.

The two are written together inside each migration's transaction, so they
cannot drift under normal operation.

### The runner

`init_db(db_path)`:

1. Reads `PRAGMA user_version`.
2. Iterates a static, ordered list `(1, _apply_v1) … (11, _apply_v11)`.
3. Skips versions `<= current`.
4. For each pending version, runs `BEGIN → apply → PRAGMA user_version = N →
   COMMIT`, rolling back the whole step on any error.
5. Re-running after success is a no-op.

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

`auth_db.init_auth(db_path)` runs **after** `store_ops_db.init_db()`. It
creates the `auth_*` tables with `CREATE TABLE IF NOT EXISTS`, seeds the five
roles, and applies additive column migrations (for example `tour_done`) via
column-existence probes. This is **additive and idempotent but unversioned**:
there is no auth-specific version counter. The `auth_login_failures`
rate-limiting table is created lazily on first use.

`AuthService.__init__` calls `init_auth`, and every `AppService` construction
ensures both base and auth schema exist.

## 5. Migration naming and ordering conventions

- Each migration is a module-level function `_apply_vN(conn)` in
  `ducklab/store_ops_db.py`.
- The version constant `SCHEMA_VERSION` is the ceiling.
- Each migration inserts its own `schema_migrations` audit row with
  `INSERT OR IGNORE`.
- Steps are appended; existing migration functions are never edited after
  release.

## 6. Schema history

The complete v1–v11 history, from the migration functions in
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

## 7. Migration execution process

Migrations run automatically; no operator action is required.

1. On app start, importing the application constructs `AuthService`, which
   calls `init_auth`, which calls `init_db`. Pending base migrations apply
   **at import/startup time**, before any request is served.
2. Two Reflex lifespan tasks then run:
   - `seed_default_expense_categories` — inserts the ten default categories
     only for stores with zero categories.
   - `load_bundled_reference_data` — imports the bundled workbook only when
     both reference tables are empty. Failures are logged and non-fatal.

On a desktop upgrade, the first launch of the new version applies pending
migrations to the existing database in place.

## 8. Upgrade procedures

### Recommended upgrade steps

1. **Back up first.** In **Settings → Data → Download Backup**, download a
   copy of the database and keep it somewhere safe. There is **no automatic
   pre-migration backup** on a routine upgrade, so this manual backup is your
   safety copy.
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
     databases are rejected with "upgrade the app first").
3. Run `init_db` on the copy to migrate it forward, then re-validate.
4. `os.replace` atomically swaps the live database for the validated copy.

Before the replace, `save_pre_restore_backup` copies the current database to
`backups/pre-restore-<timestamp>.db` next to it. The UI lists the newest
five. Any failure before `os.replace` leaves the live database untouched.

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
- **Older code vs newer schema.** The restore path rejects databases newer
  than the app supports, but ordinary startup does **not** refuse to open a
  newer database — `init_db` simply no-ops when `user_version >
  SCHEMA_VERSION`. Do not run older application code against a newer
  database without an explicit compatibility review.

## 11. Recovery procedures

| Artifact | Created by | Location |
|---|---|---|
| Manual backup | **Settings → Data → Download Backup** | Downloaded file you keep |
| Pre-restore backup | Restore flow | `backups/pre-restore-<timestamp>.db` next to the database |
| Numbered fresh-start copy | Keep/fresh choice → Start fresh | `store_ops.db.backup-N` next to the database |
| Recovery-script backup | `scripts/recover_admin_password.py` | `<db>.bak-<timestamp>` next to the database |

To undo a restore, restore the relevant pre-restore backup through the same
**Settings → Data** flow.

To inspect a database while Opscore is closed, use a SQLite tool to run
`PRAGMA integrity_check`, `PRAGMA user_version`, and query `schema_migrations`.
The expected integrity result is `ok`, and the version and migration rows
should match the installed application. Always back up before manual database
work.

## 12. Known limitations

- **No automatic pre-migration backup** on a routine upgrade. The manual
  download in **Settings → Data** is the only pre-upgrade safety copy.
- **No startup guard against older code opening a newer database.** Only the
  restore path rejects newer-schema files.
- **Unversioned authentication schema.** `auth_db` uses idempotent,
  additive-only initialization with column probes rather than a version
  counter; `auth_login_failures` is created lazily outside any migration.
- **Dual version bookkeeping is not cross-checked.** `user_version` drives
  decisions; `schema_migrations` is audit metadata only.
- **v4 and v11 are destructive** by design (reference-data rebuild; fixed-RM
  budgets cleared in favour of percentages) and are the sanctioned
  exceptions to the additive-only rule.
- **No WAL or explicit busy-timeout tuning.** SQLite defaults are used, which
  suits the single-user desktop model.

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

These are recommendations, not current behavior:

- Take an automatic backup before applying pending migrations at startup
  (reuse `backup.create_backup`).
- Refuse to start when `user_version > SCHEMA_VERSION`.
- Add a version counter for the authentication schema.
- Cross-check `schema_migrations` against `user_version` at startup.
