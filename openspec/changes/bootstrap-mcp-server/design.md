# Design: bootstrap-mcp-server

## Module layout

```text
src/localmcptools/
├── __init__.py
├── __main__.py            # python -m localmcptools entry
├── cli.py                 # argparse: start | stop | status | install
├── server.py              # FastMCP assembly + stdio loop
├── control_api.py         # (stub for change-5)
├── config/
│   ├── __init__.py
│   ├── paths.py           # resolve %APPDATA%\LocalMcpTools\
│   ├── settings.py        # load config.json (defaults for spike)
│   └── defaults.py
├── persistence/
│   ├── __init__.py
│   ├── db.py              # SQLite + WAL init + migrations
│   └── audit.py           # calls table insert / update
├── tools/
│   ├── __init__.py
│   ├── _common.py         # ToolResponse / ToolMeta / ToolError
│   └── workspace.py       # workspace.inspect stub
└── ui_assets/             # (empty in this change; filled by change-5)

tests/
├── unit/
│   ├── test_envelope.py
│   └── test_paths.py
└── integration/
    ├── test_mcp_discovery.py
    └── test_workspace_inspect_stdio.py
```

## SQLite schema (minimal for spike)

Only the `calls` table — the rest comes in later changes.

```sql
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS calls (
    id              TEXT PRIMARY KEY,        -- UUID v4
    timestamp       INTEGER NOT NULL,        -- unix ms
    agent           TEXT,                    -- 'copilot' | 'codebuddy' | 'unknown'
    client_instance TEXT,                    -- future: from local secret
    tool            TEXT NOT NULL,
    workspace_id    TEXT,                    -- null for spike
    profile         TEXT NOT NULL,           -- 'observe' for spike
    policy_version  TEXT NOT NULL,           -- 'spike-0' for spike
    approval_id     TEXT,                    -- null for spike
    run_id          TEXT NOT NULL,
    args_redacted   TEXT NOT NULL,
    ok              INTEGER NOT NULL,
    error_code      TEXT,
    error_message   TEXT,
    blocked_by      TEXT,                    -- null for spike
    severity        TEXT,                    -- null for spike
    exit_code       INTEGER,                 -- null for spike
    stdout_bytes    INTEGER,                 -- null for spike
    stderr_bytes    INTEGER,                 -- null for spike
    duration_ms     INTEGER NOT NULL,
    log_path        TEXT,                    -- null for spike
    status          TEXT NOT NULL,           -- 'success' | 'failed' | 'invalid_args'
    pid             INTEGER,
    finished_at     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_calls_timestamp ON calls(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_calls_tool ON calls(tool);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
```

## Envelope (frozen here, inherited by all later changes)

```python
# src/localmcptools/tools/_common.py
from typing import Any
from pydantic import BaseModel

class ToolMeta(BaseModel):
    tool: str
    duration_ms: int
    audit_id: str
    log_path: str | None = None
    run_id: str
    output_handle: str | None = None
    next_actions: list[str] = []

class ToolError(BaseModel):
    code: str
    message: str
    suggestion: str | None = None
    blocked_by: str | None = None
    severity: str | None = None
    approval_id: str | None = None

class ToolResponse(BaseModel):
    ok: bool
    data: Any | None = None
    meta: ToolMeta
    error: ToolError | None = None
```

**Frozen because**: every later change writes a new tool spec referencing
this shape. Changing it later breaks the schema-version contract in audit.

## Sample `mcp.json` (Code)

```jsonc
{
  "servers": {
    "localmcptools": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "localmcptools"],
      "cwd": "D:\\AI\\Projects\\LocalMcpTools"
    }
  }
}
```

> The CLI later (change-7) will print this on demand. We hand-write it
> during the spike because the CLI does not exist yet.

## New third-party dependencies

| Package | Why | Lock after spike |
|---|---|---|
| `mcp` (Python SDK) | The whole point of the spike | YES |

The lock happens **only after** stdio round-trip succeeds in both agents.
If a different SDK API surface emerges, `design.md` is updated, then the
lock.

## Config defaults for the spike

```jsonc
{
  "version": 1,
  "server": { "host": "127.0.0.1", "log_level": "INFO" },
  "security": {
    "transport_mode": "stdio",
    "http_shared_mode_enabled": false,
    "redact_before_persist": true
  },
  "workspaces": { "default_profile": "observe" },
  "audit": { "retention_days": 7, "cleanup_interval_hours": 6 }
}
```

No HTTP port is allocated in the spike. server.json is **not** written.

## Spike success / failure criteria

Success = both codebuddy and Copilot can list and call `workspace.inspect`
through stdio, and the call lands in `audit.sqlite` with the correct shape.

Failure scenarios and fallbacks:

- stdio with default args fails → try `pythonw` + `subprocess.DETACHED_PROCESS`
- Agent-specific env var requirements surface → document in this file
- MCP SDK API differs from current docs → update `design.md` and re-lock
- One agent works and the other doesn't → split the spike; do not block
  on the failing agent