# Tasks: managed-process-and-ports

> Phase: **3** — Managed dev servers + port queries
> Goal: agent can start, observe, and stop a long-running process that dies
> with the server.

## 3.1 Preset registry

- [ ] `process/presets.py`: registry `dict[str, Preset]` with at minimum
  `python-uvicorn`, `node-vite`, `node-next-dev`, `dotnet-run`
- [ ] Each `Preset` declares `command_template`, `default_args`, `env_required`,
  `port_hint_regex` (for detecting bound port from startup output)
- [ ] `resolve(workspace_id, preset_name, args)` returns the resolved argv
- [ ] Unit test: each preset resolves correctly
- [ ] Unit test: unknown preset → `unknown_preset`

## 3.2 Job Object binding

- [ ] `execution/background.py`: `_make_job()` creates Job Object with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
- [ ] `execution/background.py`: `_attach(pid, handle)` uses
  `win32job.AssignProcessToJobObject`
- [ ] Add `pywin32` to `requirements.txt`
- [ ] Unit test (xfail on non-Windows): child PID is terminated when handle is closed

## 3.3 Background runner

- [ ] `execution/background.py`: `start_dev_server(workspace_id, preset, args)`
- [ ] `execution/background.py`: spawn subprocess, attach to job, kick off
  log-pump and await-exit tasks
- [ ] `execution/background.py`: log pump appends to artifact line-by-line
  (buffered, max 10ms latency to disk)
- [ ] `execution/background.py`: await-exit updates `background_processes.status`
  and seals the artifact
- [ ] Unit test: returns within 1s (the runner is async)
- [ ] Integration test: `python -m http.server` started as
  `preset=python-uvicorn` → port 8000 appears in `list_listening_ports`
  within 5s

## 3.4 Reject long-running shell

- [ ] `execution/runner.py`: if `timeout_ms >= 60_000`, raise `UseStartDevServer`
  with `next_actions` listing the relevant preset
- [ ] Unit test: 60s shell call rejected with the right error code

## 3.5 Manager (DB + lifecycle)

- [ ] `process/manager.py`: `create_row`, `update`, `find_by_id`,
  `find_by_pid`, `all_running`, `list_managed`
- [ ] `process/manager.py`: stop graceful = `taskkill /T` → wait 5s →
  escalate to `taskkill /T /F`
- [ ] Unit test: stop sequence recorded correctly
- [ ] Unit test: unknown id → `managed_process_not_found`

## 3.6 Reconciler

- [ ] `execution/background.py`: `reconcile_loop()` runs every 30s
- [ ] Reconcile: any row with `status='running'` whose `pid` is no longer
  alive → mark `status='exited'`, `exit_code=null`, seal artifact
- [ ] Cancel previous loop task on server shutdown
- [ ] Unit test (xfail non-Windows): fake-kill a child → reconcile marks it exited

## 3.7 `tools/process.py`

- [ ] `process.start_dev_server` (managed_process profile, approval required)
- [ ] `process.get_status` (any profile, by id)
- [ ] `process.list_managed` (any profile, optional workspace filter)
- [ ] `process.stop_managed` (managed_process profile, approval required)
- [ ] `process.list_listening_ports` (any profile, read-only)
- [ ] `process.find_by_port` (any profile, read-only)
- [ ] Verify NO `process.kill(pid)` tool exists

## 3.8 Server exit hook

- [ ] `cli.py` / `__main__.py`: on SIGTERM / Ctrl+C / `localmcptools stop`:
  1. Reject new calls
  2. Wait ≤ `shutdown_grace_seconds` for in-flight to complete
  3. Close Job Object handle (kills children)
  4. Cancel reconciler
  5. Remove `server.json`
  6. Close SQLite
  7. Exit

## 3.9 DoD (must all pass)

- [ ] `process.start_dev_server` returns within 1s with id, pid, log_handle
- [ ] `process.get_status` returns correct status for running and exited
- [ ] `process.stop_managed` graceful terminates within 10s
- [ ] Server killed abruptly → all managed processes die within 2s
- [ ] On next server start, reconciler marks orphaned rows `exited`
- [ ] `process.list_listening_ports` identifies both managed and unmanaged ports
- [ ] `find_by_port(8000)` returns the correct row
- [ ] No `process.kill` tool exists; calls to it (manually wired)
  return `not_exposed`
- [ ] `shell.run_command` with timeout ≥ 60s → `use_start_dev_server`
- [ ] Demo end-to-end: start `python -m http.server 8765` →
  `list_listening_ports` shows :8765 → `stop_managed` → port gone