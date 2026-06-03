#Requires -Version 5.1
# review_app (8000) + prediction_app (8001) launcher with ngrok
# Usage  : .\start_apps.ps1
# Stop   : Ctrl+C  (stops uvicorn x2 and ngrok)
# Prereq : ngrok authenticated -- run once: ngrok authtoken YOUR_TOKEN
#          Token at https://dashboard.ngrok.com/

$ErrorActionPreference = "Stop"
$root     = Split-Path -Parent $MyInvocation.MyCommand.Path
$portRev  = 8000
$portPred = 8001

Write-Host ""
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  review_app + prediction_app + ngrok launcher" -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host ""

# --- PostgreSQL credentials --------------------------------------------------
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

# --- ngrok availability check ------------------------------------------------
$ngrokCmd = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrokCmd) {
    Write-Host "[ERROR] ngrok not found. Install with: winget install ngrok" -ForegroundColor Red
    exit 1
}

$ngrokCfg = "$env:USERPROFILE\AppData\Local\ngrok\ngrok.yml"
if (-not (Test-Path $ngrokCfg) -or -not (Select-String "authtoken" $ngrokCfg -Quiet)) {
    Write-Host "[ERROR] ngrok authtoken is not configured." -ForegroundColor Red
    Write-Host "        Run: ngrok authtoken YOUR_TOKEN"
    Write-Host "        Get token at: https://dashboard.ngrok.com/"
    exit 1
}

# --- Kill processes already on target ports ----------------------------------
foreach ($port in @($portRev, $portPred)) {
    $occ = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($occ) {
        $pid_ = ($occ | Select-Object -First 1).OwningProcess
        $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "[INFO] Port $port in use (PID $pid_ $($proc.ProcessName)) -- stopping" -ForegroundColor Yellow
            Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
            Start-Sleep 1
        }
    }
}

# --- Kill stale ngrok --------------------------------------------------------
Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1

# --- [1/4] Start review_app --------------------------------------------------
Write-Host "[1/4] Starting review_app on port $portRev ..." -ForegroundColor Yellow
$revArgs = @(
    "-3.12-32", "-m", "uvicorn",
    "review_app.main:app",
    "--host", "127.0.0.1",
    "--port", "$portRev"
)
$revProc = Start-Process "py" -ArgumentList $revArgs -PassThru -WindowStyle Minimized

# --- [2/4] Start prediction_app ----------------------------------------------
Write-Host "[2/4] Starting prediction_app on port $portPred ..." -ForegroundColor Yellow
$predArgs = @(
    "-3.12-32", "-m", "uvicorn",
    "prediction_app.main:app",
    "--host", "127.0.0.1",
    "--port", "$portPred"
)
$predProc = Start-Process "py" -ArgumentList $predArgs -PassThru -WindowStyle Minimized

# --- Wait for both apps to respond -------------------------------------------
Write-Host "  Waiting for apps to start (up to 25 s) ..." -ForegroundColor Gray
$ready = @{ $portRev = $false; $portPred = $false }
for ($i = 1; $i -le 25; $i++) {
    Start-Sleep 1
    foreach ($p in @($portRev, $portPred)) {
        if (-not $ready[$p]) {
            try {
                $null = Invoke-WebRequest "http://127.0.0.1:$p/" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
                $ready[$p] = $true
                $label = if ($p -eq $portRev) { "review_app" } else { "prediction_app" }
                Write-Host "  Port $p ($label) ready" -ForegroundColor Green
            } catch { }
        }
    }
    if ($ready[$portRev] -and $ready[$portPred]) { break }
}
if (-not $ready[$portRev])  { Write-Host "  [WARN] review_app health check timed out -- continuing"    -ForegroundColor Yellow }
if (-not $ready[$portPred]) { Write-Host "  [WARN] prediction_app health check timed out -- continuing" -ForegroundColor Yellow }

# --- [3/4] Create ngrok multi-tunnel config ----------------------------------
Write-Host "[3/4] Creating ngrok tunnels ..." -ForegroundColor Yellow

$ngrokTmpCfg = "$env:TEMP\ngrok_keiba_tunnels.yml"
$tunnelYaml  = "tunnels:`n  review:`n    proto: http`n    addr: $portRev`n  prediction:`n    proto: http`n    addr: $portPred`n"
$utf8NoBom   = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($ngrokTmpCfg, $tunnelYaml, $utf8NoBom)

$ngrokArgs = @(
    "start", "review", "prediction",
    "--config", $ngrokCfg,
    "--config", $ngrokTmpCfg
)
$ngrokProc = Start-Process "ngrok" -ArgumentList $ngrokArgs -PassThru -WindowStyle Minimized

# --- Poll ngrok local API for tunnel URLs ------------------------------------
$urlRev  = $null
$urlPred = $null
for ($i = 1; $i -le 25; $i++) {
    Start-Sleep 1
    try {
        $tunnels = Invoke-RestMethod "http://127.0.0.1:4040/api/tunnels" -ErrorAction Stop
        foreach ($t in $tunnels.tunnels) {
            if ($t.proto -ne "https") { continue }
            if ($t.config.addr -match ":$portRev`$")  { $urlRev  = $t.public_url }
            if ($t.config.addr -match ":$portPred`$") { $urlPred = $t.public_url }
        }
        if ($urlRev -and $urlPred) { break }
    } catch { }
}

# --- [4/4] Display public URLs -----------------------------------------------
Write-Host ""
Write-Host "[4/4] Tunnels ready" -ForegroundColor Green
Write-Host ""
Write-Host "  +------------------------------------------------------------+" -ForegroundColor Cyan
Write-Host "  |  review_app:                                               |" -ForegroundColor Cyan
$u1 = if ($urlRev)  { $urlRev  } else { "FAILED -- check http://127.0.0.1:4040" }
Write-Host "  |    $u1" -ForegroundColor Green
Write-Host "  |                                                            |" -ForegroundColor Cyan
Write-Host "  |  prediction_app:                                          |" -ForegroundColor Cyan
$u2 = if ($urlPred) { $urlPred } else { "FAILED -- check http://127.0.0.1:4040" }
Write-Host "  |    $u2" -ForegroundColor Green
Write-Host "  +------------------------------------------------------------+" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ngrok dashboard (QR code): http://127.0.0.1:4040" -ForegroundColor Gray
Write-Host ""
Write-Host "  Running -- press Ctrl+C to stop" -ForegroundColor Gray
Write-Host ""

# --- Monitor loop / cleanup on exit ------------------------------------------
try {
    while ($true) {
        Start-Sleep 10
        if ($revProc.HasExited) {
            Write-Host "[WARN] review_app exited unexpectedly (code: $($revProc.ExitCode))" -ForegroundColor Yellow
            break
        }
        if ($predProc.HasExited) {
            Write-Host "[WARN] prediction_app exited unexpectedly (code: $($predProc.ExitCode))" -ForegroundColor Yellow
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
    foreach ($p in @($revProc, $predProc, $ngrokProc)) {
        if ($p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path $ngrokTmpCfg) { Remove-Item $ngrokTmpCfg -ErrorAction SilentlyContinue }
    Write-Host "Done." -ForegroundColor Green
    Write-Host ""
}
