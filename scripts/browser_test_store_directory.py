#!/usr/bin/env python3
"""Browser smoke for the store directory: New Store auto-fill, Monthly Setup
history button + BD Target, and the Sales History chart toggle.

Run against a dev instance whose DUCKLAB_DB_PATH already holds the imported
directory (scripts/import_store_directory.py). Set DUCKLAB_UI to its URL.
Screenshots -> /tmp/opencode/opscore-dev/shots/.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:47822")
OUT = Path("/tmp/opencode/opscore-dev/shots")
OUT.mkdir(parents=True, exist_ok=True)

failures: list[str] = []


def check(name: str, condition: bool) -> None:
    print(("PASS  " if condition else "FAIL  ") + name)
    if not condition:
        failures.append(name)


def login(page):
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-email")
    page.fill("#login-email", "qa@example.com")
    page.fill("#login-password", "QaPassword123")
    page.click("#login-submit")
    page.wait_for_url("**/ops**", timeout=30000)


def choose(page, trigger, option_text, timeout=30000):
    for _ in range(3):
        try:
            page.click(trigger)
            page.wait_for_selector(
                f"[role='option']:has-text(\"{option_text}\")",
                state="visible", timeout=10000)
            page.click(f"[role='option']:has-text(\"{option_text}\")")
            return
        except Exception:
            page.wait_for_timeout(1200)
    raise RuntimeError(f"could not select {option_text!r}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" else None)
        login(page)

        # --- New Store auto-fill --------------------------------------
        page.goto(f"{BASE}/ops/stores")
        page.wait_for_selector("#store-new", state="visible", timeout=30000)
        page.click("#store-new")
        page.wait_for_selector("#sf-code", state="visible", timeout=15000)

        page.fill("#sf-code", "B00")          # partial: must NOT auto-fill
        page.locator("#sf-name").click()
        page.wait_for_timeout(900)
        check("partial code does not auto-fill",
              page.locator("#sf-name").input_value() == "")

        page.fill("#sf-code", "b001")         # exact, case-insensitive
        page.locator("#sf-name").click()      # blur triggers the lookup
        page.wait_for_timeout(1500)
        check("exact code fills Name", page.locator("#sf-name").input_value() == "CHP")
        check("auto-fill sets State", page.locator("#sf-state").inner_text().strip() == "WP")
        check("auto-fill sets Zone", page.locator("#sf-zone").input_value() == "KV-6")
        check("Concept is left for the user",
              "Shop Lot" not in page.locator("#sf-concept").inner_text())
        page.screenshot(path=str(OUT / "01-autofill.png"), full_page=True)

        page.select_option("#sf-concept", label="Outlet") if False else None
        page.click("#sf-concept")
        page.click("[role='option']:has-text('Outlet')")
        page.click("#store-form-save")
        page.wait_for_timeout(2500)
        check("store saved", page.locator("#store-form-save").count() == 0
              or not page.locator("#sf-code").is_visible())

        # --- Monthly Setup: history button + BD Target ------------------
        page.wait_for_selector("#setup-bd-target-cents", timeout=30000)
        check("BD Target field exists",
              page.locator("#setup-bd-target-cents").count() == 1)
        check("history button enabled for an imported month",
              page.locator("#setup-load-history").is_enabled())
        page.click("#setup-load-history")
        page.wait_for_timeout(1800)
        page.screenshot(path=str(OUT / "02-setup-history.png"), full_page=True)
        lm = page.locator("#setup-sales-last-month-cents").input_value()
        ly = page.locator("#setup-sales-last-year-cents").input_value()
        print(f"      last month={lm!r} last year={ly!r}")
        page.fill("#setup-bd-target-cents", "148000")
        page.click("#setup-save")
        page.wait_for_timeout(2500)
        check("BD Target saved",
              page.locator("#setup-bd-target-cents").input_value() == "148000.00")
        page.screenshot(path=str(OUT / "03-setup-saved.png"), full_page=True)

        # --- Sales History chart ---------------------------------------
        page.goto(f"{BASE}/ops/sales")
        page.wait_for_timeout(6000)
        check("history card rendered",
              page.locator("#history-section").count() == 1)
        check("timeline is the default",
              page.locator("#history-mode-timeline").is_checked())
        page.screenshot(path=str(OUT / "04-history-timeline.png"), full_page=True)
        if page.locator("#history-mode-yoy").count():
            page.locator("#history-mode-yoy").click(force=True)
            page.wait_for_timeout(2000)
            page.screenshot(path=str(OUT / "05-history-yoy.png"), full_page=True)
        check("no console errors", not errors)
        if errors:
            print("      " + "\n      ".join(errors[:5]))
        browser.close()
    print("\n" + ("FAILED: " + ", ".join(failures) if failures else "All checks passed"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
