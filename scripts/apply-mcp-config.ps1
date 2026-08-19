# apply-mcp-config.ps1
# Re-applies the LocalMcpTools entry in the user's VS Code mcp.json using
# the verified stdio transport.
#
# Historical note: this script used to write an HTTP+bearer entry pointing at
# http://127.0.0.1:<port>/mcp/. VS Code's MCP client treats every HTTP server
# as an OAuth 2.0 authorization endpoint and pops up "Dynamic Client
# Registration" before it ever honors the bearer header. As a workaround
# (and because the OpenSpec docs mark stdio as the verified Copilot path),
# the entry is now stdio-only.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\apply-mcp-config.ps1
#
# What it touches:
#   - %APPDATA%\Code\User\mcp.json  (write — localmcptools stdio entry)
#
# What it does NOT touch:
#   - %APPDATA%\LocalMcpTools\server.json. Stdio mode does not consume that
#     file; HTTP mode does. If you ever want the HTTP-mode bearer wiring back,
#     see docs/agent-configuration.md (Concurrent shared server section).

$ErrorActionPreference = "Stop"

$pythonExe   = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'))
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$mcpJsonPath = Join-Path $env:APPDATA 'Code\User\mcp.json'

if (-not (Test-Path $pythonExe)) {
    Write-Error "venv python not found at $pythonExe - run 'pip install -e .' first."
}
if (-not (Test-Path $projectRoot)) {
    Write-Error "project root not found at $projectRoot"
}

Write-Host "LocalMcpTools stdio entry"
Write-Host "  python = $pythonExe"
Write-Host "  cwd    = $projectRoot"

if (-not (Test-Path $mcpJsonPath)) {
    New-Item -ItemType File -Path $mcpJsonPath -Force | Out-Null
    @{ servers = @{}; inputs = @() } | ConvertTo-Json -Depth 10 |
        Set-Content $mcpJsonPath -Encoding utf8
}

# Backup before write
$backup = "$mcpJsonPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $mcpJsonPath $backup

$cfg = Get-Content $mcpJsonPath -Raw | ConvertFrom-Json

$entry = [ordered]@{
    type    = "stdio"
    command = $pythonExe
    args    = @("-m", "localmcptools", "start")
    cwd     = $projectRoot
}

# Upsert the localmcptools entry, preserve any other servers (knowledge-vault etc.)
$cfg.servers | Add-Member -NotePropertyName "localmcptools" `
    -NotePropertyValue $entry -Force

$cfg | ConvertTo-Json -Depth 10 | Set-Content $mcpJsonPath -Encoding utf8

Write-Host "Updated $mcpJsonPath"
Write-Host "  (backup: $backup)"
Write-Host ""
Write-Host "Restart VS Code (or its MCP host) so it picks up the new entry."
