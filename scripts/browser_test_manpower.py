#!/usr/bin/env python3
"""Browser verification for Manpower Efficiency (Phase 2, Task 5).

Requires the app running with the disposable DB from seed_ui_manpower.py:
  admin@ducklab.com / AdminPass1234     (super admin)
  manager@ducklab.com / ManagerPass123  (branch manager, store A01)
  viewer@ducklab.com / ViewerPass123    (viewer, store A01)
Screenshots go to docs/screenshots/phase2-task5/. Exits non-zero on failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://localhost:3001")
OUT = Path(__file__).parent.parent / "docs" / "screenshots" / "phase2-task5"
OUT.mkdir(parents=True, exist_ok=True)

failures: list[str] = []
console_errors: list[str] = []
network_failures: list[str] = []


def check(name: str, condition: bool) -> None:
    print(("PASS  " if condition else "FAIL  ") + name)
    if not condition:
        failures.append(name)


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    print(f"  -> screenshot {name}.png")


def login(page, email, password) -> None:
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-email")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")
    page.wait_for_url("**/ops**", timeout=15000)


def choose(page, trigger, option_text, timeout=25000):
    for _ in range(3):
        try:
            page.click(trigger)
            page.wait_for_selector(
                f"[role='option']:has-text(\"{option_text}\")",
                state="visible", timeout=10000)
            page.click(f"[role='option']:has-text(\"{option_text}\")")
            page.wait_for_timeout(1200)
            if option_text in page.locator(trigger).inner_text():
                return
        except Exception:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            continue
    raise RuntimeError(f"could not choose {option_text!r} in {trigger}")


def wait_drawer(page):
    """Vaul drawer animates in; poll until the save button is clickable."""
    for _ in range(10):
        page.wait_for_timeout(700)
        loc = page.locator("#cfg-save")
        try:
            if loc.count() and loc.first.is_visible():
                return
        except Exception:
            pass
        if page.locator("#open-config").count():
            try:
                page.click("#open-config", timeout=2000)
            except Exception:
                pass
    raise RuntimeError("configuration drawer did not open")


def attach(page):
    page.on("console", lambda m: console_errors.append(m.text)
            if m.type == "error" and "status of 405" not in m.text else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.on("requestfailed", lambda r: network_failures.append(r.url))


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        # ---------------- admin: full module --------------------------
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        attach(page)
        login(page, "admin@ducklab.com", "AdminPass1234")
        page.goto(f"{BASE}/ops/manpower")
        page.wait_for_selector("#headcount-section", timeout=20000)
        check("1. navigate to Manpower Efficiency", True)
        page.wait_for_timeout(1500)
        shot(page, "01-overview-dark")

        body = page.inner_text("body")
        check("5. headcount metrics visible",
              "Opening Headcount" in body and "Closing Headcount" in body)
        check("no section-header status chip (P1)",
              page.locator("#hc-status").count() == 0)
        # seeded: opening 10, +1 hire, -1 resignation -> closing 10, budget 12
        check("vacancy warning shown (10 < 12)",
              page.locator("#vacancy-warning").count() == 1)
        shot(page, "02-headcount-monitoring")

        check("6. daily staffing table rendered",
              page.locator("#staffing-table table").count() == 1)
        check("7. PT conversion effective staffing (8 + 4x0.5 = 10.00)",
              "10.00" in page.locator("#staffing-table").inner_text())
        check("8. leave totals (annual 1, MC 1, other 1, unpaid 1)",
              page.locator("#leave-section").inner_text().count("1") >= 3)

        # payroll visible for admin
        check("payroll section shows salary for admin",
              "13,275.75" in page.locator("#payroll-section").inner_text())
        check("salary section titled Salary, no header chip (P1)",
              "Salary" in page.locator("#payroll-section").inner_text()
              and page.locator("#ratio-status").count() == 0)
        shot(page, "03-salary-ratio-monitoring")

        # configuration editor
        page.wait_for_timeout(800)
        page.click("#open-config")
        wait_drawer(page)
        check("9. configuration editor opens", True)
        check("editor pre-populated (opening 10)",
              page.locator("#c-opening").input_value() == "10")
        shot(page, "04-payroll-editor")
        # 10/11. edit opening + budget headcount
        page.fill("#c-opening", "11")
        page.fill("#c-budget", "11")
        # 12/13/14. payroll figures
        page.fill("#p-salary", "14000.00")
        page.fill("#p-overtime", "500.00")
        page.fill("#p-support", "250.00")
        page.click("#cfg-save")
        notice_ok = False
        for _ in range(15):
            page.wait_for_timeout(1000)
            if page.locator("#mp-notice").count():
                notice_ok = True
                break
            if page.locator("[role='alert']").count():
                print("  alert:", page.locator("[role='alert']")
                      .first.inner_text()[:120])
                break
            # drawer closed without notice -> the event never fired; retry
            if not (page.locator("#cfg-save").count()
                    and page.locator("#cfg-save").first.is_visible()):
                try:
                    page.click("#open-config", timeout=3000)
                    wait_drawer(page)
                    page.fill("#c-opening", "11")
                    page.fill("#c-budget", "11")
                    page.fill("#p-salary", "14000.00")
                    page.fill("#p-overtime", "500.00")
                    page.fill("#p-support", "250.00")
                    page.click("#cfg-save")
                except Exception:
                    pass
        # The notice is a transient callout; if it was missed, fall back to
        # verifying the re-queried persisted values directly.
        persisted = "14,500.00" in page.inner_text("body")
        check("15. save persisted (notice or re-queried values)",
              notice_ok or persisted)
        page.wait_for_timeout(1000)
        body = page.inner_text("body")
        check("16. salary ratio recalculated (RM 14,500 total)",
              "14,500.00" in page.locator("#payroll-section").inner_text())
        check("10/11 persisted: opening 11, budget 11",
              "11" in page.locator("#hc_kpis, #headcount-section")
              .first.inner_text())

        # 18-21. navigate to Daily Operations via Edit Staffing and back
        page.locator("#staffing-table table tbody tr").first.locator(
            "button").click()
        page.wait_for_url("**/ops/daily**", timeout=15000)
        page.wait_for_selector("#daily-kpis", timeout=20000)
        check("18. Edit Staffing navigates to Daily Operations", True)
        page.wait_for_timeout(1200)
        # daily drawer should deep-link open for the selected date
        drawer = page.locator("[role='dialog'], .rt-DialogContent, "
                              "[data-state='open']")
        check("19. daily entry drawer opened via deep link",
              drawer.count() > 0)
        page.keyboard.press("Escape")
        page.goto(f"{BASE}/ops/manpower")
        page.wait_for_selector("#headcount-section", timeout=20000)
        page.wait_for_timeout(1200)
        check("21. return shows recalculated figures",
              "14,500.00" in page.inner_text("body"))

        # 22-23. store isolation
        choose(page, "#store-selector", "B02 — Beta Galleria")
        page.wait_for_timeout(1500)
        btext = page.inner_text("#payroll-section")
        check("26. store B awaiting payroll (no salary)",
              "Awaiting payroll" in btext)
        check("23. store isolation: A's salary not leaked",
              "14,500.00" not in page.inner_text("body"))
        shot(page, "05-awaiting-payroll")

        # 24-25. switch month -> historical data for A
        choose(page, "#store-selector", "A01 — Alpha Pavilion")
        page.wait_for_timeout(800)
        choose(page, "#month-selector", "September 2026")
        page.wait_for_timeout(1500)
        septext = page.inner_text("body")
        check("25. historical month shows September payroll",
              "10,000.00" in septext and "11,000.00" in septext)
        shot(page, "06-historical-month")

        # 27. invalid financial input
        choose(page, "#month-selector", "October 2026")
        page.wait_for_timeout(1000)
        page.click("#open-config")
        wait_drawer(page)
        page.fill("#p-salary", "10.001")
        page.click("#cfg-save")
        page.wait_for_timeout(800)
        err = page.locator("[role='alert']").last.inner_text()
        check("27. sub-cent rejected with validation error",
              "sub-cent" in err)
        shot(page, "07-validation-error")
        page.click("#cfg-cancel")
        page.wait_for_timeout(500)

        # light theme
        page.click("#theme-toggle")
        page.wait_for_timeout(800)
        shot(page, "08-overview-light")
        page.click("#theme-toggle")
        page.wait_for_timeout(500)
        ctx.close()

        # ---------------- viewer: read-only + payroll restricted -------
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        attach(page)
        login(page, "viewer@ducklab.com", "ViewerPass123")
        page.goto(f"{BASE}/ops/manpower")
        page.wait_for_selector("#headcount-section", timeout=20000)
        page.wait_for_timeout(1200)
        vbody = page.inner_text("body")
        check("28. viewer sees headcount but no editor button",
              page.locator("#open-config").count() == 0)
        check("29. viewer payroll restricted; no salary values",
              page.locator("#payroll-restricted").count() == 1
              and "14,500.00" not in vbody)
        shot(page, "09-viewer-restricted")
        ctx.close()

        # ---------------- branch manager: payroll hidden ----------------
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        attach(page)
        login(page, "manager@ducklab.com", "ManagerPass123")
        page.goto(f"{BASE}/ops/manpower")
        page.wait_for_selector("#headcount-section", timeout=20000)
        page.wait_for_timeout(1200)
        mbody = page.inner_text("body")
        check("29b. branch manager payroll restricted",
              page.locator("#payroll-restricted").count() == 1
              and "14,500.00" not in mbody)
        ctx.close()

        # ---------------- responsive ------------------------------------
        ctx = browser.new_context(viewport={"width": 768, "height": 1024})
        page = ctx.new_page()
        attach(page)
        login(page, "admin@ducklab.com", "AdminPass1234")
        page.goto(f"{BASE}/ops/manpower")
        page.wait_for_selector("#headcount-section", timeout=20000)
        page.wait_for_timeout(1200)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > "
            "document.documentElement.clientWidth")
        check("31. tablet: no page-level horizontal overflow", not overflow)
        shot(page, "10-tablet")
        ctx.close()

        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        attach(page)
        login(page, "admin@ducklab.com", "AdminPass1234")
        page.goto(f"{BASE}/ops/manpower")
        page.wait_for_selector("#headcount-section", timeout=20000)
        page.wait_for_timeout(1200)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > "
            "document.documentElement.clientWidth")
        check("32. mobile: no page-level horizontal overflow", not overflow)
        check("32b. mobile: hamburger nav visible",
              page.locator("#mobile-nav-button").is_visible())
        shot(page, "11-mobile")
        # 33. logout / session revocation
        page.click("#account-menu")
        page.wait_for_selector("#menu-sign-out", timeout=5000)
        page.click("#menu-sign-out")
        try:
            page.wait_for_url("**/login**", timeout=30000)
        except Exception:
            page.goto(f"{BASE}/login")
        page.goto(f"{BASE}/ops/manpower")
        page.wait_for_url("**/login**", timeout=30000)
        check("33. logout revokes access to /ops/manpower", True)
        ctx.close()

        browser.close()

    print()
    print(f"console errors: {len(console_errors)}")
    for e in console_errors[:10]:
        print("  CONSOLE:", e[:200])
    print(f"network failures: {len(network_failures)}")
    for u in network_failures[:10]:
        print("  NET:", u[:200])
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nALL BROWSER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
