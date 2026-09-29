# Windows equivalent of the Makefile. Usage: ./tasks.ps1 <target>
param([Parameter(Position = 0)][string]$Target = "help")
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root "backend/.venv/Scripts/python.exe"

function Invoke-Checked([scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
function In-Dir([string]$Dir, [scriptblock]$Block) {
    Push-Location (Join-Path $Root $Dir)
    try { Invoke-Checked $Block } finally { Pop-Location }
}

switch ($Target) {
    "setup" {
        Invoke-Checked { python -m venv (Join-Path $Root "backend/.venv") }
        Invoke-Checked { & $Py -m pip install -e "$Root/backend[dev]" }
        In-Dir "frontend" { npm ci }
    }
    "up"           { Invoke-Checked { docker compose up -d --build } }
    "down"         { Invoke-Checked { docker compose down } }
    "logs"         { docker compose logs -f backend }
    "migrate"      { Invoke-Checked { docker compose run --rm backend alembic upgrade head } }
    "migrate-down" { Invoke-Checked { docker compose run --rm backend alembic downgrade -1 } }
    "ingest"       { Write-Error "Ingestion is implemented in Phase 2 (python -m app.scripts.ingest)." }
    "eval"         { Write-Error "Evaluation is implemented in Phase 7 (python -m app.scripts.evaluate)." }
    "test" {
        In-Dir "backend" { & $Py -m pytest }
        In-Dir "frontend" { npm test }
    }
    "lint" {
        In-Dir "backend" { & $Py -m ruff check . }
        In-Dir "backend" { & $Py -m ruff format --check . }
        In-Dir "backend" { & $Py -m mypy app }
        In-Dir "frontend" { npm run typecheck }
        In-Dir "frontend" { npm run lint }
    }
    "audit" {
        In-Dir "backend" { & $Py -m pip_audit }
        In-Dir "frontend" { npm audit --omit=dev }
    }
    default { "Targets: setup up down logs migrate migrate-down ingest eval test lint audit" }
}
