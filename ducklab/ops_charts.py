"""Shared Plotly chart helper for /ops pages.

Pages must NOT contain raw Plotly layout code — call these builders with
plain, already-computed values (from kpi.py / assemble_month, never
recomputed here) and pass the returned figure dict to ``chart(...)``.

Theme: every builder returns a figure dict; the page state computes one
figure per theme (``dark=True/False``) and the page renders
``rx.cond(S.is_dark, chart(S.fig_dark), chart(S.fig_light))`` so the
chart follows the theme toggle instantly, no page reload. Dark and light
figures differ ONLY in styling.

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
    accent="#6E6BD8",      # indigo-9
    accent_fill="rgba(110,107,216,0.18)",
    pace_fill="rgba(110,107,216,0.07)",
    budget="#B0B4BA",
    green="#3DD68C",
    amber="#FFC53D",
    red="#E5484D",
    info="#6E6BD8",        # info-only categories
)
_LIGHT = dict(
    bg="rgba(0,0,0,0)",
    plot_bg="rgba(0,0,0,0)",
    text="#1C2024",
    muted="#60646C",
    grid="#E8E8EC",        # slate-5 (subtle)
    accent="#5B5BD6",
    accent_fill="rgba(91,91,214,0.14)",
    pace_fill="rgba(91,91,214,0.06)",
    budget="#60646C",
    green="#18794E",
    amber="#B26A00",
    red="#CD2B31",
    info="#5B5BD6",
)

_CONFIG = dict(
    displaylogo=False,
    modeBarButtons=[["zoom2d", "pan2d", "resetScale2d", "toImage"]],
    responsive=True,
)


def chart(fig_json, height: str = "340px"):
    """Render a builder's figure dict (may be a State Var)."""
    import reflex as rx
    return rx.plotly(
        data=fig_json,
        config=_CONFIG,
        use_resize_handler=True,
        width="100%",
        height=height,
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
                   showline=False, color=pal["muted"], automargin=True),
        yaxis=dict(title=y_title, gridcolor=pal["grid"], zeroline=False,
                   showline=False, color=pal["muted"], automargin=True),
        # above the plot: x-axis labels (which may rotate on narrow
        # screens) can never run into it
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="left", x=0, font=dict(size=11)),
        hovermode="x unified",
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
    ticks, never 31 rotated labels); ``day_labels`` then only feed hover."""
    pal = _pal(dark)
    hover = [f"{lbl}<br>{name}: {fmt_rm(v * 100 if v is not None else None)}"
             for lbl, v in zip(day_labels, values_rm)]
    x = list(day_numbers) if day_numbers is not None else list(day_labels)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=[None if v is None else v for v in values_rm],
        mode="lines+markers", connectgaps=False, name=name,
        line=dict(color=pal["accent"], width=2),
        marker=dict(size=5, color=pal["accent"]),
        fill="tozeroy", fillcolor=pal["accent_fill"],
        hoverinfo="text", hovertext=hover,
    ))
    if target_rm is not None:
        fig.add_hline(y=target_rm, line_dash="dot",
                      line_color=pal["budget"],
                      annotation_text=f"Target {fmt_rm0(target_rm * 100)}",
                      annotation_font_color=pal["muted"])
    layout = _base_layout(pal, y_title,
                          x_title="Day of month" if day_numbers else "")
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
    return rx.plotly(
        data=fig_json,
        config={"displayModeBar": False, "staticPlot": True,
                "responsive": True},
        width="100%",
        height="56px",
    )


# ---------------------------------------------------------------------------
# 2b. cumulative actual vs budget pace
# ---------------------------------------------------------------------------
def pace_chart(days: Sequence[int],
               actual_cum_cents: Sequence[Optional[float]],
               budget_cum_cents: Sequence[Optional[float]],
               dark: bool,
               y_title: str = "RM (cumulative)") -> go.Figure:
    """Actual line stops at the last reported day (None afterwards)."""
    pal = _pal(dark)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(days), y=[None if v is None else v / 100
                         for v in budget_cum_cents],
        mode="lines", name="Level 1 budget pace",
        line=dict(color=pal["budget"], dash="dot", width=1.5),
        hovertemplate="Day %{x}<br>Budget pace: RM %{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=list(days), y=[None if v is None else v / 100
                         for v in actual_cum_cents],
        mode="lines", connectgaps=False, name="Actual",
        line=dict(color=pal["accent"], width=2.5),
        fill="tozeroy", fillcolor=pal["pace_fill"],
        hovertemplate="Day %{x}<br>Actual: RM %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(**_base_layout(pal, y_title, x_title="Day of month"))
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
                     y_title: str = "RM") -> go.Figure:
    """None budget = no budget bar for that category."""
    pal = _pal(dark)
    fig = go.Figure()
    x = [wrap_label(c) for c in categories]
    names = list(categories)
    fig.add_trace(go.Bar(
        x=x, y=[None if b is None else b / 100 for b in budgets_cents],
        name="Budget", marker_color=pal["budget"], opacity=0.55,
        customdata=names,
        hovertemplate="%{customdata}<br>Budget: RM %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=x, y=[a / 100 for a in actuals_cents],
        name="Actual (paid)", marker_color=pal["accent"],
        customdata=names,
        hovertemplate="%{customdata}<br>Actual: RM %{y:,.2f}<extra></extra>",
    ))
    layout = _base_layout(pal, y_title)
    layout["barmode"] = "group"
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

    _bar(losses, "In Total Loss", pal["red"], 0.95)
    _bar(offsets, "Offsets Total Loss", pal["green"], 0.95)
    _bar(infos, "Info only", pal["info"], 0.35, suffix=" (not in Total Loss)")
    layout = _base_layout(pal, y_title, x_title="")
    fig.update_layout(**layout)
    fig.update_xaxes(type="category")
    return fig


# --- overview-only compact formatting (module pages keep exact values) ---
def fmt_money_compact(cents: Optional[float]) -> str:
    """Overview-only compact money: RM 191.3k / RM 660 / — for blank.

    Values < 10,000 are shown exact; >= 10,000 rounded to 0.1k (>= 1M as
    M). The exact value is always available via fmt_rm for tooltips.
    """
    from ducklab.design import fmt_rm_compact
    return fmt_rm_compact(cents)


def fmt_pct1(ratio: Optional[float]) -> str:
    """Overview-only: percentages to 1 decimal; dash for blank."""
    if ratio is None:
        return "—"
    return f"{float(ratio) * 100:.1f}%"
