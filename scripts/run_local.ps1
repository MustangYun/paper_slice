# Run paperslice locally on Windows (PowerShell).
#
# Usage:
#   .\scripts\run_local.ps1
#   .\scripts\run_local.ps1 -Port 9000
#   .\scripts\run_local.ps1 -Tag paperslice:gpu -Extra "--gpus","all"
[CmdletBinding()]
param(
    [string]$Tag = "paperslice:latest",
    [int]$Port = 8000,
    [string]$OutputDir = "",
    [string[]]$Extra = @()
)

$ErrorActionPreference = "Stop"

if (-not $OutputDir) { $OutputDir = Join-Path (Get-Location) "output" }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# Docker Desktop on Windows accepts forward slashes in volume mounts.
$outputMount = ($OutputDir -replace '\\', '/')

Write-Host "[run_local.ps1] Image : $Tag"
Write-Host "[run_local.ps1] Port  : $Port -> 8000"
Write-Host "[run_local.ps1] Output: $outputMount -> /app/output"

$args = @("run", "--rm",
          "-p", "$($Port):8000",
          "-v", "$($outputMount):/app/output") + $Extra + @($Tag)

docker @args
if ($LASTEXITCODE -ne 0) { throw "docker run failed ($LASTEXITCODE)" }
