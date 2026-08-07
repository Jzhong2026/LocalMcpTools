# Spec: `environment.get`, `workspace.inspect`, `workspace.search_text`

## ADDED Requirements

### REQ-ENV-1: `environment.get` returns machine context

The tool returns a structured object describing the host. Every field is
required to be present (use `null` if unavailable, never omit).

#### Scenario: returns Windows host info

- **Given** a Windows host with PowerShell 5.1 installed
- **When** an `observe`-profile agent calls `environment.get`
- **Then** the response `data` **MUST** include:
  - `os.name` (e.g. "Windows")
  - `os.version` (e.g. "10.0.22631")
  - `os.build` (string)
  - `os.architecture` ("x64" / "arm64")
  - `powershell.version` (e.g. "5.1.22621")
  - `powershell.edition` ("Desktop" / "Core")
  - `powershell.executable` (absolute path)
  - `encoding.active_code_page` (integer)
  - `encoding.console_output` (string, e.g. "utf-8" or "gbk")
  - `encoding.preferred_fs` ("utf-8" / "gbk" / detected)
  - `user.name`, `user.is_admin`
  - `cwd` (absolute path)
  - `machine.name`

#### Scenario: `next_actions` is present on failure

- **Given** any failure collecting environment
- **Then** `meta.next_actions` **MUST** be a non-empty list of strings the
  agent can act on (e.g. "ask user to confirm `chcp` value")

### REQ-WS-1: Workspace registry

#### Scenario: register a workspace

- **Given** a registered `workspace.inspect` call against an absolute path
- **When** the path exists and is a directory
- **Then** a `workspace_id` is generated and stored in
  `workspaces` table with `{id, canonical_root, registered_at, profile}`
- **And** the response returns `{workspace_id, canonical_root, profile}` (default `observe`)

#### Scenario: path escapes are rejected

- **Given** an attempt to register `..\..\Windows` or an unresolvable path
- **Then** the call returns `ok: false`, `error.code = "invalid_path"`
- **And** no row is written

### REQ-WS-2: `workspace.inspect`

#### Scenario: inspects a registered workspace

- **Given** a `workspace_id` from the registry
- **When** `workspace.inspect({workspace_id})` is called
- **Then** the response **MUST** include:
  - `project_type` (one of: `node`, `python`, `dotnet`, `mixed`, `unknown`)
  - `git.status` (`clean` / `dirty` / `not_a_repo`)
  - `git.head` (short SHA or `null`)
  - `git.branch` (or `null`)
  - `presets_available` (subset of `[test, build, lint, dev_server]`)
  - `runtimes` (array of `{name, version, path}` for `python` / `node` / `dotnet` / `npm`)
  - `missing_runtimes` (array of names expected but not found)

### REQ-WS-3: `workspace.search_text` is workspace-scoped

#### Scenario: search inside the workspace

- **Given** a registered `workspace_id`
- **When** the agent calls `workspace.search_text({workspace_id, pattern, max_results=50})`
- **Then** matches outside `canonical_root` are **silently excluded**
- **And** results are returned as `{file, line, text}` triples
- **And** binary files are skipped, not crashed on

#### Scenario: workspace path-escape attempt

- **Given** `pattern` contains `../../Windows`
- **Then** matches outside the workspace are excluded
- **And** `meta.next_actions` includes `"narrow pattern to workspace"`

## Standard error codes (additions)

| Code | Meaning |
|---|---|
| `invalid_path` | Path cannot be resolved or escapes registered workspace |
| `workspace_not_registered` | Caller referenced a `workspace_id` that does not exist |
| `artifact_not_found` | `output.*` handle does not resolve |
| `redaction_failed` | Could not safely redact; default to deny persist |