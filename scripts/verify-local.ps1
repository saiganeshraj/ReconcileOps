$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = Join-Path $PWD '.venv\Scripts\python.exe'
$ruff = Join-Path $PWD '.venv\Scripts\ruff.exe'
if (-not (Test-Path $python)) { throw 'Create the Python 3.12 project virtual environment first; see README.md.' }
& $ruff check src tests scripts alembic
if ($LASTEXITCODE -ne 0) { throw 'Ruff check failed.' }
& $ruff format --check src tests scripts alembic
if ($LASTEXITCODE -ne 0) { throw 'Ruff format check failed.' }
& $python -m pytest tests/unit tests/integration -q
if ($LASTEXITCODE -ne 0) { throw 'Core tests failed.' }
if ($env:TEST_DATABASE_URL) {
    & $python -m pytest tests/postgres -q
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL tests failed.' }
} else {
    Write-Host 'PostgreSQL tests NOT RUN: set TEST_DATABASE_URL to an isolated PostgreSQL database.'
}
