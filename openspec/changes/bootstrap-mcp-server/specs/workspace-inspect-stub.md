# Spec: `workspace.inspect` (stub)

> This is the **spike** form of the tool. The full version lands in change-2.

## ADDED Requirements

### REQ-WI-1: Tool discovery

The MCP server **MUST** expose exactly one tool — `workspace.inspect` — during
the spike. It must be discoverable by codebuddy and GitHub Copilot through
stdio transport using their respective `mcp.json` configurations.

#### Scenario: codebuddy lists tools

- **Given** codebuddy is configured with a stdio `mcp.json` pointing at
  `python -m localmcptools`
- **When** the user opens a new chat session
- **Then** `workspace.inspect` appears in codebuddy's tool list
- **And** its description mentions workspace inspection

#### Scenario: Copilot lists tools

- **Given** GitHub Copilot is configured identically
- **When** the user opens VS Code's Chat view
- **Then** `workspace.inspect` appears in the tool list

### REQ-WI-2: Tool call round-trip

#### Scenario: agent calls `workspace.inspect` successfully

- **Given** an agent invokes `workspace.inspect` with `{"placeholder": true}`
- **When** the server handles the request
- **Then** the response body **MUST** contain:
  - `ok: true`
  - `data.pid` = integer of the current process
  - `data.build` = `"spike-0"`
  - `meta.tool` = `"workspace.inspect"`
  - `meta.duration_ms` ≥ 0
  - `meta.audit_id` = UUID string
  - `meta.run_id` = UUID string
- **And** the call **MUST** be persisted in `audit.sqlite` row `calls`

#### Scenario: envelope error path

- **Given** an agent sends a malformed payload
- **When** the server tries to handle it
- **Then** the response **MUST** contain `ok: false` and an `error.code`
  from the standard registry
- **And** the error **MUST NOT** leak Python internals

### REQ-WI-3: `observe` profile default

#### Scenario: default profile is `observe`

- **Given** a fresh install with no config changes
- **When** the server starts
- **Then** the active profile **MUST** be `observe`
- **And** the response from any tool **MUST** include `meta.profile = "observe"`

### REQ-WI-4: `approval_required` error envelope

#### Scenario: future write-class tools inherit the envelope

- **Given** the spike
- **When** any tool needs to signal "this needs user approval"
- **Then** the response shape **MUST** be:
  ```jsonc
  {
    "ok": false,
    "error": {
      "code": "approval_required",
      "message": "...",
      "suggestion": "...",
      "approval_id": null   // filled once approval flow lands
    },
    "meta": { "tool": "...", "audit_id": "...", "run_id": "..." }
  }
  ```

## Standard error code registry (initial)

| Code | Meaning |
|---|---|
| `internal_error` | Unhandled exception; do not leak Python internals |
| `invalid_args` | Caller-supplied args failed validation |
| `not_implemented` | Stub intentionally returns this for spike fields |
| `approval_required` | Side-effect needs user approval |

New error codes must be added to this registry, not invented per tool.