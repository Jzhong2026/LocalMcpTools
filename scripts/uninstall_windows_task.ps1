# uninstall_windows_task.ps1
#
# Removes the user-level scheduled task created by
# install_windows_task.ps1 *and* the Startup-folder shortcut created
# by install_startup_folder.ps1. Idempotent: missing entries are
# silently skipped. Never touches %APPDATA%\LocalMcpTools\.
#
# Supports -WhatIf / -Confirm.
#
# Parameters:
#   -TaskName : scheduled task / .lnk name to remove
#               (default from Get-DefaultTaskName)

[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    # NOTE: TaskName default below MUST stay in sync with DefaultTaskName
    # in scripts/_lib.ps1 — PowerShell evaluates param defaults before
    # dot-sourcing, so we can't reference the script-scoped constant here.
    [string]$TaskName = 'LocalMcpTools'
)

. (Join-Path $PSScriptRoot '_lib.ps1')

Assert-Windows

# --- scheduled task -----------------------------------------------------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    try {
        if ($PSCmdlet.ShouldProcess("scheduled task '$TaskName'", "unregister")) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        Write-Log "scheduled task '$TaskName' removed."
    }
    catch {
        Write-Log "Could not remove scheduled task '$TaskName': $_" -Level warn
    }
}
else {
    Write-Log "no scheduled task '$TaskName' to remove."
}

# --- startup folder shortcut -------------------------------------------
$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup "$TaskName.lnk"
if (Test-Path $lnkPath) {
    if ($PSCmdlet.ShouldProcess($lnkPath, "remove shortcut")) {
        Remove-Item -Path $lnkPath -Force
    }
    Write-Log "Startup-folder shortcut removed."
}
else {
    Write-Log "no Startup-folder shortcut to remove."
}

Write-Log "uninstall complete. Data under %APPDATA%\LocalMcpTools\ was preserved."
exit $script:EXIT_OK
