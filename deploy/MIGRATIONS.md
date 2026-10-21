# DuckLab — Database Migration Safety

The store-ops database (`/var/lib/ducklab/store_ops.db`) is versioned by
`PRAGMA user_version` plus a `schema_migrations` audit table. Current
application schema version: **1** (`ducklab/store_ops_db.py: SCHEMA_VERSION`).

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
