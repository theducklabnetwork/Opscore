# DuckLab Operations — Production Verification Checklist

Run after the approved cutover. All against **https://app.theducklab.com/**.

## Infrastructure
- [ ] `systemctl status ducklab` → active (running), enabled
- [ ] `journalctl -u ducklab` → no errors, granian listening on 127.0.0.1:3000
- [ ] `curl -s http://127.0.0.1:3000/ping` → `"pong"` (loopback only)
- [ ] Port 3000 NOT reachable from outside (`ss -tlnp` shows 127.0.0.1 binding)
- [ ] `systemctl is-enabled ducklab` → enabled (survives reboot)

## HTTPS / TLS
- [ ] `https://app.theducklab.com/` serves a valid Let's Encrypt cert
- [ ] `http://app.theducklab.com/` redirects to HTTPS
- [ ] No mixed-content warnings in browser devtools

## Authentication
- [ ] Login page renders at `/login`
- [ ] Login with the bootstrapped Super Admin succeeds
- [ ] Session cookie is `HttpOnly; Secure; SameSite=Lax; Path=/`
- [ ] `document.cookie` does NOT contain `ducklab_session`
- [ ] Failed login shows generic error (no enumeration)
- [ ] Logout clears the cookie and redirects to `/login`
- [ ] Reload preserves the session
- [ ] Cross-origin POST to `/api/session` is rejected (403)

## Application
- [ ] `/ops` dashboard renders with real (empty) production data
- [ ] `/ops/daily`, `/ops/expenses`, `/ops/report` load
- [ ] WebSocket connects (no console errors; live updates work)
- [ ] Legacy routes `/placements`, `/tasks`, `/report` return 404
- [ ] `/` redirects authenticated users to `/ops` and unauthenticated users to `/login` (no business data at the root)

## RBAC
- [ ] Viewer account cannot write (server-side rejection)
- [ ] Store-level isolation enforced

## Backups
- [ ] `systemctl start ducklab-backup.service` succeeds
- [ ] Backup file appears in `/var/lib/ducklab/backups/` mode 600
- [ ] `systemctl list-timers ducklab-backup.timer` shows schedule
- [ ] Restore test on a disposable copy passes `PRAGMA integrity_check`

## Security config
- [ ] `/etc/ducklab/ducklab.env` is mode 640 root:ducklab
- [ ] `DUCKLAB_SECURE_COOKIES=1`, `DUCKLAB_TRUST_PROXY=1`
- [ ] `DUCKLAB_FRONTEND_ORIGINS` is unset
- [ ] No secrets in any config file or the repo
