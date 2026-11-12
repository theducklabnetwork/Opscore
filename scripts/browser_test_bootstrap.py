#!/usr/bin/env python3
"""Browser regression: first-account bootstrap must show the recovery kit.

Requires a running app at DUCKLAB_UI backed by a fresh, empty disposable
DB. Never run this against development or production data. Exits non-zero
on failure and creates no screenshots or project files.
"""

from __future__ import annotations

import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://localhost:3000")
PW = "Sup3rSecure!x"
KEY_RE = re.compile(r"DLAB(?:-[A-Z2-9]{5}){7}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{BASE}/login")
        page.wait_for_selector("#bootstrap-submit", timeout=15000)

        page.fill("#bootstrap-name", "First Admin")
        page.fill("#bootstrap-email", "first@ducklab.com")
        page.fill("#bootstrap-password", PW)
        page.fill("#bootstrap-confirm", PW)
        page.click("#bootstrap-submit")

        # Exactly one account submission, then the key panel—not another
        # Create Account form and not a premature dashboard navigation.
        page.wait_for_selector("#kit-done-bootstrap", timeout=15000)
        key_text = page.locator("code").inner_text()
        assert KEY_RE.fullmatch(key_text.strip()), key_text
        assert page.locator("#bootstrap-submit").count() == 0
        assert page.get_by_text("Save Your Recovery Key", exact=True).count() == 1
        print("PASS  bootstrap shows recovery key without repeating Create Account")

        page.click("#kit-done-bootstrap")
        page.wait_for_url("**/ops/", timeout=15000)
        print("PASS  recovery-kit Continue reaches dashboard")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
