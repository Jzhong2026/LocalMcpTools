# Spec: HTTP transport and control plane

## ADDED Requirements

### REQ-HTTP-1: HTTP mode is opt-in

#### Scenario: server starts in stdio by default

- **Given** `security.http_shared_mode_enabled = false` (default)
- **When** `localmcptools start` runs
- **Then** the server runs as a stdio MCP server
- **And** `server.json` is **not** written
- **And** no port is allocated

#### Scenario: server starts in HTTP mode

- **Given** `security.http_shared_mode_enabled = true`
- **And** `server.port = 0` (or unset)
- **When** `localmcptools start` runs
- **Then**:
  - Port is allocated (configured or OS-assigned)
  - `server.json` is written with `{pid, port, started_at, csrf_token}`
  - The browser is opened if `ui.auto_open_browser = true`
  - `/mcp` accepts Streamable HTTP MCP requests
  - `/ui/` serves the SPA
  - `/api/*` serves the control plane

### REQ-HTTP-2: Bind to 127.0.0.1 only

#### Scenario: server rejects non-loopback config

- **Given** `server.host` is anything other than `127.0.0.1`
- **Then** the server refuses to start with `error.code = "host_not_loopback"`
  (in `server.log`)
- **And** the rationale is logged: "LocalMcpTools is single-machine only"

### REQ-HTTP-3: Origin/Host allowlist

#### Scenario: browser request from allowed origin

- **Given** HTTP mode is on
- **When** a request has `Origin: http://127.0.0.1:<port>`
- **Then** the request is allowed

#### Scenario: request from disallowed origin

- **Given** a request has `Origin: http://evil.example.com`
- **Then** the response is `403 forbidden`
- **And** the event is written to `server.log` as `origin_denied`

#### Scenario: request with no Origin (server-to-server)

- **When** an agent posts to `/mcp` with no Origin
- **Then** it is **allowed** for the `/mcp` endpoint only
- **And** `/api/*` rejects it as `origin_required` (so the UI is the
  only legitimate origin for control plane calls)

### REQ-HTTP-4: CSRF protection on control plane

#### Scenario: SPA obtains CSRF token

- **Given** the SPA loads at `/ui/`
- **When** it requests `GET /api/csrf-token`
- **Then** the response includes:
  - Set-Cookie `lmcp_csrf=<token>; HttpOnly; SameSite=Strict`
  - Body `{csrf_token: "<token>"}` (also written for header use)

#### Scenario: SPA writes a setting

- **Given** a CSRF token has been issued
- **When** the SPA posts to `/api/settings`
- **And** the request includes header `X-LMCP-CSRF: <token>`
- **And** the cookie is present
- **Then** the request is allowed

#### Scenario: cross-origin CSRF attempt

- **Given** a request from `Origin: http://evil.example.com`
- **When** it posts to `/api/settings`
- **Then** `403 forbidden` — both Origin check and CSRF check fail

### REQ-HTTP-5: Bearer-secret for `/mcp`

#### Scenario: agent presents bearer secret

- **Given** HTTP mode is on
- **And** `server.json` has a `csrf_token` (reused as bearer secret)
- **When** an agent posts to `/mcp` with header
  `Authorization: Bearer <csrf_token>`
- **Then** the request is allowed

#### Scenario: agent omits bearer

- **Then** `401 unauthorized`

## Control plane endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | server pid, port, profile, version |
| GET | `/api/audit?agent=&tool=&ok=&from=&to=&page=&page_size=` | paginated audit list |
| GET | `/api/audit/{id}` | one audit row + sanitized args |
| GET | `/api/audit/{id}/log` | paginated log artifact read |
| GET | `/api/csrf-token` | (HTTP-only) issues CSRF token + cookie |
| POST | `/api/settings` | partial config update |
| GET | `/api/rules` | list rules + enabled state + hit stats |
| PATCH | `/api/rules/{id}` | enable/disable one rule |
| POST | `/api/rules/reload` | hot reload from disk |
| GET | `/api/backgrounds` | list managed processes |
| POST | `/api/backgrounds/{id}/stop` | stop one managed process |
| GET | `/api/mcp-config-snippet` | returns `mcp.json` snippet using current port |
| POST | `/api/shutdown` | graceful stop |

### REQ-API-1: `GET /api/status`

- **Returns**: `{ok: true, data: {pid, port, profile, version,
  uptime_seconds, http_enabled, stdio_enabled: false, ui_url,
  mcp_url}}`

### REQ-API-2: `GET /api/audit`

- **Filters**: `agent`, `tool`, `ok` (0/1), `from`/`to` (unix ms),
  `page` (1-based), `page_size` (default 50, max 200)
- **Returns**: paginated rows ordered `timestamp DESC`
- **Fields per row**: `id, timestamp, agent, tool, profile, workspace_id,
  approval_id, run_id, ok, error_code, blocked_by, severity, exit_code,
  duration_ms, output_handle`

### REQ-API-3: `POST /api/settings`

- Accepts partial `config.json` body
- Hot-reloadable fields apply immediately: `shell.*`, `audit.*`, `ui.*`,
  `process.*`, `security.approval_ttl_seconds`
- Restart-required fields: `server.host`, `server.port`, `mcp.version`,
  any path field
- Response includes `{applied: [...], requires_restart: [...]}`

### REQ-API-4: `POST /api/rules/reload`

- **Returns**: `{reloaded: [...filenames...], errors: [{file, message}], duration_ms}`
- Errors do not abort the reload; partial state is preserved

### REQ-API-5: `GET /api/mcp-config-snippet`

- **Returns**: text body that can be pasted into VS Code `mcp.json`
  (Code and Insiders variants listed separately)
- Snippet uses the **actual allocated port**, not the configured default

## Standard error codes (additions)

| Code | Meaning |
|---|---|
| `host_not_loopback` | Refused to bind non-loopback |
| `origin_denied` | Origin/Host not in allowlist |
| `origin_required` | `/api/*` requires Origin (no server-to-server) |
| `unauthorized` | Missing or wrong bearer secret |
| `forbidden` | CSRF check failed |