# install_windows_task.ps1
#
# Registers a *user-level* (no admin required) Windows Scheduled Task
# that boots the LocalMcpTools stdio server at user logon.
#
# Idempotent: if the task already exists it is replaced.
#
# Parameters (all optional; sensible defaults match the developer setup):
#   -ProjectRoot       : path to the repo checkout (default: parent of script)
#   -PythonExecutable  : python interpreter to use (default: python on PATH)
#   -TaskName          : scheduled task name (default: LocalMcpTools)

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$PythonExecutable = "python",
    [string]$TaskName = "LocalMcpTools"
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

# Build the action: `python -m localmcptools start` from the project root.
$actionArgs = "-m localmcptools start"
$Action = New-ScheduledTaskAction `
    -Execute $PythonExecutable `
    -Argument $actionArgs `
    -WorkingDirectory $ProjectRoot

# Trigger: at user logon.
$Trigger = New-ScheduledTaskTrigger -AtLogOn

# Settings: battery-friendly + retry on transient failure.
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

Write-Host "[localmcptools] scheduled task '$TaskName' is now active."
Write-Host "  Trigger:  AtLogOn"
Write-Host "  Action:   $PythonExecutable $actionArgs"
Write-Host "  CWD:      $ProjectRoot"
Write-Host ""
Write-Host "Test it manually with:  schtasks /Run /TN $TaskName"
exit 0