# LocalMcpTools

A local Model Context Protocol (MCP) toolset for VS Code agents
(codebuddy, GitHub Copilot, and others).

## Status

**Phases 0-6 (`ui-automation-and-ocr`) code-complete.** Live spike
accuracy numbers + the cross-agent live hand-off check still owed.

What's shipped:

- **Read-only diagnostics**: `environment.get`, `workspace.register / list /
  inspect / search_text / git_status`, `fs.read_range / tail_log_file /
  grep_files`, `output.tail / read_range / search`, `runtime.detect_runtime /
  get_env / list_path`, `vscode.get_problems / get_installed_extensions /
  get_logs / get_debug_sessions`, `diagnostics.collect /
  explain_failure`.
- **Controlled side effects**: `shell.run_command`, `workspace.run_test /
  build / lint`, with server-side `observe` / `workspace_exec` profile gating
  + one-time human approvals + 10 built-in deny rules (Format-Volume,
  Format/diskpart, cipher /w, rm-system, bcdedit/bootrec, netsh reset,
  registry HKLM, privilege escalation, kill-protected, RDP enable,
  remote-download-exec).
- **Lifecycle-bound dev servers**: `process.start_dev_server /
  get_status / list_managed / stop_managed / list_listening_ports /
  find_by_port`. Children are attached to a Windows Job Object so they die
  with the server.
- **HTTP control plane**: `localmcptools start --http` boots a FastAPI app
  on `127.0.0.1:7890` (configurable) with Origin allowlist, CSRF
  double-submit, bearer auth for `/mcp`, and 16 control endpoints
  (`/api/status`, `/api/audit`, `/api/rules`, `/api/backgrounds`,
  `/api/settings`, `/api/mcp-config-snippet`, `/api/windows/*`,
  `/api/shutdown`, ...).
- **Angular SPA**: Dashboard / Audit / Settings / Rules / MCP-config /
  Automation pages under `ui/`. Build with `scripts/build_frontend.bat`;
  the bundle lands in `src/localmcptools/ui_assets/` and is served at `/ui/`.
- **UI automation + OCR**: `ui.list_windows / authorize_window /
  get_ui_tree / find_element / screenshot_{window,full,region} /
  click_element / type_text / act_and_verify` and
  `ocr.ocr_region / find_text / assert_text` with a Windows OCR
  provider that falls back to a stub on hosts without `winsdk`.
  Verification harness (UIA / screenshot / OCR predicates).
- **Packaging**: `localmcptools install [--method scheduled_task|startup_folder]`
  registers a user-level (no admin) Windows scheduled task; `uninstall`
  removes it. Idempotent.

What's still owed: live Windows OCR accuracy spike (provider ships but
real numbers haven't been measured yet), the cross-agent live
hand-off check for `workbuddy` / `minimax code`, and the Windows reboot
smoke test for the scheduled task. See
[`openspec/changes/`](openspec/changes/) for the contracts and
[`docs/implementation-plan.md`](docs/implementation-plan.md) for the full
roadmap.

## HTTP control plane + UI

```powershell
# Start the FastAPI app + control plane + UI on the loopback.
localmcptools start --http --port 7890 --auto-open-browser

# In another shell: build the Angular SPA so the static mount has content.
scripts\build_frontend.bat   # runs `npm install` if needed, then `ng build --prod`

# Browse to http://127.0.0.1:7890/ui/
```

Bearer secret for `/mcp` is generated at boot and stored in
`%APPDATA%\LocalMcpTools\server.json` next to the bound port.
CSRF double-submit protects every unsafe `/api/*` request; the SPA
fetches `/api/csrf-token` on first load and includes the
`X-LMCP-CSRF` header from then on.

## Quick start

```bash
# 1. Create a venv and install (editable, so src changes are live).
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 2. Run the tests.
.\.venv\Scripts\python.exe -m pytest

# 3. Launch the server (it blocks on stdio, waiting for an MCP client).
.\.venv\Scripts\python.exe -m localmcptools
```

## Connecting agents

Both **codebuddy** and **VS Code's GitHub Copilot Chat** discover MCP servers
through a JSON file the IDE watches. The sample files in
[`samples/`](samples/) show the spike configuration; copy the relevant one
into the location your agent expects.

| Agent | Where the file goes (Windows) | Sample |
|---|---|---|
| codebuddy | `%USERPROFILE%\.codebuddy\mcp.json` | [samples/mcp.codebuddy.json](samples/mcp.codebuddy.json) |
| GitHub Copilot Chat (VS Code) | `%USERPROFILE%\.vscode\mcp.json` (workspace `.vscode/mcp.json` also works) | [samples/mcp.copilot.json](samples/mcp.copilot.json) |

The configuration is identical for both agents — both speak the standard MCP
stdio transport. Once codebuddy / Copilot reads the file, the tool list
includes the `environment.*`, `workspace.*`, `fs.*`, `output.*`, `shell.*`,
and `process.*` surfaces.

### Managed development servers

`process.start_dev_server` accepts only the built-in `python-uvicorn`,
`node-vite`, `node-next-dev`, and `dotnet-run` presets. It requires a workspace
with the `managed_process` profile and a matching one-time approval. Use
`process.get_status`, `process.list_managed`, `process.stop_managed`,
`process.list_listening_ports`, and `process.find_by_port` to observe and stop
owned processes. Arbitrary `process.kill(pid)` is intentionally not exposed.

Managed process output is available immediately through the returned Artifact
handle. On Windows, child trees are attached to a kill-on-close Job Object, so
they are terminated when the MCP server exits.

### Where the data goes

Runtime state (audit log, settings, logs) lives under
`%APPDATA%\LocalMcpTools\` by default. Override with the
`LMCP_DATA_DIR` environment variable for tests or portable installs.

## Auto-start on logon (Windows)

```powershell
# Register a user-level scheduled task. No admin required.
localmcptools install

# Or, if scheduled-task creation fails, drop a .lnk in the Startup folder.
localmcptools install --method startup_folder

# To remove
localmcptools uninstall
```

The task boots `python -m localmcptools start` from the repo root at user
logon, with `RestartCount=3, RestartInterval=1min` so transient failures
self-heal. The scheduled task never runs elevated, so it cannot affect
other users.

## Configuring your agent

See [`docs/agent-configuration.md`](docs/agent-configuration.md) for the
per-agent `mcp.json` locations and snippets (codebuddy, GitHub Copilot,
workbuddy, minimax code — last two marked "untested" until those agents
ship a public MCP spec).
