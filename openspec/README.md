# LocalMcpTools — OpenSpec Changes

This directory tracks implementation work for LocalMcpTools as
[OpenSpec](https://github.com/Fission-AI/OpenSpec) changes.

> **Source of truth**: design lives in [docs/requirements.md](../docs/requirements.md) (why)
> and [docs/implementation-plan.md](../docs/implementation-plan.md) (how). This directory
> decomposes that plan into shippable units with concrete DoDs.

## Workflow

```text
openspec/changes/<id>/
├── proposal.md    # Why we're doing this (intent, scope, approach)
├── specs/         # Requirements + Given/When/Then scenarios
├── design.md      # Technical design (modules, tables, deps)
└── tasks.md       # Implementation checklist (checkbox driven)

# Once a change is implemented and reviewed, it moves to:
openspec/archive/YYYY-MM-DD-<id>/
```

Each change covers one or more phases from `docs/implementation-plan.md` §7.
A change is **archived** (not deleted) once its tasks are all ticked off and
its specs are reflected in the running server.

## Active Changes

| # | change-id | plan phases | theme | status |
|---|---|---|---|---|
| 1 | `bootstrap-mcp-server` | 0 | Compatibility + security spike | done *(live-agent hand-off owed)* |
| 2 | `core-shell-and-audit` | 1 | Fix "agent gets no result" | done |
| 3 | `policy-and-safety` | 2 | Server-side authority + approval | done |
| 4 | `managed-process-and-ports` | 3 | Managed dev server + port queries | done |
| 5 | `angular-ui-foundation` | 4 | Local browser UI for audit + settings | done *(live browser smoke-test owed)* |
| 6 | `ui-automation-and-ocr` | 5 | UIA + OCR-backed UI verification | proposed |
| 7 | `extended-tools-and-packaging` | 6 + 7 + 8 | Diagnostic extras, packaging, cross-agent verify | done *(reboot smoke-test owed)* |

## Dependency Graph

```text
bootstrap-mcp-server ──┬──> core-shell-and-audit
                        │
                        ├──> policy-and-safety
                        │         │
                        │         └──> managed-process-and-ports
                        │
                        └──> angular-ui-foundation ──> ui-automation-and-ocr
                                                          │
                                                          └──> extended-tools-and-packaging
```

`bootstrap-mcp-server` blocks everything else because the MCP SDK version and
stdio wiring must be locked before any tool can be defined.

## How to Use

### With the OpenSpec CLI (optional)

If you install `@fission-ai/openspec` globally, every change is OPSX-aware:

```bash
openspec status                        # overview
openspec status --change <id> --json   # detailed state
openspec instructions <artifact> \
    --change <id> --json               # template + context for one artifact
openspec validate <id> --strict        # sanity check before /opsx:archive
```

### Without the CLI (manual)

You can also drive these changes by hand:

1. Read `proposal.md` to understand intent.
2. Read `specs/` for the contract (inputs, outputs, error codes, scenarios).
3. Read `design.md` for the technical approach.
4. Work through `tasks.md`, ticking boxes as you go.
5. When all tasks are done and tests pass, move the change to
   `openspec/archive/YYYY-MM-DD-<id>/` and update the table above.

### Conventions

- `client_instance` / `workspace_id` / `profile` / `policy_version` /
  `approval_id` / `run_id` are first-class audit fields. Any new tool spec
  **must** declare which of these it writes.
- Sensitive values (tokens, passwords) are redacted **before** they touch
  audit or artifacts.
- Tool error responses use the standard envelope in
  `src/localmcptools/tools/_common.py`. New error codes must be added to the
  shared registry, not invented per tool.