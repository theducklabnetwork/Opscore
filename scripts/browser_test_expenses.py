#!/usr/bin/env python3
"""Browser verification for Expenses Management (Phase 2, Task 4).

Requires the app running with the disposable DB from seed_ui_expenses.py:
  admin@ducklab.com / AdminPass1234     (super admin)
  manager@ducklab.com / ManagerPass123  (branch manager, store A01)
  viewer@ducklab.com / ViewerPass123    (viewer, store A01)
Screenshots go to docs/screenshots/phase2-task4/. Exits non-zero on failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://localhost:3000")
OUT = Path(__file__).parent.parent / "docs" / "screenshots" / "phase2-task4"
OUT.mkdir(parents=True, exist_ok=True)

failures: list[str] = []
console_errors: list[str] = []
network_failures: list[str] = []


def check(name: str, condition: bool) -> None:
    print(("PASS  " if condition else "FAIL  ") + name)
    if not condition:
        failures.append(name)


def login(page, email, password) -> None:
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-email")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")
    page.wait_for_url("**/ops**", timeout=15000)


def choose(page, trigger, option_text, wait_selector, timeout=25000):
    for _ in range(3):
        try:
            page.click(trigger)
            page.wait_for_selector(
                f"[role='option']:has-text(\"{option_text}\")",
                state="visible", timeout=10000)
            page.click(f"[role='option']:has-text(\"{option_text}\")")
            try:
                page.wait_for_selector(wait_selector, timeout=timeout)
                return
            except Exception:
                if option_text in page.locator(trigger).inner_text():
                    return
        except Exception:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            continue
    page.wait_for_selector(wait_selector, timeout=timeout)


def open_expenses(page) -> None:
    page.goto(f"{BASE}/ops/expenses")
    page.wait_for_selector("#expense-register", timeout=20000)
    # the section shells render before the chained data load; wait for
    # register rows and a money KPI so callers assert on loaded data
    page.wait_for_selector("#expense-register tbody tr", timeout=20000)
    page.wait_for_selector("#expense-kpis >> text=RM", timeout=20000)


def select_option(page, select_id, option_text):
    """Choose an option in a drawer/filter select."""
    for _ in range(3):
        try:
            page.click(select_id)
            page.wait_for_selector(
                f"[role='option']:has-text(\"{option_text}\")",
                state="visible", timeout=8000)
            page.click(f"[role='option']:has-text(\"{option_text}\")")
            return
        except Exception:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
    raise RuntimeError(f"could not select {option_text} in {select_id}")


def open_drawer(page, expense_index=None):
    """Open Add Expense or the nth Edit button (retry on event race)."""
    for _ in range(2):
        if expense_index is None:
            page.click("#add-expense")
        else:
            page.click(
                f"#expense-register >> text=Edit >> nth={expense_index}")
        try:
            page.wait_for_selector("#e-description", state="visible",
                                   timeout=15000)
            page.wait_for_timeout(500)
            return
        except Exception:
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
    page.wait_for_selector("#e-description", state="visible", timeout=20000)
    page.wait_for_timeout(500)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: network_failures.append(r.url))

        # --- route protection -------------------------------------------
        page.goto(f"{BASE}/ops/expenses")
        page.wait_for_url("**/login**", timeout=10000)
        check("unauthenticated /ops/expenses redirects to login", True)

        # --- manager login + overview -----------------------------------
        login(page, "manager@ducklab.com", "ManagerPass123")
        open_expenses(page)
        check("expense page renders register", True)
        check("KPI cards render", page.locator("#expense-kpis").count() == 1)
        check("paid KPI shows RM 3,700.00 (3000+600+100)",
              page.locator("#expense-kpis >> text=RM 3,700.00").count() > 0)
        check("pending KPI shows RM 185.00",
              page.locator("#expense-kpis >> text=RM 185.00").count() > 0)
        check("budget monitor renders",
              page.locator("#budget-monitor").count() == 1)
        check("Watch indicator present (electricity 85.7%)",
              page.locator("#budget-monitor >> text=Watch").count() > 0)
        check("OVER BUDGET indicator (water 120%)",
              page.locator("#budget-monitor >> text=OVER BUDGET").count() > 0)
        check("transaction register shows seeded rows",
              page.locator("#expense-register >> text=Utility").count() > 0)
        page.screenshot(path=str(OUT / "expenses_overview_dark.png"),
                        full_page=True)

        # --- add expense ------------------------------------------------
        open_drawer(page)
        page.fill("#e-incurred", "2026-10-08")
        select_option(page, "#e-category", "Consumables")
        page.fill("#e-description", "Pest control service")
        page.fill("#e-amount", "240.50")
        select_option(page, "#e-status", "Pending")
        page.fill("#e-receipt", "RCP-2010")
        page.fill("#e-remarks", "Quarterly service")
        page.screenshot(path=str(OUT / "expense_add_form.png"))
        page.click("#exp-save")
        page.wait_for_selector("#exp-notice", timeout=20000)
        check("add expense success notice", True)
        page.wait_for_selector("#expense-register >> text=Pest control",
                               timeout=15000)
        check("new expense visible in register", True)
        check("pending KPI updates to RM 425.50",
              page.locator("#expense-kpis >> text=RM 425.50").count() > 0)

        # --- edit: pending -> paid --------------------------------------
        open_drawer(page, expense_index=0)
        check("edit form loads saved description",
              "Pest control" in page.input_value("#e-description"))
        select_option(page, "#e-status", "Paid")
        page.fill("#e-paid", "2026-10-10")
        select_option(page, "#e-paid-by", "Petty Cash")
        select_option(page, "#e-verification", "Verified")
        page.screenshot(path=str(OUT / "expense_edit_paid.png"))
        page.click("#exp-save")
        page.wait_for_selector("#exp-notice", timeout=20000)
        check("edit expense success notice", True)
        page.wait_for_selector("#expense-kpis >> text=RM 3,940.50",
                               timeout=15000)
        check("paid KPI recalculates (+240.50)", True)
        check("verification badge shows Verified",
              page.locator("#expense-register >> text=Verified").count() > 0)

        # --- cancel an expense ------------------------------------------
        open_drawer(page, expense_index=0)
        select_option(page, "#e-status", "Cancelled")
        page.click("#exp-save")
        page.wait_for_selector("#exp-notice", timeout=20000)
        page.wait_for_timeout(1500)
        check("cancelled expense still in register",
              page.locator("#expense-register >> text=Pest control").count() > 0)
        check("paid KPI drops back to RM 3,700.00",
              page.locator("#expense-kpis >> text=RM 3,700.00").count() > 0)

        # --- incomplete draft -------------------------------------------
        open_drawer(page)
        page.fill("#e-description", "Draft — incomplete entry")
        page.click("#exp-save")
        page.wait_for_selector("#exp-notice", timeout=20000)
        check("incomplete draft saved", True)
        page.wait_for_timeout(1500)
        check("draft flagged Incomplete in Data Check",
              page.locator("#expense-register >> text=Incomplete").count() > 0)
        page.screenshot(path=str(OUT / "expense_incomplete.png"))

        # --- invalid amount ---------------------------------------------
        open_drawer(page)
        page.fill("#e-amount", "12.345")
        page.click("#exp-save")
        page.wait_for_selector("text=sub-cent", timeout=10000)
        check("sub-cent amount rejected inline", True)
        page.fill("#e-amount", "abc")
        page.click("#exp-save")
        page.wait_for_selector("text=Invalid amount", timeout=10000)
        check("malformed amount rejected inline", True)
        page.click("#exp-close")
        page.screenshot(path=str(OUT / "expense_validation_error.png"))

        # --- filters -----------------------------------------------------
        select_option(page, "#filter-status", "Paid")
        for _ in range(4):
            page.wait_for_timeout(1000)
            if page.locator("#filter-status").inner_text() == "Paid":
                break
            select_option(page, "#filter-status", "Paid")
        rows = []
        for _ in range(5):
            page.wait_for_timeout(1000)
            rows = page.locator(
                "#expense-register tbody tr").all_inner_texts()
            if rows and all("\tPaid\t" in r for r in rows):
                break
        check("status filter: only Paid rows",
              all("\tPaid\t" in r for r in rows)
              and not any("\tPending\t" in r or "\tCancelled\t" in r
                          for r in rows) and len(rows) > 0)
        select_option(page, "#filter-status", "All Statuses")
        page.wait_for_timeout(800)
        select_option(page, "#filter-period", "Pending")
        page.wait_for_timeout(1200)
        body = page.locator("#expense-register").inner_text()
        check("outstanding view shows only pending",
              "Cleaning supplies" in body and "Utility" not in body)
        select_option(page, "#filter-period", "Incurred This Month")
        page.wait_for_timeout(1200)
        page.fill("#filter-search", "Utility")
        page.wait_for_timeout(800)
        body = page.locator("#expense-register").inner_text()
        check("search narrows to Utility row",
              "Utility" in body and "Water utility" not in body)
        page.fill("#filter-search", "")
        page.wait_for_timeout(800)

        # --- cross-month accounting --------------------------------------
        choose(page, "#month-selector", "September 2026",
               "#expense-kpis >> text=RM 100.00", timeout=8000)
        page.wait_for_timeout(1500)
        check("September incurred includes cross-month RM 100",
              page.locator("#expense-kpis >> text=RM 100.00").count() > 0)
        choose(page, "#month-selector", "October 2026",
               "#expense-kpis >> text=RM 3,700.00", timeout=8000)
        page.wait_for_timeout(1500)
        check("October paid includes cross-month RM 100",
              page.locator("#expense-kpis >> text=RM 3,700.00").count() > 0)
        page.screenshot(path=str(OUT / "expenses_overview_light.png"))
        # light theme
        page.click("#theme-toggle")
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "expenses_overview_light.png"),
                        full_page=True)
        page.click("#theme-toggle")

        # --- store isolation ---------------------------------------------
        page.click("#store-selector")
        page.wait_for_selector("[role='option']", state="visible")
        mgr_opts = page.locator("[role='option']").all_inner_texts()
        check("branch manager sees only assigned store",
              mgr_opts == ["A01 — Alpha Pavilion"])
        page.keyboard.press("Escape")
        ap = browser.new_page(viewport={"width": 1440, "height": 900})
        login(ap, "admin@ducklab.com", "AdminPass1234")
        ap.goto(f"{BASE}/ops/expenses")
        ap.wait_for_selector("#expense-register", timeout=20000)
        ap.wait_for_selector("#expense-kpis >> text=RM 3,700.00",
                             timeout=15000)
        choose(ap, "#store-selector", "B02 — Beta Galleria",
               "#store-selector:has-text(\"B02\")", timeout=8000)
        ap.wait_for_timeout(2000)
        check("store switch clears A01 transactions",
              ap.locator("#expense-register >> text=Utility").count() == 0)
        check("B02 paid KPI shows RM 500.00",
              ap.locator("#expense-kpis >> text=RM 500.00").count() > 0)
        check("B02 budget shows RM 1,500.00",
              ap.locator("#budget-monitor >> text=RM 1,500.00").count() > 0)

        # --- budget editing (admin) ---------------------------------------
        choose(ap, "#store-selector", "A01 — Alpha Pavilion",
               "#expense-kpis >> text=RM 3,700.00", timeout=8000)
        ap.wait_for_timeout(1500)
        select_option(ap, "#b-category", "Consumables")
        ap.fill("#b-amount", "0.5")  # 0.5% x RM 200,000 floor = RM 1,000
        ap.click("#b-save")
        ap.wait_for_selector("#b-notice", timeout=20000)
        check("budget save notice", True)
        ap.wait_for_selector("#budget-monitor >> text=RM 1,000.00",
                             timeout=15000)
        check("budget row shows new RM 1,000 budget", True)

        # --- viewer read-only ----------------------------------------------
        vp = browser.new_page(viewport={"width": 1440, "height": 900})
        login(vp, "viewer@ducklab.com", "ViewerPass123")
        vp.goto(f"{BASE}/ops/expenses")
        vp.wait_for_selector("#expense-register", timeout=20000)
        vp.wait_for_selector("#expense-register >> text=Utility", timeout=20000)
        check("viewer sees register",
              vp.locator("#expense-register >> text=Utility").count() > 0)
        check("viewer has no Add Expense button",
              vp.locator("#add-expense").count() == 0)
        check("viewer has no budget editor",
              vp.locator("#b-save").count() == 0)
        for _ in range(2):
            vp.click("#expense-register button:has-text('View') >> nth=0")
            try:
                vp.wait_for_selector("#e-description", state="visible",
                                     timeout=15000)
                break
            except Exception:
                vp.keyboard.press("Escape")
                vp.wait_for_timeout(800)
        vp.wait_for_selector("#e-description", state="visible",
                             timeout=20000)
        check("viewer form inputs disabled",
              vp.locator("#e-description").is_disabled())
        check("viewer has no Save button", vp.locator("#exp-save").count() == 0)
        vp.close()

        # --- responsive ------------------------------------------------------
        tablet = browser.new_page(viewport={"width": 768, "height": 1024})
        login(tablet, "manager@ducklab.com", "ManagerPass123")
        tablet.goto(f"{BASE}/ops/expenses")
        tablet.wait_for_selector("#expense-register", timeout=20000)
        overflow = tablet.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth")
        check("tablet no page-level horizontal overflow", not overflow)
        tablet.screenshot(path=str(OUT / "expenses_tablet.png"),
                          full_page=True)
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        login(mobile, "manager@ducklab.com", "ManagerPass123")
        mobile.goto(f"{BASE}/ops/expenses")
        mobile.wait_for_selector("#expense-register", timeout=20000)
        overflow = mobile.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth")
        check("mobile no page-level horizontal overflow", not overflow)
        mobile.screenshot(path=str(OUT / "expenses_mobile.png"),
                          full_page=True)
        for _ in range(3):
            mobile.click("#add-expense")
            try:
                mobile.wait_for_selector("#e-description", state="visible",
                                         timeout=15000)
                break
            except Exception:
                mobile.keyboard.press("Escape")
                mobile.wait_for_timeout(800)
        mobile.wait_for_selector("#e-description", state="visible",
                                 timeout=20000)
        check("mobile expense drawer opens", True)
        overflow = mobile.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth")
        check("mobile form no overflow", not overflow)
        mobile.screenshot(path=str(OUT / "expenses_mobile_form.png"),
                          full_page=True)

        # --- logout -----------------------------------------------------------
        page.goto(f"{BASE}/ops/expenses")
        page.wait_for_selector("#expense-register", timeout=20000)
        page.click("#account-menu")
        page.wait_for_selector("#menu-sign-out")
        page.click("#menu-sign-out")
        page.wait_for_url("**/login**", timeout=10000)
        page.goto(f"{BASE}/ops/expenses")
        page.wait_for_url("**/login**", timeout=10000)
        check("logout revokes expenses page access", True)

        browser.close()

    unexpected = [e for e in console_errors if "favicon" not in e.lower()]
    check("no unexpected console errors", not unexpected)
    for e in unexpected[:5]:
        print("   console:", e[:200])
    failed_http = [u for u in network_failures if "favicon" not in u]
    check("no failed network requests", not failed_http)
    for u in failed_http[:5]:
        print("   net:", u[:200])

    print()
    if failures:
        print(f"{len(failures)} FAILURES")
        return 1
    print("All browser checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
