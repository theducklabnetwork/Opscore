"""Authenticated application shell: sidebar, header, responsive layout."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab.auth_pages import AuthState
from ducklab.ops_state import OpsState

NAV = [
    ("Overview", [("Dashboard", "/ops", "layout-dashboard", True)]),
    ("Store Operations", [
        ("Monthly Report", "/ops/report", "file-chart-column", True),
        ("Daily Operations", "/ops/daily", "calendar-check", True),
        ("Sales Performance", "/ops/sales", "trending-up", True),
        ("Expenses Management", "/ops/expenses", "receipt", True),
        ("Manpower Efficiency", "/ops/manpower", "users", True),
        ("Stock Management", "/ops/stock", "boxes", True),
    ]),
    ("Management", [
        ("Action Tracker", "", "list-checks", False),
        ("Store Management", "/ops/stores", "store", True),
    ]),
    ("System", [("Settings", "", "settings", False)]),
]


def _nav_item(label: str, href: str, icon: str, enabled: bool) -> rx.Component:
    if not enabled:
        return rx.hstack(
            rx.icon(icon, size=16, color=ds.TEXT_FAINT),
            rx.text(label, size="2", color=ds.TEXT_FAINT),
            rx.spacer(),
            rx.badge("soon", size="1", variant="outline", color_scheme="gray"),
            width="100%", spacing="2", padding="0.35em 0.6em",
            title="Coming soon",
        )
    return rx.link(
        rx.hstack(rx.icon(icon, size=16), rx.text(label, size="2"),
                  width="100%", spacing="2", padding="0.35em 0.6em"),
        href=href, color=ds.TEXT, width="100%", underline="none",
        border_radius="8px",
        _hover={"background": ds.SURFACE_HOVER},
    )


def _sidebar_nav() -> rx.Component:
    blocks = []
    for group, items in NAV:
        blocks.append(
            rx.vstack(
                rx.text(group.upper(), size="1", color=ds.TEXT_FAINT,
                        weight="bold", letter_spacing="0.08em"),
                *[_nav_item(l, h, i, e) for l, h, i, e in items],
                spacing="1", align="start", width="100%",
            )
        )
    return rx.vstack(*blocks, spacing="5", width="100%", align="start")


def sidebar() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.box(rx.icon("chart-no-axes-combined", size=20, color="white"),
                   background=f"linear-gradient(135deg, {rx.color('indigo', 9)},"
                              f" {rx.color('violet', 10)})",
                   border_radius="10px", padding="8px"),
            rx.vstack(rx.heading("DuckLab", size="4"),
                      rx.text("Operations", size="1", color=ds.TEXT_FAINT),
                      spacing="0", align="start"),
            spacing="2", align="center",
        ),
        _sidebar_nav(),
        spacing="6", align="start", padding="1.25em", width="230px",
        height="100vh", position="sticky", top="0", flex_shrink="0",
        border_right=f"1px solid {ds.BORDER}", background=ds.BG,
        display=["none", "none", "none", "flex"],
    )


def mobile_drawer() -> rx.Component:
    return rx.drawer.root(
        rx.drawer.trigger(
            rx.icon_button(rx.icon("menu", size=18), variant="ghost",
                           id="mobile-nav-button",
                           display=["flex", "flex", "flex", "none"]),
        ),
        rx.drawer.overlay(),
        rx.drawer.portal(
            rx.drawer.content(
                rx.vstack(
                    rx.hstack(
                        rx.heading("DuckLab", size="4"), rx.spacer(),
                        rx.drawer.close(rx.icon_button(rx.icon("x", size=16),
                                                       variant="ghost")),
                        width="100%", align="center",
                    ),
                    _sidebar_nav(),
                    spacing="5", align="start", width="100%",
                ),
                width="280px", background=ds.BG, padding="1.25em",
            ),
        ),
        direction="left",
    )


def store_selector(on_store=None) -> rx.Component:
    return rx.cond(
        OpsState.has_stores,
        rx.select(
            OpsState.store_options,
            value=OpsState.selected_store_label,
            on_change=on_store if on_store is not None
            else OpsState.select_store,
            size="2", id="store-selector",
            width=["100%", "100%", "240px"],
        ),
        rx.text("No stores assigned", size="2", color=ds.TEXT_FAINT),
    )


def month_selector(on_month=None) -> rx.Component:
    return rx.select(
        OpsState.month_options,
        value=OpsState.selected_month_label,
        on_change=on_month if on_month is not None
        else OpsState.select_month,
        size="2", id="month-selector",
        width=["100%", "100%", "200px"],
    )


def header(title: str, on_store=None, on_month=None) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            mobile_drawer(),
            rx.heading(title, size="6", weight="bold"),
            rx.spacer(),
            rx.box(  # wrapper: click bubbles to sync theme into state
                rx.color_mode.button(variant="ghost", id="theme-toggle"),
                on_click=AuthState.set_is_dark(
                    rx.color_mode_cond(True, False)),
            ),
            rx.menu.root(
                rx.menu.trigger(
                    rx.icon_button(rx.icon("circle-user-round", size=22),
                                   variant="ghost", id="account-menu"),
                ),
                rx.menu.content(
                    rx.menu.item(AuthState.display_name, disabled=True),
                    rx.menu.separator(),
                    rx.menu.item("Sign Out", on_click=AuthState.sign_out,
                                 color="red", id="menu-sign-out"),
                ),
            ),
            width="100%", align="center", spacing="2",
        ),
        rx.hstack(
            store_selector(on_store=on_store),
            month_selector(on_month=on_month),
            spacing="3", width="100%",
            wrap="wrap",
        ),
        spacing="3", width="100%",
        padding_bottom="1em",
        border_bottom=f"1px solid {ds.BORDER}",
    )


def shell(title: str, content: rx.Component,
          on_store=None, on_month=None) -> rx.Component:
    return rx.hstack(
        sidebar(),
        rx.box(
            rx.vstack(
                header(title, on_store=on_store, on_month=on_month),
                rx.box(content, padding_top="1.5em", width="100%"),
                width="100%", max_width="1200px", margin="0 auto",
                padding=["1em", "1.5em", "2em"],
            ),
            width="100%", min_height="100vh", background=ds.BG,
        ),
        spacing="0", align="start", width="100%",
    )
