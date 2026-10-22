#!/usr/bin/env python3
"""Browser smoke for Phase 2 Task 7: Store Management + Monthly Setup.

Runs against the dev instance with seeded dev DB. Creates a store, assigns
the admin, saves a month's setup including a stock target, then confirms
the target status appears on /ops/stock. Screenshots -> docs/screenshots/
phase2-task7/. Exits non-zero on failure.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:3000")
OUT = Path(__file__).parent.parent / "docs" / "screenshots" / "phase2-task7"
OUT.mkdir(parents=True, exist_ok=True)

failures: list[str] = []
console_errors: list[str] = []


def check(name: str, condition: bool) -> None:
    print(("PASS  " if condition else "FAIL  ") + name)
    if not condition:
        failures.append(name)


def make_admin():
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from ducklab.app_service import AppService
    app = AppService("/home/duck/project/ducklab/.devdata/dev.db")
    email = f"task7-{secrets.token_hex(3)}@ducklab.dev"
    password = "Task7!" + secrets.token_hex(6)
    admin1 = app.auth.get_user(1)
    u = app.create_user(admin1, email, "Task7 QA", password)
    app.assign_role(admin1, u.id, "super_admin")
    return email, password


def seed_day(email_admin_code):
    """Record one daily sale+loss so the stock page computes a status."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from datetime import date
    from ducklab.app_service import AppService
    app = AppService("/home/duck/project/ducklab/.devdata/dev.db")
    admin = app.auth.get_user(1)
    store = app.read_repo.get_store_by_code(email_admin_code)
    app.record_daily(admin, store.id, date(2026, 10, 2),
                     stock_losses={"DMO": 5000}, sales_cents=1000000)


def login(page, email, password):
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-email")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")
    page.wait_for_url("**/ops**", timeout=20000)


def choose(page, trigger, option_text, wait_selector=None, timeout=25000):
    for _ in range(3):
        try:
            page.click(trigger)
            page.wait_for_selector(
                f"[role='option']:has-text(\"{option_text}\")",
                state="visible", timeout=10000)
            page.click(f"[role='option']:has-text(\"{option_text}\")")
            try:
                if wait_selector:
                    page.wait_for_selector(wait_selector, timeout=timeout)
                return
            except Exception:
                if option_text in page.locator(trigger).inner_text():
                    return
        except Exception:
            page.wait_for_timeout(1200)
    raise RuntimeError(f"could not select {option_text!r}")


def main() -> int:
    email, password = make_admin()
    code = "T7" + secrets.token_hex(2).upper()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        login(page, email, password)

        # 1. create a store
        page.goto(f"{BASE}/ops/stores")
        page.wait_for_selector("#store-new", timeout=90000)
        page.wait_for_timeout(1000)
        page.click("#store-new")
        page.wait_for_selector("#sf-code", state="visible", timeout=15000)
        page.fill("#sf-code", code)
        page.fill("#sf-name", "Task Seven QA")
        page.fill("#sf-zone", "QA Zone")
        page.click("#store-form-save")
        page.wait_for_selector(f"#store-users-{code}", timeout=20000)
        check("store created and listed", True)

        # 2. assign self
        page.click(f"#store-users-{code}")
        page.wait_for_selector("#assign-select", timeout=15000)
        choose(page, "#assign-select", email, wait_selector=None)
        page.wait_for_timeout(500)
        page.click("#assign-submit")
        page.wait_for_timeout(1500)
        check("admin assigned to store",
              email in page.locator("body").inner_text())

        # 3. monthly setup for the new store (reload so the selector picks
        # up the freshly created store + assignment)
        page.goto(f"{BASE}/ops/stores")
        page.wait_for_selector("#setup-save", timeout=60000)
        choose(page, "#store-selector", f"{code} — Task Seven QA",
               wait_selector="#setup-save")
        page.wait_for_timeout(1000)
        page.fill("#setup-stock-loss-target", "1.5")
        page.fill("#setup-level1-base-cents", "200000")
        page.fill("#setup-part-timer-factor", "50")
        page.screenshot(path=str(OUT / "setup-desktop-dark.png"))
        print("  -> setup-desktop-dark.png")
        page.click("#setup-save")
        page.wait_for_timeout(2000)
        check("setup saved (notice)",
              "saved" in page.locator("body").inner_text().lower())

        # 4. stock page shows target status (one seeded day so a ratio exists)
        seed_day(code)
        page.goto(f"{BASE}/ops/stock")
        page.wait_for_selector("#stock-status", timeout=60000)
        page.wait_for_timeout(1200)
        choose(page, "#store-selector", f"{code} — Task Seven QA",
               wait_selector=None)
        page.wait_for_timeout(2500)
        status = page.locator("#stock-status").inner_text()
        check(f"stock page shows target status (got '{status}')",
              status in ("Within target", "Over target"))
        page.screenshot(path=str(OUT / "stock-target-status.png"))
        print("  -> stock-target-status.png")

        # 5. light + mobile screenshots of the stores page
        page.goto(f"{BASE}/ops/stores")
        page.wait_for_selector("#store-new", timeout=60000)
        page.wait_for_timeout(800)
        page.click("#theme-toggle")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "stores-desktop-light.png"))
        print("  -> stores-desktop-light.png")

        mob = browser.new_page(viewport={"width": 390, "height": 844})
        login(mob, email, password)
        mob.goto(f"{BASE}/ops/stores")
        mob.wait_for_selector("#store-new", timeout=60000)
        mob.wait_for_timeout(800)
        mob.screenshot(path=str(OUT / "stores-mobile.png"))
        print("  -> stores-mobile.png")

        check("no console errors", not console_errors)
        for e in console_errors[:5]:
            print("  console error:", e)
        browser.close()
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
