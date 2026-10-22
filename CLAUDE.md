@AGENTS.md

## Cloud sessions (claude.ai/code)
- There is NO dev instance, NO .devdata/, NO workbook here. Do not run scripts/dev-live.sh, --verify, deploy/ scripts or anything needing .devdata; list them in the report under "To run on the VPS".
- Setup if .venv is missing: pip install uv && uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python -r requirements.lock -r requirements-dev.txt
- Tests: .venv/bin/python -m pytest tests/ -q
- Any app you run for screenshots uses a disposable DB in a temp dir, never .devdata.
- Work on a branch named after the task (e.g. v0-toolchain), push it, open a pull request. Never push to main, never merge.
