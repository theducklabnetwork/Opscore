# Run Opscore from source on Windows

These steps run the source checkout directly. They do not create a package or installer.

## Requirements

- Windows 10 or 11 (64-bit)
- [Python 3.14](https://www.python.org/downloads/)
- Git

The first frontend export downloads its build tools and dependencies, so internet access is required during setup and builds.
Use a normal PowerShell window. Opscore opens in a native WebView2 window when the optional Windows dependency below is installed. Without it, Opscore tries Microsoft Edge app mode and then the default browser.

## Set up the checkout

Open PowerShell, then run:

```powershell
git clone https://github.com/duckl1ngz/Opscore.git ducklab-dev
cd ducklab-dev
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
```

If the repository is already cloned, replace the first command with `cd` to that checkout. The Windows compatibility changes are included in the current source; no separate compatibility branch is needed.

### Optional: native app window

For the proper Opscore window instead of a browser fallback, install the Windows-only pywebview dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
```

The native window uses Microsoft Edge WebView2 Runtime, which is normally included with current Windows 10/11 systems. If it is missing, install the **Evergreen Standalone Installer** from [Microsoft's WebView2 download page](https://developer.microsoft.com/microsoft-edge/webview2/). Opscore automatically falls back to Edge app mode or the default browser if pywebview or WebView2 cannot start.

## Build the frontend once

The desktop launcher serves a compiled frontend and does not compile it on every launch. Run this after cloning and whenever frontend source files change:

```powershell
$env:OPSCORE_DESKTOP = "1"
$env:DUCKLAB_DB_PATH = "$env:TEMP\opscore-frontend-build.db"
$env:REFLEX_ENV_MODE = "prod"
$env:REFLEX_CHECK_LATEST_VERSION = "false"
.\.venv\Scripts\python.exe -m reflex export --env prod --frontend-only --no-zip
Remove-Item Env:OPSCORE_DESKTOP, Env:DUCKLAB_DB_PATH, Env:REFLEX_ENV_MODE, Env:REFLEX_CHECK_LATEST_VERSION
```

The first export downloads the frontend build tools and npm dependencies. The build database is disposable and is not the desktop app's database.

## Run

From the repository root:

```powershell
.\.venv\Scripts\python.exe packaging\opscore_desktop.py
```

Opscore starts its backend and opens **http://127.0.0.1:47811/** in this order:

1. A 1280×800 pywebview window titled **Opscore**, using WebView2 and the Opscore icon.
2. Microsoft Edge in `--app` mode if pywebview or WebView2 is unavailable.
3. The default browser if Edge cannot start.

Closing the native window or Edge app window stops the backend and releases the single-instance lock. In default-browser mode, keep the launcher PowerShell window open and press **Ctrl+C** there to stop Opscore; closing only the browser tab does not stop it.

On first launch, create the Super Admin account. If port `47811` is already occupied, close the application using it before starting Opscore.

## Data locations

| Data | Windows path |
| --- | --- |
| Database | `%LOCALAPPDATA%\Opscore\store_ops.db` |
| Log | `%LOCALAPPDATA%\Opscore\opscore.log` |
| Pre-restore backups | `%LOCALAPPDATA%\Opscore\backups\` |
| Uploaded files | `%LOCALAPPDATA%\Opscore\uploaded_files\` |
| Reflex runtime state | `%LOCALAPPDATA%\Opscore\states\` and `%LOCALAPPDATA%\Opscore\reflex-web\` |
| Schedule CSV exports | `%USERPROFILE%\Documents\Opscore Schedules\` |

Linux continues to use `~/.local/share/opscore` and `~/Documents`.
