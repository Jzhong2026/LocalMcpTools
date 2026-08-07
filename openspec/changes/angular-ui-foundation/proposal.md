# Change: angular-ui-foundation

> Covers: `docs/implementation-plan.md` **Phase 4 — Angular UI foundation**

## Intent

Up to now everything has been agent-facing (stdio MCP). Humans have been
in the dark except through `audit.sqlite` (which they have to query by
hand). This change introduces the **local browser UI**:

1. Start the server with HTTP enabled (the only mode that exposes `/ui/`).
2. A FastAPI control plane carries the endpoints humans and the SPA need.
3. The Angular 19 SPA renders Dashboard / Audit / Settings / Rules pages.
4. The Streamable HTTP MCP endpoint is mounted at `/mcp` so any agent that
   wants to use the same shared server can do so.

After this change, a human can:

- See what the server is doing (dashboard)
- Audit any call (audit list + detail)
- Change settings live (most changes hot-reloadable)
- Manage rules (view / enable / disable / reload)
- See the MCP config snippet to copy into VS Code `mcp.json`

## Scope

### In scope

- Optional Streamable HTTP transport alongside stdio
- `server.json` lifecycle (port allocation, pid, start time)
- Bearer-secret token file for cross-process CSRF protection
- FastAPI app with: `/mcp` (Streamable HTTP), `/api/*` (control plane),
  `/ui/` (Angular SPA)
- Control plane endpoints: status, audit list/detail, settings read/update,
  rules list/enable/disable/reload, backgrounds list/stop,
  mcp-config-snippet, shutdown
- Angular 19 + Material project skeleton
- Dashboard, Audit, Settings, Rules, MCP-Config pages
- Browser-side CSRF token handling
- Optional auto-open browser on server start

### Out of scope

- Approval UI (the human approves via the CLI in change-5's interim;
  full approval UI lands as a follow-up — see "Open follow-up")
- UI automation tools (`ui.*`) — change-6
- OCR — change-6
- Boot autostart — change-7
- Dark/light theme fine-tuning beyond Angular Material defaults

## Approach

1. **HTTP transport is opt-in via config.** Default remains stdio. The
   server detects `security.http_shared_mode_enabled = true` and switches
   on the HTTP listener, allocating a port and writing `server.json`.
2. **CSRF**: the server writes a bearer secret to
   `%APPDATA%\LocalMcpTools\server.json`. The browser fetches it via
   `/api/csrf-token` (a SameSite cookie + token pattern). UI fetch calls
   include the token in a header; control plane rejects mismatches.
3. **Origin/Host allowlist**: requests whose `Origin` or `Host` header is
   not in the allowlist (default `["http://127.0.0.1:*", "http://localhost:*"]`)
   are rejected at the FastAPI middleware.
4. **Same instance can run stdio OR http, not both** — chosen at start
   time. This avoids two authority surfaces in one process.
5. **UI never makes MCP calls directly**. The MCP endpoint is for agents
   only. The UI uses `/api/*` for human actions.

## Why this matters later

- change-6 adds UI-automation and OCR controls under `/ui/automation`.
- change-7 ties "boot autostart" into the HTTP mode (so the autostart
  boots straight into the UI).

## Affected components

| Component | Notes |
|---|---|
| `server.py` | add HTTP/Streamable HTTP path |
| `control_api.py` | full implementation (was stubbed in change-1) |
| `transport/http.py` | new — bearer secret + Origin check + CSRF |
| `cli.py` | add `ui` (open browser), `start --http` |
| `ui/` | new Angular project |
| `scripts/build_frontend.bat` | Angular → `src/localmcptools/ui_assets/` |

## Open follow-up (NOT in this change)

Approval UI is genuinely needed for change-3 to be useful end-to-end. We
ship a CLI fallback (`localmcptools approve <approval_id>`) so testing
works now, and add the UI panel in a follow-up change before archive.
Tracking rationale: approval UI has its own design questions (UX for
showing the digest, command, expiry, ability to edit before approving).

## Key non-regression

- Default startup is **still** stdio.
- The HTTP listener binds `127.0.0.1` only.
- The `/mcp` HTTP endpoint is the same SDK surface as the stdio
  endpoint; tool semantics do not change.