#!/usr/bin/env python3
"""Seed Action Tracker data into the dev DB for UI testing.
--dev: targets ONLY /home/duck/project/ducklab/.devdata/dev.db (refuses any
other path). Idempotent: skips if DEV actions already exist for the store.
Seeds store A01 (Alpha Pavilion DEV) with ~8 actions covering every status
and priority, two overdue, one carried over from the previous month,
one Done, one Cancelled. Labels every issue with [DEV].
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEV_DB = Path("/home/duck/project/ducklab/.devdata/dev.db")
STORE_CODE = "A01"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dev", action="store_true",
                    help=f"seed ONLY the dev DB at {DEV_DB}")
    args = ap.parse_args()
    if not args.dev:
        print("Refusing: this seed targets ONLY .devdata/dev.db; pass --dev")
        return 2

    from ducklab.app_service import AppService
    app = AppService(DEV_DB)
    admin = app.auth.get_user(1)
    sid = app.read_repo.get_store_by_code(STORE_CODE).id
    existing = [a for a in app.get_actions(admin, sid)
                if a.issue and a.issue.startswith("[DEV]")]
    if existing:
        print(f"dev DB already seeded with {len(existing)} DEV actions "
              f"— nothing changed")
        return 0

    today = date.today()
    prev_month = (today.replace(day=1) - timedelta(days=1)).replace(day=10)
    cases = [
        # (raised, pillar, issue, priority, status, due offset, completion)
        (today - timedelta(days=9), "Sales Performance",
         "[DEV] Weekend promo underperforming vs plan", "High", "Open", 5, None),
        (today - timedelta(days=7), "Stock Management",
         "[DEV] DMO losses trending above target", "Critical", "In progress",
         4, None),
        (today - timedelta(days=6), "Expenses Management",
         "[DEV] Electricity bill missing receipt", "Medium", "Blocked", 6, None),
        (today - timedelta(days=5), "Manpower Efficiency",
         "[DEV] Part-timer schedule gap on weekends", "Medium", "Open",
         -2, None),                                    # overdue #1
        (today - timedelta(days=4), "General Operations",
         "[DEV] POS printer firmware update", "Low", "In progress",
         -1, None),                                     # overdue #2
        (prev_month, "Stock Management",
         "[DEV] Reconcile September stocktake variance", "High", "Open",
         3, None),                                      # carried over
        (today - timedelta(days=3), "Sales Performance",
         "[DEV] Update window display for payday week", "Low", "Done",
         1, today - timedelta(days=1)),
        (today - timedelta(days=2), "General Operations",
         "[DEV] Old signage removal (supplier cancelled)", "Low", "Cancelled",
         7, None),
    ]
    for raised, pillar, issue, priority, status, due_off, completion in cases:
        app.save_action(
            admin, sid, None, today, raised_date=raised, pillar=pillar,
            issue=issue, priority=priority, status=status, pic="DEV Owner",
            due_date=today + timedelta(days=due_off),
            corrective_action="[DEV] follow up in daily huddle",
            completion_date=completion, remarks=None)
    print(f"Seeded {len(cases)} DEV actions for {STORE_CODE} into {DEV_DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
