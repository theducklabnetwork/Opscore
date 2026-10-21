#!/usr/bin/env python3
"""Browser verification for Daily Operations (Phase 2, Task 3).

Requires the app running with the disposable DB seeded for this task:
  admin@ducklab.com / AdminPass1234  (super admin)
  manager@ducklab.com / ManagerPass123  (branch manager, store A01)
  viewer@ducklab.com / ViewerPass123   (viewer, store A01)
Screenshots go to docs/screenshots/phase2-task3/. Exits non-zero on failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://localhost:3000")
OUT = Path(__file__).parent.parent / "docs" / "screenshots" / "phase2-task3"
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
    """Click a select trigger, pick an option. Retries on the flaky
    Radix/Reflex event race; success = wait_selector appears OR the
    trigger itself shows the chosen value."""
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


def open_daily(page) -> None:
    page.goto(f"{BASE}/ops/daily")
    page.wait_for_selector("#daily-table", timeout=20000)
    # Records load via a chained backend event after first paint; wait for
    # any monetary cell so callers can assert on real data immediately.
    page.wait_for_selector("#daily-table >> text=RM", timeout=20000)


def open_entry(page, nth=0, host=None) -> None:
    """Open the entry drawer for the nth row; retry once if the click
    missed (rare Radix/Reflex event race)."""
    host = host or page
    for attempt in range(2):
        host.click(f"#daily-table >> text=Edit >> nth={nth}") if host is page \
            else None
        try:
            host.wait_for_selector("#f-sales", state="visible",
                                   timeout=10000)
            host.wait_for_timeout(400)  # drawer animation
            return
        except Exception:
            continue
    host.wait_for_selector("#f-sales", state="visible", timeout=15000)
    host.wait_for_timeout(400)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: network_failures.append(r.url))

        # --- route protection ------------------------------------------------
        page.goto(f"{BASE}/ops/daily")
        page.wait_for_url("**/login**", timeout=10000)
        check("unauthenticated /ops/daily redirects to login", True)

        # --- login as branch manager -----------------------------------------
        login(page, "manager@ducklab.com", "ManagerPass123")
        open_daily(page)
        check("daily page renders records table", True)
        check("monthly KPI summary renders",
              page.locator("#daily-kpis").count() == 1)
        check("existing Oct 1 record shows RM 12,000.00",
              page.locator("#daily-table >> text=RM 12,000.00").count() > 0)
        page.screenshot(path=str(OUT / "daily_overview_dark.png"),
                        full_page=True)

        # --- edit existing day (Oct 1) ----------------------------------------
        open_entry(page, nth=0)
        check("form opens with existing values",
              page.input_value("#f-sales") == "12000.00")
        check("stock DMO populated",
              page.input_value("#stock-DMO") == "45.00")
        check("effective staff preview shown",
              page.locator("text=Effective staff:").count() > 0)
        page.fill("#f-sales", "15000")
        page.fill("#f-transactions", "400")
        page.fill("#f-units", "1500")
        page.fill("#stock-MISSING", "25")
        page.screenshot(path=str(OUT / "entry_form.png"))
        page.click("#entry-save")
        page.wait_for_selector("#save-notice", timeout=20000)
        check("save shows genuine success notice", True)
        # Deterministic readiness: wait for the table to show the new value
        # (the write triggers chained reloads of table + KPI cards).
        page.wait_for_selector("#daily-table >> text=RM 15,000.00",
                               timeout=25000)
        check("table refreshes with new sales value", True)
        # KPI MTD after the edit: 15,000 (Oct 1, edited) + 11,500 (Oct 2,
        # seeded) = RM 26,500.00 — see seed_ui_daily.py data contract.
        page.wait_for_selector("#daily-kpis >> text=RM 26,500.00",
                               timeout=25000)
        check("KPI cards refresh after write (MTD 26,500)", True)

        # --- partial update: edit stock only ----------------------------------
        open_entry(page, nth=0)
        check("sales value persisted after reopen",
              page.input_value("#f-sales") == "15000.00")
        page.fill("#stock-DMO", "99.50")
        page.fill("#f-ft", "")   # blank: must NOT erase saved ft_working=8
        page.click("#entry-save")
        page.wait_for_selector("#save-notice", timeout=20000)
        open_entry(page, nth=0)
        check("blank ft field did not erase saved value",
              page.input_value("#f-ft") == "8")
        check("stock DMO updated", page.input_value("#stock-DMO") == "99.50")
        check("sales untouched by stock-only edit",
              page.input_value("#f-sales") == "15000.00")
        page.click("#entry-close")

        # --- create a new day (Oct 3) ------------------------------------------
        open_entry(page, nth=2)
        page.fill("#f-sales", "8500.50")
        page.fill("#f-transactions", "210")
        page.fill("#f-units", "640")
        page.fill("#f-ft", "7")
        page.fill("#f-leave-annual", "1")
        page.fill("#stock-DMC", "12.75")
        page.screenshot(path=str(OUT / "entry_new_day.png"))
        page.click("#entry-save")
        page.wait_for_selector("#save-notice", timeout=20000)
        page.wait_for_selector("#daily-table >> text=RM 8,500.50",
                               timeout=15000)
        check("new day created and visible", True)
        row_txt = page.locator("#daily-table tbody tr").nth(2).inner_text()
        for _ in range(3):
            if "RM 40.48" in row_txt:
                break
            page.wait_for_timeout(2000)
            row_txt = page.locator(
                "#daily-table tbody tr").nth(2).inner_text()
        check("SPR calculated (8500.50/210 = RM 40.48)",
              "RM 40.48" in row_txt)

        # --- validation: negative count -----------------------------------------
        open_entry(page, nth=2)
        page.fill("#f-ft", "-3")
        page.click("#entry-save")
        page.wait_for_selector("text=cannot be negative", timeout=10000)
        check("negative count rejected with inline error", True)
        page.screenshot(path=str(OUT / "validation_error.png"))
        # values preserved for correction
        check("form preserves entered values on error",
              page.input_value("#f-sales") == "8500.50")
        page.fill("#f-ft", "7")
        page.click("#entry-save")
        page.wait_for_selector("#save-notice", timeout=20000)
        check("correction + resubmission succeeds", True)

        # --- validation: bad money ----------------------------------------------
        open_entry(page, nth=2)
        page.fill("#f-sales", "abc")
        page.click("#entry-save")
        page.wait_for_selector("text=Invalid amount", timeout=10000)
        check("invalid money rejected", True)
        page.click("#entry-close")

        # --- branch manager store isolation (real RBAC behavior) ---------------
        page.click("#store-selector")
        page.wait_for_selector("[role='option']", state="visible")
        mgr_opts = page.locator("[role='option']").all_inner_texts()
        check("branch manager sees only assigned store",
              mgr_opts == ["A01 — Alpha Pavilion"])
        page.keyboard.press("Escape")

        # --- store switch clears stale data (admin has both stores) -------------
        ap = browser.new_page(viewport={"width": 1440, "height": 900})
        login(ap, "admin@ducklab.com", "AdminPass1234")
        ap.goto(f"{BASE}/ops/daily")
        ap.wait_for_selector("#daily-table", timeout=20000)
        ap.wait_for_selector("#daily-table >> text=RM 15,000.00",
                             timeout=15000)
        choose(ap, "#store-selector", "B02 — Beta Galleria",
               "#daily-table >> text=unreported", timeout=15000)
        # The wait selector above can match pre-switch unreported rows, so
        # deterministically wait for the switch to be APPLIED: the stale A01
        # figure must disappear (B02 has no records at all).
        ap.wait_for_function(
            "document.querySelector('#daily-table') && "
            "!document.querySelector('#daily-table').innerText.includes('RM 15,000.00')",
            timeout=15000)
        check("B02 shows unreported placeholders",
              ap.locator("#daily-table >> text=unreported").count() > 0)
        # B02 has no records: the A01 figure must NOT be present
        check("store switch clears previous store's rows",
              ap.locator("#daily-table >> text=RM 15,000.00").count() == 0)
        choose(ap, "#store-selector", "A01 — Alpha Pavilion",
               "#daily-table >> text=RM 15,000.00")
        ap.close()

        # --- month switch ---------------------------------------------------------
        choose(page, "#month-selector", "September 2026",
               "#daily-kpis >> text=RM 0.00")
        check("September shows zero MTD (no records)",
              page.locator("#daily-kpis >> text=RM 0.00").count() > 0)
        choose(page, "#month-selector", "October 2026",
               "#daily-table >> text=RM 15,000.00")

        # --- light theme ------------------------------------------------------------
        page.click("#theme-toggle")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "daily_overview_light.png"),
                        full_page=True)
        check("light theme renders", True)
        page.click("#theme-toggle")

        # --- viewer read-only ---------------------------------------------------------
        vp = browser.new_page(viewport={"width": 1440, "height": 900})
        login(vp, "viewer@ducklab.com", "ViewerPass123")
        open_daily(vp)
        check("viewer sees records (read)",
              vp.locator("#daily-table >> text=RM 15,000.00").count() > 0)
        check("viewer gets View button, not Edit",
              vp.locator("#daily-table >> text=View").count() > 0)
        vp.click("#daily-table >> text=View >> nth=0")
        vp.wait_for_selector("#f-sales", state="visible", timeout=15000)
        check("viewer form inputs disabled",
              vp.locator("#f-sales").is_disabled())
        check("viewer has no Save button",
              vp.locator("#entry-save").count() == 0)
        check("viewer sees read-only notice",
              vp.locator("text=read-only access").count() > 0)

        # --- responsive -----------------------------------------------------------------
        tablet = browser.new_page(viewport={"width": 768, "height": 1024})
        login(tablet, "manager@ducklab.com", "ManagerPass123")
        tablet.goto(f"{BASE}/ops/daily")
        tablet.wait_for_selector("#daily-table", timeout=20000)
        overflow = tablet.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth")
        check("tablet no page-level horizontal overflow", not overflow)
        tablet.screenshot(path=str(OUT / "daily_tablet.png"), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        login(mobile, "manager@ducklab.com", "ManagerPass123")
        mobile.goto(f"{BASE}/ops/daily")
        mobile.wait_for_selector("#daily-table", timeout=20000)
        overflow = mobile.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth")
        check("mobile no page-level horizontal overflow", not overflow)
        mobile.click("#daily-table >> text=Edit >> nth=0")
        mobile.wait_for_selector("#f-sales", state="visible", timeout=15000)
        check("mobile entry drawer opens", True)
        mobile.screenshot(path=str(OUT / "daily_mobile_form.png"),
                          full_page=True)

        # --- logout ----------------------------------------------------------------------
        page.goto(f"{BASE}/ops/daily")
        page.wait_for_selector("#daily-table", timeout=20000)
        page.click("#account-menu")
        page.wait_for_selector("#menu-sign-out")
        page.click("#menu-sign-out")
        page.wait_for_url("**/login**", timeout=10000)
        page.goto(f"{BASE}/ops/daily")
        page.wait_for_url("**/login**", timeout=10000)
        check("logout revokes daily page access", True)

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
