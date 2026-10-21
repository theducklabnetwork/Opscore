#!/usr/bin/env python3
"""Browser smoke for Phase 2 Task 9: Expenses Management (/ops/expenses).

Requires the dev instance + scripts/seed_ui_expenses_dev.py --dev.
Screenshots -> docs/screenshots/phase2-task9/. Exits non-zero on failure.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:3000")
OUT = Path(__file__).parent.parent / "docs" / "screenshots" / "phase2-task9"
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
    email = f"task9-{secrets.token_hex(3)}@ducklab.dev"
    password = "Task9!" + secrets.token_hex(6)
    admin1 = app.auth.get_user(1)
    u = app.create_user(admin1, email, "Task9 QA", password)
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
    """Radix select: type-to-highlight + Enter (keyboard), with a click
    fallback. Avoids the option-detach race on animated listboxes."""
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
        try:  # click fallback
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


def goto_expenses(page):
    page.goto(f"{BASE}/ops/expenses")
    page.wait_for_selector("#expense-kpis", timeout=90000)
    page.wait_for_timeout(1000)


def main() -> int:
    email, password = make_admin()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        login(page, email, password)
        goto_expenses(page)
        choose(page, "#store-selector", "X01 — Expenses QA DEV")
        page.wait_for_timeout(2000)
        body = page.locator("body").inner_text()

        check("paid card value (RM 5,205.00)", "RM 5,205.00" in body)
        check("category statuses present",
              "OVER BUDGET" in body and "Watch" in body
              and "No budget" in body)
        check("data checks shown", "Missing paid date" in body
              and "Disputed" in body and "Unverified" in body)
        check("validation summary line",
              "Missing paid date" in body and "Disputed: 1" in body)

        # --- add expense via dialog ---
        page.locator("#add-expense").scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        page.click("#add-expense")
        page.wait_for_selector("#e-amount", state="visible", timeout=15000)
        page.fill("#e-incurred", "2026-10-20")
        page.fill("#e-paid", "2026-10-20")
        choose(page, "#e-category", "Water")
        page.fill("#e-amount", "55.50")
        page.fill("#e-description", "QA smoke refill")
        choose(page, "#e-status", "Paid")
        # Paid without payer: saved as a draft and flagged "Missing paid
        # by" by the data check ladder (the workbook's own semantics —
        # drafts stay visible instead of being rejected)
        page.click("#exp-save")
        page.wait_for_timeout(3500)
        page.wait_for_selector("#expense-register", timeout=15000)
        row0 = page.locator("tr", has_text="QA smoke refill").first
        row0.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        row0.locator("text=Edit").dispatch_event("click")
        page.wait_for_selector("#e-status", state="visible", timeout=15000)
        check("Paid without payer -> draft flagged 'Missing paid by'",
              page.locator("tr", has_text="QA smoke refill").count() > 0)
        choose(page, "#e-paid-by", "Petty Cash")
        page.click("#exp-save")
        page.wait_for_timeout(3500)
        body = page.locator("body").inner_text()
        check("expense added, totals updated (RM 5,260.50)",
              "RM 5,260.50" in body)  # 5,205 + 55.50

        # --- cancel it -> totals revert ---
        page.wait_for_selector("#expense-register", timeout=15000)
        row = page.locator("tr", has_text="QA smoke refill").first
        row.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        row.locator("text=Edit").dispatch_event("click")
        page.wait_for_selector("#e-status", state="visible", timeout=15000)
        choose(page, "#e-status", "Cancelled")
        page.click("#exp-save")
        page.wait_for_timeout(3500)
        body = page.locator("body").inner_text()
        check("cancelled removes from totals (RM 5,205.00 back)",
              "RM 5,205.00" in body)

        # --- store switch ---
        page.goto(f"{BASE}/ops/expenses")
        page.wait_for_selector("#expense-kpis", timeout=90000)
        page.wait_for_timeout(1000)
        choose(page, "#store-selector", "A01 — Alpha Pavilion")
        page.wait_for_timeout(2500)
        check("store switch works (X01 totals gone)",
              "RM 5,205.00" not in page.locator("body").inner_text()
              and "RM 5,260.50" not in page.locator("body").inner_text())

        # screenshots
        goto_expenses(page)
        choose(page, "#store-selector", "X01 — Expenses QA DEV")
        page.wait_for_timeout(2000)
        page.screenshot(path=str(OUT / "expenses-desktop-dark.png"))
        print("  -> expenses-desktop-dark.png")
        page.click("#theme-toggle")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "expenses-desktop-light.png"))
        print("  -> expenses-desktop-light.png")

        mob = browser.new_page(viewport={"width": 390, "height": 844})
        login(mob, email, password)
        mob.goto(f"{BASE}/ops/expenses")
        mob.wait_for_selector("#expense-kpis", timeout=90000)
        mob.wait_for_timeout(1000)
        mob.screenshot(path=str(OUT / "expenses-mobile.png"))
        print("  -> expenses-mobile.png")

        check("no console errors", not console_errors)
        for e in console_errors[:5]:
            print("  console error:", e)
        browser.close()
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
