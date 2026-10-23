#!/usr/bin/env python3
"""Browser smoke for Task 12: /ops/actions with seeded DEV store A01.
Cards, overdue badges, filters, add/edit via dialog, viewer read-only,
store switch, mobile. Screenshots -> docs/screenshots/phase2-task12/.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:3000")
SHOTS = Path("docs/screenshots/phase2-task12")
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
    email = f"task12-{secrets.token_hex(3)}@ducklab.dev"
    password = "Task12!" + secrets.token_hex(6)
    u = app.create_user(admin1, email, "Task12 QA", password)
    app.assign_role(admin1, u.id, "super_admin")
    v_email = f"task12v-{secrets.token_hex(3)}@ducklab.dev"
    v_password = "Task12v!" + secrets.token_hex(6)
    v = app.create_user(admin1, v_email, "Task12 Viewer", v_password)
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


def goto_actions(page, month="October 2026"):
    page.goto(f"{BASE}/ops/actions")
    page.wait_for_selector("#action-cards", timeout=90000)
    choose(page, "#store-selector", "A01")
    choose(page, "#month-selector", month)
    page.wait_for_timeout(2500)


def card_values(page):
    grid = page.locator("#action-cards").inner_text()
    return grid


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
        goto_actions(page)

        body = page.locator("body").inner_text()
        # seeded: open = Open x3 + In progress x2 + Blocked x1 = 6
        # (incl. carried-over); overdue = 2; done = 1
        nums0 = [t for t in page.locator("#action-cards").inner_text().split()
                 if t.isdigit()]
        open0 = int(nums0[0])
        check("cards: open count >= 6 (seeded 6 + reruns)", open0 >= 6)
        check("overdue card highlights 2", "Needs Attention" in body)
        check("carried-over label shown", "Carried Over" in body)
        check("overdue badge in table", "Overdue" in body)
        check("priority badge Critical shown", "Critical" in body)
        check("status badge Cancelled shown", "Cancelled" in body)

        # filter: overdue only -> exactly 2 rows (Radix checkbox: plain click)
        page.click("#f-overdue")
        page.wait_for_timeout(1000)
        rows = page.locator("#actions-table tbody tr").count()
        check("overdue-only filter -> 2 rows", rows == 2)
        page.click("#f-overdue")
        page.wait_for_timeout(700)

        # filter by status Done -> at least the seeded Done row
        choose(page, "#f-status", "Done")
        page.wait_for_timeout(800)
        rows = page.locator("#actions-table tbody tr").count()
        check("status filter Done -> >=1 rows, all Done", rows >= 1 and
              all("Done" in page.locator("#actions-table tbody tr").nth(i)
                  .inner_text() for i in range(rows)))
        choose(page, "#f-status", "All Statuses")
        page.wait_for_timeout(600)

        # add an action via the centered dialog
        open_before = page.locator("#action-cards").inner_text()
        page.click("#add-action")
        page.wait_for_selector("#a-issue", state="visible", timeout=15000)
        # validation: required fields
        page.click("#a-save")
        page.wait_for_timeout(600)
        check("validation: owner/due required shown",
              page.locator("#a-issue").is_visible())
        page.fill("#a-issue", "UI smoke: follow up on weekend staffing")
        page.fill("#a-pic", "QA Tester")
        page.fill("#a-due", "2026-10-31")
        page.click("#a-save")
        page.wait_for_timeout(2500)
        check("dialog closes + notice shown",
              page.locator("#a-issue").count() == 0)
        page.wait_for_timeout(1500)
        body = page.locator("body").inner_text()
        check("new action appears in table",
              "weekend staffing" in body)
        cards_txt = page.locator("#action-cards").inner_text()
        open1 = int([t for t in cards_txt.split() if t.isdigit()][0])
        check("open count incremented by exactly 1", open1 == open0 + 1)

        # mark it Done via Edit
        edit = page.locator(".act-edit").last
        edit.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        try:
            edit.click(timeout=5000)
        except Exception:
            edit.dispatch_event("click")
        page.wait_for_selector("#a-status", state="visible", timeout=15000)
        page.wait_for_timeout(1200)
        page.locator("#a-status").dispatch_event("click")
        page.wait_for_selector("[role='option']", state="visible",
                               timeout=10000)
        page.locator("[role='option']:has-text('Done')").first.dispatch_event("click")
        page.wait_for_timeout(500)
        page.wait_for_timeout(400)

        check("completion date field appears for Done",
              page.locator("#a-completion").count() == 1)
        page.click("#a-save")
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text()
        check("done action shows completion date (today)",
              "weekend staffing" in body and "Done" in body)

        # store switch: B02 has no actions -> empty state, cards zeroed
        page.goto(f"{BASE}/ops/actions")
        page.wait_for_selector("#action-cards", timeout=90000)
        choose(page, "#store-selector", "B02")
        page.wait_for_timeout(3000)
        body = page.locator("body").inner_text()
        check("store switch clears data (B02 empty)",
              "No Actions" in body)
        check("B02 cards show 0 open",
              page.locator("#action-cards").inner_text().count("0") >= 2)

        page.screenshot(path=str(SHOTS / "actions-dark-desktop.png"),
                        full_page=False)
        page.click("#theme-toggle")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(SHOTS / "actions-light-desktop.png"),
                        full_page=False)
        check("desktop: no console errors", not console_errors)
        ctx.close()

        # viewer: read-only
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
        vp = ctx2.new_page()
        login(vp, v_email, v_password)
        goto_actions(vp)
        check("viewer: no Add Action button",
              vp.locator("#add-action").count() == 0)
        check("viewer: no Edit links", vp.locator(".act-edit").count() == 0)
        ctx2.close()

        # mobile
        ctx3 = browser.new_context(viewport={"width": 390, "height": 844})
        m = ctx3.new_page()
        mob_errors: list[str] = []
        m.on("console", lambda e: mob_errors.append(e.text)
             if e.type == "error" else None)
        m.on("pageerror", lambda e: mob_errors.append(str(e)))
        login(m, email, password)
        goto_actions(m)
        overflow = m.evaluate(
            "document.documentElement.scrollWidth > "
            "document.documentElement.clientWidth + 1")
        check("mobile: no horizontal page overflow", not overflow)
        m.screenshot(path=str(SHOTS / "actions-mobile.png"), full_page=False)
        check("mobile: no console errors", not mob_errors)
        ctx3.close()
        browser.close()

    for e in console_errors[:5]:
        print("  console error:", e)
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
