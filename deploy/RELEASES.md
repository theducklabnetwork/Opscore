# DuckLab — Versioned Releases & Dependency Management

## Dependency management

- `requirements.txt` — direct runtime dependencies only (`reflex`,
  `argon2-cffi`, `openpyxl`, `plotly`, `pywebview`, `starlette`, and Windows
  `tzdata`). Human-edited.
- `requirements.lock` — the authoritative, fully pinned production manifest
  (all transitive deps). Regenerate after changing `requirements.txt`:

  ```bash
  python3.14 -m venv /tmp/opencode/lock-venv
  /tmp/opencode/lock-venv/bin/pip install -r requirements.txt
  /tmp/opencode/lock-venv/bin/pip check
  /tmp/opencode/lock-venv/bin/pip freeze --exclude-editable | sort > requirements.lock
  rm -rf /tmp/opencode/lock-venv
  ```

  Review the lock diff before committing. Production installs **only** from
  `requirements.lock` and verifies with `pip check` + runtime imports of
  `reflex`, `granian`, `argon2`, `starlette`.
- `requirements-dev.txt` — pytest/playwright, installed only in dev venvs.
  The release pipeline fails if dev packages appear in a production venv.
- Python 3.14 is required on the build host and production host. Never
  modify the system Python; venvs are created per-release with
  `python3.14 -m venv <release>/.venv` **in place**.

## The `.venv` copying defect (permanent fix)

Root cause: the first installer `rsync`ed `/home/duck/project/ducklab/.venv`
into `/opt/ducklab`. A venv records its absolute path at creation time in
console-script shebangs (`#!.../bin/python3`) and `pyvenv.cfg`; the copied
venv's `reflex` launcher still pointed at `/home/duck/...`, causing systemd
`203/EXEC` once paths diverged.

Fix (enforced in `deploy/operator-install.sh` and `deploy/release.sh`):

1. Venvs are always created at their final absolute path
   (`python3.14 -m venv <final-dir>/.venv`).
2. `release_verify_venv()` inspects every `#!` line under `.venv/bin` and
   fails if any interpreter path points outside the release directory.
3. `pip check` + runtime import checks run in every release venv.
4. systemd hardening (`ProtectHome=true`, `ProtectSystem=full`) is never
   relaxed to accommodate a broken path.

## Release lifecycle

```
prepare  ->  verify-staged  ->  (operator approval)  ->  activate  ->  verify
                                                            |
                                              on failure: rollback (code only)
```

**Execution identity:** the pipeline runs as the non-root service identity
(`ducklab`). The release root `/opt/ducklab-releases` is owned
`ducklab:ducklab` mode `750` — no world access, no group write. Release dirs
are normalized to owner-only-write (`chmod -R o-rwx, g-w`, dir mode `750`).
The operator's only privileged steps are one-time root creation, unit
installation, and `systemctl restart` at cutover/rollback. Filesystem-level
verification of this model: `deploy/test_release_permissions.sh` (creates a
throwaway uid, requires sudo once for useradd/setfacl).

- `deploy/release.sh prepare` — source sanity, stage source, create venv in
  place, install locked deps, full backend test suite, production build,
  manifest.
- `deploy/release.sh verify-staged` — boots the release on an isolated
  loopback port with a disposable DB; checks `/api/health`, `/login`,
  `/_event` (socket.io), and all production routes; verifies the served
  release identity. Refuses port 3000 and occupied ports.
- `deploy/release.sh activate --release-id <ID>` — atomic symlink flip;
  refuses unverified releases; records `previous` for rollback.
- `deploy/release.sh rollback` — restores the previous release; refuses if
  the DB schema is newer than the rollback code; never touches the database.
- `deploy/release.sh status|history` — release identity and audit view.

## Simulation

`deploy/simulate.sh` exercises the entire cycle (stage→activate→fail→
rollback) plus failure modes (relocated venv shebangs, missing deps, port
conflicts, port-3000 guard, newer-schema rollback block, missing previous)
in disposable directories. Run it after changing release tooling:

```bash
deploy/simulate.sh
```

## Production preservation guarantees

- Release tooling never touches `/var/lib/ducklab`, `/etc/ducklab`, Caddy,
  or the live systemd units. Privileged steps are printed for the operator.
- No release directory ever contains a database (`*.db` excluded at staging).
- `rsync --delete` is only ever used against a brand-new, release-scoped
  staging directory — never against persistent storage.
- Rollback restores code only; the database is left exactly as-is.
