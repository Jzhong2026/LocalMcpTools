# Spec: `runtime.*`, `vscode.*`, `diagnostics.*`

## ADDED Requirements

### REQ-DIAG-1: `runtime.detect_runtime`

#### Scenario: detect python / node / dotnet

- **When** the agent calls `runtime.detect_runtime({})`
- **Then** the response is:
  ```jsonc
  {
    "python":  [{ "version": "3.11.9", "path": "C:\\Python311\\python.exe", "is_default": true }],
    "node":    [{ "version": "20.10.0", "path": "...", "is_default": true }],
    "dotnet":  [{ "version": "8.0.100", "path": "...", "is_default": true }],
    "npm":     [{ "version": "10.2.3",  "path": "...", "is_default": true }],
    "missing": []    // names the workspace expects but can't find
  }
  ```
- **And** `missing` is computed against the workspace's project type
  (from `workspace.inspect`)

### REQ-DIAG-2: `runtime.get_env`

#### Scenario: get a single env var

- **When** `runtime.get_env({name: "PATH"})`
- **Then** returns `{name, value, source: "process"|"user"|"system"}`
- **And** secrets are redacted per `safety.redact`

#### Scenario: name not set

- **Then** `{value: null}`

### REQ-DIAG-3: `runtime.list_path`

#### Scenario: list PATH entries

- **When** `runtime.list_path({})`
- **Then** returns `data.entries: [{dir, exists, is_file, executable?}]`
- **And** `executable` is set only when `dir` contains a runtime
  matching the project type

### REQ-VSC-1: `vscode.get_problems`

#### Scenario: list current Problems

- **Given** VS Code is running with the LocalMcpTools project open
- **When** `vscode.get_problems({workspace_path?, severity?})`
- **Then** the response is the structured Problems list
  `[{file, line, column, severity, source, code, message}]`
- **And** capped at 1000 entries; total count returned separately

#### Scenario: VS Code not running

- **Then** `error.code = "vscode_not_running"` with `next_actions` listing
  fallback tools (`fs.grep_files` for the same workspace)

### REQ-VSC-2: `vscode.get_installed_extensions`

#### Scenario: list extensions

- **Then** `data.extensions: [{id, name, version, is_active}]`

### REQ-VSC-3: `vscode.get_logs`

#### Scenario: tail the Output channel

- **Given** `channel = "Window" | "Extension Host" | "Git" | <custom>`
- **Then** the response is a streaming log handle + last 200 lines
- **And** the underlying log file is **read-only**; never written by us

### REQ-VSC-4: `vscode.get_debug_sessions`

#### Scenario: list active debug sessions

- **Then** `data.sessions: [{id, name, type, configuration, state}]`

### REQ-DIAG-4: `diagnostics.collect`

#### Scenario: aggregate everything

- **When** the agent calls `diagnostics.collect({workspace_id?, depth: "summary"|"full"})`
- **Then** the response includes:
  - `runtime` (from `runtime.detect_runtime`)
  - `git` (status, head, branch)
  - `problems` (from `vscode.get_problems`)
  - `ports` (managed + listening)
  - `recent_failures` (last 5 failed audit rows in this workspace)
  - `next_actions` (synthesized from the above)
- **And** `depth="summary"` keeps it under 64KB inline; `"full"` returns
  handles for large sections

#### Scenario: VS Code not running

- **Then** `problems` is empty with `data.notes: "vscode offline"`
- **And** the other sections still work

### REQ-DIAG-5: `diagnostics.explain_failure`

#### Scenario: explain a run

- **Given** a `run_id` from a prior audit row
- **When** `diagnostics.explain_failure({run_id})`
- **Then** the response is:
  ```jsonc
  {
    "classification": "timeout" | "exit_code" | "denied_by_rule" |
                      "approval_required" | "verification_failed" | "unknown",
    "summary": "<1-line human description>",
    "key_evidence": [{ "line": 42, "text": "...redacted..." }],
    "next_actions": ["...", "..."],
    "related_runs": [/* ids of similar recent failures */]
  }
  ```
- **And** if the run is `denied_by_rule`, classification includes the
  rule id

#### Scenario: log not found

- **Then** `classification = "unknown"`, `next_actions: ["provide log_handle manually"]`

## Standard error codes (additions)

| Code | Meaning |
|---|---|
| `vscode_not_running` | VS Code instance unreachable |
| `runtime_not_found` | `runtime.detect_runtime` could not find expected runtime |