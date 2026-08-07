# Spec: `shell.run_command` and `workspace.*` presets

## ADDED Requirements

### REQ-SHELL-1: `shell.run_command`

#### Scenario: callable by `workspace_exec` profile

- **Given** workspace profile is `workspace_exec`
- **And** an approval covers this command
- **When** the agent calls `shell.run_command({workspace_id, cmd, timeout_ms?, env?})`
- **Then** the runner executes the command inside `workspace.canonical_root`
- **And** `cwd` is forced to `workspace.canonical_root` regardless of agent arg
- **And** extra `env` keys are filtered against a per-workspace allowlist
- **And** stdout + stderr are interleaved into one artifact (line-prefixed)

#### Scenario: caller is `observe` profile

- **Given** workspace profile is `observe`
- **Then** the call returns `insufficient_capability` — not approval_required

#### Scenario: agent tries to set `cwd`

- **Given** the call includes `cwd: "C:\\Windows"`
- **Then** the runner **ignores** it and uses `workspace.canonical_root`
- **And** the audit log records the attempted `cwd` in `args_redacted` for
  forensics, but the value is **not** honored

### REQ-WS-PRE-1: `workspace.run_test`

#### Scenario: discovers test command from project type

- **Given** workspace project_type is `node`
- **When** the agent calls `workspace.run_test({workspace_id, filter?, timeout_ms?})`
- **Then** the runner executes `npm test -- <filter>` (or `npm test` if no filter)
- **And** the response includes `data.exit_code`, `meta.output_handle`,
  `meta.next_actions` (e.g. "open file:line for the first failed test")

#### Scenario: project_type is `python`

- **Then** `pytest -q <filter>` is the default; user-configurable override

#### Scenario: no test runner detected

- **Then** `error.code = "no_preset"` and `next_actions` includes
  "configure preset or fall back to shell.run_command with approval"

### REQ-WS-PRE-2: `workspace.build` / `workspace.lint` / `workspace.git_status`

Same shape as `run_test`:

- `workspace.build` — `npm run build` / `python -m build` / `dotnet build`
- `workspace.lint` — `npm run lint` / `ruff check .` / `dotnet format --verify-no-changes`
- `workspace.git_status` — read-only; no approval needed in `observe`
  (and likewise callable in any profile)

### REQ-WS-PRE-3: Pre-flight "plan before execute"

For every preset, the response on success **MUST** include `data.preset`
and `data.command_resolved` so the agent (and UI) can show "we will run
X" **before** the run starts. If `command_resolved` looks unsafe, the
human can cancel before approving.

#### Scenario: human reviews preset before approving

- **Given** the agent requested `workspace.run_test`
- **When** approval is pending
- **Then** the approval payload in UI **MUST** show the resolved command
  and the working directory it will run in

## Standard error codes (additions)

| Code | Meaning |
|---|---|
| `no_preset` | No preset matched the workspace's project type |
| `cwd_forced` | Caller tried to set `cwd`; server used workspace root (informational) |