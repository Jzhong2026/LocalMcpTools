# Change: extended-tools-and-packaging

> Covers: `docs/implementation-plan.md` **Phases 6, 7, 8 — extended diagnostic
> tools, packaging, and cross-agent verification**

## Intent

This change does three things that hang together naturally:

1. **Diagnostic extras** (phase 6): give weaker agents the tools they need
   to avoid constructing shell pipelines for diagnostics — runtime
   detection, VS Code diagnostics, log-file tail/grep, structured
   failure explanation.
2. **Packaging & boot autostart** (phase 7): installable via
   `localmcptools install`, with a Windows scheduled task for boot
   autostart, README + docs, and a rule-management UI page.
3. **Cross-agent verification** (phase 8): confirm the same server works
   with codebuddy, GitHub Copilot, and document the config for workbuddy
   / minimax code.

Bundling them into one change because:

- They all ride on top of changes 1–6 and do not introduce new capability
  surfaces that need their own approval lifecycle.
- They are the "polish + verify" step — individually they are smaller than
  earlier changes.

After this change, a fresh user can:

- Clone the repo, run `pip install -e . && localmcptools install`,
- Open VS Code with codebuddy or Copilot, configure the auto-printed
  `mcp.json` snippet,
- Watch an `observe`-profile agent diagnose their project, ask for an
  approval in the UI, watch the run succeed, audit it after.

## Scope

### In scope

- `runtime.detect_runtime` / `runtime.get_env` / `runtime.list_path`
- `vscode.get_problems` / `vscode.get_installed_extensions` /
  `vscode.get_logs` / `vscode.get_debug_sessions`
- `diagnostics.collect` / `diagnostics.explain_failure`
- `localmcptools install` / `localmcptools uninstall` (Windows scheduled
  task)
- `/ui/rules` page: full edit (not just enable/disable)
- README + `docs/agent-configuration.md`
- Verification: codebuddy + Copilot work, multi-agent concurrency

### Out of scope

- New tool capabilities beyond diagnostics
- New profile or approval lifecycle (everything here is `observe`)
- Packaging into a single-file executable (PyInstaller / Nuitka)

## Approach

1. **Diagnostic tools are all `observe`-only.** They are read paths over
   artifacts the OS already exposes. No approval flow.
2. **`diagnostics.collect` aggregates** — single call returns runtime +
   Git + VS Code Problems + ports + recent failed runs. This is what an
   agent calls first when "something is wrong".
3. **`diagnostics.explain_failure` takes a `run_id`** and returns a
   classification (timeout / exit_code / denied_by_rule / approval_required
   / verification_failed / unknown), key evidence (lines from the log),
   and `next_actions`.
4. **Packaging is scripts, not bundling.** `install_windows_task.ps1`
   creates the scheduled task; the user can also place a `.lnk` in
   `shell:startup` as a fallback.
5. **Cross-agent verification** uses the same codebuddy/Copilot `mcp.json`
   pattern from change-1, pointing each at the same HTTP-mode server.

## Why this matters later

This is the last change that builds new functionality. Anything beyond is:

- Bug fixes / rule additions → not a new change.
- A new capability profile → new change.
- Web DOM automation → new change.

## Affected components

| Component | Notes |
|---|---|
| `tools/runtime.py` | new |
| `tools/vscode.py` | new |
| `tools/diagnostics.py` | new |
| `cli.py` | add `install` / `uninstall` subcommands |
| `scripts/install_windows_task.ps1` | new |
| `scripts/uninstall_windows_task.ps1` | new |
| `ui/src/app/features/rules/` | extend with edit/create |
| `README.md` | full setup + usage |
| `docs/agent-configuration.md` | new |

## Key non-regression

- The boot autostart is **user-level** scheduled task, not a Windows
  service. No admin needed.
- The cross-agent verification config never auto-writes to user VS Code
  configs; users copy the snippet.