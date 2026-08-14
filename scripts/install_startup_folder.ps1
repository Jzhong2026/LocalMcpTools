# install_startup_folder.ps1
#
# Fallback installer: places a .lnk in the current user's Startup
# folder so the server boots at logon without needing the Windows
# Task Scheduler service. Use this when scheduled-task creation fails
# (e.g. on a corporate image where the user lacks TaskScheduler
# privileges).
#
# Parameters (all optional):
#   -ProjectRoot       : path to the repo checkout (default: parent of script)
#   -PythonExecutable  : python interpreter to use (default: python on PATH)
#   -TaskName          : base name of the .lnk (default: LocalMcpTools)
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

if ($env:OS -ne "Windows_NT") {
    Write-Error "install_startup_folder.ps1 only runs on Windows."
    exit 2
}

$startup = [Environment]::GetFolderPath('Startup')
if (-not $startup) {
    Write-Error "Could not resolve the Startup folder path."
    exit 3
}

# .lnk filename MUST stay in sync with uninstall_windows_task.ps1
# which removes "$TaskName.lnk". If you change one, change the other.
$lnkPath = Join-Path $startup "$TaskName.lnk"

# Build the command line. We use cmd.exe to launch python with the
# project root as CWD, mirroring the scheduled-task action. The
# shortcut's WorkingDirectory is also set to $ProjectRoot so the
# /d cd in cmd is belt + braces (handles double-click from elsewhere).
if ($HttpMode) {
    $startArgs = "start --http --port $HttpPort"
    $modeDesc = "http://127.0.0.1:$HttpPort"
}
else {
    $startArgs = "start"
    $modeDesc = "stdio"
}
$cmdLine = "/c cd /d `"$ProjectRoot`" && $PythonExecutable -m localmcptools $startArgs"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $env:ComSpec
$shortcut.Arguments = $cmdLine
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.WindowStyle = 7  # minimized — silent logon launch
# Index 12 of shell32.dll is a generic folder icon; intentionally
# non-branded so this .lnk doesn't masquerade as a real installer.
$shortcut.IconLocation = "$env:windir\system32\shell32.dll,12"
$shortcut.Description = "LocalMcpTools - local MCP server for VS Code agents ($modeDesc)"
$shortcut.Save()

# Verify the .lnk actually landed. CreateShortcut.Save() can return
# without raising on a permission failure.
if (-not (Test-Path $lnkPath)) {
    Write-Error "Shortcut Save() reported success but '$lnkPath' does not exist."
    exit 4
}

Write-Host "[localmcptools] Startup-folder shortcut installed at:"
Write-Host "  $lnkPath"
Write-Host "  Mode: $modeDesc"
exit 0
