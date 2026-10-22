"""DuckLab design system: reusable tokens and primitives.

Centralizes color/spacing decisions so components don't scatter hardcoded
values. Built on Radix Themes semantic color scales (slate/indigo/violet/
green/amber/red), which automatically adapt to dark and light mode.
"""

from __future__ import annotations

from datetime import datetime, timezone

import reflex as rx

from ducklab.kpi import KL_TZ

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
# One negative money format everywhere: the minus sign (U+2212) goes before
# "RM" — "−RM 9,898" — never "RM -9,898".
MINUS = "\u2212"


def _sign(cents) -> str:
    return MINUS if cents < 0 else ""


def fmt_rm(cents, dash="—") -> str:
    """Integer cents -> 'RM 350,819.00' / '−RM 85.00'. None stays a dash
    (missing != 0)."""
    if cents is None:
        return dash
    return f"{_sign(cents)}RM {abs(cents) / 100:,.2f}"


def fmt_rm0(cents, dash="—") -> str:
    """Whole-ringgit display for large figures: 'RM 350,819' / '−RM 9,898'."""
    if cents is None:
        return dash
    return f"{_sign(cents)}RM {round(abs(cents) / 100):,}"


def fmt_rm_compact(cents, dash="—", whole_below: bool = False) -> str:
    """Compact money for tight spaces: 'RM 23.5k', 'RM 1.2M'; below
    RM 10,000 the exact value ('RM 7,066.66'), or whole ringgit
    ('RM 7,067') with ``whole_below``. Always pair with the exact value
    (tooltip)."""
    if cents is None:
        return dash
    sign = _sign(cents)
    a = abs(cents / 100)
    if a >= 1_000_000:
        return f"{sign}RM {a / 1_000_000:.1f}M"
    if a >= 10_000:
        return f"{sign}RM {a / 1_000:.1f}k"
    if whole_below:
        return fmt_rm0(cents, dash)
    return f"{sign}RM {a:,.2f}"


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


def fmt_kl_datetime(utc_text, dash="—") -> str:
    """Stored UTC 'YYYY-MM-DD HH:MM:SS' (SQLite datetime('now'), no zone)
    -> Kuala Lumpur display '21 Oct 2026, 14:05 MYT'. Display only;
    unparseable text is shown unchanged."""
    if utc_text is None:
        return dash
    try:
        dt = datetime.strptime(str(utc_text), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(utc_text)
    kl = dt.replace(tzinfo=timezone.utc).astimezone(KL_TZ)
    return f"{kl.day} {kl:%b %Y, %H:%M} MYT"

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
    "Disputed": "red", "Unverified": "amber", "Verified": "green",
    # expense transaction status
    "Paid": "green", "Pending": "amber", "Cancelled": "gray",
    # stock-loss code treatment: a classification, not a status that needs
    # attention, so always neutral gray (the chart keeps its own colours)
    "Included": "gray", "Offset": "gray", "Info only": "gray",
    # actions
    "Open": "blue", "In progress": "amber", "Blocked": "red",
    "Done": "green",
    # headcount variance (Summary!D51/E51)
    "Vacancy": "amber", "On budget": "green",
    # stock (kpi.stock_status_label pre-status states)
    "No sales recorded": "amber",
    # manpower: part-timers worked without a configured factor
    "Factor not set": "amber",
    # day statuses (one wording on every page: see day_status below)
    "Complete": "green", "Partial": "amber", "Zero sales": "blue",
    # legacy lowercase day statuses (kept for older callers)
    "complete": "green", "partial": "amber", "unreported": "gray",
    "Not reported": "gray", "Zero": "green", "Loss": "red",
    "Net negative": "blue", "No sales": "amber",
    # action priorities (display strings)
    "Low": "gray", "Medium": "blue", "High": "amber", "Critical": "red",
    # growth (Summary rows 21-22)
    "Growth": "green", "Decline": "red", "No reference": "gray",
    # misc engine strings
    "Restricted": "gray",
}


def status_color(status: str, default: str = "gray") -> str:
    """The single status -> color lookup keyed by kpi.py's exact strings."""
    return STATUS_COLOR.get(status, default)


# ---------------------------------------------------------------------------
# Day status — the same four chips on every page (Daily, Sales, ...).
# ---------------------------------------------------------------------------
DAY_COMPLETE = "Complete"
DAY_PARTIAL = "Partial"
DAY_NOT_REPORTED = "Not reported"
DAY_ZERO_SALES = "Zero sales"
DAY_STATUSES = (DAY_COMPLETE, DAY_PARTIAL, DAY_NOT_REPORTED, DAY_ZERO_SALES)

_DAY_FIELDS = ("sales_cents", "transactions", "units", "ft_working",
               "pt_working", "new_hires_ft", "resigned_ft", "leave_annual",
               "leave_mc", "leave_other_paid", "leave_unpaid")


def day_status(rec) -> str:
    """Display status of one day's record (None = no row saved).

    Not reported: nothing entered. Zero sales: sales explicitly entered as
    0. Complete: at least three values entered (fields + stock codes).
    Partial: one or two values entered.
    """
    if rec is None:
        return DAY_NOT_REPORTED
    filled = sum(getattr(rec, f, None) is not None for f in _DAY_FIELDS) \
        + len(getattr(rec, "stock_losses", None) or {})
    if filled == 0:
        return DAY_NOT_REPORTED
    if rec.sales_cents == 0:
        return DAY_ZERO_SALES
    return DAY_COMPLETE if filled >= 3 else DAY_PARTIAL


def days_not_reported_label(n: int) -> str:
    """'29 days not reported' / '1 day not reported'."""
    return f"{n} day{'' if n == 1 else 's'} not reported"


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
    if status == "No reference":
        # a missing reference is a fact, not a projection: no prefix/tag
        return status, status_color(status), ""
    if status in ("Awaiting data", "No target"):
        return status, "gray", tag
    return f"Projected: {status}", "amber", tag


def projection_label(state: str, label: str) -> str:
    """Card label for a figure derived from the month-end projection:
    unchanged when the month is complete, else marked projected / early
    estimate (same rule as projection_style)."""
    if state == "actual":
        return label
    if state == "early_estimate":
        return f"{label} (early estimate)"
    return f"{label} (projected)"


def pt_factor_label(factor) -> str:
    """Effective-staff formula with the configured part-timer factor."""
    if factor is None:
        return "Factor not set"
    return f"FT + {float(factor):g} × PT"


EARLY_ESTIMATE_TIP = ("Based on only {n} of {m} days; projections "
                      "stabilise after a full week")
