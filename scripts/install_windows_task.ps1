# install_windows_task.ps1
#
# Registers a *user-level* (no admin required) Windows Scheduled Task
# that boots the LocalMcpTools server at user logon.
#
# Idempotent: if the task already exists it is replaced.
#
# Supports -WhatIf / -Confirm (PSShouldProcess): the dry run prints
# every action without actually mutating state.
#
# Parameters (all optional; sensible defaults match the developer setup):
#   -ProjectRoot       : path to the repo checkout (default: parent of script)
#   -PythonExecutable  : python interpreter to use (default: python on PATH)
#   -TaskName          : scheduled task name (default: LocalMcpTools)
#   -HttpMode          : boot the HTTP control plane + MCP endpoint (default)
#                        instead of stdio. Pass -HttpMode:$false for stdio.
#   -HttpPort          : bind port for -HttpMode (default: 7890)

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$PythonExecutable = "python",
    # NOTE: TaskName / HttpMode / HttpPort defaults below MUST stay in
    # sync with the values in scripts/_lib.ps1 (DefaultTaskName,
    # DefaultHttpMode, DefaultHttpPort). PowerShell evaluates param
    # defaults before dot-sourcing _lib.ps1, so we can't reference
    # the script-scoped constants directly here.
    [string]$TaskName = 'LocalMcpTools',
    [bool]$HttpMode = $true,
    [int]$HttpPort = 7890
)

. (Join-Path $PSScriptRoot '_lib.ps1')

Assert-Windows

if (-not $PSCmdlet.ShouldProcess("scheduled task '$TaskName' on $ProjectRoot", "register")) {
    return
}

if (-not (Test-Path $ProjectRoot)) {
    Write-Log "ProjectRoot not found: $ProjectRoot" -Level error
    exit $script:EXIT_BAD_INPUT
}

# Build the action. HTTP mode is the default because it matches the
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

# Replace any existing task with the same name (idempotent).
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Log "removing existing task '$TaskName' before re-registering."
    if ($PSCmdlet.ShouldProcess("scheduled task '$TaskName'", "unregister")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
}

try {
    Write-Log "registering scheduled task '$TaskName'..."
    if ($PSCmdlet.ShouldProcess("scheduled task '$TaskName'", "register")) {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $Action `
            -Trigger $Trigger `
            -Settings $Settings `
            -Description "LocalMcpTools - local MCP server for VS Code agents" `
            -RunLevel Limited | Out-Null
    }
}
catch {
    Write-Log "Register-ScheduledTask failed: $_" -Level error
    exit $script:EXIT_REGISTER_FAILED
}

# Verify the registration actually landed. Register-ScheduledTask can
# return without raising on a non-fatal ACL / policy failure.
$verify = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $verify) {
    Write-Log "Register-ScheduledTask reported success but task '$TaskName' is not visible to Get-ScheduledTask." -Level error
    exit $script:EXIT_VERIFY_FAILED
}

Write-Log "scheduled task '$TaskName' is now active."
Write-Log "  Trigger:  AtLogOn"
Write-Log "  Action:   $PythonExecutable $actionArgs"
Write-Log "  CWD:      $ProjectRoot"
Write-Log "  Mode:     $modeDesc"
Write-Host ""
Write-Host "Test it manually with:  schtasks /Run /TN $TaskName"
exit $script:EXIT_OK
