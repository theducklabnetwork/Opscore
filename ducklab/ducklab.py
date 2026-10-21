"""Placement tracker dashboard for the support team."""

import dataclasses

import reflex as rx

from ducklab import db

db.init_db()



@dataclasses.dataclass
class Placement:
    id: int = 0
    store_name: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    status: str = ""
    notes: str = ""


@dataclasses.dataclass
class Task:
    id: int = 0
    placement_id: int = 0
    title: str = ""
    status: str = ""
    due_date: str = ""
    store_name: str = ""


class State(rx.State):
    """Dashboard state."""

    report_month: str = ""

    @rx.var
    def active_placements(self) -> int:
        return db.get_active_placement_count()

    @rx.var
    def open_tasks(self) -> int:
        return db.get_open_task_count()

    @rx.var
    def open_issues(self) -> int:
        return db.get_open_issue_count()

    @rx.var
    def task_status_data(self) -> list[dict]:
        return [
            {"name": r["status"].replace("_", " ").title(), "value": r["count"]}
            for r in db.get_task_status_counts()
        ]

    @rx.var
    def upcoming_tasks(self) -> list[Task]:
        return [Task(**t) for t in db.get_upcoming_tasks()]

    @rx.var
    def placements(self) -> list[Placement]:
        return [Placement(**p) for p in db.get_placements()]

    @rx.var
    def tasks(self) -> list[Task]:
        return [Task(**t) for t in db.get_tasks()]

    @rx.var
    def report_months(self) -> list[str]:
        return db.get_report_months()

    @rx.var
    def report_summary(self) -> str:
        month = self.report_month or (db.get_report_months() or [""])[0]
        return db.get_report(month) if month else ""

    @rx.event
    def set_report_month(self, value: str) -> None:
        self.report_month = value

    @rx.event
    def copy_report(self) -> None:
        yield rx.set_clipboard(self.report_summary)
        yield rx.toast("Report copied to clipboard")


# ---------- Layout ----------

def nav_link(label: str, href: str, icon: str) -> rx.Component:
    return rx.link(
        rx.hstack(rx.icon(icon, size=18), rx.text(label), spacing="2"),
        href=href,
        color=rx.color("slate", 12),
        width="100%",
    )


def sidebar() -> rx.Component:
    return rx.vstack(
        rx.heading("Placement Tracker", size="5"),
        nav_link("Dashboard", "/", "layout-dashboard"),
        nav_link("Placements", "/placements", "store"),
        nav_link("Tasks", "/tasks", "list-checks"),
        nav_link("Monthly Report", "/report", "file-text"),
        spacing="4",
        align="start",
        padding="1.5em",
        width="220px",
        height="100vh",
        display=["none", "none", "flex"],
        border_right=f"1px solid {rx.color('slate', 6)}",
        position="sticky",
        top="0",
    )


def mobile_nav() -> rx.Component:
    return rx.hstack(
        rx.link("Dashboard", href="/"),
        rx.link("Placements", href="/placements"),
        rx.link("Tasks", href="/tasks"),
        rx.link("Report", href="/report"),
        spacing="4",
        padding="1em",
        display=["flex", "flex", "none"],
        wrap="wrap",
    )


def layout(content: rx.Component) -> rx.Component:
    return rx.hstack(
        sidebar(),
        rx.box(
            rx.hstack(
                rx.spacer(),
                rx.color_mode.button(),
                padding="1em",
                width="100%",
            ),
            mobile_nav(),
            rx.box(content, padding=["1em", "1.5em", "2em"], width="100%"),
            width="100%",
        ),
        spacing="0",
        align="start",
        width="100%",
    )


# ---------- Pages ----------

def kpi_card(title: str, value: rx.Var, icon: str) -> rx.Component:
    return rx.card(
        rx.hstack(
            rx.icon(icon, size=28, color=rx.color("indigo", 9)),
            rx.vstack(
                rx.text(title, size="2", color=rx.color("slate", 11)),
                rx.heading(value, size="7"),
                spacing="1",
                align="start",
            ),
            spacing="3",
            align="center",
        ),
        width="100%",
    )


def status_badge(status: str) -> rx.Component:
    return rx.badge(
        status.replace("_", " "),
        color_scheme=rx.match(
            status,
            ("todo", "slate"),
            ("in_progress", "amber"),
            ("done", "green"),
            "slate",
        ),
        variant="soft",
    )


def dashboard() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading("Dashboard", size="7"),
            rx.grid(
                kpi_card("Active Placements", State.active_placements, "store"),
                kpi_card("Open Tasks", State.open_tasks, "list-checks"),
                kpi_card("Open Issues", State.open_issues, "triangle-alert"),
                columns=rx.breakpoints(initial="1", sm="1", lg="3"),
                spacing="4",
                width="100%",
            ),
            rx.grid(
                rx.card(
                    rx.vstack(
                        rx.heading("Tasks by Status", size="4"),
                        rx.recharts.pie_chart(
                            rx.recharts.pie(
                                data=State.task_status_data,
                                data_key="value",
                                name_key="name",
                                label=True,
                            ),
                            rx.recharts.legend(),
                            width="100%",
                            height=280,
                        ),
                        spacing="3",
                    ),
                    width="100%",
                ),
                rx.card(
                    rx.vstack(
                        rx.heading("Upcoming Tasks", size="4"),
                        rx.foreach(
                            State.upcoming_tasks,
                            lambda t: rx.hstack(
                                rx.vstack(
                                    rx.text(t.title, weight="medium"),
                                    rx.text(t.store_name, size="2", color=rx.color("slate", 11)),
                                    spacing="0",
                                    align="start",
                                ),
                                rx.spacer(),
                                rx.vstack(
                                    rx.text(t.due_date, size="2"),
                                    status_badge(t.status),
                                    spacing="1",
                                    align="end",
                                ),
                                width="100%",
                                padding_y="0.5em",
                            ),
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    width="100%",
                ),
                columns=rx.breakpoints(initial="1", lg="2"),
                spacing="4",
                width="100%",
            ),
            spacing="5",
            width="100%",
        )
    )


def placements_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading("Placements", size="7"),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Store"),
                        rx.table.column_header_cell("Location"),
                        rx.table.column_header_cell("Start"),
                        rx.table.column_header_cell("End"),
                        rx.table.column_header_cell("Status"),
                    )
                ),
                rx.table.body(
                    rx.foreach(
                        State.placements,
                        lambda p: rx.table.row(
                            rx.table.cell(p.store_name),
                            rx.table.cell(p.location),
                            rx.table.cell(p.start_date),
                            rx.table.cell(p.end_date),
                            rx.table.cell(
                                rx.badge(
                                    p.status,
                                    color_scheme=rx.cond(p.status == "active", "green", "slate"),
                                    variant="soft",
                                )
                            ),
                        ),
                    )
                ),
                width="100%",
            ),
            spacing="5",
            width="100%",
        )
    )


def tasks_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading("Tasks", size="7"),
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell("Task"),
                        rx.table.column_header_cell("Store"),
                        rx.table.column_header_cell("Due"),
                        rx.table.column_header_cell("Status"),
                    )
                ),
                rx.table.body(
                    rx.foreach(
                        State.tasks,
                        lambda t: rx.table.row(
                            rx.table.cell(t.title),
                            rx.table.cell(t.store_name),
                            rx.table.cell(t.due_date),
                            rx.table.cell(status_badge(t.status)),
                        ),
                    )
                ),
                width="100%",
            ),
            spacing="5",
            width="100%",
        )
    )


def report_page() -> rx.Component:
    return layout(
        rx.vstack(
            rx.heading("Monthly Report", size="7"),
            rx.hstack(
                rx.select(
                    State.report_months,
                    placeholder="Select month",
                    value=State.report_month,
                    on_change=State.set_report_month,
                ),
                rx.button(
                    rx.icon("copy", size=16),
                    "Copy",
                    on_click=State.copy_report,
                    variant="soft",
                ),
                spacing="3",
            ),
            rx.card(
                rx.text(State.report_summary, white_space="pre-wrap"),
                width="100%",
                min_height="200px",
            ),
            spacing="5",
            width="100%",
        )
    )


app = rx.App(
    stylesheets=["/drawer_fixes.css"],
)

# --- DuckLab Operations: authentication ---
# Legacy Placement Tracker prototype routes are not registered on the
# deployed app: they render prototype business data with no authentication.
# The prototype code is preserved in this module for local reference and is
# only mounted when DUCKLAB_EXPOSE_PROTOTYPE=1 (never in production).
import os as _os  # noqa: E402

if _os.environ.get("DUCKLAB_EXPOSE_PROTOTYPE", "") == "1":
    # The prototype's index route is NOT mounted: the ops root page owns "/"
    # (redirects to login/dashboard). Prototype pages live under /prototype/*.
    app.add_page(dashboard, route="/prototype", title="Prototype Dashboard")
    app.add_page(placements_page, route="/placements", title="Placements")
    app.add_page(tasks_page, route="/tasks", title="Tasks")
    app.add_page(report_page, route="/report", title="Monthly Report")

# Secure session transport: HttpOnly cookie endpoint + WebSocket origin
# checking, mounted over the Reflex ASGI app.
from ducklab import session_transport  # noqa: E402

app.api_transformer = session_transport.asgi_transformer

from ducklab import auth_pages  # noqa: E402
from ducklab import ops_pages  # noqa: E402
from ducklab.ops_daily_pages import daily_page  # noqa: E402
from ducklab.ops_daily_state import DailyState  # noqa: E402
from ducklab.ops_expenses_pages import expenses_page  # noqa: E402
from ducklab.ops_expenses_state import ExpensesState  # noqa: E402
from ducklab.ops_manpower_pages import manpower_page  # noqa: E402
from ducklab.ops_manpower_state import ManpowerState  # noqa: E402
from ducklab.ops_state import OpsState  # noqa: E402
from ducklab.ops_stock_pages import stock_page  # noqa: E402
from ducklab.ops_stock_state import StockState  # noqa: E402
from ducklab.ops_stores_pages import stores_page  # noqa: E402
from ducklab.ops_stores_state import StoresState  # noqa: E402
from ducklab.ops_sales_pages import sales_page  # noqa: E402
from ducklab.ops_sales_state import SalesState  # noqa: E402

app.add_page(auth_pages.login_page, route="/login", title="Sign In — DuckLab")
app.add_page(
    auth_pages.root_page,
    route="/",
    title="DuckLab Operations",
    on_load=auth_pages.AuthState.root_redirect,
)
app.add_page(
    ops_pages.dashboard_page,
    route="/ops",
    title="Dashboard — DuckLab Operations",
    on_load=OpsState.load_ops,
)
app.add_page(
    ops_pages.report_page,
    route="/ops/report",
    title="Monthly Report — DuckLab Operations",
    on_load=OpsState.load_ops,
)
app.add_page(
    daily_page,
    route="/ops/daily",
    title="Daily Operations — DuckLab",
    on_load=DailyState.load_daily,
)
app.add_page(
    expenses_page,
    route="/ops/expenses",
    title="Expenses Management — DuckLab",
    on_load=ExpensesState.load_expenses,
)
app.add_page(
    manpower_page,
    route="/ops/manpower",
    title="Manpower Efficiency — DuckLab",
    on_load=ManpowerState.load_manpower,
)
app.add_page(
    stock_page,
    route="/ops/stock",
    title="Stock Management — DuckLab",
    on_load=StockState.load_stock,
)
app.add_page(
    stores_page,
    route="/ops/stores",
    title="Store Management — DuckLab",
    on_load=StoresState.load_stores,
)
app.add_page(
    sales_page,
    route="/ops/sales",
    title="Sales Performance — DuckLab",
    on_load=SalesState.load_sales,
)
