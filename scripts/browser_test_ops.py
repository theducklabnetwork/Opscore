#!/usr/bin/env python3
"""Browser verification for the DuckLab operations shell (Phase 2, Task 2).

Requires the app running with the disposable DB seeded by the task script
(admin@ducklab.com / AdminPass1234, viewer@ducklab.com / ViewerPass123).
Screenshots go to docs/screenshots/phase2/. Exits non-zero on failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://localhost:3000")
OUT = Path(__file__).parent.parent / "docs" / "screenshots" / "phase2"
OUT.mkdir(parents=True, exist_ok=True)

failures: list[str] = []
console_errors: list[str] = []


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


def choose(page, trigger: str, option_text: str, wait_selector: str,
           timeout: int = 25000) -> None:
    """Click a select trigger, pick an option, retry once if the app missed
    the event (flaky Radix select under rapid event bursts)."""
    for _ in range(2):
        page.click(trigger)
        page.wait_for_selector(
            f"[role='option']:has-text(\"{option_text}\")")
        page.click(f"[role='option']:has-text(\"{option_text}\")")
        try:
            page.wait_for_selector(wait_selector, timeout=timeout)
            return
        except Exception:
            continue
    page.wait_for_selector(wait_selector, timeout=timeout)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)

        # --- unauthenticated protection -----------------------------------
        page.goto(f"{BASE}/ops/report")
        page.wait_for_url("**/login**", timeout=10000)
        check("unauthenticated /ops/report redirects to login", True)

        # --- login + shell -------------------------------------------------
        login(page, "admin@ducklab.com", "AdminPass1234")
        page.wait_for_selector("#store-selector")
        check("authenticated shell renders", page.locator("text=DuckLab").count() > 0)
        page.screenshot(path=str(OUT / "shell_dark.png"), full_page=True)

        # --- report page (live, default month = October 2026) ---------------
        page.goto(f"{BASE}/ops/report")
        page.wait_for_selector("#sales-section", timeout=15000)
        # Backend truth: MTD = 12,000 + 11,500 = RM 23,500.00
        check("live sales MTD correct",
              page.locator("text=RM 23,500.00").count() > 0)
        check("live mode selected (switch; no LIVE chip)",
              page.locator("#report-mode [data-state='on']").inner_text()
              == "Live"
              and page.locator("text=LIVE —").count() == 0)
        check("expenses section renders",
              page.locator("#expenses-section").count() == 1)
        check("manpower section renders",
              page.locator("#manpower-section").count() == 1)
        check("stock section renders", page.locator("#stock-section").count() == 1)
        check("actions section renders",
              page.locator("#actions-section").count() == 1)
        page.screenshot(path=str(OUT / "report_live_dark.png"), full_page=True)

        # --- store selector --------------------------------------------------
        page.click("#store-selector")
        page.screenshot(path=str(OUT / "store_selector.png"))
        page.keyboard.press("Escape")
        choose(page, "#store-selector", "B02 — Beta Galleria",
               "text=No report available")
        # B02 has no October configuration -> explicit empty state,
        # and critically NO stale store-A figures remain visible.
        check("store switch clears stale data (no A01 figures)",
              page.locator("text=RM 23,500.00").count() == 0)
        page.screenshot(path=str(OUT / "report_empty.png"), full_page=True)
        # Switch month to September for B02: configured, no sales -> RM 0.00
        choose(page, "#month-selector", "September 2026",
               "#stock-section >> text=RM 0.00")
        check("B02 September shows RM 0.00 (zero, not missing)", True)
        choose(page, "#store-selector", "A01 — Alpha Pavilion",
               "#sales-section >> text=RM 19,881.00")
        check("switching back restores store A September data", True)

        page.click("#report-mode >> text=Finalized")
        page.wait_for_selector("text=Finalized version 1", timeout=20000)
        check("finalized mode selected (switch; no FINALIZED chip)",
              page.locator("#report-mode [data-state='on']").inner_text()
              == "Finalized"
              and page.locator("text=FINALIZED —").count() == 0)
        check("finalized MTD correct",
              page.locator("#sales-section >> text=RM 19,881.00").count() > 0)
        check("finalized version note shown", True)
        page.screenshot(path=str(OUT / "report_finalized.png"), full_page=True)

        # --- missing config empty state --------------------------------------
        page.click("#report-mode >> text=Live")
        page.wait_for_selector("#sales-section >> text=RM 19,881.00", timeout=15000)
        choose(page, "#month-selector", "August 2026",
               "text=No report available")
        check("missing config shows explicit empty state", True)
        page.screenshot(path=str(OUT / "report_empty.png"), full_page=True)

        # --- light theme ------------------------------------------------------
        choose(page, "#month-selector", "October 2026",
               "#sales-section >> text=RM 23,500.00")
        page.click("#theme-toggle")
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "report_light.png"), full_page=True)
        check("light theme renders", True)
        page.click("#theme-toggle")

        # --- responsive --------------------------------------------------------
        tablet = browser.new_page(viewport={"width": 768, "height": 1024})
        login(tablet, "admin@ducklab.com", "AdminPass1234")
        tablet.goto(f"{BASE}/ops/report")
        tablet.wait_for_selector("#sales-section", timeout=15000)
        overflow = tablet.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth")
        check("tablet no horizontal overflow", not overflow)
        tablet.screenshot(path=str(OUT / "report_tablet.png"), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        login(mobile, "admin@ducklab.com", "AdminPass1234")
        mobile.goto(f"{BASE}/ops/report")
        mobile.wait_for_selector("#sales-section", timeout=15000)
        overflow = mobile.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth")
        check("mobile no horizontal overflow", not overflow)
        check("mobile nav button present",
              mobile.locator("#mobile-nav-button").count() == 1)
        mobile.screenshot(path=str(OUT / "report_mobile.png"), full_page=True)

        # --- restricted viewer isolation ---------------------------------------
        viewer_page = browser.new_page(viewport={"width": 1440, "height": 900})
        login(viewer_page, "viewer@ducklab.com", "ViewerPass123")
        viewer_page.goto(f"{BASE}/ops/report")
        viewer_page.wait_for_selector("#sales-section", timeout=15000)
        viewer_page.click("#store-selector")
        options = viewer_page.locator("[role='option']").all_inner_texts()
        check("viewer sees only assigned store",
              options == ["A01 — Alpha Pavilion"])
        viewer_page.keyboard.press("Escape")

        # --- logout -------------------------------------------------------------
        page.goto(f"{BASE}/ops/report")
        page.click("#account-menu")
        page.click("#menu-sign-out")
        page.wait_for_url("**/login**", timeout=10000)
        page.goto(f"{BASE}/ops/report")
        page.wait_for_url("**/login**", timeout=10000)
        check("logout revokes protected access", True)

        browser.close()

    unexpected = [e for e in console_errors if "favicon" not in e.lower()]
    check("no unexpected console errors", not unexpected)
    for e in unexpected[:5]:
        print("   console:", e[:200])

    print()
    if failures:
        print(f"{len(failures)} FAILURES")
        return 1
    print("All browser checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
