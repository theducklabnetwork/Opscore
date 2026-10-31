#!/usr/bin/env bash
# Run the full browser acceptance suite against a staged release build.
# Args: <release_dir> <port>. Each suite gets a freshly seeded disposable DB
# and a freshly restarted staged server (the rate limiter is DB-backed, so
# reseeding between suites is required). The server is started in its own
# session so it survives the caller's shell.
set -uo pipefail
DIR="$1"; PORT="$2"; DEV="${DUCKLAB_DEV:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ACCEPT_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ducklab-accept.XXXXXX")"
trap 'stop; rm -rf "$ACCEPT_TMP"' EXIT
DB="$ACCEPT_TMP/accept-$PORT.db"

start() {
    setsid bash -c "cd '$DIR' && exec env DUCKLAB_DB_PATH='$DB' \
        DUCKLAB_SECURE_COOKIES=0 DUCKLAB_TRUST_PROXY=0 \
        '$DIR/.venv/bin/reflex' run --env prod --single-port \
        --backend-host 127.0.0.1 --backend-port '$PORT'" \
        >"$ACCEPT_TMP/accept-stage.log" 2>&1 </dev/null &
    local i
    for i in $(seq 1 45); do
        curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/api/health" && return 0
        sleep 2
    done
    return 1
}
stop() { pkill -f "backend-port $PORT" 2>/dev/null; sleep 3; }

run() {
    local seed="$1" test="$2"
    stop
    DUCKLAB_UI_DB="$DB" "$DEV/.venv/bin/python" "$DEV/scripts/$seed" >/dev/null 2>&1
    start || { echo "$test: STAGE FAILED"; return 1; }
    # Browser suites run with the release's own validation venv when present
    # (independent of the development venv); fall back to the dev venv for
    # ad-hoc QA against a plain checkout.
    local PY="$DEV/.venv/bin/python"
    [[ -x "$DIR/.venv-test/bin/python" ]] && PY="$DIR/.venv-test/bin/python"
    DUCKLAB_UI="http://127.0.0.1:$PORT" DUCKLAB_API="http://127.0.0.1:$PORT" timeout 420 \
        "$PY" "$DEV/scripts/$test" > "$ACCEPT_TMP/accept-$test.log" 2>&1
    local rc=$?
    echo "$test rc=$rc PASS=$(grep -cE '^PASS' "$ACCEPT_TMP/accept-$test.log") FAIL=$(grep -cE '^FAIL' "$ACCEPT_TMP/accept-$test.log")"
    grep -E '^FAIL' "$ACCEPT_TMP/accept-$test.log" | head -5
    return $rc
}

FAIL=0
run seed_ui_expenses.py browser_test_security.py || FAIL=1
run seed_ui_ops.py      browser_test_ops.py      || FAIL=1
run seed_ui_daily.py    browser_test_daily.py    || FAIL=1
run seed_ui_expenses.py browser_test_expenses.py || FAIL=1
run seed_ui_manpower.py browser_test_manpower.py || FAIL=1
stop
exit $FAIL
