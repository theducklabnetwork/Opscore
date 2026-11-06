# Run Opscore from source on Windows

These steps run the source checkout directly. They do not create a package or installer.

## Requirements

- Windows 10 or 11 (64-bit)
- [Python 3.14](https://www.python.org/downloads/)
- Git

Use a normal PowerShell window. pywebview and GTK are optional: without them, Opscore uses the default browser.

## Set up the checkout

Open PowerShell, then run:

```powershell
git clone https://github.com/duckl1ngz/Opscore.git ducklab-dev
cd ducklab-dev
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
```

If the repository is already cloned, replace the first command with `cd` to that checkout. Use its `windows-compat` branch if these changes have not been merged yet:

```powershell
git switch windows-compat
```

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

Opscore starts its backend and opens **http://127.0.0.1:47811/** in the default browser when pywebview/GTK is unavailable. Keep the launcher PowerShell window open while using Opscore. Close that window or press **Ctrl+C** there to stop the backend; closing only the browser tab does not stop it.

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
