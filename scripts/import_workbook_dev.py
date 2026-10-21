#!/usr/bin/env python3
"""Import the Store_Operations_Monitoring workbook into the DEV database.

Reads INPUT cells only (formulas/calculated columns are never imported),
writes through AppService as Super Admin (id=1) so validation applies.
Blank cell -> NULL; a typed 0 -> 0. Never turns a blank into 0.

  scripts/import_workbook_dev.py <workbook.xlsx> [--replace]
  scripts/import_workbook_dev.py <workbook.xlsx> --verify

--replace: wipe ONLY this store/month (daily records, stock losses,
expense transactions, month config, expense categories/budgets, payroll
fields) in dev.db, then re-import.
--verify:  read cached values (data_only=True) and compare with the app's
KPI engine (assemble_month) for the imported store/month.

Refuses any DB other than /home/duck/project/ducklab/.devdata/dev.db.
The workbook contains private data (names, salary): this script never
prints those values.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEV_DB = Path("/home/duck/project/ducklab/.devdata/dev.db")

SETUP_CELLS = {  # Setup!B-column inputs (B21-B24 are computed, not imported)
    "code": "B5", "name": "B6", "concept": "B7", "zone": "B8",
    "zone_pic": "B9", "branch_manager": "B10", "month": "B11",
    "min_rtm_target_cents": "B15", "level1_base_cents": "B16",
    "level2_pct": "B17", "level3_pct": "B18", "level4_pct": "B19",
    "sales_last_year_cents": "B25", "sales_last_month_cents": "B26",
    "salary_ratio_target": "B27", "budget_headcount": "B28",
    "turnover_threshold": "B29", "stock_loss_target": "B30",
    "opening_headcount_ft": "B46", "part_timer_factor": "B47",
    "salary_basic_allowance_cents": "B50", "overtime_cents": "B51",
    "support_team_cost_cents": "B52",
}
EXPENSE_BUDGET_ROWS = range(33, 43)  # A33:A42 names, B33:B42 budgets

DAILY_FIRST, DAILY_LAST = 7, 37  # skip example row 6
DAILY_COLS = {  # Daily column letter -> app field
    "C": "sales_cents", "D": "transactions", "E": "units",
    "AH": "resigned_ft", "AI": "new_hires_ft", "AJ": "ft_working",
    "AK": "pt_working", "AL": "leave_annual", "AM": "leave_mc",
    "AN": "leave_other_paid", "AO": "leave_unpaid",
}
STOCK_COLS = {  # Daily column -> stock code
    "T": "DMO", "U": "DMC", "V": "DMP", "W": "2DMP", "X": "DPI",
    "Y": "DMW", "Z": "DDR", "AA": "DMN", "AB": "DME", "AC": "DMQ",
    "AD": "DST", "AE": "MISSING", "AF": "EXTRA",
}
REGISTER_FIRST, REGISTER_LAST = 9, 508
REGISTER_COLS = {  # Expense Register column -> app field
    "A": "incurred_date", "B": "paid_date", "C": "_category_name",
    "D": "description", "E": "amount_cents", "F": "paid_by",
    "G": "status", "H": "receipt_ref", "I": "verification",
    "J": "recorded_by", "K": "remarks",
}


def _rm(v):
    """RM number -> integer cents; blank -> None; typed 0 -> 0."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return int(Decimal(str(v)) * 100)


def _num(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return int(v)


def _ratio(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return float(v)


def _date(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _text(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _cell(ws, coord):
    return ws[coord].value


def read_workbook(path: Path) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    setup = wb["Setup"]
    store = {k: _text(_cell(setup, c)) for k, c in SETUP_CELLS.items()
             if k in ("code", "name", "concept", "zone", "zone_pic",
                      "branch_manager")}
    month_d = _date(_cell(setup, SETUP_CELLS["month"]))
    month = month_d.strftime("%Y-%m")
    cfg = {
        "min_rtm_target_cents": _rm(_cell(setup, "B15")),
        "level1_base_cents": _rm(_cell(setup, "B16")),
        "level2_pct": _ratio(_cell(setup, "B17")),
        "level3_pct": _ratio(_cell(setup, "B18")),
        "level4_pct": _ratio(_cell(setup, "B19")),
        "sales_last_year_cents": _rm(_cell(setup, "B25")),
        "sales_last_month_cents": _rm(_cell(setup, "B26")),
        "salary_ratio_target": _ratio(_cell(setup, "B27")),
        "budget_headcount": _num(_cell(setup, "B28")),
        "turnover_threshold": _ratio(_cell(setup, "B29")),
        "stock_loss_target": _ratio(_cell(setup, "B30")),
        "opening_headcount_ft": _num(_cell(setup, "B46")),
        "part_timer_factor": _ratio(_cell(setup, "B47")),
        "salary_basic_allowance_cents": _rm(_cell(setup, "B50")),
        "overtime_cents": _rm(_cell(setup, "B51")),
        "support_team_cost_cents": _rm(_cell(setup, "B52")),
    }
    budgets = []
    for r in EXPENSE_BUDGET_ROWS:
        name = _text(_cell(setup, f"A{r}"))
        if name:
            budgets.append((name, _rm(_cell(setup, f"B{r}")), r - 32))

    daily = wb["Daily"]
    days = []
    for r in range(DAILY_FIRST, DAILY_LAST + 1):
        d = _date(_cell(daily, f"A{r}"))
        if d is None or d.strftime("%Y-%m") != month:
            continue
        row = {"date": d, "fields": {}, "stock": {}}
        for col, fld in DAILY_COLS.items():
            v = _cell(daily, f"{col}{r}")
            if fld in ("sales_cents",):
                row["fields"][fld] = _rm(v)
            else:
                row["fields"][fld] = _num(v)
        for col, code in STOCK_COLS.items():
            v = _rm(_cell(daily, f"{col}{r}"))
            if v is not None:
                row["stock"][code] = v
        days.append(row)

    reg = wb["Expense Register"]
    expenses = []
    for r in range(REGISTER_FIRST, REGISTER_LAST + 1):
        vals = {fld: _cell(reg, f"{col}{r}")
                for col, fld in REGISTER_COLS.items()}
        if not any(v is not None for v in vals.values()):
            continue
        expenses.append({
            "incurred_date": _date(vals["incurred_date"]),
            "paid_date": _date(vals["paid_date"]),
            "_category_name": _text(vals["_category_name"]),
            "description": _text(vals["description"]),
            "amount_cents": _rm(vals["amount_cents"]),
            "paid_by": _text(vals["paid_by"]),
            "status": _text(vals["status"]),
            "receipt_ref": _text(vals["receipt_ref"]),
            "verification": _text(vals["verification"]),
            "recorded_by": _text(vals["recorded_by"]),
            "remarks": _text(vals["remarks"]),
        })
    return {"store": store, "month": month, "config": cfg,
            "budgets": budgets, "days": days, "expenses": expenses}


def do_import(db_path: Path, wb_path: Path, replace: bool) -> dict:
    if db_path != DEV_DB:
        raise SystemExit(f"Refusing DB path {db_path}: only {DEV_DB} allowed")
    from ducklab.app_service import AppService
    from ducklab.operations_write_repository import DuplicateRecordError

    data = read_workbook(wb_path)
    month = data["month"]
    app = AppService(db_path)
    admin = app.auth.get_user(1)

    st = data["store"]
    try:
        sid = app.register_store(admin, st["code"], st["name"] or st["code"],
                                 concept=st["concept"], zone=st["zone"],
                                 zone_pic=st["zone_pic"],
                                 branch_manager=st["branch_manager"])
    except DuplicateRecordError:
        sid = app.read_repo.get_store_by_code(st["code"]).id
        app.update_store_details(
            admin, sid, name=st["name"] or st["code"], concept=st["concept"],
            zone=st["zone"], zone_pic=st["zone_pic"],
            branch_manager=st["branch_manager"])
    if sid not in admin.store_ids:
        app.assign_store(admin, admin.id, sid)

    if replace:
        _wipe_store_month(db_path, sid, month)

    cfg = {k: v for k, v in data["config"].items() if v is not None}
    app.configure_month(admin, sid, month, **cfg)

    cat_ids = {}
    for name, budget, order in data["budgets"]:
        try:
            cid = app.add_expense_category(admin, sid, name, sort_order=order)
        except DuplicateRecordError:
            cid = next(c.id for c in app.get_expense_categories(admin, sid)
                       if c.name == name)
        cat_ids[name] = cid
        app.set_expense_budget(admin, sid, month, cid, budget)

    n_days = 0
    for d in data["days"]:
        fields = {k: v for k, v in d["fields"].items() if v is not None}
        stock = d["stock"] or None
        if not fields and not stock:
            continue
        app.record_daily(admin, sid, d["date"],
                         stock_losses=stock, **fields)
        n_days += 1

    n_exp = 0
    for e in data["expenses"]:
        fields = dict(e)
        cname = fields.pop("_category_name")
        fields["category_id"] = cat_ids.get(cname) if cname else None
        if _expense_exists(db_path, sid, e):
            continue
        app.record_expense(admin, sid, **fields)
        n_exp += 1

    return {"store": st["code"], "month": month, "store_id": sid,
            "daily_rows": n_days, "expense_rows": n_exp,
            "categories": len(cat_ids)}


def _expense_exists(db_path: Path, store_id: int, e: dict) -> bool:
    """Natural-key match for idempotency (dates+amount+description+ref)."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM expense_transactions WHERE store_id=? AND "
            "ifnull(incurred_date,'')=? AND ifnull(paid_date,'')=? AND "
            "ifnull(amount_cents,-999999999)=? AND "
            "ifnull(description,'')=? AND ifnull(receipt_ref,'')=?",
            (store_id, e["incurred_date"].isoformat()
             if e["incurred_date"] else "",
             e["paid_date"].isoformat() if e["paid_date"] else "",
             e["amount_cents"] if e["amount_cents"] is not None
             else -999999999,
             e["description"] or "", e["receipt_ref"] or ""),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _wipe_store_month(db_path: Path, store_id: int, month: str) -> None:
    """Remove ONLY this store/month's operational rows (dev.db only)."""
    if db_path != DEV_DB:
        raise SystemExit(f"Refusing DB path {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM daily_records WHERE store_id=? AND "
            "strftime('%Y-%m', date)=?", (store_id, month))]
        if ids:
            q = ",".join("?" * len(ids))
            conn.execute(
                f"DELETE FROM daily_stock_losses WHERE daily_id IN ({q})",
                ids)
        conn.execute("DELETE FROM daily_records WHERE store_id=? AND "
                     "strftime('%Y-%m', date)=?", (store_id, month))
        conn.execute("DELETE FROM expense_transactions WHERE store_id=? AND "
                     "(strftime('%Y-%m', incurred_date)=? OR "
                     "strftime('%Y-%m', paid_date)=?)",
                     (store_id, month, month))
        conn.execute("DELETE FROM monthly_expense_budgets WHERE store_id=? "
                     "AND monthly_config_id IN (SELECT id FROM "
                     "monthly_configs WHERE store_id=? AND month=?)",
                     (store_id, store_id, month))
        conn.execute("DELETE FROM monthly_configs WHERE store_id=? AND month=?",
                     (store_id, month))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------
def verify(db_path: Path, wb_path: Path) -> int:
    if db_path != DEV_DB:
        raise SystemExit(f"Refusing DB path {db_path}: only {DEV_DB} allowed")
    import openpyxl
    from ducklab.app_service import AppService
    from ducklab import kpi

    data = read_workbook(wb_path)
    month = data["month"]
    code = data["store"]["code"]
    app = AppService(db_path)
    admin = app.auth.get_user(1)
    sid = app.read_repo.get_store_by_code(code).id

    wbv = openpyxl.load_workbook(wb_path, data_only=True)
    ws_sum = wbv["Summary"]
    ws_mon = wbv["Expense Monitor"]

    rep = app.preview_month(admin, sid, month,
                            as_of=date(2026, 9, 30))
    s, e, st, mp = rep.sales, rep.expenses, rep.stock, rep.manpower
    days = app.get_daily_records(admin, sid, month)
    exps = [r.input for r in app.get_expense_transactions(admin, sid)]

    rows = []  # (metric, workbook, app, match)

    def dec(x):
        return None if x is None else Decimal(str(x))

    def add(metric, wb_val, app_val, tol=Decimal("0.01")):
        w, a = dec(wb_val), dec(app_val)
        if w is None and a is None:
            ok = True
        elif w is None or a is None:
            ok = False
        else:
            ok = abs(w - a) <= tol * max(abs(w), Decimal(1))
        rows.append((metric, wb_val, app_val, ok))

    # sales
    add("Days with sales entered", ws_sum["B14"].value, s.days_entered)
    add("Sales MTD (RM)", ws_sum["B15"].value, s.sales_mtd_cents / 100)
    add("Month-end sales (RM)", ws_sum["B16"].value,
        (s.month_end_sales_cents / 100) if s.month_end_sales_cents else None)
    lv = s.targets
    for i, name in enumerate(("level1", "level2", "level3", "level4"), 17):
        add(f"Target Level {i-16} (RM)", ws_sum[f"C{i}"].value,
            (lv[f"level{i-16}"] / 100) if lv.get(f"level{i-16}") else None)
        add(f"Achievement L{i-16}", ws_sum[f"D{i}"].value,
            s.achievement_by_level.get(f"level{i-16}"))
        wb_status = ws_sum[f"E{i}"].value
        rows.append((f"Status L{i-16}", wb_status,
                     s.status_by_level.get(f"level{i-16}"),
                     wb_status == s.status_by_level.get(f"level{i-16}")))
    add("Growth vs last year", ws_sum["D21"].value, s.growth_last_year)
    add("Growth vs last month", ws_sum["D22"].value, s.growth_last_month)
    add("SPR (RM)", ws_sum["B25"].value, (s.spr / 100) if s.spr else None)
    add("UPT", ws_sum["B26"].value, s.upt)
    add("ASP (RM)", ws_sum["B27"].value, (s.asp / 100) if s.asp else None)

    # expenses (headline)
    add("Paid expenses (RM)", ws_mon["B8"].value, e.paid_cents / 100)
    add("Incurred expenses (RM)", ws_mon["E8"].value, e.incurred_cents / 100)
    add("Open / unpaid (RM)", ws_mon["H8"].value, e.open_cents / 100)
    add("Needs review (lines)", ws_mon["H9"].value,
        sum(v for k, v in e.validation_counts.items() if k != "OK"))
    add("Total budget (RM)", ws_mon["B9"].value,
        (e.total_budget_cents / 100) if e.total_budget_cents else None)
    wb_te_status = ws_sum["E40"].value
    rows.append(("Total expenses status (Summary!E40)", wb_te_status,
                 e.total_expenses_status,
                 wb_te_status == e.total_expenses_status))
    # Daily Dashboard!H5: MTD / Level 1 (vs projected month-end / Level 1)
    l1 = s.targets.get("level1")
    add("Budget achievement MTD (Daily Dashboard!H5)",
        (ws_sum["B15"].value / ws_sum["C17"].value)
        if ws_sum["B15"].value and ws_sum["C17"].value else None,
        s.mtd_achievement_l1)
    add("Projected achievement (month-end / Level 1)",
        (ws_sum["B16"].value / ws_sum["C17"].value)
        if ws_sum["B16"].value and ws_sum["C17"].value else None,
        s.achievement_by_level.get("level1"))

    # per-category paid vs budget (Expense Monitor rows 15-25)
    cat_rows = {c.name: c for c in e.categories}
    for r in range(15, 26):
        name = ws_mon[f"A{r}"].value
        if not name or name == "TOTAL STORE EXPENSES":
            continue
        c = cat_rows.get(name)
        if c is None:
            rows.append((f"Category {name}: present", True, False, False))
            continue
        # per-category incurred/open come from kpi.py over the register
        # (the assembled section carries paid + status per category)
        inc = sum(kpi.incurred_value_cents(x) for x in exps
                  if x.category_id == c.category_id
                  and x.incurred_date is not None
                  and x.incurred_date.strftime("%Y-%m") == month)
        opn = sum(kpi.pending_value_cents(x) for x in exps
                  if x.category_id == c.category_id)
        add(f"{name} paid (RM)", ws_mon[f"C{r}"].value,
            c.paid_mtd_cents / 100)
        add(f"{name} incurred (RM)", ws_mon[f"D{r}"].value, inc / 100)
        add(f"{name} open (RM)", ws_mon[f"E{r}"].value, opn / 100)
        add(f"{name} budget (RM)", ws_mon[f"B{r}"].value,
            (c.budget_cents / 100) if c.budget_cents else None)
        wb_status = ws_mon[f"H{r}"].value
        rows.append((f"{name} budget status", wb_status, c.budget_status,
                     wb_status == c.budget_status))

    # stock
    add("Total stock loss (RM)", ws_sum["B63"].value,
        st.total_loss_cents / 100)
    add("Stock loss ratio", ws_sum["D63"].value, st.loss_pct_of_sales)
    add("Stock loss target", ws_sum["C63"].value, 0.015)
    wb_status = ws_sum["E63"].value
    rows.append(("Stock loss status", wb_status, st.status,
                 wb_status == st.status))
    for code, r in (("DMO", 58), ("DMC", 59), ("DMP", 60), ("MISSING", 61),
                    ("EXTRA", 62), ("2DMP", 65), ("DPI", 66), ("DMW", 67),
                    ("DDR", 68), ("DMN", 69), ("DME", 70), ("DMQ", 71),
                    ("DST", 72)):
        add(f"Stock {code} (RM)", ws_sum[f"B{r}"].value,
            st.totals_by_code.get(code, 0) / 100)

    # manpower
    add("Salary ratio (store)", ws_sum["B44"].value, mp.salary_ratio_store)
    add("Turnover %", ws_sum["B53"].value, mp.turnover_pct)
    wb_status = ws_sum["E53"].value
    rows.append(("Turnover status", wb_status, mp.turnover_status,
                 wb_status == mp.turnover_status))
    add("Leave days", ws_sum["B55"].value, mp.leave_days)
    cfg_now = app.get_month_config(admin, sid, month)
    add("Budget headcount", ws_sum["B46"].value, cfg_now.budget_headcount)
    add("Closing headcount", ws_sum["B50"].value, mp.closing_headcount)
    wb_status = ws_sum["E51"].value
    rows.append(("Headcount vs budget status", wb_status,
                 mp.headcount_status, wb_status == mp.headcount_status))

    # output: mismatches first
    mism = [r for r in rows if not r[3]]
    ok = [r for r in rows if r[3]]
    print(f"\n{'METRIC':38} {'WORKBOOK':>16} {'APP':>16}  RESULT")
    print("-" * 80)
    for metric, w, a, _ in mism + ok:
        w_s = "—" if w is None else (f"{w:,.4f}" if isinstance(w, float)
                                     else str(w))
        a_s = "—" if a is None else (f"{float(a):,.4f}"
                                     if isinstance(a, Decimal)
                                     and a != a.to_integral() else str(a))
        print(f"{metric:38} {w_s:>16} {a_s:>16}  "
              f"{'MATCH' if _ else 'MISMATCH'}")
    print("-" * 80)
    print(f"{len(ok)} match, {len(mism)} mismatch")
    return 1 if mism else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook", type=Path)
    ap.add_argument("--replace", action="store_true",
                    help="wipe this store/month in dev.db before importing")
    ap.add_argument("--verify", action="store_true",
                    help="compare workbook cached values vs app KPI engine")
    args = ap.parse_args()

    if not args.workbook.exists():
        raise SystemExit(f"Workbook not found: {args.workbook}")

    if args.verify:
        return verify(DEV_DB, args.workbook)

    result = do_import(DEV_DB, args.workbook, args.replace)
    print(f"Imported store {result['store']} month {result['month']} "
          f"(id={result['store_id']}): "
          f"{result['daily_rows']} daily rows, "
          f"{result['expense_rows']} expense rows, "
          f"{result['categories']} categories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
