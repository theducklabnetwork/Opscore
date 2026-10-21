"""DuckLab design system: reusable tokens and primitives.

Centralizes color/spacing decisions so components don't scatter hardcoded
values. Built on Radix Themes semantic color scales (slate/indigo/violet/
green/amber/red), which automatically adapt to dark and light mode.
"""

from __future__ import annotations

import reflex as rx

# --- Tokens (semantic Radix scales: dark/light adaptive) -------------------
BG = rx.color("slate", 1)            # app background (deep navy-charcoal dark)
SURFACE = rx.color("slate", 2)       # card surface
SURFACE_HOVER = rx.color("slate", 3)
BORDER = rx.color("slate", 6)
TEXT = rx.color("slate", 12)
TEXT_MUTED = rx.color("slate", 11)
TEXT_FAINT = rx.color("slate", 10)
ACCENT = rx.color("indigo", 9)
ACCENT_TEXT = rx.color("indigo", 11)
SUCCESS = rx.color("green", 9)
WARNING = rx.color("amber", 9)
ERROR = rx.color("red", 9)

STATUS_COLOR = {
    "ok": "green", "green": "green", "within budget": "green",
    "on target": "green", "achieved": "green", "completed": "green",
    "watch": "amber", "amber": "amber", "warning": "amber",
    "in_progress": "amber", "in progress": "amber",
    "over budget": "red", "red": "red", "missed": "red", "high": "red",
    "overdue": "red", "blocked": "red",
    "no budget": "gray", "awaiting payroll": "gray",
    "no target": "gray", "open": "indigo", "todo": "gray",
}


def status_badge(text: str, **kw) -> rx.Component:
    scheme = STATUS_COLOR.get(text.lower(), "gray")
    return rx.badge(text, color_scheme=scheme, variant="soft", **kw)


# --- Formatting helpers (pure Python; never float money) -------------------
def fmt_rm(cents, dash="—") -> str:
    """Integer cents -> 'RM 350,819.00'. None stays a dash (missing != 0)."""
    if cents is None:
        return dash
    return f"RM {cents / 100:,.2f}"


def fmt_rm0(cents, dash="—") -> str:
    """Whole-ringgit display for large figures: 'RM 350,819'."""
    if cents is None:
        return dash
    return f"RM {round(cents / 100):,}"


def fmt_pct(ratio, dash="—") -> str:
    """Decimal ratio -> '68.2%'. None stays a dash."""
    if ratio is None:
        return dash
    return f"{float(ratio) * 100:,.1f}%"


def fmt_num(value, dash="—") -> str:
    if value is None:
        return dash
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}"


# --- Primitives ------------------------------------------------------------
def kpi_card(label: str, value, *, icon: str = "", sub=None,
             accent: str = "indigo") -> rx.Component:
    content = [
        rx.text(label, size="2", color=TEXT_MUTED, weight="medium"),
        rx.heading(value, size="6", weight="bold", wrap="nowrap",
                   style={"overflow": "hidden", "textOverflow": "ellipsis",
                          "maxWidth": "100%"}),
    ]
    if sub is not None:
        content.append(rx.text(sub, size="1", color=TEXT_FAINT))
    body = rx.vstack(*content, spacing="1", align="start", min_width="0")
    if icon:
        body = rx.hstack(
            rx.box(rx.icon(icon, size=18, color="white"),
                   background=rx.color(accent, 9), border_radius="10px",
                   padding="8px", flex_shrink="0"),
            body, spacing="3", align="center")
    return rx.card(body, width="100%", variant="surface")


def section_heading(title: str, icon: str, extra=None) -> rx.Component:
    row = [rx.icon(icon, size=18, color=ACCENT_TEXT),
           rx.heading(title, size="5", weight="bold"), rx.spacer()]
    if extra is not None:
        row.append(extra)
    return rx.hstack(*row, width="100%", align="center")


def empty_state(title: str, detail: str, icon: str = "inbox") -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.icon(icon, size=32, color=TEXT_FAINT),
            rx.heading(title, size="4", color=TEXT_MUTED),
            rx.text(detail, size="2", color=TEXT_FAINT, text_align="center"),
            spacing="2", align="center",
        ),
        padding="4em 1em", width="100%",
    )


def error_callout(message) -> rx.Component:
    return rx.callout(message, icon="triangle-alert", color_scheme="red",
                      variant="soft", width="100%", role="alert")


def loading_state(label="Loading…") -> rx.Component:
    return rx.center(rx.spinner(size="3"), rx.text(label, color=TEXT_MUTED),
                     padding="3em", width="100%", spacing="3")


# ---------------------------------------------------------------------------
# One status -> color map for every status string kpi.py can return.
# Pages must look colors up here, not define their own maps.
# ---------------------------------------------------------------------------
STATUS_COLOR: dict[str, str] = {
    # sales levels
    "Achieved": "green", "Below target": "red",
    "Awaiting data": "gray", "No target": "gray",
    # expenses (total + per-category budget status)
    "Within budget": "green", "Over budget": "red", "OVER BUDGET": "red",
    "Watch": "amber", "No budget": "gray", "No payments yet": "gray",
    # manpower
    "Within target": "green", "Failure: over target": "red",
    "Over target": "red", "Above target": "red",
    "Awaiting payroll": "gray", "Awaiting HC": "gray",
    "Normal": "green", "HIGH TURNOVER": "red",
    "On budget": "green", "Over budget (+1)": "red", "Under budget": "amber",
    # expense line validation
    "OK": "green", "Incomplete": "amber", "Unknown category": "red",
    "Missing paid date": "amber", "Check paid date": "amber",
    "Missing paid by": "amber", "Missing recorder": "amber",
    "Disputed": "red", "Unverified": "amber",
    # expense transaction status
    "Paid": "green", "Pending": "amber", "Cancelled": "gray",
    # actions
    "Open": "blue", "In progress": "amber", "Blocked": "red",
    "Done": "green",
    # growth (Summary rows 21-22)
    "Growth": "green", "Decline": "red", "No reference": "gray",
    # misc engine strings
    "Restricted": "gray",
}


def status_color(status: str, default: str = "gray") -> str:
    """The single status -> color lookup keyed by kpi.py's exact strings."""
    return STATUS_COLOR.get(status, default)


# ---------------------------------------------------------------------------
# Projection display rules (kpi.projection_state) — one helper for all pages.
# ---------------------------------------------------------------------------
def projection_style(state: str, status: str) -> tuple[str, str, str]:
    """-> (label, color, tag) for a month-end-derived status.

    actual        -> (status, semantic color, "")
    projected     -> ("Projected: <status>", neutral/amber, "")
    early_estimate-> additionally an "Early estimate" tag.
    """
    tag = "Early estimate" if state == "early_estimate" else ""
    if state == "actual":
        return status, status_color(status), ""
    if status in ("Awaiting data", "No target"):
        return status, "gray", tag
    return f"Projected: {status}", "amber", tag


EARLY_ESTIMATE_TIP = ("Based on only {n} of {m} days; projections "
                      "stabilise after a full week")
