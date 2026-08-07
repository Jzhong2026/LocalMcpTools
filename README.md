# LocalMcpTools

A local Model Context Protocol (MCP) toolset for VS Code agents
(codebuddy, GitHub Copilot, and others).

## Status

**Phase 3 (`managed-process-and-ports`)**. The stdio MCP server now includes
read-only environment/workspace/file/output inspection, controlled shell and
workspace presets, approval and deny-rule enforcement, and lifecycle-bound
development servers. See [`openspec/changes/`](openspec/changes/) for the
contracts and [`docs/implementation-plan.md`](docs/implementation-plan.md) for
the full roadmap.

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
