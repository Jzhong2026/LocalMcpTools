# Spec: Packaging, install, and cross-agent verification

## ADDED Requirements

### REQ-PKG-1: `localmcptools install`

#### Scenario: install user-level scheduled task

- **Given** PowerShell ≥ 5.1 and Windows 10/11
- **When** the user runs `localmcptools install`
- **Then**:
  - A user-level scheduled task named `LocalMcpTools` is created
  - Trigger: `AtLogOn` for the current user
  - Action: `python -m localmcptools start` with the project's
    working directory
  - Settings: `AllowStartIfOnBatteries`, `DontStopIfGoingOnBatteries`,
    `RestartCount=3`, `RestartInterval=1 minute`
  - The script writes the task XML and registers it via
    `Register-ScheduledTask`
- **And** the script never requires elevation (user-level task)
- **And** the script exits 0 on success

#### Scenario: install is idempotent

- **Given** the task already exists
- **When** `localmcptools install` runs again
- **Then** the existing task is updated, not duplicated

#### Scenario: install fails cleanly

- **Given** PowerShell execution policy blocks the script
- **Then** the script writes a clear error to stdout (not stderr-only)
  and exits non-zero

### REQ-PKG-2: `localmcptools uninstall`

- Removes the scheduled task
- Idempotent: no-op if not installed
- Never removes `%APPDATA%\LocalMcpTools\` (data is the user's)

### REQ-PKG-3: `localmcptools install` fallback

#### Scenario: user prefers Startup folder

- **Given** the scheduled task creation fails
- **When** `localmcptools install --method startup-folder` is used
- **Then** a `.lnk` is placed at
  `shell:startup\<username>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup`
  pointing at the same `python -m localmcptools start`
- **And** the `.lnk` retries on launch (no infinite loop detection — the
  server itself short-circuits if `server.json` is healthy)

### REQ-PKG-4: README + agent docs

#### Scenario: README sections

- **Then** `README.md` covers:
  - One-paragraph project description
  - "Why this exists" (links to `docs/requirements.md`)
  - Install: `pip install -e .`
  - Run: `localmcptools start` (stdio) / `localmcptools start --http`
  - Install autostart: `localmcptools install`
  - UI: `localmcptools ui`
  - "Configuring your agent" — link to `docs/agent-configuration.md`

#### Scenario: agent-configuration.md

- **Then** the doc covers codebuddy, GitHub Copilot, workbuddy, minimax code
  with a per-agent:
  - File location of `mcp.json`
  - Example snippet (using the current server's port)
  - Notes on quirks per agent (e.g. workbuddy's preferred transport)

### REQ-VER-1: codebuddy works

#### Scenario: codebuddy lists tools

- **Given** codebuddy is configured per `docs/agent-configuration.md`
- **When** a fresh chat session starts
- **Then** the LocalMcpTools tool list is visible
- **And** `workspace.inspect` is callable

#### Scenario: codebuddy runs a preset

- **When** the agent calls `workspace.run_test` against a registered workspace
- **Then** the call returns `approval_required`
- **And** after the user approves (CLI or UI), the call succeeds

### REQ-VER-2: GitHub Copilot works

#### Scenario: Copilot lists tools

- **Given** VS Code is configured per `docs/agent-configuration.md`
- **Then** Copilot Chat shows the LocalMcpTools tools

#### Scenario: Copilot runs an observe tool

- **When** the user asks "what version of python is installed?"
- **Then** Copilot calls `runtime.detect_runtime` and returns the version

### REQ-VER-3: concurrent agents share one server

#### Scenario: codebuddy + Copilot both call audit

- **Given** both agents are configured to use the same HTTP server
- **When** they each make calls
- **Then** audit rows are interleaved correctly with the right
  `agent` / `client_instance` fields
- **And** no row is dropped or duplicated

### REQ-VER-4: future-agent config is documented but not verified

#### Scenario: workbuddy / minimax code stubs

- **Then** `docs/agent-configuration.md` includes per-future-agent
  instructions based on the agent's public docs at the time of writing
- **And** it is marked "untested" so the user knows verification is owed

## Standard error codes (additions)

| Code | Meaning |
|---|---|
| `task_create_failed` | `Register-ScheduledTask` returned non-zero |
| `already_installed` | Idempotent re-install; treated as success, not error |