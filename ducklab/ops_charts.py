"""Shared Plotly chart helper for /ops pages.

Pages must NOT contain raw Plotly layout code — call these builders with
plain, already-computed values (from kpi.py / assemble_month, never
recomputed here) and pass the returned figure dict to ``chart(...)``.

Theme: page states retain only the active-theme figure and regenerate it
from their private source data when the theme changes. The existing dark
and light fields/selectors are kept for compatibility, but the inactive
field is empty. Dark and light figures differ ONLY in styling.

Semantics enforced here:
- daily line with gaps: None/unreported = a gap (connectgaps=False),
  explicit 0 = a plotted point, never interpolated.
- pace: actual cumulative line stops at the last reported day.
- budget bars: blank (None) budget = no bar.
- composition: negative contributions (e.g. Extra) plotted as offsets;
  info-only categories visually distinct and labelled "not in Total Loss";
  categories that are zero for the month are left out.
- legends sit above the plot (never under the x-axis labels, which can
  rotate on phones); long category labels wrap onto two lines.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

import plotly.graph_objects as go

# --- palette (hardcoded hex mirroring the Radix-based ds.* tokens, so the
# charts match the app's semantic colors in BOTH modes) -------------------
_DARK = dict(
    bg="rgba(0,0,0,0)",
    plot_bg="rgba(0,0,0,0)",
    text="#EDEEF0",        # slate-12
    muted="#B0B4BA",       # slate-11
    grid="#272A2D",        # slate-4 (subtle)
    border="#363A3F",      # slate-6
    surface_hover="#212225",  # slate-3
    accent="#52A9FF",      # blue-9
    accent_fill="rgba(82,169,255,0.18)",
    pace_fill="rgba(82,169,255,0.07)",
    budget="#B0B4BA",
    green="#3DD68C",
    amber="#FFC53D",
    red="#E5484D",
    info="#52A9FF",        # info-only categories
    # categorical (Expense Mix), fixed order; green/red stay status-only
    series=("#3987E5", "#D95926", "#199E70", "#C98500", "#D55181",
            "#9085E9"),
    other="#696E77",       # slate-9, the folded "Other" slice
    # sequential (Sales Calendar): one hue, low -> high
    seq=("#102A56", "#52A9FF"),
    seq_text=("#EDEEF0", "#111113"),
    surface="#18191B",     # slice / cell gaps
)
_LIGHT = dict(
    bg="rgba(0,0,0,0)",
    plot_bg="rgba(0,0,0,0)",
    text="#1C2024",
    muted="#60646C",
    grid="#E8E8EC",        # slate-5 (subtle)
    border="#B9BBC6",      # slate-6
    surface_hover="#F0F0F3",  # slate-3
    accent="#0090FF",      # blue-9
    accent_fill="rgba(0,144,255,0.14)",
    pace_fill="rgba(0,144,255,0.06)",
    budget="#60646C",
    green="#18794E",
    amber="#B26A00",
    red="#CD2B31",
    info="#0090FF",
    series=("#2A78D6", "#EB6834", "#1BAF7A", "#EDA100", "#E87BA4",
            "#6250D6"),
    other="#8B8D98",
    seq=("#E1F0FF", "#0090FF"),
    seq_text=("#1C2024", "#FFFFFF"),
    surface="#FFFFFF",
)

_CONFIG = dict(
    displaylogo=False,
    modeBarButtons=[["zoom2d", "pan2d", "resetScale2d", "toImage"]],
    responsive=True,
)


def chart(fig_json, height: str = "340px", description: str = "Interactive chart"):
    """Render a named chart; summaries/detail tables supply nonvisual values."""
    import reflex as rx
    return rx.box(
        rx.plotly(
            data=fig_json,
            config=_CONFIG,
            use_resize_handler=True,
            width="100%",
            height=height,
        ),
        role="img", aria_label=description, width="100%",
    )

FONT_FAMILY = ("ui-sans-serif, system-ui, -apple-system, 'Segoe UI', "
               "Roboto, sans-serif")


def _pal(dark: bool) -> dict:
    return _DARK if dark else _LIGHT


def fmt_rm(cents: Optional[float]) -> str:
    """RM 1,234.50 / −RM 85.00 (shared design.py format); dash if missing."""
    from ducklab.design import fmt_rm as _fmt
    return _fmt(cents)


def fmt_rm0(cents: Optional[float]) -> str:
    from ducklab.design import fmt_rm0 as _fmt
    return _fmt(cents)


def fmt_pct(ratio: Optional[float]) -> str:
    if ratio is None:
        return "—"
    return f"{float(ratio) * 100:.2f}%"


def _base_layout(pal: dict, y_title: str, x_title: str = "") -> dict:
    return dict(
        paper_bgcolor=pal["bg"],
        plot_bgcolor=pal["plot_bg"],
        font=dict(family=FONT_FAMILY, color=pal["text"], size=12),
        margin=dict(l=8, r=8, t=12, b=8),
        # quiet axes: horizontal gridlines only, no zero line / axis line
        xaxis=dict(title=x_title, showgrid=False, zeroline=False,
                   showline=False, color=pal["muted"], automargin=True,
                   showspikes=True, spikecolor=pal["border"],
                   spikethickness=1, spikedash="solid", spikemode="across"),
        yaxis=dict(title=y_title, gridcolor=pal["grid"], zeroline=False,
                   showline=False, color=pal["muted"], automargin=True,
                   showspikes=True, spikecolor=pal["border"],
                   spikethickness=1, spikedash="solid", spikemode="across"),
        # above the plot: x-axis labels (which may rotate on narrow
        # screens) can never run into it
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="left", x=0, font=dict(size=11)),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=pal["surface_hover"],
                        bordercolor=pal["border"],
                        font=dict(color=pal["text"])),
        autosize=True,
    )


# ---------------------------------------------------------------------------
# 1. daily line/area with gaps
# ---------------------------------------------------------------------------
def daily_line(day_labels: Sequence[str],
               values_rm: Sequence[Optional[float]],
               dark: bool,
               name: str = "Daily",
               y_title: str = "RM",
               target_rm: Optional[float] = None,
               day_numbers: Optional[Sequence[int]] = None) -> go.Figure:
    """One value per calendar day; None -> gap (no interpolation).

    With ``day_numbers`` the x axis is numeric day-of-month (a few clean
    ticks, never 31 rotated labels); unified hover uses that day as its title."""
    pal = _pal(dark)
    hover = [f"{name}: {fmt_rm(v * 100 if v is not None else None)}"
             for v in values_rm]
    x = list(day_numbers) if day_numbers is not None else list(day_labels)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=[None if v is None else v for v in values_rm],
        mode="lines+markers", connectgaps=False, name=name,
        line=dict(color=pal["accent"], width=2),
        marker=dict(size=5, color=pal["accent"]),
        fill="tozeroy", fillcolor=pal["accent_fill"],
        hovertemplate="%{customdata}<extra></extra>", customdata=hover,
    ))
    if target_rm is not None:
        fig.add_hline(y=target_rm, line_dash="dot",
                      line_color=pal["budget"],
                      annotation_text=f"Target {fmt_rm0(target_rm * 100)}",
                      annotation_font_color=pal["muted"])
    layout = _base_layout(pal, y_title,
                          x_title="Day" if day_numbers else "")
    if day_numbers:
        layout["xaxis"]["range"] = [0.5, max(day_numbers) + 0.5]
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# 2. sparkline (tiny daily line, no axes, static — overview hero cards)
# ---------------------------------------------------------------------------
def sparkline(days: Sequence[int],
              values_rm: Sequence[Optional[float]],
              dark: bool,
              height: int = 56) -> go.Figure:
    """One value per calendar day; None -> gap (same rules as daily_line).
    No axes, no hover, no modebar — a compact trend shape only."""
    pal = _pal(dark)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(days), y=[None if v is None else v for v in values_rm],
        mode="lines", connectgaps=False,
        line=dict(color=pal["accent"], width=1.5),
        fill="tozeroy", fillcolor=pal["accent_fill"],
        hoverinfo="skip",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=2, b=0),
        xaxis=dict(visible=False, fixedrange=True),
        yaxis=dict(visible=False, fixedrange=True),
        showlegend=False, hovermode=False, height=height,
    )
    return fig


def sparkline_chart(fig_json):
    """Render a sparkline figure (no interactions)."""
    import reflex as rx
    return rx.box(
        rx.plotly(
            data=fig_json,
            config={"displayModeBar": False, "staticPlot": True,
                    "responsive": True},
            width="100%",
            height="56px",
        ),
        role="img",
        aria_label="Recent trend; open the linked module for exact values",
        width="100%",
    )


# ---------------------------------------------------------------------------
# 2b. cumulative actual vs budget pace
# ---------------------------------------------------------------------------
def pace_chart(days: Sequence[int],
               actual_cum_cents: Sequence[Optional[float]],
               budget_cum_cents: Sequence[Optional[float]],
               dark: bool,
               y_title: str = "RM (cumulative)",
               levels: Sequence[tuple[int, Sequence[Optional[float]]]] = (),
               highlight: int = 1,
               ) -> go.Figure:
    """Actual line stops at the last reported day (None afterwards).

    ``levels``: (level, cumulative pace) for Levels 2-4 (kpi.level_pace_lines,
    already de-duplicated) — lighter dashed lines; the ``highlight`` level
    (default Level 1, the budget pace) is the stronger line and the one in
    the legend. Every target line is labelled at the right edge."""
    pal = _pal(dark)
    fig = go.Figure()
    x = list(days)
    upper = [(n, p) for n, p in levels if n != 1]
    ordered_levels = [(1, budget_cum_cents)] + upper

    def _actual_line() -> None:
        fig.add_trace(go.Scatter(
            x=x, y=[None if v is None else v / 100 for v in actual_cum_cents],
            mode="lines", connectgaps=False, name="Actual",
            line=dict(color=pal["accent"], width=2.5),
            fill="tozeroy", fillcolor=pal["pace_fill"],
            hovertemplate="Actual: RM %{y:,.0f}<extra></extra>",
        ))
    labelled = bool(upper)

    def _target_line(n: int, pace) -> None:
        y = [None if v is None else v / 100 for v in pace]
        strong = n == highlight
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="lines",
            # legend: "Actual" and "Target" (the highlighted level); the
            # other levels are labelled at the right edge
            name="Target" if strong else f"Level {n}",
            showlegend=strong,
            opacity=1.0 if strong else 0.55,
            line=dict(color=pal["budget"], dash="dash",
                      width=1.75 if strong else 1),
            hovertemplate=(f"Level {n}: RM %{{y:,.0f}}"
                           "<extra></extra>"),
        ))
        if labelled and x and y and y[-1] is not None:
            fig.add_annotation(
                x=x[-1], y=y[-1], xref="x", yref="y", text=f"Level {n}",
                showarrow=False, xanchor="left", xshift=4,
                font=dict(size=10, color=pal["muted"]),
                opacity=1.0 if strong else 0.7)

    _actual_line()
    for n, pace in ordered_levels:
        _target_line(n, pace)
    layout = _base_layout(pal, y_title, x_title="Day")
    if labelled:
        layout["margin"] = dict(layout["margin"], r=52)  # right-edge labels
    fig.update_layout(**layout)
    return fig


def wrap_label(text: str, width: int = 12) -> str:
    """Category axis label on at most two lines ("Repairs &<br>
    Maintenance"); short labels unchanged."""
    if len(text) <= width or " " not in text:
        return text
    words, first = text.split(" "), ""
    while words and len(first) + len(words[0]) <= width:
        first = f"{first} {words.pop(0)}".strip()
    if not first:
        first = words.pop(0)
    return f"{first}<br>{' '.join(words)}" if words else first


# ---------------------------------------------------------------------------
# 3. grouped bars: budget vs actual per category
# ---------------------------------------------------------------------------
def budget_vs_actual(categories: Sequence[str],
                     budgets_cents: Sequence[Optional[float]],
                     actuals_cents: Sequence[float],
                     dark: bool,
                     y_title: str = "") -> go.Figure:
    """Horizontal grouped bars ("Budget", "Paid") per category, so long
    category names read without rotation. None budget = no budget bar."""
    pal = _pal(dark)
    fig = go.Figure()
    names = list(categories)
    fig.add_trace(go.Bar(
        y=names, x=[None if b is None else b / 100 for b in budgets_cents],
        orientation="h", name="Budget", marker_color=pal["budget"],
        opacity=0.55,
        hovertemplate="Budget: RM %{x:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        y=names, x=[a / 100 for a in actuals_cents],
        orientation="h", name="Paid", marker_color=pal["accent"],
        hovertemplate="Paid: RM %{x:,.2f}<extra></extra>",
    ))
    layout = _base_layout(pal, y_title)
    layout["barmode"] = "group"
    # first category on top; values on the x axis (gridlines vertical)
    layout["yaxis"] = dict(layout["yaxis"], autorange="reversed",
                           showgrid=False)
    layout["xaxis"] = dict(layout["xaxis"], showgrid=True,
                           gridcolor=pal["grid"])
    layout["hovermode"] = "y unified"
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# 4. composition bars for stock-loss categories
# ---------------------------------------------------------------------------
def composition(items: Sequence[dict], dark: bool,
                y_title: str = "RM") -> go.Figure:
    """items: {code, name, total_cents, sign (1 loss / -1 offset / 0 info)}.

    Loss codes (sign 1) stack positive; offsets (sign -1, e.g. Extra)
    stack negative; info-only codes (sign 0) are drawn as a separate,
    visually distinct (hatched via opacity) trace labelled
    'not in Total Loss'.
    """
    pal = _pal(dark)
    fig = go.Figure()
    items = [i for i in items if i["total_cents"]]  # zero this month: omit
    losses = [i for i in items if i["sign"] == 1]
    offsets = [i for i in items if i["sign"] == -1]
    infos = [i for i in items if i["sign"] == 0]
    x = [i["code"] for i in losses] + [i["code"] for i in offsets] \
        + [i["code"] for i in infos]

    def _bar(rows, name, color, opacity, suffix=""):
        if not rows:
            return
        vals = [abs(i["total_cents"]) / 100 for i in rows]
        fig.add_trace(go.Bar(
            x=[i["code"] for i in rows], y=vals, name=name,
            marker_color=color, opacity=opacity,
            customdata=[[fmt_rm(i["total_cents"]),
                         i["name"]] for i in rows],
            hovertemplate="%{x} — %{customdata[1]}<br>"
                          f"{name}{suffix}: %{{customdata[0]}}"
                          "<extra></extra>",
        ))

    _bar(losses, "Total Loss", pal["red"], 0.95)
    _bar(offsets, "Offset", pal["green"], 0.95)
    _bar(infos, "Info Only", pal["info"], 0.35, suffix=" (not in Total Loss)")
    layout = _base_layout(pal, y_title, x_title="")
    fig.update_layout(**layout)
    fig.update_xaxes(type="category")
    return fig


# ---------------------------------------------------------------------------
# 5. daily sales vs adjusted target (one bar per reported day)
# ---------------------------------------------------------------------------
def daily_vs_target(days: Sequence[int],
                    sales_cents: Sequence[Optional[float]],
                    adjusted_cents: Sequence[Optional[float]],
                    results: Sequence[Optional[str]],
                    dark: bool) -> go.Figure:
    """Bars per reported day, green "Achieved" / red "Not Achieved" (the
    engine's daily result); the Adjusted Target as a line. Unreported days
    (None sales) are gaps: no bar, and the line breaks there."""
    from ducklab.kpi import RESULT_ACHIEVED, RESULT_NOT_ACHIEVED
    pal = _pal(dark)
    x = list(days)
    fig = go.Figure()
    for result, color in ((RESULT_ACHIEVED, pal["green"]),
                          (RESULT_NOT_ACHIEVED, pal["red"])):
        y = [v / 100 if v is not None and r == result else None
             for v, r in zip(sales_cents, results)]
        if not any(v is not None for v in y):
            continue
        fig.add_trace(go.Bar(
            x=x, y=y, name=result, marker_color=color,
            marker_line_width=0, opacity=0.9,
            hovertemplate=f"{result}: RM %{{y:,.2f}}<extra></extra>",
        ))
    fig.add_trace(go.Scatter(
        x=x, y=[None if v is None else v / 100 for v in adjusted_cents],
        mode="lines+markers", connectgaps=False, name="Adjusted Target",
        line=dict(color=pal["budget"], width=2, dash="dot"),
        marker=dict(size=5, color=pal["budget"]),
        hovertemplate="Adjusted Target: RM %{y:,.2f}<extra></extra>",
    ))
    layout = _base_layout(pal, "", x_title="Day")
    layout["barmode"] = "overlay"
    layout["bargap"] = 0.25
    layout["xaxis"]["range"] = [0.5, (max(x) if x else 1) + 0.5]
    layout["hoverlabel"] = dict(layout["hoverlabel"], namelength=-1)
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# 6. average sales per weekday
# ---------------------------------------------------------------------------
def weekday_bars(labels: Sequence[str],
                 avg_cents: Sequence[Optional[float]],
                 dark: bool) -> go.Figure:
    """One bar per weekday (Mon..Sun); None = no reported day, no bar."""
    pal = _pal(dark)
    fig = go.Figure(go.Bar(
        x=list(labels),
        y=[None if v is None else float(v) / 100 for v in avg_cents],
        name="Average Sales", marker_color=pal["accent"],
        marker_line_width=0,
        hovertemplate="%{x}<br>Average Sales: RM %{y:,.2f}<extra></extra>",
    ))
    layout = _base_layout(pal, "")
    layout["bargap"] = 0.35
    layout["hovermode"] = "closest"
    layout["showlegend"] = False
    layout["xaxis"]["fixedrange"] = True
    layout["yaxis"]["fixedrange"] = True
    fig.update_layout(**layout)
    fig.update_xaxes(type="category")
    return fig


# ---------------------------------------------------------------------------
# 7. sales calendar (month grid shaded by sales)
# ---------------------------------------------------------------------------
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def sales_calendar(weeks: Sequence[Sequence[Optional[tuple]]],
                   dark: bool) -> go.Figure:
    """weeks: rows of 7 cells (kpi.month_weeks), each None (outside the
    month) or (day number, hover label, sales cents or None).

    Reported days are shaded by sales (zero = the lightest step) with the
    value on hover; unreported days stay blank (no fill, no hover). Every
    day of the month carries its day number."""
    pal = _pal(dark)
    z, hover = [], []
    reported = [c[2] for row in weeks for c in row
                if c is not None and c[2] is not None]
    top = max(reported) if reported else 0
    for row in weeks:
        z.append([None if c is None or c[2] is None else c[2] / 100
                  for c in row])
        hover.append(["" if c is None or c[2] is None
                      else f"{c[1]}<br>Sales: {fmt_rm(c[2])}" for c in row])
    y = list(range(len(weeks)))
    fig = go.Figure(go.Heatmap(
        z=z, x=list(_WEEKDAYS), y=y, xgap=3, ygap=3,
        colorscale=[[0, pal["seq"][0]], [1, pal["seq"][1]]],
        zmin=0, zmax=max(top / 100, 1), showscale=False,
        hoverongaps=False, customdata=hover,
        hovertemplate="%{customdata}<extra></extra>",
    ))
    for r, row in enumerate(weeks):
        for col, c in enumerate(row):
            if c is None:
                continue
            if c[2] is None:
                color = pal["muted"]
            else:  # ink for the low / high end of the ramp
                share = c[2] / top if top else 0
                color = pal["seq_text"][0 if share < 0.55 else 1]
            fig.add_annotation(x=_WEEKDAYS[col], y=r, text=str(c[0]),
                               showarrow=False, font=dict(size=12,
                                                          color=color))
    layout = _base_layout(pal, "")
    layout["margin"] = dict(l=4, r=4, t=4, b=4)
    layout["xaxis"] = dict(layout["xaxis"], side="top", fixedrange=True,
                           type="category")
    layout["yaxis"] = dict(layout["yaxis"], visible=False,
                           autorange="reversed", fixedrange=True)
    layout["hovermode"] = "closest"
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# 8. expense mix donut
# ---------------------------------------------------------------------------
def expense_donut(items: Sequence[tuple[str, int]],
                  dark: bool) -> go.Figure:
    """items: (category, paid cents) from kpi.expense_mix, largest first;
    the folded "Other" slice is gray. Slices keep the given order."""
    from ducklab.kpi import EXPENSE_MIX_OTHER
    pal = _pal(dark)
    colors, slot = [], 0
    for name, _ in items:
        if name == EXPENSE_MIX_OTHER:
            colors.append(pal["other"])
        else:
            colors.append(pal["series"][slot % len(pal["series"])])
            slot += 1
    fig = go.Figure(go.Pie(
        labels=[n for n, _ in items], values=[c / 100 for _, c in items],
        hole=0.6, sort=False, direction="clockwise",
        marker=dict(colors=colors, line=dict(color=pal["surface"], width=2)),
        textinfo="percent", textposition="outside",
        textfont=dict(color=pal["text"], size=11),
        customdata=[fmt_rm(c) for _, c in items],
        hovertemplate="%{label}<br>%{customdata} (%{percent})"
                      "<extra></extra>",
    ))
    layout = _base_layout(pal, "")
    layout["margin"] = dict(l=8, r=8, t=8, b=8)
    layout["legend"] = dict(orientation="h", yanchor="top", y=-0.02,
                            xanchor="center", x=0.5, font=dict(size=11))
    layout["hovermode"] = "closest"
    fig.update_layout(**layout)
    return fig


# --- overview-only compact formatting (module pages keep exact values) ---
def fmt_money_compact(cents: Optional[float]) -> str:
    """Overview-only compact money: RM 191.3k / RM 6,774 / — for blank.

    Values < 10,000 in whole ringgit; >= 10,000 rounded to 0.1k (>= 1M as
    M) — one format for tiles and panels alike. The exact value is always
    available via fmt_rm for tooltips.
    """
    from ducklab.design import fmt_rm_compact
    return fmt_rm_compact(cents, whole_below=True)


def fmt_pct1(ratio: Optional[float]) -> str:
    """Overview-only: percentages to 1 decimal; dash for blank."""
    if ratio is None:
        return "—"
    return f"{float(ratio) * 100:.1f}%"


# Chart-capable page states register here at import time so the theme
# sync (AuthState.sync_is_dark) can restyle their open figures without
# importing the page modules (Phase 3.6: no lazy cross-module imports).
CHART_STATES: list = []


def register_chart_state(cls) -> None:
    """Register a state class exposing _apply_chart_theme(dark)."""
    if cls not in CHART_STATES:
        CHART_STATES.append(cls)


# ---------------------------------------------------------------------------
# 13. imported sales history (store directory, monthly totals)
# ---------------------------------------------------------------------------
MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def history_timeline(months: Sequence[str],
                     values_cents: Sequence[Optional[int]],
                     dark: bool) -> go.Figure:
    """One point per imported month, chronological. None -> gap, never
    interpolated (the workbook's 0/blank months are absent, not zero)."""
    pal = _pal(dark)
    labels = [f"{MONTH_ABBR[int(m[5:7]) - 1]} {m[2:4]}" for m in months]
    hover = [f"{label}<br>{fmt_rm(v)}"
             for label, v in zip(labels, values_cents)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(labels), y=[None if v is None else v / 100
                           for v in values_cents],
        mode="lines+markers", connectgaps=False, name="Monthly sales",
        line=dict(color=pal["accent"], width=2),
        marker=dict(size=5, color=pal["accent"]),
        hovertemplate="%{customdata}<extra></extra>", customdata=hover,
    ))
    layout = _base_layout(pal, "RM")
    layout["showlegend"] = False
    fig.update_layout(**layout)
    return fig


def history_year_compare(years: Sequence[int],
                         series_cents: Sequence[Sequence[Optional[int]]],
                         dark: bool) -> go.Figure:
    """Year vs year on one Jan-Dec axis; one line per year, same order as
    ``years``. None -> gap, so an unopened month never reads as zero."""
    pal = _pal(dark)
    colors = (pal["muted"], pal["accent"], pal["green"], pal["amber"])
    fig = go.Figure()
    for i, (year, values) in enumerate(zip(years, series_cents)):
        hover = [f"{MONTH_ABBR[m]} {year}<br>{fmt_rm(v)}"
                 for m, v in enumerate(values)]
        fig.add_trace(go.Scatter(
            x=list(MONTH_ABBR),
            y=[None if v is None else v / 100 for v in values],
            mode="lines+markers", connectgaps=False, name=str(year),
            line=dict(color=colors[i % len(colors)], width=2),
            marker=dict(size=5),
            hovertemplate="%{customdata}<extra></extra>", customdata=hover,
        ))
    fig.update_layout(**_base_layout(pal, "RM"))
    return fig
