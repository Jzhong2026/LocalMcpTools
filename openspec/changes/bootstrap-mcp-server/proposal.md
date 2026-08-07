# Change: bootstrap-mcp-server

> Covers: `docs/implementation-plan.md` **Phase 0 — Compatibility & security spike**

## Intent

Before any real tool is defined, we need to know which MCP Python SDK version
and transport (stdio vs Streamable HTTP) actually works against the real target
agents (codebuddy, GitHub Copilot). Without this, every later change would be
guessing at SDK surface area and we'd burn time fixing the wrong assumptions.

This change establishes:

1. A minimal but **real** MCP server that one tool (`workspace.inspect` stub)
   can be discovered through.
2. The transport wiring for both stdio and Streamable HTTP, validated against
   actual agent configs.
3. The locked MCP SDK version in `requirements.txt`.
4. The first deployment of the `observe` profile and the `approval_required`
   error envelope, so every later change inherits the same authority model.

## Scope

### In scope

- `pyproject.toml` + `requirements.txt` + `requirements-dev.txt`
- `python -m localmcptools` entrypoint
- stdio transport wired and verified with codebuddy **and** Copilot
- Streamable HTTP transport wired **only if** stdio proves insufficient
  (default position: it will be sufficient — HTTP is reserved for later change-5)
- `Workspace` registry skeleton with one canonical `workspace.inspect` stub
- `audit` table skeleton (just enough to record the calls we make during spike)
- One sample `mcp.json` snippet for Code and one for Code - Insiders
- All work recorded under `audit.retention_days=7` for the spike

### Out of scope

- Any real tool beyond `workspace.inspect` stub
- Any UI (deferred to change-5)
- Streamable HTTP if stdio is enough
- Policy file format, approval flow, OCR — all later changes
- Linux/macOS code paths (interfaces may be stubbed, implementations are empty)

## Approach

1. Install the latest MCP Python SDK in a venv, **do not lock yet**.
2. Write the smallest possible server: one tool that returns
   `{ok: true, data: {pid: ..., build: ...}, meta: ..., error: null}`.
3. Configure codebuddy and Copilot to launch the server via stdio.
4. Verify in both agents: tool list shown, tool callable, structured result
   round-trips intact.
5. Only if stdio fails, fall back to Streamable HTTP — and if so, verify
   Origin/Host checks and local bearer secret at the same time.
6. Lock the SDK version. Document the actual `import` paths used in `design.md`
   so no later change guesses.

## Why this matters later

Every later change depends on `bootstrap-mcp-server` because:

- They inherit the SDK import paths from this change's `design.md`.
- They inherit the audit schema and the envelope.
- They inherit the `observe`-by-default policy decision.

A change that goes out of bounds on these — e.g. inventing a new error
envelope, or accidentally granting default write access — must be caught here
or propagated cleanly.

## Affected components

| Component | Notes |
|---|---|
| `src/localmcptools/__main__.py` | Entry point |
| `src/localmcptools/server.py` | FastMCP assembly |
| `src/localmcptools/tools/_common.py` | Envelope (ToolResponse / ToolMeta / ToolError) |
| `src/localmcptools/tools/workspace.py` | `workspace.inspect` stub |
| `src/localmcptools/persistence/db.py` | SQLite + WAL init |
| `src/localmcptools/persistence/audit.py` | Minimal call recorder |
| `src/localmcptools/config/paths.py` | `%APPDATA%\LocalMcpTools\` resolution |
| `requirements.txt` | MCP SDK locked here |
| `tests/integration/test_mcp_discovery.py` | First integration test |

## Risks

- **MCP SDK API churn**: mitigated by spike + lock.
- **Agent-specific stdio quirks**: mitigated by testing both target agents.
- **Hidden assumptions about server lifecycle**: mitigated by checking
  `server.json` pid liveness on startup.