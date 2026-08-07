# Tasks: bootstrap-mcp-server

> Phase: **0** — Compatibility & security spike
> Goal: real stdio round-trip with both target agents, SDK locked.

## 0.1 Repo & dependencies

- [ ] Create `pyproject.toml` (PEP 621) with `localmcptools` package, Python 3.11+
- [ ] Create `requirements.txt` (no lock yet) with: `mcp`, `pydantic`, `psutil`
- [ ] Create `requirements-dev.txt` with: `pytest`, `pytest-asyncio`, `ruff`, `mypy`
- [ ] Create `.gitignore` (Python + Node + Angular + IDE + `%APPDATA%`-shaped paths)
- [ ] Create `.editorconfig`
- [ ] Create `python -m venv .venv` (development venv; not committed)
- [ ] `pip install -e .` from source

## 0.2 Path & config plumbing

- [ ] `config/paths.py`: resolve `%APPDATA%\LocalMcpTools\` (with `LMCP_DATA_DIR` override env var)
- [ ] `config/paths.py`: create dir if missing on first call
- [ ] `config/defaults.py`: hard-coded defaults matching design.md
- [ ] `config/settings.py`: load `config.json` with defaults merge; never crash on missing file
- [ ] Unit test: missing config.json returns defaults, never raises

## 0.3 SQLite + audit

- [ ] `persistence/db.py`: open `%APPDATA%\LocalMcpTools\audit.sqlite` with WAL
- [ ] `persistence/db.py`: run migrations in `schema_version` table; create `calls` table per design.md
- [ ] `persistence/audit.py`: `record_start(call_id, tool, args_redacted, run_id, profile, policy_version)` inserts `status='running'` row
- [ ] `persistence/audit.py`: `record_finish(call_id, *, ok, error_code, error_message, duration_ms, exit_code, stdout_bytes, stderr_bytes, log_path)` updates the row
- [ ] Unit test: insert then update; verify `finished_at` set; verify timestamps monotonic

## 0.4 Envelope

- [ ] `tools/_common.py`: `ToolMeta`, `ToolError`, `ToolResponse` exactly as in design.md
- [ ] `tools/_common.py`: standard error code list (internal_error, invalid_args, not_implemented, approval_required)
- [ ] Unit test: every error code serializes to JSON without leaking Python types

## 0.5 Server & entry

- [ ] `__main__.py`: parses args (`start` is default for now; `stop`/`status` can stub)
- [ ] `server.py`: assemble `FastMCP("LocalMcpTools")` and register `workspace.inspect` only
- [ ] `server.py`: stdio loop is the **only** transport in this change
- [ ] `tools/workspace.py`: `inspect()` returns `{pid, build: "spike-0"}` with `profile="observe"` and `policy_version="spike-0"`
- [ ] Run from PowerShell: `python -m localmcptools` and confirm it blocks waiting for stdio input (no crash, no port)

## 0.6 sample mcp.json

- [ ] Write `samples/mcp.codebuddy.json` and `samples/mcp.copilot.json` matching design.md
- [ ] README snippet: "where these go for each agent"

## 0.7 Agent integration — codebuddy

- [ ] Configure codebuddy to launch `python -m localmcptools` via stdio
- [ ] Verify tool list contains `workspace.inspect`
- [ ] Invoke `workspace.inspect` from codebuddy; confirm structured response
- [ ] Inspect `audit.sqlite`: 1 row, ok=1, tool=`workspace.inspect`, profile=`observe`
- [ ] Capture exact stderr / log lines that show stdio wiring works; paste into design.md if anything surprising

## 0.8 Agent integration — GitHub Copilot

- [ ] Same as 0.7 but for VS Code Copilot Chat
- [ ] If 0.7 works but 0.8 doesn't: stop, document the gap, **do not lock SDK yet**
- [ ] If both work: lock `mcp` version in requirements.txt; commit exact import paths into design.md

## 0.9 DoD checks (must all pass before archive)

- [ ] codebuddy lists `workspace.inspect` and calls it successfully
- [ ] Copilot lists `workspace.inspect` and calls it successfully
- [ ] Each call lands in `audit.sqlite` with correct envelope fields
- [ ] MCP SDK version pinned in requirements.txt
- [ ] `design.md` documents actual import paths used
- [ ] No HTTP listener is created
- [ ] `localmcptools start` does **not** write `server.json` (stdio mode)
- [ ] Spike takes < 1 day wall clock; if not, escalate to user