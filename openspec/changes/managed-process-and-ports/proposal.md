# Change: managed-process-and-ports

> Covers: `docs/implementation-plan.md` **Phase 3 — Managed background processes + port management**

## Intent

Solve the second-most-reported pain: **"agent started a dev server and
forgot about it."** This change introduces a **lifecycle-bound** notion of
"process this server started" so that:

1. Agents can start a long-running command and continue working.
2. The server owns those processes via Windows Job Object so they die when
   the server dies (no orphaned `node`/`uvicorn` after server restart).
3. The server can stop, list, and tail those processes.
4. The server can answer "what's listening on port 8080" — and propose
   "kill the *managed* process holding it", **never** arbitrary PID kill.

After this change, an agent that needs a dev server can use one tool to
start it and one tool to stop it, without leaving zombies behind.

## Scope

### In scope

- `process.start_dev_server` tool behind `managed_process` profile
- `process.get_status`, `process.stop_managed`, `process.list_managed`
- `process.list_listening_ports` (read-only, callable in any profile)
- Windows Job Object binding for child processes
- `background_processes` SQLite table
- Reconciler that syncs Job Object child status to DB every 30s
- Server-exit hook: close Job Object → child processes die
- Preset registry for `dev_server` (e.g. `python-uvicorn`, `node-vite`)
- Long-running commands write to a streaming artifact (line-buffered)

### Out of scope

- Arbitrary `process.kill(pid)` — **deliberately not exposed**
- `shell.run_command` long-running pattern (it has a timeout)
- `interactive_ui` profile usage — change-6
- UI for managing these processes — change-5

## Approach

1. **Ownership = Job Object**. Every child process spawned by this server is
   attached to a Windows Job Object configured with
   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. This is the OS-level guarantee
   that no zombie survives server death.
2. **`process.start_dev_server` is the only entry point** for long-running
   processes. `shell.run_command` is rejected for `timeout_ms >= 60_000`
   with a `next_actions` hint to use `start_dev_server` instead.
3. **Read-only port queries** are observable in `observe` profile.
   Resolution goes "port → managed process" first; only if the port is held
   by a non-managed process do we surface that fact (and refuse to kill).
4. **Reconciler** runs every 30s; updates `background_processes.status`,
   writes `exit_code` when processes exit, and frees slots in the
   `ConcurrencyGate` from change-3.

## Why this matters later

- change-5 wires `process.list_managed` into the UI dashboard.
- change-7 (packaging) ties process lifecycle to "user closes the UI =
  stop managed processes" semantics.
- Without this change, every other tool that needs a server (e.g. an
  integration test fixture in change-7) leaks processes.

## Affected components

| Component | Notes |
|---|---|
| `execution/background.py` | new — Job Object + long-running runner |
| `execution/runner.py` | detect long-running → delegate to background |
| `process/manager.py` | new — list_managed, get_status, stop_managed |
| `process/ports.py` | new — list_listening_ports via psutil.net_connections |
| `tools/process.py` | new |
| `persistence/audit.py` | schema bump (background_processes already in change-2) |
| `cli.py` | server-exit hook: close Job Object before exit |

## Key non-regression

- `process.kill(pid)` is **not** added. The user is expected to either
  reconfigure their app, restart the OS service, or use the dev server's
  own shutdown endpoint.
- `shell.run_command` does **not** gain `run_async`. Long-running work
  always goes through `start_dev_server`.
- A managed process cannot outlive its server. If the user needs that,
  the answer is "register it as a Windows service yourself", not "extend
  LocalMcpTools".