# Spec: Angular UI pages

## ADDED Requirements

### REQ-UI-1: Dashboard

#### Scenario: render Dashboard

- **Given** a user opens `http://127.0.0.1:<port>/ui/`
- **Then** the Dashboard renders four widgets:
  1. **Server Status** — pid, port, profile, version, uptime, http on/off
  2. **Managed Processes** — list of running + recently-exited, with stop button
  3. **Listening Ports** — current ports; rows for managed ports are linked
     to their managed process
  4. **Recent Calls** — last 5 audit rows, clickable to detail drawer

### REQ-UI-2: Audit page

#### Scenario: list + filter

- **Given** the Audit page
- **Then**:
  - Filter chips: agent, tool, ok, time range
  - Virtual-scroll table for > 200 rows
  - Row click opens a detail drawer

#### Scenario: detail drawer

- **Then** the drawer shows:
  - All call fields (sanitized)
  - Resolved args (with the redact pass applied to display)
  - On approval_used: link to the approval row
  - "View Log" button → log viewer modal

#### Scenario: log viewer

- **Then**:
  - Lazy-loads `/api/audit/{id}/log`
  - Renders line ranges via virtual scroll
  - "Search inside log" → debounced regex search

### REQ-UI-3: Settings page

- **Sections**:
  - **Shell** — default timeout, max timeout, concurrency, queue timeout, encoding
  - **Audit** — retention days, max size MB
  - **UI** — theme (light/dark/system), auto-open browser
  - **Process** — managed_max_concurrent, shutdown grace, reconcile interval
  - **Security** — approval TTL, http shared mode toggle (restart required)
- Save button on each section
- After save, the response splits fields into `applied` and
  `requires_restart`; the UI shows a toast and (if needed) a banner
  "Restart required for some settings"

### REQ-UI-4: Rules page

#### Scenario: list rules

- **Then** rows show: id, description, severity, enabled, hit count,
  last hit time, last hit command (truncated)

#### Scenario: enable/disable a rule

- **Then** `PATCH /api/rules/{id}` with `{enabled: false}` is sent
- **And** the row updates optimistically; failure reverts

#### Scenario: hot reload

- **Then** "Reload from disk" button calls `/api/rules/reload`
- **And** the response shows `{reloaded, errors}` in a toast

### REQ-UI-5: MCP Config page

#### Scenario: copy snippet

- **Then** a card shows:
  - The actual `mcp.json` snippet (with the real port)
  - One "Copy" button per agent (Code, Insiders)
  - A note about manual paste (server never auto-writes)

### REQ-UI-6: Theme + auto-open browser

#### Scenario: system theme

- **Given** `ui.theme = "system"`
- **Then** the app listens to `prefers-color-scheme` and applies
  Material's light or dark palette accordingly

#### Scenario: auto-open browser on start

- **Given** `ui.auto_open_browser = true` and HTTP mode is on
- **When** the server starts
- **Then** `os.startfile(url)` opens the default browser to
  `http://127.0.0.1:<port>/ui/` (no-op if it crashes)

### REQ-UI-7: graceful failure on server down

#### Scenario: server stops while UI is open

- **Given** the SPA is loaded
- **When** a control plane call returns 5xx or connection refused
- **Then** a global error banner shows "Server is unreachable"
- **And** retry button re-issues the last call
- **And** the SPA does **not** crash or freeze