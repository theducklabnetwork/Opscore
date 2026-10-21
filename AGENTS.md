<!-- reflex managed begin (do not edit inside this block; add custom content outside the markers) -->
# Agents

This project uses the **Reflex agent skills** — up-to-date knowledge for building full-stack Python web apps with [Reflex](https://reflex.dev). Install them first, then use them as described below.

## Installing the Skills

Before working on this project, make sure the Reflex skills are available to you.

### Claude Code

If the skills are not already available, install them yourself by running these shell commands:

```bash
claude plugin marketplace add reflex-dev/agent-skills
claude plugin install reflex@reflex-agent-skills
```

Newly installed plugins load on the next session. After installing, tell the user to restart the session so the skills auto-load; until then, read the skill files directly from `~/.claude/plugins/` when you need them.

### Other agents (Cursor, OpenCode, Codex, Pi)

```
npx skills add reflex-dev/agent-skills
```

Or clone https://github.com/reflex-dev/agent-skills and copy the `skills/` folders into your agent's skill directory (see the repo README for paths).

### Verifying

Before writing or editing any Reflex code, confirm these three skills are available: `reflex-docs`, `setup-python-env`, and `reflex-process-management`. If they are not, STOP and run the install step above — do not proceed without them.

## Using the Skills

### Reflex documentation

For anything about Reflex APIs — components, state management, events, styling, database, routing, authentication — use the **reflex-docs** skill rather than relying on memory. It carries current, version-accurate docs.

### Initializing a new Reflex project

When starting a new Reflex project or setting up a development environment, you **must** follow the **setup-python-env** skill before doing anything else.

Do not skip any steps. Do not assume a virtual environment or Reflex is already available — always verify first by following the skill's instructions in order.

After the environment is ready and Reflex is installed, run:

```bash
reflex init
```

Then proceed with the user's request.

### Managing a Reflex process

When you need to compile, run, reload, or debug a Reflex application, follow the **reflex-process-management** skill for the correct sequence and error investigation steps.
<!-- reflex managed end -->

## Release management (Deployment Task 3)

This project has a versioned release pipeline under `deploy/`:

- `deploy/release.sh` — prepare / verify-staged / activate / rollback /
  status / history (all unprivileged). `prepare --src <snapshot.tar.gz|dir>`
  consumes a published archive (independently re-validated, extracted with
  `--no-same-owner`) or a source dir; tests run in the release's own
  `.venv-test` (requirements.lock + requirements-dev.txt, created in place)
  — never the dev venv. Production `.venv` stays free of pytest/playwright.
  The operator-installed trusted CLI copy lives in `deploy/cli/`
  (`/opt/ducklab-cli`); a test enforces it stays in sync.
- `deploy/publish-source.sh` — run as `duck`: packs a filtered,
  double-audited `source.tar.gz` into `/opt/ducklab-staging/src-<ts>/`
  (dir+archive inherit group `ducklab` via the setgid root, mode 2750).
  `release.sh prepare --src <archive>` re-validates and extracts it as the
  release identity (`--no-same-owner`, dirs 750/files 640, exec restored).
  `ducklab` never needs any access to `/home/duck`.
- `deploy/release_lib.sh` — shared library; `release_verify_venv` enforces
  the "venv created in place, never relocated" rule (the original
  203/EXEC defect fix).
- `deploy/simulate.sh` — full-cycle simulation in disposable dirs (26 checks).
- `deploy/acceptance.sh` — browser regression suites against a staged prod
  build (each suite with a freshly seeded disposable DB; seeds:
  seed_ui_ops/daily/expenses/manpower per suite contract).
- `deploy/test_release_permissions.sh` — filesystem-level permission-model
  verification with a throwaway uid (operator-run; needs sudo once).
- `deploy/RELEASES.md`, `deploy/OPERATOR-RELEASE.md`, `deploy/MIGRATIONS.md`
  — design, operator runbook, migration policy.
- `requirements.txt` (direct deps) + `requirements.lock` (pinned manifest,
  authoritative for production) + `requirements-dev.txt` (test-only).
- `ducklab/db_inspect.py` — read-only schema version/compatibility checks.

Rules: production releases install ONLY from `requirements.lock`; venvs are
created at their final absolute path; rollback never touches the database;
privileged steps are printed for the operator, never executed by the agent.

Run everything: `.venv/bin/python -m pytest tests/ -q` and
`deploy/simulate.sh`.
