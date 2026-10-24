# DuckLab

DuckLab is a Reflex web application for store operations monitoring. It tracks
sales targets, daily activity, expenses, manpower, stock losses, and corrective
actions by store and month, with role-based access and versioned monthly reports.

Data is stored in SQLite. Set `DUCKLAB_DB_PATH` to choose the operations database.
Money is stored as integer cents, and blank values remain distinct from zero.

## Setup

Requires Python 3.14+ and Node.js 20+.

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements.lock -r requirements-dev.txt
```

## Run

```bash
.venv/bin/reflex run
```

The frontend runs on http://localhost:3000 and the backend on
http://localhost:8000. Bootstrap the first administrator before signing in.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Structure

- `ducklab/ducklab.py` — Reflex application and route registration
- `ducklab/kpi.py` — business calculations
- `ducklab/ops_*` — operational pages, state, and charts
- `ducklab/store_ops_db.py` — SQLite schema and migrations
- `scripts/` — import, development seed, and browser-check utilities
- `deploy/` — release and operator tooling
