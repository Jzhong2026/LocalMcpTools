# Change: core-shell-and-audit

> Covers: `docs/implementation-plan.md` **Phase 1 — Workspace diagnostics + artifacts + audit**

## Intent

Solve the single most-reported pain point: **"agent gets no result back from
shell commands."** This change brings up:

1. A real `environment.get` that returns OS, PowerShell version, encoding,
   locale, current user — the context every agent needs and currently has to
   guess at.
2. A workspace registry so that any side-effecting tool can be told **where**
   it's allowed to act.
3. `workspace.inspect` for real: project type, Git status, available presets,
   runtime availability.
4. `fs.read_range` / `fs.tail_log_file` / `fs.grep_files` — agents can read
   evidence without constructing shell pipelines themselves.
5. `output.tail` / `output.read_range` / `output.search` — agents page through
   large outputs through handles, never raw bytes.
6. **Artifact storage**: large outputs go to ACL-protected files; only
   handles and summaries ever touch the agent.
7. Audit field extension: `client_instance`, `workspace_id`, `profile`,
   `policy_version`, `approval_id`, `run_id` all populated.

After this change, an `observe`-profile agent can fully diagnose a project
without writing any file or running arbitrary commands — meeting the
"diagnose before prescribing" goal from `docs/requirements.md` §1.4.

## Scope

### In scope

- `environment.get` tool
- `workspace.inspect` (full, not stub)
- `workspace.search_text` (workspace-scoped read)
- `fs.read_range` / `fs.tail_log_file` / `fs.grep_files` (workspace + artifact scoped)
- `output.tail` / `output.read_range` / `output.search`
- `Workspace` registry with `register / list / resolve`
- Audit extensions: `client_instance`, `workspace_id`, `approval_id`
- Artifact directory at `%APPDATA%\LocalMcpTools\artifacts\`
- ACL on artifacts (Windows ACL or DACL set to current user)
- Redaction pass before any persistence
- Stable `next_actions` field populated for failure modes

### Out of scope

- Any side-effecting tool (`shell.run_command`, file write, install, etc.) —
  these come in change-3 with the policy/approval layer.
- `process.*` — change-4.
- `ui.*` / `ocr.*` — change-6.
- Angular UI for browsing these — change-5.

## Approach

1. Build the **read path** first: every tool added here must answer a question
   without touching the host.
2. Use the spike's envelope unchanged.
3. The `output.*` family uses **handles** — opaque strings like
   `art://2026-08-07/calls/call-uuid.log` — so the agent can request ranges
   without seeing the path.
4. `environment.get` is deliberately small — it answers the question
   "what is this machine?", nothing more.
5. Artifact ACL is enforced via the OS (Windows DACL), not in-app, so a
   compromised Python process can't trivially read prior tool outputs.

## Why this matters later

`core-shell-and-audit` is the read-only contract that **everything else
builds on**:

- change-3 (policy + approval) gates the write path on the workspace registry
  introduced here.
- change-5 (UI) reads the audit + artifact handles introduced here.
- change-6 (UI automation) reuses `output.*` for screenshots.
- change-7 (diagnostics) reuses `workspace.inspect` and `environment.get`.

If this change ships with leaky redaction or path-escape in
`fs.search_text`, every later change inherits the bug.

## Affected components

| Component | Notes |
|---|---|
| `tools/environment.py` | new |
| `tools/workspace.py` | replaces stub |
| `tools/fs.py` | new |
| `tools/output.py` | new |
| `workspaces/` | new top-level module (registry, resolver) |
| `persistence/audit.py` | schema extensions |
| `persistence/artifacts.py` | new (writes ACL-protected files) |
| `safety/redact.py` | new (token / password scrubber) |
| `execution/encoding.py` | new (used by change-3 but landed here for tests) |