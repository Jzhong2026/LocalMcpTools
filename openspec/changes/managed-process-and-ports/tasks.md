# Tasks: managed-process-and-ports

> Phase: **3** — Managed dev servers + port queries
> Goal: agent can start, observe, and stop a long-running process that dies
> with the server.

## 3.1 Preset registry

- [x] `process/presets.py`: registry `dict[str, Preset]` with at minimum
  `python-uvicorn`, `node-vite`, `node-next-dev`, `dotnet-run`
- [x] Each `Preset` declares `command_template`, `default_args`, `env_required`,
  `port_hint_regex` (for detecting bound port from startup output)
- [x] `resolve(workspace_id, preset_name, args)` returns the resolved argv
- [x] Unit test: each preset resolves correctly
- [x] Unit test: unknown preset → `unknown_preset`

## 3.2 Job Object binding

- [x] `execution/background.py`: `_make_job()` creates Job Object with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
- [x] `execution/background.py`: `_attach(pid, handle)` uses
  `win32job.AssignProcessToJobObject`
- [x] Add `pywin32` to `requirements.txt`
- [x] Unit test (xfail on non-Windows): child PID is terminated when handle is closed

## 3.3 Background runner

- [x] `execution/background.py`: `start_dev_server(workspace_id, preset, args)`
- [x] `execution/background.py`: spawn subprocess, attach to job, kick off
  log-pump and await-exit tasks
- [x] `execution/background.py`: log pump appends to artifact line-by-line
  (buffered, max 10ms latency to disk)
- [x] `execution/background.py`: await-exit updates `background_processes.status`
  and seals the artifact
- [x] Unit test: returns within 1s (the runner is async)
- [x] Integration test: `python -m http.server` started as
  `preset=python-uvicorn` → port 8000 appears in `list_listening_ports`
  within 5s

## 3.4 Reject long-running shell

- [x] `execution/runner.py`: if `timeout_ms >= 60_000`, raise `UseStartDevServer`
  with `next_actions` listing the relevant preset
- [x] Unit test: 60s shell call rejected with the right error code

## 3.5 Manager (DB + lifecycle)

- [x] `process/manager.py`: `create_row`, `update`, `find_by_id`,
  `find_by_pid`, `all_running`, `list_managed`
- [x] `process/manager.py`: stop graceful = `taskkill /T` → wait 5s →
  escalate to `taskkill /T /F`
- [x] Unit test: stop sequence recorded correctly
- [x] Unit test: unknown id → `managed_process_not_found`

## 3.6 Reconciler

- [x] `execution/background.py`: `reconcile_loop()` runs every 30s
- [x] Reconcile: any row with `status='running'` whose `pid` is no longer
  alive → mark `status='exited'`, `exit_code=null`, seal artifact
- [x] Cancel previous loop task on server shutdown
- [x] Unit test (xfail non-Windows): fake-kill a child → reconcile marks it exited

## 3.7 `tools/process.py`

- [x] `process.start_dev_server` (managed_process profile, approval required)
- [x] `process.get_status` (any profile, by id)
- [x] `process.list_managed` (any profile, optional workspace filter)
- [x] `process.stop_managed` (managed_process profile, approval required)
- [x] `process.list_listening_ports` (any profile, read-only)
- [x] `process.find_by_port` (any profile, read-only)
- [x] Verify NO `process.kill(pid)` tool exists

## 3.8 Server exit hook

- [x] `cli.py` / `__main__.py`: on SIGTERM / Ctrl+C / `localmcptools stop`:
  1. Reject new calls
  2. Wait ≤ `shutdown_grace_seconds` for in-flight to complete
  3. Close Job Object handle (kills children)
  4. Cancel reconciler
  5. Remove `server.json`
  6. Close SQLite
  7. Exit

## 3.9 DoD (must all pass)

- [x] `process.start_dev_server` returns within 1s with id, pid, log_handle
- [x] `process.get_status` returns correct status for running and exited
- [x] `process.stop_managed` graceful terminates within 10s
- [x] Server killed abruptly → all managed processes die within 2s
- [x] On next server start, reconciler marks orphaned rows `exited`
- [x] `process.list_listening_ports` identifies both managed and unmanaged ports
- [x] `find_by_port(8000)` returns the correct row
- [x] No `process.kill` tool exists; calls to it (manually wired)
  return `not_exposed`
- [x] `shell.run_command` with timeout ≥ 60s → `use_start_dev_server`
- [x] Demo end-to-end: start `python -m http.server 8765` →
  `list_listening_ports` shows :8765 → `stop_managed` → port gone

Verification note (2026-08-07): the fixed-port 8000 variant is environment-
conditional because an unrelated local process already owns port 8000. The
same managed/unmanaged mapping is covered by unit tests, and the complete real
start → discover → stop → port-gone lifecycle passes on port 8765.
