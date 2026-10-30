# Phase 3.6 — Flatten Page State Inheritance: Dependency Matrix (audit)

Audit produced before any code movement (Phase 3.6 step 1).
**No code has been moved. This document is the review gate.**

## 0. Reflex 0.10 runtime mechanics that shape the design

Verified against `.venv` (reflex 0.10.0, `reflex_base.vars.base.Field._get_raw`,
`reflex.state`):

- **Vars resolve to the owning class's instance.** Each `Field` carries
  `_owner` = the class that *declared* it. Reading `self.x` walks the
  `parent_state` chain to the owner's instance. Pages currently read e.g.
  `DailyState.selected_store_label`, but the value lives on the **OpsState
  instance**.
- **Event namespaces are owner-based.** `DailyState.refresh_report()`
  serializes as `...ops_state.refresh_report` because the handler is defined
  on `OpsState`. The state full name embeds module path + class name
  (`ducklab___ops_state____ops_state`).
- Consequences:
  - Moving a member to another class changes its compiled namespace → the
    component reference must be updated **in the same commit** (Phase 3.6
    step 4).
  - A flat `SharedOpsState` works the same way the chain does today: all
    page states become siblings under one `SharedOpsState` instance, so
    shared vars (selectors, status) are still a single source of truth.
  - Cross-state event yields (`yield OpsState.load_ops()` from a page
    state) resolve by name at runtime and keep working between siblings.

## 1. Current inheritance (before)

```
AuthState (auth + theme: is_dark, toggle_is_dark)
└── OpsState                       (shared selectors + tours + Report page)
    ├── DailyState                 (/ops/daily)
    │   └── ExpensesState          (/ops/expenses)
    │       └── ScheduleState      (schedule tab, embedded in /ops/manpower)
    │           └── ManpowerState  (/ops/manpower)
    │               └── StockState (/ops/stock)
    │                   └── AssignmentState (store mgmt: user assignment)
    │                       └── SetupState  (store mgmt: Monthly Setup)
    │                           └── StoresState (/ops/stores)
    │                               ├── ActionsState   (/ops/actions)
    │                               │   └── OverviewState (/ops dashboard)
    │                               └── SalesState (/ops/sales)
    └── SettingsState              (/ops/settings)
```

Routes / on-load wiring (`ducklab/ducklab.py`):

| Route | Page state | on_load |
|---|---|---|
| `/ops` | OverviewState | `OverviewState.load_overview`, `OverviewState.maybe_start_tour`, `OpsState.maybe_start_page_tour("dashboard")` |
| `/ops/report` | OpsState | `OpsState.load_ops`, `OpsState.maybe_start_page_tour("report")` |
| `/ops/daily` | DailyState | `DailyState.load_daily`, `...+("daily")` |
| `/ops/expenses` | ExpensesState | `ExpensesState.load_expenses`, `...+("expenses")` |
| `/ops/manpower` | ManpowerState | `ManpowerState.load_manpower`, `...+("manpower")` |
| `/ops/stock` | StockState | `StockState.load_stock`, `...+("stock")` |
| `/ops/stores` | StoresState | `StoresState.load_stores`, `...+("stores")` |
| `/ops/sales` | SalesState | `SalesState.load_sales`, `...+("sales")` |
| `/ops/actions` | ActionsState | `ActionsState.load_actions`, `...+("actions")` |
| `/ops/settings` | SettingsState | `SettingsState.load_settings`, `...+("settings")` |

## 2. Shared surface (all pages) — `ops_shell.py` + routes + theme

| Reference | Current owner | Destination |
|---|---|---|
| `store_options`, `dashboard_store_options`, `selected_store_label`, `month_options`, `selected_month_label`, `has_stores`, `selected_store_active` | OpsState | **SharedOpsState** |
| `select_store`, `select_month` (shell defaults; pages pass own overrides via `on_store=`/`on_month=`) | OpsState | **SharedOpsState** (base impl); page overrides stay on page classes |
| `tour_finished`, `page_tour_finished`, `replay_page_tour` (+ hidden `#tour-done-trigger*` buttons — element IDs unchanged) | OpsState | **SharedOpsState** |
| `maybe_start_page_tour` (routes) | OpsState | **SharedOpsState** (routes updated in same commit) |
| `display_name`, `sign_out`, `toggle_is_dark`/`is_dark` (theme) | AuthState | stays on AuthState |
| `error_message`, `report_status` ("shared error surface", values `idle/loading/ok/empty/error`) | OpsState | **SharedOpsState** |
| `load_ops` selector-init part (auth guard, stores/month options, no-store message) | OpsState | **SharedOpsState** (see §6 Q1); report refresh stays on OpsState |

## 3. Per-page dependency matrix

Legend: **component refs** = `XxxState.member` used by the page module (via its
alias); **inherited use** = `self.*` reads/writes inside the state class that
are owned by an ancestor today; **explicit** = `OtherState.xxx` references.
"→ sever" marks couplings that disappear in the flat shape.

### 3.1 Actions (`/ops/actions`) — `ActionsState(StoresState)`

- Component refs (46): all **ActionsState-owned** (`rows`, `n_open/n_overdue/n_done`,
  `f_*` filters, `a_*` form fields, `open_add/open_edit/save_action/set_form_open`,
  `filtered_rows`, `notice`, `form_error`, `saving`, `loaded`, `can_write_actions`,
  `select_store/month` overrides).
- Inherited use: `_actor`, `require_auth` (AuthState); `_clear_report`,
  `_selected_month`, `_selected_store_id`, `error_message`, `has_stores`,
  `report_status`, `selected_store_active`, `selected_store_label`,
  `selected_month_label` (OpsState → SharedOpsState).
- Explicit: `OpsState.load_ops` (→ shared loader).
- **From StoresState/SetupState/…: nothing.** Linchpin only. → flatten directly.

### 3.2 Stock (`/ops/stock`) — `StockState(ManpowerState)`

- Component refs (32): all StockState-owned (`kpi_*`, `st_*`, `cat_rows`,
  `day_rows`, `inv_rows`, `cmp_*`, `chart_fig_*`, `comp_fig_*`, `selected_code*`,
  `select_category`, `toggle_stock_hidden_days`, overrides) + `is_dark` (AuthState).
- Inherited use: AuthState pair; OpsState shared set; **`ManpowerState._clear_manpower` → sever.**
- Explicit: `OpsState.load_ops`.

### 3.3 Daily (`/ops/daily`) — `DailyState(OpsState)`

- Component refs (43): all DailyState-owned (`day_rows*`, `day_kpis`, `form_*`,
  `f_*`, `stock_form`, schedule-preview block, `record_day_*`, `can_write`,
  `month_finalized`, `save_entry`, `select_store/month` overrides) +
  `error_message`, `report_status` (OpsState → SharedOpsState).
- Inherited use: AuthState pair; OpsState shared set (`_clear_report`,
  `_selected_month`, `_selected_store_id`, …).
- Explicit: `OpsState.load_ops`; yields `…refresh_report()` (today
  `DailyState.refresh_report` ≈ owner OpsState; see §6 Q2).

### 3.4 Expenses (`/ops/expenses`) — `ExpensesState(DailyState)`

- Component refs (80): all ExpensesState-owned (`exp_kpis`, `budget_rows`,
  `budget_fig_*`, `mix_*`, filters `filter_*`/`search_text`/`sort_*`,
  `e_*` form, `b_*` budget form, `save_expense/save_budget/clear_budget*`,
  overrides) + `is_dark` (AuthState) + `error_message`, `report_status` (→ Shared).
- Inherited use: AuthState pair; OpsState shared set. **Nothing from DailyState.**
- Explicit: `OpsState.load_ops`.
- Cross-module constants imported by others: `BUDGET_CLEAR_CONFLICT`
  (→ `ops_setup_state`), `PAYMENT_SOURCES/PAYMENT_STATUSES/VERIFICATIONS`
  (→ `ops_expenses_pages`). Keep in `ops_expenses_state` (no move).

### 3.5 Schedule tab (embedded in `/ops/manpower`) — `ScheduleState(ExpensesState)`

- Component refs (58, via alias `M = ManpowerState`): all **ScheduleState-owned**
  (`schedule_*`, `sc_*`, staff form `sf_name/sf_staff_code/sf_position/sf_employment_type`,
  grid/kanban computed vars, `generate_schedule`, `export_schedule_csv`,
  `print_schedule`, `select_schedule_week`) + `selected_store_label` (→ Shared).
- Inherited use: `_actor` (AuthState); `_selected_month`, `_selected_store_id`,
  `selected_store_active` (OpsState → SharedOpsState). **Nothing from ExpensesState.**
- Migration note: after flattening, `ops_schedule_pages.py` alias becomes
  `ScheduleState`; the tab and the manpower page then use two sibling states
  sharing selectors via the single SharedOpsState instance (safe per §0).

### 3.6 Manpower (`/ops/manpower`) — `ManpowerState(ScheduleState)`

- Component refs (25): all ManpowerState-owned (`hc/eff/pay/leave_kpis`,
  `staff_rows*`, `cfg_*`, `c_*`, `p_*`, `open_config/save_config`, `edit_day`,
  `mp_*`, overrides).
- Inherited use: AuthState pair; OpsState shared set;
  **`ScheduleState._clear_schedule` → sever.**
- Explicit: `OpsState.load_ops`; yields `…load_schedule_data()` (today
  `ManpowerState.load_schedule_data` ≈ owner ScheduleState → after flatten,
  yield sibling `ScheduleState.load_schedule_data()`, §6 Q3).

### 3.7 Setup/Stores/Assignment (`/ops/stores`) — three classes, one page

- Component refs (55, alias `S = StoresState`) span **three owners**:
  - StoresState: `store_rows`, `can_manage_stores`, store form `sf_open/sf_editing/
    sf_code/sf_name/sf_concept*/sf_state/sf_zone*/sf_branch_manager/sf_error/sf_saving`,
    `load_stores`, `open_store_form/close_store_form/save_store/toggle_store_active`,
    `stores_error/stores_notice`, `select_store/month`.
  - AssignmentState: `can_assign_users`, `assign_*`, `open_assign`,
    `assign_user/unassign_user/select_assign_user`.
  - SetupState: `can_edit_setup`, `setup_error/notice/finalized/levels*/saving`,
    `s_holiday*`, `holiday_chips`, `save_setup/use_hq_defaults/add_holiday/
    remove_holiday`, `budget_rows`, `set_budget_value/clear_budget`.
- Inherited use (across the three classes): OpsState shared set; AuthState
  pair; **`_clear_setup`, `budget_rows`, setup fields (Setup→Stores);
  `_clear_manpower`, `_clear_stock` (via Stores) → sever.**
- Explicit: `SetupState` → `StoresState.load_page_data` (forward ref);
  `OpsState.load_ops`. Module consts: `ops_setup_state` imports
  `BUDGET_CLEAR_CONFLICT` from `ops_expenses_state` (keep import).
- Today `ops_setup_state` and `ops_stores_state` import each other → flatten
  makes all three siblings under SharedOpsState; components split the alias
  into `S = StoresState`, `UP = SetupState`, `A = AssignmentState` (names per
  review). Sibling event yields wire the flow (§0).

### 3.8 Sales (`/ops/sales`) — `SalesState(StoresState)`

- Component refs (44): all SalesState-owned (`sp_*` KPIs/levels/rows/charts,
  `active_*_fig`, `select_compare`, overrides).
- Inherited use: AuthState pair + `is_dark`; OpsState shared set;
  `_clear_setup`, `_clear_stock`, `_clear_stores`, `_clear_manpower` via
  `_clear_all_modules()` → the whole method becomes unnecessary post-flatten
  (each sibling owns its data and resets on its own page load) — drop it in
  the Sales commit (§6 Q4).
- Explicit: `OpsState.load_ops` (local lazy import inside `load_sales` — a
  Phase 3.5 leftover to inline/relocate).
- **Matrix verdict: Sales uses ~nothing of the Stores surface** (no
  `store_rows`, no setup/assign members) → compose directly on SharedOpsState
  (task allows Stores-inheritance only if the whole surface were used: it is
  not).

### 3.9 Settings (`/ops/settings`) — `SettingsState(OpsState)`

- Component refs (56): all SettingsState-owned (`account_*`, `pw_*`,
  `ref_*`, `cat_*`, `rename_*`, `dsc_*`, `data_*`, `visible_tabs`,
  `about_*`, `handle_dsc_upload`/`handle_db_upload` async upload events
  (async defs; not visible to the field scan)) + `store_options` (→ Shared)
  + `sign_out` (AuthState), `confirm_dsc_import`/`is_super_admin`.
- Inherited use: `_actor` (AuthState); `_selected_store_id`, `has_stores`,
  `selected_store_label`, `store_options`, `stores` (OpsState → Shared).
- Only the base class changes (`OpsState` → `SharedOpsState`).

### 3.10 Overview dashboard (`/ops`) — `OverviewState(ActionsState)`

- Component refs (25): OverviewState-owned (`pill_*`, `glance`, `attention`,
  `targets*`, `loaded`, `error`, `finalized_notice`, `levels_missing`,
  `portfolio*` (own re-declared copies), `spark_*`, `pace_*`, `active_*_fig`) +
  `select_store/month` overrides + `OpsState.has_stores` (→ Shared).
- Inherited use: AuthState pair + `is_dark`; OpsState `_selected_month`,
  `_selected_store_id`, `_sync_store_active`, `stores`, selector labels
  (→ Shared). **Nothing from ActionsState or deeper.**
- Explicit: `OpsState.load_ops`, `OpsState.tour_finished` (welcome-tour
  handler `maybe_start_tour` stays on OverviewState).
- `compute_portfolio` (module fn) is consumed by `OpsState.refresh_report`
  through a Phase 3.5 lazy import (§4).

### 3.11 Report (`/ops/report`) — `OpsState` itself

- Component refs (32): report machinery (`report` via `sales/expenses/manpower/
  stock/actions` computed vars, `report_heading/colors/category_rows`,
  `report_mode`, `versions`, `selected_version_label`, finalize dialog
  `finalize_*`, `open_finalize/open_correct/set_finalize_dialog_open/
  save_finalization/export_report`, `all_stores`, `finalized_note`,
  `live_preflight_ok`, `can_finalize`, `select_mode/select_version`).
- `ops_finalize.py` duck-type contract (unchanged): `_actor`, `_clear_report`,
  `_load_report`, `_selected_month`, `_selected_store_id`, `error_message`,
  `report_status`, `report_mode`, `versions`, `selected_version_label`,
  `finalize_*` — all stay reachable on OpsState (shared ones via inheritance).
- **All of it stays on OpsState; only its base changes to SharedOpsState.**

## 4. Phase 3.5 lazy imports to remove (final cleanup commit)

1. `auth_pages.toggle_is_dark` → lazy `OverviewState`, `SalesState` to restyle
   charts. Removal design (recommended): a tiny registration list in a leaf
   module both sides already import (e.g. `ops_charts`): chart-capable states
   register `(state_class, applier_name)` at import; `toggle_is_dark` iterates
   the registry — keeps `auth_state.toggle_is_dark` namespace, no cycles.
2. `ops_state.refresh_report` → lazy `compute_portfolio` from
   `ops_overview_state` (All Stores branch). After flattening the overview
   module no longer imports the chain, so a top-level import is safe again.
3. `ops_sales_state.load_sales` → lazy `from ducklab.ops_state import OpsState`
   (superfluous; replaced by the shared-base import).

## 5. Proposed `SharedOpsState(AuthState)` content (names unchanged)

Fields: `stores`, `store_options`, `dashboard_store_options`,
`selected_store_label`, `month_options`, `selected_month_label`, `has_stores`,
`selected_store_active`, `report_status`, `error_message`.
Helpers: `_month_choices`, `_selected_store_id`, `_selected_month`,
`_sync_store_active`, `auth_message`, `SESSION_EXPIRED`, `ACCESS_DENIED`,
`_page_tour_script`, `month_label`.
Events: `load_selectors` (see Q1), `select_store`, `select_month`,
`tour_finished`, `maybe_start_page_tour`, `replay_page_tour`,
`page_tour_finished`.
Auth/token handling stays on `AuthState` (parent); theme (`is_dark`,
`toggle_is_dark`) stays on `AuthState`.

OpsState keeps: monthly-report data + computed vars, versions/finalize
machinery + dialogs, `refresh_report`/`_load_report`/`_clear_report`,
`select_mode`, `select_version`, portfolio fields for the report page,
`load_ops` (report variant, see Q2).

## 6. Review questions (must be answered before migration)

**Q1 — selector-init event name.** Shared base needs what `load_ops` does
minus the report refresh. Options: (a) new `SharedOpsState.load_selectors`
(namespace `shared_ops_state.load_selectors`; page `load_*` yield it instead
of `OpsState.load_ops()`); (b) move `load_ops` down and let OpsState override.
Recommendation: (a) — one shared name, no shadowing. All page `load_*`
handlers update consistently in their per-page commit.

**Q2 — what page selector changes still trigger.** Today every page's
`select_store`/`select_month` runs `_clear_report()` + `refresh_report()`.
The user-visible parts that must survive per page: re-read of
`selected_store_active` (read-only banner, write guards) and the shared
`report_status`/`error_message` surface (incl. "No stores are assigned…").
The report/versions recompute is invisible off `/ops/report` (the report page
fully reloads on its own `on_load`/`on_change`). Proposal: SharedOpsState
implements the shared re-sync (service-layer `_refresh_store_active`, no
business-logic duplication); report refresh stays exclusive to OpsState.
Store/month switch on any page keeps: label set → active re-read → page data
reload. Confirm acceptable (no visible behavior change).

**Q3 — Schedule tab decoupling.** `ManpowerState.load_manpower` yields
`ScheduleState.load_schedule_data()` (sibling) after flattening;
`ops_schedule_pages.py` alias switches `ManpowerState → ScheduleState`;
schedule components keep identical var values/element IDs. The generated
schedule preview in Daily (`preview_schedule`) is unaffected (DailyState-owned).

**Q4 — Sales `_clear_all_modules`.** Dead code post-flatten (clears other
pages' fields on one instance). Drop and reset only `sp_*` in
`_clear_sales`. Confirm.

**Q5 — Sales inheritance.** Matrix proves Stores surface is unused →
`SalesState(SharedOpsState)` direct (no StoresState inheritance). Confirm.

**Q6 — Setup/Stores aliases.** Split `S = StoresState` into three aliases in
`ops_stores_pages.py` (`StoresState`, `SetupState`, `AssignmentState`) in the
same commit; `StoresState.load_page_data` splits into per-class loaders wired
by sibling yields. Confirm the alias naming.

**Q7 — `month_label` helper** is re-exported from `ops_state`; used in ops
report page and possibly tests. Keep re-export in `ops_state.py` (exact import
path stability) — confirm you want re-exports vs. updating all imports.

## 7. Verification plan (per commit)

- `.venv/bin/python -m pytest tests/ -q` (full suite — 33 test files touch
  state classes; per-page commits keep each page class's full member surface
  so expected fallout is import paths only).
- `DUCKLAB_DB_PATH=$(mktemp -d)/compile.db .venv/bin/reflex export --env prod
  --frontend-only --no-zip` (compile check; required pre-PR per CLAUDE.md).
- `git diff --check`; state-module line counts before/after recorded in the
  final commit message.
- Browser smoke tests per page: **not possible in this clone (no `.devdata`)
  — will be flagged in each PR for operator-side verification** per
  `deploy/acceptance.sh` runbook. No scripts needing `.devdata` were run here.

## 8. Baseline line counts (before)

| Module | Lines |
|---|---|
| ops_state.py | 512 |
| ops_daily_state.py | 782 |
| ops_expenses_state.py | 810 |
| ops_schedule_state.py | 421 |
| ops_manpower_state.py | 623 |
| ops_stock_state.py | 427 |
| ops_assignment_state.py | 128 |
| ops_setup_state.py | 425 |
| ops_stores_state.py | 354 |
| ops_sales_state.py | 562 |
| ops_actions_state.py | 383 |
| ops_overview_state.py | 691 |
| **total** | **6118** |

(Settings stays in `ops_settings_pages.py`, 1013 lines; after-numbers are
recorded in the final cleanup commit.)
