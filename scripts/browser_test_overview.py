#!/usr/bin/env python3
"""Browser smoke for Task 13: /ops overview dashboard, seeded DEV stores.
Pillar strip, command-center cards, needs-attention, pace chart, restricted
payroll, store switch, mobile. Screenshots -> docs/screenshots/phase2-task13/.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:3000")
SHOTS = Path("docs/screenshots/phase2-task13")
SHOTS.mkdir(parents=True, exist_ok=True)

failures: list[str] = []
console_errors: list[str] = []


def check(name: str, condition: bool) -> None:
    print(("PASS  " if condition else "FAIL  ") + name)
    if not condition:
        failures.append(name)


def make_users():
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from ducklab.app_service import AppService
    app = AppService("/home/duck/project/ducklab/.devdata/dev.db")
    admin1 = app.auth.get_user(1)
    email = f"task13-{secrets.token_hex(3)}@ducklab.dev"
    password = "Task13!" + secrets.token_hex(6)
    u = app.create_user(admin1, email, "Task13 QA", password)
    app.assign_role(admin1, u.id, "super_admin")
    v_email = f"task13v-{secrets.token_hex(3)}@ducklab.dev"
    v_password = "Task13v!" + secrets.token_hex(6)
    v = app.create_user(admin1, v_email, "Task13 Viewer", v_password)
    app.assign_role(admin1, v.id, "viewer")
    a01 = app.read_repo.get_store_by_code("A01").id
    app.assign_store(admin1, v.id, a01)
    return (email, password), (v_email, v_password)


def login(page, email, password):
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-email")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")
    page.wait_for_url("**/ops**", timeout=20000)


def choose(page, trigger, option_text):
    key = option_text.split(" — ")[0]
    for _ in range(4):
        try:
            page.click(trigger)
            page.wait_for_selector("[role='option']", state="visible",
                                   timeout=10000)
            page.keyboard.type(key, delay=40)
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1200)
            if key in page.locator(trigger).inner_text():
                return
        except Exception:
            page.wait_for_timeout(1200)
    raise RuntimeError(f"could not select {option_text!r}")


def goto_ops(page, store="A01", month="October 2026"):
    page.goto(f"{BASE}/ops")
    page.wait_for_selector("#pillar-strip", timeout=90000)
    choose(page, "#store-selector", store)
    choose(page, "#month-selector", month)
    page.wait_for_timeout(2500)


def main() -> int:
    (email, password), (v_email, v_password) = make_users()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        login(page, email, password)
        goto_ops(page)

        body = page.locator("body").inner_text()
        strip = page.locator("#pillar-strip").inner_text()
        check("pillar strip: 4 pillars with statuses",
              all(k in strip for k in ("Sales", "Expenses", "Manpower",
                                       "Stock")))
        check("pillar: engine status badge shown (e.g. Within/Achieved/Over)",
              any(k in strip for k in ("Achieved", "Below target",
                                       "Within budget", "Within target",
                                       "Over target", "No target",
                                       "No budget", "No payments yet")))
        # the V4 Overview has no "command center" card: the same facts are
        # in the pillar strip, the targets panel, the header line and At a
        # glance
        check("sales MTD, Level 1 target, days reported, open actions",
              "Sales MTD" in strip
              and "Level 1" in page.locator("#targets-panel").inner_text()
              and "days reported" in body
              and "Open actions" in page.locator("#at-a-glance").inner_text())
        check("needs attention: seeded overdue actions shown",
              "overdue action" in body)
        check("needs attention: unreported days (month in progress)",
              "unreported day" in body)
        check("pace chart rendered",
              page.locator("div.js-plotly-plot").count() >= 1)

        # card link preserves store + month
        page.locator("#pillar-strip a").first.click()
        page.wait_for_timeout(3000)
        check("card link opens a module page",
              "/ops/" in page.url and page.url != f"{BASE}/ops")
        check("module keeps store+month selection",
              "A01" in page.locator("#store-selector").inner_text()
              and "October" in page.locator("#month-selector").inner_text())

        page.goto(f"{BASE}/ops")
        page.wait_for_selector("#pillar-strip", timeout=90000)
        choose(page, "#store-selector", "A01")
        choose(page, "#month-selector", "October 2026")
        page.wait_for_timeout(2000)
        page.screenshot(path=str(SHOTS / "overview-dark-desktop.png"),
                        full_page=True)
        page.click("#theme-toggle")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(SHOTS / "overview-light-desktop.png"),
                        full_page=True)
        check("desktop: no console errors", not console_errors)
        ctx.close()

        # restricted viewer
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
        vp = ctx2.new_page()
        login(vp, v_email, v_password)
        goto_ops(vp)
        strip = vp.locator("#pillar-strip").inner_text()
        check("viewer: payroll shows Restricted", "Restricted" in strip)
        ctx2.close()

        # mobile
        ctx3 = browser.new_context(viewport={"width": 390, "height": 844})
        m = ctx3.new_page()
        mob_errors: list[str] = []
        m.on("console", lambda e: mob_errors.append(e.text)
             if e.type == "error" else None)
        m.on("pageerror", lambda e: mob_errors.append(str(e)))
        login(m, email, password)
        goto_ops(m)
        overflow = m.evaluate(
            "document.documentElement.scrollWidth > "
            "document.documentElement.clientWidth + 1")
        check("mobile: no horizontal page overflow", not overflow)
        m.screenshot(path=str(SHOTS / "overview-mobile.png"), full_page=True)
        check("mobile: no console errors", not mob_errors)
        ctx3.close()
        browser.close()

    for e in console_errors[:5]:
        print("  console error:", e)
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
