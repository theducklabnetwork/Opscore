"""Build every registered page function: a missing attribute (e.g. calling
ui.shell when the shell lives in ops_shell) fails the suite instead of the
app at startup.

Run: python -m pytest tests/test_pages_build.py -v
"""

import unittest

from ducklab import auth_pages, ops_pages
from ducklab import (
    ops_actions_pages, ops_daily_pages, ops_expenses_pages,
    ops_manpower_pages, ops_sales_pages, ops_settings_pages,
    ops_stock_pages, ops_stores_pages,
)


class TestPagesBuild(unittest.TestCase):
    def test_registered_pages_build(self):
        # Every page function registered in ducklab.ducklab (the ops app).
        for fn in (
            auth_pages.login_page,
            auth_pages.root_page,
            ops_pages.dashboard_page,
            ops_pages.report_page,
            ops_daily_pages.daily_page,
            ops_expenses_pages.expenses_page,
            ops_manpower_pages.manpower_page,
            ops_stock_pages.stock_page,
            ops_stores_pages.stores_page,
            ops_sales_pages.sales_page,
            ops_actions_pages.actions_page,
            ops_settings_pages.settings_page,
        ):
            with self.subTest(page=fn.__module__ + "." + fn.__name__):
                component = fn()
                self.assertIsNotNone(component)


if __name__ == "__main__":
    unittest.main()
