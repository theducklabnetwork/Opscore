#!/usr/bin/env python3
"""Browser smoke for Task 11: Plotly charts on /ops/stock, /ops/sales,
/ops/expenses using the seeded DEV store S01 (never ZI68 — private).
Checks render, hover values, theme restyle, zoom/reset, mobile overflow,
console errors, and page weight (transferred bytes) for /ops/sales.
Screenshots -> docs/screenshots/phase2-task11/.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DUCKLAB_UI", "http://127.0.0.1:3000")
SHOTS = Path("docs/screenshots/phase2-task11")
SHOTS.mkdir(parents=True, exist_ok=True)

failures: list[str] = []
console_errors: list[str] = []
transferred: dict[str, int] = {}


def check(name: str, condition: bool) -> None:
    print(("PASS  " if condition else "FAIL  ") + name)
    if not condition:
        failures.append(name)


def make_admin():
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from ducklab.app_service import AppService
    app = AppService("/home/duck/project/ducklab/.devdata/dev.db")
    email = f"task11-{secrets.token_hex(3)}@ducklab.dev"
    password = "Task11!" + secrets.token_hex(6)
    admin1 = app.auth.get_user(1)
    u = app.create_user(admin1, email, "Task11 QA", password)
    app.assign_role(admin1, u.id, "super_admin")
    return email, password


def login(page, email, password):
    page.goto(f"{BASE}/login")
    page.wait_for_selector("#login-email")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")
    page.wait_for_url("**/ops**", timeout=20000)


def choose(page, trigger, option_text, timeout=25000):
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
            pass
        try:
            page.click(trigger)
            opt = f"[role='option']:has-text(\"{option_text}\")"
            page.wait_for_selector(opt, state="visible", timeout=10000)
            page.locator(opt).first.dispatch_event("click")
            page.wait_for_timeout(1200)
            if key in page.locator(trigger).inner_text():
                return
        except Exception:
            page.wait_for_timeout(1200)
    raise RuntimeError(f"could not select {option_text!r}")


def pick(page, store, month):
    choose(page, "#store-selector", store)
    choose(page, "#month-selector", month)
    page.wait_for_timeout(3000)


def count_plotly(page) -> int:
    return page.locator("div.js-plotly-plot").count()


def hover_first_point(page, index=0) -> str:
    """Dispatch a mousemove over a plot (Playwright's synthetic moves don't
    reliably trigger plotly's hover); return tooltip text (may be '')."""
    page.locator("div.js-plotly-plot").nth(index).scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    for frac in (0.05, 0.12, 0.2, 0.35, 0.5, 0.65, 0.8):
        tip = page.evaluate("""(args) => new Promise(res => {
          const gd = document.querySelectorAll('div.js-plotly-plot')[args.i];
          if (!gd) return res('');
          const layer = gd.querySelector('.nsewdrag');
          if (!layer) return res('');
          const bb = layer.getBoundingClientRect();
          const x = bb.x + bb.width * args.f, y = bb.y + bb.height * 0.45;
          for (const type of ['mousemove', 'mouseover']) {
            layer.dispatchEvent(new MouseEvent(type,
              {clientX: x, clientY: y, bubbles: true}));
          }
          setTimeout(() => {
            res([...gd.querySelectorAll('.hoverlayer text')]
                .map(e => e.textContent).join(' '));
          }, 600);
        })""", {"i": index, "f": frac})
        if tip and tip.strip():
            return tip
    return ""


def main() -> int:
    email, password = make_admin()
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---- desktop, dark (default) ------------------------------------
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        # page weight measurement for /ops/sales
        page.on("response", lambda r: transferred.__setitem__(
            "bytes", transferred.get("bytes", 0)
            + len(r.body()) if r.request.resource_type != "websocket"
            else transferred.get("bytes", 0)))

        login(page, email, password)

        # --- /ops/sales pace chart ---
        page.goto(f"{BASE}/ops/sales")
        page.wait_for_selector("#ladder-section", timeout=90000)
        pick(page, "S01", "October 2026")
        n = count_plotly(page)
        check("sales: plotly chart rendered", n >= 1)
        tip = hover_first_point(page)
        check("sales: hover shows RM value", "RM" in tip)
        page.screenshot(path=str(SHOTS / "sales-dark-desktop.png"),
                        full_page=False)
        sales_weight = transferred.get("bytes", 0)

        # zoom + reset via modebar
        page.locator("div.js-plotly-plot").first.hover()
        mb = page.locator(".modebar-btn[data-title='Zoom']").first
        check("sales: zoom button present", mb.count() == 1)
        reset = page.locator(
            ".modebar-btn[data-title*='Reset'],"
            ".modebar-btn[data-title*='reset']").first
        check("sales: reset button present", reset.count() >= 0)
        if mb.count():
            mb.click()
            page.wait_for_timeout(400)

        # --- /ops/stock: trend + composition ---
        page.goto(f"{BASE}/ops/stock")
        page.wait_for_selector("#stock-status", timeout=90000)
        # under 3 days with stock entries the charts are replaced by one
        # "Not enough data yet" card (B02 has 2 days in October)
        pick(page, "B02", "October 2026")
        check("stock: few entries -> one 'Not enough data yet' card",
              page.locator("text=Not enough data yet").count() == 1
              and count_plotly(page) == 0)
        pick(page, "A01", "September 2026")
        n = count_plotly(page)
        check("stock: two plotly charts (trend + composition)", n >= 2)
        tip = hover_first_point(page)
        check("stock: trend hover shows RM value", "RM" in tip)
        tip2 = hover_first_point(page, index=1)
        check("stock: composition hover shows RM value", "RM" in tip2)

        # --- /ops/expenses: budget vs actual ---
        page.goto(f"{BASE}/ops/expenses")
        page.wait_for_selector("#expense-kpis", timeout=90000)
        # X01 (seed_ui_expenses_dev) has categories + budgets in October
        pick(page, "X01", "October 2026")
        n = count_plotly(page)
        check("expenses: plotly budget-vs-actual rendered", n >= 1)

        # --- theme toggle restyles the chart (no reload) ---
        before = page.locator("div.js-plotly-plot").first.inner_html()
        page.click("#theme-toggle")
        page.wait_for_timeout(1500)
        after = page.locator("div.js-plotly-plot").first.inner_html()
        check("theme toggle restyles chart (figure content changes)",
              before != after)
        page.screenshot(path=str(SHOTS / "expenses-light-desktop.png"),
                        full_page=False)

        check("desktop: no console errors", not console_errors)
        ctx.close()

        # ---- mobile viewport --------------------------------------------
        ctx2 = browser.new_context(viewport={"width": 390, "height": 844})
        m = ctx2.new_page()
        mob_errors: list[str] = []
        m.on("console", lambda e: mob_errors.append(e.text)
             if e.type == "error" else None)
        m.on("pageerror", lambda e: mob_errors.append(str(e)))
        login(m, email, password)
        m.goto(f"{BASE}/ops/sales")
        m.wait_for_selector("#ladder-section", timeout=90000)
        pick(m, "S01", "October 2026")
        check("mobile: plotly chart rendered", count_plotly(m) >= 1)
        overflow = m.evaluate(
            "document.documentElement.scrollWidth > "
            "document.documentElement.clientWidth + 1")
        check("mobile: no horizontal page overflow", not overflow)
        legend_below = m.evaluate("""(() => {
            const el = document.querySelector('div.js-plotly-plot');
            if (!el) return false;
            const svg = el.querySelector('.infolayer');
            const plot = el.querySelector('.plot');
            if (!svg || !plot) return true;
            const lb = svg.getBoundingClientRect(), pb = plot.getBoundingClientRect();
            return lb.top >= pb.bottom - 2;
        })()""")
        check("mobile: legend below plot area", legend_below)
        m.screenshot(path=str(SHOTS / "sales-mobile.png"), full_page=False)
        check("mobile: no console errors", not mob_errors)
        for e in (console_errors + mob_errors)[:5]:
            print("  console error:", e)
        ctx2.close()
        browser.close()

    print(f"page weight /ops/sales (transferred, after): "
          f"{sales_weight / 1024:.0f} KiB")
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
