# Tasks: core-shell-and-audit

> Phase: **1** — Workspace diagnostics + artifacts + audit
> Goal: `observe`-profile agent can fully diagnose a project without side effects.

## 1.1 Path safety & workspace registry

- [ ] `workspaces/registry.py`: `register(path, profile='observe')` returns `Workspace`
- [ ] `workspaces/registry.py`: `resolve(workspace_id)` raises `WorkspaceNotRegistered`
- [ ] `workspaces/registry.py`: `canonicalize(path)` uses `os.path.realpath`
- [ ] `workspaces/registry.py`: rejects paths that are not directories
- [ ] `workspaces/registry.py`: rejects `..` traversal before `realpath` (early reject)
- [ ] Add `workspaces` SQLite table + migration; bump `schema_version` to 2
- [ ] Unit test: path-escape attempts (`..\..\Windows`, `C:\Windows\..\..\foo`) rejected
- [ ] Unit test: two different inputs that canonicalize to the same path share one row

## 1.2 Redaction

- [ ] `safety/redact.py`: ordered regex list per design.md
- [ ] `safety/redact.py`: pure function, returns `(redacted_text, redacted_count)`
- [ ] Unit test: Bearer, api_key=, JWT, .env lines, mixed content
- [ ] Unit test: `password=secret123` → `password=***`; `name=John` unchanged

## 1.3 Artifact storage

- [ ] `persistence/artifacts.py`: `write(call_id, content, sensitive=False)` returns handle
- [ ] `persistence/artifacts.py`: write path is `artifacts/YYYY-MM-DD/calls/<call_id>.log`
- [ ] `persistence/artifacts.py`: set Windows DACL to current user only (`icacls`)
- [ ] `persistence/artifacts.py`: ACL failure → raise `RedactionFailed` (caller aborts persist)
- [ ] `persistence/artifacts.py`: 64KB threshold; smaller content can stay inline
- [ ] `persistence/artifacts.py`: `lookup(handle)` returns metadata or raises `ArtifactNotFound`
- [ ] `persistence/artifacts.py`: `read_range(handle, start, end)` / `tail(handle, n)` / `search(handle, pattern)`
- [ ] Add `artifacts` SQLite table; bump `schema_version` to 2
- [ ] Unit test: ACL applied (skip on non-Windows CI; mark xfail)
- [ ] Unit test: handle shape is `art://YYYY-MM-DD/calls/<uuid>.log`

## 1.4 Audit extensions

- [ ] `persistence/audit.py`: `record_start` accepts `client_instance`, `workspace_id`
- [ ] `persistence/audit.py`: `record_finish` accepts `approval_id`
- [ ] `persistence/audit.py`: writes `log_path` as artifact handle on success
- [ ] Unit test: round-trip with all new fields

## 1.5 `environment.get`

- [ ] `tools/environment.py`: collect from `platform`, `sys`, `os`, PowerShell probe
- [ ] `tools/environment.py`: encoding detection uses `chardet` on a PowerShell echo probe
- [ ] `tools/environment.py`: probe `whoami /groups` for `is_admin` (cached 60s)
- [ ] `tools/environment.py`: returns `next_actions` on partial failure
- [ ] Unit test: schema matches `REQ-ENV-1`
- [ ] Integration test: Chinese Windows host → `encoding.console_output = "utf-8"` after probe

## 1.6 `workspace.inspect`

- [ ] `workspaces/inspect.py`: detect project_type via marker files
  (`package.json`, `pyproject.toml`, `*.csproj`)
- [ ] `workspaces/inspect.py`: git status via `git status --porcelain` (no shell; use `subprocess`)
- [ ] `workspaces/inspect.py`: presets_available based on detected scripts
- [ ] `workspaces/inspect.py`: runtimes via `shutil.which` + version flags
- [ ] `workspaces/inspect.py`: missing_runtimes = expected ∩ not found
- [ ] Unit test: project_type matrix
- [ ] Integration test: register a fixture workspace; assert full payload

## 1.7 `workspace.search_text` & `fs.*`

- [ ] `tools/workspace.py`: `search_text(workspace_id, pattern, max_results=50)`
- [ ] `tools/fs.py`: `read_range(workspace_id, path, start_line, end_line)`
- [ ] `tools/fs.py`: `tail_log_file(workspace_id, path, n=200)`
- [ ] `tools/fs.py`: `grep_files(workspace_id, pattern, include_glob=None, max_results=200)`
- [ ] Every entry point: canonicalize path; reject if not under `workspace.canonical_root`
- [ ] Binary file detection: check first 8KB for null bytes; return `binary_file` error
- [ ] Default exclude globs in `grep_files`: `node_modules/**`, `.git/**`, `dist/**`, `bin/**`, `obj/**`, `.venv/**`
- [ ] Unit test: path-escape → `invalid_path`
- [ ] Unit test: binary file → `binary_file`
- [ ] Integration test: large fixture file → `tail_log_file` returns last N lines, never loads all

## 1.8 `output.*`

- [ ] `tools/output.py`: `tail(handle, n=200)`
- [ ] `tools/output.py`: `read_range(handle, start_line, end_line)`
- [ ] `tools/output.py`: `search(handle, pattern, max_results=200)`
- [ ] Every entry: artifact ACL re-check before read
- [ ] Unit test: unknown handle → `artifact_not_found`
- [ ] Unit test: search never returns paths outside the artifact

## 1.9 Envelope wiring

- [ ] Every new tool populates `meta.profile`, `meta.run_id`, `meta.audit_id`
- [ ] Tools with a workspace: also populate `meta.workspace_id` (add to envelope)
- [ ] Tools producing >64KB output: set `meta.output_handle` and `data.summary.truncated = true` if truncated
- [ ] Tools failing partway: `meta.next_actions` is non-empty

## 1.10 DoD (must all pass)

- [ ] An `observe` agent can call `environment.get`, `workspace.register`, `workspace.inspect`, `workspace.search_text`, `fs.read_range`, `fs.tail_log_file`, `fs.grep_files` — all without any side effect on the host
- [ ] Every call lands in `audit.sqlite` with the new fields populated
- [ ] Any output > 64KB becomes an artifact with handle and ACL set
- [ ] Bearer tokens in outputs are redacted before any persist
- [ ] Path-escape attempts return `invalid_path`, never reach the OS
- [ ] Binary file attempts return `binary_file`, never crash
- [ ] `next_actions` is non-empty whenever `ok: false`
- [ ] No shell command is run by any tool in this change
- [ ] No HTTP listener started
- [ ] No write tool exposed (still `observe`-only)