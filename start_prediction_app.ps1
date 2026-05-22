#Requires -Version 5.1
<#
.SYNOPSIS
    Start prediction_app and expose it via ngrok for mobile access
.DESCRIPTION
    1. Launch prediction_app with uvicorn on port 8001
    2. Create an ngrok HTTP tunnel
    3. Display the public URL in the console
    Press Ctrl+C to stop both uvicorn and ngrok.
.NOTES
    ngrok must be authenticated: run "ngrok authtoken <TOKEN>" first.
    Get your token at https://dashboard.ngrok.com/
    Set ANTHROPIC_API_KEY to enable AI analysis.
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8001

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  prediction_app + ngrok launcher" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# -- PostgreSQL password ------------------------------------------------------
$pgPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD', 'User')
if ([string]::IsNullOrEmpty($pgPassword)) { $pgPassword = $env:PGPASSWORD }
if ([string]::IsNullOrEmpty($pgPassword)) {
    Write-Host "[ERROR] PGPASSWORD is not set." -ForegroundColor Red
    Write-Host "        Run register_weekly_friday_task.ps1 to configure it."
    exit 1
}

$env:POSTGRES_HOST     = "localhost"
$env:POSTGRES_PORT     = "5432"
$env:POSTGRES_DATABASE = "keiba"
$env:POSTGRES_USER     = "postgres"
$env:POSTGRES_PASSWORD = $pgPassword
$env:PYTHONIOENCODING  = "utf-8"

Set-Location $root

# -- ngrok availability check -------------------------------------------------
$ngrokCmd = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrokCmd) {
    Write-Host "[ERROR] ngrok not found. Install with: winget install ngrok" -ForegroundColor Red
    exit 1
}

$ngrokCfg = "$env:USERPROFILE\AppData\Local\ngrok\ngrok.yml"
if (-not (Test-Path $ngrokCfg) -or -not (Select-String "authtoken" $ngrokCfg -Quiet)) {
    Write-Host "[ERROR] ngrok authtoken is not configured." -ForegroundColor Red
    Write-Host "        Get a token at https://dashboard.ngrok.com/ and run:"
    Write-Host "        ngrok authtoken <YOUR_TOKEN>"
    exit 1
}

# -- Kill any process already listening on the port --------------------------
$occupied = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($occupied) {
    Write-Host "[INFO] Port $port is in use. Stopping existing process..." -ForegroundColor Yellow
    $pid_ = ($occupied | Select-Object -First 1).OwningProcess
    $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "       Stopping PID $pid_ ($($proc.ProcessName))"
        Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
        Start-Sleep 1
    }
}

# -- Start uvicorn ------------------------------------------------------------
Write-Host "[1/3] Starting uvicorn on port $port ..." -ForegroundColor Yellow

$uvicornArgs = @(
    "-3.12-32", "-m", "uvicorn",
    "prediction_app.main:app",
    "--host", "127.0.0.1",
    "--port", "$port"
)
$uvicornProc = Start-Process "py" -ArgumentList $uvicornArgs -PassThru -WindowStyle Minimized

# Wait up to 20 seconds for uvicorn to respond
$uvicornReady = $false
for ($i = 1; $i -le 20; $i++) {
    Start-Sleep 1
    try {
        $null = Invoke-WebRequest "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
        $uvicornReady = $true
        break
    } catch { }
}

if ($uvicornReady) {
    Write-Host "  uvicorn ready (PID: $($uvicornProc.Id))" -ForegroundColor Green
} else {
    Write-Host "  [WARN] uvicorn health check timed out -- continuing anyway" -ForegroundColor Yellow
}

# -- Start ngrok tunnel -------------------------------------------------------
Write-Host "[2/3] Creating ngrok tunnel ..." -ForegroundColor Yellow

# Kill any stale ngrok process before starting fresh
Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1

$ngrokProc = Start-Process "ngrok" -ArgumentList @("http", "$port") -PassThru -WindowStyle Minimized

# Poll ngrok local API for the public URL (up to 20 seconds)
$ngrokUrl = $null
for ($i = 1; $i -le 20; $i++) {
    Start-Sleep 1
    try {
        $tunnels = Invoke-RestMethod "http://127.0.0.1:4040/api/tunnels" -ErrorAction Stop
        $https = $tunnels.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        if ($https) { $ngrokUrl = $https.public_url; break }
    } catch { }
}

# -- Display public URL -------------------------------------------------------
Write-Host ""
if ($ngrokUrl) {
    Write-Host "[3/3] Public URL ready" -ForegroundColor Green
    Write-Host ""
    Write-Host "  +------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host "  |  Open on your phone:                                 |" -ForegroundColor Cyan
    Write-Host "  |  $ngrokUrl" -ForegroundColor Green
    Write-Host "  |                                                      |" -ForegroundColor Cyan
    Write-Host "  |  Local: http://127.0.0.1:$port                        |" -ForegroundColor Cyan
    Write-Host "  +------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  ngrok dashboard (QR code etc.): http://127.0.0.1:4040" -ForegroundColor Gray
} else {
    Write-Host "[WARN] Could not retrieve ngrok URL automatically." -ForegroundColor Yellow
    Write-Host "       Open http://127.0.0.1:4040 in your browser to find the URL."
}

Write-Host ""
Write-Host "  Running -- press Ctrl+C to stop" -ForegroundColor Gray
Write-Host ""

# -- Wait loop / cleanup on exit ----------------------------------------------
try {
    while ($true) {
        Start-Sleep 10
        if ($uvicornProc.HasExited) {
            Write-Host "[WARN] uvicorn exited unexpectedly (code: $($uvicornProc.ExitCode))" -ForegroundColor Yellow
            break
        }
        if ($ngrokProc.HasExited) {
            Write-Host "[WARN] ngrok exited unexpectedly (code: $($ngrokProc.ExitCode))" -ForegroundColor Yellow
            break
        }
    }
} finally {
    Write-Host ""
    Write-Host "Shutting down ..." -ForegroundColor Yellow
    if (-not $uvicornProc.HasExited) {
        Stop-Process -Id $uvicornProc.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  uvicorn stopped (PID: $($uvicornProc.Id))" -ForegroundColor Gray
    }
    if (-not $ngrokProc.HasExited) {
        Stop-Process -Id $ngrokProc.Id  -Force -ErrorAction SilentlyContinue
        Write-Host "  ngrok stopped  (PID: $($ngrokProc.Id))" -ForegroundColor Gray
    }
    Write-Host "Done." -ForegroundColor Green
    Write-Host ""
}
