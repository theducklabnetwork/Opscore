# Opscore User Guide

This guide shows you how to install Opscore, set up your stores and use each
page day to day. It is written from your perspective as a user.

For a higher-level description of what Opscore is and why, see the
[Project Overview](PROJECT_OVERVIEW.md). For what changed between releases,
see the [Changelog](CHANGELOG.md). For database upgrades and recovery, see
[Migrations](MIGRATIONS.md).

---

## 1. Install

### Ubuntu and Debian

Opscore runs on **Ubuntu 22.04 or newer** and Debian-based systems (amd64).

1. Download `opscore_<version>_amd64.deb`.
2. If you are upgrading, first open your current Opscore and use
   **Settings → Data → Download Backup**. Keep the backup somewhere safe.
3. Open a terminal in the folder where you saved the package and run:

   ```bash
   sudo apt install ./opscore_<version>_amd64.deb
   ```

4. Open your app menu and click **Opscore**.

The app opens in its own window. You do not need internet after installing.

**To uninstall:** `sudo apt remove opscore`. Your data is kept.

### Windows

Opscore supports **64-bit Windows 10 and 11**.

1. Before upgrading, open your current Opscore and use **Settings → Data →
   Download Backup**.
2. Run `Opscore-Setup-<version>.exe`. The per-user installer does not require
   administrator rights.
3. Open **Opscore** from the Start Menu. The app uses WebView2, with
   Microsoft Edge app mode and the default browser as fallbacks.

The v1.2 installer has an Authenticode signature from the self-signed
**Opscore Development** certificate, with a DigiCert timestamp. Windows may
show **Unknown publisher** or a SmartScreen warning because this certificate
is not issued by a publicly trusted certificate authority. Only continue with
an installer obtained from the official Opscore release. You do not need to
install a private CA certificate to run Opscore.

The installer places the program under `%LOCALAPPDATA%\Programs\Opscore`.
Your database, log and backups are kept separately under
`%LOCALAPPDATA%\Opscore` and are preserved by upgrades and uninstallation.

A self-contained folder build is also available: copy the whole `Opscore`
folder and double-click `Opscore.cmd`.

### Database choice on install or upgrade

When you install or upgrade and an existing database is found, Opscore asks
whether to use the existing database (the default) or start fresh. Choosing
**Start fresh** renames the existing file to `store_ops.db.backup-1` (or the
next free number), so your old data is never deleted.

On Windows, the installer asks this question during setup. On Linux, the
question appears on the first launch after installing or upgrading.

## 2. First launch

1. **Create Account.** Enter your name, email and a password (at least 10
   characters, with letters and numbers). This first account becomes the
   Super Admin and can do everything.
2. **Save your recovery key.** Opscore shows a single-use recovery key once.
   Use **Save Kit to Data Folder** or **Download Kit**, and keep it private.
   You will need it if you ever forget your password.
3. **Welcome tour.** A short tour shows you around. Each page has its own
   tour the first time you open it. Replay any tour with the **?** button at
   the top right.
4. **Check reference data.** Opscore loads the bundled Daily Sales
   Contribution workbook automatically when both reference tables are empty.
   Go to **Settings → Reference Data** to review it or upload a replacement.
   This gives each day its target.
5. **Add your stores.** Go to **Store Management** (see section 4).

## 3. Getting around

### Sidebar

| Section | Pages |
|---|---|
| Overview | Dashboard |
| Store Operations | Monthly Report, Daily Operations, Sales Performance, Expenses Management, Manpower Efficiency, Stock Management |
| Management | Action Tracker, Store Management |
| System | Settings |

### Top of each page

- **Store** and **Month** selectors: choose which store and month you are
  looking at. The month list offers the current month and the previous 11
  months.
- **?**: replay the page tour.
- **Sun/moon**: switch between light and dark mode.
- **Profile menu**: sign out.

Dashboard and Monthly Report also offer **All Stores** in the Store selector,
an aggregate across your assigned stores.

### Deactivated stores

A deactivated store becomes read-only and shows a banner: *"This store is
deactivated — read-only. Reactivate it on Store Management to make changes."*
Its history is kept, and reactivating it restores normal access.

## 4. Store Management

### Add a store

1. Click **New Store**.
2. Fill in **Code**, **Name**, **Concept**, **State**, **Zone**, **Zone PIC**
   and **Branch Manager**.
3. Click **Save Store**.

**State and Concept matter.** They pick the right daily contribution figures,
so daily targets stay blank until both are set.

### Monthly Setup

Pick the store and month, then fill in:

| Group | Fields |
|---|---|
| Sales Targets | Minimum RTM target, Level 1 base target, Level 2, 3 and 4 uplift. Use **Use HQ Defaults** to fill Minimum RTM (RM 200,000) and +10%, +20% and +30% uplifts. Sales same month last year, sales last month. |
| Daily Sales | Target Transactions, Target BV, Target UPT, Public Holidays. If Target Transactions is blank, the form suggests Level 1 ÷ Target BV, rounded to a whole transaction. |
| Manpower | Salary ratio target, budget headcount, opening headcount, turnover threshold, part-timer factor, payroll figures |
| Stock | Stock loss target |
| Expenses | Budget % for each expense category — a share of the active sales target |

Click **Save Setup**. Use **Copy From Last Month** to pre-fill the form and
expense budgets from the previous month; review the values and click
**Save Setup** to apply them.

**Budget percentages.** Each category's budget is a percentage of the store's
active sales target. The target starts at **Minimum RTM**; once cumulative
recorded sales reach Level 1, the budget moves to that target, and keeps
stepping up through Levels 2–4 as sales pass them. If Level 1 Base is not set,
Minimum RTM is used. Correcting sales downward steps the budget back down.
Ten default categories ship with suggested percentages (for example
Electricity 2.5%, Water 0.3%); you can change each one here or on the
Expenses page under **Set Budget**.

**Tips:**

- Once Level 1 is set, all four levels must be filled in.
- Leave a field blank if it isn't set. Enter 0 only when it's truly zero.
- Add public holidays before entering sales, so the daily targets use the
  holiday share.

### Give others access (Super Admin)

Under **User Assignment**, choose a user and a store and click **Assign**.
Users see only the stores they are assigned to. **Unassign** removes access.

### Deactivate a store

**Deactivate** makes a store read-only, including payroll editing in Manpower
Efficiency. All its history is kept, and **Activate** brings it back.

## 5. Daily Operations

If you want to try the app with sample figures before entering real data, see
**Load demo data** in section 19.

Enter each day's figures here.

1. Pick the store and month.
2. Click **New Entry**, or **Edit** on a day in **Daily Records**.
3. A new entry opens with working full-time and part-time staff, Annual
   Leave, Replacement Leave (under **Other Paid Leave**) and Unpaid Leave
   filled from that day's schedule. These are editable planned values.
   Previously saved entries keep their saved values when reopened. To refresh
   deliberately, click **Apply Schedule**, review the preview, then **Apply to
   Entry**.
4. At day end, adjust FT/PT for unexpected absences and enter **Medical
   Leave** and **Emergency Leave** manually. The schedule never fills or
   clears these exceptions. Medical Leave is included in reported Other Paid
   Leave but remains separately visible; Emergency Leave is reported
   separately. Total Leave counts every leave person-day once.
5. Fill in what you have:
   - **Sales**, **Transactions**, **Units Sold**
   - **Stock Loss** by code
   - **Staff**: working full-time and part-time, **New Hires**,
     **Resignations**
   - **Leave**: Annual, Medical, Emergency, Other Paid (including RPL), Unpaid
6. Click **Save Entry**.

**Blank is not zero.** Leave a field blank if it wasn't reported. Enter 0
only for a real zero, such as a closed day. A blank day shows as not reported
and is left out of averages and targets. You can clear a saved value to put
it back to blank.

**Monthly Summary** at the top shows sales, projected sales and achievement
for the month so far. Daily records and the entry form also show paid
expenses for each day, including an entered RM0 expense.

## 6. Sales Performance

| Section | Shows |
|---|---|
| Sales Summary | Sales to date, **Projected Sales** (until the month is complete), **Level Reached**, and achievement against Levels 1 to 4 |
| Growth | Sales same month last year and sales last month comparisons |
| Basket | **BV**, **UPT**, **ASP**, compared with the **State Average** |
| Sales Trend | Chart of daily and cumulative sales against target levels |
| Sales Calendar | The month at a glance, colour-coded by result |
| Daily Sales | Each day's **Daily Target**, **Adjusted Target**, **Sales** and **Result** |

### How daily targets work

- **Daily Target** is Level 1 × that day's share of the month (depending on
  the weekday or public holiday, your State and Concept).
- **Adjusted Target** adds any shortfall from earlier days. If you miss
  today's target, the gap carries forward to the **next reported day**.
- A day with no sales entered keeps its Daily Target but gets no Adjusted
  Target or Result, and passes nothing forward.
- **Result** is Achieved when sales reach the Adjusted Target.

**Projected Sales** = sales so far ÷ days entered × days in the month. It
becomes actual sales once every day is entered.

Sales editing is done in **Daily Operations**; the **Edit** link on each day
opens it there.

## 7. Expenses Management

### Record an expense

Click **Add Expense** and fill in the incurred date, paid date, category,
description, amount, **Paid By** (Petty Cash, HQ / Central, Store Account,
Other), status (**Pending**, **Paid**, **Cancelled**), receipt reference and
verification (**Unverified**, **Verified**, **Disputed**). Incomplete drafts
are allowed; the page flags them for you to finish later.

### What the page shows

- **Paid**, **Incurred** and **Pending** totals
- Each category's paid amount against its budget
- **Needs Review**: lines that need attention, such as a missing category or
  recorder
- Filters for period, category, status, verification and description/receipt
  search, and sorting by incurred date, paid date or amount

### Set a budget

Under **Budget vs Paid**, choose a category and enter its **Budget (%)** — a
percentage of the active sales target (Minimum RTM, then the highest sales
level reached). The page shows the current basis, for example "Budget = % ×
Minimum RTM (RM 200,000.00)". Click the **×** beside a saved percentage to
clear it back to **No budget**.

Expense categories are set up in **Settings → Reference Data**. Stores without
categories receive ten defaults automatically on startup; stores with existing
categories are left unchanged.

## 8. Manpower Efficiency

The **Overview** tab shows **Opening** and **Closing Headcount** against
budget, **Effective Staff**, **Hires**, **Resignations**, **Turnover**,
**Leave Days**, **Sales per Staff**, **Salary Cost** and **Salary Ratio**
against target.

Salary figures appear only to Super Admin and Administrator. Until month-end
payroll is entered, the salary ratio shows **Awaiting Payroll**.

### Weekly schedule

Open the **Schedule** tab to plan staff shifts for the selected store. The
existing manpower figures remain under **Overview**.

1. Open **Staff**, click **Add Staff**, and enter each person's name, staff
   ID, position and **Employment Type** (**Full-Time**, **Part-Time** or
   **Not Set**). Use **Save & Add Another** to enter a roster quickly. Set
   the employment type before using the schedule to fill daily staffing
   figures.
2. Open **Planner** and choose a week. Select a shift code for each person
   and day. Changes save automatically.
3. Enter **Target Manpower** for the week. The **Headcount** row shows
   scheduled working staff each day and highlights days below target in red.
   Only shift codes with start and end times count toward headcount.
4. Super Admin and Administrator users can open **Config** to add or edit
   shift codes, labels, times and colors. Start and End must either both be
   set or both be blank. **Shift codes are shared by all stores**; deleting a
   code used by a schedule is not allowed. Use the small **View** checkbox
   before each code to include or omit it from the generated View legend;
   this does not change assignments, headcounts or exports. RPL means
   Replacement Leave. EL, MC and OPH are hidden from Config; EL/MC are also
   excluded from Planner because they are day-end exceptions entered in Daily
   Operations.
5. Public holidays configured in **Store Management → Daily Sales** are marked
   **PH** in Planner. Blank ABM cells on those dates receive OPH automatically;
   other staff keep their assigned shifts. Opscore never overwrites a manual
   assignment, and removing a holiday removes only its automatic OPH cells.
6. Click **Generate** to open the formatted schedule in **View**. Long names
   use at most two lines, with a compact Position column. Click **Print** to
   print or save as a landscape A4/Letter PDF sized to the printable page.
7. Click **Export CSV** in Planner to save one CSV per staff member who has
   shifts that week. Each file contains `date,shift_code` for import into the
   single-employee detail view in the MyESS HRMS Android app. Files are saved
   at `Documents/Opscore Schedules/<store-code>/<week-start>/`.
8. Click **MyESS** to save one combined batch file for every employee with
   shifts that week. Its columns are `employee_no,date,shift_code`;
   `employee_no` is the employee's Opscore **Staff ID**. Upload
   `MyESS_<store-code>_<week-start>.csv` through the MyESS batch schedule
   workflow. Staff without shifts are omitted. Both exports require a valid
   week; a malformed week produces an error instead of an export.

Deleting a staff member also deletes that person's schedule entries. Viewers
can read schedules but cannot edit them or export CSV files.

## 9. Stock Management

Stock Management is read-only: **Total Loss** and **Loss Ratio** against the
stock loss target, with a breakdown by code, daily and category charts, and a
previous-month comparison.

| Code | Meaning | Effect on Total Loss |
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

Stock loss is entered in **Daily Operations**. Use **Edit** in the daily table
to correct a day.

## 10. Action Tracker

Use it to log every issue until it's fixed.

1. Click **Add Action**.
2. Fill in **Pillar** (Sales Performance, Expenses Management, Manpower
   Efficiency, Stock Management, General Operations), **Issue**, **Owner**,
   **Priority** (Low, Medium, High, Critical), **Due** date and **Next Step**.
3. Click **Save Action**.

Update the **Status** as it moves: **Open**, **In progress**, **Blocked**,
**Done**, **Cancelled**. Setting **Done** defaults the completion date to
today; moving away from **Done** clears it. Completion cannot precede the
raised date.

**Action Summary** shows open, overdue, completed and carried-over actions.
Actions raised in earlier months stay in scope and are labelled **Carried
Over**. Tick **Overdue Only** to see what's late.

## 11. Dashboard and Monthly Report

### Dashboard

The month at a glance for the selected store: sales, expenses, manpower,
stock and actions, with **Alerts** for anything off track. Choose **All
Stores** in the Store selector to see an aggregate across your assigned
stores.

### Monthly Report

The full month-end report across all areas: sales, expenses, manpower, stock
and actions.

The Store selector also offers **All Stores**, a live aggregate report across
your assigned stores. All Stores reports are always live and cannot be
finalized.

Click **Export** to download the selected report as Excel. A live report is
named `opscore-<store>-<month>.xlsx`; a finalized report is named
`opscore-<store>-<month>-finalized-v<version>.xlsx`.

For a single-store report, **Live** shows current figures. Users with
finalization permission can click **Finalize Month**, review the warnings and
confirm to save a finalized version from the current live figures. The
**Finalized** view retains every finalized version and provides a version
selector; draft snapshots are not presented as finalized. Click **Correct
Month** there to create a new version from current live figures; previous
versions are never overwritten. Editing a finalized month shows a warning
that live figures may now differ.

## 12. Settings

The visible tabs depend on your role.

| Tab | Who sees it | What you can do |
|---|---|---|
| Account | Everyone | See your name and email, **Change Password**, manage your **Recovery Key**, **Replay Tour**. |
| Reference Data | Super Admin, Administrator | Manage expense categories per store; upload a Daily Sales Contribution workbook. |
| Data | Super Admin | **Download Backup**; **Restore Database** from a backup file. |
| About | Everyone | See the app name, app version and database schema version. Super Admin also sees the database path. |

### Change Password

Enter your current password and a new one (at least 10 characters, with
letters and digits). Changing your password signs out all other sessions;
this tab stays signed in until you sign out.

### Recovery Key

A single-use key that resets your password on the sign-in page if you forget
it. The key is shown once when issued — save the kit file and keep it
private. **Regenerate Recovery Key** requires your current password and
replaces the previous key immediately.

### Reference Data

- **Expense Categories**: add, rename and reorder categories per store.
  Categories are never deleted; renaming keeps history.
- **Daily Sales Contribution**: upload a DSC workbook (up to 64 MiB) to
  replace the contribution shares and BV/UPT benchmarks for every store. The
  import is all-or-nothing: a rejected workbook changes nothing.

### Backup and restore (Super Admin)

- **Back up regularly**, especially before upgrading or reinstalling. Keep
  the file somewhere safe.
- **Restore** saves the current database automatically, then validates and
  migrates a temporary copy of the uploaded backup. Checks include SQLite
  integrity, foreign keys, required tables and columns, and schema
  compatibility. Only a valid temporary copy replaces the live database; the
  pre-restore backup is kept so a restore can be undone.
- **Automatic backups** are also created before pending startup migrations.
  **Settings → Data** lists pre-migration and pre-restore copies together.
  Only the newest 10 recognized automatic copies are retained. Downloaded
  backups, fresh-start copies and recovery-script copies are never pruned.
- **Restore replaces everything, including logins and passwords** — they come
  from the restored file — and you are signed out afterwards.

## 13. Roles

| Role | Access |
|---|---|
| Super Admin | Everything, all stores, users, backup/restore |
| Administrator | Assigned stores: records, Monthly Setup, finalize reports |
| Zone Manager | Assigned stores: write daily, stock, expenses, actions; read reports |
| Branch Manager | Assigned stores: write daily, stock, expenses, actions; read reports |
| Viewer | Assigned stores: read only |

Access is deny-by-default and enforced server-side. Payroll figures appear
only to Super Admin and Administrator.

## 14. Daily routine

A typical day:

1. Open **Daily Operations**, click **New Entry**, and enter the day's sales,
   staffing and stock loss.
2. Log any expenses in **Expenses Management**.
3. Raise any issues in **Action Tracker** with an owner and due date.
4. Glance at the **Dashboard** for alerts.

At month-end:

1. Complete any missing days.
2. Enter month-end payroll in **Manpower Efficiency**.
3. Open **Monthly Report**, review the finalization warnings, and click
   **Finalize Month**.
4. Click **Export** to download the Excel report.
5. Download a backup from **Settings → Data**.

## 15. Glossary

| Term | Meaning |
|---|---|
| BV | Basket Value: sales ÷ transactions |
| UPT | Units Per Transaction |
| ASP | Average Selling Price: sales ÷ units |
| Level 1–4 | Sales target levels; Levels 2–4 are Level 1 plus an uplift |
| Adjusted Target | A day's target plus shortfall carried forward from earlier days |
| DSC | Daily Sales Contribution: each day type's share of the month |
| Live / Finalized | A report recalculated from current data vs an immutable saved version |
| Recovery key | A single-use code that resets a forgotten password |

## 16. Troubleshooting and FAQ

**The app won't start and mentions port 47811.** Another application is using
the fixed port. Close it and open Opscore again.

**I forgot my password.** Click **Forgot password?** on the sign-in page and
enter your recovery key, then choose a new password. The key is single-use; a
replacement is issued on success. If you have lost both password and recovery
key, an administrator can reset it offline with
`scripts/recover_admin_password.py`.

**I can't see salary figures.** Payroll values appear only to Super Admin and
Administrator.

**My store is read-only.** It is deactivated. A Super Admin can reactivate it
on **Store Management**.

**The month I want isn't in the selector.** The month list covers the current
month and the previous 11 months.

**Where is the log?** `opscore.log` in your data directory
(`~/.local/share/opscore` on Linux, `%LOCALAPPDATA%\Opscore` on Windows).
At startup, a log of 5 MiB or more rotates to `opscore.log.1`; three rotated
archives are retained. A continuously running app's active log can exceed
5 MiB until the next restart.

**The app says my database schema is newer.** Install a current Opscore
release, or restore a compatible backup. Older versions refuse to modify a
newer database.

## 17. Safe shutdown

Closing the Opscore window stops the app. On Linux the backend receives a
termination signal; on Windows a graceful stop is requested first. You can
leave the app running; sessions expire after 12 hours.

If you run Opscore from a terminal or `Opscore.cmd`, closing that window (or
pressing Ctrl+C) stops the app.

## 18. Upgrading

1. Download a backup from **Settings → Data → Download Backup**.
2. Install the new package.
3. On first launch, choose to keep your existing database (the default) when
   asked.

The database is upgraded automatically on first launch; existing records and
accounts are preserved. Before a pending migration, Opscore saves a consistent
copy in `backups/pre-migration-*.db`. Startup stops without migrating if this
backup cannot be created. See [Migrations](MIGRATIONS.md) for details.

## 19. Load demo data

If you'd like to explore Opscore with sample figures instead of real data, you
can load the **AB01 / DEMO** store from a pair of CSV files. This is optional
and intended for trying the app out.

The import is done from a terminal, not from the app window, and the database
must already contain its first account (complete first launch first).

```bash
.venv/bin/python scripts/import_demo.py --db /path/to/store_ops.db daily.csv setup.csv
```

- `daily.csv` holds daily sales rows; `setup.csv` holds the store and monthly
  target setup. Money columns are in RM, uplifts are whole percentages, and
  holidays are ISO dates separated by semicolons. Blank daily values stay
  blank (NULL).
- Re-running the import updates the same records rather than duplicating them.

To use your real desktop database, point `--db` at your data directory
(`~/.local/share/opscore/store_ops.db` on Linux, `%LOCALAPPDATA%\Opscore\store_ops.db`
on Windows). To experiment safely, use a copy of the database or a separate
demo database instead of your live data.
