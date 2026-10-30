"""Shared presentation mapping for live and finalized monthly reports.

This module formats already-calculated report data only. It performs no KPI
calculations beyond the existing snapshot display compatibility rules.
"""

from __future__ import annotations

import calendar

from ducklab import authz, kpi, store_ops_db
from ducklab import design as ds
from ducklab.operations_snapshots import SnapshotNotFoundError


def month_label(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{calendar.month_name[m]} {y}"


def _version_label(row: dict) -> str:
    """Version label based on the active-head link, never version order."""
    label = (f"Version {row['version']} — "
             f"{ds.fmt_kl_datetime(row['created_at'])}")
    if "superseded_by" in row:
        label += " — " + ("Superseded" if row["superseded_by"] is not None
                          else "Current")
    return label


def _version_for_label(label: str, version_rows: list[dict]) -> int:
    """Map a picker label back to its version number. Labels arrive over
    the socket, so anything not in the current list is rejected."""
    for row in version_rows:
        if _version_label(row) == label:
            return int(row["version"])
    raise SnapshotNotFoundError(
        "Unknown report version — choose one from the list.")


def _payroll_gated(values: dict, user) -> dict:
    """Only configuration managers receive payroll figures in report state."""
    if user is not None and authz.can_manage_config(user):
        return values
    return {**values, "salary": "Restricted", "ratio": "Restricted",
            "ratio_support": "Restricted", "ratio_status": "Restricted"}


_SIGNS = {code: sign for code, _name, sign in store_ops_db.STOCK_LOSS_CODES}


def _report_codes(totals: dict) -> list[dict]:
    """Map stock contribution signs to their display treatment labels."""
    rows = []
    for code, total in sorted(totals.items()):
        if not total:
            continue
        sign = _SIGNS.get(code, 0)
        label = ds.TREATMENT[sign]
        rows.append({"code": code, "total": ds.fmt_rm(total),
                     "counts": "1" if sign else "0", "treatment": label,
                     "treatment_color": ds.status_color(label)})
    return rows


def _report_expenses(paid, incurred, open_, budget, share, paid_count,
                     incurred_count) -> dict:
    """Format expense amounts, preserving empty-state semantics."""
    no_paid = paid_count == 0
    no_incurred = incurred_count == 0
    return {
        "paid": "—" if no_paid else ds.fmt_rm(paid),
        "paid_note": "No payments yet" if no_paid else "",
        "incurred": "—" if no_incurred else ds.fmt_rm(incurred),
        "incurred_note": "Not reported yet" if no_incurred else "",
        "pending": ds.fmt_rm(open_),
        "budget": ds.fmt_rm(budget),
        "share": "—" if no_paid else ds.fmt_pct(share),
    }


def _salary_status(status: str, complete: bool) -> str:
    if not complete and status in (kpi.SALARY_WITHIN, kpi.SALARY_FAILURE):
        return f"Month to date: {status}"
    return status


def _projection_card_labels(pstate: str) -> dict:
    return {
        "achievement_label": ds.projection_label(pstate,
                                                 "Achievement vs Target"),
        "growth_year_label": ds.projection_label(pstate,
                                                 "Growth vs Last Year"),
        "growth_month_label": ds.projection_label(pstate,
                                                  "Growth vs Last Month"),
    }


def _effective_staff_display(value, factor_missing: bool) -> str:
    if factor_missing:
        return kpi.PT_FACTOR_NOT_SET
    return ds.fmt_num(float(value) if value is not None else None)


def _present_report(*, mode: str, store_label: str, store_title: str,
                    month: str, pstate: str, entered: int, dim: int,
                    complete: bool, sales: dict, expenses: dict,
                    manpower: dict, stock: dict, actions: dict,
                    user=None) -> dict:
    """Build the common UI payload from normalized section values."""
    return {
        "mode": mode,
        "store": store_label,
        "store_title": store_title,
        "month": month_label(month),
        "sales": {
            "mtd": ds.fmt_rm(sales["mtd_cents"]),
            "projected": ds.fmt_rm(
                int(sales["month_end_cents"])
                if sales["month_end_cents"] is not None else None),
            "target_l1": ds.fmt_rm(sales["target_l1"]),
            "achievement_l1": ds.fmt_achievement(
                sales["achievement_l1"], sales["status_l1"]),
            "status_l1": ds.projection_style(
                pstate, sales["status_l1"] or "no target")[0],
            "projection_state": pstate,
            "early_estimate_tag": (
                f"Early estimate: {entered} of {dim} days"
                if pstate == "early_estimate" else ""),
            **_projection_card_labels(pstate),
            "growth_year": ds.fmt_pct(sales["growth_year"]),
            "growth_year_status": ds.projection_style(
                pstate, sales["growth_year_status"])[0],
            "growth_month": ds.fmt_pct(sales["growth_month"]),
            "growth_month_status": ds.projection_style(
                pstate, sales["growth_month_status"])[0],
            "spr": ds.fmt_rm(int(sales["spr"])
                             if sales["spr"] is not None else None),
            "upt": ds.fmt_num(float(sales["upt"])
                              if sales["upt"] is not None else None),
            "asp": ds.fmt_rm(int(sales["asp"])
                             if sales["asp"] is not None else None),
            "completeness": f"{entered} of {dim} days reported",
            "complete": complete,
        },
        "expenses": {
            **_report_expenses(
                expenses["paid_cents"], expenses["incurred_cents"],
                expenses["open_cents"], expenses["budget_cents"],
                expenses["share"], expenses["paid_count"],
                expenses["incurred_count"]),
            **({"budget_control_status": expenses["budget_control_status"]}
               if mode == "live" else {}),
            "hq_status": expenses["hq_status"],
            "validation": dict(expenses["validation"]),
            "categories": [
                {"name": c["name"], "paid": ds.fmt_rm(c["paid_cents"]),
                 "budget": ds.fmt_rm(c["budget_cents"]),
                 "status": c["status"]}
                for c in expenses["categories"]
            ],
        },
        "manpower": _payroll_gated({
            "opening": ds.fmt_num(manpower["opening"]),
            "hires": ds.fmt_num(manpower["hires"]),
            "resigned": ds.fmt_num(manpower["resigned"]),
            "closing": ds.fmt_num(manpower["closing"]),
            "salary": ds.fmt_rm(manpower["salary_cents"]),
            "ratio": ds.fmt_pct(manpower["ratio"]),
            "ratio_support": ds.fmt_pct(manpower["ratio_support"]),
            "ratio_status": _salary_status(manpower["ratio_status"], complete),
            "ratio_mtd": "" if complete else "1",
            "turnover": ds.fmt_pct(manpower["turnover"]),
            "turnover_status": manpower["turnover_status"],
            "leave_days": ds.fmt_num(manpower["leave_days"]),
            "effective_staff": _effective_staff_display(
                manpower["effective_staff"], manpower["pt_factor_missing"]),
            "pt_factor_label": ds.pt_factor_label(manpower["pt_factor"]),
        }, user),
        "stock": {
            "total_loss": ds.fmt_rm(stock["total_loss_cents"]),
            "loss_pct": ds.fmt_pct(stock["loss_pct"]),
            "status": stock["status"],
            "codes": _report_codes(stock["totals_by_code"]),
        },
        "actions": {
            "total": actions["counts"].get("total", 0),
            "open": actions["open"],
            "in_progress": actions["counts"].get("in_progress", 0),
            "blocked": actions["counts"].get("blocked", 0),
            "done": actions["counts"].get("done", 0),
            "overdue": actions["counts"].get("overdue", 0),
            "completion": ds.fmt_pct(actions["completion_pct"]),
        },
    }


def serialize_live(rep, user=None) -> dict:
    """Map a typed live report to the monthly report UI payload."""
    s, e, m, st, a = (rep.sales, rep.expenses, rep.manpower, rep.stock,
                      rep.actions)
    entered, dim = s.days_entered, s.days_in_month
    return _present_report(
        mode="live", store_label=f"{rep.store.code} — {rep.store.name}",
        store_title=f"{rep.store.name} ({rep.store.code})", month=rep.month,
        pstate=s.projection_state, entered=entered, dim=dim,
        complete=entered == dim,
        sales={
            "mtd_cents": s.sales_mtd_cents,
            "month_end_cents": s.month_end_sales_cents,
            "target_l1": s.targets.get("level1"),
            "achievement_l1": s.achievement_by_level.get("level1"),
            "status_l1": s.status_by_level.get("level1", ""),
            "growth_year": s.growth_last_year,
            "growth_year_status": s.growth_status_last_year,
            "growth_month": s.growth_last_month,
            "growth_month_status": s.growth_status_last_month,
            "spr": s.spr, "upt": s.upt, "asp": s.asp,
        },
        expenses={
            "paid_cents": e.paid_cents, "incurred_cents": e.incurred_cents,
            "open_cents": e.open_cents, "budget_cents": e.total_budget_cents,
            "share": e.expense_share_of_sales, "paid_count": e.paid_count,
            "incurred_count": e.incurred_count,
            "budget_control_status": e.budget_control_status,
            "hq_status": e.total_expenses_status,
            "validation": e.validation_counts,
            "categories": [
                {"name": c.name, "paid_cents": c.paid_mtd_cents,
                 "budget_cents": c.budget_cents, "status": c.budget_status}
                for c in e.categories
            ],
        },
        manpower={
            "opening": m.opening_headcount, "hires": m.hires,
            "resigned": m.resigned, "closing": m.closing_headcount,
            "salary_cents": m.total_salary_cents,
            "ratio": m.salary_ratio_store,
            "ratio_support": m.salary_ratio_incl_support,
            "ratio_status": m.salary_ratio_status,
            "turnover": m.turnover_pct, "turnover_status": m.turnover_status,
            "leave_days": m.leave_days,
            "effective_staff": m.average_effective_staff,
            "pt_factor_missing": m.pt_factor_missing,
            "pt_factor": rep.config.part_timer_factor,
        },
        stock={
            "total_loss_cents": st.total_loss_cents,
            "loss_pct": st.loss_pct_of_sales,
            "status": kpi.stock_status_label(
                st.total_loss_cents, s.sales_mtd_cents,
                rep.config.stock_loss_target, entered),
            "totals_by_code": st.totals_by_code,
        },
        actions={"counts": a.counts, "open": a.open,
                 "completion_pct": a.completion_pct},
        user=user,
    )


def serialize_snapshot(snap: dict, user=None) -> dict:
    """Map current and legacy snapshot payloads to the same UI payload."""
    rep = snap["report"]
    s, e, m, st, a = (rep["sales"], rep["expenses"], rep["manpower"],
                      rep["stock"], rep["actions"])
    cfg = rep.get("config") or {}
    store = rep.get("store", {})
    has_store = isinstance(store, dict) and "code" in store
    entered = s.get("days_entered") or 0
    dim = s.get("days_in_month") or 0
    pstate = kpi.projection_state(entered, dim)
    growth_year_status = s.get("growth_status_last_year") or kpi.growth_status(
        s.get("growth_last_year"), entered)
    growth_month_status = s.get("growth_status_last_month") or kpi.growth_status(
        s.get("growth_last_month"), entered)
    return _present_report(
        mode="finalized",
        store_label=f"{store['code']} — {store['name']}" if has_store else "",
        store_title=f"{store['name']} ({store['code']})" if has_store else "",
        month=rep.get("month", snap["month"]), pstate=pstate,
        entered=entered, dim=dim, complete=entered == dim,
        sales={
            "mtd_cents": s.get("sales_mtd_cents"),
            "month_end_cents": s.get("month_end_sales_cents"),
            "target_l1": s.get("targets", {}).get("level1"),
            "achievement_l1": s.get("achievement_by_level", {}).get("level1"),
            "status_l1": s.get("status_by_level", {}).get("level1", ""),
            "growth_year": s.get("growth_last_year"),
            "growth_year_status": growth_year_status,
            "growth_month": s.get("growth_last_month"),
            "growth_month_status": growth_month_status,
            "spr": s.get("spr"), "upt": s.get("upt"), "asp": s.get("asp"),
        },
        expenses={
            "paid_cents": e.get("paid_cents"),
            "incurred_cents": e.get("incurred_cents"),
            "open_cents": e.get("open_cents"),
            "budget_cents": e.get("total_budget_cents"),
            "share": e.get("expense_share_of_sales"),
            "paid_count": e.get("paid_count"),
            "incurred_count": e.get("incurred_count"),
            "hq_status": e.get("total_expenses_status", ""),
            "validation": e.get("validation_counts", {}),
            "categories": [
                {"name": c["name"], "paid_cents": c.get("paid_mtd_cents"),
                 "budget_cents": c.get("budget_cents"),
                 "status": c.get("budget_status", "")}
                for c in e.get("categories", [])
            ],
        },
        manpower={
            "opening": m.get("opening_headcount"), "hires": m.get("hires"),
            "resigned": m.get("resigned"), "closing": m.get("closing_headcount"),
            "salary_cents": m.get("total_salary_cents"),
            "ratio": m.get("salary_ratio_store"),
            "ratio_support": m.get("salary_ratio_incl_support"),
            "ratio_status": m.get("salary_ratio_status", ""),
            "turnover": m.get("turnover_pct"),
            "turnover_status": m.get("turnover_status", ""),
            "leave_days": m.get("leave_days"),
            "effective_staff": m.get("average_effective_staff"),
            "pt_factor_missing": m.get("pt_factor_missing", False),
            "pt_factor": cfg.get("part_timer_factor"),
        },
        stock={
            "total_loss_cents": st.get("total_loss_cents"),
            "loss_pct": st.get("loss_pct_of_sales"),
            "status": (kpi.stock_status_label(
                st.get("total_loss_cents") or 0, s.get("sales_mtd_cents"),
                cfg.get("stock_loss_target"), entered)
                if cfg else st.get("status", "")),
            "totals_by_code": st.get("totals_by_code", {}),
        },
        actions={"counts": a.get("counts", {}), "open": a.get("open"),
                 "completion_pct": a.get("completion_pct")},
        user=user,
    )
