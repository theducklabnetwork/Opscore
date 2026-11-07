#!/usr/bin/env python3
"""Browser smoke for Phase 2 Task 8: Sales Performance (/ops/sales).

Requires the dev instance + scripts/seed_ui_sales.py --dev. Screenshots ->
docs/screenshots/phase2-task8/. Exits non-zero on failure.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:3000")
OUT = Path(__file__).parent.parent / "docs" / "screenshots" / "phase2-task8"
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
    email = f"task8-{secrets.token_hex(3)}@ducklab.dev"
    password = "Task8!" + secrets.token_hex(6)
    admin1 = app.auth.get_user(1)
    u = app.create_user(admin1, email, "Task8 QA", password)
    app.assign_role(admin1, u.id, "super_admin")
    return email, password


def login(page, email, password):
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-email")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")
    page.wait_for_url("**/ops**", timeout=20000)


def choose(page, trigger, option_text, timeout=25000):
    for _ in range(3):
        try:
            page.click(trigger)
            page.wait_for_selector(
                f"[role='option']:has-text(\"{option_text}\")",
                state="visible", timeout=10000)
            page.click(f"[role='option']:has-text(\"{option_text}\")")
            page.wait_for_timeout(1200)
            if option_text.split(" — ")[0] in page.locator(trigger).inner_text():
                return
        except Exception:
            page.wait_for_timeout(1200)
    raise RuntimeError(f"could not select {option_text!r}")


def goto_sales(page):
    page.goto(f"{BASE}/ops/sales")
    page.wait_for_selector("#ladder-section", timeout=90000)
    page.wait_for_timeout(1200)


def main() -> int:
    email, password = make_admin()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        login(page, email, password)
        goto_sales(page)
        choose(page, "#store-selector", "S01 — Sales QA DEV")
        page.wait_for_timeout(1500)

        body = page.locator("body").inner_text()
        check("projected label on partial month",
              "Projected: " in body)
        check("Sales seeded (RM 120,000.00)", "RM 120,000.00" in body)
        check("Projected Sales tile on a partial month (P1)",
              "Projected Sales" in body
              and "days reported" not in body)
        check("Level 1 status badge", "Achieved" in body
              or "Below target" in body)
        check("comparison rows", "SSSG/Year" in body
              and "SSSG/Month" in body)
        check("unreported days collapsed into one row",
              "12 days not reported" in body)
        page.click("#sales-hidden-toggle")
        page.wait_for_selector("#sales-row-2026-10-11", timeout=15000)
        check("Daily Sales columns",
              all(h in body for h in ("Contribution", "Adjusted Target",
                                      "UPT", "Result")))
        check("unreported day has no Result; zero-sales day Not Achieved",
              page.locator("#sales-row-2026-10-11 >> text=Achieved")
              .count() == 0
              and page.locator("#sales-row-2026-10-05 >> text=Not Achieved")
              .count() == 1)
        check("public holiday marked (seed: 14 Oct)",
              page.locator("#sales-row-2026-10-14 >> text=PH").count() == 1)
        check("basket section", "Basket" in body and "BV" in body)

        # edit deep link for the 1st of the month
        page.click("#sales-row-2026-10-01 >> text=Edit")
        page.wait_for_url("**/ops/daily?date=2026-10-01**", timeout=20000)
        page.wait_for_selector("#f-sales", state="visible", timeout=90000)
        check("edit link opens dialog for 2026-10-01",
              "2026-10-01" in page.url
              and page.input_value("#f-sales") == "6400.00")
        page.keyboard.press("Escape")

        # store switch clears and reloads (A01: no sales on some days)
        page.goto(f"{BASE}/ops/sales")
        page.wait_for_selector("#ladder-section", timeout=90000)
        choose(page, "#store-selector", "A01")
        page.wait_for_timeout(2500)
        body2 = page.locator("body").inner_text()
        check("store switch works (S01 MTD gone, A01 loaded)",
              "RM 120,000" not in body2 and "Level Reached" in body2)

        # screenshots
        goto_sales(page)
        choose(page, "#store-selector", "S01 — Sales QA DEV")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "sales-desktop-dark.png"))
        print("  -> sales-desktop-dark.png")
        page.click("#theme-toggle")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "sales-desktop-light.png"))
        print("  -> sales-desktop-light.png")

        mob = browser.new_page(viewport={"width": 390, "height": 844})
        login(mob, email, password)
        mob.goto(f"{BASE}/ops/sales")
        mob.wait_for_selector("#ladder-section", timeout=90000)
        mob.wait_for_timeout(1200)
        mob.screenshot(path=str(OUT / "sales-mobile.png"))
        print("  -> sales-mobile.png")

        check("no console errors", not console_errors)
        for e in console_errors[:5]:
            print("  console error:", e)
        browser.close()
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
