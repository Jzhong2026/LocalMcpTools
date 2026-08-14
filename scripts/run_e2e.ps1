# Run the e2e suite with the right environment.
#
# Usage:
#   pwsh scripts/run_e2e.ps1                 # default e2e only
#   pwsh scripts/run_e2e.ps1 -IncludeUnit    # unit + e2e
#   pwsh scripts/run_e2e.ps1 -TestPath tests/e2e/test_00_boot_stdio.py
#   pwsh scripts/run_e2e.ps1 -Verbose        # verbose pytest
#
# Outputs JUnit XML to tests/e2e/_report/junit.xml so CI can ingest it.

[CmdletBinding()]
param(
    [switch]$IncludeUnit,
    [string]$TestPath = "tests/e2e",
    [string]$ReportDir = "tests/e2e/_report",
    [switch]$VerboseOutput
)

$ErrorActionPreference = "Stop"

# Activate venv if it exists
$venvActivate = Join-Path $PSScriptRoot "..\.venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    & $venvActivate
} else {
    Write-Warning "No .venv at $venvActivate — falling back to system Python"
}

$python = "python"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

Push-Location $repoRoot
try {
    New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null

    # IncludeUnit=True -> run everything (no -m filter).
    # IncludeUnit=False (default) -> -m e2e only.
    $pytestArgs = @(
        $TestPath,
        "--junitxml", (Join-Path $ReportDir "junit.xml"),
        "--tb=short"
    )
    if (-not $IncludeUnit) {
        $pytestArgs += @("-m", "e2e")
    }
    if ($VerboseOutput) {
        $pytestArgs += "-vv"
    }

    Write-Host "[run_e2e] pytest $pytestArgs" -ForegroundColor Cyan
    & $python -m pytest @pytestArgs
    $exitCode = $LASTEXITCODE

    if ($exitCode -eq 0) {
        Write-Host "[run_e2e] all green — report at $junitPath" -ForegroundColor Green
    } else {
        Write-Host "[run_e2e] failed (exit $exitCode) — report at $junitPath" -ForegroundColor Red
    }
    exit $exitCode
} finally {
    Pop-Location
}