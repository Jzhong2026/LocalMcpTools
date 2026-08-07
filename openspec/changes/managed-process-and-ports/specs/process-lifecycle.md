# Spec: `process.*` — managed dev servers and port queries

## ADDED Requirements

### REQ-MP-1: `process.start_dev_server`

#### Scenario: agent starts a uvicorn dev server

- **Given** workspace profile is `workspace_exec`
- **And** an approval covers the preset `python-uvicorn`
- **When** the agent calls
  `process.start_dev_server({workspace_id, preset="python-uvicorn", args=["app.main:app"]})`
- **Then** the response is `ok: true` and returns:
  - `data.id` (e.g. `"mp-abc123"`)
  - `data.pid` (integer)
  - `data.command_resolved` (string the server actually runs)
  - `data.log_handle` (artifact handle for streaming logs)
  - `data.port` (int or null — parsed from process spawn output if available)
- **And** the call **MUST** return within 1 second (the runner keeps running asynchronously)

#### Scenario: preset registry

Supported presets at minimum:

| Preset | Project type | Command template |
|---|---|---|
| `python-uvicorn` | python | `python -m uvicorn {args} --reload` |
| `node-vite` | node | `npx vite {args}` |
| `node-next-dev` | node | `npx next dev {args}` |
| `dotnet-run` | dotnet | `dotnet run --project {args[0]}` |

Unknown preset → `error.code = "unknown_preset"`.

#### Scenario: server-start rejection in `observe` profile

- **Given** workspace profile is `observe`
- **Then** the call returns `insufficient_capability`, NOT `approval_required`

### REQ-MP-2: Long-running artifacts

#### Scenario: streaming logs

- **Given** a managed process is running
- **When** `output.tail({handle, n=50})` is called
- **Then** it returns the last 50 lines of the streaming log
- **And** the log is **append-only** until the process exits
- **And** after exit, the artifact is sealed with `line_count = final`

#### Scenario: long-running command via `shell.run_command`

- **Given** `shell.run_command({..., timeout_ms=300000})`
- **Then** the response is `error.code = "use_start_dev_server"` with
  `next_actions` containing the canonical preset name

### REQ-MP-3: `process.get_status` / `process.list_managed`

#### Scenario: get_status

- **Given** a managed process id
- **When** the agent calls `process.get_status({id})`
- **Then** the response is:
  - `data.status` (`running` / `exited`)
  - `data.pid`
  - `data.started_at` (unix ms)
  - `data.duration_ms`
  - `data.exit_code` (only if exited)
  - `data.tail` (last 20 log lines)
  - `data.log_handle`

#### Scenario: list_managed

- **When** the agent calls `process.list_managed({workspace_id?})`
- **Then** all rows from `background_processes` matching the filter are returned
- **And** rows are ordered by `started_at DESC`

### REQ-MP-4: `process.stop_managed`

#### Scenario: stop a managed process

- **Given** a managed process id
- **When** the agent calls `process.stop_managed({id, graceful=true})`
- **Then**:
  - graceful=true: `taskkill /T` (no `/F`); wait up to 5s; if still alive,
    escalate to `taskkill /T /F`
  - graceful=false: `taskkill /T /F` immediately
- **And** on exit, the row's `status='exited'`, `exit_code` set, `finished_at` set
- **And** the log artifact is sealed

#### Scenario: unknown id

- **Then** `error.code = "managed_process_not_found"`

### REQ-MP-5: Job Object ownership

#### Scenario: server crashes, managed processes die

- **Given** a managed process is running under our Job Object
- **When** the server process is killed abruptly (e.g. `taskkill /F` on the server)
- **Then** all managed processes are terminated by Windows within ≤ 2s
- **And** on next server start, the reconciler marks them `status='exited'`,
  `exit_code=null`, `finished_at=now` (orphan reconciled)

#### Scenario: server graceful shutdown

- **Given** SIGTERM/Ctrl+C
- **Then**:
  1. Stop accepting new calls.
  2. Wait ≤ 5s for in-flight calls to complete (return their result).
  3. Close Job Object handle → child processes die.
  4. Persist `server.json` removal.
  5. Exit.

### REQ-MP-6: `process.list_listening_ports` (read-only)

#### Scenario: list all listening sockets

- **Given** any profile (including `observe`)
- **When** the agent calls `process.list_listening_ports({})`
- **Then** the response is `data.ports: [{port, address, protocol, pid?}]`
- **And** `pid` is set only when the port-holder can be identified
- **And** if the port is held by a managed process, the row's
  `managed_id` is also returned

#### Scenario: `find_by_port`

- **Given** `port: 8080`
- **Then** the response is the single matching row or
  `error.code = "port_not_found"`

### REQ-MP-7: deliberately absent — no `process.kill(pid)`

`process.kill(pid)` is **not** exposed in this change (or any later change
without a separate approval chain). Rationale:

- It is the most destructive non-critical capability.
- It is a stepping stone to privilege escalation.
- Users who need it can run `taskkill` in an admin shell themselves.

#### Scenario: kill request is rejected

- **Given** an attempt to call any tool that wraps `process.kill`
- **Then** the call returns `error.code = "not_exposed"`

## Standard error codes (additions)

| Code | Meaning |
|---|---|
| `unknown_preset` | Preset name not in registry |
| `use_start_dev_server` | `shell.run_command` timeout too long; use preset |
| `managed_process_not_found` | Process id not in `background_processes` |
| `port_not_found` | Nothing listening on requested port |
| `not_exposed` | Capability exists in design but is intentionally not exposed |