# Design: angular-ui-foundation

## Folder layout

```text
ui/                                    # Angular 19 source
├── angular.json                       # outputPath: ../src/localmcptools/ui_assets
├── package.json
├── tsconfig.json
├── src/
│   ├── index.html
│   ├── main.ts
│   ├── styles.css
│   └── app/
│       ├── app.config.ts              # provideRouter + provideHttpClient
│       ├── app.routes.ts              # /dashboard /audit /settings /rules /mcp-config
│       ├── app.component.ts/html
│       ├── core/
│       │   ├── api.service.ts         # HttpClient wrapper
│       │   ├── csrf.service.ts        # token + cookie sync
│       │   └── models.ts              # interface types
│       ├── features/
│       │   ├── dashboard/
│       │   │   ├── dashboard.component.ts/html/css
│       │   │   └── widgets/
│       │   │       ├── server-status.widget.ts
│       │   │       ├── backgrounds.widget.ts
│       │   │       ├── ports.widget.ts
│       │   │       └── recent-calls.widget.ts
│       │   ├── audit/
│       │   │   ├── audit-list.component.ts
│       │   │   ├── audit-detail-drawer.component.ts
│       │   │   └── log-viewer.component.ts
│       │   ├── settings/
│       │   │   └── settings.component.ts
│       │   ├── rules/
│       │   │   ├── rules-list.component.ts
│       │   │   └── rule-detail.component.ts
│       │   └── mcp-config/
│       │       └── mcp-config.component.ts
│       └── shared/
│           ├── components/
│           │   ├── status-badge.component.ts
│           │   ├── time-ago.component.ts
│           │   └── bytes-pipe.component.ts
│           └── pipes/

src/localmcptools/
├── server.py                          # add http mount when configured
├── control_api.py                     # full implementation
├── transport/
│   ├── __init__.py
│   └── http.py                        # bearer + Origin + CSRF middleware
└── ui_assets/                         # build output (gitignored except via build_frontend.bat)

scripts/
├── dev_frontend.bat
└── build_frontend.bat                 # ng build --prod && copy to ui_assets/

tests/
├── integration/
│   ├── test_http_origin.py
│   ├── test_http_csrf.py
│   └── test_control_plane.py
└── e2e/
    └── test_ui_smoke.py               # playwright (optional)
```

## Server bootstrap (HTTP mode)

```text
1. Load config; check security.http_shared_mode_enabled
2. If false: start stdio loop; exit
3. If true:
   a. Bind 127.0.0.1:<port>; if 0, OS-assign
   b. Write server.json {pid, port, started_at, csrf_token}
   c. Mount FastAPI:
        /mcp    -> mcp.streamable_http_app()
        /api    -> control_api.router
        /ui     -> StaticFiles("ui_assets", html=True)
        /       -> redirect to /ui/
   d. Open browser if ui.auto_open_browser
   e. Run uvicorn programmatically
   f. SIGTERM hook -> /api/shutdown path
```

## Middleware (`transport/http.py`)

```python
@app.middleware("http")
async def origin_and_csrf(request, call_next):
    if request.url.path.startswith("/mcp"):
        return await _check_bearer_then_call(request, call_next)
    if request.url.path.startswith("/api/"):
        return await _check_origin_csrf_then_call(request, call_next)
    return await call_next(request)
```

Order of checks for `/api/*`:
1. Origin must be present and in allowlist.
2. If method is unsafe (POST/PATCH/DELETE), require `X-LMCP-CSRF` matching
   the cookie `lmcp_csrf`.

`/api/csrf-token` is excluded from the CSRF check itself.

## Control plane skeleton

```python
# control_api.py (sketch)
router = APIRouter(prefix="/api")

@router.get("/status")
async def status(): ...

@router.get("/audit")
async def audit_list(agent: str | None, tool: str | None,
                     ok: int | None, from_: int | None,
                     to: int | None, page: int = 1,
                     page_size: int = 50,
                     user: AuthContext = Depends(require_csrf)): ...

@router.post("/settings")
async def update_settings(payload: dict,
                          user: AuthContext = Depends(require_csrf)): ...

# etc.
```

## Config additions

```jsonc
{
  "security": {
    "transport_mode": "stdio",          // "stdio" | "http"
    "http_shared_mode_enabled": false,
    "origin_allowlist": ["http://127.0.0.1", "http://localhost"],
    "redact_before_persist": true,
    "approval_ttl_seconds": 600
  },
  "ui": {
    "auto_open_browser": true,
    "theme": "system"                   // "light" | "dark" | "system"
  },
  "process": {
    "managed_max_concurrent": 4,
    "shutdown_grace_seconds": 5,
    "reconcile_interval_seconds": 30
  }
}
```

## New dependencies

| Package | Why |
|---|---|
| `fastapi` | HTTP framework |
| `uvicorn[standard]` | ASGI server |
| Angular 19 + Material | UI |

Versions locked after `npm install` succeeds.

## Out-of-scope reminders

- Approval UI is deferred (see proposal's "Open follow-up").
- The UI never directly calls MCP tools. It's a control plane.
- Boot autostart is change-7.