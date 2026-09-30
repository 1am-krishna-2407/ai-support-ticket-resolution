# Windows equivalent of the Makefile. Usage: ./tasks.ps1 <target>
param(
    [Parameter(Position = 0)][string]$Target = "help",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest = @()
)
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
        Invoke-Checked { & $Py -m pip install --require-hashes -r "$Root/backend/requirements-dev.lock" }
        Invoke-Checked { & $Py -m pip install --no-deps -e "$Root/backend" }
        In-Dir "frontend" { npm ci }
    }
    "up"           { Invoke-Checked { docker compose up -d --build } }
    "down"         { Invoke-Checked { docker compose down } }
    "logs"         { docker compose logs -f backend }
    "migrate"      { Invoke-Checked { docker compose run --rm backend alembic upgrade head } }
    "migrate-down" { Invoke-Checked { docker compose run --rm backend alembic downgrade -1 } }
    "ingest"       { Invoke-Checked { docker compose run --rm backend python -m app.scripts.ingest @Rest } }
    "eval"         { Invoke-Checked { docker compose run --rm backend python -m app.scripts.evaluate @Rest } }
    "test" {
        In-Dir "backend" { & $Py -m pytest }
        In-Dir "frontend" { npm test }
    }
    "test-security" { In-Dir "backend" { & $Py -m pytest tests/security } }
    "openapi"      { In-Dir "backend" { & $Py -m app.scripts.export_openapi } }
    "lock" {
        In-Dir "backend" { uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes -o requirements.lock }
        In-Dir "backend" { uv pip compile pyproject.toml --extra dev --universal --python-version 3.12 --generate-hashes -o requirements-dev.lock }
    }
    "lint" {
        In-Dir "backend" { & $Py -m ruff check . }
        In-Dir "backend" { & $Py -m ruff format --check . }
        In-Dir "backend" { & $Py -m mypy app }
        In-Dir "frontend" { npm run typecheck }
        In-Dir "frontend" { npm run lint }
    }
    "audit" {
        In-Dir "backend" { & $Py -m pip_audit -r requirements-dev.lock --disable-pip --no-deps }
        In-Dir "frontend" { npm audit }
    }
    default { "Targets: setup up down logs migrate migrate-down ingest eval test test-security lint audit lock openapi" }
}
