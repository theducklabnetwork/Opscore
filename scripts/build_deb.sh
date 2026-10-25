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
if [[ -z ${UV:-} ]]; then
    UV="$ROOT/dist/build-tools/uv"
    mkdir -p "${UV%/*}"
    curl -fsSL --retry 3 https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="${UV%/*}" sh
fi
if [[ ! -x $UV ]]; then
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
"$UV" python install 3.14
PYTHON_BIN=$(env -u VIRTUAL_ENV "$UV" python find --managed-python 3.14)
if [[ $PYTHON_BIN == *"/.venv/"* || ! -x $PYTHON_BIN ]]; then
    printf 'uv did not provide a standalone managed Python 3.14 interpreter.\n' >&2
    exit 1
fi
PYTHON_HOME=$(dirname -- "$(dirname -- "$PYTHON_BIN")")
if [[ ! -x $PYTHON_HOME/bin/python3.14 ]]; then
    printf 'uv did not provide a managed Python 3.14 runtime.\n' >&2
    exit 1
fi
cp -a "$PYTHON_HOME" "$APP/python"
# nFPM trees do not preserve symlinks. Copy python3, the sole interpreter used
# by the package, to a real executable and remove the remaining build-only links.
rm -f "$APP/python/bin/python3"
cp "$APP/python/bin/python3.14" "$APP/python/bin/python3"
find "$APP/python/bin" -maxdepth 1 -type l -delete
"$UV" pip install --python "$APP/python/bin/python3" --break-system-packages --no-cache \
    -r "$ROOT/requirements.lock"
# PyGObject is compiled for the bundled CPython ABI; GTK/WebKit themselves stay
# system libraries declared in Debian Depends, not private bundled copies.
PYTHON_VERSION=$("$APP/python/bin/python3" -I -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [[ $PYTHON_VERSION != 3.14 ]]; then
    printf 'Expected Python 3.14, got %s.\n' "$PYTHON_VERSION" >&2
    exit 1
fi
for tool in pkg-config meson ninja gcc; do
    command -v "$tool" >/dev/null || {
        printf 'PyGObject build requires %s (install libgirepository1.0-dev meson ninja-build).\n' "$tool" >&2
        exit 1
    }
done
pkg-config --exists gobject-introspection-1.0 cairo || {
    printf 'PyGObject build requires libgirepository1.0-dev.\n' >&2
    exit 1
}
"$UV" pip install --python "$APP/python/bin/python3" --break-system-packages --no-cache \
    --no-binary pygobject pygobject==3.50.0
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
# A clean checkout has no .web: Reflex creates it and installs its own Bun plus
# locked frontend dependencies. An existing checkout can reuse its toolchain.
if [[ -d "$ROOT/.web" ]]; then
    cp -a "$ROOT/.web" "$APP/.web"
fi
if [[ -d "$ROOT/reflex.lock" ]]; then
    cp -a "$ROOT/reflex.lock" "$APP/reflex.lock"
fi
(
    cd "$APP"
    DUCKLAB_DB_PATH="$WORK/build.db" \
    DUCKLAB_RELEASE_ID="$VERSION" \
    REFLEX_CHECK_LATEST_VERSION=false \
    "$APP/python/bin/python3" -m reflex export --env prod --frontend-only --no-zip
)
[[ -f "$APP/.web/build/client/index.html" ]] || { printf 'Frontend export missing.\n' >&2; exit 1; }
[[ -f "$APP/.web/build/client/login.html" ]] || { printf 'Login frontend export missing.\n' >&2; exit 1; }
# Runtime needs only the compiled output; package.json and node_modules are omitted.
find "$APP/.web" -mindepth 1 -maxdepth 1 ! -name build -exec rm -rf -- {} +
mv "$APP/.web/build" "$APP/frontend"
rm -rf "$APP/.web" "$APP/reflex.lock" "$APP/.states" "$APP/uploaded_files"
find "$APP" -type d -name __pycache__ -prune -exec rm -rf -- {} +

printf '==> Validating bundled runtime\n'
[[ $("$APP/python/bin/python3" -I -c 'import sys; print(sys.version_info[:2])') == '(3, 14)' ]]
"$APP/python/bin/python3" -I -c 'import reflex, granian, argon2, openpyxl, webview, gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk'
! find "$APP" -name '*.db' -print -quit | grep -q .
[[ ! -e "$APP/.venv" ]]
# Console entry points are not used at runtime; the app invokes python -m.
# Remove their build-specific shebangs rather than relocating them with sed.
find "$APP/python/bin" -maxdepth 1 -type f ! -name 'python*' ! -name 'pip*' -delete
if grep -R -l -F "$ROOT" "$APP/python/bin"; then
    printf 'Build path remains in the packaged runtime.\n' >&2
    exit 1
fi

printf '/opt/opscore\n' > "$APP/python/lib/python${PYTHON_VERSION}/site-packages/opscore-app.pth"

mkdir -p "$WORK/package/desktop" "$WORK/package/icons/hicolor"
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
