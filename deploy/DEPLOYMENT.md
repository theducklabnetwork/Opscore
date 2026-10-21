# DuckLab Operations — Production Deployment Procedure

Target: **https://app.theducklab.com/** on DuckVM (Ubuntu 26.04, Caddy,
systemd, SQLite). Operator runbook for the approved cutover.

> **Ordering is safety-critical.** The Caddy maintenance gate is installed and
> ACTIVATED **before** the DuckLab service is started on port 3000, so the app
> is never publicly reachable without the gate. `deploy/operator-install.sh`
> performs steps 1–5; you then bootstrap and start the gated service.

## Architecture (chosen)

**Reflex production single-port** (`reflex run --env prod --single-port`).
The granian backend serves the compiled frontend, `/api/*`, the `/_event`
WebSocket, and static assets on one loopback port (127.0.0.1:3000). Caddy
reverse-proxies the public domain to that single port — no path splitting;
WebSocket upgrade is automatic.

## Deployment layout

- **Running app**: a controlled copy at `/opt/ducklab` (owned `root:ducklab`,
  world-inaccessible). The original checkout under `/home/duck/project/ducklab`
  is preserved untouched and is NOT used by the running service.
  *Superseded for future releases by the versioned layout — see
  `deploy/RELEASES.md` and `deploy/OPERATOR-RELEASE.md`. The `/opt/ducklab`
  installation remains the live production deployment until the first
  versioned activation is approved.*
- **Service identity**: dedicated non-root user `ducklab`.
- **Persistent DB**: `/var/lib/ducklab/store_ops.db` (owned `ducklab`, 750).
- **Backups**: `/var/lib/ducklab/backups` (owned `ducklab`, 700; files 600),
  written by `ducklab-backup.service` running as `ducklab`.
- **Config**: `/etc/ducklab/ducklab.env` (mode 640 `root:ducklab`).

## Execution sequence

### Step A — privileged install (one command, runs the gate-first sequence)

```bash
cd /home/duck/project/ducklab
sudo bash deploy/operator-install.sh
```

The installer, in order: preflight checks (port free, no conflicting service,
existing DB preserved) → creates `ducklab` user → creates `/var/lib/ducklab`
and `/var/lib/ducklab/backups` → deploys the app copy to `/opt/ducklab` with
narrow permissions → installs/validates `/etc/ducklab/ducklab.env` (preserving
an existing one) → installs systemd units + backup script → prompts for the
operator IP and gate password (interactive, never on argv) → builds, validates,
and atomically installs the **gated** Caddyfile (previous one backed up) →
**reloads Caddy to activate the gate** → verifies the gate responds.

It does **not** start the app.

### Step B — bootstrap the first Super Admin (before starting the app)

```bash
cd /opt/ducklab
sudo -u ducklab DUCKLAB_DB_PATH=/var/lib/ducklab/store_ops.db \
  /opt/ducklab/.venv/bin/python scripts/bootstrap_admin.py
```

Interactive (password not echoed), refuses if a Super Admin exists, no default
credentials, nothing logged.

### Step C — start the gated service

```bash
sudo systemctl enable --now ducklab
journalctl -u ducklab -f
curl -s http://127.0.0.1:3000/ping        # -> "pong" (loopback only)
```

### Step D — verify behind the gate

From the allowlisted operator IP (or with the gate credential), open
`https://app.theducklab.com/`. Non-allowlisted visitors receive an **HTTP 401**
Basic-auth challenge (`WWW-Authenticate: Basic realm="restricted"`).

### Step E — enable backups

```bash
sudo systemctl enable --now ducklab-backup.timer
sudo systemctl start ducklab-backup.service     # one manual run
ls -l /var/lib/ducklab/backups/
```

## Maintenance gate behavior

- Allowlisted operator IP → passes through to the app.
- Any other client → HTTP 401 Basic-auth challenge; valid `operator`
  credential grants access. (401, not 403.)
- The gate is persistent in `/etc/caddy/Caddyfile` and survives Caddy restarts.

## Public cutover (ONLY after explicit approval)

1. Edit `/etc/caddy/Caddyfile`: remove the `@blocked` matcher and the
   `basic_auth` block.
2. `sudo caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile`
3. `sudo cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.$(date -u +%s)`
4. `sudo systemctl reload caddy`

## Rollback

See `deploy/ROLLBACK.md`. The previous Caddyfile is preserved at
`/etc/caddy/Caddyfile.bak.<timestamp>`; reinstall it and `systemctl reload
caddy`. The live DB is never overwritten by rollback.
