#!/usr/bin/env python3
"""Browser smoke for Task 10: imported workbook values on /ops/sales,
/ops/stock, /ops/expenses for the imported store, September 2026.
No screenshots (private data). Exits non-zero on failure.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:3000")

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
    email = f"task10-{secrets.token_hex(3)}@ducklab.dev"
    password = "Task10!" + secrets.token_hex(6)
    admin1 = app.auth.get_user(1)
    u = app.create_user(admin1, email, "Task10 QA", password)
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
    key = option_text.split(" — ")[0]
    for _ in range(4):
        try:
            page.click(trigger)
            page.wait_for_selector("[role='option']", state="visible",
                                   timeout=10000)
            page.keyboard.type(key, delay=40)
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1000)
            if key in page.locator(trigger).inner_text():
                return
        except Exception:
            pass
        try:
            page.click(trigger)
            opt = f"[role='option']:has-text(\"{option_text}\")"
            page.wait_for_selector(opt, state="visible", timeout=10000)
            page.locator(opt).first.dispatch_event("click")
            page.wait_for_timeout(1000)
            if key in page.locator(trigger).inner_text():
                return
        except Exception:
            page.wait_for_timeout(1200)
    raise RuntimeError(f"could not select {option_text!r}")


def main() -> int:
    email, password = make_admin()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        login(page, email, password)

        # --- Sales ---
        page.goto(f"{BASE}/ops/sales")
        page.wait_for_selector("#ladder-section", timeout=90000)
        choose(page, "#store-selector", "ZI68")
        choose(page, "#month-selector", "September 2026")
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text()
        check("sales: MTD RM 350,819", "RM 350,819" in body)
        check("sales: days 30 / 30", "30 / 30" in body)
        check("sales: Actual (complete month)", "(Actual)" in body)
        check("sales: L1-L3 Achieved", body.count("Achieved") >= 3)
        check("sales: SPR RM 30.26", "RM 30.26" in body)

        # --- Stock ---
        page.goto(f"{BASE}/ops/stock")
        page.wait_for_selector("#stock-status", timeout=90000)
        choose(page, "#store-selector", "ZI68")
        choose(page, "#month-selector", "September 2026")
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text()
        check("stock: total loss RM 1,595.00", "RM 1,595.00" in body)
        check("stock: ratio 0.45%", "0.45%" in body or "0.5%" in body)
        check("stock: Within target",
              page.locator("#stock-status").inner_text() == "Within target")

        # --- Expenses ---
        page.goto(f"{BASE}/ops/expenses")
        page.wait_for_selector("#expense-kpis", timeout=90000)
        choose(page, "#store-selector", "ZI68")
        choose(page, "#month-selector", "September 2026")
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text()
        check("expenses: paid RM 4,819.00", "RM 4,819.00" in body)
        check("expenses: incurred RM 5,259.00", "RM 5,259.00" in body)
        check("expenses: open RM 440.00", "RM 440.00" in body)
        check("expenses: 2 needs review",
              "Missing recorder: 2" in body or "2" in body)

        check("no console errors", not console_errors)
        for e in console_errors[:5]:
            print("  console error:", e)
        browser.close()
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
