# install_startup_folder.ps1
#
# Fallback installer: places a .lnk in the current user's Startup
# folder so the server boots at logon without needing the Windows
# Task Scheduler service. Use this when scheduled-task creation fails
# (e.g. on a corporate image where the user lacks TaskScheduler
# privileges).

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$PythonExecutable = "python",
    [string]$TaskName = "LocalMcpTools"
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

$lnkPath = Join-Path $startup "$TaskName.lnk"

# Build the command line. We use cmd.exe to launch python with the
# project root as CWD, mirroring the scheduled-task action.
$cmdLine = "/c cd /d `"$ProjectRoot`" && $PythonExecutable -m localmcptools start"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $env:ComSpec
$shortcut.Arguments = $cmdLine
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.WindowStyle = 7  # minimized
$shortcut.IconLocation = "$env:windir\system32\shell32.dll,12"
$shortcut.Description = "LocalMcpTools - local MCP server for VS Code agents"
$shortcut.Save()

Write-Host "[localmcptools] Startup-folder shortcut installed at:"
Write-Host "  $lnkPath"
exit 0