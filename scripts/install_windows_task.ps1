# install_windows_task.ps1
#
# Registers a *user-level* (no admin required) Windows Scheduled Task
# that boots the LocalMcpTools server at user logon.
#
# Idempotent: if the task already exists it is replaced.
#
# Parameters (all optional; sensible defaults match the developer setup):
#   -ProjectRoot       : path to the repo checkout (default: parent of script)
#   -PythonExecutable  : python interpreter to use (default: python on PATH)
#   -TaskName          : scheduled task name (default: LocalMcpTools)
#   -HttpMode          : boot the HTTP control plane + MCP endpoint (default)
#                        instead of stdio. Pass -HttpMode:$false for stdio.
#   -HttpPort          : bind port for -HttpMode (default: 7890)

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$PythonExecutable = "python",
    [string]$TaskName = "LocalMcpTools",
    [bool]$HttpMode = $true,
    [int]$HttpPort = 7890
)

$ErrorActionPreference = 'Stop'

# Defensive: refuse to run on a non-Windows host (the script is
# launched from Python's `localmcptools install` which already gates
# on os.name == "nt", but belt + braces.)
if ($env:OS -ne "Windows_NT") {
    Write-Error "install_windows_task.ps1 only runs on Windows."
    exit 2
}

Write-Host "[localmcptools] registering scheduled task '$TaskName'..."

if (-not (Test-Path $ProjectRoot)) {
    Write-Error "ProjectRoot not found: $ProjectRoot"
    exit 3
}

# Build the action: `python -m localmcptools start [--http --port N]` from
# the project root. HTTP mode is the default because it matches the
# apply-mcp-config.ps1 integration path.
if ($HttpMode) {
    $actionArgs = "-m localmcptools start --http --port $HttpPort"
    $modeDesc = "http://127.0.0.1:$HttpPort"
}
else {
    $actionArgs = "-m localmcptools start"
    $modeDesc = "stdio"
}

$Action = New-ScheduledTaskAction `
    -Execute $PythonExecutable `
    -Argument $actionArgs `
    -WorkingDirectory $ProjectRoot

# Trigger: at user logon.
$Trigger = New-ScheduledTaskTrigger -AtLogOn

# Settings: battery-friendly + retry on transient failure.
# ExecutionTimeLimit = 0 (New-TimeSpan -Minutes 0) means "unbounded" —
# the server is a long-lived process so we never want the task
# scheduler to kill it on a timer.
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 0) `
    -MultipleInstances IgnoreNew

# If the task already exists, replace it.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[localmcptools] removing existing task '$TaskName' before re-registering."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "LocalMcpTools - local MCP server for VS Code agents" `
        -RunLevel Limited | Out-Null
}
catch {
    Write-Error "Register-ScheduledTask failed: $_"
    exit 4
}

# Verify the registration actually landed (defensive — Register-ScheduledTask
# can swallow non-fatal errors).
$verify = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $verify) {
    Write-Error "Register-ScheduledTask reported success but task '$TaskName' is not visible to Get-ScheduledTask."
    exit 5
}

Write-Host "[localmcptools] scheduled task '$TaskName' is now active."
Write-Host "  Trigger:  AtLogOn"
Write-Host "  Action:   $PythonExecutable $actionArgs"
Write-Host "  CWD:      $ProjectRoot"
Write-Host "  Mode:     $modeDesc"
Write-Host ""
Write-Host "Test it manually with:  schtasks /Run /TN $TaskName"
exit 0
