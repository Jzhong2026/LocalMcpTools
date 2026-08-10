# apply-mcp-config.ps1
# Re-applies the LocalMcpTools entry in the user's minimax-code mcp.json
# with the current server.json bearer token. Run after every server restart
# (or whenever server.json's csrf_token changes).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\apply-mcp-config.ps1
#
# What it touches:
#   - $env:APPDATA\LocalMcpTools\server.json (read — current token + port)
#   - $env:APPDATA\Code\User\mcp.json (write — localmcptools entry)

$ErrorActionPreference = "Stop"

$serverJsonPath = Join-Path $env:APPDATA "LocalMcpTools\server.json"
$mcpJsonPath    = Join-Path $env:APPDATA "Code\User\mcp.json"

if (-not (Test-Path $serverJsonPath)) {
    Write-Error "server.json not found at $serverJsonPath — is the HTTP server running?"
}
$server = Get-Content $serverJsonPath -Raw | ConvertFrom-Json
$token  = $server.csrf_token
$port   = $server.port
Write-Host "LocalMcpTools server: port=$port token=${token.Substring(0,8)}..."

if (-not (Test-Path $mcpJsonPath)) {
    New-Item -ItemType File -Path $mcpJsonPath -Force | Out-Null
    # Initialize a fresh config with the empty servers object
    @{ servers = @{}; inputs = @() } | ConvertTo-Json -Depth 10 |
        Set-Content $mcpJsonPath -Encoding utf8
}

# Backup before write
$backup = "$mcpJsonPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $mcpJsonPath $backup

$cfg = Get-Content $mcpJsonPath -Raw | ConvertFrom-Json

$entry = [ordered]@{
    type    = "http"
    url     = "http://127.0.0.1:$port/mcp/"
    headers = [ordered]@{
        Authorization = "Bearer $token"
    }
}

# Upsert the localmcptools entry, preserve any other servers (knowledge-vault etc.)
$cfg.servers | Add-Member -NotePropertyName "localmcptools" `
    -NotePropertyValue $entry -Force

$cfg | ConvertTo-Json -Depth 10 | Set-Content $mcpJsonPath -Encoding utf8

Write-Host "Updated $mcpJsonPath"
Write-Host "  (backup: $backup)"
Write-Host ""
Write-Host "minimax code will pick up the new server after a restart."
