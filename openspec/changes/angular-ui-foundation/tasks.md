# Tasks: angular-ui-foundation

> Phase: **4** — Angular UI foundation
> Goal: HTTP mode + control plane + Angular SPA, all behind CSRF.

## 4.1 Transport HTTP module

- [x] `transport/http.py`: middleware that:
  - allows `/mcp` with valid bearer (cookie not required)
  - requires `Origin` in allowlist for `/api/*`
  - requires `X-LMCP-CSRF` matching cookie for unsafe methods
- [x] `transport/http.py`: allowlist defaults to `["http://127.0.0.1", "http://localhost"]`
- [x] `transport/http.py`: reject `server.host != 127.0.0.1` at startup
- [x] `server.py`: branch on `security.http_shared_mode_enabled`
- [x] Unit test: missing Origin → `origin_required` on `/api/*`
- [x] Unit test: wrong Origin → `403 origin_denied`
- [x] Unit test: bad CSRF → `403 forbidden`

## 4.2 server.json lifecycle

- [x] On HTTP start: allocate port (configured or OS-assigned via `port=0`)
- [x] Write `server.json = {pid, port, started_at, csrf_token}` atomically
- [x] On stop: remove `server.json`
- [x] On start: detect stale `server.json` (different pid) → refuse with
  `error.message: "another server appears to be running at port X"`
- [x] Unit test: stale pid detected via `psutil.pid_exists`

## 4.3 Bearer secret

- [x] On HTTP start: generate 32-byte URL-safe random token
- [x] Token is reused as CSRF secret (single secret keeps things simple)
- [x] `/mcp` requires `Authorization: Bearer <token>`
- [x] `/api/*` requires the cookie `lmcp_csrf=<token>` (HttpOnly) AND
  header `X-LMCP-CSRF: <token>` (same value)
- [x] `/api/csrf-token` returns `{csrf_token: <token>}` AND sets the cookie
- [x] Unit test: missing bearer on `/mcp` → `401 unauthorized`

## 4.4 Control plane endpoints

For each endpoint: implementation + at least one integration test.

- [x] `GET /api/status`
- [x] `GET /api/audit` (with all filters)
- [x] `GET /api/audit/{id}`
- [x] `GET /api/audit/{id}/log`
- [x] `GET /api/csrf-token`
- [x] `POST /api/settings` (split applied vs requires_restart)
- [x] `GET /api/rules`
- [x] `PATCH /api/rules/{id}` (toggle enabled)
- [x] `POST /api/rules/reload`
- [x] `GET /api/backgrounds`
- [x] `POST /api/backgrounds/{id}/stop`
- [x] `GET /api/mcp-config-snippet`
- [x] `POST /api/shutdown`

## 4.5 Angular project skeleton

- [x] `ui/angular.json` with `outputPath: "../src/localmcptools/ui_assets"`
- [x] `ui/package.json` with Angular 19 + Material pinned
- [x] `ui/src/main.ts` bootstraps standalone components
- [x] `ui/src/app/app.routes.ts` defines all five pages
- [x] `ui/src/app/app.component.ts` is a layout shell (top bar + side nav)
- [x] `scripts/build_frontend.bat` runs `ng build --configuration production`
  and copies output to `src/localmcptools/ui_assets/`
- [x] `scripts/dev_frontend.bat` runs `ng serve` with proxy to backend
- [x] `src/localmcptools/ui_assets/` is gitignored; rebuilt artifact is the
  served one

## 4.6 Core services

- [x] `core/api.service.ts`: typed HttpClient wrapper with CSRF header injection
- [x] `core/csrf.service.ts`: bootstrap fetches `/api/csrf-token`,
  caches, refreshes on 403 *(implemented as ``csrf.interceptor.ts`` + cached token in core/csrf.interceptor.ts)*
- [x] `core/models.ts`: interfaces for `Audit`, `Rule`, `Background`,
  `Setting`, `McpConfig`

## 4.7 Feature pages

### Dashboard
- [x] `dashboard.component`: composes 4 widgets
- [x] `server-status.widget`: calls `/api/status`
- [x] `backgrounds.widget`: calls `/api/backgrounds`, renders stop buttons
- [x] `ports.widget`: calls `/api/backgrounds` (managed ports) + filter
- [x] `recent-calls.widget`: calls `/api/audit?page_size=5`

### Audit
- [x] `audit-list.component`: virtual scroll table, filter chips
- [x] `audit-detail-drawer.component`: shows row details + link to log
- [x] `log-viewer.component`: virtual scroll + debounced search
- [x] Row click → drawer; "View Log" button → modal

### Settings
- [x] `settings.component`: section forms
- [x] Save → `POST /api/settings`; show toast split applied/requires_restart
- [x] Restart banner if any required

### Rules
- [x] `rules-list.component`: list with hit count + last hit
- [x] `rule-detail.component`: shows match rules
- [x] Enable/disable toggle calls `PATCH`
- [x] "Reload" button calls `POST /api/rules/reload`

### MCP Config
- [x] `mcp-config.component`: shows snippet, copy buttons (Code + Insiders)

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