$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    $docker = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
    if (-not (Test-Path $docker)) { throw 'Install Docker Desktop with Linux containers first.' }
} else { $docker = $docker.Source }
& $docker compose up --build -d --wait
if ($LASTEXITCODE -ne 0) { throw 'Docker startup failed. Check Docker Desktop and compose logs.' }
Write-Host 'ReconcileOps is ready at http://localhost:8000'
