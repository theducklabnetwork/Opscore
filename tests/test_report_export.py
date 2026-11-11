"""Excel monthly-report export tests (temporary databases and in-memory files)."""

import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

from ducklab import ops_finalize
from ducklab import ops_state
from ducklab import store_ops_db
from ducklab.app_service import AppService
from ducklab.operations_repository import OperationsRepository
from ducklab.operations_service import assemble_month
from ducklab.operations_write_service import OperationsWriteService
from ducklab.report_export import DATE_FORMAT, MONEY_FORMAT, PERCENT_FORMAT
from ducklab.report_export import export_bytes, export_filename


def rm(value) -> int:
    return int(Decimal(str(value)) * 100)


class ReportExportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "export_test.db"
        store_ops_db.init_db(self.db_path)
        self.write = OperationsWriteService(self.db_path)
        self.repo = OperationsRepository(self.db_path)
        self.store_id = self.write.register_store("S01", "Store A")
        self.write.configure_month(
            self.store_id, "2026-09", level1_base_cents=rm(200000),
            min_rtm_target_cents=rm(200000), level2_pct=0.1, level3_pct=0.2,
            level4_pct=0.3, sales_last_year_cents=rm(250000),
            salary_ratio_target=0.06818, opening_headcount_ft=9,
            turnover_threshold=0.45, stock_loss_target=0.015,
            part_timer_factor=0.5, salary_basic_allowance_cents=rm(10000),
            overtime_cents=rm(1000))
        # create_store() seeds the default categories; reuse the seeded
        # "Electricity" category instead of inserting a duplicate.
        self.category = next(
            c.id for c in self.repo.get_expense_categories(self.store_id)
            if c.name == "Electricity")
        self.write.set_expense_budget(self.store_id, "2026-09", self.category,
                                      rm(4000))
        self.write.record_daily(
            self.store_id, date(2026, 9, 1), sales_cents=rm(10000),
            transactions=100, units=300, ft_working=7, pt_working=1,
            stock_losses={"DMO": rm(70), "EXTRA": rm(5)})
        self.write.record_expense(
            self.store_id, incurred_date=date(2026, 9, 1),
            paid_date=date(2026, 9, 2), category_id=self.category,
            description="September bill", amount_cents=rm(3000),
            paid_by="HQ / Central", status="Paid", verification="Verified",
            recorded_by="Staff A")
        self.write.raise_action(
            self.store_id, date(2026, 9, 1), "Sales Performance",
            "Improve pace", "High", due_date=date(2026, 9, 5))
        report = assemble_month(self.repo, self.store_id, "2026-09",
                                as_of=date(2026, 9, 30))
        self.assertEqual(export_filename(report), "opscore-S01-2026-09.xlsx")
        self.book = load_workbook(BytesIO(export_bytes(report, self.repo)))

    def tearDown(self):
        self._tmp.cleanup()

    def test_file_opens_with_expected_sheets_and_formats(self):
        self.assertEqual(self.book.sheetnames, [
            "Summary", "Daily Sales", "Expenses", "Manpower", "Stock Loss", "Actions",
        ])
        for sheet in self.book.worksheets:
            self.assertEqual(sheet.freeze_panes, "A2")
            self.assertTrue(all(cell.value[0].isupper() for cell in sheet[1]))
            self.assertGreater(sheet.column_dimensions["A"].width, 0)

        daily = self.book["Daily Sales"]
        self.assertIsInstance(daily["A2"].value, datetime)
        self.assertEqual(daily["A2"].number_format, DATE_FORMAT)
        self.assertEqual(daily["B2"].value, 10000)
        self.assertEqual(daily["B2"].number_format, MONEY_FORMAT)
        self.assertEqual(self.book["Expenses"]["E2"].value, 3000)
        self.assertEqual(self.book["Expenses"]["E2"].number_format, MONEY_FORMAT)
        self.assertEqual(self.book["Manpower"]["B9"].value, 1.1)
        self.assertEqual(self.book["Manpower"]["B9"].number_format, PERCENT_FORMAT)

    def test_literal_kpis_and_source_records(self):
        summary = {row[0]: row[1] for row in self.book["Summary"].iter_rows(
            min_row=2, max_col=2, values_only=True)}
        self.assertEqual(summary["Sales MTD"], 10000)
        self.assertEqual(summary["BV"], 100)
        self.assertEqual(summary["UPT"], 3)
        self.assertEqual(summary["Expenses Paid"], 3000)
        self.assertEqual(summary["Closing Headcount"], 9)
        self.assertEqual(summary["Total Stock Loss"], 65)
        self.assertEqual(summary["Actions Overdue"], 1)
        self.assertIsNone(summary["Growth vs Last Month"])

        expenses = self.book["Expenses"]
        self.assertEqual(expenses["C2"].value, "Electricity")
        self.assertEqual(expenses["D2"].value, "September bill")
        self.assertEqual(self.book["Actions"]["C2"].value, "Improve pace")
        self.assertEqual(self.book["Stock Loss"]["C4"].value, 65)

    def test_blank_day_stays_blank_not_zero(self):
        daily = self.book["Daily Sales"]
        self.assertEqual(daily["A3"].value.date(), date(2026, 9, 2))
        for cell in daily[3][1:]:
            self.assertIsNone(cell.value)

    def test_payroll_can_be_redacted(self):
        report = assemble_month(self.repo, self.store_id, "2026-09")
        book = load_workbook(BytesIO(export_bytes(report, self.repo,
                                                  include_payroll=False)))
        summary = {row[0]: row[1] for row in book["Summary"].iter_rows(
            min_row=2, max_col=2, values_only=True)}
        self.assertIsNone(summary["Salary Cost"])
        self.assertIsNone(summary["Salary Ratio"])
        self.assertIsNone(book["Manpower"]["B8"].value)
        self.assertEqual(book["Manpower"]["B11"].value, "Restricted")


class ReportExportEventTest(unittest.TestCase):
    def test_download_uses_selected_store_month_and_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "export_event.db"
            app = AppService(db_path)
            admin = app.auth.bootstrap_first_admin(
                "admin@ducklab.com", "Root Admin", "Password123!")
            store_ops_db.init_db(db_path)
            store_id = app.register_store(admin, "S01", "Store A")
            app.configure_month(admin, store_id, "2026-09",
                                level1_base_cents=rm(200000), level2_pct=0.1,
                                level3_pct=0.2, level4_pct=0.3)
            state = ops_state.OpsState()
            state.stores = [{"id": store_id, "label": "S01 — Store A"}]
            state.selected_store_label = "S01 — Store A"
            state.report_mode = "live"
            with mock.patch.object(ops_state, "app_service", app), \
                    mock.patch.object(ops_finalize, "app_service", app), \
                    mock.patch.object(ops_state.OpsState, "_actor",
                                      lambda _: admin), \
                    mock.patch.object(ops_state.OpsState, "_selected_month",
                                      lambda _: "2026-09"), \
                    mock.patch.object(ops_finalize.rx, "download",
                                      side_effect=lambda **kw: kw) as download:
                result = state.export_report()
            self.assertEqual(result["filename"], "opscore-S01-2026-09.xlsx")
            self.assertEqual(load_workbook(BytesIO(result["data"])).sheetnames, [
                "Summary", "Daily Sales", "Expenses", "Manpower", "Stock Loss", "Actions",
            ])
            download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
