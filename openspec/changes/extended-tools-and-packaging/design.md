# Design: extended-tools-and-packaging

## Folder layout (additions)

```text
src/localmcptools/tools/
├── runtime.py                # runtime.detect_runtime / get_env / list_path
├── vscode.py                 # vscode.get_problems / get_installed_extensions /
│                             # get_logs / get_debug_sessions
└── diagnostics.py            # diagnostics.collect / explain_failure

src/localmcptools/
├── diagnostics/              # new (classifiers + heuristics)
│   ├── __init__.py
│   ├── classify.py           # map run -> classification
│   ├── aggregate.py          # diagnostics.collect
│   └── next_actions.py       # advice per classification
└── cli.py                    # add install / uninstall subcommands

scripts/
├── install_windows_task.ps1
├── uninstall_windows_task.ps1
└── README.md

README.md
docs/agent-configuration.md
```

## Diagnostics classifier

```python
# diagnostics/classify.py
def classify(run: AuditRow, log: list[str] | None) -> Classification:
    if run.status == "timed_out": return Classification.TIMEOUT
    if run.error_code == "denied_by_rule": return Classification.DENIED_BY_RULE
    if run.error_code == "approval_required": return Classification.APPROVAL_REQUIRED
    if run.error_code == "verification_failed": return Classification.VERIFICATION_FAILED
    if run.exit_code and run.exit_code != 0: return Classification.EXIT_CODE
    if run.ok: return Classification.SUCCESS
    return Classification.UNKNOWN
```

`next_actions` is per-classification:

```python
TIMEOUT: ["raise timeout_ms", "split into smaller commands", "use process.start_dev_server for long work"]
DENIED_BY_RULE: ["review rule {rule_id}", "request human approval at UI"]
EXIT_CODE: ["open file:line from key_evidence", "re-run with --verbose"]
```

## `vscode.get_problems`

VS Code exposes problems via the `vscode-test-content` or the
`vscode.commands.getCommands` API in some agent SDKs. For this change we
use the **CLI fallback**: read `%APPDATA%\Code\User\workspaceStorage\...` -
style files only if available; otherwise we shell out to
`code --status` (deprecated) — and if neither works, return
`vscode_not_running`.

A pragmatic approach: try VS Code's stdin/stdout JSON-RPC if a connection
is detectable; otherwise fall back to reading the workspace's stored
Problems file under `%APPDATA%\Code\User\workspaceStorage\<id>\state.vscdb`
(SQLite, single read-only connection).

## `localmcptools install` PowerShell script

```powershell
# scripts/install_windows_task.ps1 (sketch)
$TaskName = "LocalMcpTools"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path

$Action = New-ScheduledTaskAction `
    -Execute "python" `
    -Argument "-m localmcptools start" `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "LocalMcpTools — local MCP server for VS Code agents"
```

## Config additions

```jsonc
{
  "diagnostics": {
    "explain_failure_max_lines": 200,
    "recent_failure_window": 5
  },
  "windows_task": {
    "enabled": false,
    "task_name": "LocalMcpTools",
    "install_method": "scheduled_task"   // or "startup_folder"
  }
}
```

## New dependencies

None new — `psutil`, `python-dotenv`, etc. are already in `requirements.txt`.

## Out-of-scope reminders

- No PyInstaller / Nuitka single-file build in this change.
- No Linux/macOS autostart.
- No cross-agent verification actually runs workbuddy / minimax code
  (they are not yet available).