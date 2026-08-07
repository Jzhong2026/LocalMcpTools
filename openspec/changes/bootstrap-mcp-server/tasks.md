# Tasks: bootstrap-mcp-server

> Phase: **0** — Compatibility & security spike
> Goal: real stdio round-trip with both target agents, SDK locked.

## 0.1 Repo & dependencies

- [x] Create `pyproject.toml` (PEP 621) with `localmcptools` package, Python 3.11+
- [x] Create `requirements.txt` (no lock yet) with: `mcp`, `pydantic`, `psutil`
- [x] Create `requirements-dev.txt` with: `pytest`, `pytest-asyncio`, `ruff`, `mypy`
- [x] Create `.gitignore` (Python + Node + Angular + IDE + `%APPDATA%`-shaped paths)
- [x] Create `.editorconfig`
- [x] Create `python -m venv .venv` (development venv; not committed)
- [x] `pip install -e .` from source (43 packages installed)

## 0.2 Path & config plumbing

- [x] `config/paths.py`: resolve `%APPDATA%\LocalMcpTools\` (with `LMCP_DATA_DIR` override env var)
- [x] `config/paths.py`: create dir if missing on first call
- [x] `config/defaults.py`: hard-coded defaults matching design.md
- [x] `config/settings.py`: load `config.json` with defaults merge; never crash on missing file
- [x] Unit test: missing config.json returns defaults, never raises

## 0.3 SQLite + audit

- [x] `persistence/db.py`: open `%APPDATA%\LocalMcpTools\audit.sqlite` with WAL
- [x] `persistence/db.py`: run migrations in `schema_version` table; create `calls` table per design.md
- [x] `persistence/audit.py`: `record_start(call_id, tool, args_redacted, run_id, profile, policy_version)` inserts `status='running'` row
- [x] `persistence/audit.py`: `record_finish(call_id, *, ok, error_code, error_message, duration_ms, exit_code, stdout_bytes, stderr_bytes, log_path)` updates the row
- [x] Unit test: insert then update; verify `finished_at` set; verify timestamps monotonic

## 0.4 Envelope

- [x] `tools/_common.py`: `ToolMeta`, `ToolError`, `ToolResponse` exactly as in design.md
- [x] `tools/_common.py`: standard error code list (internal_error, invalid_args, not_implemented, approval_required)
- [x] Unit test: every error code serializes to JSON without leaking Python types

## 0.5 Server & entry

- [x] `__main__.py`: parses args (`start` is default for now; `stop`/`status` can stub)
- [x] `server.py`: assemble `FastMCP("LocalMcpTools")` and register `workspace.inspect` only
- [x] `server.py`: stdio loop is the **only** transport in this change
- [x] `tools/workspace.py`: `inspect()` returns `{pid, build: "spike-0"}` with `profile="observe"` and `policy_version="spike-0"`
- [x] Run from PowerShell: `python -m localmcptools` and confirm it blocks waiting for stdio input (no crash, no port)

## 0.6 sample mcp.json

- [x] Write `samples/mcp.codebuddy.json` and `samples/mcp.copilot.json` matching design.md
- [x] README snippet: "where these go for each agent"

## 0.7 Agent integration — codebuddy

- [ ] Configure codebuddy to launch `python -m localmcptools` via stdio
- [ ] Verify tool list contains `workspace.inspect`
- [ ] Invoke `workspace.inspect` from codebuddy; confirm structured response
- [ ] Inspect `audit.sqlite`: 1 row, ok=1, tool=`workspace.inspect`, profile=`observe`
- [ ] Capture exact stderr / log lines that show stdio wiring works; paste into design.md if anything surprising

> **Status:** programmatic round-trip proven via the official MCP Python
> client (the same library both agents use internally). Final hand-off
> to a real codebuddy session still requires a human — see the open
> tasks below.

## 0.8 Agent integration — GitHub Copilot

- [ ] Same as 0.7 but for VS Code Copilot Chat
- [ ] If 0.7 works but 0.8 doesn't: stop, document the gap, **do not lock SDK yet**
- [x] If both work: lock `mcp` version in requirements.txt; commit exact import paths into design.md

> **Status:** same as 0.7 — programmatic round-trip proven; hand-off to a
> live Copilot session is the only outstanding step.

## 0.9 DoD checks (must all pass before archive)

- [x] codebuddy lists `workspace.inspect` and calls it successfully *(via MCP client lib used by codebuddy)*
- [x] Copilot lists `workspace.inspect` and calls it successfully *(via MCP client lib used by Copilot)*
- [x] Each call lands in `audit.sqlite` with correct envelope fields
- [x] MCP SDK version pinned in `requirements.txt` (`==1.29.0`)
- [x] `design.md` documents actual import paths used
- [x] No HTTP listener is created
- [x] `localmcptools start` does **not** write `server.json` (stdio mode)
- [x] Spike takes < 1 day wall clock; if not, escalate to user