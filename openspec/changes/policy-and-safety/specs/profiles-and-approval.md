# Spec: Profiles, approvals, and the policy layer

## ADDED Requirements

### REQ-PROF-1: Profile registry

Four profiles are defined. Each workspace has exactly one active profile
at any time. The default is `observe`.

| Profile | Default capabilities | Forbidden actions |
|---|---|---|
| `observe` | env / project diagnostics, workspace-scoped read, artifact read, port queries | write, arbitrary shell, install, kill, UI input |
| `workspace_exec` | registered workspace's preset test/build/lint + controlled shell | workspace-external paths, privilege escalation, unapproved net-download-execute |
| `managed_process` | start/stop Job Object processes owned by this server | arbitrary PID kill, orphan adoption |
| `interactive_ui` | authorized window UI tree + element find + (separately approved) input | arbitrary desktop, credential windows, system settings |

#### Scenario: default profile is `observe`

- **Given** a fresh workspace registration
- **Then** `workspace.profile = "observe"`

#### Scenario: profile cannot be raised by an arg

- **Given** a call to `shell.run_command` with `profile: "workspace_exec"` in args
- **When** the workspace is `observe`
- **Then** `error.code = "insufficient_capability"` (NOT `approval_required`)
- **And** the supplied `profile` arg is ignored entirely

### REQ-APR-1: Approval lifecycle

An approval is server-issued. It binds:

- `workspace_id`
- `requested_capability` (e.g. `workspace_exec:shell.run_command`)
- `action_digest` = `sha256(tool + canonical(args) + workspace_id + profile)`
- `expires_at` (default 10 minutes from creation)
- `consumed_at` (null until first use; approval is one-shot)

#### Scenario: tool call requires approval

- **Given** workspace profile is `observe`
- **When** the agent calls `workspace.run_test({workspace_id, ...})`
- **Then** the response is `ok: false, error.code = "approval_required"`,
  with `error.approval_id` set to a UUID
- **And** the approval row exists with `status = "pending"`

#### Scenario: agent uses approval to retry

- **Given** an `approval_id` was returned above
- **And** a human has approved it via UI/CLI
- **When** the agent retries with the same args + `approval_id`
- **Then** the call proceeds
- **And** `audit` row records `approval_id` and `consumed_at`

#### Scenario: approval digest mismatch

- **Given** the approval was issued for `workspace.run_test`
- **When** the agent tries to use it for `shell.run_command`
- **Then** `error.code = "approval_digest_mismatch"` and the approval is
  **not** consumed

#### Scenario: approval expiry

- **Given** an approval is older than its `expires_at`
- **When** the agent tries to use it
- **Then** `error.code = "approval_expired"`
- **And** the row is marked `status = "expired"`

### REQ-RULE-1: Built-in deny rules

Ten built-in rules ship in `safety/builtin/*.json`. Rules **deny**; they
never grant. The list (matching `implementation-plan.md` §4.3):

- `block-format-volume` (critical)
- `block-disk-wipe` (critical)
- `block-system-rm` (critical)
- `block-registry-delete` (high)
- `block-boot-loader` (critical)
- `block-firewall-reset` (high)
- `block-privilege-escalation` (high)
- `block-kill-protected` (critical)
- `block-remote-download-exec` (high)
- `block-rdp-enable` (medium)

#### Scenario: critical rule blocks regardless of approval

- **Given** an approval exists for `shell.run_command`
- **When** the agent invokes it with `Format-Volume -DriveLetter C`
- **Then** `error.code = "denied_by_rule"`, `error.blocked_by = "block-format-volume"`,
  `error.severity = "critical"`
- **And** the approval is **not** consumed

#### Scenario: rule hit stats accumulate

- **Given** the rule fires N times during the session
- **Then** `rule_hit_stats` row for that rule has `hit_count = N`,
  `last_hit_at` = now, `last_hit_cmd` = truncated to 200 chars

#### Scenario: rule hot-reload

- **Given** a `*.json` is added or removed in `rules.d/`
- **When** an operator (later: UI button; now: `POST /api/rules/reload`) calls
- **Then** the engine reloads without server restart
- **And** reload errors are returned in the response, not silent

### REQ-EXEC-1: Execution core

`shell.run_command` and `workspace.*` preset tools share an execution core.

#### Scenario: timeout enforcement

- **Given** `default_timeout_ms = 120000`
- **When** the command runs > 120000 ms
- **Then** the process is terminated gracefully (Windows: `taskkill /T`)
- **And** the response is `ok: false, status = "timed_out"` (NOT an exception)
- **And** `meta.output_handle` is still set so the agent can `output.tail`

#### Scenario: encoding is forced for PowerShell

- **Given** the command is a PowerShell call
- **When** the runner spawns the process
- **Then** `-NoProfile`, `-NonInteractive`, `-ExecutionPolicy Bypass` are set
- **And** the command is prefixed with
  `$OutputEncoding = [System.Text.Encoding]::UTF8; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; chcp 65001 > $null;`

#### Scenario: concurrency cap

- **Given** 5 calls arrive simultaneously
- **When** `max_concurrent = 4`
- **Then** 4 start; the 5th is `status = "queued"` and proceeds when a slot frees
- **And** audit row is updated from `queued` to `running` to `success` / `failed`

#### Scenario: queue timeout

- **Given** `queue_timeout_ms = 600000`
- **When** a call has been queued longer than that
- **Then** `error.code = "queue_timeout"` and the call is dropped

#### Scenario: long output becomes an artifact

- **Given** the command produces > 64KB output
- **When** it finishes
- **Then** `meta.output_handle` is set
- **And** the artifact passes through the redactor before persist

## Standard error codes (additions)

| Code | Meaning |
|---|---|
| `insufficient_capability` | Workspace profile lacks the needed capability |
| `approval_required` | Side-effect needs approval; `approval_id` returned |
| `approval_digest_mismatch` | Approval was for a different action |
| `approval_expired` | Approval past `expires_at` |
| `denied_by_rule` | Built-in rule blocked the call |
| `queue_timeout` | Call sat in queue longer than `queue_timeout_ms` |
| `timed_out` | Process exceeded `timeout_ms` |