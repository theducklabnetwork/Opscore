@AGENTS.md

## Agent sessions
- Opscore is a local Windows/Linux desktop app. There is no hosted deployment pipeline.
- Setup if .venv is missing: pip install uv && uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python -r requirements.lock -r requirements-dev.txt
- Tests: .venv/bin/python -m pytest tests/ -q
- Before opening a PR, the task must pass a Reflex compile with a disposable DB: `DUCKLAB_DB_PATH=$(mktemp -d)/compile.db .venv/bin/reflex export --env prod --frontend-only --no-zip`
- Any app you run for screenshots uses a disposable DB in a temp dir, never .devdata.
- Work on a branch named after the task (e.g. v0-toolchain), push it, open a pull request. Never push to main, never merge.
