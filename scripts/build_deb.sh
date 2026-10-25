#!/usr/bin/env bash
# Build an offline, self-contained amd64 Opscore desktop package with nFPM.
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
VERSION=${1:-${OPSCORE_VERSION:-$(date -u +%Y.%m.%d)}}
if [[ ! $VERSION =~ ^[0-9][A-Za-z0-9.+~:-]*$ ]]; then
    printf 'Invalid Debian package version: %s\n' "$VERSION" >&2
    exit 2
fi

UV=${UV:-"$ROOT/.venv/bin/uv"}
if [[ ! -x $UV ]]; then
    UV=$(command -v uv || true)
fi
if [[ -z ${UV:-} || ! -x $UV ]]; then
    printf 'uv is required (set UV=/path/to/uv).\n' >&2
    exit 1
fi
if [[ $(uname -m) != x86_64 ]]; then
    printf 'This build targets amd64 and must run on x86_64.\n' >&2
    exit 1
fi
for command in curl tar dpkg-deb envsubst; do
    command -v "$command" >/dev/null || { printf 'Required build tool: %s\n' "$command" >&2; exit 1; }
done

DIST="$ROOT/dist"
mkdir -p "$DIST"
WORK="$DIST/build-deb"
rm -rf -- "$WORK"
APP="$WORK/package/app"
mkdir -p "$APP" "$WORK/tools" "$WORK/nfpm"

printf '==> Installing the nFPM build tool\n'
NFPM_VERSION=${NFPM_VERSION:-2.48.0}
curl -fsSL --retry 3 \
    "https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}/nfpm_${NFPM_VERSION}_Linux_x86_64.tar.gz" \
    | tar -xz -C "$WORK/tools" nfpm
NFPM="$WORK/tools/nfpm"

printf '==> Preparing standalone Python 3.14\n'
PYTHON_HOME=$("$UV" python find --managed-python 3.14)
PYTHON_HOME=$(dirname -- "$(dirname -- "$PYTHON_HOME")")
if [[ ! -x $PYTHON_HOME/bin/python3.14 ]]; then
    printf 'uv did not provide a managed Python 3.14 runtime.\n' >&2
    exit 1
fi
cp -a "$PYTHON_HOME" "$APP/python"
# nFPM tree entries preserve regular-file modes but do not package symlinks.
# The launcher and scripts always invoke python3.14, which is a real ELF file.
find "$APP/python/bin" -maxdepth 1 -type l -delete
"$APP/python/bin/python3.14" -m venv --copies "$APP/.venv"
"$UV" pip install --python "$APP/.venv/bin/python" --no-cache \
    -r "$ROOT/requirements.lock"
printf '==> Copying application (without databases or development data)\n'
mkdir -p "$APP/ducklab" "$APP/assets" "$APP/packaging"
for source in "$ROOT/ducklab"/*.py; do
    cp "$source" "$APP/ducklab/"
done
for source in "$ROOT/assets"/*; do
    [[ -f $source ]] && cp "$source" "$APP/assets/"
done
cp "$ROOT/rxconfig.py" "$APP/"
cp "$ROOT/packaging/opscore-launcher" "$ROOT/packaging/opscore-backend" \
    "$ROOT/packaging/opscore_backend.py" "$ROOT/packaging/opscore_desktop.py" "$APP/packaging/"
printf '%s\n' "$VERSION" > "$APP/RELEASE"

printf '==> Building the frontend once\n'
# Reuse the developer's already-resolved frontend toolchain; runtime files are
# still produced only by this export. This avoids resolving npm twice per build.
cp -a "$ROOT/.web" "$APP/.web"
(
    cd "$APP"
    DUCKLAB_DB_PATH="$WORK/build.db" \
    DUCKLAB_RELEASE_ID="$VERSION" \
    REFLEX_CHECK_LATEST_VERSION=false \
    "$APP/.venv/bin/reflex" export --env prod --frontend-only --no-zip
)
[[ -f "$APP/.web/build/client/index.html" ]] || { printf 'Frontend export missing.\n' >&2; exit 1; }
[[ -f "$APP/.web/build/client/login.html" ]] || { printf 'Login frontend export missing.\n' >&2; exit 1; }
# Runtime needs only the compiled output; package.json and node_modules are omitted.
find "$APP/.web" -mindepth 1 -maxdepth 1 ! -name build -exec rm -rf -- {} +
find "$APP" -type d -name __pycache__ -prune -exec rm -rf -- {} +

printf '==> Validating bundled runtime\n'
[[ $("$APP/.venv/bin/python" -c 'import sys; print(sys.version_info[:2])') == '(3, 14)' ]]
"$APP/.venv/bin/python" -c 'import reflex, granian, argon2, openpyxl, webview'
! find "$APP" -name '*.db' -print -quit | grep -q .
# uv's standalone CPython is relocatable; make the venv independent of every
# build-machine path as well. All console scripts use the packaged interpreter.
printf 'home = /opt/opscore/python/bin\ninclude-system-site-packages = false\nversion = 3.14\n' > "$APP/.venv/pyvenv.cfg"
while IFS= read -r script; do
    if [[ $(head -c 2 "$script") == '#!' ]]; then
        printf '#!/opt/opscore/.venv/bin/python\n' > "$script.tmp"
        tail -n +2 "$script" >> "$script.tmp"
        chmod 0755 "$script.tmp"
        mv "$script.tmp" "$script"
    fi
done < <(find "$APP/.venv/bin" -maxdepth 1 -type f)
rm -f "$APP/.venv/bin/activate" "$APP/.venv/bin/activate.csh" \
    "$APP/.venv/bin/activate.fish" "$APP/.venv/bin/Activate.ps1"
if grep -R -l -F "$ROOT" "$APP/.venv/bin" "$APP/.venv/pyvenv.cfg"; then
    printf 'Build path remains in the packaged runtime.\n' >&2
    exit 1
fi

mkdir -p "$WORK/package/desktop" "$WORK/package/icons/hicolor" "$WORK/package/modes"
cp "$APP/.venv/bin/python3.14" "$WORK/package/modes/venv-python"
cp "$APP/python/bin/python3.14" "$WORK/package/modes/base-python"
cp "$APP/packaging/opscore-launcher" "$APP/packaging/opscore-backend" "$WORK/package/modes/"
chmod 0755 "$WORK/package/modes"/*
cp "$ROOT/packaging/opscore.desktop" "$WORK/package/desktop/"
for size in 512 192 48; do
    icon_dir="$WORK/package/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$icon_dir"
    if [[ $size == 48 ]]; then
        cp "$ROOT/assets/favicon-48.png" "$icon_dir/opscore.png"
    else
        cp "$ROOT/assets/icon-${size}.png" "$icon_dir/opscore.png"
    fi
done
cp "$ROOT/packaging/postinst" "$ROOT/packaging/prerm" "$WORK/package/"
chmod 0755 "$WORK/package/postinst" "$WORK/package/prerm" \
    "$APP/packaging/opscore-launcher" "$APP/packaging/opscore-backend"

export OPSCORE_VERSION="$VERSION"
export OPSCORE_ROOT="$WORK/package"
envsubst < "$ROOT/packaging/nfpm.yaml" > "$WORK/nfpm/nfpm.yaml"

OUTPUT="$DIST/opscore_${VERSION}_amd64.deb"
rm -f -- "$OUTPUT"
"$NFPM" package --config "$WORK/nfpm/nfpm.yaml" --packager deb --target "$OUTPUT"

printf '==> Verifying package metadata\n'
dpkg-deb --field "$OUTPUT" Package Version Architecture
dpkg-deb --contents "$OUTPUT" >/dev/null
printf 'Built %s (%s bytes)\n' "$OUTPUT" "$(stat -c %s "$OUTPUT")"
