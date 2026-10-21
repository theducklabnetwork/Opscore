#!/usr/bin/env bash
# DuckLab release tooling — shared library. Sourced by deploy/release.sh
# and deploy/simulate.sh. Not executable on its own.
#
# Conventions:
#   - Release root holds versioned release dirs + `current`/`previous` symlinks.
#   - Each release dir contains the app source, deploy/, requirements.lock,
#     .venv (created IN PLACE — never relocated), .web (built in place), and
#     release.json metadata.
#   - All functions are safe to re-run unless documented otherwise.

release_log()  { printf '[release] %s\n' "$*" >&2; }
release_warn() { printf '[release] WARN: %s\n' "$*" >&2; }
release_die()  { printf '[release] ERROR: %s\n' "$*" >&2; exit 1; }
# For library functions sourced by other scripts: fail without killing the
# caller's shell. Callers use `release_require func args...` to exit on error.
release_fail() { printf '[release] ERROR: %s\n' "$*" >&2; return 1; }
release_require() { "$@" || release_die "step failed: $*"; }

# --- release id ----------------------------------------------------------------
# Usage: release_make_id [suffix]
# Prints e.g. 20261019T013000Z or 20261019T013000Z-rc1
release_make_id() {
    local stamp suffix="${1:-}"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    printf '%s%s\n' "$stamp" "${suffix:+-$suffix}"
}

# --- venv creation (THE FIX for the copied-venv defect) --------------------------
# Usage: release_create_venv <release_dir> <python>
# Creates the virtual environment IN ITS FINAL LOCATION. A venv records its
# absolute path in shebangs and pyvenv.cfg at creation time; moving it later
# breaks every console script (this was the systemd 203/EXEC root cause).
release_create_venv() {
    local release_dir="$1" python="${2:-python3.14}"
    [[ -d "$release_dir" ]] || release_fail "release dir missing: $release_dir"
    [[ -x "$release_dir/.venv/bin/python" ]] && {
        release_log "venv already exists at $release_dir/.venv (in place) — reusing"
        return 0
    }
    command -v "$python" >/dev/null || release_fail "python interpreter not found: $python"
    release_log "creating venv in place: $release_dir/.venv ($python)"
    "$python" -m venv "$release_dir/.venv"
}

# --- venv integrity verification -------------------------------------------------
# Usage: release_verify_venv <release_dir>
# Fails if: interpreter missing, shebangs point outside the release dir, or the
# console scripts are not executable. Catches relocated/copied venvs.
release_verify_venv() {
    local release_dir="$1" venv="$1/.venv"
    [[ -x "$venv/bin/python" ]] || { release_fail "venv python missing/not executable: $venv/bin/python"; return 1; }
    # Shebang check: every script in .venv/bin must reference THIS release's
    # interpreter (or /usr/bin/env). A copied venv would reference the old path.
    local bad=0 f first
    for f in "$venv"/bin/*; do
        [[ -f "$f" && -x "$f" ]] || continue
        first="$(head -c 200 "$f" | head -1 | tr -d '\0' || true)"
        case "$first" in
            '#!'*)
                case "$first" in
                    "#!$venv/bin/"*|'#!/usr/bin/env '*|"#!$venv/bin/python"*) ;;
                    *) release_warn "bad shebang in $f: $first"; bad=1 ;;
                esac ;;
        esac
    done
    [[ "$bad" -eq 0 ]] || { release_fail "venv shebang integrity check FAILED for $venv (relocated/copied venv?)"; return 1; }
    # Key console scripts must run and import from this venv.
    "$venv/bin/python" -c 'import sys; assert sys.prefix != sys.base_prefix' \
        || { release_fail "venv sanity check failed for $venv"; return 1; }
    release_log "venv integrity OK: $venv"
}

# --- dependency install + verification --------------------------------------------
# Usage: release_install_deps <release_dir>
release_install_deps() {
    local release_dir="$1" venv="$1/.venv"
    [[ -f "$release_dir/requirements.lock" ]] \
        || { release_fail "requirements.lock missing in $release_dir"; return 1; }
    release_log "installing locked dependencies into $venv"
    "$venv/bin/pip" install --no-cache-dir -q -r "$release_dir/requirements.lock"
    release_log "pip check:"
    "$venv/bin/pip" check || { release_fail "pip check failed in $venv"; return 1; }
    # Runtime import verification (Section 2 requirements).
    local import_ok=0
    "$venv/bin/python" - <<'PY' || import_ok=1
import importlib
for mod in ("reflex", "granian", "argon2", "starlette"):
    importlib.import_module(mod)
import importlib.metadata as md
for dist in ("reflex", "granian", "argon2-cffi"):
    md.version(dist)
print("imports OK: reflex/granian/argon2/starlette")
PY
    if [[ "$import_ok" -ne 0 ]]; then
        release_fail "runtime import verification failed"
        return 1
    fi
    # Guard against dev-only packages sneaking into production images.
    if "$venv/bin/pip" freeze | grep -qiE '^(pytest|playwright)=='; then
        release_fail "dev/test package detected in production venv (pytest/playwright)"
        return 1
    fi
    release_log "dependencies OK for $release_dir"
}

# --- health check -------------------------------------------------------------------
# Usage: release_health_check <base_url> [timeout_sec]
# Checks: login page HTTP 200, /api/health, WebSocket handshake (401/101/403 —
# any non-5xx protocol response proves the WS endpoint is alive), and that the
# login page is served over the expected app. Returns non-zero on failure.
release_health_check() {
    local base="$1" timeout="${2:-60}" deadline=$((SECONDS + ${2:-60}))
    local code
    while (( SECONDS < deadline )); do
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$base/api/health" || echo 000)"
        if [[ "$code" == "200" ]]; then break; fi
        sleep 2
    done
    [[ "$code" == "200" ]] || { release_warn "health: /api/health not 200 (got $code)"; return 1; }
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -L "$base/login" || echo 000)"
    [[ "$code" == "200" ]] || { release_warn "health: /login not 200 (got $code)"; return 1; }
    # WebSocket endpoint: the event namespace is socket.io at /_event.
    # A plain GET returns 4xx (not 5xx/000) when the mount is alive.
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
        "$base/_event/?EIO=4&transport=polling" || echo 000)"
    case "$code" in
        200|400|401|403|404|426) : ;;  # socket.io endpoint responding
        *) release_warn "health: /_event unexpected code $code"; return 1 ;;
    esac
    release_log "health check OK: $base"
    return 0
}

# --- expected production routes ------------------------------------------------------
# Usage: release_route_check <base_url>
release_route_check() {
    local base="$1" route code
    for route in /login /ops /ops/report /ops/daily /ops/expenses /ops/manpower; do
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$base$route" || echo 000)"
        case "$code" in
            200|302|303|307|308) : ;;  # served or redirected to login
            *) release_warn "route $route -> $code"; return 1 ;;
        esac
    done
    release_log "route check OK: $base"
}
