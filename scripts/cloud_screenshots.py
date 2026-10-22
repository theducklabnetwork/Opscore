#!/usr/bin/env python3
"""Visual check in one command: fresh DB, seeds, prod server, screenshots.

For cloud VMs / disposable checkouts (no .devdata, no real data). Steps:
  1. create a fresh disposable DB in a temp dir and seed it:
       scripts/seed_ui_ops.py           (bootstraps admin@ducklab.com /
                                         AdminPass1234 non-interactively,
                                         stores A01 + B02, manpower config)
       scripts/seed_ui_sales.py         (S01)
       scripts/seed_ui_expenses_dev.py  (X01)
       scripts/seed_ui_actions_dev.py   (actions on A01)
     The --dev seeds hard-code the VPS path .devdata/dev.db; they are run
     here with their DEV_DB pointed at the disposable DB instead.
     scripts/seed_ui_stock.py is not layered on: it also creates A01/B02 and
     its --dev mode is a no-op once A01 exists.
  2. start `reflex run --env prod --single-port` against that DB;
  3. log in with Playwright and screenshot every /ops page at 1440px dark,
     1440px light and 390px mobile (full page), then each entry dialog
     (viewport, as OUT_DIR/<mode>/dialog-<name>.png) and, on mobile, the
     wide tables on their own (OUT_DIR/mobile/table-<name>.png);
  4. stop the server (the temp DB is deleted unless --keep-db).

Usage:
    .venv/bin/python scripts/cloud_screenshots.py OUT_DIR [--port 3000]

Writes PNGs to OUT_DIR/<desktop-dark|desktop-light|mobile>/<page>.png and
the server log to OUT_DIR/reflex.log. Never touches store_ops.db or
.devdata. Exits non-zero if any page failed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"
REFLEX = ROOT / ".venv" / "bin" / "reflex"
# Preinstalled browser on claude.ai cloud VMs; it may be an older build than
# the pinned playwright expects, so launch it by path instead of downloading.
CHROMIUM = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")

ADMIN = ("admin@ducklab.com", "AdminPass1234")
DEV_SEEDS = ("seed_ui_sales", "seed_ui_expenses_dev", "seed_ui_actions_dev")
# page path -> store to show (the seeds put each module's data on one store)
PAGES = {
    "/ops": "A01",
    "/ops/report": "A01",
    "/ops/sales": "S01",
    "/ops/stock": "A01",
    "/ops/expenses": "X01",
    "/ops/manpower": "A01",
    "/ops/daily": "A01",
    "/ops/actions": "A01",
    "/ops/stores": None,
}
# dialog name -> (page, store, opener selector, selector inside the dialog)
DIALOGS = {
    "daily": ("/ops/daily", "A01", "#daily-table tbody button", "#f-sales"),
    "expense": ("/ops/expenses", "X01", "#expense-register tbody button",
                "#e-amount"),
    "action": ("/ops/actions", "A01", ".act-edit", "#a-issue"),
    "manpower": ("/ops/manpower", "A01", "#open-config", "#c-opening"),
    "store": ("/ops/stores", None, "#store-new", "#sf-code"),
}
# phone table close-ups: name -> (page, store, table container selector)
TABLES = {
    "daily": ("/ops/daily", "A01", "#daily-table"),
    "sales": ("/ops/sales", "S01", "#sales-daily-section"),
    "stock-categories": ("/ops/stock", "A01", "#category-section"),
    "stock": ("/ops/stock", "A01", "#daily-section"),
    "expenses-budget": ("/ops/expenses", "X01", "#budget-monitor"),
    "expenses": ("/ops/expenses", "X01", "#expense-register"),
    "actions": ("/ops/actions", "A01", "#actions-table"),
    "manpower": ("/ops/manpower", "A01", "#staffing-table"),
    "report-expenses": ("/ops/report", "A01", "#expenses-section"),
    "report-stock": ("/ops/report", "A01", "#stock-section"),
}
MODES = {
    "desktop-dark": ({"width": 1440, "height": 900}, "dark"),
    "desktop-light": ({"width": 1440, "height": 900}, "light"),
    "mobile": ({"width": 390, "height": 844}, "dark"),
}

# Runs one --dev seed with its hard-coded DEV_DB replaced by argv[2].
_DEV_SEED_RUNNER = """
import importlib.util, sys
from pathlib import Path
name, db = sys.argv[1], Path(sys.argv[2])
spec = importlib.util.spec_from_file_location(name, f"scripts/{name}.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.DEV_DB = db
sys.argv = [name, "--dev"]
sys.exit(mod.main())
"""


def seed(db: Path) -> None:
    env = {**os.environ, "DUCKLAB_UI_DB": str(db)}
    subprocess.run([PY, "scripts/seed_ui_ops.py"], cwd=ROOT, env=env,
                   check=True)
    for name in DEV_SEEDS:
        subprocess.run([PY, "-c", _DEV_SEED_RUNNER, name, str(db)],
                       cwd=ROOT, check=True)


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_server(db: Path, port: int, log_path: Path,
                 timeout: int) -> subprocess.Popen:
    env = {**os.environ, "DUCKLAB_DB_PATH": str(db),
           "DUCKLAB_RELEASE_ID": "cloud"}
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [REFLEX, "run", "--env", "prod", "--single-port",
         "--backend-host", "127.0.0.1", "--backend-port", str(port)],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"reflex exited ({proc.returncode}); "
                               f"see {log_path}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/",
                                        timeout=5) as r:
                if r.status == 200:
                    print(f"server up in {time.time() - t0:.0f}s "
                          f"(build + start)")
                    return proc
        except OSError:
            pass
        time.sleep(2)
    stop_server(proc)
    raise RuntimeError(f"server not up after {timeout}s; see {log_path}")


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    for sig, wait in ((signal.SIGTERM, 20), (signal.SIGKILL, 10)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(wait)
            return
        except subprocess.TimeoutExpired:
            continue


def choose_store(page, code: str) -> None:
    """Pick a store in the shell selector (retry once: Radix select can
    miss a click while the page is still hydrating)."""
    if code in page.locator("#store-selector").inner_text():
        return
    option = f"[role='option']:has-text(\"{code} —\")"
    for _ in range(2):
        page.click("#store-selector")
        page.wait_for_selector(option, timeout=10000)
        page.click(option)
        try:
            page.wait_for_function(
                "c => document.querySelector('#store-selector')"
                ".innerText.includes(c)", arg=code, timeout=10000)
            break
        except Exception:
            continue
    page.wait_for_load_state("networkidle")


def open_page(page, base: str, path: str, store) -> None:
    page.goto(base + path)
    page.wait_for_selector("#store-selector", timeout=30000)
    page.wait_for_load_state("networkidle")
    if store:
        choose_store(page, store)
    page.wait_for_timeout(1500)  # charts / transitions


def shoot_dialogs(page, base: str, out: Path, mode: str,
                  failures: list[str]) -> None:
    for name, (path, store, opener, inside) in DIALOGS.items():
        try:
            open_page(page, base, path, store)
            btn = page.locator(opener).first
            btn.scroll_into_view_if_needed()
            btn.click()
            page.wait_for_selector(inside, state="visible", timeout=15000)
            page.wait_for_timeout(800)  # open animation
            page.screenshot(path=str(out / mode / f"dialog-{name}.png"))
            page.keyboard.press("Escape")
            print(f"ok    {mode:13} dialog {name}")
        except Exception as exc:
            failures.append(f"{mode} dialog {name}: {exc}")
            print(f"FAIL  {mode:13} dialog {name}: {exc}")


def shoot_tables(page, base: str, out: Path, mode: str,
                 failures: list[str]) -> None:
    for name, (path, store, sel) in TABLES.items():
        try:
            open_page(page, base, path, store)
            el = page.locator(sel).first
            el.scroll_into_view_if_needed()
            el.screenshot(path=str(out / mode / f"table-{name}.png"))
            print(f"ok    {mode:13} table {name}")
        except Exception as exc:
            failures.append(f"{mode} table {name}: {exc}")
            print(f"FAIL  {mode:13} table {name}: {exc}")


def screenshots(base: str, out: Path) -> list[str]:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []
    with sync_playwright() as p:
        launch = {}
        if Path(CHROMIUM).exists():
            launch["executable_path"] = CHROMIUM
        browser = p.chromium.launch(**launch)
        for mode, (viewport, theme) in MODES.items():
            (out / mode).mkdir(parents=True, exist_ok=True)
            ctx = browser.new_context(viewport=viewport)
            page = ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text)
                    if m.type == "error" else None)
            page.goto(f"{base}/login")
            page.wait_for_selector("#login-email")
            page.fill("#login-email", ADMIN[0])
            page.fill("#login-password", ADMIN[1])
            page.click("#login-submit")
            page.wait_for_url("**/ops**", timeout=20000)
            page.wait_for_selector("#theme-toggle")
            current = page.evaluate("document.documentElement.className")
            if theme not in current:
                page.click("#theme-toggle")  # persists for the context
                page.wait_for_timeout(800)
            for path, store in PAGES.items():
                name = path.strip("/").replace("/", "-")
                try:
                    open_page(page, base, path, store)
                    got = page.evaluate("document.documentElement.className")
                    if theme not in got:
                        raise RuntimeError(f"theme is {got!r}, not {theme}")
                    page.screenshot(path=str(out / mode / f"{name}.png"),
                                    full_page=True)
                    print(f"ok    {mode:13} {path}")
                except Exception as exc:
                    failures.append(f"{mode} {path}: {exc}")
                    print(f"FAIL  {mode:13} {path}: {exc}")
            shoot_dialogs(page, base, out, mode, failures)
            if mode == "mobile":
                shoot_tables(page, base, out, mode, failures)
            if errors:
                print(f"      {mode}: {len(errors)} console error(s), "
                      f"first: {errors[0][:200]}")
            ctx.close()
        browser.close()
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path, help="folder for the screenshots")
    ap.add_argument("--port", type=int, default=3000)
    ap.add_argument("--build-timeout", type=int, default=900,
                    help="seconds to wait for the build + server start")
    ap.add_argument("--keep-db", action="store_true",
                    help="keep the temp DB dir (path is printed)")
    args = ap.parse_args()

    if port_in_use(args.port):
        print(f"port {args.port} is already in use", file=sys.stderr)
        return 2
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ducklab-shots-"))
    db = tmp / "cloud.db"
    proc = None
    try:
        seed(db)
        print(f"seeded {db}")
        proc = start_server(db, args.port, out / "reflex.log",
                            args.build_timeout)
        failures = screenshots(f"http://127.0.0.1:{args.port}", out)
    finally:
        if proc is not None:
            stop_server(proc)
            print("server stopped")
        if args.keep_db:
            print(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"screenshots in {out}")
    for f in failures:
        print(f"FAIL  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
