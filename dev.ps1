<#
.SYNOPSIS
    Starts the dispatcher backend (uvicorn, :8000) and frontend (vite, :5173)
    together from one script. Skips any service that's already running.

.USAGE
    ./dev.ps1
    (Ctrl+C stops both)
#>

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

function Test-PortInUse($port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

$procs = @()

if (Test-PortInUse 8000) {
    Write-Host "[backend]  already listening on :8000, skipping" -ForegroundColor Yellow
} else {
    Write-Host "[backend]  uv run uvicorn app.main:app --reload --port 8000" -ForegroundColor Cyan
    $procs += Start-Process -FilePath "uv" `
        -ArgumentList "run", "uvicorn", "app.main:app", "--reload", "--port", "8000" `
        -WorkingDirectory (Join-Path $root "backend") `
        -NoNewWindow -PassThru
}

if (Test-PortInUse 5173) {
    Write-Host "[frontend] already listening on :5173, skipping" -ForegroundColor Yellow
} else {
    Write-Host "[frontend] npm run dev" -ForegroundColor Cyan
    $procs += Start-Process -FilePath "npm.cmd" `
        -ArgumentList "run", "dev" `
        -WorkingDirectory (Join-Path $root "frontend") `
        -NoNewWindow -PassThru
}

if ($procs.Count -eq 0) {
    Write-Host "Both servers already running, nothing to do." -ForegroundColor Green
    exit 0
}

try {
    Write-Host "`nBoth up. Press Ctrl+C to stop.`n" -ForegroundColor Green
    Wait-Process -Id ($procs.Id)
} finally {
    Write-Host "`nStopping..." -ForegroundColor Yellow
    foreach ($p in $procs) {
        if (-not $p.HasExited) {
            # taskkill /T kills the whole process tree (uv->uvicorn, npm->vite/node)
            taskkill /PID $p.Id /T /F | Out-Null
        }
    }
}
