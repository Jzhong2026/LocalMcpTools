# Tasks: policy-and-safety

> Phase: **2** — Policy, approval, controlled execution
> Goal: a workspace_exec agent can run real work via approvals; critical
> commands are denied by rules regardless of approval.

## 2.1 Profile registry

- [x] `policy/profile.py`: enum of 4 profiles + `current(workspace_id)` reader
- [x] `policy/profile.py`: default `observe` per workspace
- [x] `policy/profile.py`: raises on attempt to change profile from inside a tool
- [x] Unit test: each profile's allowed/forbidden matrix (lands with `authorize.py`)
- [x] Unit test: agent-supplied `profile` arg is ignored

## 2.2 Approval data model

- [x] `policy/digest.py`: deterministic `sha256(tool|ws|profile|canonical_args)`
- [x] `policy/approval.py`: `request(workspace_id, capability, args)` → row + id
- [x] `policy/approval.py`: `consume(approval_id, presented_digest)` → bool
- [x] `policy/approval.py`: `expire_due()` sweeper called every 60s
- [x] Add `approvals` SQLite table; bump `schema_version` to 3
- [x] Unit test: digest stable across key order, whitespace, default repr
- [x] Unit test: digest mismatch → no consumption
- [x] Unit test: TTL expiry → status flips to `expired`

## 2.3 Authorize layer

- [x] `policy/authorize.py`: `check(profile, capability)` returns
  `Allow | Deny | NeedApproval`
- [x] `policy/authorize.py`: capability string is `"{profile}:{tool}"` or `"{profile}:{namespace}.{tool}"`
- [x] Wire into `_common.py`: every tool call passes through `authorize.check`
- [x] Unit test: observe calling shell → Deny
- [x] Unit test: workspace_exec calling workspace.run_test → NeedApproval

## 2.4 Built-in deny rules

- [x] Write 10 `safety/builtin/*.json` matching design.md
- [x] `safety/rules.py`: `load_all()` reads `builtin/` + `rules.d/custom/`
- [x] `safety/rules.py`: `match(cmd, args)` returns first hit or None
- [x] `safety/rules.py`: `record_hit(rule_id, cmd)` updates `rule_hit_stats`
- [x] `safety/rules.py`: `reload()` is async-safe and reports per-file errors
- [x] Unit test: each rule has ≥1 positive case + ≥1 negative case
- [x] Unit test: critical rule + approval → still `denied_by_rule`
- [x] Unit test: hot reload adds new rule and matches without restart

## 2.5 Execution core

- [x] `execution/runner.py`: async subprocess + timeout + tee
- [x] `execution/runner.py`: `_terminate_tree` via `taskkill /T /F` on Windows
- [x] `execution/encoding.py`: chardet + GBK fallback decode
- [x] `execution/powershell.py`: PS arg builder per REQ-EXEC-1
- [x] `execution/concurrency.py`: `ConcurrencyGate` per design.md
- [x] Unit test: timeout fires → `TimedOut` raised
- [x] Unit test: queue timeout fires when 5 calls hit cap=4
- [x] Unit test: encoding.decode decodes UTF-8, GBK, and bogus input safely

## 2.6 `shell.run_command`

- [x] `tools/shell.py`: requires `workspace_exec`
- [x] `tools/shell.py`: forces `cwd = workspace.canonical_root`
- [x] `tools/shell.py`: filters `env` keys against per-workspace allowlist
  (default: no override)
- [x] `tools/shell.py`: on completion, pipes stdout+stderr through redactor
  before artifact persist
- [ ] Integration test: PowerShell `Write-Host "中文"` returns correct UTF-8 *(deferred — Chinese-Windows-only)*
- [x] Integration test: long output → `meta.output_handle` set

## 2.7 `workspace.*` presets

- [x] `workspaces/presets.py`: registry of (project_type, preset) → command template
- [x] `tools/workspace.py`: `run_test`, `build`, `lint`, `git_status`
- [x] All preset tools return `data.preset`, `data.command_resolved`,
  `meta.workspace_id`
- [x] `git_status` does **not** require approval (read-only)
- [x] `run_test` / `build` / `lint` require approval (write side effects)
- [x] Integration test: node fixture → `run_test` resolves to `npm test`
- [x] Integration test: no preset → `no_preset` error with `next_actions`

## 2.8 Rule reload endpoint (UI comes later)

- [ ] `control_api.py`: `POST /api/rules/reload` returns
  `{reloaded: n, errors: [{file, message}]}`
- [ ] Smoke test: add a temp rule, hit endpoint, see it active

## 2.9 DoD (must all pass)

- [x] `observe` workspace: any side-effect tool returns `insufficient_capability`
- [x] `workspace_exec` workspace without approval: returns `approval_required`
  with `approval_id`
- [x] After approval, the same call succeeds and the row is `consumed`
- [x] Replaying the approval under different args → `approval_digest_mismatch`
- [x] After 10 minutes, the approval is `expired` and unusable
- [x] All 10 built-in rules block, including critical ones, with approval
- [x] `shell.run_command` `cwd` arg is ignored; workspace root is used
- [x] `shell.run_command` extra env keys are filtered
- [ ] Encoding-correct PowerShell output (Chinese) end-to-end *(deferred — Chinese-Windows-only)*
- [x] Timeout returns `timed_out`, not an exception
- [x] 5th concurrent call is `queued`, then `running`, then completes
- [x] Long output (≥64KB) becomes an artifact with handle + ACL
- [x] Hot reload picks up new rule without restart
- [x] No HTTP listener started (stdio only in this change)
- [x] Audit row for every call, with `approval_id` populated when applicable
