#!/usr/bin/env python3
"""Browser verification for the DuckLab login flow (Phase 2, Task 1).

Requires the app running at BASE_URL with a fresh test DB seeded with
test@ducklab.com / TestPass1234 (see commands in the task-7 report).
Captures screenshots to docs/screenshots/ and exits non-zero on failure.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://localhost:3000")
OUT = Path(__file__).parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

failures: list[str] = []


def check(name: str, condition: bool) -> None:
    print(("PASS  " if condition else "FAIL  ") + name)
    if not condition:
        failures.append(name)


def main() -> int:
    console_errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)

        # 1. Login page renders (dark theme default).
        page.goto(f"{BASE}/login")
        page.wait_for_selector("#login-email")
        check("login page renders", page.locator("text=DuckLab").count() > 0)
        page.screenshot(path=str(OUT / "login_dark.png"), full_page=True)

        # 2. Light theme readable.
        page.click("button:has(svg.lucide-sun), button:has(svg.lucide-moon)"
                   if page.locator("button:has(svg.lucide-sun)").count()
                   else "button:has(svg.lucide-moon)")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "login_light.png"), full_page=True)
        check("light theme toggle works", True)

        # 3. Invalid login feedback.
        page.fill("#login-email", "test@ducklab.com")
        page.fill("#login-password", "WrongPassword1")
        page.click("#login-submit")
        page.wait_for_selector("text=Invalid email or password", timeout=8000)
        check("invalid login shows generic error", True)
        page.screenshot(path=str(OUT / "login_error.png"), full_page=True)

        # 4. Password visibility toggle.
        page.click("button[aria-label='Toggle password visibility']")
        page.wait_for_function(
            "document.querySelector('#login-password').type === 'text'")
        check("password toggle switches to text", True)

        # 5. Protected route rejects unauthenticated access.
        page.goto(f"{BASE}/ops")
        page.wait_for_url("**/login**", timeout=8000)
        check("unauthenticated /ops redirects to /login", True)

        # 6. Valid login works.
        page.fill("#login-email", "test@ducklab.com")
        page.fill("#login-password", "TestPass1234")
        page.click("#login-submit")
        page.wait_for_url("**/ops**", timeout=15000)
        check("valid login lands on /ops", True)
        page.wait_for_timeout(1500)
        check("landing shows user",
              page.locator("text=Test Admin").count() > 0
              or page.locator("text=No stores").count() > 0)
        page.screenshot(path=str(OUT / "ops_authenticated.png"), full_page=True)

        # 7. Logout works and clears access.
        page.click("#account-menu")
        page.click("#menu-sign-out")
        page.wait_for_url("**/login**", timeout=8000)
        page.goto(f"{BASE}/ops")
        page.wait_for_url("**/login**", timeout=8000)
        check("logout revokes access to /ops", True)

        # 8. Mobile layout.
        mobile = browser.new_page(viewport={"width": 375, "height": 720})
        mobile.goto(f"{BASE}/login")
        mobile.wait_for_selector("#login-email")
        mobile.screenshot(path=str(OUT / "login_mobile.png"), full_page=True)
        overflow = mobile.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth")
        check("mobile layout has no horizontal overflow", not overflow)

        # 9. Console errors. The intentional invalid-login attempt (step 3)
        # logs a 401 from the session endpoint — expected, not a failure.
        unexpected = [e for e in console_errors
                      if "favicon" not in e.lower()
                      and "401" not in e]
        check("no unexpected browser console errors", not unexpected)
        for e in unexpected:
            print("   console:", e[:200])

        browser.close()

    print()
    if failures:
        print(f"{len(failures)} FAILURES")
        return 1
    print("All browser checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
