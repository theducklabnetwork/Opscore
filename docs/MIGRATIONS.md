# Opscore — Database Migration Safety

The store-ops database is versioned by `PRAGMA user_version` plus a
`schema_migrations` audit table. Each desktop installation keeps its own
database in the per-user location documented in `README.md`. Current
application schema version: **10** (`ducklab/store_ops_db.py: SCHEMA_VERSION`).

## Version history

- **v1** — initial schema (stores, daily, expenses, actions, snapshots).
- **v2** — additive: `actions` gains `created_by`, `created_at`,
  `updated_by`, `updated_at` audit columns for the Action Tracker page.
  Existing rows keep NULL audit data; no KPI-relevant columns change.
- **v3** — additive: `stores.state`; `monthly_configs` gains
  `target_transactions`, `target_spr_cents`, `target_ipr`; new tables
  `public_holidays` (per store/date), `contribution_shares` (Daily Sales
  Contribution by State + Concept + Day + calendar month) and
  `basket_benchmarks` (12-month average SPR / IPR). Existing rows keep NULL;
  existing store concepts are kept until the store is edited. The two
  reference tables are filled once with `scripts/import_dsc.py <xlsx>`
  (requires `DUCKLAB_DB_PATH`; replaces both tables in one transaction).
- **v4** — `basket_benchmarks` becomes monthly: (concept, state, month,
  avg_spr, avg_ipr), matching the workbook's November..October SPR / IPR
  columns. The v3 single-value table was never read by the app and holds
  only re-importable reference data, so it is dropped and recreated (the
  one sanctioned exception to rule 1); no other table changes. Re-run
  `scripts/import_dsc.py <xlsx>` after this migration to fill it.
- **v5** — additive: `reference_data_info` singleton row records the last
  DSC import timestamp; written by `ducklab/dsc_import` inside the same
  transaction that replaces the reference tables (shown on Settings >
  Reference Data, and importable there too). No existing table changes.
- **v6** — additive: store-scoped `staff`, shared `shift_codes`, dated
  `schedule_entries`, and per-store/week `schedule_targets` for Manpower
  Efficiency scheduling. Seeds the standard shift codes; no staff are seeded.
- **v7** — soft default colors for the standard shift codes. Only codes still
  using the v6 default colors are updated; customized colors are preserved.
- **v8** — additive: `staff` gains `employment_type` (`Full-Time` /
  `Part-Time` / `Not Set`, default `Not Set`) for schedule-staffing
  integration. Existing staff keep `Not Set`; no other table changes.
- **v9** — additive: index `idx_actions_store_raised_date` on
  `actions (store_id, raised_date)` for half-open month-range queries.
  No data changes.
- **v10** — additive: `reference_data_info.bundled` records whether the
  current reference tables came from the bundled workbook. The startup
  loader imports that workbook only when both reference tables are empty;
  an explicit upload clears the flag. Existing reference data is unchanged.
  DSC import remains compatible with v5 through v9 by detecting whether the
  `bundled` column exists before writing reference-data metadata.

## Desktop upgrades

Before installing a desktop release that raises the schema version, download a
backup in **Settings → Data**. The migration runs automatically on first launch
and preserves existing data. Reinstalling older application code does not
reverse a database migration.

## Rules

1. **Migrations are additive-only and idempotent.** `init_db()` runs at app
   startup and applies pending steps in ascending order; it never drops or
   rewrites existing data except for the documented v4 reference-data rebuild.
   Each migration runs in one transaction, including its `PRAGMA user_version`
   update, so a failed step rolls back completely and can be retried.
   `SCHEMA_VERSION` in code is the ceiling.
2. **Back up before any schema change.** Use **Settings → Data → Download
   Backup** before installing a release that raises `SCHEMA_VERSION`.
3. **Downgrades never restore a database automatically.** Reinstalling older
   application code restores only the application. Restoring an old database
   backup is a separate, deliberate operation through **Settings → Data**.
4. **Older code versus newer schema.** Older application code must not be used
   with a newer database schema without an explicit compatibility review.
5. **Non-additive migrations** (drop/rename/retype a column or table) are
   breaking changes. They require a written migration and recovery plan before
   release, plus testing against a copy of an existing desktop database.

## Verification

Tests and development checks must use disposable databases. Migration tests
should start with the older schema, initialize the current application, and
verify that existing records remain intact.

To inspect a desktop database while Opscore is closed, run `PRAGMA
integrity_check`, `PRAGMA user_version`, and query `schema_migrations` with a
SQLite tool. The expected integrity result is `ok`, and the schema version and
migration rows should match the installed application. Always make a backup
before manual database work.
