# LocalMcpTools operator notes

The project overview and quick start live in the repository
[README](../README.md). This page is the HTTP tunnel checklist.

## Control-plane secrets

`localmcptools start --http` writes `%APPDATA%\LocalMcpTools\server.json`
(mode `0600` on platforms that honor it) with two secrets:

- `mcp_bearer` — `Authorization: Bearer` for `/mcp` only.
- `admin_secret` — signs a human into `/api` and the UI. It is not a
  bearer token for tools.

The process also prints a one-time `http://127.0.0.1:<port>/api/login?token=...`
link. Opening it sets an HttpOnly `lmcp_session` cookie. The MCP bearer
cannot approve commands, change a profile, edit rules or settings, read
the audit log, or shut the server down.

`localmcptools approve` and `localmcptools set-profile` keep working
from a local shell. They update the database directly.

Rebuild the UI (`scripts\build_frontend.bat`) so `/ui/` shows the sign-in
form. Paste `admin_secret` there if the one-time link was already used.

## Tunnel

Keep `server.host` on loopback. The settings API rejects `0.0.0.0` and
any other non-loopback bind. Point the tunnel at the loopback port, for
example:

```powershell
cloudflared tunnel --url http://127.0.0.1:7890
```

Before the public hostname can reach the process, allow that hostname
and its origin. Edit `config.json` and restart, or sign in on loopback
and post the same patch to `/api/settings`:

```json
{
  "security": {
    "allowed_hosts": ["your-name.trycloudflare.com"],
    "origin_allowlist": [
      "http://127.0.0.1",
      "http://localhost",
      "https://your-name.trycloudflare.com"
    ]
  }
}
```

`allowed_hosts` is an exact hostname list (no wildcards). A quick tunnel
issues a new name each run, so update the list when it changes. Requests
with any other `Host` are rejected.

Copy the HTTP agent snippet from the signed-in MCP Config page. Put
`mcp_bearer` in the agent config and leave `admin_secret` in `server.json`.
Restarting the server rotates both secrets.
