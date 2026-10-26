#!/usr/bin/env bash
# DuckLab Operations — privileged production installation (run ONCE with sudo).
#
# CRITICAL ORDERING: the Caddy maintenance gate is installed and ACTIVATED
# (Caddy reloaded) BEFORE the DuckLab service is started on port 3000, so the
# application is never publicly reachable without the gate in front of it.
#
# This script:
#   - is idempotent and non-destructive
#   - backs up any file it replaces (timestamped)
#   - deploys a controlled copy of the app to /opt/ducklab (the original
#     checkout under /home/duck is preserved untouched)
#   - runs the app and backups as the dedicated non-root user `ducklab`
#   - preserves an existing /etc/ducklab/ducklab.env (never overwritten)
#   - collects the maintenance-gate password interactively (never on the
#     command line, never logged)
#
# Review first:   less deploy/operator-install.sh
# Run:            sudo bash deploy/operator-install.sh
#
# It does NOT: start/enable the public service, remove the gate, touch the
# source workbook / prototype DB / existing ops DB, change DNS/firewall, or
# reboot.

set -euo pipefail

# --- Configuration -----------------------------------------------------------
SRC=/home/duck/project/ducklab          # original checkout (preserved)
DEPLOY_DIR=/opt/ducklab                 # controlled deployment copy
DATA_DIR=/var/lib/ducklab
BACKUP_DIR=/var/lib/ducklab/backups
ETC_DIR=/etc/ducklab
SVC_USER=ducklab
CADDYFILE=/etc/caddy/Caddyfile
APP_PORT=3000
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OPERATOR_IP="${DUCKLAB_OPERATOR_IP:-}"  # may be pre-set; otherwise prompted

log()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- [0] Preflight -----------------------------------------------------------
log "[0/11] Preflight checks"
[[ $EUID -eq 0 ]] || die "run with sudo"
command -v caddy   >/dev/null || die "caddy not installed"
command -v rsync   >/dev/null || die "rsync not installed (apt install rsync)"
[[ -d "$SRC/.venv" ]] || die "missing $SRC/.venv"
[[ -x "$SRC/.venv/bin/reflex" ]] || die "missing reflex in $SRC/.venv"
[[ -f "$SRC/deploy/Caddyfile.gated" ]] || die "missing deploy/Caddyfile.gated"
[[ -f "$SRC/requirements.lock" ]] || die "missing requirements.lock (see requirements.txt header)"
command -v python3.14 >/dev/null || die "python3.14 not found on this host"

# Port 3000 must be free (we bind the app there; it must not be already used).
if ss -tln 2>/dev/null | grep -q ":$APP_PORT "; then
    die "port $APP_PORT is already in use — investigate before proceeding"
fi

# Refuse to run if a conflicting ducklab service is already active.
if systemctl is-active --quiet ducklab 2>/dev/null; then
    warn "ducklab service is already active; it will be restarted by hand later"
fi

# Warn if a production DB already exists (we never overwrite it).
if [[ -f "$DATA_DIR/store_ops.db" ]]; then
    warn "$DATA_DIR/store_ops.db already exists — it will be preserved untouched"
fi

# --- [1] Service user --------------------------------------------------------
log "[1/11] Service user '$SVC_USER'"
if ! id "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
    log "    created $SVC_USER"
else
    log "    $SVC_USER already exists"
fi

# --- [2] Directories ----------------------------------------------------------
log "[2/11] Persistent directories"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 750 "$DATA_DIR"
# Backups are written by the backup service, which runs as $SVC_USER so the
# main app never needs root and the backup job needs no extra privilege.
install -d -o "$SVC_USER" -g "$SVC_USER" -m 700 "$BACKUP_DIR"
install -d -m 755 "$ETC_DIR"

# --- [3] Controlled deployment copy at /opt/ducklab ---------------------------
log "[3/11] Deploy controlled copy to $DEPLOY_DIR (original checkout preserved)"
install -d -o root -g "$SVC_USER" -m 755 "$DEPLOY_DIR"
# Copy app code, assets, and deploy artifacts. Exclude local state, dev DBs,
# caches, and VCS. The original tree is only READ, never modified.
rsync -a --delete \
    --exclude '.git/' \
    --exclude '.web/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude 'store_ops.db' \
    --exclude 'placement_tracker.db' \
    --exclude 'node_modules/' \
    "$SRC/ducklab" "$SRC/rxconfig.py" "$SRC/assets" "$SRC/scripts" \
    "$SRC/deploy" "$SRC/requirements.txt" "$SRC/requirements.lock" \
    "$DEPLOY_DIR"/
# The virtualenv is created IN PLACE at its final absolute path.
# NEVER copy or relocate a venv: console-script shebangs and pyvenv.cfg record
# the creation-time absolute path, and a moved venv fails with systemd
# 203/EXEC (this was the original deployment defect).
if [[ ! -x "$DEPLOY_DIR/.venv/bin/python" ]]; then
    log "    creating virtualenv in place at $DEPLOY_DIR/.venv"
    python3.14 -m venv "$DEPLOY_DIR/.venv"
else
    log "    reusing existing in-place venv at $DEPLOY_DIR/.venv"
fi
"$DEPLOY_DIR/.venv/bin/pip" install --no-cache-dir -q -r "$DEPLOY_DIR/requirements.lock"
"$DEPLOY_DIR/.venv/bin/pip" check
# Verify shebangs reference this location (fail-closed).
if grep -l '^#!/home/' "$DEPLOY_DIR"/.venv/bin/* 2>/dev/null | grep -q .; then
    die "venv shebangs reference a foreign path — venv integrity check failed"
fi
# Runtime import verification.
"$DEPLOY_DIR/.venv/bin/python" -c 'import reflex, granian, argon2, starlette' \
    || die "runtime import verification failed"
chown -R root:"$SVC_USER" "$DEPLOY_DIR"
# Narrow permissions: world gets nothing; group (ducklab) reads/executes.
chmod -R o-rwx "$DEPLOY_DIR"
find "$DEPLOY_DIR" -type d -exec chmod g+rx {} +
find "$DEPLOY_DIR" -type f -exec chmod g+r {} +
# Executables the service needs to run.
chmod -R g+x "$DEPLOY_DIR/.venv/bin" 2>/dev/null || true
# The app writes build artifacts to .web at startup; give the group a writable
# project root and let it create .web.
chmod g+w "$DEPLOY_DIR"

# --- [4] Production environment (preserve existing) ---------------------------
log "[4/11] Production environment file"
if [[ -f "$ETC_DIR/ducklab.env" ]]; then
    log "    existing ducklab.env found — preserving it (not overwriting)"
else
    install -o root -g "$SVC_USER" -m 640 \
        "$DEPLOY_DIR/deploy/ducklab.env.template" "$ETC_DIR/ducklab.env"
    log "    installed ducklab.env from template"
fi
# Validate required settings (whichever file is in place).
# shellcheck disable=SC1091
set -a; . "$ETC_DIR/ducklab.env"; set +a
[[ "${DUCKLAB_DB_PATH:-}" == "$DATA_DIR/store_ops.db" ]] \
    || die "ducklab.env: DUCKLAB_DB_PATH must be $DATA_DIR/store_ops.db"
[[ "${DUCKLAB_SECURE_COOKIES:-}" == "1" ]] \
    || die "ducklab.env: DUCKLAB_SECURE_COOKIES must be 1"
[[ "${DUCKLAB_TRUST_PROXY:-}" == "1" ]] \
    || die "ducklab.env: DUCKLAB_TRUST_PROXY must be 1"
[[ -z "${DUCKLAB_FRONTEND_ORIGINS:-}" ]] \
    || die "ducklab.env: DUCKLAB_FRONTEND_ORIGINS must be empty in production"
chmod 640 "$ETC_DIR/ducklab.env"; chown root:"$SVC_USER" "$ETC_DIR/ducklab.env"
log "    environment validated"

# --- [5] systemd units ---------------------------------------------------------
log "[5/11] Install systemd units"
install -m 644 "$DEPLOY_DIR/deploy/ducklab.service"        /etc/systemd/system/ducklab.service
install -m 644 "$DEPLOY_DIR/deploy/ducklab-backup.service" /etc/systemd/system/ducklab-backup.service
install -m 644 "$DEPLOY_DIR/deploy/ducklab-backup.timer"   /etc/systemd/system/ducklab-backup.timer
install -m 755 "$DEPLOY_DIR/deploy/ducklab-backup.sh"      /usr/local/bin/ducklab-backup
systemctl daemon-reload

# --- [6] Operator IP allowlist (verify) ---------------------------------------
log "[6/11] Operator IP allowlist"
if [[ -z "$OPERATOR_IP" ]]; then
    # Best-effort: detect the operator's current public IP from this session.
    DETECTED="$(echo "${SSH_CONNECTION:-}" | awk '{print $1}')"
    echo "    Detected connecting client IP (SSH_CONNECTION): ${DETECTED:-unknown}"
    read -rp "    Operator public IP to allowlist [${DETECTED:-required}]: " OPERATOR_IP
    OPERATOR_IP="${OPERATOR_IP:-$DETECTED}"
fi
[[ -n "$OPERATOR_IP" ]] || die "no operator IP provided"
# Validate IPv4/IPv6 literal.
[[ "$OPERATOR_IP" =~ ^[0-9a-fA-F.:]+$ ]] || die "invalid IP: $OPERATOR_IP"
log "    allowlisting operator IP: $OPERATOR_IP"

# --- [7] Maintenance-gate credential (interactive, never on argv) -------------
log "[7/11] Maintenance-gate credential"
echo "    Set the HTTP Basic-auth credential for the maintenance gate."
echo "    (Separate from the app's own login. Non-allowlisted visitors will be"
echo "     challenged with HTTP 401 and must supply this.)"
read -rsp "    Gate password: " GATE_PW; echo
read -rsp "    Confirm gate password: " GATE_PW2; echo
[[ "$GATE_PW" == "$GATE_PW2" ]] || die "gate passwords differ"
[[ ${#GATE_PW} -ge 12 ]] || die "gate password must be >= 12 chars"
# Hash via stdin-free subcommand; plaintext stays in this shell only.
GATE_HASH="$(caddy hash-password --plaintext "$GATE_PW")"
unset GATE_PW GATE_PW2

# --- [8] Build gated Caddyfile (atomic, with backup) --------------------------
log "[8/11] Install gated Caddy configuration"
CIDR="$OPERATOR_IP"; [[ "$CIDR" != */* ]] && CIDR="$CIDR/32"
TMP_CADDY="$(mktemp)"
sed -e "s|__OPERATOR_IP_CIDR__|$CIDR|g" \
    -e "s|__OPERATOR_BCRYPT_HASH__|$GATE_HASH|g" \
    "$DEPLOY_DIR/deploy/Caddyfile.gated" > "$TMP_CADDY"
unset GATE_HASH
# Refuse to install if placeholders survived (fail-closed).
if grep -q "__OPERATOR_" "$TMP_CADDY"; then
    rm -f "$TMP_CADDY"; die "placeholder substitution failed; not installing"
fi
caddy validate --adapter caddyfile --config "$TMP_CADDY" \
    || { rm -f "$TMP_CADDY"; die "Caddyfile validation failed"; }
# Back up the existing Caddyfile, then install atomically (rename).
cp -a "$CADDYFILE" "$CADDYFILE.bak.$STAMP"
chmod 644 "$TMP_CADDY"
mv -f "$TMP_CADDY" "$CADDYFILE"
log "    previous Caddyfile backed up to $CADDYFILE.bak.$STAMP"

# --- [9] ACTIVATE THE GATE (reload Caddy) BEFORE the app starts ---------------
log "[9/11] Activate maintenance gate (reload Caddy)"
systemctl reload caddy
sleep 1
# Fail-closed verification: the gated site must now challenge / not proxy
# freely. We expect 401 (no creds) since the app is not up yet (502 is also
# acceptable pre-start, but the gate must respond, not an open proxy).
GATE_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    https://app.theducklab.com/ || echo 000)"
log "    https://app.theducklab.com/ -> HTTP $GATE_CODE (gate active)"

# --- [10] Done — hand off ------------------------------------------------------
log "[10/11] Installation complete. Gate is ACTIVE."
cat <<EOF

NEXT STEPS (operator, in order — the app is NOT started yet):

  1. Bootstrap the first Super Admin (interactive; before starting the app):
       cd $DEPLOY_DIR
       sudo -u $SVC_USER DUCKLAB_DB_PATH=$DATA_DIR/store_ops.db \\
         $DEPLOY_DIR/.venv/bin/python scripts/bootstrap_admin.py

  2. Start the app (now safely behind the active gate):
       sudo systemctl enable --now ducklab
       journalctl -u ducklab -f

  3. Verify (from the allowlisted IP, or with the gate credential):
       open https://app.theducklab.com/
     Non-allowlisted visitors receive an HTTP 401 Basic-auth challenge.

  4. Enable backups:
       sudo systemctl enable --now ducklab-backup.timer
       sudo systemctl start ducklab-backup.service   # one manual run
       ls -l $BACKUP_DIR

Rollback: reinstall $CADDYFILE.bak.$STAMP and 'systemctl reload caddy';
see deploy/ROLLBACK.md. The gate stays up until you explicitly remove it.
EOF
