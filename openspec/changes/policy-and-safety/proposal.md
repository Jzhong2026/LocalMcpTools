# Change: policy-and-safety

> Covers: `docs/implementation-plan.md` **Phase 2 — Policy, approval, and controlled execution**

## Intent

Make side-effects safe. Until this change, the server is read-only by design.
This change introduces the **authority layer** that makes any future write
tool auditable, approvable, and revocable:

1. Three more profiles: `workspace_exec`, `managed_process`, `interactive_ui`
   (on top of the spike's `observe`).
2. **User approval flow**: a tool call that needs side effects returns
   `approval_required` with an `approval_id`; the user approves in UI/CLI,
   and the approval is bound to workspace + action digest + expiry.
3. **Deny rules** as a defense-in-depth layer over profile + workspace
   scope (the rules never **grant** access — they only deny the most
   catastrophic commands).
4. The first side-effecting tool: `shell.run_command`, behind
   `workspace_exec` + per-call approval.
5. Semantic presets (`workspace.run_test`, `workspace.build`,
   `workspace.lint`, `workspace.git_status`) so agents don't construct shell
   strings themselves.

After this change, an agent can be **told to do real work** without handing
it arbitrary host access.

## Scope

### In scope

- Profile registry: `observe`, `workspace_exec`, `managed_process`, `interactive_ui`
- Profile selection per workspace (default `observe` per workspace)
- Approval table + lifecycle (created → approved → consumed → expired)
- Approval digest = SHA-256 of (tool + canonical args + workspace_id + profile)
- Approval TTL default = 10 minutes; configurable per profile
- Approval consumed on first matching call; subsequent calls re-request
- `shell.run_command` tool behind `workspace_exec`
- `workspace.run_test`, `workspace.build`, `workspace.lint`,
  `workspace.git_status` semantic presets
- Execution core: timeout, encoding, concurrency (`Semaphore(4)`),
  queueing, `tee` to artifact
- Built-in deny rules (10 rules from `implementation-plan.md` §4.3)
- Rule hot-reload via `POST /api/rules/reload` (UI lands in change-5,
  endpoint stub now for testing)

### Out of scope

- `process.start_dev_server` — change-4
- UI to grant approvals — change-5
- `interactive_ui` profile usage — change-6
- OCR / UI automation — change-6

## Approach

1. The **profile** is a workspace-level property, not a per-call property.
   It can be raised only via an **existing approval**, never via agent arg.
2. The **approval** is a server-issued handle. The agent cannot fabricate
   one. The token is short-lived and one-shot.
3. **Deny rules** run after profile + approval checks. Critical rules can
   never be bypassed by `allow_dangerous` (which is now **not** a tool arg
   — it's a profile attribute, and even high-severity non-critical rules
   require explicit approval).
4. **Semantic presets first**: any `workspace_exec` agent should reach for
   `workspace.run_test` instead of `shell.run_command` whenever possible.
   `shell.run_command` is the explicit "I know what I'm doing" escape.
5. **Concurrency cap = 4** with visible queue state, so an agent never
   waits forever for a slot.

## Why this matters later

- change-4 inherits the approval lifecycle for `process.start_dev_server`.
- change-5 wires the approval request into the browser UI.
- change-6 inherits `interactive_ui` profile.
- change-7 ties everything together for packaging.

## Affected components

| Component | Notes |
|---|---|
| `policy/` | new — `profile.py`, `approval.py`, `digest.py` |
| `safety/` | expand — `rules.py` engine + `builtin/*.json` |
| `tools/workspace.py` | add `run_test`, `build`, `lint`, `git_status` |
| `tools/shell.py` | new — `shell.run_command` |
| `execution/runner.py` | new — async process execution |
| `execution/concurrency.py` | new — `Semaphore` + queue telemetry |
| `persistence/db.py` | migration to schema v3 (approvals + rule_hits tables) |
| `control_api.py` | add `POST /api/rules/reload` (UI comes in change-5) |

## Key non-regression

`allow_dangerous` is **no longer a tool argument**. It exists only as a
property of the profile (e.g. `workspace_exec` may have a few rules
overrideable, but `critical` rules never are). Agent-supplied args cannot
elevate permissions.