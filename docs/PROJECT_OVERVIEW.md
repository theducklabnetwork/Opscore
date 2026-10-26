# Opscore: Project Overview

**Opscore** \ ˈäps-ˌkȯr \ (ops core)
*noun.* An offline desktop app for tracking a store's daily operations (sales against targets, expenses, manpower, stock loss and corrective actions) in one place, one month at a time.

Code name: **DuckLab**. Display name: **Opscore**.

---

## 1. Background

Assistant Branch Managers (ABMs) are each responsible for **at least two stores**. Store performance is normally followed through reports from HQ and the zone, and through personal Excel workbooks for those who keep one.

Opscore started as a replacement for one such workbook, the *Store Operations Monitoring* workbook. It grew into a standalone app that any ABM can install and use for their own stores.

## 2. The problem

> **ABMs manage at least two stores, but most have no way to track them.**

This is based on the author's own observation of ABMs across stores.

### Impact

| Impact | What happens |
|---|---|
| Weak Store Understanding | ABMs don't really know how their stores are performing beyond what they're told. |
| Shortfalls Found Late | Sales gaps show only at month-end, when it's too late to recover. |
| Issues Forgotten | Problems raised at a store have no follow-up, so they repeat. |
| Not Ready to Lead | ABMs don't build the habit a future Branch Manager needs: running a store by its numbers. |

Other effects seen: stores are hard to compare, there's no record of the ABM's own work, and knowledge leaves with the person when they're transferred.

### Causes

> ABMs are expected to manage two or more stores, yet they are given no tool, no training and no routine to track them.

| Category | Cause |
|---|---|
| Tools | No system built for the ABM level. Reports are designed for HQ and zones. |
| People | Building a tracker needs Excel skills most ABMs weren't trained in. |
| Time | Floor work comes first, and time is split across two or more stores. |
| Process | No standard monitoring routine, and no one reviews an ABM's own tracking. |
| Data | Figures are scattered across reports, chats and systems, and some arrive late. |
| Environment | Frequent transfers mean any personal tracker gets abandoned. |

### Why Excel alone falls short

- Each store and month lives in a separate file.
- A blank day can look exactly like a zero-sales day.
- One bad paste or overwritten formula breaks the totals without anyone noticing.
- Level targets, daily targets and carried-forward shortfall are easy to get wrong by hand.
- Anyone with the file sees everything, including payroll.
- There's no locked "final" version of a month once it's reported.

## 3. The solution

> **Opscore gives every ABM a ready-made way to track all their stores daily, follow up on issues and report their results, with no Excel skills needed.**

| Impact | Opscore answer |
|---|---|
| Weak Store Understanding | **Clear Daily Picture:** sales, SPR, IPR, salary ratio and stock loss for each store on one screen |
| Shortfalls Found Late | **Daily Target:** every day shows its target and whether it was achieved |
| Issues Forgotten | **Action Tracker:** every issue logged with an owner and status until done |
| Not Ready to Lead | **Monthly Report:** a full month-end report, ready to export |

It also removes the causes: it's ready-made (Tools), needs no formulas (People), takes minutes a day (Time), gives everyone the same pages and rules (Process), keeps all figures in one place (Data), and the data stays in the app (Environment).

## 4. Goals and principles

1. **Built for ABMs.** Simple pages, short labels, no formulas on screen.
2. **Correct numbers.** Every KPI matches the workbook's rules, proven by a 102/102 parity check against real data.
3. **Blank is not zero.** Unreported days are never treated as zero sales.
4. **Private by design.** Each user installs their own copy with their own database. No shared server, no cloud.
5. **No company names in code.** Store concepts use aliases (Outlet, Mall, Retail, and so on).
6. **Works offline.** No internet needed after installation.

## 5. Scope

### In scope (built)

- Seven operational modules: Dashboard, Daily Operations, Sales Performance, Expenses Management, Manpower Efficiency, Stock Management, Action Tracker
- Store Management with Monthly Setup (targets, budgets, holidays) and user assignment
- Monthly Report with Excel export, plus finalize and versioning in the engine
- Daily Sales Contribution: daily targets by State, Concept and weekday or public holiday
- Settings: account, reference data upload, backup and restore
- Five roles with server-side access control
- First-launch account creation and guided page tours
- Ubuntu/Debian desktop package

### Out of scope (for now)

- Windows and macOS installers
- A shared server or syncing between users
- Mobile app
- Forecasting beyond the workbook's straight-line projection

## 6. Status

| Area | Status |
|---|---|
| All modules | Done |
| Workbook parity (September 2026) | 102/102 metrics match |
| Desktop app (.deb) | Done and tested |
| Demo data (AB01 / DEMO, June to September 2026) | Done |
| Light mode default | Done |
| Tests | 930 passing |
| Wording pass | Pending |
| Finalize button on screen | Pending |
| Windows installer | Not started |

## 7. Timeline

| Phase | Work |
|---|---|
| 1. Foundation | VPS dev environment, toolchain, design kit |
| 2. Core modules | Stock, Manpower, Expenses, Actions, Stores, Sales (PRs #1 to #9) |
| 3. Parity and reviews | Workbook import and parity check, two independent security and correctness reviews |
| 4. Reporting | Dashboard, Monthly Report, Daily Operations (PRs #10 to #15) |
| 5. Daily targets | Daily Sales Contribution import, carry-forward rule (PRs #16, #17) |
| 6. Design | Plotly charts, polish, private data scrub, Opscore branding (PRs #18 to #22) |
| 7. Move to desktop | Local Ubuntu development, one copy per user |
| 8. Settings and onboarding | Account, Reference Data, Backup/Restore, first launch, page tours (PRs #23 to #29) |
| 9. Release | Excel export, desktop package, demo data, light mode (PRs #30 to #32) |

## 8. How it was built

Opscore was built by the author with AI coding agents (Claude Code, then OpenCode with Kimi K3.1 Pro). Each feature went through a short written task, an agent pull request, a review and a hands-on check before merging. Business rules came from the workbook and were checked against real monthly figures.

## 9. Next steps

1. Wording pass across all pages
2. Finalize button on the Monthly Report page
3. Windows installer
4. Wider trial with ABMs, then feedback

## 10. Related documents

- [README](../README.md): setup, build and status
- [Architecture](ARCHITECTURE.md): how the system is built
- [User Guide](USER_GUIDE.md): how to use the app
