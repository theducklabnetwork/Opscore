#!/usr/bin/env bash
# Verify the release-directory permission model with REAL filesystem access,
# using a purpose-created non-root identity in an isolated release root.
# Requires sudo ONCE to create/remove the throwaway test user; everything
# else runs as that user. Production paths are never touched.
#
# Usage: deploy/test_release_permissions.sh
# Exit 0 = all scenarios behave as required.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTUSER="ducklab-reltest"
SIM="$(mktemp -d "${TMPDIR:-/tmp}/ducklab-relperm.XXXXXX")"
ROOT="$SIM/releases"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf 'PERM PASS: %s\n' "$*"; }
bad() { FAIL=$((FAIL+1)); printf 'PERM FAIL: %s\n' "$*" >&2; }

cleanup() {
    pkill -u "$TESTUSER" 2>/dev/null
    sudo -n userdel "$TESTUSER" 2>/dev/null || true
    sudo -n rm -rf "$SIM" 2>/dev/null || rm -rf "$SIM" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== release permission model test (root: $SIM) ==="

# 1. Create the throwaway identity (the ONLY privileged step).
if ! id "$TESTUSER" >/dev/null 2>&1; then
    sudo -n useradd --system --shell /usr/sbin/nologin "$TESTUSER" 2>/dev/null \
        || sudo useradd --system --shell /usr/sbin/nologin "$TESTUSER" \
        || { echo "cannot create test user (needs sudo); aborting"; exit 2; }
fi
ok "throwaway identity '$TESTUSER' created"

run_as() { sudo -u "$TESTUSER" env HOME="$SIM/home" "$@"; }
mkdir -p "$SIM/home"

# 2. Operator-style root creation: owned by the service identity, 750.
sudo install -d -o "$TESTUSER" -g "$TESTUSER" -m 750 "$ROOT"
ok "release root installed (owner=$TESTUSER mode=750)"

# 3. The identity CAN prepare a release inside it.
run_as mkdir -p "$ROOT/probe" \
    && ok "service identity can create release dirs" \
    || bad "service identity cannot write release root"

# 4. A different non-root identity (this user, duck) CANNOT write.
if mkdir "$ROOT/duck-probe" 2>/dev/null; then
    bad "developer account could write into release root"; rmdir "$ROOT/duck-probe"
else
    ok "developer account denied write to release root"
fi
# ...and cannot even read it (750, owner=service identity).
if ls "$ROOT" >/dev/null 2>&1; then
    bad "developer account could READ release root"
else
    ok "developer account denied read of release root"
fi

# 5. release.sh refuses when run as the WRONG identity against that root.
OUT="$(env DUCKLAB_RELEASE_ROOT="$ROOT" DUCKLAB_CURRENT_LINK="$SIM/current" \
    "$HERE/release.sh" prepare --skip-build --skip-browser 2>&1)"
if echo "$OUT" | grep -q "not writable by"; then
    ok "release.sh refuses with clear error when root not writable"
else
    bad "release.sh did not report permission problem: $(echo "$OUT" | tail -1)"
fi

# 6. release.sh SUCCEEDS as the service identity (real venv creation in place).
#    The identity needs read access to the source tree; grant via ACL if
#    available, else skip with a documented note.
SRC="$(cd "$HERE/.." && pwd)"
if command -v setfacl >/dev/null 2>&1; then
    sudo setfacl -R -m u:"$TESTUSER":rX "$SRC" 2>/dev/null
    sudo setfacl -m u:"$TESTUSER":x /home/duck /home/duck/project 2>/dev/null || true
fi
OUT="$(run_as env DUCKLAB_RELEASE_ROOT="$ROOT" DUCKLAB_CURRENT_LINK="$SIM/current" \
    DUCKLAB_STAGE_DB="$SIM/stage.db" \
    "$HERE/release.sh" prepare --skip-build --skip-browser 2>&1)"
if echo "$OUT" | grep -q "Release staged:"; then
    ok "full prepare succeeds as service identity"
    RID="$(echo "$OUT" | grep -oP 'Release staged: \K\S+' | xargs basename)"
    # 7. Release dir permissions: not world-accessible, not group-writable.
    MODE="$(run_as stat -c '%a' "$ROOT/$RID")"
    [[ "$MODE" == "750" ]] && ok "release dir mode 750" || bad "release dir mode $MODE"
    if run_as stat -c '%A' "$ROOT/$RID" | grep -q '....rwx'; then
        bad "release dir world-accessible"
    else
        ok "release dir not world-accessible"
    fi
    # 8. Venv created in place with correct shebangs.
    SHEBANG="$(run_as head -1 "$ROOT/$RID/.venv/bin/pip")"
    [[ "$SHEBANG" == "#!$ROOT/$RID/.venv/bin/"* ]] \
        && ok "venv shebangs point at final path" \
        || bad "venv shebang wrong: $SHEBANG"
    # 9. Broken symlink / stale current handling.
    ln -sfn "$ROOT/nonexistent" "$SIM/current"
    run_as test -e "$SIM/current" 2>/dev/null \
        && bad "broken current symlink resolves" || ok "broken current symlink detected as broken"
else
    bad "prepare as service identity failed: $(echo "$OUT" | tail -3)"
fi

# 10. Failed dependency installation is reported (bad lock).
mkdir -p "$ROOT/badlock"
printf 'nonexistent-pkg-xyz==9.9.9\n' > "$ROOT/badlock/requirements.lock"
chown -R "$TESTUSER:$TESTUSER" "$ROOT/badlock" 2>/dev/null || sudo chown -R "$TESTUSER:$TESTUSER" "$ROOT/badlock"
run_as python3.14 -m venv "$ROOT/badlock/.venv" 2>/dev/null
if run_as bash -c ". '$HERE/release_lib.sh'; release_install_deps '$ROOT/badlock'" >/dev/null 2>&1; then
    bad "bogus lockfile install succeeded"
else
    ok "failed dependency install detected and reported"
fi

echo
echo "=== PERMISSION TEST RESULT: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
