# Design: managed-process-and-ports

## Module layout (additions)

```text
src/localmcptools/
├── execution/
│   ├── background.py         # Job Object + streaming log + long-running runner
│   └── runner.py             # extend: detect timeout_ms >= 60_000 and reject
├── process/
│   ├── __init__.py
│   ├── manager.py            # CRUD over background_processes
│   ├── ports.py              # psutil.net_connections() parsing
│   └── presets.py            # registry: python-uvicorn, node-vite, etc.
└── tools/
    └── process.py            # process.start_dev_server / get_status /
                              # stop_managed / list_managed /
                              # list_listening_ports / find_by_port

tests/
├── unit/
│   ├── test_presets.py
│   ├── test_process_manager.py
│   └── test_ports.py
└── integration/
    ├── test_start_dev_server_lifecycle.py
    └── test_job_object_orphan_reconcile.py
```

## Job Object binding (Windows)

Use `pywin32` (`win32job`) — adds one new dependency.

```python
# execution/background.py (sketch)
import win32job
import win32api

def _make_job():
    h = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(h, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(h, win32job.JobObjectExtendedLimitInformation, info)
    return h

def _attach(pid: int, job_handle):
    h_process = win32api.OpenProcess(win32job.PROCESS_TERMINATE, False, pid)
    win32job.AssignProcessToJobObject(job_handle, h_process)
```

The job handle lives on the manager process; closing it (or the manager
process dying) tears down children.

## Background runner

```python
# execution/background.py
async def start_dev_server(workspace_id: str, preset: str, args: list[str]) -> ManagedProcess:
    row = manager.create_row(workspace_id, preset, args)
    job = _make_job()
    proc = await asyncio.create_subprocess_exec(
        *resolved_cmd, cwd=workspace.canonical_root,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env=filtered_env)
    _attach(proc.pid, job)
    row.pid = proc.pid
    asyncio.create_task(_pump_log(row.id, proc))   # append-only artifact
    asyncio.create_task(_await_exit(row.id, proc, job))
    return row
```

## Reconciler

```python
# execution/background.py
async def reconcile_loop():
    while True:
        await asyncio.sleep(30)
        for row in manager.all_running():
            if not psutil.pid_exists(row.pid):
                row.status = 'exited'
                row.exit_code = None        # unknown — process is gone
                row.finished_at = now_ms()
                manager.update(row)
                artifacts.seal(row.log_handle)
```

## Port enumeration

```python
# process/ports.py
import psutil

def list_listening_ports() -> list[PortInfo]:
    out = []
    for c in psutil.net_connections(kind="tcp"):
        if c.status != psutil.CONN_LISTEN: continue
        pid = c.pid  # may be None on Windows for system sockets
        managed = manager.find_by_pid(pid) if pid else None
        out.append(PortInfo(port=c.laddr.port, address=c.laddr.ip,
                            protocol="tcp", pid=pid, managed_id=managed.id if managed else None))
    return out
```

## SQLite schema (no new tables; `background_processes` was added in change-2)

Final columns used in this change:

```sql
-- already exists from change-2:
CREATE TABLE background_processes (
    id          TEXT PRIMARY KEY,
    command     TEXT NOT NULL,
    cwd         TEXT,
    pid         INTEGER NOT NULL,
    log_path    TEXT NOT NULL,
    started_at  INTEGER NOT NULL,
    persistent  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,           -- running | exited
    exit_code   INTEGER,
    finished_at INTEGER
);
```

## Config additions

```jsonc
{
  "process": {
    "managed_max_concurrent": 4,
    "shutdown_grace_seconds": 5,
    "reconcile_interval_seconds": 30,
    "long_running_threshold_ms": 60000
  }
}
```

## New dependencies

| Package | Why |
|---|---|
| `pywin32` | `win32job` for Job Object creation and child binding |

Locked after this change's integration tests pass on Windows.

## Out-of-scope reminders

- No `process.kill(pid)` exposed — by design.
- No UI for process management — change-5.
- No managed processes for non-Windows — Job Object is Windows-only;
  the module exposes a no-op shim for the Linux/macOS stub path.