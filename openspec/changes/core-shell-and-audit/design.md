# Design: core-shell-and-audit

## Module layout (additions on top of change-1)

```text
src/localmcptools/
├── execution/
│   └── encoding.py            # chardet wrapper used by change-3 shell tools
├── persistence/
│   ├── artifacts.py           # write + ACL + retention
│   └── audit.py               # extended schema (see below)
├── safety/
│   └── redact.py              # token scrubber
├── workspaces/
│   ├── __init__.py
│   ├── registry.py            # register / list / resolve / canonicalize
│   └── inspect.py             # project_type / git / presets / runtimes
└── tools/
    ├── environment.py         # environment.get
    ├── workspace.py           # workspace.inspect / search_text
    ├── fs.py                  # fs.read_range / tail_log_file / grep_files
    └── output.py              # output.tail / read_range / search

tests/
├── unit/
│   ├── test_workspace_registry.py
│   ├── test_redact.py
│   ├── test_artifact_acl.py
│   └── test_path_escape.py
└── integration/
    ├── test_environment_get.py
    └── test_workspace_inspect_e2e.py
```

## SQLite schema (additions)

```sql
CREATE TABLE IF NOT EXISTS workspaces (
    id              TEXT PRIMARY KEY,
    canonical_root  TEXT NOT NULL,
    registered_at   INTEGER NOT NULL,
    profile         TEXT NOT NULL DEFAULT 'observe',
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_workspaces_canonical ON workspaces(canonical_root);

CREATE TABLE IF NOT EXISTS artifacts (
    handle          TEXT PRIMARY KEY,        -- 'art://2026-08-07/calls/<id>.log'
    path            TEXT NOT NULL,           -- absolute, ACL-protected
    call_id         TEXT NOT NULL,
    bytes_total     INTEGER NOT NULL,
    line_count      INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    expires_at      INTEGER,                -- null = use retention default
    sensitive       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_artifacts_call ON artifacts(call_id);

-- Migration path: bump schema_version from 1 -> 2 here.
```

## Artifact directory layout

```text
%APPDATA%\LocalMcpTools\
└── artifacts\
    └── YYYY-MM-DD\
        └── calls\
            └── <call_id>.log      # each call gets its own log
```

ACL on Windows: `icacls <path> /inheritance:r /grant:r "%USERNAME%:R"`
(run via `subprocess.run` at write time; failure to set ACL = treat as
`redaction_failed` and abort the persist).

## Workspace registry

```python
# workspaces/registry.py (sketch)
def register(path: str, profile: str = "observe") -> Workspace:
    canonical = os.path.realpath(path)
    if not canonical.startswith(<safe prefix or absolute>):
        raise InvalidPath("path cannot be canonicalized")
    if not os.path.isdir(canonical):
        raise InvalidPath("path is not a directory")
    workspace_id = uuid4().hex
    db.execute(
        "INSERT INTO workspaces (id, canonical_root, registered_at, profile) "
        "VALUES (?, ?, ?, ?)",
        (workspace_id, canonical, now_ms(), profile))
    return Workspace(id=workspace_id, canonical_root=canonical, profile=profile)
```

`resolve(workspace_id)` is the only function later tools call. Path-escape
prevention happens here: every `path` arg in `fs.*` is checked
`os.path.realpath(path).startswith(workspace.canonical_root)`.

## Redaction (`safety/redact.py`)

Ordered rules — first match wins, but all rules run by default to catch
unknown patterns:

```python
PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?([^\s'\"]{4,})"), r"\1: ***"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "***.***.***"),
    # .env style
    (re.compile(r"(?im)^(\s*[A-Z_][A-Z0-9_]*)\s*=\s*([^\s#]+)"),
     lambda m: f"{m.group(1)}=***" if _looks_like_secret(m.group(1)) else m.group(0)),
]
```

`_looks_like_secret` is a curated denylist of common env var names.

## Audit field additions

`record_start` now takes `client_instance` (from local bearer secret, or
`null` for stdio-only), `workspace_id` (or `null`).

`record_finish` now writes `approval_id` (always `null` in this change;
populated starting change-3) and the artifact `handle` to the row's
`log_path` field (re-using the column rather than adding a new one).

## New dependencies (none new — chardet was already in plan)

This change does **not** introduce a new third-party dependency.
`psutil`, `chardet`, and `pydantic` are already in `requirements.txt`
from change-1.

## Out-of-scope reminders

- **No `shell.run_command`** — added in change-3.
- **No HTTP server** — change-5.
- **No dev server lifecycle** — change-4.
- **No UI** — change-5.

If a task in this change's `tasks.md` reaches for those, stop and file a
new change.