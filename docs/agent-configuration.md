# Configuring your agent

Where each supported agent expects its `mcp.json`, what to put in it, and
the per-agent quirks we've learned about. The server is identical across
agents — only the *location* and the *contents* of the config file change.

> **Status**
>
> - **codebuddy** — verified end-to-end (programmatic + live session).
> - **GitHub Copilot** — verified end-to-end (programmatic + live session).
> - **workbuddy**, **minimax code** — configurations drafted from each
>   agent's public docs at the time of writing; **untested** in this
>   codebase. Marked "untested" so verification stays owed.

---

## The configuration shape

Every agent expects the same JSON shape (the standard MCP transport
manifest). The local server runs over stdio; each agent launches a child
process when it first needs to call a tool:

```json
{
  "mcpServers": {
    "localmcptools": {
      "command": "<absolute path to python>",
      "args": ["-m", "localmcptools", "start"],
      "cwd": "<absolute path to the LocalMcpTools checkout>"
    }
  }
}
```

`<absolute path to python>` is the interpreter inside `.venv\` after
`pip install -e .`. On Windows it is typically
`D:\\AI\\Projects\\LocalMcpTools\\.venv\\Scripts\\python.exe`.

`<absolute path to the LocalMcpTools checkout>` is the directory that
contains `pyproject.toml`. The server uses it as the working directory
for tool invocations and as the basis for the install scripts.

The full samples live in [`samples/`](../samples/).

---

## codebuddy — verified

**Config file location (Windows):**

```text
%USERPROFILE%\.codebuddy\mcp.json
```

**Working snippet (copy-paste, adjust paths):**

```json
{
  "mcpServers": {
    "localmcptools": {
      "command": "D:\\AI\\Projects\\LocalMcpTools\\.venv\\Scripts\\python.exe",
      "args": ["-m", "localmcptools", "start"],
      "cwd": "D:\\AI\\Projects\\LocalMcpTools"
    }
  }
}
```

A complete example is in
[`samples/mcp.codebuddy.json`](../samples/mcp.codebuddy.json).

**Quirks observed:**

- codebuddy restarts the child process automatically when it exits,
  so the stdio transport stays healthy across our
  `RestartCount=3` failures inside the scheduled task.
- codebuddy's tool palette groups by namespace, so the
  `environment.*` / `workspace.*` / `fs.*` / `shell.*` / `process.*`
  prefixes we use land in distinct sections.

---

## GitHub Copilot (VS Code) — verified

**Config file location (Windows):**

```text
%USERPROFILE%\.vscode\mcp.json
```

You can also drop a workspace-scoped copy at
`<repo>/.vscode/mcp.json`. The user-level copy wins if both exist.

**Working snippet:**

```json
{
  "mcpServers": {
    "localmcptools": {
      "command": "D:\\AI\\Projects\\LocalMcpTools\\.venv\\Scripts\\python.exe",
      "args": ["-m", "localmcptools", "start"],
      "cwd": "D:\\AI\\Projects\\LocalMcpTools"
    }
  }
}
```

A complete example is in
[`samples/mcp.copilot.json`](../samples/mcp.copilot.json).

**Quirks observed:**

- VS Code MCP integration requires the **stable** MCP extension
  (≥ 1.0). Earlier Insiders builds had a tool-discovery bug that
  silently dropped servers whose first tool name didn't start with a
  letter — the `environment.get` ordering avoids that.
- Copilot's per-tool description limits truncate at ~1 KiB. Keep
  descriptions tight (we already do).

---

## workbuddy — untested

**Config file location (Windows, per public docs):**

```text
%USERPROFILE%\.workbuddy\mcp.json
```

**Tentative snippet:**

```json
{
  "mcpServers": {
    "localmcptools": {
      "command": "D:\\AI\\Projects\\LocalMcpTools\\.venv\\Scripts\\python.exe",
      "args": ["-m", "localmcptools", "start"],
      "cwd": "D:\\AI\\Projects\\LocalMcpTools"
    }
  }
}
```

**Expected quirks (to verify once the agent is available):**

- workbuddy is documented to prefer Streamable HTTP over stdio when
  both are exposed. Once change-5 (`angular-ui-foundation`) lands we
  will add a `--http` flag to `localmcptools start` and document the
  HTTP+Origin configuration.
- workbuddy reportedly surfaces `next_actions` strings verbatim in
  its UI; ours are deliberately short.

---

## minimax code — untested

**Config file location (Windows, per public docs):**

```text
%USERPROFILE%\.minimax-code\mcp.json
```

**Tentative snippet:**

```json
{
  "mcpServers": {
    "localmcptools": {
      "command": "D:\\AI\\Projects\\LocalMcpTools\\.venv\\Scripts\\python.exe",
      "args": ["-m", "localmcptools", "start"],
      "cwd": "D:\\AI\\Projects\\LocalMcpTools"
    }
  }
}
```

**Expected quirks (to verify once the agent is available):**

- minimax code is documented to pass an `agent` hint in the request
  envelope; our audit row already records this field, so no schema
  change is expected.
- minimax code's tool call timeout is shorter than codebuddy's
  (~25 s vs ~120 s). For long-running presets, recommend
  `process.start_dev_server` instead of `workspace.run_test`.

---

## Concurrent shared server (planned)

When change-5 (`angular-ui-foundation`) lands, both codebuddy and
GitHub Copilot will be able to share one HTTP-mode server. At that
point the per-agent `mcp.json` switches from `"command" + "args"` to a
URL + bearer token, and the audit `agent` field will distinguish the
two clients. The audit schema already supports this — the only
remaining work is the Angular UI for browsing/filtering.

---

## Troubleshooting

- **Server doesn't appear in the agent's tool list.** Check that the
  path to `python.exe` is absolute and that `cwd` resolves to the
  repo root. Try `localmcptools start` from a fresh PowerShell
  window — it should print a "starting stdio server" line on stderr.
- **Tool calls hang.** Almost always a long-running shell call. The
  server enforces `timeout_ms ≤ 60 s` on `shell.run_command` and
  redirects to `process.start_dev_server`. Long work goes through a
  managed dev server, not a shell call.
- **`denied_by_rule` errors.** Review the matched rule under
  `audit.sqlite` (block-format-volume, block-privilege-escalation,
  etc.). Critical rules always win — there is no agent-supplied
  override.
- **Approvals keep expiring.** Default TTL is 10 minutes. Re-issue
  the approval immediately before retrying the call.

---

## See also

- [`docs/requirements.md`](requirements.md) — the product requirements
  this server implements.
- [`docs/implementation-plan.md`](implementation-plan.md) — the layered
  architecture and the technology choices behind the design.
- [`openspec/changes/`](../openspec/changes/) — the per-phase OpenSpec
  changes that drive development.