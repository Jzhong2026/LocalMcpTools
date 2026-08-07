# Tasks: extended-tools-and-packaging

> Phases: **6** (extended tools) + **7** (packaging) + **8** (cross-agent verify)

## 6.1 `runtime.detect_runtime` / `get_env` / `list_path`

- [ ] `tools/runtime.py`: enumerate python via `python --version` per PATH entry
- [ ] Same for node (`node --version`), npm (`npm --version`), dotnet (`dotnet --version`)
- [ ] Cache results for 60s
- [ ] `get_env` redacts values per `safety.redact`
- [ ] `list_path` checks `os.path.isdir` and `os.path.isfile`
- [ ] Unit test: fixture PATH entries → correct is_default detection
- [ ] Integration test: real system → python detected

## 6.2 `vscode.*` tools

- [ ] `tools/vscode.py`: `get_problems` reads workspaceStorage state.vscdb
  (read-only) and parses known shapes
- [ ] `get_installed_extensions`: read `extensions.json` from `%APPDATA%\Code\User\`
- [ ] `get_logs(channel)`: tail the channel's log file
- [ ] `get_debug_sessions`: read `debug.sessions` from VS Code state
- [ ] Each tool returns `vscode_not_running` if state files don't exist
- [ ] Unit test: missing files → `vscode_not_running`
- [ ] Integration test: open fixture VS Code workspace → problems list non-empty

## 6.3 `diagnostics.collect` / `explain_failure`

- [ ] `diagnostics/aggregate.py`: one call fans out to all section tools
- [ ] `diagnostics/aggregate.py`: cap each section to 64KB inline; spill to
  artifact handles when over
- [ ] `diagnostics/classify.py`: classification enum + per-class logic
- [ ] `diagnostics/next_actions.py`: advice strings per classification
- [ ] `tools/diagnostics.py`: `collect` and `explain_failure`
- [ ] Unit test: classifier matrix
- [ ] Integration test: a failed `workspace.run_test` → `explain_failure`
  returns `exit_code` classification with key_evidence lines

## 7.1 `localmcptools install` / `uninstall`

- [ ] `cli.py`: add `install` and `uninstall` subcommands
- [ ] `scripts/install_windows_task.ps1` registered task per design.md
- [ ] `scripts/uninstall_windows_task.ps1` removes task
- [ ] Idempotent install
- [ ] `--method startup-folder` fallback places `.lnk`
- [ ] Smoke test on Windows VM: install → reboot → server is running

## 7.2 Rule management UI

- [ ] `ui/src/app/features/rules/`: full edit form (not just enable/disable)
- [ ] Validate JSON before save
- [ ] "Test match" button: take a sample command + show which rule matches
- [ ] "Reload from disk" button (already in change-5)

## 7.3 README + agent docs

- [ ] Top-level `README.md`: per design.md scope
- [ ] `docs/agent-configuration.md`: per agent, file location + snippet +
  quirks
- [ ] Link from README

## 8.1 Cross-agent verification

- [ ] Verify codebuddy: tool list visible; `workspace.inspect` callable;
  `workspace.run_test` returns `approval_required`; after approval,
  succeeds
- [ ] Verify Copilot: same scenarios; "what python is installed" question
  triggers `runtime.detect_runtime`
- [ ] Verify concurrent: two agents share one HTTP server; audit rows
  interleaved correctly with `agent` set
- [ ] Document any per-agent quirks in `docs/agent-configuration.md`

## 8.2 Future-agent stubs

- [ ] `docs/agent-configuration.md` includes workbuddy and minimax code
  sections marked "untested"
- [ ] Based on each agent's published docs at the time of writing

## DoD (must all pass)

- [ ] `runtime.detect_runtime` returns python / node / dotnet / npm paths + versions
- [ ] `vscode.get_problems` reads VS Code state correctly
- [ ] `diagnostics.collect` returns all sections + `next_actions`
- [ ] `diagnostics.explain_failure` classifies correctly per the matrix
- [ ] `localmcptools install` creates the scheduled task without admin
- [ ] `localmcptools install --method startup-folder` creates the .lnk
- [ ] `localmcptools uninstall` removes the task
- [ ] README + agent docs cover codebuddy, Copilot, workbuddy, minimax code
- [ ] codebuddy + Copilot can use the same server concurrently
- [ ] Audit log correctly attributes concurrent calls per agent
- [ ] No new capability profiles introduced
- [ ] No new approval flow introduced
- [ ] Boot autostart works across a real Windows reboot