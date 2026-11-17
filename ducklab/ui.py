"""DuckLab UI kit: the shared page components every /ops page builds on.

Style rules (one place, so pages stay consistent):
- calm, spacious layout: soft 1px card borders, no card inside a card;
- big numbers in the primary text colour, never the accent;
- the accent (cobalt blue) only for links, active items and chart lines;
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
RADIUS = "12px"
CARD_PADDING = ["0.875em", "1.25em", "1.5em"]
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
                 radius="full", white_space="nowrap", tab_index=0),
        content=tip))


METRIC_HELP = {
    "ASP": "Average Selling Price: sales divided by units sold.",
    "BV": "Basket Value: average sales per transaction.",
    "SSSG": "Same-Store Sales Growth compared with the reference period.",
    "UPT": "Units Per Transaction: units sold divided by transactions.",
}


def metric_label(label, definition=None) -> rx.Component:
    """Metric name with a hover-, keyboard-, and touch-accessible definition."""
    definition = definition or METRIC_HELP.get(str(label), "")
    if not definition:
        return rx.text(label, size="2", color=ds.TEXT_MUTED, weight="medium")
    trigger = rx.el.button(
        label, type="button", class_name="metric-definition",
        aria_label=f"{label}: {definition}",
        style={"color": "var(--slate-11)", "fontSize": "var(--font-size-2)",
               "fontWeight": "500", "cursor": "help", "padding": "0",
               "textDecoration": "underline dotted",
               "textUnderlineOffset": "3px"},
    )
    return rx.box(
        rx.tooltip(trigger, content=definition),
        rx.popover.root(
            rx.popover.trigger(rx.el.button(
                label, type="button", class_name="metric-definition-touch",
                aria_label=f"Explain {label}",
                style={"color": "var(--slate-11)",
                       "fontSize": "var(--font-size-2)", "fontWeight": "500",
                       "padding": "0", "textDecoration": "underline dotted",
                       "textUnderlineOffset": "3px"})),
            rx.popover.content(rx.text(definition, size="2"),
                               width="min(320px, calc(100vw - 32px))",
                               collision_padding=16),
        ),
        class_name="metric-definition-host", display="inline-block",
    )


def labelled_select(options, *, value=None, on_change=None, placeholder=None,
                    id=None, aria_label, size=None, **props) -> rx.Component:
    """Radix select with its accessible name on the focusable trigger."""
    root_props = dict(value=value, on_change=on_change, size=size)
    root_props.update(props)
    return rx.select.root(
        rx.select.trigger(id=id, aria_label=aria_label,
                          placeholder=placeholder),
        rx.select.content(rx.foreach(
            options, lambda item: rx.select.item(item, value=item))),
        **root_props,
    )


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
                                                lg=size_wide), tab_index=0,
                     aria_label=str(exact if exact is not None else value),
                     **style)
    else:
        el = rx.box(
            rx.text(value, size=size_wide, display=_WIDE_ONLY, **style),
            rx.text(compact, size=size_narrow, display=_NARROW_ONLY, **style),
            tab_index=0, aria_label=str(exact if exact is not None else value),
        )
    if exact is None and compact is None:
        return el
    # Focus the value itself so the exact figure works with keyboard and touch,
    # not only mouse hover.
    exact_value = str(exact if exact is not None else value)
    return rx.tooltip(el, content=exact_value)


def stat(label, value, *, compact=None, exact=None, sub=None, chip=None,
         tag=None, tag_tip="", id=None, size="7",
         chip_id=None, size_narrow="6", sub2=None) -> rx.Component:
    """Label + big value (+ chips, + sub-line). No border: use inside a
    section_card, or kpi_tile for a standalone tile. ``size_narrow`` is the
    value size below 1280px (smaller where exact money sits two-up on a
    phone)."""
    # below 992px labels get room for two lines, so values in a row line
    # up and tiles keep one height even when a label wraps
    items = [rx.box(metric_label(label),
                    min_height=rx.breakpoints(initial="2.6em", md="0")),
             _value(value, compact, exact, size_wide=size,
                    size_narrow=size_narrow)]
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
    if sub2 is not None:  # a second sub-line, same style
        items.append(_when(sub2, rx.text(sub2, size="1",
                                         color=ds.TEXT_FAINT)))
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
# Phones (< 640px) show each table's phone column set (data_table ``phone``)
# so the useful columns come first; the rest stay on wider screens and in
# the row's dialog. Wider than that, a table that does not fit scrolls
# sideways inside its own container. Tables whose last column is the row
# action (Edit) pin that column to the right edge with a rule + soft
# shadow, so Edit is always in view; other tables get a fade at the right
# edge (see data_table).
PHONE = "@media (max-width: 639px)"
_ACT = "&.has-act tr:not(.collapsed-row) > :last-child"
_TABLE_STYLE = {
    "width": "100%",
    # cell content shown only on phones (e.g. the category under an expense
    # description) / hidden on phones (e.g. the weekday next to a date)
    "& .ph-only": {"display": "none"},
    # xl-only columns (secondary detail, also in the row's dialog) hide on
    # tablet/desktop widths below 1536px so Edit stays in view there; phones
    # scroll and show them
    "@media (min-width: 768px) and (max-width: 1535px)": {
        "& .xl-only": {"display": "none"}},
    "@media (max-width: 767px)": {
        "& th, & td": {"paddingLeft": "8px", "paddingRight": "8px"},
        # room under the right-edge fade once scrolled to the end
        "&:not(.has-act) tr > :last-child": {"paddingRight": "28px"},
    },
    "@media (max-width: 991px)": {
        _ACT: {"borderLeft": f"1px solid {CARD_BORDER}",
               "boxShadow": "-8px 0 10px -8px rgba(0, 0, 0, 0.35)",
               "paddingLeft": "10px"},
    },
    _ACT: {"position": "sticky", "right": "0", "zIndex": "1",
           "background": CARD_BG},
    "&.has-act tbody tr:not(.collapsed-row):hover > td:last-child": {
        "background": ds.SURFACE_HOVER},
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
    # last, so it wins over the wider breakpoints above: tighter cells so a
    # phone column set fits 390px without sideways scroll
    PHONE: {
        "& .ph-only": {"display": "block"},
        "& .ph-hide": {"display": "none"},
        "& th, & td": {"paddingLeft": "5px", "paddingRight": "5px"},
        "& td": {"fontSize": "13px"},
        # a long word hyphenates (or, failing that, breaks) rather than
        # push a phone column off-screen
        "& td.wrap": {"hyphens": "auto", "overflowWrap": "anywhere"},
        _ACT: {"paddingLeft": "6px", "paddingRight": "2px"},
    },
}


def _cls(numeric: bool, wrap: bool = False, xl_only: bool = False) -> str:
    return " ".join(c for c, on in (("num", numeric), ("wrap", wrap),
                                    ("xl-only", xl_only)) if on)


def th(label, numeric: bool = False, xl_only: bool = False) -> rx.Component:
    return rx.el.th(label, class_name=_cls(numeric, xl_only=xl_only))


def td(*children, numeric: bool = False, wrap: bool = False,
       xl_only: bool = False, **props) -> rx.Component:
    """Table cell; ``wrap`` lets long labels/chips wrap; ``xl_only`` hides
    it at 768-1535px (match the column)."""
    return rx.el.td(*children, class_name=_cls(numeric, wrap, xl_only),
                    **props)


# right-edge fade over the sideways-scrolling area (tables without a
# pinned action column); the table's last cells leave room under it
_FADE_STYLE = {
    "position": "relative",
    "@media (max-width: 767px)": {"&::after": {
        "content": '""', "position": "absolute", "top": "0", "right": "0",
        "bottom": "0", "width": "28px", "pointerEvents": "none",
        "background": f"linear-gradient(to right, transparent, {CARD_BG})",
    }},
}


def _phone_style(columns, phone) -> dict:
    """Hide (below 640px) every column not in ``phone`` (labels kept)."""
    labels = [c if isinstance(c, str) else c[0] for c in columns]
    unknown = set(phone) - set(labels)
    if unknown:
        raise ValueError(f"phone columns not in the table: {unknown}")
    hide = {f"& tr:not(.collapsed-row) > :nth-child({i})": {"display": "none"}
            for i, label in enumerate(labels, 1) if label not in phone}
    return {PHONE: {**_TABLE_STYLE[PHONE], **hide}}


def data_table(columns, *body, id=None, hidden_note=None,
               phone=None, phone_labels=None) -> rx.Component:
    """Detail table. Grows with its rows and scrolls with the page (no
    fixed-height inner scroll); on narrow screens it scrolls sideways
    inside its own container (never hides columns there). (A sticky header
    is not possible together with that sideways scroll in plain CSS, so the
    header is not sticky.)

    ``columns``: labels or (label, numeric[, xl_only]) tuples — xl_only
    columns hide at 768-1535px (pass the same flag to their ``td``) and
    ``hidden_note`` then says where their values are. A last column
    labelled "" is the row action (Edit/View): it stays pinned to the
    right edge while the table scrolls.
    ``phone``: the column labels shown below 640px (include "" to keep
    Edit); the others hide there and a phone-only line says so.
    ``phone_labels``: shorter header text on phones ({"Incurred": "Date"}).
    ``body``: row components (``rx.el.tr`` built with ``td``)."""
    def head(label):
        short = (phone_labels or {}).get(label)
        if short is None:
            return label
        return rx.fragment(rx.el.span(label, class_name="ph-hide"),
                           rx.el.span(short, class_name="ph-only"))

    heads = [th(head(c)) if isinstance(c, str) else th(head(c[0]), *c[1:])
             for c in columns]
    has_act = columns[-1] == ""
    style = _TABLE_STYLE
    if phone is not None:
        style = {**_TABLE_STYLE, **_phone_style(columns, phone)}
    table = rx.box(
        rx.el.table(rx.el.thead(rx.el.tr(*heads)), rx.el.tbody(*body),
                    style=style,
                    class_name="has-act" if has_act else ""),
        width="100%", max_width="100%", overflow_x="auto",
    )
    items = [rx.box(table, width="100%", min_width="0",
                    style={} if has_act else _FADE_STYLE,
                    class_name="kit-table")]
    if hidden_note is not None:
        items.append(rx.box(note(hidden_note),
                            display=rx.breakpoints(initial="none", sm="block",
                                                   xl="none")))
    if phone is not None and len(phone) < len(columns):
        more = ("More columns on a wider screen and in each row's Edit."
                if "" in phone else "More columns on a wider screen.")
        items.append(rx.box(note(more), class_name="ph-note",
                            style={"display": "none",
                                   PHONE: {"display": "block"}}))
    return rx.vstack(*items, id=id, spacing="2", width="100%",
                     min_width="0", align="stretch")


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
def empty_state(title, detail=None, icon: str = "inbox", action=None) -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.icon(icon, size=22, color=ds.TEXT_FAINT, aria_hidden=True),
            rx.text(title, size="2", weight="medium", color=ds.TEXT_MUTED),
            *([rx.text(detail, size="1", color=ds.TEXT_MUTED,
                       text_align="center", max_width="48ch")]
              if detail is not None else []),
            *([action] if action is not None else []),
            spacing="2", align="center",
        ),
        padding="2em 1em", width="100%", role="status",
    )


def success_callout(message, id=None) -> rx.Component:
    """Announce successful mutations without unexpectedly moving focus."""
    return rx.callout(message, icon="circle-check", color_scheme="green",
                      variant="soft", width="100%", id=id, role="status",
                      aria_live="polite")


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


def chart_summary(summary) -> rx.Component:
    """A compact, nonvisual explanation; detailed values stay in page tables."""
    return rx.text(summary, size="1", color=ds.TEXT_MUTED, role="note")


def chart_card(title, chart, *, ready, subtitle=None, detail=None,
               summary=None, id=None) -> rx.Component:
    """Chart with an optional concise summary; no duplicate data table."""
    return section_card(
        rx.cond(ready, chart, not_enough_data(detail)),
        *([rx.cond(ready, chart_summary(summary))] if summary is not None else []),
        title=title, subtitle=subtitle, id=id,
    )


# --- entry dialogs and forms ---------------------------------------------------------
# One look for every entry dialog: header (title, one-line subtitle, close),
# pinned notices, a scrolling body of form sections, and a pinned footer
# (status text left, Cancel + primary action right).
_DIALOG_PAD = ["1em", "1.25em", "1.5em"]
_RULE = f"1px solid {CARD_BORDER}"
# consecutive form sections are separated by a soft rule
_SECTION_RULES = {"& > .form-section + .form-section": {
    "borderTop": f"1px solid {ROW_BORDER}", "paddingTop": "1.25em"}}


def form_field(label, control, hint=None, *, required=False,
               clear=None) -> rx.Component:
    """Label (text, or a component for a richer label), control (stretched
    to the column width), optional hint and an optional "Clear saved
    value" checkbox (``clear``) under it."""
    head = [label if isinstance(label, rx.Component)
            else rx.text(label, size="2", weight="medium", color=ds.TEXT)]
    if required:
        head.append(rx.text("*", size="2", color=ds.ERROR))
    # An implicit native label works for inputs and Radix select trigger
    # buttons alike, without relying on optional page-specific ids.
    items = [rx.el.label(
        rx.hstack(*head, spacing="1", align="baseline"), control,
        display="flex", flex_direction="column", gap="4px", width="100%",
    )]
    if hint is not None:
        items.append(_when(hint, rx.text(hint, size="1",
                                         color=ds.TEXT_FAINT)))
    if clear is not None:
        items.append(clear)
    return rx.vstack(
        *items, spacing="1", align="stretch", width="100%", min_width="0",
        style={"& .rt-TextFieldRoot, & .rt-TextAreaRoot, & .rt-SelectTrigger":
               {"width": "100%"}},
    )


def clear_box(checked, on_change, disabled=False, id=None) -> rx.Component:
    """The "Clear saved value" checkbox under a field."""
    return rx.checkbox("Clear saved value", checked=checked,
                       on_change=on_change, size="1", disabled=disabled,
                       id=id, color=ds.TEXT_MUTED)


def clear_x(visible, on_click, id=None) -> rx.Component:
    """A small "×" inside a field (right slot), shown only while the field
    has a saved value; replaces the "Clear saved value" checkbox."""
    return rx.input.slot(
        rx.cond(visible, rx.tooltip(
            rx.icon_button(rx.icon("x", size=12), on_click=on_click,
                           size="1", variant="ghost", color_scheme="gray",
                           radius="full", type="button", id=id,
                           aria_label="Clear saved value"),
            content="Clear")),
        side="right")


def form_grid(*fields, columns: str = "2") -> rx.Component:
    """Fields in equal columns (one column on phones)."""
    return rx.grid(*fields,
                   columns=rx.breakpoints(initial="1", sm=columns),
                   spacing="4", width="100%")


def form_section(title, *children, extra=None, hint=None,
                 id=None) -> rx.Component:
    """A titled group of fields; groups are separated by a soft rule."""
    head = [rx.text(title, size="3", weight="medium", color=ds.TEXT)]
    if extra is not None:
        head += [rx.spacer(), extra]
    items = [rx.hstack(*head, width="100%", align="center", wrap="wrap",
                       spacing="2"), *children]
    if hint is not None:
        items.append(note(hint))
    return rx.vstack(*items, spacing="3", width="100%", align="stretch",
                     class_name="form-section", id=id)


def form_stack(*sections, **props) -> rx.Component:
    """Form sections stacked with a soft rule between them (dialog bodies
    and on-page forms such as Monthly Setup)."""
    return rx.vstack(*sections, spacing="5", width="100%", align="stretch",
                     style=_SECTION_RULES, **props)


def dialog_actions(*, save_label, on_save, saving, save_id, cancel_id,
                   can_save=True) -> list:
    """Footer buttons: Cancel (closes) + the primary save action."""
    save = rx.button(
        rx.cond(saving, rx.spinner(size="1"), rx.icon("save", size=14)),
        save_label, color_scheme="blue", on_click=on_save,
        disabled=saving, id=save_id)
    return [
        rx.dialog.close(rx.button("Cancel", variant="soft",
                                  color_scheme="gray", id=cancel_id)),
        save if can_save is True else rx.cond(can_save, save),
    ]


def form_dialog(*body, open, on_open_change, title, subtitle=None,
                notices=(), actions=(), status=None, close_id=None,
                width: str = "720px", **content_props) -> rx.Component:
    """Centered entry dialog (Radix, portaled to <body>) with a pinned
    header and footer; only the body scrolls."""
    head_text = [rx.dialog.title(title, size="4", weight="medium",
                                 color=ds.TEXT)]
    if subtitle is not None:
        head_text.append(rx.dialog.description(
            subtitle, size="2", color=ds.TEXT_MUTED))
    header = rx.hstack(
        rx.vstack(*head_text, spacing="1", align="start", min_width="0"),
        rx.spacer(),
        rx.dialog.close(rx.icon_button(rx.icon("x", size=16),
                                       variant="ghost", color_scheme="gray",
                                       aria_label="Close dialog", id=close_id)),
        width="100%", align="start", flex_shrink="0",
        padding=_DIALOG_PAD, border_bottom=_RULE,
    )
    parts = [header]
    if notices:
        parts.append(rx.vstack(*notices, spacing="2", width="100%",
                               padding_x=_DIALOG_PAD, padding_top="1em",
                               flex_shrink="0",
                               style={"&:empty": {"display": "none"}}))
    parts.append(rx.scroll_area(
        form_stack(*body, padding=_DIALOG_PAD),
        type="auto", scrollbars="vertical", flex="1", min_height="0",
        width="100%"))
    parts.append(rx.hstack(
        *([status] if status is not None else []),
        rx.spacer(), *actions,
        spacing="3", width="100%", align="center", flex_shrink="0",
        wrap="wrap", padding_x=_DIALOG_PAD, padding_y="0.85em", border_top=_RULE,
        background=CARD_BG,
    ))
    return rx.dialog.root(
        rx.dialog.content(
            *parts,
            padding="0", background=ds.BG, overflow="hidden",
            style={"width": f"min({width}, 92vw)",
                   "maxWidth": f"min({width}, 92vw)", "maxHeight": "85vh",
                   "display": "flex", "flexDirection": "column"},
            **content_props,
        ),
        open=open, on_open_change=on_open_change,
    )
