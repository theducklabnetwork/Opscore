# DuckLab — Operator Release Runbook

This runbook is the operator-side workflow for versioned releases.
OpenCode (unprivileged) prepares and validates releases; the operator
(sudo) performs the one-time layout setup and each cutover/rollback.

**Production preservation rules:** never edit `/var/lib/ducklab`,
`/etc/ducklab/ducklab.env`, or the Caddy maintenance gate as part of a
release. The database is never restored or replaced by release tooling.

---

## Layout

```
/opt/ducklab-releases/<release-id>/   # versioned releases (app + venv + .web)
/opt/ducklab-current                  # symlink -> active release
/opt/ducklab-releases/previous        # symlink -> last known-good release
/opt/ducklab-staging/src-<ts>/        # published source snapshots (duck->ducklab)
/var/lib/ducklab/store_ops.db         # persistent DB (NEVER in a release dir)
/etc/ducklab/ducklab.env              # environment (NEVER overwritten)
/etc/ducklab/ducklab-release.env      # DUCKLAB_RELEASE_ID=<id> (updated at cutover)
```

**Ownership model:** `/opt/ducklab-releases` is owned `ducklab:ducklab`,
mode `750`. Release preparation runs **unprivileged as the service identity**
(the same non-root `ducklab` user that executes the app), NOT as root and
NOT as a developer account.

**Source publishing:** the release identity has **no access to /home/duck**.
The developer (`duck`) publishes a filtered, audited **tar archive** with
`deploy/publish-source.sh` into `/opt/ducklab-staging/src-<ts>/` — owned
`duck:ducklab` mode `2750` (setgid). Only the snapshot directory and the
`source.tar.gz` inside it need the `ducklab` group (both inherit it from
the setgid parent); **no nested tree** is created in the staging area, so
no group-inheritance dependency exists for content. The archive is audited
twice: content audit before packing (secrets/databases/venvs/caches/git/
symlinks/non-regular files) and member audit after packing (relative paths
only, no `..`, no symlinks/hardlinks/devices/fifos). `release.sh`
re-validates the archive independently and extracts it as the release
identity with `--no-same-owner --no-same-permissions`, then normalizes
modes (dirs 750, files 640, exec bits restored from the archive listing). The publisher copies ONLY an explicit include-list
(`ducklab rxconfig.py assets scripts packaging deploy tests requirements*.txt
AGENTS.md README.md`) with strict excludes (venvs, `.env*`, keys, `*.db`,
caches, git metadata, screenshots/logs) and refuses symlinks; a post-copy
audit fails the snapshot if any database, secret, cache, venv, symlink, or
credential-pattern content survives. Snapshots are created at their final
path and never moved. `release.sh` re-audits staged content (databases,
secrets, git metadata, escaping symlinks) before building.

Each release directory is immutable after verification. Its `.venv` was
created **in place** — never move a release directory after its venv exists.
The legacy `/opt/ducklab` installation is preserved untouched until the
operator retires it explicitly.

---

## ONE-TIME setup (operator, sudo)

```bash
# 1. Release root (service identity writes) and staging root (setgid:
#    duck publishes archives, ducklab group-reads).
sudo install -d -o ducklab -g ducklab -m 750 /opt/ducklab-releases
sudo install -d -o duck -g ducklab -m 2750 /opt/ducklab-staging

# 2. Trusted release CLI location. The pipeline must NOT be executed from
#    /home/duck (the release identity has no access there) or from inside a
#    snapshot (which a future publication could replace). Install the CLI
#    once; it is deliberately tiny (release.sh + release_lib.sh) and changes
#    rarely. Re-install it whenever deploy/cli/ changes (checksum shown by
#    the publisher and verified by tests).
sudo install -d -o root -g ducklab -m 750 /opt/ducklab-cli
sudo install -o root -g ducklab -m 750 deploy/cli/release.sh /opt/ducklab-cli/
sudo install -o root -g ducklab -m 640 deploy/cli/release_lib.sh /opt/ducklab-cli/

# 3. Install the versioned systemd unit (deploy/ducklab.service in the
#    prepared release), create the release-identity env file, and re-enable.
sudo install -m 644 deploy/ducklab.service /etc/systemd/system/ducklab.service
echo 'DUCKLAB_RELEASE_ID=legacy-opt-ducklab' | sudo tee /etc/ducklab/ducklab-release.env
sudo chmod 640 /etc/ducklab/ducklab-release.env
sudo chown root:ducklab /etc/ducklab/ducklab-release.env
sudo systemctl daemon-reload
```

The initial installer creates `/opt/ducklab-current` pointing at
`/opt/ducklab`; if that link is absent, do NOT restart the versioned service
until the first activation creates it. The legacy installation remains at
`/opt/ducklab`.

## Running the pipeline as the service identity

Release preparation/verification runs unprivileged **as `ducklab`**. The
release identity has **no access to `/home/duck`** — the developer publishes
a filtered snapshot first:

```bash
# Developer (duck, no sudo): publish a filtered, audited source archive.
# The snapshot path printed is what the release pipeline consumes.
deploy/publish-source.sh          # -> /opt/ducklab-staging/src-<ts>/source.tar.gz

# Release identity (ducklab, no sudo for the pipeline itself) — the CLI is
# the TRUSTED copy at /opt/ducklab-cli, never the snapshot's:
sudo systemd-run --uid=ducklab --gid=ducklab --collect --wait --pty \
    --setenv=HOME=/var/lib/ducklab \
    --working-directory=/var/lib/ducklab \
    /opt/ducklab-cli/release.sh prepare --src /opt/ducklab-staging/src-<ts>/source.tar.gz
sudo systemd-run --uid=ducklab --gid=ducklab --collect --wait --pty \
    --setenv=HOME=/var/lib/ducklab \
    --working-directory=/var/lib/ducklab \
    /opt/ducklab-cli/release.sh verify-staged --release-id <ID>

# Activate (symlink flip — still unprivileged, as ducklab):
sudo -u ducklab /opt/ducklab-cli/release.sh activate --release-id <ID>
```

Validation is **independent of the development venv**: `prepare` creates
`<release>/.venv-test` in place from `requirements.lock` +
`requirements-dev.txt` and runs the backend suite with it. The production
runtime venv (`.venv`) stays free of pytest/playwright (guarded).

No ACL on `/home/duck` is needed or used. Alternatively, if policy prefers
the developer account to run preparation, the operator can make
`/opt/ducklab-releases` group-writable by a dedicated `ducklab-rel` group;
the default above is the narrowest model and requires no new groups.

---

## Per-release workflow

### 1. Publish source, then prepare/validate as the service identity (pipeline needs no sudo)

```bash
# Developer (duck): filtered archive, final path, security-audited.
cd /home/duck/project/ducklab
deploy/publish-source.sh --id src-<YYYYMMDD>      # prints the snapshot path

# Release identity (ducklab) — trusted CLI at /opt/ducklab-cli:
sudo systemd-run --uid=ducklab --gid=ducklab --collect --wait --pty \
    --setenv=HOME=/var/lib/ducklab --working-directory=/var/lib/ducklab \
    /opt/ducklab-cli/release.sh prepare --src /opt/ducklab-staging/src-<...>/source.tar.gz
sudo systemd-run --uid=ducklab --gid=ducklab --collect --wait --pty \
    --setenv=HOME=/var/lib/ducklab --working-directory=/var/lib/ducklab \
    /opt/ducklab-cli/release.sh verify-staged --release-id <ID>
```

Review the manifest (`sudo -u ducklab cat /opt/ducklab-releases/<ID>/release.json`).
Its `schema_version_code` is read from the staged application's actual
`SCHEMA_VERSION`. The production frontend build uses a temporary
`DUCKLAB_DB_PATH`, not the persistent database. Approve or reject.

### 2. OpenCode stages the cutover (no sudo, no restart)

```bash
sudo -u ducklab /opt/ducklab-cli/release.sh activate --release-id <ID>
```

This only flips the `/opt/ducklab-current` symlink (atomic rename) and
records `/opt/ducklab-releases/previous`. The running service is NOT
affected until the operator restarts it.

### 3. Operator: pre-cutover checks (sudo)

```bash
# Confirm current production is healthy BEFORE touching it.
curl -s http://127.0.0.1:3000/api/health          # expect {"status":"ok",...}
systemctl is-active ducklab                        # expect: active

# Confirm a recent valid backup exists.
sudo ls -lt /var/lib/ducklab/backups/ | head -3
# If none is recent, take one now:
sudo systemctl start ducklab-backup.service

# Confirm DB schema is compatible with the release (read-only):
sudo -u ducklab /opt/ducklab-releases/<ID>/.venv/bin/python \
    -c 'import sys; sys.path.insert(0,"/opt/ducklab-releases/<ID>");
        from ducklab.db_inspect import check_compatibility;
        ok,msg = check_compatibility("/var/lib/ducklab/store_ops.db");
        print(msg); sys.exit(0 if ok else 1)'
```

If the schema check reports a pending migration, stop and follow
`deploy/MIGRATIONS.md` — migrations require explicit approval.

### 4. Operator: cutover (sudo)

```bash
# Record the release identity the service will report.
echo 'DUCKLAB_RELEASE_ID=<ID>' | sudo tee /etc/ducklab/ducklab-release.env

# Controlled restart. The maintenance gate stays up throughout.
sudo systemctl restart ducklab
```

### 5. Verify readiness (NOT just exit code 0)

```bash
sudo systemctl status ducklab --no-pager          # active (running)
journalctl -u ducklab --since '-2 min' | tail -20 # no exceptions
curl -s http://127.0.0.1:3000/api/health
#   expect: {"status":"ok","release":"<ID>"}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/login   # 200/307
curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:3000/_event/?EIO=4&transport=polling'  # 400
# Routes (all 200/307):
for r in /ops /ops/report /ops/daily /ops/expenses /ops/manpower; do
  curl -s -o /dev/null -w "$r %{http_code}\n" http://127.0.0.1:3000$r
done
# External check through the gate (from allowlisted IP):
curl -s https://app.theducklab.com/api/health
# Log in as Super Admin through the UI once; verify dashboard loads.
```

Readiness = all of the above, not merely a clean `systemctl restart`. For
`verify-staged`, the automated health check polls `/_event` first, treats 404
as a missing event endpoint and failure, and enforces a 60-second readiness
deadline by default. Set `DUCKLAB_READINESS_TIMEOUT` to override the deadline
in seconds.

### 6. If readiness fails — application rollback

```bash
# OpenCode (as the service identity, no sudo):
sudo -u ducklab /opt/ducklab-cli/release.sh rollback  # flips symlink back; DB untouched
# Operator (sudo):
echo 'DUCKLAB_RELEASE_ID=<PREVIOUS-ID>' | sudo tee /etc/ducklab/ducklab-release.env
sudo systemctl restart ducklab
# Re-run the readiness checks above.
```

Rollback NEVER restores a database backup. If the failed release migrated
the schema forward, the rollback tooling refuses and asks for operator
review (`deploy/MIGRATIONS.md`).

The failed release directory is preserved for debugging; do not delete it
until the incident is understood.

---

## Identifying the deployed version

```bash
sudo -u ducklab /opt/ducklab-cli/release.sh status                # local view of the symlinks + manifest
curl -s http://127.0.0.1:3000/api/health # live service view (release field)
sudo -u ducklab /opt/ducklab-cli/release.sh history               # all releases + statuses
```

## Public access changes

Removing or altering the Caddy maintenance gate is a SEPARATE operator
decision, never part of a release. See `deploy/OPERATOR.md`.

## Appendix — safe cleanup of failed/unwanted release candidates

Failed candidates (e.g. `20261019T194139Z`, which failed preparation with a
temp-path permission error) are never deleted by the tooling. To retire one
manually, after confirming it is NOT referenced by `/opt/ducklab-current` or
`/opt/ducklab-releases/previous`:

```bash
readlink /opt/ducklab-current /opt/ducklab-releases/previous   # must NOT point at the candidate
sudo -u ducklab rm -rf /opt/ducklab-releases/<CANDIDATE-ID>
```

Never delete the target of `current` or `previous`.
