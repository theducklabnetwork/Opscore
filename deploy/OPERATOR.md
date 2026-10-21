# DuckLab Operations — Operator Quick Reference (post-install)

Short operational runbook for after `deploy/operator-install.sh` has been run
with sudo. Full procedure: `deploy/DEPLOYMENT.md`.

## Maintenance gate (active until public launch is approved)

`https://app.theducklab.com/` is gated. The gate was activated BEFORE the app
started, so the app was never publicly exposed.

- **Allowlisted operator IP** → passes straight through to the app.
- **Any other client** → receives an **HTTP 401** Basic-auth challenge
  (`WWW-Authenticate: Basic realm="restricted"`); a valid `operator`
  credential grants access. (401 challenge, not 403.)
- **Gate credential**: chosen interactively during install; only its bcrypt
  hash is stored in `/etc/caddy/Caddyfile`.
- **Change operator IP**: edit the `remote_ip` matcher in
  `/etc/caddy/Caddyfile`, then `sudo caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile`
  and `sudo systemctl reload caddy`.

## Layout

- Running app: `/opt/ducklab` (controlled copy; source checkout untouched)
- Service user: `ducklab` (non-root)
- DB: `/var/lib/ducklab/store_ops.db` · Backups: `/var/lib/ducklab/backups`
- Config: `/etc/ducklab/ducklab.env`

## Service control

```bash
sudo systemctl status ducklab
sudo systemctl restart ducklab
journalctl -u ducklab -f
```

## Backups

```bash
sudo systemctl start ducklab-backup.service      # run one now
ls -l /var/lib/ducklab/backups/
sudo systemctl enable --now ducklab-backup.timer # nightly 02:15
```

Restore: see `deploy/RECOVERY.md`. Never restore over the live DB without a
fresh safety copy.

## Updating the app

```bash
cd /home/duck/project/ducklab && git pull
# re-sync the deployment copy, then restart:
sudo rsync -a --delete --exclude '.git/' --exclude '.web/' \
  --exclude 'store_ops.db' --exclude 'placement_tracker.db' \
  ducklab rxconfig.py assets scripts deploy /opt/ducklab/
sudo systemctl restart ducklab
```

## Public cutover (ONLY after explicit approval)

1. Edit `/etc/caddy/Caddyfile`: remove the `@blocked` matcher + `basic_auth`.
2. `sudo cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.$(date -u +%s)`
3. `sudo caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile`
4. `sudo systemctl reload caddy`
