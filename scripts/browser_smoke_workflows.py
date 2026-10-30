#!/usr/bin/env python3
"""Browser smoke for the extracted state workflows (Phase 3.5).

Runs against a disposable DB seeded by scripts/seed_ui_schedule.py
(admin@ducklab.com / AdminPass1234). Covers the workflows moved out of the
large state modules — without touching implementation internals:

  1. Manpower weekly schedule tab (builder renders staff/codes/target).
  2. Daily Operations schedule apply (preview -> confirm fills the form).
  3. Monthly Report finalize / correct / export / version picker.
  4. Store Management: create store, assign user, save Monthly Setup.

Exits non-zero on failure. Downloads go to a temp dir.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:3210")
DB = os.environ.get("DUCKLAB_UI_DB", "/tmp/opencode/ui-workflows.db")
OUT = Path(tempfile.mkdtemp(prefix="ducklab-smoke-"))

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
    page.wait_for_url("**/ops**", timeout=20000)


def choose(page, trigger: str, option_text: str, wait_selector: str | None = None,
           timeout: int = 25000) -> None:
    """Click a select trigger, pick an option, retry once if the app missed
    the event (flaky Radix select under rapid event bursts)."""
    for _ in range(3):
        page.click(trigger)
        page.wait_for_selector(f"[role='option']:has-text(\"{option_text}\")")
        page.click(f"[role='option']:has-text(\"{option_text}\")")
        try:
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=timeout)
            return
        except Exception:
            if option_text in page.locator(trigger).inner_text():
                return
            continue
    if wait_selector:
        page.wait_for_selector(wait_selector, timeout=timeout)


def seed_extra_record() -> None:
    """One more daily record after finalizing, so Correct picks up a live
    change (via AppService; the running server reads the same DB)."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from datetime import date
    from ducklab.app_service import AppService
    app = AppService(DB)
    admin = app.auth.get_user(1)
    store = app.read_repo.get_store_by_code("A01")
    app.record_daily(admin, store.id, date(2026, 10, 6), sales_cents=10000)


def main() -> int:
    code = "W35"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        login(page, "admin@ducklab.com", "AdminPass1234")

        # --- 1. Manpower weekly schedule tab ---------------------------
        page.goto(f"{BASE}/ops/manpower")
        page.wait_for_selector("#manpower-tab-schedule", timeout=90000)
        page.wait_for_timeout(1000)
        page.click("#manpower-tab-schedule")
        page.wait_for_selector("#schedule-builder", timeout=20000)
        page.wait_for_timeout(1000)
        builder = page.locator("#schedule-builder").inner_text()
        check("schedule builder lists seeded staff",
              "Alice" in builder and "Bob" in builder)
        check("schedule target loaded from seed",
              page.locator("#schedule-target").input_value() == "2")
        page.click("#schedule-tab-generated")
        page.wait_for_selector("#schedule-generated", timeout=15000)
        generated = page.locator("#schedule-generated").inner_text()
        check("generated view shows staff and shift codes",
              "Alice" in generated and "M900" in generated)

        # --- 2. Daily Operations: schedule apply ------------------------
        page.goto(f"{BASE}/ops/daily")
        page.wait_for_selector("#daily-table", timeout=90000)
        page.wait_for_timeout(1000)
        # rows are date-ascending: Oct 1 first, Oct 5 second
        page.click("#daily-table >> text=Edit >> nth=1")
        page.wait_for_selector("#apply-schedule", timeout=20000)
        page.click("#apply-schedule")
        page.wait_for_selector("#schedule-preview", timeout=20000)
        preview = page.locator("#schedule-preview").inner_text()
        check("schedule preview shows seeded counts",
              "Full-Time Staff: 1" in preview
              and "Part-Time Staff: 1" in preview)
        page.click("#apply-schedule-confirm")
        page.wait_for_function(
            "document.querySelector('#f-ft').value === '1'", timeout=15000)
        check("apply fills staffing fields",
              page.locator("#f-ft").input_value() == "1"
              and page.locator("#f-pt").input_value() == "1")
        page.screenshot(path=str(OUT / "daily-schedule-apply.png"))
        page.click("#entry-close")

        # --- 3. Monthly Report: finalize, correct, export, versions -----
        page.goto(f"{BASE}/ops/report")
        page.wait_for_selector("#export-report:not([disabled])",
                               timeout=90000)
        page.click("#finalize-month")
        page.wait_for_selector("#finalize-dialog", timeout=15000)
        check("finalize dialog offers version 1",
              "Version 1" in page.locator("#finalize-confirm").inner_text())
        page.click("#finalize-confirm")
        page.wait_for_selector("text=Finalized version 1", timeout=20000)
        check("finalize selects new current version",
              "Version 1" in page.locator("#version-selector").inner_text()
              and "Current" in page.locator("#version-selector").inner_text())
        with page.expect_download(timeout=20000) as download_info:
            page.click("#export-report")
        download = download_info.value
        path = OUT / download.suggested_filename
        download.save_as(str(path))
        check("finalized export downloads a versioned workbook",
              "finalized-v1" in download.suggested_filename
              and os.path.getsize(path) > 0)
        seed_extra_record()
        # toggle Live -> Finalized: refreshes the report (recomputes the
        # live preflight that enables Corrections) and smokes select_mode
        page.click("#report-mode >> text=Live")
        page.wait_for_selector("#finalize-month:not([disabled])",
                               timeout=20000)
        page.click("#report-mode >> text=Finalized")
        page.wait_for_selector("#correct-month:not([disabled])",
                               timeout=20000)
        page.click("#correct-month")
        page.wait_for_selector("#finalize-dialog", timeout=15000)
        check("correct dialog snapshots current live data",
              "current live data" in page.locator("#finalize-dialog").inner_text())
        page.click("#finalize-confirm")
        page.wait_for_selector("text=Finalized version 2", timeout=20000)
        page.click("#version-selector")
        page.wait_for_selector("[role='option']", timeout=15000)
        options = " ".join(o.inner_text() for o in
                           page.locator("[role='option']").all())
        check("version picker shows Current + Superseded",
              "Current" in options and "Superseded" in options)
        page.keyboard.press("Escape")
        page.screenshot(path=str(OUT / "report-versions.png"))

        # --- 4. Store Management: create, assign, Monthly Setup ---------
        page.goto(f"{BASE}/ops/stores")
        page.wait_for_selector("#store-new", timeout=90000)
        page.wait_for_timeout(1000)
        page.click("#store-new")
        page.wait_for_selector("#sf-code", state="visible", timeout=15000)
        page.fill("#sf-code", code)
        page.fill("#sf-name", "Workflow Smoke")
        page.click("#store-form-save")
        page.wait_for_selector(f"#store-users-{code}", timeout=20000)
        check("store created and listed", True)
        page.click(f"#store-users-{code}")
        page.wait_for_selector("#assign-select", timeout=15000)
        choose(page, "#assign-select", "admin@ducklab.com")
        page.wait_for_timeout(500)
        page.click("#assign-submit")
        page.wait_for_timeout(1500)
        check("store assignment saved",
              "admin@ducklab.com" in page.locator("body").inner_text())
        # Monthly Setup for the new store (reload so the selector refreshes)
        page.goto(f"{BASE}/ops/stores")
        page.wait_for_selector("#setup-save", timeout=60000)
        choose(page, "#store-selector", f"{code} — Workflow Smoke",
               wait_selector="#setup-save")
        page.wait_for_timeout(1000)
        page.fill("#setup-level1-base-cents", "200000")
        # Level 2-4 uplifts are mandatory once a Level 1 target is set;
        # the HQ defaults button fills them (+10/+20/+30).
        page.click("#setup-hq-levels")
        page.fill("#setup-part-timer-factor", "0.5")
        page.click("#setup-save")
        try:
            page.wait_for_selector("text=Monthly setup saved.", timeout=20000)
            check("Monthly Setup saved", True)
        except Exception:
            page.screenshot(path=str(OUT / "stores-setup-failed.png"))
            try:
                body = page.locator("body").inner_text()
                for hint in ("Save failed", "uplift", "Session expired",
                             "Select a store", "Nothing to save"):
                    if hint in body:
                        print(f"   setup error hint in page: {hint!r}")
            except Exception:
                pass
            check("Monthly Setup saved", False)
        page.screenshot(path=str(OUT / "stores-setup.png"))

        check("no console errors", not console_errors)
        for e in console_errors[:5]:
            print("  console error:", e)
        browser.close()
    print(f"screenshots/downloads: {OUT}")
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
