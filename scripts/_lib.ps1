# scripts/_lib.ps1
#
# Shared helpers for the install / uninstall / config scripts.
# Dot-source from each script with:
#
#   . (Join-Path $PSScriptRoot '_lib.ps1')
#
# Provides:
#   * Assert-Windows                  — early exit on non-Windows hosts.
#   * Write-Log                        — uniform [localmcptools] prefix.
#   * Get-DefaultTaskName              — single source of truth for the
#                                       scheduled-task / .lnk name.
#   * Exit codes (as script-scoped
#     constants EXIT_*)                — see comments for semantics.
#   * $Script:ModeHttp / $Script:Port
#     default values for -HttpMode /
#     -HttpPort in install scripts.
#
# Exit code contract
# ------------------
#   0   OK
#   1   Unused (reserved for "user cancelled"; not currently emitted)
#   2   Wrong platform / environment (e.g. not Windows_NT, no Startup dir)
#   3   Bad input / missing path (e.g. ProjectRoot not found)
#   4   Install verification failed (e.g. Register-ScheduledTask OK but
#       Get-ScheduledTask can't see the task; or .lnk Save() didn't land)
#   5   Register-ScheduledTask itself threw (caught and re-raised)
#   6   -HttpMode was requested but the bound port is already in use
#       (only install_windows_task.ps1 emits this)
#
# Scripts that import this module MUST NOT redefine these constants.

$ErrorActionPreference = 'Stop'

# Exit codes — see header comment for the contract.
$script:EXIT_OK = 0
$script:EXIT_USER_CANCELLED = 1
$script:EXIT_BAD_ENV = 2
$script:EXIT_BAD_INPUT = 3
$script:EXIT_VERIFY_FAILED = 4
$script:EXIT_REGISTER_FAILED = 5
$script:EXIT_PORT_IN_USE = 6

# Defaults for the install scripts. Kept here so a future change to
# the auto-start policy is a one-line edit.
$script:DefaultTaskName = 'LocalMcpTools'
$script:DefaultHttpMode = $true
$script:DefaultHttpPort = 7890

function Assert-Windows {
    <#
    .SYNOPSIS
        Refuse to run on a non-Windows host.
    .DESCRIPTION
        install_windows_task.ps1 and the .lnk installer both call into
        Win32-only APIs (WScript.Shell, Register-ScheduledTask). We
        guard here so the PowerShell parser still loads the script on
        macOS / Linux for syntax checks, but execution fails fast.
    #>
    if ($env:OS -ne 'Windows_NT') {
        Write-Error "$($MyInvocation.ScriptName) only runs on Windows."
        exit $script:EXIT_BAD_ENV
    }
}

function Get-DefaultTaskName {
    <#
    .SYNOPSIS
        Return the canonical task / .lnk name.
    .DESCRIPTION
        The same string is used by both install paths (scheduled task
        and Startup-folder shortcut) and by uninstall_windows_task.ps1
        to find what to remove. Keep them in sync via this single getter.
    #>
    return $script:DefaultTaskName
}

function Write-Log {
    <#
    .SYNOPSIS
        Print a uniform "[localmcptools] <message>" log line.
    .PARAMETER Message
        Free-form text. No newline needed.
    .PARAMETER Level
        'info' (default), 'warn', or 'error'. Error calls Write-Error
        which under $ErrorActionPreference='Stop' aborts the caller.
    #>
    param(
        [Parameter(Mandatory)] [string]$Message,
        [ValidateSet('info', 'warn', 'error')] [string]$Level = 'info'
    )
    $line = "[localmcptools] $Message"
    if ($Level -eq 'warn') {
        Write-Warning $line
    }
    elseif ($Level -eq 'error') {
        Write-Error $line
    }
    else {
        Write-Host $line
    }
}
