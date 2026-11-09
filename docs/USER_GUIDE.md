# Opscore User Guide

This guide shows you how to install Opscore, set up your stores and use each page day to day.

---

## 1. Install

### Ubuntu and Debian

Opscore runs on **Ubuntu 22.04 or newer** (and Debian-based systems).

1. Download `opscore_<version>_amd64.deb`.
2. If you are upgrading, first open your current Opscore and use **Settings → Data → Download Backup**. Keep the backup somewhere safe.
3. Open a terminal in the folder where you saved it and run:
   ```bash
   sudo apt install ./opscore_*_amd64.deb
   ```
4. Open your app menu and click **Opscore**.

When upgrading from 0.1.0, your database is automatically upgraded on first launch. Existing records and accounts are kept.

The app opens in its own window. You don't need internet after installing.

**To uninstall:** `sudo apt remove opscore`. Your data is kept.

### Windows

Opscore supports **64-bit Windows 10 and 11**. Windows packaging is implemented, but published-release and real-machine validation remain outstanding.

1. Before upgrading, open your current Opscore and use **Settings → Data → Download Backup**.
2. Run `Opscore-Setup-<version>.exe`. The per-user installer does not require administrator rights.
3. Open **Opscore** from the Start Menu. The app uses WebView2, with Microsoft Edge app mode and the default browser as fallbacks.

The installer places the program under `%LOCALAPPDATA%\Programs\Opscore`. Your database, log and backups are kept separately under `%LOCALAPPDATA%\Opscore` and are preserved by upgrades and uninstallation.

A self-contained folder build is also available: copy the whole `Opscore` folder and double-click `Opscore.cmd`. For source setup and build instructions, see [Windows development](windows-dev.md).

## 2. First launch

1. **Create Account.** Enter your name, email and a password (at least 10 characters, with letters and numbers). This first account is the Super Admin and can do everything.
2. **Welcome tour.** A short tour shows you around. Each page has its own tour the first time you open it. Replay any tour with the **?** button at the top right.
3. **Check reference data.** Opscore loads the bundled Daily Sales Contribution workbook automatically when both reference tables are empty. Go to **Settings → Reference Data** to review it or upload a replacement. This gives each day its target.
4. **Add your stores.** Go to **Store Management** (see section 4).

## 3. Getting around

### Sidebar

| Section | Pages |
|---|---|
| Overview | Dashboard |
| Store Operations | Monthly Report, Daily Operations, Sales Performance, Expenses Management, Manpower Efficiency, Stock Management |
| Management | Action Tracker, Store Management |
| System | Settings |

### Top of each page

- **Store** and **Month** selectors: choose which store and month you're looking at.
- **?**: replay the page tour.
- **Sun/moon**: switch between light and dark mode.
- **Profile**: sign out.

## 4. Store Management

### Add a store

1. Click **New Store**.
2. Fill in **Code**, **Name**, **Concept**, **State**, **Zone**, **Zone PIC** and **Branch Manager**.
3. Click **Save Store**.

**State and Concept matter.** They pick the right daily contribution figures, so daily targets stay blank until both are set.

### Monthly Setup

Pick the store and month, then fill in:

| Group | Fields |
|---|---|
| Sales Targets | Minimum RTM target, Level 1 base target, Level 2, 3 and 4 uplift. Use **HQ defaults** to fill Minimum RTM (RM 200,000) and +10%, +20% and +30% uplifts. Sales same month last year, sales last month. |
| Daily Sales | Target Transactions, Target BV, Target UPT, Public Holidays. If Target Transactions is blank, the form suggests Level 1 ÷ Target BV, rounded to a whole transaction. |
| Manpower | Salary ratio target, budget headcount, opening headcount, turnover threshold, part-timer factor, payroll figures |
| Stock | Stock loss target |
| Expenses | Budget for each expense category |

Click **Save Setup**. Use **Copy From Last Month** to pre-fill the form and expense budgets from the previous month; review the values and click **Save Setup** to apply them.

**Tips:**
- Once Level 1 is set, all four levels must be filled in.
- Leave a field blank if it isn't set. Enter 0 only when it's truly zero.
- Add public holidays before entering sales, so the daily targets use the holiday share.

### Give others access (Super Admin)

Under **User Assignment**, choose a user and a store and click **Assign**. Users see only the stores they're assigned to. **Unassign** removes access.

### Deactivate a store

**Deactivate** makes a store read-only, including payroll editing in Manpower Efficiency. All its history is kept, and **Activate** brings it back.

## 5. Daily Operations

Enter each day's figures here.

1. Pick the store and month.
2. Click **New Entry**, or **Edit** on a day in **Daily Records**.
3. To use the week's schedule for staffing, click **Apply Schedule**. Review the **Schedule Preview**, including any warnings, then click **Apply to Entry** to fill working full-time and part-time staff and annual, medical and unpaid leave. You can still enter or adjust the figures yourself.
4. Fill in what you have:
   - **Sales**, **Transactions**, **Units Sold**
   - **Stock Loss** by code
   - **Staff**: working full-time and part-time, **New Hires**, **Resignations**
   - **Leave**: Annual, Medical, Other Paid, Unpaid
5. Click **Save Entry**.

**Blank is not zero.** Leave a field blank if it wasn't reported. Enter 0 only for a real zero, such as a closed day. A blank day shows as not reported and is left out of averages and targets.

**Monthly Summary** at the top shows sales, projected sales and achievement for the month so far. Daily records and the entry form also show paid expenses for each day, including an entered RM0 expense.

## 6. Sales Performance

| Section | Shows |
|---|---|
| Sales Summary | Sales to date, **Projected Sales** (until the month is complete), **Level Reached**, and achievement against Levels 1 to 4 |
| Growth | **SSSG/Year** and **SSSG/Month** |
| Basket | **BV**, **UPT**, **ASP**, compared with the **State Average** |
| Sales Trend | Chart of daily and cumulative sales against target levels |
| Sales Calendar | The month at a glance, colour-coded by result |
| Daily Sales | Each day's **Daily Target**, **Adjusted Target**, **Sales** and **Result** |

### How daily targets work

- **Daily Target** is Level 1 × that day's share of the month (depending on the weekday or public holiday, your State and Concept).
- **Adjusted Target** adds any shortfall from earlier days. If you miss today's target, the gap carries forward to the **next reported day**.
- A day with no sales entered keeps its Daily Target but gets no Adjusted Target or Result, and passes nothing forward.
- **Result** is Achieved when sales reach the Adjusted Target.

**Projected Sales** = sales so far ÷ days entered × days in the month. It becomes actual sales once every day is entered.

## 7. Expenses Management

### Record an expense

Fill in the incurred date, paid date, category, description, amount, **Paid By** (Petty Cash, HQ / Central, Store Account, Other), status (**Pending**, **Paid**, **Cancelled**), receipt reference and verification (**Unverified**, **Verified**, **Disputed**).

### What the page shows

- **Paid**, **Incurred** and **Pending** totals
- Each category's paid amount against its budget
- **Expense Ratio** against sales
- Lines that need review, such as a missing category or recorder

Expense categories are set up in **Settings → Reference Data**. Stores without categories receive ten defaults automatically on startup; stores with existing categories are left unchanged.

## 8. Manpower Efficiency

Shows **Opening** and **Closing Headcount** against budget, **Effective Staff**, **Hires**, **Resignations**, **Turnover**, **Leave Days**, **Sales per Staff**, **Salary Cost** and **Salary Ratio** against target.

Salary figures appear only to Super Admin and Administrator. Until month-end payroll is entered, the salary ratio shows **Awaiting Payroll**.

### Weekly schedule

Open the **Schedule** tab to plan staff shifts for the selected store. The existing manpower figures remain under **Overview**.

1. Open **Staff**, click **Add Staff**, and enter each person's name, staff ID, position and **Employment Type** (**Full-Time**, **Part-Time** or **Not Set**). Use **Save & Add Another** to enter a roster quickly. Set the employment type before using the schedule to fill daily staffing figures.
2. Open **Planner** and choose a week. Select a shift code for each person and day. Changes save automatically.
3. Enter **Target Manpower** for the week. The **Headcount** row shows scheduled working staff each day and highlights days below target in red. Only shift codes with start and end times count toward headcount.
4. Super Admin and Administrator users can open **Config** to add or edit shift codes, labels, times and colors. Start and End must either both be set or both be blank. Shift codes are shared by all stores; deleting a code used by a schedule is not allowed.
5. Click **Generate** to open the formatted schedule in **View**, then **Print** to print it or save it as a PDF.
6. Click **Export CSV** in Planner to save one CSV per staff member who has shifts that week. Each file contains `date,shift_code` and is saved at `~/Documents/Opscore Schedules/<store-code>/<week-start>/`. Select a valid week before exporting; a malformed week produces an error instead of an export.

Deleting a staff member also deletes that person's schedule entries. Viewers can read schedules but cannot edit them or export CSV files. Other store-writing roles can manage schedules for their assigned, active stores.

## 9. Stock Management

Shows **Total Loss** and **Loss Ratio** against the stock loss target, with a breakdown by code.

| Code | Meaning | Counts toward Total Loss |
|---|---|---|
| DMO | Damaged in outlet | Adds |
| DMC | Returned by customer | Adds |
| DMP | Expired | Adds |
| MISSING | Missing at stock balance checks | Adds |
| EXTRA | Extra stock found at stock balance checks | Reduces |
| 2DMP | Expired Buy 1 Free 1 (DPI) stock | Information only |
| DPI | Buy 1 Free 1 promotion | Information only |
| DMW | Damaged under warranty | Information only |
| DDR | Damaged during receiving | Information only |
| DMN | Natural disaster | Information only |
| DME | E-commerce return | Information only |
| DMQ | Damage in Quality | Information only |
| DST | Damaged in stock transfer | Information only |

Stock loss is entered in **Daily Operations**. Use **Edit** in the daily table to correct a day.

## 10. Action Tracker

Use it to log every issue until it's fixed.

1. Click **Add Action**.
2. Fill in **Pillar** (Sales Performance, Expenses Management, Manpower Efficiency, Stock Management, General Operations), **Issue**, **Owner**, **Priority** (Low, Medium, High, Critical), **Due** date and **Next Step**.
3. Click **Save Action**.

Update the **Status** as it moves: **Open**, **In progress**, **Blocked**, **Done**, **Cancelled**.

**Action Summary** shows open, overdue, completed and carried-over actions. Tick **Overdue Only** to see what's late.

## 11. Dashboard and Monthly Report

### Dashboard

The month at a glance for the selected store: sales, expenses, manpower, stock and actions, with **Alerts** for anything off track. Choose **All Stores** in the Store selector to see an aggregate across your assigned stores. A dashboard link such as `/ops?store=12` selects store ID 12 on the first load; changing the selector afterwards takes precedence over the original link.

### Monthly Report

The full month-end report across all areas: sales, expenses, manpower, stock and actions.

The Store selector also offers **All Stores**, a live aggregate report across your assigned stores. All Stores reports cannot be finalized.

Click **Export** to download the selected report as Excel. A live report is named `opscore-<store>-<month>.xlsx`; a finalized report is named `opscore-<store>-<month>-finalized-v<version>.xlsx`.

For a single-store report, **Live** shows current figures. Users with finalization permission can click **Finalize Month**, review the warnings and confirm to save a finalized version from the current live figures. The **Finalized** view retains every finalized version and provides a version selector; draft snapshots are not presented as finalized. Click **Correct Month** there to create a new version from current live figures; previous versions are never overwritten. Editing a finalized month shows a warning that live figures may now differ.

## 12. Settings

| Section | What you can do |
|---|---|
| Account | See your name and email. **Change Password**. |
| Reference Data | Super Admin and Administrator users can upload a Daily Sales Contribution file up to 64 MiB, and add or rename expense categories. The store selector reloads that store's categories. |
| Data (Super Admin) | **Download Backup**. **Restore Database** from a backup file. |
| About | See the app name, app version and database schema version. Super Admin also sees the database path. |

### Backup and restore

- **Back up regularly**, especially before reinstalling. Keep the file somewhere safe.
- **Restore** saves the current database automatically, then validates and migrates a temporary copy of the uploaded backup. Checks include SQLite integrity, foreign keys, required tables and columns, and schema compatibility. Only a valid temporary copy atomically replaces the live database; the pre-restore backup is kept so a restore can be undone.
- After a reinstall: create an account, then **Restore**. The restored backup brings back its own accounts too.

## 13. Roles

| Role | Can do |
|---|---|
| Super Admin | Everything, all stores, users, backup and restore |
| Administrator | Assigned stores: all records, Monthly Setup, finalize reports |
| Zone Manager | Assigned stores: enter daily, stock, expenses, actions. Read reports. |
| Branch Manager | Assigned stores: enter daily, stock, expenses, actions. Read reports. |
| Viewer | Assigned stores: read only |

## 14. Daily routine

A suggested routine for an ABM with two or more stores:

**Every morning (5 minutes per store)**
1. Enter yesterday's figures in **Daily Operations**.
2. Check **Sales Performance**: was yesterday Achieved? What's today's Adjusted Target?
3. Add any new issue to **Action Tracker**.

**Every week**
1. Compare stores on the **Dashboard**.
2. Review **Expenses** and **Stock Management**.
3. Close finished actions and chase overdue ones.
4. Prepare and review the weekly staff schedule.

**Month-end**
1. Make sure every day is entered.
2. Enter payroll figures in Monthly Setup.
3. Review the **Monthly Report**, then **Export** it.
4. **Finalize Month** to retain a versioned report. If corrections are needed later, use **Correct Month** in the **Finalized** view to create a new version.
5. **Download Backup**.
6. Set up next month's targets and holidays.

## 15. Glossary

| Term | Meaning |
|---|---|
| Level 1 to 4 | The four sales targets. Level 1 is the base; Levels 2 to 4 add an uplift. |
| Minimum RTM | The floor for Level 4 |
| Daily Target | Level 1 × the day's contribution share |
| Adjusted Target | Daily Target plus shortfall carried forward |
| Projected Sales | Expected month-end sales at the current pace |
| SSSG/Year, SSSG/Month | Sales growth against last year and last month |
| BV | Basket value (sales ÷ transactions) |
| UPT | Units per transaction (units sold ÷ transactions) |
| ASP | Average selling price (sales ÷ units) |
| Salary Ratio | Salary cost ÷ sales |
| Turnover | Resignations ÷ opening headcount |
| Loss Ratio | Total Loss ÷ sales |
| PH | Public holiday |

## 16. Troubleshooting

| Problem | Fix |
|---|---|
| Daily Target is blank | Set the store's **State** and **Concept**, set **Level 1**, and upload the Daily Sales Contribution file. |
| A day shows Not Reported | No sales were entered for that day. Enter them, or enter 0 if the store was closed. |
| Salary ratio shows Awaiting Payroll | Enter the month's payroll figures in Monthly Setup. |
| "No stores assigned" | Ask the Super Admin to assign you a store, or create one if you're the Super Admin. |
| Opscore won't open | Check `~/.local/share/opscore/opscore.log` on Linux or `%LOCALAPPDATA%\Opscore\opscore.log` on Windows. Another program may be using port 47811. |
| Forgot the password | There's no reset by email. Restore a backup, or ask whoever set up your account. |

Your database is at `~/.local/share/opscore/store_ops.db` on Linux or `%LOCALAPPDATA%\Opscore\store_ops.db` on Windows.
