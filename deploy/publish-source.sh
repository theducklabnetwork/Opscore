#!/usr/bin/env bash
# publish-source.sh — publish a FILTERED source snapshot of the development
# checkout as a single audited tar archive, readable by the release identity
# (ducklab), WITHOUT granting the release identity any access to /home/duck
# and WITHOUT requiring nested files to carry the ducklab group.
#
# Design: instead of rsyncing a tree (which makes every nested directory
# depend on setgid group inheritance), the publisher stages content in a
# PRIVATE scratch dir, audits it, then packs a tar archive. Only two things
# need to be readable by ducklab:
#   1. the snapshot directory   (group inherited via setgid parent)
#   2. the archive file         (group inherited likewise)
# The release pipeline extracts the archive AS ducklab, so all extracted
# content is owned by the release identity natively. No chgrp, no setgid
# tree propagation, no group membership for duck.
#
# Run UNPRIVILEGED as the development user (duck). The staging root must be
# pre-created ONCE by the operator:
#   sudo install -d -o duck -g ducklab -m 2750 /opt/ducklab-staging
# (duck can write; ducklab can read via group; no world access.)
#
# Usage:
#   deploy/publish-source.sh [--dest-root DIR] [--id NAME]
# Env:
#   DUCKLAB_SOURCE_STAGING  staging root (default /opt/ducklab-staging)
#   DUCKLAB_SOURCE_DIR      source checkout (default: repo root)
#
# The snapshot is created at its FINAL path and never moved. The printed
# path is what release.sh consumes:
#   deploy/release.sh prepare --src <snapshot>/source.tar.gz
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${DUCKLAB_SOURCE_DIR:-$(cd "$HERE/.." && pwd)}"
DEST_ROOT="${DUCKLAB_SOURCE_STAGING:-/opt/ducklab-staging}"
SNAP_ID=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest-root) DEST_ROOT="$2"; shift 2 ;;
        --id)        SNAP_ID="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

fail() { echo "[publish] ERROR: $*" >&2; exit 1; }
log()  { echo "[publish] $*"; }

[[ -f "$SRC/rxconfig.py" && -d "$SRC/ducklab" ]] \
    || fail "not a DuckLab source tree: $SRC"
# Refuse to run if the source itself is a symlink (unexpected layouts).
[[ ! -L "$SRC" ]] || fail "source dir is a symlink: $SRC"
[[ -d "$DEST_ROOT" ]] || fail "staging root missing: $DEST_ROOT (operator: sudo install -d -o duck -g ducklab -m 2750 $DEST_ROOT)"
[[ -w "$DEST_ROOT" ]] || fail "staging root not writable by $(id -un): $DEST_ROOT"
# Staging root must be: owned by the invoking user, group = release group
# (ducklab), mode 2750 (setgid so the snapshot dir + archive inherit the
# group — the ONLY inheritance this design needs).
ROOT_GROUP="$(stat -c '%G' "$DEST_ROOT")"
ROOT_OWNER="$(stat -c '%U' "$DEST_ROOT")"
ROOT_MODE="$(stat -c '%a' "$DEST_ROOT")"
[[ "$ROOT_OWNER" == "$(id -un)" ]] \
    || fail "staging root must be owned by $(id -un), is $ROOT_OWNER (operator: sudo chown $(id -un):ducklab $DEST_ROOT)"
[[ "$ROOT_GROUP" != "$(id -gn)" || -n "${DUCKLAB_PUBLISH_TEST_GROUP:-}" ]] \
    || fail "staging root group must be the RELEASE group (ducklab), not the publisher's own group ($ROOT_GROUP): sudo chgrp ducklab $DEST_ROOT"
[[ "$ROOT_MODE" == "2750" ]] \
    || fail "staging root must be mode 2750 (setgid), is $ROOT_MODE: sudo chmod 2750 $DEST_ROOT"
id "$ROOT_GROUP" >/dev/null 2>&1 || [[ -n "${DUCKLAB_PUBLISH_TEST_GROUP:-}" ]] \
    || fail "staging group '$ROOT_GROUP' is not a valid account (expected the release identity, ducklab)"
command -v tar >/dev/null || fail "tar not available"

SNAP_ID="${SNAP_ID:-src-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$SNAP_ID" =~ ^[A-Za-z0-9._-]+$ ]] || fail "unsafe snapshot id: $SNAP_ID"
DEST="$DEST_ROOT/$SNAP_ID"
[[ ! -e "$DEST" ]] || fail "snapshot already exists: $DEST"

# Private scratch space (publisher-owned only; cleaned on any exit).
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/ducklab-publish.XXXXXX")"
chmod 700 "$SCRATCH"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT
WORK="$SCRATCH/content"
mkdir -p "$WORK"

# ---------------------------------------------------------------------------
# EXPLICIT include rules — only these top-level entries are ever published.
# Anything not listed here (dev venv, .web build dir, .git, local state,
# ad-hoc files) never leaves /home/duck.
# ---------------------------------------------------------------------------
INCLUDES=(ducklab rxconfig.py assets scripts deploy tests
          requirements.txt requirements.lock requirements-dev.txt
          AGENTS.md README.md)

rsync_args=(
    -a --delete
    --no-links           # skip symlinks entirely — the audit rejects any
                         # that slip through; the tree must not rely on them
    # secrets / credentials
    --exclude '.env' --exclude '.env.*' --exclude '*.env'
    --exclude '*.pem' --exclude '*.key' --exclude '*.p12' --exclude '*.pfx'
    --exclude 'id_rsa*' --exclude 'id_ed25519*' --exclude '*.keystore'
    # databases (any nesting depth) + sqlite sidecars
    --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm'
    --exclude '*.sqlite' --exclude '*.sqlite3'
    # venvs / build / runtime caches
    --exclude '.venv*/' --exclude '.web/' --exclude '.states/'
    --exclude '__pycache__/' --exclude '*.pyc' --exclude '.pytest_cache/'
    --exclude 'node_modules/' --exclude '.mypy_cache/' --exclude '.ruff_cache/'
    # vcs metadata
    --exclude '.git/' --exclude '.gitignore'
    # sensitive test artifacts
    --exclude 'docs/screenshots/' --exclude '*.png' --exclude '*.har'
    --exclude '*.log' --exclude '*.tmp' --exclude '*~' --exclude '.DS_Store'
)

for item in "${INCLUDES[@]}"; do
    [[ -e "$SRC/$item" ]] || fail "expected source item missing: $item"
done
rsync "${rsync_args[@]}" "${INCLUDES[@]/#/$SRC/}" "$WORK/"

# ---------------------------------------------------------------------------
# Content audit on the STAGED copy (before archiving — catches anything the
# exclude list missed).
# ---------------------------------------------------------------------------
AUDIT_FAIL=0
audit() { # audit <description> <find-results>
    if [[ -n "$2" ]]; then
        echo "[publish] AUDIT FAIL: $1:" >&2
        echo "$2" | sed 's/^/    /' >&2
        AUDIT_FAIL=1
    fi
}
audit "database files"        "$(find "$WORK" \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' -o -name '*.sqlite*' \) | head)"
audit "environment/secret files" "$(find "$WORK" \( -name '.env*' -o -name '*.env' -o -name '*.pem' -o -name '*.key' -o -name 'id_rsa*' -o -name 'id_ed25519*' \) | head)"
audit "virtual environments"  "$(find "$WORK" -maxdepth 2 -name '.venv*' -type d | head)"
audit "vcs metadata"          "$(find "$WORK" -name '.git' | head)"
audit "runtime caches"        "$(find "$WORK" \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.states' -o -name '.web' \) | head)"
audit "symlinks (none allowed)" "$(find "$WORK" -type l | head)"
audit "non-regular entries (devices/fifos/sockets)" "$(find "$WORK" ! -type f ! -type d | head)"
audit "screenshots/logs/tmp"  "$(find "$WORK" \( -name '*.png' -o -name '*.log' -o -name '*.tmp' -o -name '*~' \) | head)"
# Secret-content scan: common credential patterns in staged text files.
# This script's own regex literals would self-match; exclude it explicitly.
audit "credential-like content" "$(grep -rIlE '(BEGIN [A-Z ]*PRIVATE KEY|AWS_SECRET_ACCESS_KEY|api[_-]?key["'"'"']?[[:space:]]*[:=])' "$WORK" 2>/dev/null | grep -v 'test\|docs\|publish-source.sh' | head)"
[[ "$AUDIT_FAIL" -eq 0 ]] || fail "staged content failed security audit — nothing published"
# Executables must survive publishing (release.sh, acceptance.sh, ...).
[[ -x "$WORK/deploy/release.sh" ]] \
    || fail "deploy/release.sh lost its execute bit in staging"

# ---------------------------------------------------------------------------
# Build the archive. Paths are relative to $WORK ('./ducklab/...'), never
# absolute. Ownership/modes are normalized on extraction by release.sh;
# here we make the tar deterministically duck-owned (uid/gid 0 recorded as
# the publisher — extraction uses --no-same-owner).
# ---------------------------------------------------------------------------
ARCHIVE="$SCRATCH/source.tar.gz"
tar -C "$WORK" --format=posix --sort=name \
    --owner=0 --group=0 --numeric-owner \
    -czf "$ARCHIVE" .

# ---------------------------------------------------------------------------
# Archive audit: paths must be relative, no '..', no unsafe member types.
# ---------------------------------------------------------------------------
LIST="$SCRATCH/listing"
tar -tzvf "$ARCHIVE" > "$LIST"
AUDIT_FAIL=0
audit "absolute paths"      "$(awk '$NF ~ /^\// {print $NF}' "$LIST" | head)"
audit "parent traversal"    "$(awk '$NF ~ /(^|\/)\.\.(\/|$)/ {print $NF}' "$LIST" | head)"
audit "symlinks in archive" "$(awk '$1 ~ /^l/ {print $NF}' "$LIST" | head)"
audit "hardlinks in archive" "$(awk '$1 ~ /^h/ {print $NF}' "$LIST" | head)"
audit "device/fifo members" "$(awk '$1 ~ /^[bcp]/ {print $NF}' "$LIST" | head)"
[[ "$AUDIT_FAIL" -eq 0 ]] || fail "archive failed security audit — nothing published"

# ---------------------------------------------------------------------------
# Publish: snapshot dir + archive only. Setgid root gives both the release
# group automatically. No nested tree is ever created in the staging area.
# ---------------------------------------------------------------------------
mkdir -p "$DEST"          # group inherited via setgid parent
[[ "$(stat -c '%G' "$DEST")" == "$ROOT_GROUP" ]] \
    || { rm -rf "$DEST"; fail "snapshot dir did not inherit group $ROOT_GROUP"; }
cp "$ARCHIVE" "$DEST/source.tar.gz"
sha256sum "$DEST/source.tar.gz" | awk '{print $1"  source.tar.gz"}' > "$DEST/SHA256SUMS"
# Lock down: dir 2750, files 640 — group can traverse+read, no world.
chmod 2750 "$DEST"
chmod 640 "$DEST/source.tar.gz" "$DEST/SHA256SUMS"
[[ -r "$DEST/source.tar.gz" && -x "$DEST" ]] \
    || { rm -rf "$DEST"; fail "published snapshot not group-accessible"; }
[[ "$(stat -c '%G' "$DEST/source.tar.gz")" == "$ROOT_GROUP" ]] \
    || { rm -rf "$DEST"; fail "archive did not inherit group $ROOT_GROUP"; }
# No world access anywhere in the snapshot.
[[ -z "$(find "$DEST" -perm -o+rwx)" ]] \
    || { rm -rf "$DEST"; fail "world-accessible entry in snapshot"; }

cat <<EOF
[publish] snapshot published: $DEST
[publish] archive: $DEST/source.tar.gz ($(stat -c '%U:%G %a' "$DEST/source.tar.gz"))
[publish] next (as the release identity):
    deploy/release.sh prepare --src $DEST/source.tar.gz
EOF
