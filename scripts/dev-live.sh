#!/usr/bin/env bash
# dev-live.sh — run the working-tree DuckLab code live on 127.0.0.1:3000
# behind the existing Caddy gate (https://app.theducklab.com/) for manual
# testing, then hand port 3000 back to the production service.
#
# Subcommands: start | stop | status | bootstrap
#
# HARD RULES (enforced by this script):
#  - Never runs sudo/systemctl/pkill; never kills a process it did not start
#    and verify. Privileged steps are PRINTED for the operator only.
#  - Dev DB is pinned to <checkout>/.devdata/dev.db (DUCKLAB_DB_PATH,
#    no fallback). .devdata/ is mode 700, owned by the invoking user.
#  - Launch mirrors production: reflex run --env prod --single-port
#    --backend-host 127.0.0.1 --backend-port 3000 (see ducklab.service).
#  - The dev process runs in its own session; its REAL PGID is recorded from
#    inside the session before exec and re-verified before any signal.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
DEVDATA="$ROOT/.devdata"
DB="$DEVDATA/dev.db"
PGID_FILE="$DEVDATA/dev.pgid"
LOG="$DEVDATA/dev.log"
VENV_REFLEX="$ROOT/.venv/bin/reflex"
HEALTH_URL="http://127.0.0.1:3000/api/health"
PORT=3000
HOST=127.0.0.1
START_TIMEOUT="${DEV_LIVE_TIMEOUT:-900}"   # seconds; first start builds the frontend

die()  { echo "dev-live: ERROR: $*" >&2; exit 1; }
info() { echo "dev-live: $*"; }

ensure_devdata() {
    if [[ ! -d "$DEVDATA" ]]; then
        install -d -m 700 "$DEVDATA" || die "cannot create $DEVDATA"
    fi
    local mode owner
    mode=$(stat -c '%a' "$DEVDATA")
    owner=$(stat -c '%u' "$DEVDATA")
    [[ "$owner" == "$(id -u)" ]] || die "$DEVDATA not owned by current user (owner uid=$owner)"
    if [[ "$mode" != "700" ]]; then
        chmod 700 "$DEVDATA" || die "cannot chmod 700 $DEVDATA"
    fi
}

pgid_file_state() {
    # echoes the recorded PGID if file exists and parses as a positive int
    [[ -f "$PGID_FILE" ]] || return 1
    local p
    p=$(cat "$PGID_FILE" 2>/dev/null) || return 1
    [[ "$p" =~ ^[0-9]+$ ]] && [[ "$p" -gt 1 ]] || return 1
    echo "$p"
}

group_live() {  # group_live <pgid>
    kill -0 "$1" 2>/dev/null
}

group_verified() {  # group_verified <pgid>: PGID exists AND its cmdline belongs to this checkout
    local pgid="$1" cmdline
    [[ -r "/proc/$pgid/cmdline" ]] || return 1
    cmdline=$(tr '\0' ' ' < "/proc/$pgid/cmdline")
    [[ "$cmdline" == *"$ROOT"* ]]
}

port_listener_pid() {
    # PID (if any) of the process listening on 127.0.0.1:3000; empty if none/unknown
    ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1 || true
}

pid_pgid() { ps -o pgid= -p "$1" 2>/dev/null | tr -d ' '; }

port_busy() { ss -ltnH "sport = :$PORT" | grep -q .; }

service_state() { systemctl is-active ducklab 2>/dev/null || echo "unknown"; }

health_json() {
    # prints body only on a real 200; returns non-zero otherwise
    local code
    code=$(curl -sS -o /tmp/dev-live-health.$$.json -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null) || return 1
    [[ "$code" == "200" ]] || return 1
    cat /tmp/dev-live-health.$$.json
}

# ---------------------------------------------------------------------------
cmd_start() {
    ensure_devdata

    # 1. Refuse if a recorded dev group is still live.
    if pgid=$(pgid_file_state); then
        if group_live "$pgid"; then
            die "a dev instance is already recorded (PGID $pgid). Use: $0 status | $0 stop"
        fi
        info "stale dev.pgid ($pgid not live) — removing"
        rm -f "$PGID_FILE"
    fi

    # 2. Refuse if port 3000 is occupied on ANY address; print operator step
    #    if the production service is active. Never stop it ourselves.
    if port_busy; then
        echo "dev-live: ERROR: port $PORT is occupied:" >&2
        ss -ltnpH "sport = :$PORT" >&2 || true
        local st
        st=$(service_state)
        if [[ "$st" == "active" ]]; then
            cat >&2 <<EOF
dev-live: production service 'ducklab' is active and owns the port.
dev-live: operator step (NOT run by this script):
dev-live:     sudo systemctl stop ducklab
dev-live: then re-run: $0 start
EOF
        fi
        exit 1
    fi

    [[ -x "$VENV_REFLEX" ]] || die "missing $VENV_REFLEX (create the dev venv first)"

    # 3. Explicit environment, no fallbacks.
    local release_id="dev-$(date -u +%Y%m%dT%H%M%SZ)"

    # 4. Launch fully detached; record the REAL PGID from inside the session.
    #    setsid(1) only forks when it is already a process-group leader; a
    #    backgrounded child of a script subshell never is, so $$ inside the
    #    inner bash IS the new session's PGID. We prove it right after.
    setsid bash -c '
        echo $$ > "$1"
        exec env \
            DUCKLAB_DB_PATH="$2" \
            DUCKLAB_RELEASE_ID="$3" \
            "$4" run --env prod --single-port \
                --backend-host 127.0.0.1 --backend-port 3000 \
        </dev/null >> "$5" 2>&1
    ' _ "$PGID_FILE" "$DB" "$release_id" "$VENV_REFLEX" "$LOG" &

    sleep 2
    local pgid
    pgid=$(pgid_file_state) || die "dev process died before recording PGID — see $LOG"
    if ! group_live "$pgid"; then
        echo "dev-live: ERROR: dev process exited immediately (PGID $pgid). Last log lines:" >&2
        tail -30 "$LOG" >&2 || true
        rm -f "$PGID_FILE"
        exit 1
    fi

    # Prove the recorded value is the real process-group ID.
    local evidence
    evidence=$(ps -o pid,pgid,sid,args -g "$pgid" 2>/dev/null || true)
    [[ -n "$evidence" ]] || die "cannot list process group $pgid"
    awk -v p="$pgid" 'NR>1 && $2 != p {bad=1} END {exit bad}' <<<"$evidence" \
        || die "recorded PGID $pgid does not match actual process group:\n$evidence"
    info "PGID evidence (recorded=$pgid):"
    echo "$evidence" | sed 's/^/    /'
    info "DUCKLAB_RELEASE_ID=$release_id  DUCKLAB_DB_PATH=$DB"

    # 5. Wait for health 200 (first start builds the frontend).
    info "waiting up to ${START_TIMEOUT}s for $HEALTH_URL (first start builds the frontend)…"
    local deadline=$((SECONDS + START_TIMEOUT)) code body
    while (( SECONDS < deadline )); do
        if ! group_live "$pgid"; then
            echo "dev-live: ERROR: dev process died while starting. Last 30 log lines:" >&2
            tail -30 "$LOG" >&2 || true
            rm -f "$PGID_FILE"
            exit 1
        fi
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null || true)
        if [[ "$code" == "200" ]]; then
            body=$(health_json || true)
            break
        fi
        sleep 5
    done
    if [[ "${code:-}" != "200" ]]; then
        echo "dev-live: ERROR: health check timed out after ${START_TIMEOUT}s (last HTTP ${code:-none})." >&2
        echo "dev-live: NOT killing anything. Last 30 log lines:" >&2
        tail -30 "$LOG" >&2 || true
        echo "dev-live: check with: $0 status   |   stop with: $0 stop" >&2
        exit 1
    fi
    info "health 200; endpoint reports: ${body:-<unreadable>}"

    # 6. Success banner.
    cat <<EOF
dev-live: DEV INSTANCE RUNNING (PGID $pgid, release $release_id)
  Test:     https://app.theducklab.com/  (Caddy basic-auth/IP gate still applies)
  Log:      $LOG
  Status:   $0 status
  Stop:     $0 stop    (prints the operator steps to restore production)
EOF
}

# ---------------------------------------------------------------------------
cmd_bootstrap() {
    ensure_devdata
    # Refuse if the dev DB path would resolve outside .devdata.
    local resolved
    resolved=$(realpath -m "$DB")
    [[ "$resolved" == "$DEVDATA/"* ]] || die "DUCKLAB_DB_PATH escapes .devdata: $resolved"
    info "bootstrapping first admin on $DB (interactive)"
    DUCKLAB_DB_PATH="$DB" "$ROOT/.venv/bin/python" "$ROOT/scripts/bootstrap_admin.py"
}

# ---------------------------------------------------------------------------
cmd_stop() {
    ensure_devdata
    local pgid
    pgid=$(pgid_file_state) \
        || die "no readable/valid $PGID_FILE — changing nothing (fail safe)"

    # Verify BEFORE any signal: the PGID's own cmdline must reference this
    # checkout, and any port-3000 listener must belong to the same PGID.
    if ! group_verified "$pgid"; then
        die "PGID $pgid is not verified as this checkout's dev process (/proc/$pgid/cmdline missing or foreign) — refusing to signal"
    fi
    local lpid lpgid
    lpid=$(port_listener_pid || true)
    if [[ -n "${lpid:-}" ]]; then
        lpgid=$(pid_pgid "$lpid" || true)
        [[ "$lpgid" == "$pgid" ]] \
            || die "port $PORT listener PID $lpid is in PGID ${lpgid:-?}, not $pgid — refusing to signal"
    fi

    info "sending TERM to verified dev process group -$pgid"
    kill -TERM -- "-$pgid"
    local i
    for i in $(seq 1 30); do
        group_live "$pgid" || break
        sleep 1
    done
    if group_live "$pgid"; then
        info "still alive after 30s — sending KILL to the same verified group -$pgid"
        kill -KILL -- "-$pgid" || true
        sleep 2
    fi
    group_live "$pgid" \
        && die "process group $pgid still alive after KILL — NOT touching anything else; investigate manually"

    if port_busy; then
        echo "dev-live: ERROR: port $PORT still occupied after dev shutdown:" >&2
        ss -ltnpH "sport = :$PORT" >&2 || true
        die "resolve the listener before restoring production"
    fi
    rm -f "$PGID_FILE"
    info "dev instance stopped; port $PORT is free."

    cat <<'EOF'
dev-live: OPERATOR RESTORE STEPS (printed only — run them yourself):
    sudo systemctl start ducklab
    curl -fsS http://127.0.0.1:3000/api/health        # expect 200 + prod release id
    curl -sI https://app.theducklab.com/ | head -1     # expect 401 without credentials
EOF
}

# ---------------------------------------------------------------------------
cmd_status() {
    ensure_devdata
    echo "== dev-live status =="
    echo "checkout:        $ROOT"
    echo "dev db:          $DB $([[ -f "$DB" ]] && echo "(exists)" || echo "(missing)")"
    echo "log:             $LOG $([[ -f "$LOG" ]] && echo "(exists)" || echo "(missing)")"

    local pgid
    if pgid=$(pgid_file_state); then
        echo "dev.pgid:        $pgid"
        if group_live "$pgid"; then
            if group_verified "$pgid"; then
                echo "dev group:       LIVE and verified as this checkout"
            else
                echo "dev group:       LIVE but NOT verified (foreign cmdline) — will not be signalled"
            fi
            ps -o pid,pgid,sid,args -g "$pgid" 2>/dev/null | sed 's/^/    /' || true
        else
            echo "dev group:       not live (stale pgid file)"
        fi
    else
        echo "dev.pgid:        absent or invalid"
    fi

    echo "port $PORT:      $(ss -ltnH "sport = :$PORT" | grep -q . && echo "LISTENING" || echo "free")"
    ss -ltnpH "sport = :$PORT" 2>/dev/null | sed 's/^/    /' || true
    echo "ducklab.service: $(service_state)"

    local body
    if body=$(health_json); then
        echo "health:          200 $body"
    else
        echo "health:          unreachable"
    fi
}

# ---------------------------------------------------------------------------
main() {
    case "${1:-}" in
        start)     cmd_start ;;
        stop)      cmd_stop ;;
        status)    cmd_status ;;
        bootstrap) cmd_bootstrap ;;
        *) echo "usage: $0 {start|stop|status|bootstrap}" >&2; exit 2 ;;
    esac
}
main "$@"
