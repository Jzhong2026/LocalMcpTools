# Tasks: angular-ui-foundation

> Phase: **4** — Angular UI foundation
> Goal: HTTP mode + control plane + Angular SPA, all behind CSRF.

## 4.1 Transport HTTP module

- [ ] `transport/http.py`: middleware that:
  - allows `/mcp` with valid bearer (cookie not required)
  - requires `Origin` in allowlist for `/api/*`
  - requires `X-LMCP-CSRF` matching cookie for unsafe methods
- [ ] `transport/http.py`: allowlist defaults to `["http://127.0.0.1", "http://localhost"]`
- [ ] `transport/http.py`: reject `server.host != 127.0.0.1` at startup
- [ ] `server.py`: branch on `security.http_shared_mode_enabled`
- [ ] Unit test: missing Origin → `origin_required` on `/api/*`
- [ ] Unit test: wrong Origin → `403 origin_denied`
- [ ] Unit test: bad CSRF → `403 forbidden`

## 4.2 server.json lifecycle

- [ ] On HTTP start: allocate port (configured or OS-assigned via `port=0`)
- [ ] Write `server.json = {pid, port, started_at, csrf_token}` atomically
- [ ] On stop: remove `server.json`
- [ ] On start: detect stale `server.json` (different pid) → refuse with
  `error.message: "another server appears to be running at port X"`
- [ ] Unit test: stale pid detected via `psutil.pid_exists`

## 4.3 Bearer secret

- [ ] On HTTP start: generate 32-byte URL-safe random token
- [ ] Token is reused as CSRF secret (single secret keeps things simple)
- [ ] `/mcp` requires `Authorization: Bearer <token>`
- [ ] `/api/*` requires the cookie `lmcp_csrf=<token>` (HttpOnly) AND
  header `X-LMCP-CSRF: <token>` (same value)
- [ ] `/api/csrf-token` returns `{csrf_token: <token>}` AND sets the cookie
- [ ] Unit test: missing bearer on `/mcp` → `401 unauthorized`

## 4.4 Control plane endpoints

For each endpoint: implementation + at least one integration test.

- [ ] `GET /api/status`
- [ ] `GET /api/audit` (with all filters)
- [ ] `GET /api/audit/{id}`
- [ ] `GET /api/audit/{id}/log`
- [ ] `GET /api/csrf-token`
- [ ] `POST /api/settings` (split applied vs requires_restart)
- [ ] `GET /api/rules`
- [ ] `PATCH /api/rules/{id}` (toggle enabled)
- [ ] `POST /api/rules/reload`
- [ ] `GET /api/backgrounds`
- [ ] `POST /api/backgrounds/{id}/stop`
- [ ] `GET /api/mcp-config-snippet`
- [ ] `POST /api/shutdown`

## 4.5 Angular project skeleton

- [ ] `ui/angular.json` with `outputPath: "../src/localmcptools/ui_assets"`
- [ ] `ui/package.json` with Angular 19 + Material pinned
- [ ] `ui/src/main.ts` bootstraps standalone components
- [ ] `ui/src/app/app.routes.ts` defines all five pages
- [ ] `ui/src/app/app.component.ts` is a layout shell (top bar + side nav)
- [ ] `scripts/build_frontend.bat` runs `ng build --configuration production`
  and copies output to `src/localmcptools/ui_assets/`
- [ ] `scripts/dev_frontend.bat` runs `ng serve` with proxy to backend
- [ ] `src/localmcptools/ui_assets/` is gitignored; rebuilt artifact is the
  served one

## 4.6 Core services

- [ ] `core/api.service.ts`: typed HttpClient wrapper with CSRF header injection
- [ ] `core/csrf.service.ts`: bootstrap fetches `/api/csrf-token`,
  caches, refreshes on 403
- [ ] `core/models.ts`: interfaces for `Audit`, `Rule`, `Background`,
  `Setting`, `McpConfig`

## 4.7 Feature pages

### Dashboard
- [ ] `dashboard.component`: composes 4 widgets
- [ ] `server-status.widget`: calls `/api/status`
- [ ] `backgrounds.widget`: calls `/api/backgrounds`, renders stop buttons
- [ ] `ports.widget`: calls `/api/backgrounds` (managed ports) + filter
- [ ] `recent-calls.widget`: calls `/api/audit?page_size=5`

### Audit
- [ ] `audit-list.component`: virtual scroll table, filter chips
- [ ] `audit-detail-drawer.component`: shows row details + link to log
- [ ] `log-viewer.component`: virtual scroll + debounced search
- [ ] Row click → drawer; "View Log" button → modal

### Settings
- [ ] `settings.component`: section forms
- [ ] Save → `POST /api/settings`; show toast split applied/requires_restart
- [ ] Restart banner if any required

### Rules
- [ ] `rules-list.component`: list with hit count + last hit
- [ ] `rule-detail.component`: shows match rules
- [ ] Enable/disable toggle calls `PATCH`
- [ ] "Reload" button calls `POST /api/rules/reload`

### MCP Config
- [ ] `mcp-config.component`: shows snippet, copy buttons (Code + Insiders)

## 4.8 Browser-side niceties

- [ ] Theme = system: subscribe to `prefers-color-scheme` media query
- [ ] Auto-open browser: `os.startfile` (Windows); guarded by try/except
- [ ] Server-down detection: HttpInterceptor converts connection errors
  into a global banner with "Retry"

## 4.9 DoD (must all pass)

- [ ] Default startup still stdio; HTTP not started
- [ ] `http_shared_mode_enabled=true` → HTTP listener, `/ui/` loads
- [ ] `/ui/` shows Dashboard with live data
- [ ] `/audit` page lists recent calls with filters working
- [ ] `/audit` drawer shows args + log viewer works
- [ ] `/settings` saves; applied fields hot-reload, restart-required ones
  trigger banner
- [ ] `/rules` lists + enables/disables + reloads
- [ ] `/mcp-config` shows correct port and copy buttons work
- [ ] Origin/Host/CSRF enforced (verified by integration tests)
- [ ] Bearer required on `/mcp` (verified by integration test)
- [ ] `server.json` lifecycle correct (start, mid-run, stop, stale)
- [ ] `localmcptools stop` cleanly tears down HTTP, deletes `server.json`
- [ ] Server-down detection works in SPA
- [ ] No code-behind (`.cs`, `Click=`) for any XAML — N/A for web; for
  Angular: no jQuery, no inline scripts in templates