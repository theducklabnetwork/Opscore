# Placement Tracker Dashboard

A Reflex work dashboard for a support team that works at one store at a time for about a month, then writes a monthly report.

## Features

- **Dashboard** — KPI cards (active placements, open tasks, open issues), tasks-by-status pie chart, upcoming tasks list.
- **Placements** — table of all store placements with dates and status.
- **Tasks** — table of all tasks with store, due date, and status.
- **Monthly Report** — pick a month, view the summary, copy it with one click.

Dark mode first with a light/dark toggle (top-right). Sidebar navigation on desktop, top links on mobile. Data lives in SQLite (`ducklab/placement_tracker.db`) via plain `sqlite3` in `ducklab/db.py`, seeded with sample data on first run.

## Requirements

- Python 3.14+ with `venv`
- Node.js 20+

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install reflex
```

(The project was scaffolded with `reflex init --template blank`.)

## Run

```bash
.venv/bin/reflex run
```

Then open http://localhost:3000 (frontend) — the backend API runs on http://localhost:8000.

To run only the backend or frontend: `reflex run --backend-only` / `reflex run --frontend-only`.

## Structure

- `ducklab/ducklab.py` — app, state, pages, theme
- `ducklab/db.py` — SQLite schema, seed data, queries
- `rxconfig.py` — Reflex config
