#!/usr/bin/env python3
"""Visual verification for the Daily Entry dialog (Phase 2, Task 6b).

Requires the dev instance with the seeded dev DB. Screenshots go to
docs/screenshots/phase2-task6b/. Exits non-zero on failure.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:3000")
OUT = Path(__file__).parent.parent / "docs" / "screenshots" / "phase2-task6b"
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
    email = f"dlg-{secrets.token_hex(3)}@ducklab.dev"
    password = "Dlg!" + secrets.token_hex(6)
    admin1 = app.auth.get_user(1)
    u = app.create_user(admin1, email, "Dialog QA", password)
    app.assign_role(admin1, u.id, "super_admin")
    return email, password


def login(page, email, password):
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-email")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")
    page.wait_for_url("**/ops**", timeout=20000)


def open_dialog(page):
    page.goto(f"{BASE}/ops/daily?date=2026-10-01")
    page.wait_for_selector("#f-sales", state="visible", timeout=90000)


def dialog_box(page):
    return page.locator("#entry-save").locator(
        "xpath=ancestor-or-self::*[@role='dialog'][1]").bounding_box()


def main() -> int:
    email, password = make_admin()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        login(page, email, password)

        open_dialog(page)
        page.wait_for_timeout(800)  # allow entrance animation to finish

        box = dialog_box(page)
        check("dialog found", box is not None)
        if box:
            cx = box["x"] + box["width"] / 2
            check(f"centered (center-x {cx:.0f} vs 720±8)",
                  abs(cx - 720) <= 8)
            check(f"width ~720 (got {box['width']:.0f})",
                  700 <= box["width"] <= 730)
            check(f"height <= 85vh (got {box['height']:.0f})",
                  box["height"] <= 765)
        # overlay present and semi-transparent
        overlay = page.evaluate(
            """() => {
                const el = document.querySelector('[data-state="open"].rt-DialogOverlay');
                if (!el) return null;
                const cs = getComputedStyle(el);
                return {bg: cs.backgroundColor, pos: cs.position};
            }""")
        check("semi-transparent overlay", bool(overlay)
              and "rgba" in str(overlay.get("bg")))
        check("no page layout shift (no body scroll lock offset)",
              page.evaluate("() => document.documentElement.style.paddingRight") == "")

        # header/footer pinned while body scrolls: footer visible at top…
        check("footer visible without scrolling",
              page.locator("#entry-save").is_visible())
        # …and still visible after scrolling the body to the bottom
        page.evaluate(
            """() => {
                const vps = document.querySelectorAll(
                    '[data-radix-scroll-area-viewport]');
                for (const vp of vps) {
                    if (vp.querySelector('#f-sales')) vp.scrollTop = 1e6;
                }
            }""")
        page.wait_for_timeout(300)
        check("footer visible after scrolling body",
              page.locator("#entry-save").is_visible())
        check("info-only section present",
              "Information Only" in page.locator("body").inner_text())

        page.screenshot(path=str(OUT / "dialog-desktop-dark.png"))
        print("  -> dialog-desktop-dark.png")
        page.keyboard.press("Escape")  # close dialog (overlay blocks header)
        page.wait_for_timeout(600)
        page.click("#theme-toggle")
        page.wait_for_timeout(1200)
        open_dialog(page)
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "dialog-desktop-light.png"))
        print("  -> dialog-desktop-light.png")

        mob = browser.new_page(viewport={"width": 390, "height": 844})
        login(mob, email, password)
        open_dialog(mob)
        mob.wait_for_timeout(800)
        mbox = dialog_box(mob)
        if mbox:
            check(f"mobile width <= 92vw (got {mbox['width']:.0f})",
                  mbox["width"] <= 0.92 * 390 + 1)
            check("mobile roughly centered",
                  abs(mbox["x"] + mbox["width"] / 2 - 195) <= 10)
        mob.screenshot(path=str(OUT / "dialog-mobile.png"))
        print("  -> dialog-mobile.png")

        check("no console errors", not console_errors)
        for e in console_errors[:5]:
            print("  console error:", e)
        browser.close()
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
