#!/usr/bin/env bash
# DuckLab Operations — versioned release management CLI.
#
# All commands run UNPRIVILEGED. Privileged steps (first-time layout creation,
# symlink ownership, service restart) are printed for the operator instead —
# see `release.sh operator-commands` and deploy/OPERATOR-RELEASE.md.
#
# Usage:
#   deploy/release.sh prepare [--release-id ID] [--src DIR] [--root DIR]
#                             [--skip-browser] [--skip-build]
#   deploy/release.sh verify-staged --release-id ID [--root DIR] [--port N]
#   deploy/release.sh manifest --release-id ID [--root DIR]
#   deploy/release.sh activate --release-id ID [--root DIR] [--dry-run]
#   deploy/release.sh rollback [--root DIR] [--dry-run]
#   deploy/release.sh status [--root DIR]
#   deploy/release.sh history [--root DIR]
#   deploy/release.sh operator-commands
#
# Defaults (production layout):
#   root = /opt/ducklab-releases , current link = /opt/ducklab-current
# Override with DUCKLAB_RELEASE_ROOT / DUCKLAB_CURRENT_LINK / --root for
# isolated testing. NEVER points at the live /opt/ducklab directory itself.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=release_lib.sh
. "$HERE/release_lib.sh"

SRC_DEFAULT="$(cd "$HERE/.." && pwd)"
ROOT="${DUCKLAB_RELEASE_ROOT:-/opt/ducklab-releases}"
CURRENT_LINK="${DUCKLAB_CURRENT_LINK:-/opt/ducklab-current}"
SRC="$SRC_DEFAULT"
RELEASE_ID=""
STAGE_PORT="${DUCKLAB_STAGE_PORT:-3011}"
STAGE_DB="${DUCKLAB_STAGE_DB:-}"
PYTHON_BIN="${DUCKLAB_PYTHON:-python3.14}"
SKIP_BROWSER=0
SKIP_BUILD=0
DRY_RUN=0
BACKEND_TIMEOUT=900

die() { release_die "$@"; }
log() { release_log "$@"; }

# --- arg parsing -------------------------------------------------------------
CMD="${1:-}"; shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --release-id) RELEASE_ID="$2"; shift 2 ;;
        --src)        SRC="$2"; shift 2 ;;
        --root)       ROOT="$2"; shift 2 ;;
        --port)       STAGE_PORT="$2"; shift 2 ;;
        --stage-db)   STAGE_DB="$2"; shift 2 ;;
        --skip-browser) SKIP_BROWSER=1; shift ;;
        --skip-build)   SKIP_BUILD=1; shift ;;
        --dry-run)      DRY_RUN=1; shift ;;
        *) die "unknown argument: $1" ;;
    esac
done

release_dir() { printf '%s/%s\n' "$ROOT" "$1"; }

require_id() {
    [[ -n "$RELEASE_ID" ]] || die "--release-id is required"
    [[ "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] || die "unsafe release id: $RELEASE_ID"
}

# --- source-tree sanity (gate 1) ----------------------------------------------
sanity_check_source() {
    local src="$1"
    if [[ -f "$src" ]]; then
        # Published archive: verify required members exist; the full safety
        # re-validation happens in extract_source_archive at staging time.
        local list
        list="$(tar -tzf "$src" 2>/dev/null)" || die "unreadable source archive: $src"
        grep -qE '^(\./)?rxconfig\.py$' <<<"$list" || die "rxconfig.py missing in $src"
        grep -qE '(^\./|^)ducklab/' <<<"$list" || die "ducklab/ package missing in $src"
        grep -qE '^(\./)?requirements\.txt$' <<<"$list" \
            && grep -qE '^(\./)?requirements\.lock$' <<<"$list" \
            || die "requirements.txt / requirements.lock missing in $src"
        # Lock must satisfy requirements.txt (direct deps present in lock).
        local tmpd; tmpd="$(mktemp -d "${TMPDIR:-/tmp}/ducklab-reqchk.XXXXXX")"
        tar -xzf "$src" -C "$tmpd" --no-same-owner --no-same-permissions \
            ./requirements.txt ./requirements.lock 2>/dev/null \
            || tar -xzf "$src" -C "$tmpd" --no-same-owner --no-same-permissions \
               requirements.txt requirements.lock
        local dep name
        while read -r dep; do
            [[ -z "$dep" || "$dep" == \#* ]] && continue
            name="${dep%%==*}"
            grep -qi "^${name}==" "$tmpd/requirements.lock" \
                || { rm -rf "$tmpd"; die "direct dependency '$name' missing from requirements.lock"; }
        done < "$tmpd/requirements.txt"
        rm -rf "$tmpd"
        log "source archive sanity OK: $src"
        return 0
    fi
    [[ -f "$src/rxconfig.py" ]] || die "rxconfig.py missing in $src"
    [[ -d "$src/ducklab" ]] || die "ducklab/ package missing in $src"
    [[ -f "$src/requirements.txt" && -f "$src/requirements.lock" ]] \
        || die "requirements.txt / requirements.lock missing in $src"
    # Never ship local state or databases in a release.
    local bad
    bad="$(find "$src" -maxdepth 2 -name '*.db' -not -path '*/.venv/*' | head -5)"
    [[ -z "$bad" ]] || release_warn "dev database files present in source tree (excluded from release): $(echo $bad | tr '\n' ' ')"
    # Guard: lock must satisfy requirements.txt (direct deps present in lock).
    local dep
    while read -r dep; do
        [[ -z "$dep" || "$dep" == \#* ]] && continue
        local name="${dep%%==*}"
        grep -qi "^${name}==" "$src/requirements.lock" \
            || die "direct dependency '$name' missing from requirements.lock"
    done < "$src/requirements.txt"
    log "source sanity OK: $src"
}

# --- copy source into release dir ----------------------------------------------
# SRC may be either a source DIRECTORY (legacy/dev path) or a published
# ARCHIVE (source.tar.gz from publish-source.sh — the production workflow).
extract_source_archive() {
    local archive="$1" dest="$2"
    command -v tar >/dev/null || die "tar not available on this host"
    [[ -r "$archive" ]] || die "source archive not readable: $archive"
    # Independent re-validation of the archive (never trust the publisher's
    # audit alone): no absolute paths, no '..' traversal, only regular
    # files and directories, no links/devices/fifos.
    local list
    list="$(mktemp "${TMPDIR:-/tmp}/ducklab-tarlist.XXXXXX")"
    tar -tzvf "$archive" > "$list" || { rm -f "$list"; die "unreadable/corrupt archive: $archive"; }
    if awk '$NF ~ /^\// {exit 1}' "$list" \
       && awk '$NF ~ /(^|\/)\.\.(\/|$)/ {exit 1}' "$list" \
       && awk '$1 !~ /^[d-]/ {exit 1}' "$list"; then
        :
    else
        rm -f "$list"; die "archive contains unsafe paths or member types: $archive"
    fi
    rm -f "$list"
    mkdir -p "$dest"
    # Extract WITHOUT restoring source ownership/permissions: content becomes
    # owned by the release identity with its own umask-derived modes, then we
    # normalize modes explicitly below.
    tar -xzf "$archive" -C "$dest" --no-same-owner --no-same-permissions \
        || die "archive extraction failed: $archive"
    # Normalize: dirs 750, files 640 + preserve exec bits recorded in the tar
    # (tar with --no-same-permissions keeps the archived exec bits).
    find "$dest" -type d -exec chmod 750 {} +
    find "$dest" -type f -exec chmod 640 {} +
    # Deterministic exec restoration: anything archived with an exec bit.
    local m
    while IFS= read -r m; do
        m="${m#./}"
        [[ -f "$dest/$m" ]] && chmod 750 "$dest/$m"
    done < <(tar -tzvf "$archive" | awk '$1 ~ /^-.*x/ {print $NF}')
    [[ -d "$dest/ducklab" && -f "$dest/requirements.lock" ]] \
        || die "archive extraction incomplete in $dest"
    log "archive extracted: $archive -> $dest"
}

stage_source() {
    local src="$1" dest="$2"
    mkdir -p "$dest"
    if [[ -f "$src" ]]; then
        extract_source_archive "$src" "$dest"
    elif [[ -d "$src" ]]; then
        # rsync -a copies symlinks AS symlinks (never follows them); the
        # post-stage audit rejects any link that escapes the release dir.
        # assets/*.png holds runtime favicon/icon assets: ducklab/ducklab.py
        # calls rx.asset(...) at module import time and a staged copy without
        # them dies with FileNotFoundError. rsync filters are
        # first-match-wins, so the include MUST precede the blanket *.png
        # screenshot exclusion.
        rsync -a --delete \
            --exclude '.git/' --exclude '.venv*/' --exclude '.web/' \
            --exclude '.states/' --exclude '__pycache__/' --exclude '*.pyc' \
            --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
            --exclude '*.sqlite' --exclude '*.sqlite3' \
            --exclude '.env' --exclude '.env.*' --exclude '*.env' \
            --exclude '*.pem' --exclude '*.key' --exclude 'id_rsa*' \
            --exclude 'id_ed25519*' --exclude '*.keystore' \
            --exclude '.pytest_cache/' --exclude 'node_modules/' \
            --exclude '*.log' --exclude '*.tmp' --exclude '*~' \
            --include 'assets/*.png' \
            --exclude 'docs/screenshots/' --exclude '*.png' --exclude '*.har' \
            "$src/ducklab" "$src/rxconfig.py" "$src/assets" "$src/scripts" \
            "$src/deploy" "$src/requirements.txt" "$src/requirements.lock" \
            "$src/requirements-dev.txt" "$src/AGENTS.md" "$src/README.md" \
            "$dest/"
        # Tests ship with the release so validation is reproducible.
        rsync -a --delete --exclude '__pycache__/' --exclude '*.pyc' \
            "$src/tests" "$dest/"
        [[ -d "$dest/ducklab" && -f "$dest/requirements.lock" ]] \
            || die "source staging incomplete in $dest"
    else
        die "--src must be a source directory or a published source archive: $src"
    fi
    audit_staged_release "$dest"
}

# --- staged-release security audit ------------------------------------------------
# Fails preparation if any dangerous content survived staging.
audit_staged_release() {
    local dir="$1" bad=""
    bad="$(find "$dir" \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*.sqlite*' \) -not -path '*/.venv*/*' | head -3)"
    [[ -z "$bad" ]] || die "staged release contains database files: $bad"
    bad="$(find "$dir" \( -name '.env*' -o -name '*.env' -o -name '*.pem' -o -name '*.key' \) -not -path '*/.venv*/*' | head -3)"
    [[ -z "$bad" ]] || die "staged release contains secret-like files: $bad"
    bad="$(find "$dir" -name '.git' -not -path '*/.venv*/*' | head -3)"
    [[ -z "$bad" ]] || die "staged release contains vcs metadata: $bad"
    # Symlink audit: staged content must not reference paths OUTSIDE the
    # release dir (a symlink to /home/duck/... would leak private files to
    # the service identity).
    local link target
    while IFS= read -r link; do
        target="$(readlink -f "$link" 2>/dev/null || echo UNRESOLVABLE)"
        case "$target" in
            "$dir"/*) ;;  # internal link: fine
            *) die "staged release contains escaping symlink: $link -> $target" ;;
        esac
    done < <(find "$dir" -type l -not -path '*/.venv*/*')
    log "staged-release audit OK: $dir"
}

# --- production build (gate 11) ---------------------------------------------------
build_release() {
    local dir="$1"
    log "production build (reflex export) in $dir — this can take minutes"
    (cd "$dir" && "./.venv/bin/reflex" export --env prod --frontend-only --no-zip >/dev/null)
    [[ -d "$dir/.web" ]] || die "build produced no .web directory in $dir"
    log "build OK: $dir/.web"
}

# --- isolated validation environment (release-owned, dev deps only) ------------
# Usage: create_validation_venv <release_dir> <python>
# Creates <release_dir>/.venv-test AT ITS FINAL PATH, installing the locked
# runtime deps PLUS requirements-dev.txt (pytest/playwright). The production
# runtime venv (.venv) stays free of dev tooling; systemd only uses .venv.
create_validation_venv() {
    local dir="$1" venv="$dir/.venv-test"
    [[ -f "$dir/requirements-dev.txt" ]] \
        || die "requirements-dev.txt missing in $dir (needed for validation)"
    if [[ ! -x "$venv/bin/python" ]]; then
        log "creating validation venv in place: $venv ($PYTHON_BIN)"
        "$PYTHON_BIN" -m venv "$venv"
        "$venv/bin/pip" install --no-cache-dir -q \
            -r "$dir/requirements.lock" -r "$dir/requirements-dev.txt"
        "$venv/bin/pip" check || die "validation venv pip check failed"
    fi
}

# --- per-invocation private temp space --------------------------------------------
# The pipeline may run as any identity (duck, ducklab); it must never depend
# on another user's directories (the /tmp/opencode defect). Each caller gets
# a private temp dir under the system temp location, cleaned up on exit.
release_mktempdir() {
    local d
    d="$(mktemp -d "${TMPDIR:-/tmp}/ducklab-release.XXXXXX")" \
        || release_fail "cannot create temp dir under ${TMPDIR:-/tmp}"
    chmod 700 "$d"
    printf '%s\n' "$d"
}

# --- full test suite in the release validation venv ------------------------------
run_backend_tests() {
    local dir="$1"
    # Tests run with the release's OWN validation venv against the RELEASE
    # source tree — never against the development checkout or dev venv.
    create_validation_venv "$dir"
    local pytest_bin="$dir/.venv-test/bin/python"
    local tmpd; tmpd="$(release_mktempdir)"
    log "running backend regression suite (validation venv, disposable DB)"
    local rc=0
    (cd "$dir" && DUCKLAB_DB_PATH="$tmpd/test.db" \
        PYTHONPATH="$dir" "$pytest_bin" -m pytest "$dir/tests" -q) || rc=$?
    rm -rf "$tmpd"
    [[ "$rc" -eq 0 ]] || return "$rc"
}

# --- manifest -------------------------------------------------------------------------
write_manifest() {
    local dir="$1" id="$2" result="${3:-prepared}"
    local locksum gitsrc prev
    locksum="$(sha256sum "$dir/requirements.lock" | awk '{print $1}')"
    gitsrc="$(git -C "$SRC" rev-parse HEAD 2>/dev/null | head -1 || true)"
    gitsrc="${gitsrc:-untracked-worktree}"
    prev="$(readlink "$CURRENT_LINK" 2>/dev/null | xargs -r basename || echo '')"
    cat > "$dir/release.json" <<EOF
{
  "release_id": "$id",
  "created_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source": "$SRC",
  "git_commit": "$gitsrc",
  "lockfile_sha256": "$locksum",
  "python": "$("$dir/.venv/bin/python" --version 2>&1 | awk '{print $2}')",
  "features": [
    "authentication-rbac",
    "operations-dashboard",
    "monthly-reporting",
    "daily-operations",
    "expenses-management",
    "manpower-efficiency-payroll"
  ],
  "schema_version_code": 1,
  "schema_migration": "additive-only; auto-migrate forward on service start; verified backup required before activation",
  "previous_release_at_creation": "$prev",
  "approval_required": true,
  "status": "$result"
}
EOF
    log "manifest written: $dir/release.json"
}

# --- commands -----------------------------------------------------------------------------

cmd_prepare() {
    [[ -n "$RELEASE_ID" ]] || RELEASE_ID="$(release_make_id)"
    require_id
    local dir; dir="$(release_dir "$RELEASE_ID")"
    [[ ! -e "$dir" ]] || die "release dir already exists: $dir (choose another id)"
    log "=== PREPARE release $RELEASE_ID ==="
    sanity_check_source "$SRC"
    if [[ ! -d "$ROOT" ]]; then
        mkdir -p "$ROOT" 2>/dev/null || die "cannot create $ROOT (operator must create the release root; see operator-commands)"
    elif [[ ! -w "$ROOT" ]]; then
        die "release root $ROOT is not writable by $(id -un) — run as the service identity (ducklab) or ask the operator to fix ownership (see OPERATOR-RELEASE.md)"
    fi
    # On any failure during prepare, remove the incomplete release dir we
    # created (never anything pre-existing — we asserted ! -e above).
    # The path is captured NOW: the trap string must not reference the
    # function-local $dir, which is unbound by the time the trap fires
    # (this was the "dir: unbound variable" defect).
    local guard=""; printf -v guard 'rc=$?; [[ $rc -ne 0 ]] && [[ "%s" == "%s"/* ]] && rm -rf -- "%s" 2>/dev/null; trap - EXIT' \
        "$dir" "$ROOT" "$dir"
    trap "$guard" EXIT
    stage_source "$SRC" "$dir"
    # Release content must be private to the service identity: deny group-write
    # and all world access on staged files (umask on shared dev machines may be
    # permissive). No world-readable release trees.
    chmod -R o-rwx "$dir"
    chmod -R g-w "$dir"
    chmod 750 "$dir"
    release_require release_create_venv "$dir" "$PYTHON_BIN"
    release_require release_verify_venv "$dir"
    release_require release_install_deps "$dir"
    run_backend_tests "$dir"
    if [[ "$SKIP_BUILD" -eq 0 ]]; then build_release "$dir"; fi
    write_manifest "$dir" "$RELEASE_ID" "prepared"
    trap - EXIT   # success: keep the staged release
    cat <<EOF

Release staged: $dir
Next:
  1. Review:  cat $dir/release.json
  2. Verify:  deploy/release.sh verify-staged --release-id $RELEASE_ID
  3. Approve + activate: see deploy/OPERATOR-RELEASE.md
EOF
}

cmd_verify_staged() {
    require_id
    local dir; dir="$(release_dir "$RELEASE_ID")"
    [[ -d "$dir" ]] || die "no such release: $dir"
    log "=== VERIFY STAGED $RELEASE_ID on port $STAGE_PORT ==="
    # Never bind the production upstream port.
    [[ "$STAGE_PORT" != "3000" ]] || die "refusing to bind staging to production port 3000"
    if ss -tln 2>/dev/null | grep -q ":$STAGE_PORT "; then
        die "staging port $STAGE_PORT already in use"
    fi
    release_require release_verify_venv "$dir"
    local stagedir; stagedir="$(release_mktempdir)"
    STAGE_DB="${STAGE_DB:-$stagedir/stage.db}"
    local logf="$stagedir/ducklab-stage-$RELEASE_ID.log"
    log "starting staged app (db: $STAGE_DB, log: $logf)"
    # Own session so we can kill the whole process group (reflex spawns
    # child processes; killing only the launcher leaves the port bound).
    setsid bash -c "cd '$dir' && exec env \
        DUCKLAB_DB_PATH='$STAGE_DB' \
        DUCKLAB_SECURE_COOKIES=0 DUCKLAB_TRUST_PROXY=0 \
        DUCKLAB_RELEASE_ID='$RELEASE_ID' \
        '$dir/.venv/bin/reflex' run --env prod --single-port \
        --backend-host 127.0.0.1 --backend-port '$STAGE_PORT'" \
        >"$logf" 2>&1 </dev/null &
    local pid=$!
    trap 'kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true' EXIT
    local ok=1
    if release_health_check "http://127.0.0.1:$STAGE_PORT" "$BACKEND_TIMEOUT" \
       && release_route_check "http://127.0.0.1:$STAGE_PORT"; then
        ok=0
        # Confirm release identity is served.
        local served
        served="$(curl -s --max-time 5 "http://127.0.0.1:$STAGE_PORT/api/health" | grep -o '"release":"[^"]*"' || true)"
        log "served identity: $served"
        [[ "$served" == "\"release\":\"$RELEASE_ID\"" ]] || { release_warn "release identity mismatch"; ok=1; }
    fi
    kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    trap - EXIT
    # Report log location before cleanup; keep it on failure for debugging.
    if [[ "$ok" -eq 0 ]]; then
        rm -rf "$stagedir"
    else
        release_warn "staged app log preserved for debugging: $logf"
    fi
    if [[ "$ok" -eq 0 ]]; then
        sed -i 's/"status": "prepared"/"status": "verified"/' "$dir/release.json" 2>/dev/null || true
        log "STAGED RELEASE VERIFIED: $RELEASE_ID"
        if [[ "$SKIP_BROWSER" -eq 0 ]]; then
            release_warn "browser acceptance tests: run scripts/browser_test_*.py against a seeded staging instance (see OPERATOR-RELEASE.md)"
        fi
    else
        sed -i 's/"status": "[a-z]*"/"status": "verify-failed"/' "$dir/release.json" 2>/dev/null || true
        die "STAGED VERIFICATION FAILED: $RELEASE_ID (log: $logf)"
    fi
}

cmd_manifest() { require_id; write_manifest "$(release_dir "$RELEASE_ID")" "$RELEASE_ID" "manifest-updated"; }

cmd_activate() {
    require_id
    local dir; dir="$(release_dir "$RELEASE_ID")"
    [[ -d "$dir" ]] || die "no such release: $dir"
    grep -q '"status": "verified"' "$dir/release.json" \
        || die "release $RELEASE_ID is not verified; run verify-staged first"
    log "=== ACTIVATE $RELEASE_ID ==="
    # Record previous for rollback, then flip the symlink atomically.
    local prev=""
    [[ -L "$CURRENT_LINK" ]] && prev="$(basename "$(readlink "$CURRENT_LINK")")"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        log "DRY RUN: would set $CURRENT_LINK -> $dir (previous: ${prev:-none})"
        return 0
    fi
    [[ -n "$prev" ]] && ln -sfn "$(release_dir "$prev")" "$ROOT/previous"
    ln -sfn "$dir" "$CURRENT_LINK.new" && mv -T "$CURRENT_LINK.new" "$CURRENT_LINK"
    sed -i 's/"status": "[a-z-]*"/"status": "activated"/' "$dir/release.json"
    log "activated: $CURRENT_LINK -> $dir (previous: ${prev:-none})"
    log "operator must now: sudo systemctl restart ducklab  (then run status checks)"
}

cmd_rollback() {
    log "=== ROLLBACK ==="
    [[ -L "$ROOT/previous" ]] || die "no previous release recorded — cannot roll back"
    local prevdir cur
    prevdir="$(readlink "$ROOT/previous")"
    cur="$(basename "$(readlink "$CURRENT_LINK" 2>/dev/null || echo none)")"
    [[ -d "$prevdir" && -x "$prevdir/.venv/bin/python" ]] \
        || die "previous release is broken: $prevdir"
    release_verify_venv "$prevdir" || die "previous release failed integrity; manual intervention required"
    # Schema compatibility guard: never roll code back across a schema upgrade.
    local prev_code_v db_v
    prev_code_v="$(PYTHONPATH="$prevdir" "$prevdir/.venv/bin/python" -c \
        'from ducklab.store_ops_db import SCHEMA_VERSION; print(SCHEMA_VERSION)')"
    db_v="${DUCKLAB_DB_PATH:-/var/lib/ducklab/store_ops.db}"
    if [[ -f "$db_v" ]]; then
        db_v="$(PYTHONPATH="$prevdir" "$prevdir/.venv/bin/python" -m ducklab.db_inspect 2>/dev/null \
            || PYTHONPATH="$prevdir" "$prevdir/.venv/bin/python" -c \
            "import sqlite3,os;print(sqlite3.connect('file:'+os.environ.get('DUCKLAB_DB_PATH','/var/lib/ducklab/store_ops.db')+'?mode=ro',uri=True).execute('PRAGMA user_version').fetchone()[0])")"
        if [[ "$db_v" -gt "$prev_code_v" ]]; then
            die "database schema v$db_v is NEWER than previous release code v$prev_code_v — STOP; operator must review migration compatibility before rollback"
        fi
    fi
    if [[ "$DRY_RUN" -eq 1 ]]; then
        log "DRY RUN: would roll back $CURRENT_LINK -> $prevdir (current: $cur)"
        return 0
    fi
    ln -sfn "$prevdir" "$CURRENT_LINK.new" && mv -T "$CURRENT_LINK.new" "$CURRENT_LINK"
    # Mark the failed release for audit (preserved for debugging).
    sed -i 's/"status": "[a-z-]*"/"status": "rolled-back-failed"/' \
        "$(release_dir "$cur")/release.json" 2>/dev/null || true
    sed -i 's/"status": "[a-z-]*"/"status": "activated"/' \
        "$prevdir/release.json" 2>/dev/null || true
    log "rolled back: $CURRENT_LINK -> $prevdir"
    log "failed release '$cur' preserved at $(release_dir "$cur") for debugging"
    log "operator must now: sudo systemctl restart ducklab  (then verify health)"
    log "NOTE: database was NOT restored or modified — code rollback only."
}

cmd_status() {
    echo "release root:   $ROOT"
    if [[ -L "$CURRENT_LINK" ]]; then
        echo "current:        $(basename "$(readlink "$CURRENT_LINK")")"
    else
        echo "current:        (none)"
    fi
    if [[ -L "$ROOT/previous" ]]; then
        echo "previous:       $(basename "$(readlink "$ROOT/previous")")"
    fi
    if [[ -L "$CURRENT_LINK" && -f "$(readlink -f "$CURRENT_LINK")/release.json" ]]; then
        echo "--- current release.json ---"
        cat "$(readlink -f "$CURRENT_LINK")/release.json"
    fi
    # Legacy production (pre-versioning) note.
    if [[ -d /opt/ducklab && ! -L "$CURRENT_LINK" ]]; then
        echo "note: legacy /opt/ducklab deployment still active (unversioned)"
    fi
}

cmd_history() {
    local d
    for d in "$ROOT"/*/; do
        [[ -L "${d%/}" ]] && continue   # skip symlinked entries (previous)
        [[ -f "$d/release.json" ]] || continue
        printf '%-28s %s\n' "$(basename "$d")" \
            "$(grep -o '"status": "[^"]*"' "$d/release.json" | cut -d'"' -f4)"
    done
}

cmd_operator_commands() {
    cat <<'EOF'
# DuckLab release layout — ONE-TIME privileged setup (run with sudo):
#
# The release root is owned by the SERVICE IDENTITY (ducklab), not root and
# not a developer account. Release preparation runs unprivileged as that
# identity (e.g. via systemd-run below); the service reads exactly the files
# it executes. No world-writable or world-readable release content.
sudo install -d -o ducklab -g ducklab -m 750 /opt/ducklab-releases
sudo install -d -o duck -g ducklab -m 2750 /opt/ducklab-staging   # source archives (duck publishes, ducklab reads)
sudo install -d -o root -g ducklab -m 750 /opt/ducklab-cli        # trusted CLI location
sudo install -o root -g ducklab -m 750 deploy/cli/release.sh /opt/ducklab-cli/
sudo install -o root -g ducklab -m 640 deploy/cli/release_lib.sh /opt/ducklab-cli/

# Developer publishes a filtered, audited source archive (no sudo, no ACL on /home/duck):
deploy/publish-source.sh         # prints /opt/ducklab-staging/src-<ts>/source.tar.gz

# Release preparation/verification as the service identity (pipeline itself
# needs no sudo once the roots exist). Always run the TRUSTED CLI copy:
sudo systemd-run --uid=ducklab --gid=ducklab --collect --wait --pty \
    --setenv=HOME=/var/lib/ducklab --working-directory=/var/lib/ducklab \
    /opt/ducklab-cli/release.sh prepare --src /opt/ducklab-staging/src-<ts>/source.tar.gz

# systemd migration to the versioned layout (first activation only):
sudo install -m 644 deploy/ducklab.service /etc/systemd/system/ducklab.service
sudo systemctl daemon-reload

# Per-release privileged steps are only ever:
sudo systemctl restart ducklab          # after activate / rollback
sudo systemctl status ducklab --no-pager
curl -s http://127.0.0.1:3000/api/health   # readiness verification
EOF
}

case "$CMD" in
    prepare)        cmd_prepare ;;
    verify-staged)  cmd_verify_staged ;;
    manifest)       cmd_manifest ;;
    activate)       cmd_activate ;;
    rollback)       cmd_rollback ;;
    status)         cmd_status ;;
    history)        cmd_history ;;
    operator-commands) cmd_operator_commands ;;
    *) cat <<'EOF'
Usage: deploy/release.sh <command> [options]
Commands: prepare | verify-staged | manifest | activate | rollback |
          status | history | operator-commands
See deploy/OPERATOR-RELEASE.md for the full workflow.
EOF
        exit 2 ;;
esac
