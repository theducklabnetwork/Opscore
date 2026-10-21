#!/usr/bin/env python3
"""Browser smoke check for Stock Management (Phase 2, Task 6).

Requires the dev instance running with the dev DB seeded via
seed_ui_stock.py --dev (Super Admin id=1; password from scripts/bootstrap_admin.py).
Screenshots go to docs/screenshots/phase2-task6/. Exits non-zero on failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:3000")
OUT = Path(__file__).parent.parent / "docs" / "screenshots" / "phase2-task6"
OUT.mkdir(parents=True, exist_ok=True)

failures: list[str] = []
console_errors: list[str] = []


def check(name: str, condition: bool) -> None:
    print(("PASS  " if condition else "FAIL  ") + name)
    if not condition:
        failures.append(name)


def _login_user(email: str, password: str):
    return (email, password)


def _bootstrap_temp_admin():
    """Create a smoke-test admin directly in the dev DB (dev data only)."""
    import secrets
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from ducklab.app_service import AppService
        app = AppService("/home/duck/project/ducklab/.devdata/dev.db")
        email = f"smoke-{secrets.token_hex(3)}@ducklab.dev"
        password = "Smoke!" + secrets.token_hex(6)
        admin1 = app.auth.get_user(1)
        u = app.create_user(admin1, email, "Smoke Tester", password)
        app.assign_role(admin1, u.id, "super_admin")
        return (email, password)
    except Exception as exc:
        print(f"SKIP: cannot create smoke admin: {exc}")
        return None


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    print(f"  -> screenshot {name}.png")


def main() -> int:
    email = os.environ.get("DUCKLAB_ADMIN_EMAIL")
    password = os.environ.get("DUCKLAB_ADMIN_PASSWORD")
    if email and password:
        admin = _login_user(email, password)
    else:
        admin = _bootstrap_temp_admin()
    if admin is None:
        print("SKIP: set DUCKLAB_ADMIN_EMAIL / DUCKLAB_ADMIN_PASSWORD")
        return 2
    email, password = admin
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        page.goto(f"{BASE}/login")
        page.wait_for_selector("#login-email")
        page.fill("#login-email", email)
        page.fill("#login-password", password)
        page.click("#login-submit")
        page.wait_for_url("**/ops**", timeout=20000)

        page.goto(f"{BASE}/ops/stock")
        page.wait_for_selector("#stock-kpi-section", timeout=60000)
        check("page renders (KPI section)", True)
        check("Total Loss seeded",
              "RM 660.00" in page.locator("#stock-kpi-section").inner_text())
        check("status badge", "Within Target"
              in page.locator("#stock-status").inner_text())

        cat = page.locator("#category-section").inner_text()
        check("2DMP shows Info only", "2DMP" in cat and "Info only" in cat)
        check("Missing shown as Included", "Included" in cat)

        # category click -> investigation panel
        page.wait_for_selector("#category-section", timeout=60000)
        page.wait_for_timeout(1000)
        page.get_by_text("DMO", exact=True).first.click()
        page.wait_for_selector("#investigation-panel", timeout=15000)
        panel = page.locator("#investigation-panel").inner_text()
        check("investigation panel shows DMO total", "RM 385.00" in panel)
        check("investigation lists dates", "01 Oct 2026" in panel)

        # Edit link opens the right daily date
        href = page.locator(
            "#daily-section a:has-text('Edit')").first.get_attribute("href")
        check("edit link deep-links to daily date",
              href is not None and href.startswith("/ops/daily?date=2026-10"))
        page.goto(f"{BASE}{href}")
        page.wait_for_selector("text=Daily Operations", timeout=60000)
        check("daily entry opened for the linked date",
              "October 2026" in page.locator("body").inner_text())
        page.goto(f"{BASE}/ops/stock")
        page.wait_for_selector("#stock-kpi-section", timeout=120000)
        page.wait_for_timeout(1500)

        # store switch clears + reloads data
        page.reload()
        page.wait_for_selector("#stock-kpi-section", timeout=120000)
        page.click("#store-selector")
        page.click("[role='option']:has-text('B02')")
        page.wait_for_timeout(2500)
        kpi = page.locator("#stock-kpi-section").inner_text()
        check("store switch shows B02 total (RM 140.00)", "RM 140.00" in kpi)
        page.click("#store-selector")
        page.click("[role='option']:has-text('A01')")
        page.wait_for_timeout(2500)

        shot(page, "desktop-dark")
        page.click("#theme-toggle")
        page.wait_for_timeout(1200)
        shot(page, "desktop-light")
        page.click("#theme-toggle")
        mob = browser.new_page(viewport={"width": 390, "height": 844})
        mob.goto(f"{BASE}/login")
        mob.wait_for_selector("#login-email")
        mob.fill("#login-email", email)
        mob.fill("#login-password", password)
        mob.click("#login-submit")
        mob.wait_for_url("**/ops**", timeout=20000)
        mob.goto(f"{BASE}/ops/stock")
        mob.wait_for_selector("#stock-kpi-section", timeout=120000)
        mob.wait_for_timeout(1000)
        shot(mob, "mobile")

        check("no console errors", not console_errors)
        for e in console_errors[:5]:
            print("  console error:", e)
        browser.close()
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
