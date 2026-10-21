# DuckLab Operations — Rollback Plan

Goal: restore service quickly and **non-destructively**. The live database and
the original source checkout are never overwritten by rollback.

## 1. Failed application startup

- Symptom: `systemctl status ducklab` = failed; `journalctl -u ducklab -n 100`.
- Action: fix config/code, `sudo systemctl restart ducklab`.
- If unfixable: `sudo systemctl stop ducklab`; redeploy a last-known-good copy
  to `/opt/ducklab` and restart. DB untouched.

## 2. Failed Caddy routing / TLS / gate misconfiguration

- Symptom: site unreachable, 502, cert errors, or the gate blocking legitimate
  operator access after `reload caddy`.
- Rollback (atomic, previous version preserved by the installer):
  ```bash
  sudo cp -a /etc/caddy/Caddyfile.bak.<timestamp> /etc/caddy/Caddyfile
  sudo caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile
  sudo systemctl reload caddy
  ```
- To restore the pre-deployment proxy exactly, use the backed-up Caddyfile.

## 3. Authentication failures

- Check `journalctl -u ducklab -f`. Confirm `DUCKLAB_SECURE_COOKIES=1` (HTTPS),
  `DUCKLAB_TRUST_PROXY=1`, and that the gate is passing the operator through.
- Rollback: revert to the prior app copy and restart. Server-side sessions
  persist across restart; a bad build is rolled back, not the DB.

## 4. WebSocket failures

- Pages load but live events fail: confirm the proxy forwards Upgrade headers
  (Caddy does by default) and that the WS Origin check passes (Origin host ==
  Host). Rebuild/restart the app; no destructive step.

## 5. Database errors / failed schema init

- `init_db` is idempotent and additive. Restore from backup only on corruption
  (see `deploy/RECOVERY.md`). Never overwrite the live DB with a test DB.

## 6. Unexpected service crashes

- `Restart=on-failure` + `StartLimitBurst=5` per 300s prevents restart loops.
  Inspect the journal, fix, restart.

## 7. Full cutover abort

1. `sudo systemctl stop ducklab`
2. Restore the backed-up Caddyfile + `systemctl reload caddy` (gate or prior
   config back in force).
3. Leave `/var/lib/ducklab/store_ops.db` in place (valid; a later attempt
   resumes from it). Optionally back it up first:
   `sudo systemctl start ducklab-backup.service`.

No rollback step deletes or overwrites the operational database, the source
workbook, or the original Placement Tracker checkout.
