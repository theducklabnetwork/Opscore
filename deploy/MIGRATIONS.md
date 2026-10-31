# DuckLab — Database Migration Safety

The store-ops database (`/var/lib/ducklab/store_ops.db`) is versioned by
`PRAGMA user_version` plus a `schema_migrations` audit table. Current
application schema version: **9** (`ducklab/store_ops_db.py: SCHEMA_VERSION`).

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
  using their v6 default colors are updated; customized colors are preserved.
- **v8** — additive: `staff` gains `employment_type` (`Full-Time` /
  `Part-Time` / `Not Set`, default `Not Set`) for schedule-staffing
  integration. Existing staff keep `Not Set`; no other table changes.
- **v9** — additive: index `idx_actions_store_raised_date` on
  `actions (store_id, raised_date)` for half-open month-range queries.
  No data changes.

## Desktop upgrades

Before installing a desktop release that raises the schema version, download a
backup in **Settings → Data**. The migration runs automatically on first launch
and preserves existing data. Reinstalling older application code does not
reverse a database migration.

## Rules

1. **Migrations are additive-only and idempotent.** `init_db()` runs at app
   startup and applies pending steps in ascending order; it never drops or
   rewrites existing data. `SCHEMA_VERSION` in code is the ceiling.
2. **Backup before any schema change.** A release that raises
   `SCHEMA_VERSION` requires a verified backup (created after the backup
   service reports success) before activation. The release pipeline's
   `check_compatibility` reports pending migrations and stops activation
   until the operator confirms a backup exists.
3. **Code rollback never touches the database.** Rolling back the
   application restores only the previous code release. Restoring an old
   database backup is a separate, manual disaster-recovery decision
   (`deploy/RECOVERY.md`) and is never automated.
4. **Older code vs newer schema.** The release tooling refuses to roll back
   to code whose `SCHEMA_VERSION` is lower than the database's
   `user_version`, unless the operator has reviewed and confirmed the older
   code tolerates the newer schema (additive changes make this true in
   practice, but the check is deliberate, not assumed).
5. **Non-additive migrations** (drop/rename/retype a column or table) are
   breaking changes. They require a written migration + rollback plan
   approved by the operator *before* the release is staged, and a maintenance
   window. Do not ship one as a routine release.
6. **Integrity verification.** After any migration, run:

   ```bash
   sudo -u ducklab sqlite3 file:/var/lib/ducklab/store_ops.db?mode=ro \
       'PRAGMA integrity_check; PRAGMA user_version;
        SELECT version FROM schema_migrations ORDER BY version;'
   ```

   Expect `ok`, the new version, and a matching `schema_migrations` row.

## Release-time procedure

1. `deploy/release.sh verify-staged` runs the app against a disposable DB —
   the migration path is exercised there first.
2. Before cutover, run the compatibility check (see OPERATOR-RELEASE.md
   step 3). If it reports "will be migrated", confirm a fresh backup.
3. Activate + restart. On first start, `init_db()` applies pending
   migrations automatically.
4. Verify `user_version` and `integrity_check` (above) and the app health
   endpoint.
5. Record the outcome in the release manifest `status` field.
