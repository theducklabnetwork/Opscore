"""Authenticated application shell: sidebar, header, responsive layout."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ui
from ducklab.auth_pages import AuthState
from ducklab.ops_shared_state import SharedOpsState

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
        ("Action Tracker", "/ops/actions", "list-checks", True),
        ("Store Management", "/ops/stores", "store", True),
    ]),
    ("System", [("Settings", "/ops/settings", "settings", True)]),
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
        id="nav-" + href.strip("/").replace("/", "-"),
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


def _wordmark() -> rx.Component:
    return rx.image(
        src=rx.asset("opscore-wordmark.svg"),
        alt="Opscore",
        width="100%",
        height="auto",
        display="block",
    )


def sidebar() -> rx.Component:
    # The outer column stretches to the full page height (background and
    # border never stop at the first screen); the nav inside stays sticky.
    return rx.box(
        rx.vstack(
            rx.link(
                _wordmark(),
                href="/ops",
                aria_label="Opscore Dashboard",
                width="100%",
            ),
            _sidebar_nav(),
            spacing="6", align="start", padding="1.25em", width="100%",
            height="100vh", position="sticky", top="0", overflow_y="auto",
        ),
        width="230px", flex_shrink="0", align_self="stretch",
        border_right=f"1px solid {ds.BORDER}", background=ds.BG,
        display=["none", "none", "none", "block"],
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
                        rx.link(
                            _wordmark(),
                            href="/ops",
                            aria_label="Opscore Dashboard",
                            width="75%",
                        ),
                        rx.spacer(),
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


def store_selector(on_store=None, include_all: bool = False) -> rx.Component:
    options = SharedOpsState.store_options
    if include_all:
        options = SharedOpsState.dashboard_store_options
    return rx.cond(
        SharedOpsState.has_stores,
        rx.select(
            options,
            value=SharedOpsState.selected_store_label,
            on_change=on_store if on_store is not None
            else SharedOpsState.select_store,
            size="2", id="store-selector",
            width=["100%", "100%", "240px"],
        ),
        rx.text("No stores assigned", size="2", color=ds.TEXT_FAINT),
    )


def month_selector(on_month=None) -> rx.Component:
    return rx.select(
        SharedOpsState.month_options,
        value=SharedOpsState.selected_month_label,
        on_change=on_month if on_month is not None
        else SharedOpsState.select_month,
        size="2", id="month-selector",
        width=["100%", "100%", "200px"],
    )


def header(title: str, on_store=None, on_month=None,
           description=None, actions=None, tour_key=None,
           include_all_stores: bool = False) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            mobile_drawer(),
            ui.page_header(title, description),
            rx.spacer(),
            *([rx.icon_button(
                rx.icon("circle-help", size=20),
                variant="ghost", aria_label=f"Replay {title} tour",
                title=f"Replay {title} tour", id=f"tour-replay-{tour_key}",
                on_click=SharedOpsState.replay_page_tour(tour_key),
            )] if tour_key is not None else []),
            rx.box(  # wrapper: click bubbles to sync theme into state
                rx.color_mode.button(variant="ghost", id="theme-toggle"),
                on_click=AuthState.toggle_is_dark,
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
            width="100%", align="start", spacing="2",
        ),
        rx.hstack(
            store_selector(on_store=on_store, include_all=include_all_stores),
            month_selector(on_month=on_month),
            *([rx.spacer(), actions] if actions is not None else []),
            spacing="3", width="100%",
            wrap="wrap", align="center",
        ),
        spacing="4", width="100%",
        padding_bottom="1.25em",
        border_bottom=f"1px solid {ui.CARD_BORDER}",
    )


def shell(title: str, content: rx.Component,
          on_store=None, on_month=None, description=None,
          actions=None, tour_key=None, include_all_stores: bool = False) -> rx.Component:
    return rx.hstack(
        sidebar(),
        rx.box(
            rx.vstack(
                header(title, on_store=on_store, on_month=on_month,
                       description=description, actions=actions,
                       tour_key=tour_key, include_all_stores=include_all_stores),
                # Hidden trigger: assets/tour.js marks the guided tour done
                # by dispatching the SharedOpsState.tour_finished event (primary
                # path) or clicking this button (fallback).
                rx.button("tour-done", on_click=SharedOpsState.tour_finished(),
                          id="tour-done-trigger",
                          display="none", aria_hidden="true"),
                *([rx.button(
                    "page-tour-done",
                    on_click=SharedOpsState.page_tour_finished(tour_key),
                    id=f"tour-done-trigger-{tour_key}",
                    display="none", aria_hidden="true",
                )] if tour_key is not None else []),
                rx.cond(
                    ~SharedOpsState.selected_store_active,
                    rx.callout(
                        "This store is deactivated — read-only. "
                        "Reactivate it on Store Management to make changes.",
                        icon="lock", color_scheme="gray", variant="surface",
                        width="100%", margin_top="0.75em",
                        id="store-readonly-banner",
                    ),
                ),
                rx.box(content, padding_top="1.75em", width="100%",
                       min_width="0"),
                width="100%", max_width="1200px", margin="0 auto",
                padding=["1em", "1.5em", "2em"],
            ),
            width="100%", min_width="0", min_height="100vh",
            background=ds.BG,
        ),
        spacing="0", align="start", width="100%", min_height="100vh",
        background=ds.BG,
    )
