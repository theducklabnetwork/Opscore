"""Opscore store operations application."""

import reflex as rx


app = rx.App(
    head_components=[
        rx.el.link(rel="icon", type="image/png", sizes="48x48",
                   href=rx.asset("favicon-48.png")),
        rx.el.link(rel="icon", type="image/png", sizes="96x96",
                   href=rx.asset("favicon-96.png")),
        rx.el.link(rel="apple-touch-icon", sizes="176x176",
                   href=rx.asset("apple-touch-icon.png")),
        rx.el.link(rel="icon", type="image/png", sizes="192x192",
                   href=rx.asset("icon-192.png")),
        rx.el.link(rel="icon", type="image/png", sizes="512x512",
                   href=rx.asset("icon-512.png")),
        # Guided tour (driver.js 1.3.1, bundled locally): tour.js loads
        # driver.js on demand from /driver.js.
        rx.el.script(src="/tour.js", defer=True),
    ],
    stylesheets=["/drawer_fixes.css", "/driver.css", "/tour.css",
                 "/schedule_print.css"],
    # page language: screen readers, and CSS hyphenation of long words in
    # narrow phone table columns
    html_lang="en",
)

# --- Opscore Operations: authentication ---
# Secure session transport: HttpOnly cookie endpoint + WebSocket origin
# checking, mounted over the Reflex ASGI app.
from ducklab import session_transport  # noqa: E402

app.api_transformer = session_transport.asgi_transformer

from ducklab import auth_pages  # noqa: E402
from ducklab import ops_pages  # noqa: E402
from ducklab.ops_daily_pages import daily_page  # noqa: E402
from ducklab.ops_daily_state import DailyState  # noqa: E402
from ducklab.ops_actions_pages import actions_page  # noqa: E402
from ducklab.ops_actions_state import ActionsState  # noqa: E402
from ducklab.ops_expenses_pages import expenses_page  # noqa: E402
from ducklab.ops_expenses_state import ExpensesState  # noqa: E402
from ducklab.ops_manpower_pages import manpower_page  # noqa: E402
from ducklab.ops_manpower_state import ManpowerState  # noqa: E402
from ducklab.ops_state import OpsState  # noqa: E402
from ducklab.ops_shared_state import SharedOpsState  # noqa: E402
from ducklab.ops_overview_state import OverviewState  # noqa: E402
from ducklab.ops_stock_pages import stock_page  # noqa: E402
from ducklab.ops_stock_state import StockState  # noqa: E402
from ducklab.ops_stores_pages import stores_page  # noqa: E402
from ducklab.ops_stores_state import StoresState  # noqa: E402
from ducklab.ops_sales_pages import sales_page  # noqa: E402
from ducklab.ops_sales_state import SalesState  # noqa: E402
from ducklab.ops_settings_pages import SettingsState, settings_page  # noqa: E402
from ducklab.reference_bootstrap import load_bundled_reference_data  # noqa: E402
from ducklab.session_transport import DB_PATH  # noqa: E402

app.register_lifespan_task(load_bundled_reference_data, db_path=DB_PATH)

app.add_page(auth_pages.login_page, route="/login", title="Sign In — Opscore",
             on_load=auth_pages.AuthState.check_bootstrap)
app.add_page(
    auth_pages.root_page,
    route="/",
    title="Opscore Operations",
    on_load=auth_pages.AuthState.root_redirect,
)
app.add_page(
    ops_pages.dashboard_page,
    route="/ops",
    title="Dashboard — Opscore Operations",
    on_load=[OverviewState.load_overview,
             OverviewState.maybe_start_tour,
             SharedOpsState.maybe_start_page_tour("dashboard")],
)
app.add_page(
    ops_pages.report_page,
    route="/ops/report",
    title="Monthly Report — Opscore Operations",
    on_load=[OpsState.load_ops,
             SharedOpsState.maybe_start_page_tour("report")],
)
app.add_page(
    daily_page,
    route="/ops/daily",
    title="Daily Operations — Opscore",
    on_load=[DailyState.load_daily,
             SharedOpsState.maybe_start_page_tour("daily")],
)
app.add_page(
    expenses_page,
    route="/ops/expenses",
    title="Expenses Management — Opscore",
    on_load=[ExpensesState.load_expenses,
             SharedOpsState.maybe_start_page_tour("expenses")],
)
app.add_page(
    manpower_page,
    route="/ops/manpower",
    title="Manpower Efficiency — Opscore",
    on_load=[ManpowerState.load_manpower,
             SharedOpsState.maybe_start_page_tour("manpower")],
)
app.add_page(
    stock_page,
    route="/ops/stock",
    title="Stock Management — Opscore",
    on_load=[StockState.load_stock,
             SharedOpsState.maybe_start_page_tour("stock")],
)
app.add_page(
    stores_page,
    route="/ops/stores",
    title="Store Management — Opscore",
    on_load=[StoresState.load_stores,
             SharedOpsState.maybe_start_page_tour("stores")],
)
app.add_page(
    sales_page,
    route="/ops/sales",
    title="Sales Performance — Opscore",
    on_load=[SalesState.load_sales,
             SharedOpsState.maybe_start_page_tour("sales")],
)
app.add_page(
    actions_page,
    route="/ops/actions",
    title="Action Tracker — Opscore",
    on_load=[ActionsState.load_actions,
             SharedOpsState.maybe_start_page_tour("actions")],
)
app.add_page(
    settings_page,
    route="/ops/settings",
    title="Settings — Opscore",
    on_load=[SettingsState.load_settings,
             SharedOpsState.maybe_start_page_tour("settings")],
)
