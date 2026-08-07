# Spec: `fs.read_range`, `fs.tail_log_file`, `fs.grep_files`, `output.*`

## ADDED Requirements

### REQ-FS-1: `fs.read_range`

#### Scenario: read a line range inside a registered workspace

- **Given** `workspace_id` is registered
- **When** `fs.read_range({workspace_id, path, start_line, end_line})` is called
- **Then** it returns `data.lines: [string]` and `data.total_lines`
- **And** `path` is validated to be inside `canonical_root`
- **And** binary files return `ok: false, error.code = "binary_file"`

### REQ-FS-2: `fs.tail_log_file`

#### Scenario: tail the last N lines

- **Given** `path` is a file inside a registered workspace
- **When** `fs.tail_log_file({workspace_id, path, n=200})` is called
- **Then** the last `n` lines are returned
- **And** the file is **never** fully loaded into the response if > 5MB

### REQ-FS-3: `fs.grep_files`

#### Scenario: workspace-scoped regex grep

- **Given** `pattern` is a valid regex
- **When** `fs.grep_files({workspace_id, pattern, include_glob?, max_results=200})` is called
- **Then** matches are returned with `{file, line, text}`
- **And** default `include_glob` excludes `node_modules/**`, `.git/**`, `dist/**`,
  `bin/**`, `obj/**`, `.venv/**`
- **And** absolute `path` is forbidden — relative paths under workspace only

### REQ-OUT-1: artifact handle contract

All tool outputs larger than **64KB** MUST be persisted as an artifact and
returned by handle. The response body for the call that produced it includes
`meta.output_handle` and `data.summary` (truncated, with `truncated: true` flag
when applicable).

#### Scenario: large output becomes a handle

- **Given** a tool produces 200KB of stdout
- **When** the tool finishes
- **Then** `meta.output_handle` is non-null and shaped like
  `art://2026-08-07/calls/<call_id>.log`
- **And** the agent **cannot** specify the artifact path

### REQ-OUT-2: `output.tail`

#### Scenario: page through an artifact by line range

- **Given** a handle from a previous tool
- **When** `output.tail({handle, n=200})` is called
- **Then** the last `n` lines are returned
- **And** `meta.evidence_handle` is the same handle (idempotent)

### REQ-OUT-3: `output.read_range`

#### Scenario: read a line range from an artifact

- **Given** a handle
- **When** `output.read_range({handle, start_line, end_line})` is called
- **Then** the lines are returned
- **And** if `handle` does not exist → `error.code = "artifact_not_found"`

### REQ-OUT-4: `output.search`

#### Scenario: regex search inside an artifact

- **Given** a handle
- **When** `output.search({handle, pattern, max_results=200})` is called
- **Then** matches are returned as `{line, text}` triples
- **And** matches never include paths outside the artifact

## Redaction (REQ-REDACT-1)

Every line of any artifact **MUST** be passed through `safety.redact` before
persistence. The redactor **MUST** at minimum replace:

- `Bearer [A-Za-z0-9._\-]+` → `Bearer ***`
- `(api[_-]?key|token|secret|password)\s*[:=]\s*\S+` → `$1: ***`
- JWT-shaped `xxx.yyy.zzz` strings → `***.***.***`
- Common key=value patterns in `.env`, `~/.aws/credentials` style content

#### Scenario: artifact with a Bearer token

- **Given** stdout contains `Authorization: Bearer abc.def.ghi`
- **When** the artifact is persisted
- **Then** the on-disk line is `Authorization: Bearer ***`
- **And** the agent response never includes the original token