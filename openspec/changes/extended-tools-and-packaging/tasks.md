# Tasks: extended-tools-and-packaging

> Phases: **6** (extended tools) + **7** (packaging) + **8** (cross-agent verify)

## 6.1 `runtime.detect_runtime` / `get_env` / `list_path`

- [x] `tools/runtime.py`: enumerate python via `python --version` per PATH entry
- [x] Same for node (`node --version`), npm (`npm --version`), dotnet (`dotnet --version`)
- [x] Cache results for 60s
- [x] `get_env` redacts values per `safety.redact`
- [x] `list_path` checks `os.path.isdir` and `os.path.isfile`
- [x] Unit test: fixture PATH entries → correct is_default detection
- [x] Integration test: real system → python detected

## 6.2 `vscode.*` tools

- [x] `tools/vscode.py`: `get_problems` reads workspaceStorage state.vscdb
  (read-only) and parses known shapes
- [x] `get_installed_extensions`: read `extensions.json` from `%APPDATA%\Code\User\`
- [x] `get_logs(channel)`: tail the channel's log file
- [x] `get_debug_sessions`: read `debug.sessions` from VS Code state
- [x] Each tool returns `vscode_not_running` if state files don't exist
- [x] Unit test: missing files → `vscode_not_running`
- [ ] Integration test: open fixture VS Code workspace → problems list non-empty

## 6.3 `diagnostics.collect` / `explain_failure`

- [x] `diagnostics/aggregate.py`: one call fans out to all section tools
- [x] `diagnostics/aggregate.py`: cap each section to 64KB inline; spill to
  artifact handles when over
- [x] `diagnostics/classify.py`: classification enum + per-class logic
- [x] `diagnostics/next_actions.py`: advice strings per classification
- [x] `tools/diagnostics.py`: `collect` and `explain_failure`
- [x] Unit test: classifier matrix
- [ ] Integration test: a failed `workspace.run_test` → `explain_failure`
  returns `exit_code` classification with key_evidence lines

## 7.1 `localmcptools install` / `uninstall`

- [x] `cli.py`: add `install` and `uninstall` subcommands
- [x] `scripts/install_windows_task.ps1` registered task per design.md
- [x] `scripts/uninstall_windows_task.ps1` removes task
- [x] Idempotent install
- [x] `--method startup-folder` fallback places `.lnk`
- [ ] Smoke test on Windows VM: install → reboot → server is running

## 7.2 Rule management UI

- [ ] `ui/src/app/features/rules/`: full edit form (not just enable/disable)
- [ ] Validate JSON before save
- [ ] "Test match" button: take a sample command + show which rule matches
- [ ] "Reload from disk" button (already in change-5)

## 7.3 README + agent docs

- [x] Top-level `README.md`: per design.md scope
- [x] `docs/agent-configuration.md`: per agent, file location + snippet +
  quirks
- [x] Link from README

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

- [x] `docs/agent-configuration.md` includes workbuddy and minimax code
  sections marked "untested"
- [x] Based on each agent's published docs at the time of writing

## DoD (must all pass)

- [x] `runtime.detect_runtime` returns python / node / dotnet / npm paths + versions
- [x] `vscode.get_problems` reads VS Code state correctly
- [x] `diagnostics.collect` returns all sections + `next_actions`
- [x] `diagnostics.explain_failure` classifies correctly per the matrix
- [x] `localmcptools install` creates the scheduled task without admin
- [x] `localmcptools install --method startup-folder` creates the .lnk
- [x] `localmcptools uninstall` removes the task
- [x] README + agent docs cover codebuddy, Copilot, workbuddy, minimax code
- [ ] codebuddy + Copilot can use the same server concurrently *(deferred — requires HTTP shared mode from change-5)*
- [ ] Audit log correctly attributes concurrent calls per agent *(deferred — requires HTTP shared mode)*
- [x] No new capability profiles introduced
- [x] No new approval flow introduced
- [ ] Boot autostart works across a real Windows reboot *(needs a real reboot on the user's machine)*