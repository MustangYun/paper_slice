# Run paperslice locally on Windows (PowerShell).
#
# Usage:
#   .\scripts\run_local.ps1                                  # 기본 포트 8100
#   .\scripts\run_local.ps1 -Port 9000                       # 포트 변경
#   .\scripts\run_local.ps1 -Port 8000                       # v8 구버전 호환 (호스트만 8000)
#   .\scripts\run_local.ps1 -Tag paperslice:gpu -Extra "--gpus","all"
[CmdletBinding()]
param(
    [string]$Tag = "paperslice:latest",
    # v9: 컨테이너 내부 포트 8000 → 8100 (이슈 #2). 호스트 측은 -Port 로 override.
    [int]$Port = 8100,
    [string]$OutputDir = "",
    [string[]]$Extra = @()
)

$ErrorActionPreference = "Stop"

if (-not $OutputDir) { $OutputDir = Join-Path (Get-Location) "output" }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# Docker Desktop on Windows accepts forward slashes in volume mounts.
$outputMount = ($OutputDir -replace '\\', '/')

Write-Host "[run_local.ps1] Image : $Tag"
Write-Host "[run_local.ps1] Port  : $Port -> 8100"
Write-Host "[run_local.ps1] Output: $outputMount -> /app/output"

$args = @("run", "--rm",
          "-p", "$($Port):8100",
          "-v", "$($outputMount):/app/output") + $Extra + @($Tag)

docker @args
if ($LASTEXITCODE -ne 0) { throw "docker run failed ($LASTEXITCODE)" }
