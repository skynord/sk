# Starts the Sky backend and frontend together (Windows PowerShell).
# Backend: http://127.0.0.1:8000/api  |  Frontend: http://localhost:5173
#
# Usage:  .\run.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$backendPy = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $backendPy)) {
    Write-Error "Backend venv not found at $backendPy. See README 'Quickstart' to create it."
}

Write-Host "Initialising database (safe to re-run)..." -ForegroundColor Cyan
Push-Location (Join-Path $root "backend")
& $backendPy -m app.cli initdb

Write-Host "Starting API on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
$api = Start-Process -FilePath $backendPy `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000" `
    -PassThru -WorkingDirectory (Join-Path $root "backend")
Pop-Location

Write-Host "Waiting for the API to become ready ..." -ForegroundColor Cyan
$healthUrl = "http://127.0.0.1:8000/api/health"
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    if ($api.HasExited) {
        Write-Warning "The API process exited during startup; check the backend logs."
        break
    }
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($health.status -eq "ok") { $ready = $true; break }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if ($ready) {
    Write-Host "API is ready." -ForegroundColor Green
} else {
    Write-Warning "The API did not report ready in time; starting the frontend anyway."
}

$frontend = Join-Path $root "frontend"
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "Installing frontend dependencies (first run)..." -ForegroundColor Cyan
    Push-Location $frontend
    & npm.cmd install
    Pop-Location
}

Write-Host "Starting frontend on http://localhost:5173 ..." -ForegroundColor Cyan
$web = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" `
    -PassThru -WorkingDirectory $frontend

Write-Host ""
Write-Host "Sky is running." -ForegroundColor Green
Write-Host "  API:      http://127.0.0.1:8000/api/docs"
Write-Host "  Frontend: http://localhost:5173"
Write-Host "Press Ctrl+C to stop both." -ForegroundColor Yellow

try {
    Wait-Process -Id $api.Id, $web.Id
}
finally {
    foreach ($proc in @($api, $web)) {
        if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    }
}
