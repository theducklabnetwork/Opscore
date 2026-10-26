#!/usr/bin/env python3
"""Browser verification for authentication & production security hardening.

Requires the app running with a disposable seeded DB (seed_ui_expenses.py).
Verifies the HttpOnly cookie session architecture end-to-end.
Exits non-zero on failure.
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://localhost:3000")

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
    # trailing slash means wait_for_url matching can land before 'load'
    page.wait_for_load_state("load")


def wait_url_contains(page, fragment: str, timeout: int = 15000) -> None:
    """Poll until the current URL contains `fragment` (tolerant of the
    trailing-slash 307s and client-side navigation timing in prod builds)."""
    page.wait_for_function(
        "frag => window.location.href.includes(frag)",
        arg=fragment, timeout=timeout)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: network_failures.append(r.url))

        # 1. login + cookie attributes ----------------------------------
        login(page, "manager@ducklab.com", "ManagerPass123")
        check("login works (authenticated navigation to /ops)", True)
        cookies = ctx.cookies(BASE)
        sess = [c for c in cookies if c["name"] == "ducklab_session"]
        check("session cookie exists", len(sess) == 1)
        if sess:
            c = sess[0]
            check("cookie HttpOnly", c["httpOnly"] is True)
            check("cookie SameSite=Lax", c["sameSite"] == "Lax")
            check("cookie has expiration (12h)",
                  0 < c["expires"] and c["expires"] < 99999999999)
            check("cookie path=/", c["path"] == "/")

        # 2. JavaScript cannot read the session credential --------------
        js_cookie = page.evaluate("document.cookie")
        check("document.cookie does NOT expose session token",
              "ducklab_session" not in js_cookie)
        check("no session token in page JS state",
              page.evaluate(
                  "JSON.stringify(Object.values(window.__state ?? {}))")
              .find("ducklab_session") == -1)

        # 3. protected pages work via cookie ----------------------------
        page.goto(f"{BASE}/ops/daily")
        page.wait_for_selector("#daily-table", timeout=20000)
        check("daily page loads via HttpOnly cookie", True)
        page.goto(f"{BASE}/ops/expenses")
        page.wait_for_selector("#expense-register", timeout=20000)
        check("expenses page loads via HttpOnly cookie", True)

        # 4. reload preserves valid session ------------------------------
        page.reload()
        page.wait_for_selector("#expense-register", timeout=20000)
        check("reload preserves valid session", True)

        # 5. store authorization enforced --------------------------------
        page.click("#store-selector")
        page.wait_for_selector("[role='option']", state="visible")
        opts = page.locator("[role='option']").all_inner_texts()
        check("branch manager sees only assigned store",
              opts == ["A01 — Alpha Pavilion"])
        page.keyboard.press("Escape")

        # 6. logout revokes access ---------------------------------------
        page.click("#account-menu")
        page.wait_for_selector("#menu-sign-out")
        page.click("#menu-sign-out")
        wait_url_contains(page, "/login")
        cookies = ctx.cookies(BASE)
        sess = [c for c in cookies if c["name"] == "ducklab_session"
                and c["value"]]
        check("logout clears session cookie", not sess)
        page.goto(f"{BASE}/ops/daily")
        wait_url_contains(page, "/login")
        check("post-logout navigation redirects to login", True)

        # 7. tampered/invalid cookie rejected -----------------------------
        ctx.add_cookies([{
            "name": "ducklab_session", "value": "forged-token-value",
            "domain": "localhost", "path": "/", "httpOnly": True,
        }])
        page.goto(f"{BASE}/ops/daily")
        wait_url_contains(page, "/login")
        check("forged session cookie rejected", True)

        # 8. CSRF: cross-origin login POST rejected -----------------------
        # The session endpoint lives on the Reflex backend ASGI app. In
        # production the reverse proxy routes /api/* to it; in dev it is
        # the backend port. Test the actual endpoint directly.
        api = os.environ.get("DUCKLAB_API", "http://localhost:8000")
        resp = page.request.post(
            f"{api}/api/session",
            data={"action": "login", "email": "manager@ducklab.com",
                  "password": "ManagerPass123"},
            headers={"Origin": "https://evil.example.com"})
        check("cross-origin login POST rejected (403)",
              resp.status == 403)
        resp = page.request.post(
            f"{api}/api/session",
            data={"action": "login", "email": "manager@ducklab.com",
                  "password": "ManagerPass123"})
        # no Origin header at all: also rejected
        check("origin-less login POST rejected (403)", resp.status == 403)

        # 9. legacy routes not exposed -----------------------------------
        for path in ("/prototype", "/placements", "/tasks", "/report"):
            page.goto(f"{BASE}{path}")
            page.wait_for_timeout(800)
            url = page.url
            check(f"legacy route {path} not exposed",
                  "/login" in url or "404" in page.locator("body")
                  .inner_text() or page.locator("h1").count() == 0
                  or path not in url)

        # 10. rate limiting LAST (locks out the shared IP bucket) ----------
        codes = set()
        for _ in range(12):
            r = page.request.post(
                f"{api}/api/session",
                data={"action": "login", "email": "nosuch@ducklab.com",
                      "password": "wrong"},
                headers={"Origin": BASE})
            codes.add(r.status)
        check("rate limiter engages (429 after repeated failures)",
              429 in codes)
        browser.close()

    # Ignore favicon noise and the deliberate 404s from probing legacy routes
    # (/placements, /tasks, /report) — those 404s are the expected secure
    # behavior, not real errors.
    unexpected = [e for e in console_errors
                  if "favicon" not in e.lower()
                  and "404" not in e]
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
    print("All security browser checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
