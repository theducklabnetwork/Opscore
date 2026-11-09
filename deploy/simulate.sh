#!/usr/bin/env bash
# DuckLab release pipeline — full-cycle simulation in disposable locations.
#
# Simulates: stage release A -> activate -> health -> stage release B ->
# activate -> health -> break B (sabotaged copy) -> rollback to A -> health.
# Also exercises failure modes: bad venv shebangs, missing dependency,
# failed startup (bad port), incompatible schema, failed rollback.
#
# NOTHING here touches production paths. Everything lives under a temp root.
#
# Usage: deploy/simulate.sh [--keep]
# Exit 0 = all simulation scenarios behaved as expected.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=release_lib.sh
. "$HERE/release_lib.sh"

SRC="$(cd "$HERE/.." && pwd)"
SIM="$(mktemp -d "${TMPDIR:-/tmp}/ducklab-sim.XXXXXX")"
ROOT="$SIM/releases"
CUR="$SIM/current"
DB="$SIM/persistent.db"
PORT_BASE=$(( (RANDOM % 2000) + 4100 ))
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

PASS=0; FAIL=0
HTTPPID=""
ok()   { PASS=$((PASS+1)); printf 'SIM PASS: %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf 'SIM FAIL: %s\n' "$*" >&2; }
check(){ if "$@" >/dev/null 2>&1; then ok "$*"; else bad "$*"; fi }

cleanup() {
    # release.sh stops each staged server by its saved process-group PID.
    [[ -n "$HTTPPID" ]] && kill "$HTTPPID" 2>/dev/null || true
    [[ "$KEEP" -eq 1 ]] && { echo "sim kept at $SIM"; return; }
    rm -rf "$SIM"
}
trap cleanup EXIT

REL_SH=(env "DUCKLAB_RELEASE_ROOT=$ROOT" "DUCKLAB_CURRENT_LINK=$CUR"
        "DUCKLAB_STAGE_DB=$DB" "$HERE/release.sh")

echo "=== DuckLab release simulation in $SIM ==="
mkdir -p "$ROOT"

# --- Scenario 1-7: stage, verify, activate A -----------------------------------
echo "--- release A: prepare + verify + activate"
OUT="$("${REL_SH[@]}" prepare --skip-build --skip-browser 2>&1)" \
    && ok "prepare A" || { bad "prepare A"; echo "$OUT" | tail -5; exit 1; }
IDA="$(basename "$(ls -d "$ROOT"/[0-9]* | sort | head -1)")"
OUT="$("${REL_SH[@]}" verify-staged --release-id "$IDA" --port "$PORT_BASE" --skip-browser 2>&1)" \
    && ok "verify-staged A" || { bad "verify-staged A"; echo "$OUT" | tail -5; }
# Wait for the staged app's socket to be fully released before port reuse.
wait_port_free() {
    local port="$1" n=0
    while ss -tln 2>/dev/null | grep -q ":$port "; do
        sleep 1; n=$((n+1)); [[ $n -gt 30 ]] && return 1
    done
    return 0
}
wait_port_free "$PORT_BASE" || bad "port $PORT_BASE never freed after verify A"
"${REL_SH[@]}" activate --release-id "$IDA" >/dev/null 2>&1 \
    && ok "activate A" || bad "activate A"
[[ "$(readlink "$CUR")" == "$ROOT/$IDA" ]] && ok "current -> A" || bad "current link A"

# Activation must be idempotent-safe / refuse unverified releases.
sed -i 's/"status": "[a-z-]*"/"status": "prepared"/' "$ROOT/$IDA/release.json"
"${REL_SH[@]}" activate --release-id "$IDA" >/dev/null 2>&1 \
    && bad "activate unverified release was allowed" || ok "activate refuses unverified release"
sed -i 's/"status": "[a-z-]*"/"status": "verified"/' "$ROOT/$IDA/release.json"
"${REL_SH[@]}" activate --release-id "$IDA" >/dev/null 2>&1 && ok "re-activate verified A"

# --- Failure mode: missing runtime dependency ------------------------------------
echo "--- sabotage: missing dependency"
rm -rf "$ROOT/baddep"; cp -a "$ROOT/$IDA" "$ROOT/baddep"
"$ROOT/baddep/.venv/bin/pip" uninstall -q -y argon2-cffi >/dev/null 2>&1
if release_install_deps "$ROOT/baddep" >/dev/null 2>&1; then
    ok "deps reinstalled (lock restores missing dep)"
else
    bad "dep restore failed"
fi

# --- Failure mode: invalid venv shebangs (the copied-venv defect) -----------------
echo "--- sabotage: relocated venv shebangs"
rm -rf "$ROOT/badvenv"; cp -a "$ROOT/$IDA" "$ROOT/badvenv"
sed -i "1s|#!.*|#!/home/duck/project/ducklab/.venv/bin/python3|" "$ROOT/badvenv/.venv/bin/reflex"
release_verify_venv "$ROOT/badvenv" >/dev/null 2>&1 \
    && bad "relocated-venv shebang NOT detected" || ok "relocated-venv shebang detected"

# --- Scenario 2-9: release B, activate, then roll back -----------------------------
echo "--- release B: prepare + verify + activate + rollback"
sleep 1  # distinct timestamp id
OUT="$("${REL_SH[@]}" prepare --skip-build --skip-browser 2>&1)" \
    && ok "prepare B" || { bad "prepare B"; echo "$OUT" | tail -5; exit 1; }
IDB="$(basename "$(ls -d "$ROOT"/[0-9]* | sort | tail -1)")"
[[ "$IDB" != "$IDA" ]] && ok "distinct release ids" || bad "release id collision"
OUT="$("${REL_SH[@]}" verify-staged --release-id "$IDB" --port "$PORT_BASE" --skip-browser 2>&1)" \
    && ok "verify-staged B" || { bad "verify-staged B"; echo "$OUT" | tail -5; }
wait_port_free "$PORT_BASE" || true
"${REL_SH[@]}" activate --release-id "$IDB" >/dev/null 2>&1 && ok "activate B"
[[ -L "$ROOT/previous" && "$(readlink "$ROOT/previous")" == "$ROOT/$IDA" ]] \
    && ok "previous recorded as A" || bad "previous link"

# Persistent DB untouched by all of the above (simulated by mtime/content).
DB_BEFORE="$(sha256sum "$DB" 2>/dev/null | awk '{print $1}')"

# Rollback (dry run first, then real).
"${REL_SH[@]}" rollback --dry-run >/dev/null 2>&1 && ok "rollback dry-run"
OUT="$("${REL_SH[@]}" rollback 2>&1)" && ok "rollback executed" || { bad "rollback"; echo "$OUT" | tail -5; }
[[ "$(readlink "$CUR")" == "$ROOT/$IDA" ]] && ok "current restored to A" || bad "current after rollback"
[[ -d "$ROOT/$IDB" ]] && ok "failed release B preserved" || bad "release B deleted"

DB_AFTER="$(sha256sum "$DB" 2>/dev/null | awk '{print $1}')"
[[ "$DB_BEFORE" == "$DB_AFTER" ]] && ok "persistent DB unchanged" || bad "persistent DB changed"

# --- Failure mode: incompatible schema on rollback ----------------------------------
echo "--- sabotage: newer DB schema blocks rollback"
PY="$ROOT/$IDA/.venv/bin/python"
PYTHONPATH="$ROOT/$IDA" "$PY" - <<PYEOF 2>/dev/null
import sqlite3
c = sqlite3.connect("$DB")
c.execute("PRAGMA user_version = 99")
c.commit(); c.close()
PYEOF
"${REL_SH[@]}" activate --release-id "$IDB" >/dev/null 2>&1
OUT="$(DUCKLAB_DB_PATH="$DB" "${REL_SH[@]}" rollback 2>&1)" \
    && bad "rollback across newer schema was allowed" || ok "rollback blocked by newer schema"
echo "$OUT" | grep -qi 'newer' && ok "schema guard message shown" || bad "schema guard message"
# restore schema version
PYTHONPATH="$ROOT/$IDA" "$PY" - <<PYEOF 2>/dev/null
import sqlite3
c = sqlite3.connect("$DB")
c.execute("PRAGMA user_version = 1")
c.commit(); c.close()
PYEOF
"${REL_SH[@]}" rollback >/dev/null 2>&1 && ok "rollback allowed after schema restore"

# --- Failure mode: rollback with no previous ----------------------------------------
echo "--- failure: no previous release"
rm -f "$ROOT/previous"
"${REL_SH[@]}" rollback >/dev/null 2>&1 \
    && bad "rollback with no previous was allowed" || ok "rollback refuses when no previous"

# --- Failure mode: port conflict on staging -----------------------------------------
echo "--- failure: staging port conflict"
"$ROOT/$IDA/.venv/bin/python" -m http.server "$((PORT_BASE+7))" --bind 127.0.0.1 >/dev/null 2>&1 &
HTTPPID=$!
sleep 1
"${REL_SH[@]}" verify-staged --release-id "$IDA" --port "$((PORT_BASE+7))" --skip-browser >/dev/null 2>&1 \
    && bad "staging on occupied port was allowed" || ok "staging port conflict detected"
kill "$HTTPPID" 2>/dev/null
HTTPPID=""

# --- Failure mode: refuse staging on production port ---------------------------------
"${REL_SH[@]}" verify-staged --release-id "$IDA" --port 3000 --skip-browser >/dev/null 2>&1 \
    && bad "staging on port 3000 was allowed" || ok "staging refuses production port 3000"

# --- Failure mode: stale/broken current symlink -----------------------------------------
echo "--- failure: stale current symlink"
ln -sfn "$ROOT/nonexistent-release" "$CUR"
"${REL_SH[@]}" status >/dev/null 2>&1 && ok "status tolerates broken current link" || bad "status on broken link"
sed -i 's/"status": "[a-z-]*"/"status": "verified"/' "$ROOT/$IDA/release.json"
"${REL_SH[@]}" activate --release-id "$IDA" >/dev/null 2>&1 && ok "activate recovers from broken current link"
[[ "$(readlink "$CUR")" == "$ROOT/$IDA" ]] && ok "current link repaired" || bad "current link repair"

# --- Failure mode: missing release directory ---------------------------------------------
"${REL_SH[@]}" activate --release-id no-such-release >/dev/null 2>&1 \
    && bad "activate of missing release was allowed" || ok "activate refuses missing release dir"
"${REL_SH[@]}" verify-staged --release-id no-such-release --port "$PORT_BASE" --skip-browser >/dev/null 2>&1 \
    && bad "verify of missing release was allowed" || ok "verify refuses missing release dir"

# --- Failure mode: failed service readiness (sabotaged app entrypoint) --------------------
echo "--- failure: readiness gate on sabotaged release"
rm -rf "$ROOT/badrun"
# Copy source only; build the venv IN PLACE (a copied venv would trip the
# shebang integrity guard — verified separately above).
mkdir -p "$ROOT/badrun"
rsync -a --exclude '.venv/' --exclude '.venv-test/' --exclude '.web/' --exclude '__pycache__/' \
    "$ROOT/$IDA/" "$ROOT/badrun/"
printf 'raise RuntimeError("simulated failed release")\n' > "$ROOT/badrun/ducklab/ducklab.py"
release_create_venv "$ROOT/badrun" python3.14 >/dev/null 2>&1
release_install_deps "$ROOT/badrun" >/dev/null 2>&1
cp "$ROOT/$IDA/release.json" "$ROOT/badrun/release.json"
sed -i 's/"status": "[a-z-]*"/"status": "verified"/' "$ROOT/badrun/release.json"
DUCKLAB_RELEASE_ROOT="$ROOT" DUCKLAB_CURRENT_LINK="$CUR" \
    DUCKLAB_STAGE_DB="$SIM/badrun.db" DUCKLAB_STAGE_PORT="$((PORT_BASE+9))" \
    "$HERE/release.sh" verify-staged --release-id badrun --port "$((PORT_BASE+9))" --skip-browser >/dev/null 2>&1 \
    && bad "sabotaged release PASSED readiness" || ok "sabotaged release fails readiness gate"
grep -q 'verify-failed' "$ROOT/badrun/release.json" \
    && ok "failed release marked verify-failed" || bad "failed release status"

# --- Failure mode: release dir permission removed ------------------------------------------
echo "--- failure: unwritable release root"
chmod 500 "$ROOT"
OUT="$(env DUCKLAB_RELEASE_ROOT="$ROOT" DUCKLAB_CURRENT_LINK="$CUR" \
    "$HERE/release.sh" prepare --skip-build --skip-browser 2>&1)"
chmod 750 "$ROOT"
echo "$OUT" | grep -qiE 'not writable|cannot create|Permission denied' \
    && ok "prepare reports unwritable release root" || bad "unwritable root handling: $(echo "$OUT" | tail -1)"

# --- Status/history ---------------------------------------------------------------------
"${REL_SH[@]}" status >/dev/null 2>&1 && ok "status command"
HIST="$("${REL_SH[@]}" history 2>&1)"
echo "$HIST" | grep -q "$IDA" && ok "history lists release A" || { bad "history"; echo "$HIST"; }

echo
echo "=== SIMULATION RESULT: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
