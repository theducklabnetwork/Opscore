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
  info-only categories visually distinct and labelled "not in Total Loss".
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
    grid="#31353B",        # slate-6
    accent="#6E6BD8",      # indigo-9
    accent_fill="rgba(110,107,216,0.18)",
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
    grid="#DFE0E2",
    accent="#5B5BD6",
    accent_fill="rgba(91,91,214,0.14)",
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


def chart(fig_json):
    """Render a builder's figure dict (may be a State Var)."""
    import reflex as rx
    return rx.plotly(
        data=fig_json,
        config=_CONFIG,
        use_resize_handler=True,
        width="100%",
        height="340px",
    )

FONT_FAMILY = ("ui-sans-serif, system-ui, -apple-system, 'Segoe UI', "
               "Roboto, sans-serif")


def _pal(dark: bool) -> dict:
    return _DARK if dark else _LIGHT


def fmt_rm(cents: Optional[float]) -> str:
    """RM 1,234.50 with thousands separators; dash for missing."""
    if cents is None:
        return "—"
    return f"RM {cents / 100:,.2f}"


def fmt_rm0(cents: Optional[float]) -> str:
    if cents is None:
        return "—"
    return f"RM {cents / 100:,.0f}"


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
        xaxis=dict(title=x_title, gridcolor=pal["grid"],
                   zerolinecolor=pal["grid"], color=pal["muted"]),
        yaxis=dict(title=y_title, gridcolor=pal["grid"],
                   zerolinecolor=pal["grid"], color=pal["muted"]),
        legend=dict(orientation="h", yanchor="top", y=-0.22,
                    xanchor="center", x=0.5, font=dict(size=11)),
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
               target_rm: Optional[float] = None) -> go.Figure:
    """One value per calendar day; None -> gap (no interpolation)."""
    pal = _pal(dark)
    hover = [f"{lbl}<br>{name}: {fmt_rm(v * 100 if v is not None else None)}"
             for lbl, v in zip(day_labels, values_rm)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(day_labels), y=[None if v is None else v for v in values_rm],
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
    fig.update_layout(**_base_layout(pal, y_title))
    return fig


# ---------------------------------------------------------------------------
# 2. cumulative actual vs budget pace
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
        fill="tozeroy", fillcolor=pal["accent_fill"],
        hovertemplate="Day %{x}<br>Actual: RM %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(**_base_layout(pal, y_title, x_title="Day of month"))
    return fig


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
    fig.add_trace(go.Bar(
        x=list(categories),
        y=[None if b is None else b / 100 for b in budgets_cents],
        name="Budget", marker_color=pal["budget"], opacity=0.55,
        hovertemplate="%{x}<br>Budget: RM %{y:,.2f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=list(categories), y=[a / 100 for a in actuals_cents],
        name="Actual (paid)", marker_color=pal["accent"],
        hovertemplate="%{x}<br>Actual: RM %{y:,.2f}<extra></extra>",
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
