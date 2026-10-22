"""DuckLab UI kit: the shared page components every /ops page builds on.

Style rules (one place, so pages stay consistent):
- calm, spacious layout: soft 1px card borders, no card inside a card;
- big numbers in the primary text colour, never the accent;
- the accent (indigo) only for links, active items and chart lines;
- colour otherwise only for statuses (ds.status_color);
- headline numbers first, then charts, then detail tables;
- money never wraps: exact on wide screens, compact ("RM 23.5k") where
  space is tight, the exact value always in a tooltip.

Tokens and formatting live in design.py; this module only composes them.
"""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds

# --- kit tokens ---------------------------------------------------------------
CARD_BG = ds.SURFACE
CARD_BORDER = rx.color("slate", 5)      # soft card / header rule
ROW_BORDER = rx.color("slate", 4)       # even softer row separators
RADIUS = "14px"
CARD_PADDING = ["1em", "1.25em", "1.5em"]
GAP = "5"                               # spacing between page sections

# responsive display: [initial, xs(480), sm(768), md(992), lg(1280)]
_WIDE_ONLY = ["none", "none", "none", "none", "block"]
_NARROW_ONLY = ["block", "block", "block", "block", "none"]


def _when(value, component) -> rx.Component:
    """Render ``component`` only when ``value`` (str or Var) is non-empty."""
    if isinstance(value, rx.Var):
        return rx.cond(value != "", component)
    return component if value else rx.fragment()


# --- page structure -----------------------------------------------------------
def page_header(title, description=None) -> rx.Component:
    """Page title with an optional one-line description under it."""
    items = [rx.heading(title, size=rx.breakpoints(initial="6", md="7"),
                        weight="bold", color=ds.TEXT, id="page-title")]
    if description is not None:
        items.append(rx.text(description, size="2", color=ds.TEXT_MUTED,
                             max_width="72ch"))
    return rx.vstack(*items, spacing="1", align="start", min_width="0")


def section_card(*children, title=None, subtitle=None, extra=None,
                 id=None, **props) -> rx.Component:
    """The one card container. Never nest these: put tables, charts and
    stats straight inside."""
    body = list(children)
    if title is not None:
        head = [rx.vstack(
            rx.heading(title, size="4", weight="medium", color=ds.TEXT),
            *([rx.text(subtitle, size="1", color=ds.TEXT_FAINT)]
              if subtitle is not None else []),
            spacing="1", align="start", min_width="0")]
        if extra is not None:
            head += [rx.spacer(), extra]
        body.insert(0, rx.hstack(*head, width="100%", align="start",
                                 wrap="wrap", spacing="3"))
    return rx.box(
        rx.vstack(*body, spacing="4", width="100%", align="stretch"),
        id=id, width="100%", min_width="0",
        background=CARD_BG, border=f"1px solid {CARD_BORDER}",
        border_radius=RADIUS, padding=CARD_PADDING, **props,
    )


# --- chips ----------------------------------------------------------------------
def status_chip(text, color=None, id=None,
                wrap: bool = False) -> rx.Component:
    """Status chip. ``color`` defaults to ds.status_color(text) for plain
    strings; pass the state-computed colour for Vars."""
    if color is None:
        color = ds.status_color(text) if isinstance(text, str) else "gray"
    return rx.badge(text, color_scheme=color, variant="soft", size="1",
                    radius="full", id=id,
                    white_space="normal" if wrap else "nowrap")


def early_tag(tag, tip="") -> rx.Component:
    """The engine's "Early estimate" tag (projection under 7 days)."""
    return _when(tag, rx.tooltip(
        rx.badge(tag, color_scheme="blue", variant="outline", size="1",
                 radius="full", white_space="nowrap"),
        content=tip))


def status_legend(statuses=ds.DAY_STATUSES, note=None) -> rx.Component:
    items = [status_chip(s) for s in statuses]
    if note is not None:
        items.append(rx.text(note, size="1", color=ds.TEXT_FAINT))
    return rx.hstack(*items, spacing="2", align="center", wrap="wrap")


# --- numbers --------------------------------------------------------------------
def _value(value, compact=None, exact=None, size_wide="7",
           size_narrow="6") -> rx.Component:
    """A big number in the primary text colour that never wraps. With
    ``compact`` the exact figure shows only on wide screens; ``exact``
    (default ``value``) is the tooltip."""
    style = dict(color=ds.TEXT, weight="medium", white_space="nowrap",
                 style={"fontVariantNumeric": "tabular-nums",
                        "letterSpacing": "-0.01em", "lineHeight": "1.15"})
    if compact is None:
        el = rx.text(value, size=rx.breakpoints(initial=size_narrow,
                                                lg=size_wide), **style)
    else:
        el = rx.box(
            rx.text(value, size=size_wide, display=_WIDE_ONLY, **style),
            rx.text(compact, size=size_narrow, display=_NARROW_ONLY, **style),
        )
    if exact is None and compact is None:
        return el
    return rx.tooltip(el, content=exact if exact is not None else value)


def stat(label, value, *, compact=None, exact=None, sub=None, chip=None,
         tag=None, tag_tip="", id=None, size="7",
         chip_id=None) -> rx.Component:
    """Label + big value (+ chips, + sub-line). No border: use inside a
    section_card, or kpi_tile for a standalone tile."""
    # below 992px labels get room for two lines, so values in a row line
    # up and tiles keep one height even when a label wraps
    items = [rx.text(label, size="2", color=ds.TEXT_MUTED, weight="medium",
                     min_height=rx.breakpoints(initial="2.6em", md="0")),
             _value(value, compact, exact, size_wide=size)]
    if chip is not None or tag is not None:
        chips = []
        if chip is not None:
            text, color = chip
            chips.append(_when(text, status_chip(text, color, id=chip_id)))
        if tag is not None:
            chips.append(early_tag(tag, tag_tip))
        items.append(rx.hstack(*chips, spacing="1", wrap="wrap"))
    if sub is not None:
        items.append(_when(sub, rx.text(sub, size="1", color=ds.TEXT_FAINT)))
    return rx.vstack(*items, spacing="2", align="start", min_width="0",
                     width="100%", id=id)


def kpi_tile(label, value, **kw) -> rx.Component:
    """Headline KPI tile: label, value, optional sub-line and status chip.
    No icon badge. Same arguments as ``stat``."""
    return rx.box(
        stat(label, value, **kw),
        background=CARD_BG, border=f"1px solid {CARD_BORDER}",
        border_radius=RADIUS, padding=["1em", "1.1em", "1.25em"],
        min_width="0", height="100%",
    )


def stat_grid(*stats, columns=None) -> rx.Component:
    """Stats inside a section card (no nested borders)."""
    return rx.grid(
        *stats,
        columns=columns or rx.breakpoints(initial="2", md="4"),
        spacing="5", width="100%",
    )


def note(text) -> rx.Component:
    """One muted explanatory line under a group."""
    return rx.text(text, size="1", color=ds.TEXT_FAINT)


def kpi_grid(*tiles, columns=None, id=None) -> rx.Component:
    return rx.grid(
        *tiles,
        columns=columns or rx.breakpoints(initial="2", md="4"),
        spacing="4", width="100%", id=id,
    )


# --- tables ----------------------------------------------------------------------
_TABLE_STYLE = {
    "width": "100%",
    # columns marked wide-only drop out on phones (< 768px)
    # xl-only columns (secondary detail) need a very wide screen
    "@media (max-width: 1535px)": {"& .xl-only": {"display": "none"}},
    "@media (max-width: 767px)": {
        "& .wide-only": {"display": "none"},
        "& th, & td": {"paddingLeft": "8px", "paddingRight": "8px"},
    },
    "borderCollapse": "separate",
    "borderSpacing": "0",
    "fontSize": "14px",
    "fontVariantNumeric": "tabular-nums",
    "& th": {
        "background": CARD_BG, "color": ds.TEXT_MUTED,
        "fontWeight": "500", "fontSize": "12px", "textAlign": "left",
        "padding": "10px 14px", "whiteSpace": "nowrap",
        "borderBottom": f"1px solid {CARD_BORDER}",
    },
    "& td": {
        "padding": "11px 14px", "whiteSpace": "nowrap", "color": ds.TEXT,
        "borderBottom": f"1px solid {ROW_BORDER}",
    },
    "& .num": {"textAlign": "right"},
    "& td.wrap": {"whiteSpace": "normal"},
    # Radix's badge class sets nowrap; let chips wrap in wrap cells
    "& td.wrap .rt-Badge": {"whiteSpace": "normal", "height": "auto",
                            "flexShrink": "1"},
    "& tbody tr:last-child td": {"borderBottom": "none"},
    "& tbody tr:hover td": {"background": ds.SURFACE_HOVER},
    "& tr.collapsed-row td": {"color": ds.TEXT_MUTED},
}


def _cls(numeric: bool, wide_only: bool, wrap: bool = False,
         xl_only: bool = False) -> str:
    return " ".join(c for c, on in (("num", numeric),
                                    ("wide-only", wide_only),
                                    ("wrap", wrap),
                                    ("xl-only", xl_only)) if on)


def th(label, numeric: bool = False, wide_only: bool = False,
       xl_only: bool = False) -> rx.Component:
    return rx.el.th(label, class_name=_cls(numeric, wide_only,
                                           xl_only=xl_only))


def td(*children, numeric: bool = False, wide_only: bool = False,
       wrap: bool = False, xl_only: bool = False, **props) -> rx.Component:
    """Table cell; ``wrap`` lets long labels/chips wrap; ``wide_only``
    hides it on phones, ``xl_only`` below 1536px (match the column)."""
    return rx.el.td(*children,
                    class_name=_cls(numeric, wide_only, wrap, xl_only),
                    **props)


def data_table(columns, *body, id=None) -> rx.Component:
    """Detail table. Grows with its rows and scrolls with the page (no
    fixed-height inner scroll); only overflows sideways, inside its own
    container, on narrow screens. (A sticky header is not possible together
    with that sideways scroll in plain CSS, so the header is not sticky.)

    ``columns``: labels or (label, numeric[, wide_only[, xl_only]])
    tuples — wide_only columns are hidden on phones, xl_only ones below
    1536px (pass the same flag to their ``td``);
    ``body``: row components (``rx.el.tr`` built with ``td``)."""
    heads = [th(c) if isinstance(c, str) else th(*c) for c in columns]
    return rx.box(
        rx.el.table(rx.el.thead(rx.el.tr(*heads)), rx.el.tbody(*body),
                    style=_TABLE_STYLE),
        id=id, width="100%", max_width="100%", overflow_x="auto",
    )


def collapsed_row(n_cols: int, label, expanded, on_toggle,
                  id=None) -> rx.Component:
    """One row standing in for hidden rows: "29 days not reported · Show"."""
    return rx.el.tr(
        rx.el.td(
            rx.hstack(
                rx.text(label, size="2", color=ds.TEXT_MUTED),
                rx.text("·", size="2", color=ds.TEXT_FAINT),
                rx.button(rx.cond(expanded, "Hide", "Show"), variant="ghost",
                          size="1", on_click=on_toggle, id=id,
                          style={"cursor": "pointer"}),
                spacing="2", align="center",
            ),
            col_span=n_cols,
        ),
        class_name="collapsed-row",
    )


def day_rows(all_rows, reported_rows, n_hidden, label, expanded, on_toggle,
             render, n_cols: int, toggle_id=None) -> list:
    """Table body for a month of days: reported days only, plus one
    collapsed row for the unreported ones (default collapsed). Expanded,
    every day shows in date order."""
    return [
        rx.cond(expanded, rx.foreach(all_rows, render),
                rx.foreach(reported_rows, render)),
        rx.cond(n_hidden > 0,
                collapsed_row(n_cols, label, expanded, on_toggle,
                              id=toggle_id)),
    ]


# --- empty / chart states ----------------------------------------------------------
def empty_state(title, detail=None, icon: str = "inbox") -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.icon(icon, size=22, color=ds.TEXT_FAINT),
            rx.text(title, size="2", weight="medium", color=ds.TEXT_MUTED),
            *([rx.text(detail, size="1", color=ds.TEXT_FAINT,
                       text_align="center", max_width="48ch")]
              if detail is not None else []),
            spacing="1", align="center",
        ),
        padding="2em 1em", width="100%",
    )


def not_enough_data(detail=None) -> rx.Component:
    """Compact stand-in for a chart that would be near-empty."""
    return rx.hstack(
        rx.icon("chart-line", size=18, color=ds.TEXT_FAINT),
        rx.vstack(
            rx.text("Not enough data yet", size="2", weight="medium",
                    color=ds.TEXT_MUTED),
            *([rx.text(detail, size="1", color=ds.TEXT_FAINT)]
              if detail is not None else []),
            spacing="0", align="start",
        ),
        spacing="3", align="center", width="100%",
        padding="1em 1.25em", border_radius="10px",
        border=f"1px dashed {CARD_BORDER}",
    )


def chart_card(title, chart, *, ready, subtitle=None, detail=None,
               id=None) -> rx.Component:
    """Chart in a section card; ``ready`` False -> compact
    "Not enough data yet" instead of a near-empty plot."""
    return section_card(rx.cond(ready, chart, not_enough_data(detail)),
                        title=title, subtitle=subtitle, id=id)
