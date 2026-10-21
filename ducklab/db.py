"""SQLite data layer for the placement tracker. No ORM, no migrations."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "placement_tracker.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create tables and seed sample data if the database is empty."""
    conn = _connect()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS placements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_name TEXT NOT NULL,
            location TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',  -- active | completed
            notes TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_id INTEGER REFERENCES placements(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'todo',  -- todo | in_progress | done
            due_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_id INTEGER REFERENCES placements(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',  -- low | medium | high
            status TEXT NOT NULL DEFAULT 'open',  -- open | resolved
            raised_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS monthly_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT NOT NULL UNIQUE,  -- YYYY-MM
            summary TEXT NOT NULL
        );
        """
    )
    if cur.execute("SELECT COUNT(*) FROM placements").fetchone()[0] == 0:
        _seed(cur)
    conn.commit()
    conn.close()


def _seed(cur: sqlite3.Cursor) -> None:
    today = date.today()
    start = today.replace(day=1) - timedelta(days=20)
    end = start + timedelta(days=40)
    placements = [
        ("Northgate Market", "Portland, OR", start.isoformat(), end.isoformat(), "active", "First placement of the quarter."),
        ("Riverside Grocer", "Salem, OR", (start - timedelta(days=45)).isoformat(), (start - timedelta(days=5)).isoformat(), "completed", "Smooth handover."),
        ("Hilltop Foods", "Bend, OR", (end + timedelta(days=10)).isoformat(), (end + timedelta(days=50)).isoformat(), "active", "Starts next month."),
    ]
    cur.executemany(
        "INSERT INTO placements (store_name, location, start_date, end_date, status, notes) VALUES (?,?,?,?,?,?)",
        placements,
    )
    tasks = [
        (1, "Kickoff meeting with store manager", "done", start.isoformat()),
        (1, "Shelf audit — aisle 3-5", "in_progress", (today + timedelta(days=2)).isoformat()),
        (1, "POS system check", "todo", (today + timedelta(days=4)).isoformat()),
        (1, "Staff training session", "todo", (today + timedelta(days=9)).isoformat()),
        (1, "Inventory reconciliation", "todo", (today + timedelta(days=14)).isoformat()),
        (2, "Final walkthrough", "done", (start - timedelta(days=6)).isoformat()),
    ]
    cur.executemany(
        "INSERT INTO tasks (placement_id, title, status, due_date) VALUES (?,?,?,?)", tasks
    )
    issues = [
        (1, "Freezer unit temperature fluctuating", "high", "open", (today - timedelta(days=3)).isoformat()),
        (1, "Delivery dock scheduling conflict", "medium", "open", (today - timedelta(days=1)).isoformat()),
        (2, "Broken signage at entrance", "low", "resolved", (start - timedelta(days=30)).isoformat()),
    ]
    cur.executemany(
        "INSERT INTO issues (placement_id, title, severity, status, raised_date) VALUES (?,?,?,?,?)",
        issues,
    )
    month = today.strftime("%Y-%m")
    prev = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    reports = [
        (month, "Placement at Northgate Market is progressing well. Kickoff completed; shelf audit underway. Two open issues being tracked, freezer unit flagged as high severity."),
        (prev, "Wrapped up Riverside Grocer placement. Final walkthrough done, all tasks closed. One low-severity signage issue resolved before handover."),
    ]
    cur.executemany("INSERT INTO monthly_reports (month, summary) VALUES (?,?)", reports)


def _rows(query: str, params: tuple = ()) -> list[dict]:
    conn = _connect()
    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()
    return rows


def get_placements() -> list[dict]:
    return _rows("SELECT * FROM placements ORDER BY start_date DESC")


def get_active_placement_count() -> int:
    today = date.today().isoformat()
    rows = _rows(
        "SELECT COUNT(*) AS c FROM placements WHERE start_date <= ? AND end_date >= ?", (today, today)
    )
    return rows[0]["c"]


def get_open_task_count() -> int:
    return _rows("SELECT COUNT(*) AS c FROM tasks WHERE status != 'done'")[0]["c"]


def get_open_issue_count() -> int:
    return _rows("SELECT COUNT(*) AS c FROM issues WHERE status = 'open'")[0]["c"]


def get_task_status_counts() -> list[dict]:
    return _rows("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status")


def get_upcoming_tasks(limit: int = 5) -> list[dict]:
    return _rows(
        """
        SELECT t.*, p.store_name FROM tasks t
        JOIN placements p ON p.id = t.placement_id
        WHERE t.status != 'done' AND t.due_date >= ?
        ORDER BY t.due_date ASC LIMIT ?
        """,
        (date.today().isoformat(), limit),
    )


def get_tasks() -> list[dict]:
    return _rows(
        """
        SELECT t.*, p.store_name FROM tasks t
        JOIN placements p ON p.id = t.placement_id
        ORDER BY t.due_date ASC
        """
    )


def get_report_months() -> list[str]:
    return [r["month"] for r in _rows("SELECT month FROM monthly_reports ORDER BY month DESC")]


def get_report(month: str) -> str:
    rows = _rows("SELECT summary FROM monthly_reports WHERE month = ?", (month,))
    return rows[0]["summary"] if rows else "No report found for this month."
