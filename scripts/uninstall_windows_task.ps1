# uninstall_windows_task.ps1
#
# Removes the user-level scheduled task created by
# install_windows_task.ps1 *and* the Startup-folder shortcut created
# by install_startup_folder.ps1. Idempotent: missing entries are
# silently skipped. Never touches %APPDATA%\LocalMcpTools\.

[CmdletBinding()]
param(
    [string]$TaskName = "LocalMcpTools"
)

$ErrorActionPreference = 'Continue'

if ($env:OS -ne "Windows_NT") {
    Write-Error "uninstall_windows_task.ps1 only runs on Windows."
    exit 2
}

# --- scheduled task -----------------------------------------------------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[localmcptools] scheduled task '$TaskName' removed."
    }
    catch {
        Write-Warning "Could not remove scheduled task '$TaskName': $_"
    }
}
else {
    Write-Host "[localmcptools] no scheduled task '$TaskName' to remove."
}

# --- startup folder shortcut -------------------------------------------
$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup "$TaskName.lnk"
if (Test-Path $lnkPath) {
    Remove-Item -Path $lnkPath -Force
    Write-Host "[localmcptools] Startup-folder shortcut removed."
}
else {
    Write-Host "[localmcptools] no Startup-folder shortcut to remove."
}

Write-Host "[localmcptools] uninstall complete. Data under %APPDATA%\LocalMcpTools\ was preserved."
exit 0