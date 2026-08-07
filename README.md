# LocalMcpTools

A local Model Context Protocol (MCP) toolset for VS Code agents
(codebuddy, GitHub Copilot, and others).

## Status

**Phase 0 spike (`bootstrap-mcp-server`)**. The first usable artifact is a
stdio MCP server that exposes a single `workspace.inspect` tool. See
[`openspec/changes/bootstrap-mcp-server/`](openspec/changes/bootstrap-mcp-server/)
for the contract and [`docs/implementation-plan.md`](docs/implementation-plan.md)
for the full roadmap.

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

The spike configuration is identical for both agents — both speak the
standard MCP stdio transport. Once codebuddy / Copilot reads the file
they'll show `workspace.inspect` in their tool list.

### Where the data goes

Runtime state (audit log, settings, logs) lives under
`%APPDATA%\LocalMcpTools\` by default. Override with the
`LMCP_DATA_DIR` environment variable for tests or portable installs.