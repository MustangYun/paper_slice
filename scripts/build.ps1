# Build the paperslice Docker image on Windows (PowerShell).
#
# Usage:
#   .\scripts\build.ps1                 # CPU image, no corporate CA
#   .\scripts\build.ps1 -CorpCa         # inject certs from .\certs\
#   .\scripts\build.ps1 -Gpu            # build the GPU variant
#   .\scripts\build.ps1 -Tag paperslice:dev
[CmdletBinding()]
param(
    [switch]$CorpCa,
    [switch]$Gpu,
    [string]$Tag = "paperslice:latest",
    [string]$Platform = ""
)

$ErrorActionPreference = "Stop"

$dockerfile = "Dockerfile"
if ($Gpu) {
    $dockerfile = "Dockerfile.gpu"
    if ($Tag -eq "paperslice:latest") { $Tag = "paperslice:gpu" }
}

$withCa = if ($CorpCa) { "1" } else { "0" }

$args = @("build", "-f", $dockerfile, "-t", $Tag)
if ($Platform) { $args += @("--platform", $Platform) }
$args += @("--build-arg", "WITH_CORP_CA=$withCa", ".")

Write-Host "[build.ps1] docker $($args -join ' ')"
docker @args
if ($LASTEXITCODE -ne 0) { throw "docker build failed ($LASTEXITCODE)" }

Write-Host "[build.ps1] Built image: $Tag"
