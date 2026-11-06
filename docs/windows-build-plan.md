# Opscore Windows build plan

> **Historical proposal.** This document records the original design options, not the current build procedure. The implemented installer build uses standalone CPython 3.14 from `scripts/build_windows.ps1` and Inno Setup, installed per user under `%LOCALAPPDATA%\Programs\Opscore`; it does not use the proposed PyInstaller + `%ProgramFiles%` layout. See `README.md` for current build instructions. Code signing, release checksums and real-machine install/upgrade/uninstall validation remain outstanding.

## Current package and Windows equivalents

The `.deb` installs standalone CPython 3.14 plus locked dependencies, app/config/assets/launchers, and the compiled frontend under `/opt/opscore`; it also installs a desktop entry/icons and refreshes desktop metadata. The menu launcher sets desktop mode and per-user paths, enforces one instance, starts the loopback backend, waits for `/api/health`, opens GTK/WebKit (with Chromium/`xdg-open` fallbacks), and stops the backend process group on exit. Uninstall preserves user data.

| Linux-only piece | Windows replacement |
| --- | --- |
| Bash build, nFPM/`dpkg-deb`, shell launchers, maintainer scripts | PowerShell/python build steps, PyInstaller executables, Inno Setup |
| `/opt/opscore`, `/usr/share` desktop entries/icons | `%ProgramFiles%\Opscore`, Start Menu shortcut, `.ico` icon |
| GTK/WebKit apt dependencies and PyGObject | pywebview EdgeChromium + WebView2 Runtime, or default browser |
| `~/.local/share/opscore`, `chmod`/`flock` | `%LOCALAPPDATA%\Opscore`, ACL defaults, named mutex/`LockFileEx` |
| process groups, `killpg`, Unix signals, `geteuid` | Job Object/process tree, `CTRL_BREAK_EVENT` then `TerminateProcess`; omit root handling |
| `wmctrl`, Chromium `--class`, `xdg-open` | existing focus endpoint/Win32 focus; `os.startfile`/default browser |
| systemd server service, journal, release symlinks | not needed for desktop; Windows Service + Event Log only for a future server package |

Node/Bun and frontend packages are build-time only. `reflex export --env prod --frontend-only` creates static client output, checked for the fixed `127.0.0.1:47811` endpoints; `.web/build` is served by the Python/Granian backend alongside API/WebSocket routes. `node_modules` and the JS toolchain are not packaged.

## Recommended build: PyInstaller one-folder + Inno Setup

Use one-folder rather than one-file to avoid extraction/startup overhead and make Reflex assets and diagnostics manageable. It is preferable to an embeddable-Python installer here because Reflex, its dynamic imports, pywebview backends, and data files need explicit runtime packaging; PyInstaller can freeze and smoke-test that graph on Windows without installing Python for users.

1. In a GitHub Actions `windows-latest` job, check out the tag, install Python 3.14 and `uv`, install `requirements.lock` plus a pinned Windows build-requirements file (PyInstaller; do not require PyGObject).
2. Build the production frontend once with `OPSCORE_DESKTOP=1`, a temporary database, and `REFLEX_ENV_MODE=prod`; assert both HTML entry points and the fixed loopback endpoints, then retain only compiled output.
3. Freeze a windowed launcher and console backend into one-folder output. Bundle `ducklab`, `rxconfig.py`, assets, `RELEASE`, and `.web/build`; include Reflex hooks/hidden imports/data only as shown necessary by tests. Never bundle databases or development data.
4. Smoke-test the unpacked folder on the runner: launch backend, verify health/API/frontend/WebSocket/login, open the launcher, close it, and verify its child processes stop and the database remains under `%LOCALAPPDATA%\Opscore`.
5. Build `Opscore-Setup-<version>.exe` with Inno Setup: per-machine or per-user install, Start Menu/desktop shortcuts, WebView2 Runtime detection/bootstrap if native windows are retained, upgrade-safe files, uninstall that preserves app data. Upload the installer and checksums as release artifacts; sign when a certificate is available.

## Launcher and data behavior

Create `%LOCALAPPDATA%\Opscore` for `store_ops.db`, `opscore.log`, lock/state, uploads, Reflex state files, and browser/webview storage. Acquire a per-user single-instance mutex; focus the existing instance or exit. Start the loopback-only backend with the same release/database/Reflex environment as Linux, hidden console windows, and redirected logs. Poll `http://127.0.0.1:47811/api/health` for up to 90 seconds, failing visibly if the child exits, the port is occupied, or startup times out. Open the URL in the default browser (optionally native pywebview later), wait for launcher/browser exit as appropriate, then gracefully stop the complete backend process tree and force-kill after timeout. Ensure normal exit, Ctrl+C, installer upgrade, and failure paths all clean up children without deleting data.

## Risks and small follow-ups

- Reflex/PyInstaller hidden imports, frontend paths, and the current Reflex web-directory monkey-patch may differ on Windows; validate frozen rather than source execution.
- WebView2 availability, browser auto-close behavior, firewall/antivirus prompts, long paths, and locked files during upgrades need real-machine testing.
- `argon2-cffi`, Granian, and all locked transitive wheels must be confirmed for Windows/Python 3.14; native dependencies must not be reused from Linux.
- Fixed-port conflicts, single-instance/focus behavior, Unicode usernames, non-ASCII install paths, and abrupt termination need coverage.

Follow-ups: add a small platform-path/process/lock abstraction; choose default-browser versus native-window behavior; write the spec/hooks and workflow; add frozen-app health and shutdown tests; test install/upgrade/uninstall/data preservation; add code signing and release checksums.
